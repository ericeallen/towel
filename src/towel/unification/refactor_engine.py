# Copyright 2025-2026 Eric Allen
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Main refactoring engine using unification.

This orchestrates the entire refactoring process:
1. Parse files into ASTs
2. Find pairs of code blocks in top-level functions
3. Attempt unification to find parameterizable differences
4. Extract functions hygienically if unification succeeds
5. Generate replacement calls
"""

import ast
import hashlib
import os
import re
from collections import OrderedDict
from concurrent.futures.process import BrokenProcessPool
from typing import (
    Any,
    Callable,
    List,
    Tuple,
    Dict,
    Optional,
    FrozenSet,
    Iterable,
    MutableMapping,
    Sequence,
    cast,
)
from weakref import WeakKeyDictionary
from pathlib import Path
from .unifier import Unifier
from .extractor import (
    HygienicExtractor,
)
from .structural_memo import (
    StoredSubstitution,
    structural_id,
)
from .progress import DEFAULT_PROGRESS, ProgressMode, load_tqdm, quietly, wants_bar
from .fixed_point import FixedPointDrivers
from .materialize import Materialization
from .annotation_wiring import HelperAnnotationWiring
from .reuse import ExistingFunctionReuse
from .placement import HelperPlacement
from .parallel import ParallelEvaluation
from .clustering import Clustering
from .pair_evaluation import PairEvaluation
from .block_analysis import BlockAnalysis
from .insertion import InsertionPoints
from ..diagnostics import LOG, REJECTIONS, Settings, debugging
from .semantic_safety import (
    ImportGraphCache,
)
from .block_signature import (
    BlockBucketKey,
    BlockSignature,
    quick_filter,
    signature_bucket_key,
)
from .models import (
    CodeBlockPair,
    FunctionArtifact,
    ClassInfo,
    RefactoringProposal,
    RejectReason,
    AppliedChange,
    FunctionNode,
)
from ..type_inference import TypeOracle
from .pipeline import run_pipeline, AnalysisSession

# Configuration defaults
DEFAULT_MAX_PARAMETERS = 5
DEFAULT_MIN_LINES = 3
DEFAULT_MAX_ITERATIONS = 0  # Unlimited


class UnificationRefactorEngine(
    InsertionPoints,
    HelperPlacement,
    ExistingFunctionReuse,
    HelperAnnotationWiring,
    Materialization,
    FixedPointDrivers,
    ParallelEvaluation,
    Clustering,
    PairEvaluation,
    BlockAnalysis,
):
    """
    Main engine for unification-based refactoring.

    This finds and extracts duplicate code using unification.
    """

    def __init__(
        self,
        max_parameters: int = DEFAULT_MAX_PARAMETERS,
        min_lines: int = DEFAULT_MIN_LINES,
        parameterize_constants: bool = True,
        *,
        prefer_absolute_imports: Optional[bool] = None,
        pep420_namespace_packages: Optional[bool] = None,
        promote_equal_hof_literals: bool = False,
        excluded_directories: Sequence[str] = (),
        skip_trivial_helpers: bool = True,
        reuse_existing_functions: bool = True,
        annotate_helpers: bool = True,
        type_oracle: Optional[TypeOracle] = None,
        snippet_formatter: Optional[Callable[[str], str]] = None,
        file_finisher: Optional[Callable[[str, str], str]] = None,
        incremental_global_passes: bool = True,
        settings: Optional[Settings] = None,
    ):
        """
        Initialize the refactoring engine.

        Args:
            max_parameters: Maximum parameters an extracted helper may take; a
                candidate needing more is rejected (default: 5).
            min_lines: Minimum number of source lines a duplicated block must span
                to be considered (default: 3).
            parameterize_constants: Whether differing constants across the matched
                blocks become helper parameters (default: True).
            prefer_absolute_imports: For a cross-file helper, prefer an absolute
                import over a relative one -- honored only when packaging metadata
                anchors the module name. None (default) lets the discovered layout
                decide.
            pep420_namespace_packages: Treat directories without ``__init__.py`` as
                namespace packages when deriving module paths. None (default)
                infers it from the project.
            promote_equal_hof_literals: Expose literal arguments of higher-order
                factory calls as parameters even when they are equal across blocks
                (Option B policy); default False.
            excluded_directories: Directory names to skip in directory mode, such
                as a package that carries its own test suite.
            skip_trivial_helpers: Skip proposing a helper whose body is a single
                forwarding statement -- a lone ``raise``, a ``return`` of one
                call, or a bare call -- which adds indirection without sharing any
                logic (default: True).
            reuse_existing_functions: When a duplicate site is the whole body of a
                plain module-level function, leave that function as it is and
                have the other sites call it instead of extracting a helper that
                would only restate it (default: True).
            annotate_helpers: Give a helper the parameter and return annotations
                its call sites agree on -- an annotated, never-rebound parameter
                of the enclosing function, a literal's builtin type, the sites'
                declared return type -- in code that already uses annotations
                (default: True). Nothing is inferred unless ``type_oracle``
                is given.
            type_oracle: Asked, when a proposal is applied, for the types of
                the argument expressions and returned values the copied
                annotations could not name, for example ``MypyInferrer`` (see
                ``towel.type_inference``). Used only where the sites declare
                types. None (default) infers nothing.
            snippet_formatter: Renders each generated helper definition and
                call statement from ``ast.unparse`` output to the text that is
                inserted, for example Black (see ``towel.formatting``). None
                (default) inserts the ``ast.unparse`` text as is.
            file_finisher: Maps ``(path, source)`` of each modified file to its
                final text, for example with imports sorted the way the project
                sorts them (see ``towel.formatting.import_sorter_for_project``).
                None (default) leaves files as assembled.
            settings: What Towel reads from the environment (worker cap,
                debug switches). Read once from ``os.environ`` when omitted.
            incremental_global_passes: In directory mode, after the first
                analysis, re-pair only functions in files that changed since
                the previous global pass (default: True). This is exact: an
                unchanged pair's verdict depends on its two files, the class
                hierarchy (which refactoring never alters) and the import
                graph (to which refactoring only adds edges, so a pair
                declined for a cycle stays declined), and any proposal it
                produced was applied, which changed its files. False re-pairs
                everything on every global pass.
        """
        self.analysis_session = AnalysisSession()
        self._settings = settings if settings is not None else Settings.from_environ()
        self.import_graph = ImportGraphCache()
        self._source_lines_cache: Dict[str, Tuple[Tuple[int, int], Tuple[str, ...]]] = {}
        self._settings.enable_debug_logging()
        self.max_parameters = max_parameters
        self.min_lines = min_lines
        self.skip_trivial_helpers = skip_trivial_helpers
        self.reuse_existing_functions = reuse_existing_functions
        self.annotate_helpers = annotate_helpers
        self.type_oracle = type_oracle
        self.snippet_formatter = snippet_formatter
        self.file_finisher = file_finisher
        self.incremental_global_passes = incremental_global_passes
        # Directory names left out of directory mode, such as ``tests`` when a
        # package carries its test suite inside itself (networkx: 77k of its
        # 198k lines).
        self.excluded_directories = tuple(excluded_directories)
        self.parameterize_constants = parameterize_constants
        self.unifier = Unifier(
            max_parameters=max_parameters,
            parameterize_constants=parameterize_constants,
            promote_equal_hof_literals=promote_equal_hof_literals,
        )
        self.extractor = HygienicExtractor()
        # Cross-file import preferences
        self.prefer_absolute_imports = prefer_absolute_imports
        self.pep420_namespace_packages = pep420_namespace_packages
        # Default behavior: allow safe handling of globals/nonlocals by not parameterizing
        # them and promoting necessary declarations into the extracted function when needed.

        # Memoization caches keyed by the identity of AST nodes parsed for this engine run.
        self._assignment_cache: WeakKeyDictionary[ast.AST, Dict[int, bool]] = WeakKeyDictionary()
        self._used_names_cache: WeakKeyDictionary[ast.AST, FrozenSet[str]] = WeakKeyDictionary()
        self._value_producing_cache: WeakKeyDictionary[ast.AST, Dict[int, bool]] = (
            WeakKeyDictionary()
        )
        # Block guards are pure in (guard, function, block); a block takes part
        # in every pair it forms, so its verdicts are computed once.
        self._block_guard_cache: "OrderedDict[Tuple[Any, ...], bool]" = OrderedDict()
        # Unification is a function of the two blocks' nodes. The clustering
        # pass unifies one template against the same candidates for every pair
        # that shares it, so results are memoized per (block, block) within an
        # analysis; callers mutate substitutions, so a hit returns a copy.
        # Keyed by the two blocks' structure, valued by positions: a hit
        # serves any later block with the same structure, including the same
        # code after a re-parse. Bounded and self-validating, so never evicted
        # by path.
        self._unify_cache: "OrderedDict[Tuple[str, str], Optional[StoredSubstitution]]" = (
            OrderedDict()
        )
        self._cluster_cache: "OrderedDict[Tuple[Any, ...], Optional[ast.AST]]" = OrderedDict()
        self._structural_ids: Dict[Tuple[ast.AST, ...], str] = {}
        self._function_sources: Dict[FunctionNode, str] = {}
        self._source_digests: Dict[str, str] = {}
        # Per-block analyses (binding snapshot, reassignment and unbinding
        # checks) depend only on the function and the block; a block takes
        # part in every pair it forms, so each is computed once per analysis.
        self._per_block_cache: "OrderedDict[Tuple[str, str, str], Any]" = OrderedDict()
        # Every cache entry is registered under the absolute path(s) of the
        # file(s) it describes, so a file that changes between fixed-point
        # iterations evicts exactly its own entries and unchanged files keep
        # theirs across iterations.
        self._cache_entries_by_path: Dict[str, List[Tuple[MutableMapping[Any, Any], Any]]] = {}
        self._function_paths: Dict[FunctionNode, str] = {}
        # Track helper name allocation per canonical file so helpers remain unique.
        self._helper_name_counters: Dict[str, int] = {}
        # Per-run record of what each applied extraction replaced: the original
        # block and the generated call, for the naming step's before/after view.
        self._change_log: List[AppliedChange] = []
        # Every file of the current analysis: helper names must be unique
        # across all of them, because any module may import from any other.
        self._analysis_paths: Tuple[str, ...] = ()
        self._signed_block_cache: WeakKeyDictionary[
            FunctionNode, List[Tuple[Tuple[int, int], List[ast.AST], BlockSignature]]
        ] = WeakKeyDictionary()

    # --- Debug helpers ---
    def _debug_reject(
        self, reason: RejectReason, pair: "CodeBlockPair", detail: Optional[str] = None
    ) -> None:
        """Emit a concise rejection line when DEBUG_PROPOSAL_REJECTIONS is set.

        Includes function names and basic block ranges to help triage pruning gates.
        """
        if not debugging(REJECTIONS):
            return
        msg = (
            f"REJECT[{reason}]: {pair.function1_name}{'@'+str(pair.block1_range) if pair.block1_range else ''} "
            f"<-> {pair.function2_name}{'@'+str(pair.block2_range) if pair.block2_range else ''}"
        )
        if detail:
            msg += f" :: {detail}"
        REJECTIONS.debug(msg)

    def analyze_file(self, file_path: str) -> List[RefactoringProposal]:
        """
        Analyze a Python file and find refactoring opportunities.

        Args:
            file_path: Path to Python file

        Returns:
            List of refactoring proposals
        """
        return self.analyze_files([file_path])

    def analyze_directory(
        self,
        directory: str,
        recursive: bool = True,
        *,
        verbose: bool = False,
        progress: ProgressMode = DEFAULT_PROGRESS,
        changed_files: Optional[FrozenSet[str]] = None,
    ) -> List[RefactoringProposal]:
        """
        Analyze all Python files in a directory and find refactoring opportunities.

        Args:
            directory: Path to directory
            recursive: Whether to search subdirectories (default: True)

        Returns:
            List of refactoring proposals
        """
        # Find all Python files
        python_files = self._find_python_files(directory, recursive)

        if not python_files:
            return []

        if verbose:
            LOG.info("Found %d Python files in %s", len(python_files), directory)

        # Analyze all files together
        return self.analyze_files(
            python_files, verbose=verbose, progress=progress, changed_files=changed_files
        )

    def _find_python_files(self, directory: str, recursive: bool = True) -> List[str]:
        """
        Find all Python files in a directory.

        Args:
            directory: Directory to search
            recursive: Whether to search subdirectories

        Returns:
            List of Python file paths
        """
        python_files = []
        directory_path = Path(directory)

        if not directory_path.exists():
            return []

        if recursive:
            # Recursively find all .py files
            for py_file in directory_path.rglob("*.py"):
                # Skip common directories to ignore
                if any(
                    part.startswith(".")
                    or part in ["__pycache__", "venv", "env", "node_modules"]
                    or part in self.excluded_directories
                    for part in py_file.relative_to(directory_path).parts[:-1]
                ):
                    continue
                if py_file.is_file() and not py_file.is_symlink():
                    python_files.append(str(py_file))
        else:
            # Only find .py files in this directory
            for py_file in directory_path.glob("*.py"):
                if py_file.is_file() and not py_file.is_symlink():
                    python_files.append(str(py_file))

        return sorted(python_files)

    def analyze_files(
        self,
        file_paths: List[str],
        *,
        verbose: bool = False,
        progress: ProgressMode = DEFAULT_PROGRESS,
        invalidate_paths: Optional[List[str]] = None,
        changed_files: Optional[FrozenSet[str]] = None,
    ) -> List[RefactoringProposal]:
        """Analyze multiple files using the compiler-style pipeline and return proposals.

        invalidate_paths: If provided, forces reparse/reanalysis of these paths even if cached.
        changed_files: If provided, only pairs with a function in one of these
            files are considered (see ``incremental_global_passes``).
        """
        stale = {os.path.abspath(path) for path in (invalidate_paths or ())}
        stale.update(
            os.path.abspath(path) for path in file_paths if not self.analysis_session.reusable(path)
        )
        for path in stale:
            self._evict_cached_analysis(path)
        self._analysis_paths = tuple(file_paths)
        return run_pipeline(
            file_paths,
            engine=self,
            verbose=verbose,
            progress=progress,
            invalidate_paths=invalidate_paths,
            session=self.analysis_session,
            changed_files=changed_files,
        )

    #: Entries kept per structural cache; oldest are dropped beyond this.
    STRUCTURAL_CACHE_LIMIT = 250_000
    #: When a file's eviction-index list grows past this, drop entries the caches no longer hold.
    _EVICTION_INDEX_PRUNE_AT = 4096

    @staticmethod
    def _bounded_put(cache: "OrderedDict[Any, Any]", key: Any, value: Any) -> None:
        cache[key] = value
        while len(cache) > UnificationRefactorEngine.STRUCTURAL_CACHE_LIMIT:
            cache.popitem(last=False)

    def _sid(self, nodes: Sequence[ast.AST]) -> str:
        """The structural id of a block or function, computed once per node tuple."""
        key = tuple(nodes)
        cached = self._structural_ids.get(key)
        if cached is None:
            cached = structural_id(key)
            self._structural_ids[key] = cached
            owner = self._function_paths.get(cast(FunctionNode, key[0])) if key else None
            self._remember((owner,), self._structural_ids, key)
        return cached

    def _module_digest(self, func: Optional[FunctionNode]) -> Optional[str]:
        """A digest of the module source a function came from, for module-wide analyses."""
        return self._function_sources.get(func) if func is not None else None

    def _remember(
        self, paths: Iterable[Optional[str]], cache: MutableMapping[Any, Any], key: Any
    ) -> None:
        """Register a cache entry under the files it depends on."""
        for path in paths:
            if path is not None:
                entries = self._cache_entries_by_path.setdefault(os.path.abspath(path), [])
                entries.append((cache, key))
                if len(entries) > self._EVICTION_INDEX_PRUNE_AT:
                    # Entries whose keys the bounded caches already dropped are
                    # dead weight; without this the index grew without bound.
                    entries[:] = [(c, k) for c, k in entries if k in c]

    def _evict_cached_analysis(self, path: str) -> None:
        """Drop every cache entry that depends on ``path``."""
        absolute = os.path.abspath(path)
        for cache, key in self._cache_entries_by_path.pop(absolute, ()):
            cache.pop(key, None)
        for function, function_path in tuple(self._function_paths.items()):
            if os.path.abspath(function_path) == absolute:
                del self._function_paths[function]
                self._function_sources.pop(function, None)

    def _record_function_paths(self, all_functions: Sequence[FunctionArtifact]) -> None:
        for entry in all_functions:
            self._function_paths[entry.node] = entry.file_path
            source = entry.source
            digest = self._source_digests.get(source)
            if digest is None:
                digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
                self._source_digests[source] = digest
            self._function_sources[entry.node] = digest

    def process_block_pairs(
        self,
        block_pairs: List[CodeBlockPair],
        all_functions: Sequence[FunctionArtifact],
        class_infos: List[ClassInfo],
        *,
        verbose: bool,
        progress: ProgressMode,
    ) -> List[RefactoringProposal]:
        if not block_pairs:
            return []

        self._record_function_paths(all_functions)
        if self._should_use_parallel(len(block_pairs)):
            try:
                return self._evaluate_pairs_parallel(
                    block_pairs,
                    all_functions,
                    class_infos,
                    verbose=verbose,
                    progress=progress,
                )
            except BrokenProcessPool:
                LOG.warning("Parallel worker pool failed; retrying serial evaluation")
                # Fall back to serial evaluation if multiprocessing encounters an issue
                return self._evaluate_pairs_serial(
                    block_pairs,
                    all_functions,
                    class_infos,
                    verbose=verbose,
                    progress=progress,
                )

        return self._evaluate_pairs_serial(
            block_pairs,
            all_functions,
            class_infos,
            verbose=verbose,
            progress=progress,
        )

    def _allocate_helper_name(
        self,
        file_path: str,
        *,
        class_context: bool = False,
        related_paths: Sequence[str] = (),
    ) -> str:
        """Return a unique helper name for the given canonical file."""

        counter = max(
            self._helper_name_counters.get(file_path, 0),
            *(
                self._discover_helper_counter_seed(path)
                for path in {file_path, *related_paths, *self._analysis_paths}
            ),
        )
        # Python mangles double-underscore names in class bodies, including
        # references to module helpers and inherited methods from another class.
        prefix = "_extracted_func" if class_context else "__extracted_func"
        helper_name = f"{prefix}_{counter}"
        self._helper_name_counters[file_path] = counter + 1
        return helper_name

    def _discover_helper_counter_seed(self, file_path: str) -> int:
        """Prime the helper counter based on existing helper names in a file."""

        try:
            content = Path(file_path).read_text(encoding="utf-8")
        except FileNotFoundError:
            return 0

        pattern = re.compile(r"(?<!\w)_{1,2}extracted_func(?:_(\d+))?(?!\w)")
        max_seen = -1
        for match in pattern.finditer(content):
            suffix = match.group(1)
            idx = int(suffix) if suffix is not None else 0
            if idx > max_seen:
                max_seen = idx
        return max_seen + 1

    def find_block_pairs(
        self,
        all_functions: Sequence[FunctionArtifact],
        *,
        progress: ProgressMode = DEFAULT_PROGRESS,
        changed_files: Optional[FrozenSet[str]] = None,
    ) -> List[CodeBlockPair]:
        """
        Find all non-overlapping pairs of code blocks across multiple files.

        Args:
            all_functions: The analyzed functions with their context

        Returns:
            List of code block pairs
        """

        self._record_function_paths(all_functions)

        pairs: List[CodeBlockPair] = []
        signed_blocks: List[List[Tuple[Tuple[int, int], List[ast.AST], BlockSignature]]] = []
        block_buckets: List[
            Dict[BlockBucketKey, List[Tuple[Tuple[int, int], List[ast.AST], BlockSignature]]]
        ] = []
        # Precompute once per function. Each bucket retains the original block
        # order, so traversing i, j, block1, matching block2 preserves proposal
        # priority as well as the exact set of candidates.
        for entry in all_functions:
            blocks = self._signed_blocks(entry.node)
            signed_blocks.append(blocks)
            buckets: Dict[
                BlockBucketKey, List[Tuple[Tuple[int, int], List[ast.AST], BlockSignature]]
            ] = {}
            for block in blocks:
                buckets.setdefault(signature_bucket_key(block[2]), []).append(block)
            block_buckets.append(buckets)
        # The bucket keys each function's blocks fall into; two functions with
        # no key in common cannot form a pair, so their blocks are never visited.
        bucket_keys = [frozenset(buckets) for buckets in block_buckets]

        # Progress setup
        use_tqdm = wants_bar(progress)
        tqdm_bar = None
        total_funcs = len(all_functions)
        total_func_pairs = (total_funcs * (total_funcs - 1)) // 2 if total_funcs > 1 else 0
        if use_tqdm and total_func_pairs > 0:
            tqdm_cls = load_tqdm()
            if tqdm_cls is not None:
                tqdm_bar = tqdm_cls(
                    total=total_func_pairs,
                    desc="pairs",
                    unit="fp",
                    dynamic_ncols=True,
                    leave=False,
                )
            else:
                use_tqdm = False

        use_inline = (not use_tqdm) and wants_bar(progress) and total_func_pairs > 0
        last_pct = -1
        self._start_inline_status("Pairing blocks:", use_inline)

        func_pairs_done = 0

        # For each pair of functions (including across files)
        for i, first in enumerate(all_functions):
            file1_changed = changed_files is None or first.file_path in changed_files
            for j, second in enumerate(all_functions[i + 1 :], i + 1):
                if (
                    changed_files is not None
                    and not file1_changed
                    and second.file_path not in changed_files
                ):
                    continue  # both files unchanged since the last global pass: verdict stands
                # Only compare structurally compatible buckets; tolerance
                # checks still use the unchanged quick_filter below.
                blocks1 = signed_blocks[i] if not bucket_keys[i].isdisjoint(bucket_keys[j]) else ()
                for block1_range, block1_nodes, sig1 in blocks1:
                    for block2_range, block2_nodes, sig2 in block_buckets[j].get(
                        signature_bucket_key(sig1), []
                    ):
                        if not quick_filter(sig1, sig2):
                            continue

                        # Create pair with all necessary context
                        pair = CodeBlockPair(
                            file_path=first.file_path,
                            function1_name=first.node.name,
                            function2_name=second.node.name,
                            block1_range=block1_range,
                            block2_range=block2_range,
                            block1_nodes=block1_nodes,
                            block2_nodes=block2_nodes,
                            file_path2=second.file_path,
                            class1_name=first.class_name,
                            class2_name=second.class_name,
                            enclosing_function1_name=first.enclosing_function,
                            enclosing_function2_name=second.enclosing_function,
                            function1_ancestry=first.ancestry,
                            function2_ancestry=second.ancestry,
                            scope_analyzer1=first.scope_analyzer,
                            scope_analyzer2=second.scope_analyzer,
                            root_scope1=first.root_scope,
                            root_scope2=second.root_scope,
                            source1=first.source,
                            source2=second.source,
                            function1_node=first.node,
                            function2_node=second.node,
                        )
                        pairs.append(pair)

                # Update progress per function pair
                func_pairs_done += 1
                if tqdm_bar is not None:
                    bar, done = tqdm_bar, func_pairs_done

                    def advance() -> None:
                        bar.update(1)
                        if done % 20 == 0 or done == total_func_pairs:
                            bar.set_postfix({"pairs": len(pairs)}, refresh=True)

                    quietly(advance)
                elif use_inline:
                    pct = int(100 * func_pairs_done / max(total_func_pairs, 1))
                    if pct != last_pct:
                        last_pct = pct
                        self._update_inline_status(
                            "Pairing blocks:",
                            pct,
                            suffix=f"| pairs={len(pairs)}",
                        )
        if tqdm_bar is not None:
            quietly(tqdm_bar.close)
        self._finish_inline_status(use_inline)

        return pairs

    # ------------------------------------------------------------------
    # Reusing an existing function instead of extracting a redundant helper
    # ------------------------------------------------------------------

    # Optional analysis cache invalidation hook used by directory fixed-point runner
    @property
    def change_log(self) -> Sequence[AppliedChange]:
        """Every call site the last directory run rewrote, in application order."""
        return tuple(self._change_log)

    def invalidate_paths(self, paths: List[str]) -> None:
        self.analysis_session.invalidate(paths)


# Utility functions for overlap filtering
