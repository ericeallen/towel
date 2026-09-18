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
from dataclasses import dataclass
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
    Literal,
    MutableMapping,
    Sequence,
    cast,
)
from weakref import WeakKeyDictionary
from pathlib import Path
from .scope_analyzer import ScopeAnalyzer, Scope
from .unifier import Unifier, Substitution
from .extractor import (
    HygienicExtractor,
    UnsupportedExtraction,
    has_complete_return_coverage,
    is_value_producing,
)
from .instantiation import instantiation_mismatch
from .thunk_inlining import inline_leading_thunks
from .structural_memo import (
    StoredSubstitution,
    load_substitution,
    store_substitution,
    structural_id,
)
from .definite_assignment import (
    definitely_bound_after,
    definitely_bound_before,
    locally_bound_names,
)
from .orphan_detector import has_orphaned_variables
from .assignment_analyzer import (
    analyze_assignments,
    has_reassignments_without_bindings,
    _collect_block_binding_stats,
    _collect_bindings_and_reassignments,
)
from .progress import load_tqdm, quietly
from .fixed_point import FixedPointDrivers
from .materialize import Materialization
from .annotation_wiring import HelperAnnotationWiring
from .reuse import ExistingFunctionReuse
from .placement import HelperPlacement
from .parallel import ParallelEvaluation
from .clustering import Clustering
from .insertion import InsertionPoints
from ..diagnostics import LOG, REJECTIONS, VALIDATION, Settings, debugging
from .parameters import parameter_names, fresh_parameter_name
from .semantic_safety import (
    nested_bindings_escape,
    uses_class_private_names,
    snapshots_rebound_external_names,
    requires_original_frame,
    would_create_import_cycle,
    ImportGraphCache,
    unbinds_external_name,
    nested_scopes_cross_block_boundary,
    has_impure_eager_parameters,
    defer_impure_parameters,
    moves_scope_declaration,
)
from .block_signature import BlockSignature, extract_block_signature, quick_filter
from .models import (
    CodeBlockPair,
    FunctionArtifact,
    ClassInfo,
    ClassInsertionPlan,
    Replacement,
    RefactoringProposal,
    RejectReason,
    AppliedChange,
    FunctionNode,
    BlockBindingSnapshot,
    HelperTemplate,
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


@dataclass(frozen=True)
class _PairContext:
    """Each block's resolved function, scope analyzer, and root scope."""

    func1: Optional[FunctionNode]
    func2: Optional[FunctionNode]
    scope_analyzer: Optional[ScopeAnalyzer]
    scope_analyzer2: Optional[ScopeAnalyzer]
    root_scope: Optional[Scope]
    # The analyzer discovered for block1's own function, before falling back to
    # the pair-provided analyzer; some downstream checks need the raw value.
    scope_analyzer1: Optional[ScopeAnalyzer]


class UnificationRefactorEngine(
    InsertionPoints,
    HelperPlacement,
    ExistingFunctionReuse,
    HelperAnnotationWiring,
    Materialization,
    FixedPointDrivers,
    ParallelEvaluation,
    Clustering,
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
        type_inferrer: Optional[TypeOracle] = None,
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
                (default: True). Nothing is inferred unless ``type_inferrer``
                is given.
            type_inferrer: Asked, when a proposal is applied, for the types of
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
        self.type_inferrer = type_inferrer
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
        progress: str = "tqdm",
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
        progress: str = "tqdm",
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
        progress: str,
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
        progress: str = "none",
        changed_files: Optional[FrozenSet[str]] = None,
    ) -> List[CodeBlockPair]:
        """
        Find all non-overlapping pairs of code blocks across multiple files.

        Args:
            all_functions: List of (file_path, function, source, scope_analyzer, root_scope)

        Returns:
            List of code block pairs
        """
        from .block_signature import BlockBucketKey, BlockSignature, signature_bucket_key

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
        use_tqdm = progress in ("tqdm", "auto")
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

        use_inline = (not use_tqdm) and progress in ("tqdm", "auto") and total_func_pairs > 0
        last_pct = -1
        self._start_inline_status("Pairing blocks:", use_inline)

        func_pairs_done = 0

        # For each pair of functions (including across files)
        for i, entry1 in enumerate(all_functions):
            # Backward compatibility: allow 5-tuples (no class context)
            if len(entry1) >= 8:
                file1, func1, source1, analyzer1, scope1, class1, encl1, anc1 = entry1
            elif len(entry1) == 7:
                file1, func1, source1, analyzer1, scope1, class1, encl1 = entry1
                anc1 = []
            else:
                file1, func1, source1, analyzer1, scope1 = entry1
                class1 = None
                encl1 = None
                anc1 = []

            file1_changed = changed_files is None or file1 in changed_files
            for j, entry2 in enumerate(all_functions[i + 1 :], i + 1):
                if (
                    changed_files is not None
                    and not file1_changed
                    and entry2[0] not in changed_files
                ):
                    continue  # both files unchanged since the last global pass: verdict stands
                if len(entry2) >= 8:
                    file2, func2, source2, analyzer2, scope2, class2, encl2, anc2 = entry2
                elif len(entry2) == 7:
                    file2, func2, source2, analyzer2, scope2, class2, encl2 = entry2
                    anc2 = []
                else:
                    file2, func2, source2, analyzer2, scope2 = entry2
                    class2 = None
                    encl2 = None
                    anc2 = []
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
                            file_path=file1,
                            function1_name=func1.name,
                            function2_name=func2.name,
                            block1_range=block1_range,
                            block2_range=block2_range,
                            block1_nodes=block1_nodes,
                            block2_nodes=block2_nodes,
                            file_path2=file2,
                            class1_name=class1,
                            class2_name=class2,
                            enclosing_function1_name=encl1,
                            enclosing_function2_name=encl2,
                            function1_ancestry=anc1,
                            function2_ancestry=anc2,
                            scope_analyzer1=analyzer1,
                            scope_analyzer2=analyzer2,
                            root_scope1=scope1,
                            root_scope2=scope2,
                            source1=source1,
                            source2=source2,
                            function1_node=func1,
                            function2_node=func2,
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

    def _resolve_pair_context(
        self,
        pair: CodeBlockPair,
        all_functions: Sequence[FunctionArtifact],
    ) -> "_PairContext":
        """Resolve each block's function, scope analyzer, and root scope.

        Prefers the analyzer/scope discovered for the block's own function in the
        aggregated function list, falling back to the values carried on the pair.
        """
        # Resolve contextual analyzers and scopes from the aggregated function list
        func1: Optional[FunctionNode] = pair.function1_node
        func2: Optional[FunctionNode] = pair.function2_node
        scope_analyzer1: Optional[ScopeAnalyzer] = None
        scope_analyzer2: Optional[ScopeAnalyzer] = None
        root_scope1: Optional[Scope] = None
        root_scope2: Optional[Scope] = None

        for entry in all_functions:
            file_path = entry.file_path
            func = entry.node
            analyzer = entry.scope_analyzer
            root_scope_entry = entry.root_scope
            same1 = (
                func is pair.function1_node
                if pair.function1_node is not None
                else func.name == pair.function1_name
            )
            if func1 is None and file_path == pair.file_path and same1:
                func1 = func
                scope_analyzer1 = analyzer
                root_scope1 = root_scope_entry
            same2 = (
                func is pair.function2_node
                if pair.function2_node is not None
                else func.name == pair.function2_name
            )
            if func2 is None and file_path == (pair.file_path2 or pair.file_path) and same2:
                func2 = func
                scope_analyzer2 = analyzer
                root_scope2 = root_scope_entry
            if func1 is not None and func2 is not None:
                break

        # Fallback to the pair-provided analyzers/scopes when discovery fails
        scope_analyzer = scope_analyzer1 or pair.scope_analyzer1
        root_scope = root_scope1 or pair.root_scope1
        if scope_analyzer2 is None and pair.scope_analyzer2 is not None:
            scope_analyzer2 = pair.scope_analyzer2
        if root_scope2 is None and pair.root_scope2 is not None:
            root_scope2 = pair.root_scope2
        return _PairContext(
            func1=func1,
            func2=func2,
            scope_analyzer=scope_analyzer,
            scope_analyzer2=scope_analyzer2,
            root_scope=root_scope,
            scope_analyzer1=scope_analyzer1,
        )

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

    def _try_refactor_pair_multi_file(
        self,
        pair: CodeBlockPair,
        all_functions: Sequence[FunctionArtifact],
        class_infos: List[ClassInfo],
    ) -> Optional[RefactoringProposal]:
        """
        Try to refactor a pair of code blocks using unification (cross-file support).

        Args:
            pair: Code block pair (may be cross-file)
            all_functions: All functions being analyzed
            class_infos: Metadata about classes discovered in analyzed files

        Returns:
            Refactoring proposal or None
        """
        if self._block_rejected(
            requires_original_frame, pair.block1_nodes, path=pair.file_path
        ) or self._block_rejected(
            requires_original_frame, pair.block2_nodes, path=pair.file_path2 or pair.file_path
        ):
            self._debug_reject(RejectReason.FRAME_SENSITIVE_BLOCK, pair)
            return None

        debug_enabled = debugging(VALIDATION)

        if debug_enabled:
            VALIDATION.debug("\n=== _try_refactor_pair_multi_file called ===")
            VALIDATION.debug(f"Functions: {pair.function1_name} and {pair.function2_name}")
            VALIDATION.debug(f"Block1 range: {pair.block1_range}")
            VALIDATION.debug(f"Block2 range: {pair.block2_range}")

        ctx = self._resolve_pair_context(pair, all_functions)
        func1 = ctx.func1
        func2 = ctx.func2
        scope_analyzer = ctx.scope_analyzer
        scope_analyzer2 = ctx.scope_analyzer2
        root_scope = ctx.root_scope
        scope_analyzer1 = ctx.scope_analyzer1

        if (
            func1 is not None
            and scope_analyzer is not None
            and self._block_rejected(
                snapshots_rebound_external_names, pair.block1_nodes, func1, scope_analyzer
            )
        ) or (
            func2 is not None
            and scope_analyzer2 is not None
            and self._block_rejected(
                snapshots_rebound_external_names, pair.block2_nodes, func2, scope_analyzer2
            )
        ):
            self._debug_reject(RejectReason.REBOUND_EXTERNAL_BINDING, pair)
            return None

        # A function nested inside a method shares the class for name mangling
        # but has no receiver; only a function defined directly in the class
        # body dispatches as a method.
        method_info1 = self._get_method_context(
            func1, self._method_class(func1, pair.class1_name, scope_analyzer)
        )
        method_info2 = self._get_method_context(
            func2, self._method_class(func2, pair.class2_name, scope_analyzer2)
        )

        for guard, reason in (
            (nested_bindings_escape, RejectReason.NESTED_BINDING_ESCAPES),
            (nested_scopes_cross_block_boundary, RejectReason.CLOSURE_CROSSES_BLOCK_BOUNDARY),
            (moves_scope_declaration, RejectReason.MOVES_SCOPE_DECLARATION),
        ):
            if (func1 is not None and self._block_rejected(guard, pair.block1_nodes, func1)) or (
                func2 is not None and self._block_rejected(guard, pair.block2_nodes, func2)
            ):
                self._debug_reject(reason, pair)
                return None

        # (Removed specialized full-body extraction fast-path; reverting to generic pairing logic.)

        if debug_enabled:
            VALIDATION.debug("\n=== Finding Functions ===")
            VALIDATION.debug(f"Looking for: {pair.function1_name} and {pair.function2_name}")
            VALIDATION.debug(f"Found func1: {func1 is not None}")
            VALIDATION.debug(f"Found func2: {func2 is not None}")

        # CRITICAL: Validate that blocks don't contain reassignments without initial bindings
        # Initialize return_variables tracking
        # This will be populated if we find variables that need to be returned
        return_variables_block1: Set[str] = set()
        return_variables_block2: Set[str] = set()

        bound_in_block1: Set[str] = set()
        bound_before_block1: Set[str] = set()
        bound_after_block1: Set[str] = set()
        initially_bound1: Set[str] = set()

        bound_in_block2: Set[str] = set()
        bound_before_block2: Set[str] = set()
        bound_after_block2: Set[str] = set()
        initially_bound2: Set[str] = set()

        # This prevents extracting code like "result = result + 10" when "result = x * 2"
        # is outside the block. Such extractions are fundamentally unsound.
        if func1 and func2:
            # Analyze assignments in both functions
            reassignments1 = self._get_assignment_reuse(func1)
            reassignments2 = self._get_assignment_reuse(func2)

            # Check if block1 contains reassignments without bindings
            has_unsafe1, problematic_vars1 = self._per_block(
                "reassignments",
                func1,
                pair.block1_nodes,
                lambda: has_reassignments_without_bindings(
                    func1, pair.block1_nodes, reassignments1
                ),
            )
            if has_unsafe1:
                self._debug_reject(
                    RejectReason.UNSAFE_REASSIGNMENT_BLOCK1, pair, str(problematic_vars1)
                )
                return None

            # Check if block2 contains reassignments without bindings
            has_unsafe2, problematic_vars2 = self._per_block(
                "reassignments",
                func2,
                pair.block2_nodes,
                lambda: has_reassignments_without_bindings(
                    func2, pair.block2_nodes, reassignments2
                ),
            )
            if has_unsafe2:
                self._debug_reject(
                    RejectReason.UNSAFE_REASSIGNMENT_BLOCK2, pair, str(problematic_vars2)
                )
                return None

            block1_snapshot = self._build_block_binding_snapshot(
                func1, pair.block1_nodes, pair.block1_range, reassignments1
            )
            block2_snapshot = self._build_block_binding_snapshot(
                func2, pair.block2_nodes, pair.block2_range, reassignments2
            )

            bound_in_block1 = block1_snapshot.bound_in_block
            bound_before_block1 = block1_snapshot.bound_before_block
            bound_after_block1 = block1_snapshot.bound_after_block
            initially_bound1 = block1_snapshot.initially_bound

            bound_in_block2 = block2_snapshot.bound_in_block
            bound_before_block2 = block2_snapshot.bound_before_block
            bound_after_block2 = block2_snapshot.bound_after_block
            initially_bound2 = block2_snapshot.initially_bound

            if self._per_block(
                "unbinds",
                func1,
                pair.block1_nodes,
                lambda: unbinds_external_name(func1, pair.block1_nodes, bound_before_block1),
            ) or self._per_block(
                "unbinds",
                func2,
                pair.block2_nodes,
                lambda: unbinds_external_name(func2, pair.block2_nodes, bound_before_block2),
            ):
                self._debug_reject(RejectReason.UNBINDS_EXTERNAL_NAME, pair)
                return None

            if debug_enabled:
                VALIDATION.debug("\n=== Block1 Validation Debug ===")
                VALIDATION.debug(f"Function: {pair.function1_name}")
                VALIDATION.debug(f"Block lines: {pair.block1_range}")
                VALIDATION.debug(f"Bound in block: {bound_in_block1}")
                VALIDATION.debug(f"Bound before block: {bound_before_block1}")
                VALIDATION.debug(f"Newly bound in block: {initially_bound1}")

            return_variables_block1 = self._find_return_variables(
                func1,
                pair.block1_range,
                initially_bound1,
                debug_label="Block1 Validation Debug" if debug_enabled else None,
            )

            if debug_enabled:
                VALIDATION.debug("\n=== Block2 Validation Debug ===")
                VALIDATION.debug(f"Function: {pair.function2_name}")
                VALIDATION.debug(f"Block lines: {pair.block2_range}")
                VALIDATION.debug(f"Bound in block: {bound_in_block2}")
                VALIDATION.debug(f"Bound before block: {bound_before_block2}")
                VALIDATION.debug(f"Newly bound in block: {initially_bound2}")

            return_variables_block2 = self._find_return_variables(
                func2,
                pair.block2_range,
                initially_bound2,
                debug_label="Block2 Validation Debug" if debug_enabled else None,
            )

        # Check if both blocks are value-producing or both are not
        # Blocks with return_variables are treated as value-producing because
        # we will add return statements for those variables
        value_prod1 = self._is_value_producing(pair.block1_nodes) or bool(return_variables_block1)
        value_prod2 = self._is_value_producing(pair.block2_nodes) or bool(return_variables_block2)

        if debug_enabled:
            VALIDATION.debug(f"  Value-producing check: block1={value_prod1}, block2={value_prod2}")
            if return_variables_block1:
                VALIDATION.debug(f"  Block1 has return_variables: {return_variables_block1}")
            if return_variables_block2:
                VALIDATION.debug(f"  Block2 has return_variables: {return_variables_block2}")

        if value_prod1 != value_prod2:
            if debug_enabled:
                VALIDATION.debug("  REJECTED: Value-producing mismatch")
            self._debug_reject(RejectReason.VALUE_PRODUCING_MISMATCH, pair)
            return None

        # CRITICAL: If blocks are NATURALLY value-producing (have return statements),
        # ensure complete return coverage. This prevents extracting partial control flow.
        # Skip this check for blocks that will have return statements ADDED for return_variables
        if value_prod1 and not return_variables_block1:  # Naturally value-producing
            from .extractor import has_complete_return_coverage

            if not has_complete_return_coverage(cast(List[ast.stmt], pair.block1_nodes)):
                if debug_enabled:
                    VALIDATION.debug("  REJECTED: Block1 missing complete return coverage")
                self._debug_reject(RejectReason.INCOMPLETE_RETURN_COVERAGE_BLOCK1, pair)
                return None
            if not has_complete_return_coverage(cast(List[ast.stmt], pair.block2_nodes)):
                if debug_enabled:
                    VALIDATION.debug("  REJECTED: Block2 missing complete return coverage")
                self._debug_reject(RejectReason.INCOMPLETE_RETURN_COVERAGE_BLOCK2, pair)
                return None

        # Heuristic: avoid extracting trivial single-line return blocks that just
        # return a previously bound local name (e.g., `return result`). Prefer
        # extracting the preceding computation that produces the value.
        def _is_trivial_return_of_bound_name(
            block_nodes: List[ast.AST], bound_before_block: Set[str], bound_in_block: Set[str]
        ) -> bool:
            if len(block_nodes) != 1:
                return False
            stmt = block_nodes[0]
            return (
                isinstance(stmt, ast.Return)
                and isinstance(stmt.value, ast.Name)
                and stmt.value.id in bound_before_block
                and stmt.value.id not in bound_in_block
            )

        if _is_trivial_return_of_bound_name(
            pair.block1_nodes, bound_before_block1, bound_in_block1
        ) and _is_trivial_return_of_bound_name(
            pair.block2_nodes, bound_before_block2, bound_in_block2
        ):
            if debug_enabled:
                VALIDATION.debug(
                    "  REJECTED: Trivial single-line return blocks (prefer extracting computation)"
                )
            self._debug_reject(RejectReason.TRIVIAL_RETURN_BLOCKS, pair)
            return None

        # Check structural similarity
        if not self._are_structurally_similar(pair.block1_nodes, pair.block2_nodes):
            if debug_enabled:
                VALIDATION.debug("  REJECTED: Not structurally similar")
            self._debug_reject(RejectReason.NOT_STRUCTURALLY_SIMILAR, pair)
            return None

        # Attempt unification
        blocks = [pair.block1_nodes, pair.block2_nodes]
        hygienic_renames: List[Dict[str, str]] = [{}, {}]

        if debug_enabled:
            VALIDATION.debug("  Attempting unification...")

        substitution = self._unify_memoized(
            blocks, hygienic_renames, (pair.file_path, pair.file_path2 or pair.file_path)
        )

        if not substitution:
            if debug_enabled:
                VALIDATION.debug("  REJECTED: Unification failed (no substitution)")
            self._debug_reject(RejectReason.UNIFICATION_FAILED, pair)
            return None

        if debug_enabled:
            VALIDATION.debug("  ✓ Unification successful")
            VALIDATION.debug(f"  Substitution: {substitution}")

        aligned = _align_return_variables(
            return_variables_block1,
            return_variables_block2,
            bound_in_block1,
            bound_in_block2,
            hygienic_renames,
        )
        if aligned is None:
            self._debug_reject(RejectReason.RETURN_VARIABLES_NOT_ALIGNED, pair)
            return None
        ordered_return_variables = aligned
        if ordered_return_variables[0] and (
            self._is_value_producing(pair.block1_nodes)
            or self._is_value_producing(pair.block2_nodes)
        ):
            # A call statement is either `x = helper()` or `return helper()`;
            # a block that both returns early and binds live variables needs both.
            self._debug_reject(RejectReason.MIXED_RETURN_AND_VARIABLES, pair)
            return None

        # Pre-compute the deepest common enclosing function (for same-file cases)
        dce_insert_func: Optional[str] = None
        dce_node: Optional[FunctionNode] = None
        same_file_ctx = pair.file_path2 is not None and pair.file_path2 == pair.file_path
        if same_file_ctx:
            dce_insert_func = self._deepest_common_ancestry(
                pair.function1_ancestry, pair.function2_ancestry
            )
            # Ancestry is a list of names, and methods of different classes
            # share names (prompt_toolkit: two ``_all_children``). The helper
            # may go into a function only when exactly one function of that
            # name encloses both blocks' functions.
            if dce_insert_func and func1 is not None and func2 is not None:
                dce_node = self._enclosing_function_named(
                    dce_insert_func, pair.file_path, all_functions, (func1, func2)
                )
                if dce_node is None:
                    dce_insert_func = None

        # Get enclosing names to avoid shadowing (module-level by default)
        enclosing_names = set(root_scope.bindings.keys()) if root_scope else set()
        # If we plan to insert into a specific function scope (DCE), enrich hygiene set with that
        # function's local bindings to avoid name collisions
        if dce_insert_func:
            for artifact in all_functions:
                fpath, fn, analyzer = artifact.file_path, artifact.node, artifact.scope_analyzer
                if fpath == (pair.file_path2 or pair.file_path) and fn.name == dce_insert_func:
                    func_scope = analyzer.node_scopes.get(fn)
                    if func_scope is not None:
                        enclosing_names.update(func_scope.bindings.keys())
                    break
        # Hygiene improvement: if we'll insert into a specific function scope (DCE),
        # include that function's local bindings to avoid name collisions.
        same_file_for_hygiene = pair.file_path2 is not None and pair.file_path2 == pair.file_path

        target_insert_fn: Optional[str] = None
        if same_file_for_hygiene:
            target_insert_fn = self._deepest_common_ancestry(
                pair.function1_ancestry, pair.function2_ancestry
            )
        if target_insert_fn:
            # Locate the target function node and its scope analyzer for this file
            target_func_node = None
            target_analyzer: Optional[ScopeAnalyzer] = None
            for artifact in all_functions:
                fpath, fn, analyzer = artifact.file_path, artifact.node, artifact.scope_analyzer
                if fpath == pair.file_path and fn.name == target_insert_fn:
                    target_func_node = fn
                    target_analyzer = analyzer
                    break
            if target_func_node is not None and target_analyzer is not None:
                func_scope = target_analyzer.node_scopes.get(target_func_node)
                if func_scope is not None:
                    enclosing_names.update(func_scope.bindings.keys())

        # Compute free variables for both blocks (variables used but not defined in each block)
        # Use block1's free variables to derive parameters for the extracted function,
        # but validate incomplete lifetimes independently for each block.
        free_vars1 = (
            scope_analyzer.get_free_variables(pair.block1_nodes) if scope_analyzer else set()
        )
        free_vars2 = (
            scope_analyzer2.get_free_variables(pair.block2_nodes) if scope_analyzer2 else set()
        )

        # CRITICAL VALIDATION: Reject proposals with incomplete variable lifetimes
        # A free variable bound AFTER the block is problematic - we'd be using it before it's defined.
        # However, free variables bound BEFORE the block are OK - they become parameters.
        # The helper ends with ``return (v, ...)``. A variable bound only on some
        # path through the block, such as inside a branch that raises, is unbound
        # there unless it entered as a parameter; the original block left the
        # caller's binding untouched on that path instead of raising.
        for index, (nodes, entering) in enumerate(
            ((pair.block1_nodes, free_vars1), (pair.block2_nodes, free_vars2))
        ):
            bound_at_exit = definitely_bound_after(cast(List[ast.stmt], nodes))
            if bound_at_exit is None:
                continue
            not_definite = [
                name
                for name in ordered_return_variables[index]
                if name not in entering and name not in bound_at_exit
            ]
            if not_definite:
                self._debug_reject(RejectReason.CONDITIONALLY_BOUND_RETURN, pair, str(not_definite))
                return None
        if free_vars1 & bound_after_block1:
            incomplete_vars = free_vars1 & bound_after_block1
            if debug_enabled:
                VALIDATION.debug(
                    f"  REJECTED: Block1 uses variables defined AFTER the block: {incomplete_vars}"
                )
                VALIDATION.debug("    These variables would be used before they're defined")
            self._debug_reject(RejectReason.INCOMPLETE_LIFETIME_BLOCK1, pair, str(incomplete_vars))
            return None

        if free_vars2 & bound_after_block2:
            incomplete_vars = free_vars2 & bound_after_block2
            if debug_enabled:
                VALIDATION.debug(
                    f"  REJECTED: Block2 uses variables defined AFTER the block: {incomplete_vars}"
                )
            self._debug_reject(RejectReason.INCOMPLETE_LIFETIME_BLOCK2, pair, str(incomplete_vars))
            return None

        aug_assign_vars = self._reserve_augassign_params(pair, substitution)

        self._strip_fstring_params(substitution)

        free_vars = self._working_free_vars(substitution, aug_assign_vars, free_vars1)

        if self._rejects_module_data_lookup(
            pair,
            scope_analyzer1 or pair.scope_analyzer1,
            scope_analyzer2 or pair.scope_analyzer2,
        ):
            return None

        # CRITICAL: Check if any free variables are declared global or nonlocal
        # If a free variable is global/nonlocal, we cannot parameterize it
        # because you cannot have a parameter that is also declared global/nonlocal
        # This would create: SyntaxError: name 'x' is parameter and global
        assert scope_analyzer is not None
        globals_to_declare_in_extracted, nonlocals_to_declare_in_extracted, free_vars = (
            self._global_nonlocal_declarations(pair, scope_analyzer, free_vars)
        )

        defer_impure_parameters(substitution, pair.block1_nodes)
        if func1 is not None and func2 is not None:
            free_vars = _thunk_uncertain_free_variables(
                substitution,
                free_vars,
                ((func1, pair.block1_nodes), (func2, pair.block2_nodes)),
                hygienic_renames,
            )

        # Extract function
        try:
            func_def, param_order = self.extractor.extract_function(
                template_block=pair.block1_nodes,
                substitution=substitution,
                free_variables=free_vars,
                enclosing_names=enclosing_names,
                is_value_producing=value_prod1,
                return_variables=list(ordered_return_variables[0]),
                global_decls=(
                    globals_to_declare_in_extracted if globals_to_declare_in_extracted else None
                ),
                nonlocal_decls=(
                    nonlocals_to_declare_in_extracted if nonlocals_to_declare_in_extracted else None
                ),
                # Use hygienic double-underscore name; engine will prefix underscore for methods.
                function_name="__extracted_func",
            )
        except UnsupportedExtraction:
            return None

        inline_leading_thunks(func_def, substitution, param_order)
        if has_impure_eager_parameters(substitution):
            self._debug_reject(RejectReason.IMPURE_EAGER_PARAMETER, pair)
            return None
        helper_preamble_length = int(bool(globals_to_declare_in_extracted)) + int(
            bool(nonlocals_to_declare_in_extracted)
        )

        # Check for orphaned variables before proceeding
        # Need to find the function nodes to check for orphans
        func1_for_orphans: Optional[FunctionNode] = None
        func2_for_orphans: Optional[FunctionNode] = None
        for entry in all_functions:
            file_path, func = entry.file_path, entry.node
            if file_path == pair.file_path and func.name == pair.function1_name:
                func1_for_orphans = func
            if (
                file_path == (pair.file_path2 or pair.file_path)
                and func.name == pair.function2_name
            ):
                func2_for_orphans = func

        if func1_for_orphans and func2_for_orphans:
            # Get block indices within their respective function bodies
            indices1 = self._get_block_indices(func1_for_orphans, pair.block1_nodes)
            indices2 = self._get_block_indices(func2_for_orphans, pair.block2_nodes)

            if indices1 and indices2:
                # Get function bodies (skip docstring)
                body1 = func1_for_orphans.body
                start_idx1 = 0
                if (
                    body1
                    and isinstance(body1[0], ast.Expr)
                    and isinstance(body1[0].value, ast.Constant)
                    and isinstance(body1[0].value.value, str)
                ):
                    start_idx1 = 1
                body1 = body1[start_idx1:]

                body2 = func2_for_orphans.body
                start_idx2 = 0
                if (
                    body2
                    and isinstance(body2[0], ast.Expr)
                    and isinstance(body2[0].value, ast.Constant)
                    and isinstance(body2[0].value.value, str)
                ):
                    start_idx2 = 1
                body2 = body2[start_idx2:]

                # Check for orphaned variables in both blocks. A name the helper
                # returns is rebound by the generated call on every path out of
                # the block, so a later read of it is not orphaned.
                _, orphans1 = has_orphaned_variables(cast(List[ast.AST], body1), indices1)
                _, orphans2 = has_orphaned_variables(cast(List[ast.AST], body2), indices2)
                orphans1 -= set(ordered_return_variables[0])
                orphans2 -= set(ordered_return_variables[1])

                if orphans1 or orphans2:
                    # Cannot extract - would create orphaned variable references
                    self._debug_reject(
                        RejectReason.ORPHANED_VARIABLES,
                        pair,
                        detail=str(sorted(orphans1 | orphans2)),
                    )
                    return None

        # Generate replacement calls
        replacements: List[Replacement] = []
        # Method context of each clustered call site, by index in ``replacements``
        cluster_contexts: Dict[int, Tuple[Optional[str], Optional[str], Optional[str], bool]] = {}

        # Map block indices to their return variables, in one shared order
        return_vars_by_block = {
            0: list(ordered_return_variables[0]),
            1: list(ordered_return_variables[1]),
        }

        for block_idx, (block_range, file_path) in enumerate(
            [
                (pair.block1_range, pair.file_path),
                (pair.block2_range, pair.file_path2 or pair.file_path),
            ]
        ):
            try:
                call_node = self.extractor.generate_call(
                    function_name=func_def.name,
                    block_idx=block_idx,
                    substitution=substitution,
                    param_order=param_order,
                    free_variables=free_vars,
                    is_value_producing=value_prod1,
                    return_variables=return_vars_by_block[block_idx],
                    hygienic_renames=hygienic_renames,
                )
                # Guard-rail: validate that the generated call does not reference
                # undefined names at the call site. This rejects brittle proposals
                # that leak placeholders (e.g., "__param_1") or invented locals
                # like "filtered"/"mapped" that are not bound before the block.
                # Allowed names = variables bound before the block ∪ free variables
                # (builtins are implicitly allowed by runtime).
                allowed_before: Set[str]
                if block_idx == 0:
                    allowed_before = set(bound_before_block1) | set(free_vars1)
                else:
                    allowed_before = set(bound_before_block2) | set(free_vars2)

                used_in_call = self._get_used_names(call_node)
                # Whitelist a small set of common builtins used in arguments
                builtin_whitelist = {
                    "len",
                    "sum",
                    "min",
                    "max",
                    "any",
                    "all",
                    "map",
                    "filter",
                    "sorted",
                    "list",
                    "dict",
                    "set",
                    "range",
                    "int",
                    "float",
                    "str",
                    "bool",
                    "enumerate",
                    "zip",
                }
                invalid_names = set()
                for name in used_in_call:
                    if name == func_def.name:
                        continue  # referring to the helper itself is handled elsewhere
                    if name.startswith("__param_"):
                        invalid_names.add(name)
                        continue
                    if name in allowed_before or name in builtin_whitelist:
                        continue
                    invalid_names.add(name)

                if invalid_names:
                    # Reject this proposal as it would introduce undefined names
                    self._debug_reject(
                        RejectReason.UNDEFINED_NAMES_IN_CALL,
                        pair,
                        detail=f"block{block_idx+1}: {sorted(invalid_names)}",
                    )
                    return None
                mismatch = instantiation_mismatch(
                    func_def,
                    call_node,
                    pair.block1_nodes if block_idx == 0 else pair.block2_nodes,
                    hygienic_renames[0],
                    hygienic_renames[block_idx],
                    preamble_length=helper_preamble_length,
                    returns_variables=bool(ordered_return_variables[0]),
                )
                if mismatch is not None:
                    self._debug_reject(
                        RejectReason.INSTANTIATION_MISMATCH,
                        pair,
                        detail=f"block{block_idx+1}: {mismatch}",
                    )
                    return None
                # Store file_path and class context
                class_name = pair.class1_name if block_idx == 0 else pair.class2_name
                method_info = method_info1 if block_idx == 0 else method_info2
                replacements.append(
                    Replacement(
                        line_range=block_range,
                        node=call_node,
                        file_path=file_path,
                        class_name=class_name,
                        method_kind=method_info.kind,
                        implicit_param=method_info.implicit_param,
                    )
                )
            except UnsupportedExtraction:
                return None

        # Multi-occurrence clustering (same-file): look for additional identical blocks
        # beyond the initial pair and include them in this proposal as extra replacements.
        # This helps cases like example1_simple where three functions share the same
        # validator block; by default pairwise selection would only cover two.
        # Only attempt simple clustering for module-level, non-returning validators
        same_file_ctx = (pair.file_path2 is None) or (pair.file_path2 == pair.file_path)
        if same_file_ctx and not return_variables_block1 and not return_variables_block2:
            self._add_clustered_replacements(
                HelperTemplate(
                    pair=pair,
                    func_def=func_def,
                    func_def_dump=ast.dump(func_def),
                    param_order=param_order,
                    preamble_length=helper_preamble_length,
                    free_vars=free_vars,
                    enclosing_names=enclosing_names,
                    is_value_producing=value_prod1,
                    globals_to_declare=globals_to_declare_in_extracted,
                    nonlocals_to_declare=nonlocals_to_declare_in_extracted,
                ),
                dce_node,
                all_functions,
                replacements,
                cluster_contexts,
            )
        # Determine canonical file for extracted function
        # Default to the first file, but this may change if we insert into an ancestor class
        canonical_file = pair.file_path

        # Create proposal
        is_cross_file = pair.file_path2 is not None and pair.file_path != pair.file_path2

        desc = f"Extract common code from {pair.function1_name}"
        if is_cross_file:
            assert pair.file_path2 is not None
            desc += f" ({Path(pair.file_path).name}) and {pair.function2_name} ({Path(pair.file_path2).name})"
        else:
            desc += f" and {pair.function2_name}"

        # Default to module-level insertion; when possible insert into deepest common enclosing function.
        insert_into_class = None
        insert_into_function = None
        method_kind_metadata: Optional[Literal["instance", "classmethod", "staticmethod"]] = None
        method_param_name: Optional[str] = None

        # Consider pairs from the same file even if file_path2 is None (same-file pairing)
        same_file = (pair.file_path2 is None) or (pair.file_path2 == pair.file_path)

        # Prefer insertion into the function that actually contains BOTH blocks when they come from the
        # same function (process_user_data & process_admin_data are top-level siblings: no shared ancestry).
        # If the two functions are the SAME name (duplicates), insert into that function instead of module.
        if same_file:
            # Only consider deepest common enclosing function based on ancestry; do NOT use
            # simple name equality as methods across different classes may share the same
            # name but are not in the same function scope. Name-equality caused incorrectly
            # inserting helpers inside one sibling method.
            if dce_insert_func:
                # The helper is placed textually by function name (FuncLocator),
                # so the name must identify one function in the file. When two
                # classes have a same-named method (tornado: several
                # ``get_handlers``), the deepest common enclosing function is a
                # real, unique node, but the name alone would resolve to the
                # wrong one and the call sites would not see the helper. Every
                # free variable is already a parameter, so a module-level helper
                # is equally correct; fall back to it when the name is ambiguous.
                same_name_functions = {
                    id(a.node)
                    for a in all_functions
                    if a.file_path == canonical_file and a.node.name == dce_insert_func
                }
                if len(same_name_functions) == 1:
                    insert_into_function = dce_insert_func

        class_plan: Optional[ClassInsertionPlan] = None
        if insert_into_function is None:
            class_plan = self._choose_class_insertion(
                pair,
                method_info1,
                method_info2,
                class_infos,
            )

        if class_plan is not None:
            # Refined insertion policy:
            # 1. If both blocks are from the SAME class -> insert into that class.
            # 2. If blocks are from DIFFERENT classes and class_plan points to a COMMON ANCESTOR
            #    that is neither of the concrete classes, insert into ancestor (shared visibility).
            # 3. If class_plan resolves to one of the concrete classes while the other differs ->
            #    fallback to module-level to preserve accessibility (avoid privileging one sibling).
            same_class = pair.class1_name is not None and pair.class1_name == pair.class2_name
            target_is_concrete_sibling = (
                class_plan.class_name in {pair.class1_name, pair.class2_name} and not same_class
            )
            if target_is_concrete_sibling:
                # Helper would become invisible to the other sibling; abort class insertion.
                class_plan = None
            else:
                insert_into_class = class_plan.class_name
                canonical_file = class_plan.file_path
                method_kind_metadata = class_plan.method_kind
                method_param_name = class_plan.implicit_param

        if insert_into_class is not None and cluster_contexts:
            # The helper is a method called through the receiver. A clustered
            # block in another class, in a module-level function, or in a
            # function merely nested in a method has no such receiver, so it
            # keeps its code (pyflakes: sibling TestCase classes).
            expected = (method_info1.kind, method_info1.implicit_param, method_info1.receiver_known)
            replacements = [
                replacement
                for index, replacement in enumerate(replacements)
                if index not in cluster_contexts
                or (
                    cluster_contexts[index][0] in {pair.class1_name, pair.class2_name}
                    and cluster_contexts[index][1:] == expected
                )
            ]

        # SAFETY: Avoid refactoring across closures with nonlocal variables for now.
        # If either containing function declares nonlocal variables, skip this proposal
        # to preserve known semantics and baseline expectations (e.g., closure_adversarial.py).
        if self._declares_nonlocal(func1, scope_analyzer1) or self._declares_nonlocal(
            func2, scope_analyzer2
        ):
            self._debug_reject(RejectReason.NONLOCAL_SAFETY_SKIP, pair)
            return None

        participating_paths = {canonical_file} | {
            replacement.file_path or canonical_file for replacement in replacements
        }
        if len(participating_paths) > 1 and any(
            isinstance(node, ast.Global)
            for a in all_functions
            if a.file_path in participating_paths
            for node in ast.walk(a.node)
        ):
            self._debug_reject(RejectReason.CROSS_MODULE_GLOBAL_DECLARATION, pair)
            return None

        # The helper lives in ``canonical_file`` and every other participating
        # module imports it. Placing it in a module the others already depend on
        # closes an import cycle. When the blocks span modules with no
        # pre-existing cycle among them, at least one module can host the helper
        # without adding a back-edge (the one the others already import), so try
        # each participating module and keep the first safe home rather than
        # relying on the default choice never cycling. Only a plain module-level
        # helper can move; class- or function-scoped insertion is pinned to a
        # location. Decline only when no home is safe (a genuine cycle).
        replacement_files = {
            replacement.file_path or canonical_file for replacement in replacements
        }
        participating = {canonical_file} | replacement_files
        if would_create_import_cycle(canonical_file, participating, self.import_graph):
            safe_home = None
            if insert_into_class is None and insert_into_function is None:
                for candidate in sorted(participating - {canonical_file}):
                    if not would_create_import_cycle(candidate, participating, self.import_graph):
                        safe_home = candidate
                        break
            if safe_home is None:
                self._debug_reject(RejectReason.IMPORT_CYCLE, pair)
                return None
            canonical_file = safe_home

        destination_class = insert_into_class
        if insert_into_function:
            destinations = {
                a.class_name
                for a in all_functions
                if a.file_path == canonical_file and a.node.name == insert_into_function
            }
            destination_class = next(iter(destinations)) if len(destinations) == 1 else None
        for replacement in replacements:
            if replacement.class_name and replacement.class_name != destination_class:
                source_path = replacement.file_path or canonical_file
                for a in all_functions:
                    if (
                        a.file_path == source_path
                        and a.class_name == replacement.class_name
                        and a.node.lineno <= replacement.line_range[0]
                        and (a.node.end_lineno or a.node.lineno) >= replacement.line_range[1]
                        and uses_class_private_names([a.node])
                    ):
                        self._debug_reject(RejectReason.PRIVATE_NAME_LEXICAL_CLASS, pair)
                        return None

        proposal = RefactoringProposal(
            file_path=canonical_file,
            extracted_function=func_def,
            replacements=replacements,  # Now includes file_path
            description=desc,
            parameters_count=len(substitution.param_expressions),
            return_variables=list(ordered_return_variables[0]),
            insert_into_class=insert_into_class,
            insert_into_function=insert_into_function,
            method_kind=method_kind_metadata,
            method_param_name=method_param_name,
            source_digests=tuple(
                sorted(
                    {
                        (a.file_path, hashlib.sha256(a.source.encode("utf-8")).hexdigest())
                        for a in all_functions
                        if a.file_path in participating_paths
                    }
                )
            ),
        )

        if self.skip_trivial_helpers and self._helper_is_trivial_forwarding(
            proposal.extracted_function
        ):
            self._debug_reject(RejectReason.TRIVIAL_FORWARDING_HELPER, pair)
            return None
        if self.reuse_existing_functions:
            redirected = self._redirect_to_existing_function(proposal, all_functions)
            if redirected is not None:
                return redirected
        if self.annotate_helpers:
            proposal = self._with_helper_annotations(proposal, all_functions)
        return proposal

    # Optional analysis cache invalidation hook used by directory fixed-point runner
    @property
    def change_log(self) -> Sequence[AppliedChange]:
        """Every call site the last directory run rewrote, in application order."""
        return tuple(self._change_log)

    def invalidate_paths(self, paths: List[str]) -> None:
        self.analysis_session.invalidate(paths)


# Utility functions for overlap filtering


def _align_return_variables(
    first: Set[str],
    second: Set[str],
    bound_first: Set[str],
    bound_second: Set[str],
    renames: Sequence[Dict[str, str]],
) -> Optional[Tuple[List[str], List[str]]]:
    """Order both blocks' live variables so one helper return serves every call.

    Each block reads its own set of names after the block, possibly under
    different spellings unified by alpha-renaming. The helper returns the
    union, spelled in the template's names and sorted; each call assigns the
    same positions under its own spelling. ``None`` means a live variable of
    one block has no binding in the other, so no single helper can return it.
    """
    template_renames = renames[0] if renames else {}
    block_renames = renames[1] if len(renames) > 1 else {}
    canonical_to_template = {canonical: name for name, canonical in template_renames.items()}
    canonical_to_block = {canonical: name for name, canonical in block_renames.items()}

    def to_template(name: str) -> str:
        canonical = block_renames.get(name, name)
        return canonical_to_template.get(canonical, canonical)

    def to_block(name: str) -> str:
        canonical = template_renames.get(name, name)
        return canonical_to_block.get(canonical, canonical)

    template_names = sorted(set(first) | {to_template(name) for name in second})
    block_names = [to_block(name) for name in template_names]
    if not set(template_names) <= bound_first or not set(block_names) <= bound_second:
        return None
    return template_names, block_names


def _thunk_uncertain_free_variables(
    substitution: Substitution,
    free_variables: Set[str],
    blocks: Sequence[Tuple[FunctionNode, List[ast.AST]]],
    renames: Sequence[Dict[str, str]],
) -> Set[str]:
    """Pass free variables that may be unbound at the call as thunks.

    A free variable read only on some path inside the block, and bound
    before the block only on some path, must be read where the block read
    it. The thunk keeps that timing; the eager argument would raise
    ``UnboundLocalError`` at the call.
    """
    template_renames = renames[0] if renames else {}
    canonical_to_block = [
        {
            canonical: name
            for name, canonical in (renames[index] if index < len(renames) else {}).items()
        }
        for index in range(len(blocks))
    ]

    def spelling(index: int, template_name: str) -> str:
        canonical = template_renames.get(template_name, template_name)
        return canonical_to_block[index].get(canonical, canonical)

    def uncertain(index: int, spelled: str) -> bool:
        function, block = blocks[index]
        if spelled not in locally_bound_names(function):
            return False  # resolves lexically outside the function; not path-dependent
        return spelled not in definitely_bound_before(function, cast(ast.stmt, block[0]))

    # A parameter whose argument is a bare local name is read eagerly too.
    deferred = set(substitution.function_params) | set(substitution.params_used_as_callee)
    for parameter, expressions in substitution.param_expressions.items():
        if parameter in deferred:
            continue
        if any(
            isinstance(expression, ast.Name) and uncertain(index, expression.id)
            for index, expression in expressions
            if index < len(blocks)
        ):
            substitution.function_params[parameter] = []

    remaining = set(free_variables)
    for name in sorted(free_variables):
        if not any(uncertain(index, spelling(index, name)) for index in range(len(blocks))):
            continue
        parameter = _fresh_parameter_name(substitution, blocks)
        for index in range(len(blocks)):
            substitution.add_mapping(
                index, ast.Name(id=spelling(index, name), ctx=ast.Load()), parameter
            )
        substitution.function_params[parameter] = []
        remaining.discard(name)
    return remaining


def _fresh_parameter_name(
    substitution: Substitution, blocks: Sequence[Tuple[FunctionNode, List[ast.AST]]]
) -> str:
    taken = set(substitution.param_expressions) | {
        node.id
        for _, block in blocks
        for statement in block
        for node in ast.walk(statement)
        if isinstance(node, ast.Name)
    }
    name, _ = fresh_parameter_name(taken)
    return name
