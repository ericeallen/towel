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
    Set,
    Optional,
    FrozenSet,
    Iterable,
    MutableMapping,
    Sequence,
    cast,
)
from weakref import WeakKeyDictionary
from pathlib import Path
from .scope_analyzer import ScopeAnalyzer
from .unifier import Unifier, Substitution
from .extractor import (
    HygienicExtractor,
    has_complete_return_coverage,
    is_value_producing,
)
from .structural_memo import (
    StoredSubstitution,
    load_substitution,
    store_substitution,
    structural_id,
)
from .assignment_analyzer import (
    analyze_assignments,
    _collect_block_binding_stats,
    _collect_bindings_and_reassignments,
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
from .insertion import InsertionPoints
from ..diagnostics import LOG, REJECTIONS, VALIDATION, Settings, debugging
from .parameters import parameter_names
from .semantic_safety import (
    ImportGraphCache,
)
from .block_signature import (
    BlockBucketKey,
    BlockSignature,
    extract_block_signature,
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
    BlockBindingSnapshot,
    encloses,
)
from ..type_inference import TypeOracle
from .pipeline import run_pipeline, AnalysisSession
from .visitors import (
    body_without_docstring,
    LoopReturnFinder,
    NameCollector,
    AugAssignFinder,
    AssignTargetVisitor,
)

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

    def _unify_memoized(
        self,
        blocks: List[List[ast.AST]],
        hygienic_renames: List[Dict[str, str]],
        paths: Sequence[Optional[str]] = (),
    ) -> Optional[Substitution]:
        """Unify two blocks, reusing the result for any pair with the same structure."""
        if len(blocks) != 2:
            return self.unifier.unify_blocks(blocks, hygienic_renames)
        key = (self._sid(blocks[0]), self._sid(blocks[1]))
        if key in self._unify_cache:
            stored = self._unify_cache[key]
            self._unify_cache.move_to_end(key)
            if stored is None:
                return None
            substitution, renames = load_substitution(stored, blocks)
            for target, source in zip(hygienic_renames, renames):
                target.clear()
                target.update(source)
            return substitution
        result = self.unifier.unify_blocks(blocks, hygienic_renames)
        self._bounded_put(
            self._unify_cache,
            key,
            None if result is None else store_substitution(result, blocks, hygienic_renames),
        )
        return result

    def _block_rejected(
        self,
        guard: Callable[..., bool],
        nodes: Sequence[ast.AST],
        func: Optional[FunctionNode] = None,
        analyzer: Optional[ScopeAnalyzer] = None,
        path: Optional[str] = None,
    ) -> bool:
        """Evaluate a pure block guard once per (guard, function, block)."""
        key = (
            guard,
            self._sid([func]) if func is not None else None,
            self._sid(nodes),
            self._module_digest(func) if analyzer is not None else None,
        )
        cached = self._block_guard_cache.get(key)
        if cached is not None:
            self._block_guard_cache.move_to_end(key)
            return cached
        if analyzer is not None:
            verdict = bool(guard(analyzer, func, list(nodes)))
        elif func is not None:
            verdict = bool(guard(func, list(nodes)))
        else:
            verdict = bool(guard(list(nodes)))
        self._bounded_put(self._block_guard_cache, key, verdict)
        return verdict

    def _is_value_producing(self, block: Sequence[ast.AST]) -> bool:
        """``is_value_producing`` memoized per block: a block is paired many times."""
        if not block:
            return False
        first = block[0]
        by_length = self._value_producing_cache.get(first)
        if by_length is None:
            by_length = {}
            self._value_producing_cache[first] = by_length
        result = by_length.get(len(block))
        if result is None:
            result = is_value_producing(cast(List[ast.stmt], list(block)))
            by_length[len(block)] = result
        return result

    def _get_assignment_reuse(self, func: FunctionNode) -> Dict[int, bool]:
        """Return (and cache) assignment analysis for a function definition."""
        cached = self._assignment_cache.get(func)
        if cached is not None:
            return cached
        analysis = analyze_assignments(func)
        self._assignment_cache[func] = analysis
        return analysis

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

    def _signed_blocks(
        self, function: FunctionNode
    ) -> List[Tuple[Tuple[int, int], List[ast.AST], BlockSignature]]:
        """Share one block/signature enumeration across pairing and clustering."""
        cached = self._signed_block_cache.get(function)
        if cached is None:
            cached = [
                (span, nodes, extract_block_signature(nodes))
                for span, nodes in self._extract_code_blocks(function)
                if span[1] - span[0] + 1 >= self.min_lines
            ]
            self._signed_block_cache[function] = cached
            self._remember(
                (self._function_paths.get(function),), self._signed_block_cache, function
            )
        return cached

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

    def _extract_code_blocks(
        self, function: FunctionNode
    ) -> List[Tuple[Tuple[int, int], List[ast.AST]]]:
        """
        Extract all contiguous code blocks from a function body, including nested bodies.

        Args:
            function: Function definition

        Returns:
            List of (line_range, statements) tuples
        """

        def extract_from_body(
            body: List[ast.stmt], parent: Optional[ast.stmt] = None
        ) -> List[Tuple[Tuple[int, int], List[ast.AST]]]:
            # Extract all contiguous subsequences of minimum length from a given body
            results: List[Tuple[Tuple[int, int], List[ast.AST]]] = []

            # An ``elif`` is the sole statement of its parent's ``orelse`` and
            # shares the parent's column. It has no position of its own in the
            # source, so a block starting there would be rendered as a sibling
            # of the parent ``if`` and run unconditionally. Its own body and
            # branches are still visited below.
            is_elif = (
                isinstance(parent, ast.If)
                and len(body) == 1
                and isinstance(body[0], ast.If)
                and body[0].col_offset == parent.col_offset
            )

            # Extract all contiguous subsequences
            for length in range(0 if is_elif else len(body), 0, -1):
                for start in range(len(body) - length + 1):
                    block = body[start : start + length]

                    # Do not extract blocks that contain nested function/class definitions.
                    # These statements establish new scopes whose bindings must remain in the
                    # original function so later statements can reference them.
                    if any(
                        isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                        for stmt in block
                    ):
                        continue

                    span = self._block_line_span(block)
                    if span is None:
                        continue
                    start_line, end_line = span
                    line_count = end_line - start_line + 1

                    # A block that returns on some path but not on every path
                    # (a conditional return, or a lone expression statement,
                    # which counts as value-producing) is never accepted: a
                    # value-producing helper needs complete return coverage,
                    # and one that also binds live variables is rejected as
                    # mixed. Leaving such blocks out spares every pair they
                    # would have formed; on pyflakes' test_other.py that is
                    # 32,857 of 44,826 rejected pairs.
                    if is_value_producing(block) and not has_complete_return_coverage(block):
                        continue

                    if line_count >= self.min_lines:
                        results.append(((start_line, end_line), cast(List[ast.AST], block)))

            # Recurse into nested bodies for control-flow/container statements
            for stmt in body:
                # Skip nested function/class definitions to avoid crossing scopes
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue

                # Common body/orelse containers
                if hasattr(stmt, "body") and isinstance(getattr(stmt, "body"), list):
                    results.extend(extract_from_body(getattr(stmt, "body")))
                if hasattr(stmt, "orelse") and isinstance(getattr(stmt, "orelse"), list):
                    results.extend(extract_from_body(getattr(stmt, "orelse"), stmt))

                # With and AsyncWith already covered by .body
                # Try/Except/Finally blocks
                if isinstance(stmt, ast.Try):
                    if stmt.handlers:
                        for h in stmt.handlers:
                            if hasattr(h, "body") and isinstance(h.body, list):
                                results.extend(extract_from_body(h.body))
                    if hasattr(stmt, "finalbody") and isinstance(stmt.finalbody, list):
                        results.extend(extract_from_body(stmt.finalbody))

            return results

        # Prepare top-level body (skip docstring)
        body = body_without_docstring(function.body)

        return extract_from_body(body)

    def _has_code_after_block(self, function: ast.FunctionDef, block_end_line: int) -> bool:
        """
        Check if there's any executable code after block_end_line in the function.

        This is used to detect when extracting a block with returns would make
        subsequent code unreachable.

        Args:
            function: Function definition
            block_end_line: End line of the block

        Returns:
            True if there's code after the block
        """
        body = body_without_docstring(function.body)

        # Check if any statement starts after block_end_line
        for stmt in body:
            if stmt.lineno > block_end_line:
                return True
        return False

    def _has_returns_in_loops(self, block: List[ast.AST]) -> bool:
        """
        Check if a block contains return statements inside loops.

        Returns in loops are conditional on the loop executing, so if the loop
        doesn't execute (e.g., empty iteration), control continues after the loop.

        Args:
            block: List of AST statements

        Returns:
            True if there are returns inside loop statements
        """

        finder = LoopReturnFinder()
        for stmt in block:
            finder.visit(stmt)
        return finder.has_loop_return

    def _get_used_names(self, node: ast.AST) -> Set[str]:
        """
        Get all variable names that are used (read from) in an AST node.

        This collects all Name nodes with Load context.

        Args:
            node: AST node to analyze

        Returns:
            Set of variable names that are read in the node
        """
        cached = self._used_names_cache.get(node)
        if cached is not None:
            return set(cached)

        collector = NameCollector()
        collector.visit(node)

        frozen = frozenset(collector.used)
        self._used_names_cache[node] = frozen
        return set(frozen)

    def _collect_parameter_names(self, func: FunctionNode) -> Set[str]:
        """Return all argument names for a function (including pos-only and varargs)."""

        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return set()
        params = set(parameter_names(func.args))

        return params

    @staticmethod
    def _enclosing_function_named(
        name: str,
        file_path: str,
        all_functions: Sequence[FunctionArtifact],
        inner: Sequence[FunctionNode],
    ) -> Optional[FunctionNode]:
        """The one function called ``name`` in ``file_path`` enclosing every ``inner`` function."""
        matches = [
            entry.node
            for entry in all_functions
            if entry.file_path == file_path
            and entry.node.name == name
            and all(encloses(entry.node, function) for function in inner)
        ]
        return matches[0] if len(matches) == 1 else None

    def _deepest_common_ancestry(
        self, anc1: Optional[List[str]], anc2: Optional[List[str]]
    ) -> Optional[str]:
        """Return deepest shared symbol in two ancestry chains."""

        if not anc1 or not anc2:
            return None

        dce: Optional[str] = None
        for left, right in zip(anc1, anc2):
            if left == right:
                dce = left
            else:
                break
        return dce

    def _per_block(
        self,
        name: str,
        func: FunctionNode,
        block_nodes: Sequence[ast.AST],
        compute: Callable[[], Any],
    ) -> Any:
        """Compute an immutable per-(function, block) result once per analysis."""
        key = (name, self._sid([func]), self._sid(block_nodes))
        if key not in self._per_block_cache:
            self._bounded_put(self._per_block_cache, key, compute())
        else:
            self._per_block_cache.move_to_end(key)
        return self._per_block_cache[key]

    def _build_block_binding_snapshot(
        self,
        func: FunctionNode,
        block_nodes: List[ast.AST],
        block_range: Tuple[int, int],
        reassignments: Dict[int, bool],
    ) -> BlockBindingSnapshot:
        """Binding statistics for a block, computed once per (function, block)."""
        snapshot: BlockBindingSnapshot = self._per_block(
            "snapshot",
            func,
            block_nodes,
            lambda: self._compute_block_binding_snapshot(
                func, block_nodes, block_range, reassignments
            ),
        )
        return snapshot

    def _compute_block_binding_snapshot(
        self,
        func: FunctionNode,
        block_nodes: List[ast.AST],
        block_range: Tuple[int, int],
        reassignments: Dict[int, bool],
    ) -> BlockBindingSnapshot:
        """Aggregate binding stats for a block."""

        bound_in_block, reassigned_in_block = _collect_block_binding_stats(
            block_nodes, reassignments
        )

        bound_before_block: Set[str] = set()
        block_start_line = block_range[0]
        for stmt in func.body:
            if hasattr(stmt, "lineno") and stmt.lineno < block_start_line:
                stmt_bound: Set[str] = set()
                stmt_reassigned: Set[str] = set()
                _collect_bindings_and_reassignments(
                    stmt, reassignments, stmt_bound, stmt_reassigned
                )
                bound_before_block.update(stmt_bound)

        bound_before_block.update(self._collect_parameter_names(func))

        bound_after_block: Set[str] = set()
        block_end_line = block_range[1]
        for stmt in func.body:
            if hasattr(stmt, "lineno") and stmt.lineno > block_end_line:
                stmt_bound = set()
                stmt_reassigned = set()
                _collect_bindings_and_reassignments(
                    stmt, reassignments, stmt_bound, stmt_reassigned
                )
                bound_after_block.update(stmt_bound)

        initially_bound = bound_in_block - bound_before_block

        return BlockBindingSnapshot(
            bound_in_block=bound_in_block,
            reassigned_in_block=reassigned_in_block,
            bound_before_block=bound_before_block,
            bound_after_block=bound_after_block,
            initially_bound=initially_bound,
        )

    def _find_return_variables(
        self,
        func: FunctionNode,
        block_range: Tuple[int, int],
        initially_bound: Set[str],
        *,
        debug_label: Optional[str] = None,
    ) -> Set[str]:
        """
        Determine which newly-bound variables are read after the block.
        """

        if not initially_bound:
            return set()

        block_end_line = block_range[1]
        result: Set[str] = set()
        debug_enabled = debugging(VALIDATION)

        for stmt in func.body:
            if not hasattr(stmt, "lineno") or stmt.lineno <= block_end_line:
                continue

            uses = self._get_used_names(stmt)
            if debug_enabled and debug_label:
                VALIDATION.debug(
                    f"  {debug_label}: stmt@{stmt.lineno} ({stmt.__class__.__name__}) uses {uses}"
                )

            overlap = uses & initially_bound
            if overlap:
                result.update(overlap)
                if debug_enabled and debug_label:
                    VALIDATION.debug(
                        f"    RETURN NEEDED ({debug_label}): Variable(s) {overlap} will be returned from extracted function"
                    )

        if debug_enabled and debug_label and result:
            VALIDATION.debug(f"{debug_label} requires returning: {result}")

        return result

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

    def _global_nonlocal_declarations(
        self,
        pair: CodeBlockPair,
        scope_analyzer: ScopeAnalyzer,
        free_vars: Set[str],
    ) -> Tuple[Set[str], Set[str], Set[str]]:
        """Names the helper must declare global/nonlocal, and free_vars pruned of them.

        A free variable declared global or nonlocal in the enclosing function
        cannot also be a parameter (``SyntaxError: name 'x' is parameter and
        global``), so it is dropped from free_vars and left as a free reference.
        A name assigned inside the block that is global/nonlocal but not declared
        there must be re-declared in the helper to preserve assignment semantics.
        Returns (globals_to_declare, nonlocals_to_declare, pruned_free_vars).
        """
        func1_scope_id = None
        for node, scope in scope_analyzer.node_scopes.items():
            if isinstance(node, ast.FunctionDef) and node.name == pair.function1_name:
                func1_scope_id = scope.scope_id
                break

        globals_to_declare: Set[str] = set()
        nonlocals_to_declare: Set[str] = set()

        if func1_scope_id is not None:
            global_vars = scope_analyzer.global_vars.get(func1_scope_id, set())
            nonlocal_vars = scope_analyzer.nonlocal_vars.get(func1_scope_id, set())

            # A free variable that is global/nonlocal here cannot be parameterized.
            problematic = free_vars & (global_vars | nonlocal_vars)

            # Assignment targets and explicit declarations inside the template blocks.
            v = AssignTargetVisitor()
            for n in pair.block1_nodes:
                v.visit(n)
            for n in pair.block2_nodes:
                v.visit(n)
            assigned_names = v.assigned_names
            declared_global_in_block = v.declared_global_in_block
            declared_nonlocal_in_block = v.declared_nonlocal_in_block

            # Names assigned in the block that are global/nonlocal in the enclosing
            # function must be declared in the helper to preserve assignment semantics.
            assigned_problematic_any = assigned_names & (global_vars | nonlocal_vars)
            globals_to_declare = (assigned_problematic_any & global_vars) - declared_global_in_block
            nonlocals_to_declare = (
                assigned_problematic_any & nonlocal_vars
            ) - declared_nonlocal_in_block

            # Leave problematic free variables free so the helper references the
            # outer binding rather than shadowing it with a parameter.
            free_vars = free_vars - problematic

        return globals_to_declare, nonlocals_to_declare, free_vars

    @staticmethod
    def _reserve_augassign_params(pair: CodeBlockPair, substitution: Substitution) -> Set[str]:
        """Keep augmented-assignment targets free variables rather than parameters.

        ``total += x`` reads ``total`` before writing it, so it must stay a
        parameter even when unification matched it as a substitutable expression.
        Removes those parameters from the substitution and records, per block, the
        name each maps to (for call generation). Returns the target names.
        """
        aug_finder = AugAssignFinder()
        for node in pair.block1_nodes:
            aug_finder.visit(node)
        aug_assign_vars = aug_finder.aug_assign_targets

        aug_assign_param_mappings: Dict[str, Dict[int, str]] = {}
        params_to_remove = []
        for param_name, exprs in list(substitution.param_expressions.items()):
            for block_idx, expr in exprs:
                if block_idx == 0 and isinstance(expr, ast.Name) and expr.id in aug_assign_vars:
                    if param_name not in aug_assign_param_mappings:
                        aug_assign_param_mappings[param_name] = {}
                    aug_assign_param_mappings[param_name][block_idx] = expr.id
            if 0 in aug_assign_param_mappings.get(param_name, {}):
                params_to_remove.append(param_name)

        aug_mappings = substitution.aug_assign_mappings
        for param_name, block_mappings in aug_assign_param_mappings.items():
            if 0 in block_mappings:
                aug_mappings[block_mappings[0]] = block_mappings

        for param_name in params_to_remove:
            del substitution.param_expressions[param_name]
        return aug_assign_vars

    @staticmethod
    def _strip_fstring_params(substitution: Substitution) -> None:
        """Drop parameters that map to a whole f-string in the template block.

        Parameterizing an entire ``JoinedStr`` would replace the f-string with a
        single argument and lose its structure, so those parameters are removed.
        """
        fstring_params = []
        for param_name, exprs in substitution.param_expressions.items():
            for block_idx, expr in exprs:
                if block_idx == 0 and isinstance(expr, ast.JoinedStr):
                    fstring_params.append(param_name)
                    break
        for param_name in fstring_params:
            del substitution.param_expressions[param_name]

    @staticmethod
    def _working_free_vars(
        substitution: Substitution, aug_assign_vars: Set[str], free_vars1: Set[str]
    ) -> Set[str]:
        """Block1's free variables minus the ones now carried as parameters.

        A variable that became a parameter is no longer free, except an
        augmented-assignment target, which stays free so it is passed in and out.
        """
        parameterized_vars = set()
        for param_name, exprs in substitution.param_expressions.items():
            for block_idx, expr in exprs:
                if block_idx == 0 and isinstance(expr, ast.Name) and expr.id not in aug_assign_vars:
                    parameterized_vars.add(expr.id)
        return set(free_vars1) - parameterized_vars

    def _rejects_module_data_lookup(
        self,
        pair: CodeBlockPair,
        analyzer1: Optional[ScopeAnalyzer],
        analyzer2: Optional[ScopeAnalyzer],
    ) -> bool:
        """True when a block reads a module-level name that a callback could rebind.

        Module data can be rebound between two reads; passing it as a helper
        argument snapshots the value, but retaining the global name could capture
        a different caller's local. Reject until extraction can represent
        deferred, scope-correct lookups.
        """
        for block, block_analyzer in (
            (pair.block1_nodes, analyzer1),
            (pair.block2_nodes, analyzer2),
        ):
            if block_analyzer is None or block_analyzer.root_scope is None:
                continue
            for statement in block:
                for node in ast.walk(statement):
                    if not isinstance(node, ast.Name) or not isinstance(node.ctx, ast.Load):
                        continue
                    binding = block_analyzer.identifier_bindings.get(
                        node
                    ) or block_analyzer.root_scope.bindings.get(node.id)
                    if (
                        binding is not None
                        and binding.scope_id == block_analyzer.root_scope.scope_id
                        and isinstance(
                            binding.node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Name)
                        )
                    ):
                        self._debug_reject(RejectReason.MODULE_DATA_LOOKUP, pair)
                        return True
        return False

    @staticmethod
    def _helper_is_trivial_forwarding(func: ast.FunctionDef) -> bool:
        """Whether the helper body is a single forwarding statement with no logic.

        A lone ``raise``, a ``return`` of a single call, or a bare call expression
        just forwards to something else, so extracting it trades a readable inline
        statement for an indirection. Size is not the signal -- a passthrough that
        forwards many arguments is verbose yet worthless -- so this matches on
        structure. Anything with real computation or more than one statement is
        left for the ordinary gates.
        """
        body = [
            statement
            for statement in func.body
            if not isinstance(statement, (ast.Global, ast.Nonlocal))
        ]
        if UnificationRefactorEngine._helper_only_renames(body):
            return True
        if len(body) == 2:
            # ``name = call(...)`` then ``return name``, or ``a, b = call(...)``
            # then ``return (a, b)``, forwards just as a lone ``return call(...)``
            # does. Left unfiltered, two such helpers pair with each other and
            # extract a third, without end (h2 under Black, whose wrapping of
            # the call took the body over the line minimum).
            first, second = body
            if not (
                isinstance(first, ast.Assign)
                and len(first.targets) == 1
                and isinstance(first.value, ast.Call)
                and isinstance(second, ast.Return)
                and second.value is not None
            ):
                return False
            return UnificationRefactorEngine._same_names(first.targets[0], second.value)
        if len(body) != 1:
            return False
        statement = body[0]
        if isinstance(statement, ast.Raise):
            return True
        if isinstance(statement, ast.Return) and isinstance(statement.value, ast.Call):
            return True
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
            return True
        return False

    # ------------------------------------------------------------------
    # Reusing an existing function instead of extracting a redundant helper
    # ------------------------------------------------------------------

    @staticmethod
    def _helper_only_renames(body: Sequence[ast.stmt]) -> bool:
        """Whether the body computes nothing: it binds names to parameters or
        literals and returns some of them.

        Such a helper turns ``a = 0; b = x`` into a call that unpacks a tuple,
        which is longer and says less (pycodestyle's initialization blocks
        once returned variables became extractable). A body with any call,
        operator, attribute, subscript, or compound statement is not this.
        """
        if not body:
            return False

        def is_plain(value: ast.expr) -> bool:
            if isinstance(value, (ast.Name, ast.Constant)):
                return True
            if isinstance(value, ast.Tuple):
                return all(is_plain(element) for element in value.elts)
            return False

        statements = list(body)
        if isinstance(statements[-1], ast.Return):
            returned = statements[-1].value
            if returned is not None and not is_plain(returned):
                return False
            statements = statements[:-1]
        if not statements:
            return False
        for statement in statements:
            if isinstance(statement, ast.Assign):
                if not is_plain(statement.value) or not all(
                    isinstance(target, (ast.Name, ast.Tuple)) for target in statement.targets
                ):
                    return False
            elif isinstance(statement, ast.AnnAssign):
                if statement.value is None or not is_plain(statement.value):
                    return False
            else:
                return False
        return True

    @staticmethod
    def _same_names(target: ast.expr, returned: ast.expr) -> bool:
        """Whether ``returned`` is exactly the name, or tuple of names, ``target`` binds."""
        if isinstance(target, ast.Name) and isinstance(returned, ast.Name):
            return target.id == returned.id
        if isinstance(target, (ast.Tuple, ast.List)) and isinstance(
            returned, (ast.Tuple, ast.List)
        ):
            return len(target.elts) == len(returned.elts) and all(
                isinstance(bound, ast.Name) and isinstance(used, ast.Name) and bound.id == used.id
                for bound, used in zip(target.elts, returned.elts)
            )
        return False

    # Optional analysis cache invalidation hook used by directory fixed-point runner
    @property
    def change_log(self) -> Sequence[AppliedChange]:
        """Every call site the last directory run rewrote, in application order."""
        return tuple(self._change_log)

    def invalidate_paths(self, paths: List[str]) -> None:
        self.analysis_session.invalidate(paths)


# Utility functions for overlap filtering
