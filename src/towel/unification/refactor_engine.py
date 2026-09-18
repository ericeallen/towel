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
import copy
import dataclasses
import hashlib
from towel.changes import ChangePlan, ChangeConflict, apply_changes
import os
import re
from collections import deque
import multiprocessing
import sys
import resource
import textwrap
import threading
import time
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor
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
    Union,
    Sequence,
    cast,
)
from weakref import WeakKeyDictionary
from pathlib import Path
from .scope_analyzer import Binding, ScopeAnalyzer, Scope
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
from .exceptions import RefactoringError
from .orphan_detector import has_orphaned_variables
from .assignment_analyzer import (
    analyze_assignments,
    has_reassignments_without_bindings,
    _collect_block_binding_stats,
    _collect_bindings_and_reassignments,
)
from .project_layout import ProjectLayout, is_package_dir
from .progress import ProgressBarFactory, load_tqdm, quietly, render_inline_bar
from ..diagnostics import LOG, OVERLAP, REJECTIONS, TYPES, VALIDATION, Settings, debugging
from .parameters import parameter_names, fresh_parameter_name
from .semantic_safety import (
    frame_sensitivity_markers,
    imported_definition_sites,
    nested_bindings_escape,
    uses_class_private_names,
    snapshots_rebound_external_names,
    requires_original_frame,
    would_create_import_cycle,
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
    MethodInfo,
    ClassInfo,
    ClassInsertionPlan,
    Replacement,
    RefactoringProposal,
    ReusedFunction,
)
from .annotations import (
    ApplySite,
    CallSite,
    annotate_helper,
    call_in_statement,
    complete_with_any,
    infer_missing_annotations,
    respell_bare,
    sites_use_annotations,
    typing_imports_needed,
)
from ..type_inference import TypeOracle
from .pipeline import run_pipeline, AnalysisSession
from .visitors import (
    body_without_docstring,
    MethodCallRewriter,
    LoopReturnFinder,
    NameCollector,
    AugAssignFinder,
    AssignTargetVisitor,
    ClassLocator,
    FuncLocator,
)

# Configuration defaults
DEFAULT_MAX_PARAMETERS = 5
DEFAULT_MIN_LINES = 3
DEFAULT_SIMILARITY_THRESHOLD = 0.6
DEFAULT_MAX_ITERATIONS = 0  # Unlimited

FunctionNode = Union[ast.FunctionDef, ast.AsyncFunctionDef]


@dataclass(frozen=True)
class BlockBindingSnapshot:
    """Summarized binding data for a block: what it binds, reassigns, and what is bound around it."""

    bound_in_block: Set[str]
    reassigned_in_block: Set[str]
    bound_before_block: Set[str]
    bound_after_block: Set[str]
    initially_bound: Set[str]


@dataclass(frozen=True)
class _HelperTemplate:
    """The template helper a clustered occurrence must reproduce to reuse it.

    These values are fixed for a given (pair, extracted helper) and are shared
    across every candidate occurrence tested against that helper.
    """

    pair: CodeBlockPair
    func_def: ast.FunctionDef
    func_def_dump: str
    param_order: Dict[str, int]
    preamble_length: int
    free_vars: Set[str]
    enclosing_names: Set[str]
    is_value_producing: bool
    globals_to_declare: Set[str]
    nonlocals_to_declare: Set[str]


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


@dataclass(frozen=True)
class _ClusterCandidate:
    """A candidate occurrence tested for whether it can share a helper."""

    file_path: str
    function: FunctionNode
    analyzer: Optional[ScopeAnalyzer]
    nodes: List[ast.AST]
    snapshot: BlockBindingSnapshot


_worker_engine: Optional["UnificationRefactorEngine"] = None
_worker_functions: Optional[Sequence[FunctionArtifact]] = None
_worker_class_infos: Optional[List[ClassInfo]] = None


# Decorators that call the decorated function with the same receiver the
# source names. Anything else may rebind the first argument.
_RECEIVER_PRESERVING_DECORATORS = frozenset(
    {
        "property",
        "cached_property",
        "abstractmethod",
        "abstractproperty",
        "lru_cache",
        "cache",
        "contextmanager",
        "asynccontextmanager",
        "overload",
        "final",
        "override",
        "setter",
        "getter",
        "deleter",
    }
)

# Implicit classmethods and staticmethods that carry no decorator.
_IMPLICIT_RECEIVER_SPECIAL_METHODS = frozenset(
    {"__new__", "__init_subclass__", "__class_getitem__"}
)


@dataclass(frozen=True)
class _ReusePlan:
    """How a helper's arguments map onto an existing function it restates.

    ``parameter_positions[j]`` is the helper argument index that supplies the
    function's j-th positional parameter. ``ambient`` maps the remaining
    argument indices to the module-level name (and its binding at the
    function's own site) that the function reads for itself.
    """

    parameter_positions: List[int]
    ambient: Dict[int, Tuple[str, Optional["Binding"]]]


def _preserves_receiver(decorator: ast.expr) -> bool:
    """Whether a decorator is known to pass the receiver through unchanged."""
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    if isinstance(target, ast.Name):
        return target.id in _RECEIVER_PRESERVING_DECORATORS
    if isinstance(target, ast.Attribute):
        return target.attr in _RECEIVER_PRESERVING_DECORATORS
    return False


_worker_pairs: Optional[List[CodeBlockPair]] = None

PARENT_WATCH_INTERVAL_SECONDS = 1.0


def _exit_when_parent_dies(parent: int, interval: float) -> None:
    """Poll the parent pid and end this worker as soon as it is reparented."""
    while os.getppid() == parent:
        time.sleep(interval)
    os._exit(1)


def _start_parent_watchdog() -> None:
    """Run in each forked worker: a killed parent must not leave workers behind.

    A worker checks nothing itself: between chunks it blocks on the pool's
    call queue, whose write end every sibling inherited, so it would wait
    there forever once the parent is gone (fourteen such orphans from a
    killed run once filled a 128 GB machine's swap). A daemon thread that
    polls the parent pid ends the worker within one interval wherever the
    main thread happens to be, mid-pair or idle.
    """
    threading.Thread(
        target=_exit_when_parent_dies,
        args=(os.getppid(), PARENT_WATCH_INTERVAL_SECONDS),
        name="towel-parent-watchdog",
        daemon=True,
    ).start()


def _evaluate_pair_chunk(bounds: Tuple[int, int]) -> List[Tuple[int, RefactoringProposal]]:
    """Evaluate ``_worker_pairs[start:end]`` in a forked worker.

    The worker inherited the parent's engine, function context, pairs and
    caches copy-on-write at fork time, so nothing is pickled in; only the
    accepted proposals travel back.
    """
    if _worker_engine is None or _worker_functions is None or _worker_class_infos is None:
        raise RuntimeError("Worker not initialized for pair processing")
    if _worker_pairs is None:
        raise RuntimeError("Worker has no pairs to evaluate")
    start, end = bounds
    accepted: List[Tuple[int, RefactoringProposal]] = []
    for index in range(start, end):
        proposal = _worker_engine._try_refactor_pair_multi_file(
            _worker_pairs[index], _worker_functions, _worker_class_infos
        )
        if proposal is not None:
            accepted.append((index, proposal))
    return accepted


def _encloses(outer: FunctionNode, inner: FunctionNode) -> bool:
    """Whether ``inner`` is ``outer`` or lies within its source span."""
    if outer is inner:
        return True
    outer_end = outer.end_lineno or outer.lineno
    inner_end = inner.end_lineno or inner.lineno
    return outer.lineno <= inner.lineno and inner_end <= outer_end and outer is not inner


class UnificationRefactorEngine:
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
        self._change_log: List[Dict[str, object]] = []
        # Every file of the current analysis: helper names must be unique
        # across all of them, because any module may import from any other.
        self._analysis_paths: Tuple[str, ...] = ()
        self._signed_block_cache: WeakKeyDictionary[
            FunctionNode, List[Tuple[Tuple[int, int], List[ast.AST], BlockSignature]]
        ] = WeakKeyDictionary()

    # --- Debug helpers ---
    def _debug_reject(
        self, reason: str, pair: "CodeBlockPair", detail: Optional[str] = None
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

    def _cluster_candidate_call(
        self, template: "_HelperTemplate", candidate: "_ClusterCandidate"
    ) -> Optional[ast.AST]:
        """The call replacing a clustered occurrence, or None when it cannot share the helper.

        Everything here is a function of the template and candidate blocks'
        structure, the candidate's function and module, and the pair's helper,
        so the caller memoizes it on exactly those.
        """
        pair = template.pair
        fpath = candidate.file_path
        fn = candidate.function
        analyzerX = candidate.analyzer
        cand_nodes = candidate.nodes
        candidate_snapshot = candidate.snapshot
        free_vars = template.free_vars
        enclosing_names = template.enclosing_names
        value_prod1 = template.is_value_producing
        globals_to_declare_in_extracted = template.globals_to_declare
        nonlocals_to_declare_in_extracted = template.nonlocals_to_declare
        func_def = template.func_def
        func_def_dump = template.func_def_dump
        param_order = template.param_order
        helper_preamble_length = template.preamble_length
        cluster_renames: List[Dict[str, str]] = [{}, {}]
        subst2 = self._unify_memoized(
            [pair.block1_nodes, cand_nodes], cluster_renames, (pair.file_path, fpath)
        )
        if not subst2:
            return None
        defer_impure_parameters(subst2, pair.block1_nodes)
        # Unifying another occurrence may require a different,
        # more general helper. Its parameter numbers alone do
        # not identify the meanings of the existing helper's
        # arguments. Only reuse the helper when extraction from
        # this substitution produces the same body/signature.
        candidate_helper, candidate_order = HygienicExtractor().extract_function(
            template_block=pair.block1_nodes,
            substitution=subst2,
            free_variables=free_vars,
            enclosing_names=enclosing_names,
            is_value_producing=value_prod1,
            global_decls=globals_to_declare_in_extracted or None,
            nonlocal_decls=nonlocals_to_declare_in_extracted or None,
            function_name=func_def.name,
        )
        inline_leading_thunks(candidate_helper, subst2, candidate_order)
        if candidate_order != param_order or ast.dump(candidate_helper) != func_def_dump:
            return None
        if has_impure_eager_parameters(subst2):
            return None
        # Orphan check for candidate within its function body
        indices = self._get_block_indices(fn, cand_nodes)
        if indices is None:
            return None
        # Skip docstring in body
        body = body_without_docstring(fn.body)
        has_orph, _orph = has_orphaned_variables(cast(List[ast.AST], body), indices)
        if has_orph:
            return None
        # Generate a call node for the candidate
        try:
            call_node2 = self.extractor.generate_call(
                function_name=func_def.name,
                block_idx=1,
                substitution=subst2,
                param_order=param_order,
                free_variables=free_vars,
                is_value_producing=value_prod1,
                return_variables=[],
                hygienic_renames=cluster_renames,
            )
        except UnsupportedExtraction:
            return None
        # Validate candidate call-site does not reference undefined names
        used2 = self._get_used_names(call_node2)
        if any(n.startswith("__param_") for n in used2):
            # Skip brittle candidate that leaked placeholders
            return None
        if (
            instantiation_mismatch(
                func_def,
                call_node2,
                cand_nodes,
                cluster_renames[0],
                cluster_renames[1],
                preamble_length=helper_preamble_length,
                returns_variables=False,
            )
            is not None
        ):
            return None
        bound_before_cand: Set[str] = set(candidate_snapshot.bound_before_block)
        free_vars_cand: Set[str] = set()
        if analyzerX is not None:
            free_vars_cand = set(analyzerX.get_free_variables(cand_nodes))
        allowed_cand = bound_before_cand | free_vars_cand
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
        invalid2 = {
            name
            for name in used2
            if name != func_def.name and name not in allowed_cand and name not in builtin_whitelist
        }
        if invalid2:
            return None
        # Append replacement
        return call_node2

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
                self._cache_entries_by_path.setdefault(os.path.abspath(path), []).append(
                    (cache, key)
                )

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

    def _process_block_pairs(
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

    #: Fewer cold pairs than this never fork; above it, a serial prefix is
    #: timed and the rest forks only when the projected serial time exceeds
    #: PARALLEL_MIN_PROJECTED_SECONDS. Pair counts alone misjudge cost: pygments'
    #: lexer modules form tens of thousands of cheap pairs per iteration, and
    #: forking a pool for each iteration made the run three times slower,
    #: while pyflakes' test modules form a few thousand expensive pairs that
    #: a pool halves. The probe, not the count, tells the two apart.
    PARALLEL_PAIR_THRESHOLD = 2000
    PARALLEL_PROBE_PAIRS = 400
    PARALLEL_MIN_PROJECTED_SECONDS = 12.0

    #: Forked workers may keep at most this share of physical memory between
    #: them, estimated from this process's resident size: refcount updates
    #: copy the pages a worker touches, so a large analysis graph costs about
    #: one process size per worker.
    PARALLEL_MEMORY_SHARE = 0.35

    def _parallel_workers(self) -> int:
        """Worker processes to use, or 1 when pair evaluation must stay serial."""
        if self._settings.workers is not None:
            return self._settings.workers
        if "fork" not in multiprocessing.get_all_start_methods():
            return 1
        workers = max(1, os.cpu_count() or 1)
        return max(1, min(workers, self._workers_that_fit_in_memory()))

    @staticmethod
    def _workers_that_fit_in_memory() -> int:
        """How many copies of this process's resident set fit in the allowed memory share."""
        try:
            physical = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
            resident = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            if sys.platform != "darwin":
                resident *= 1024  # Linux reports kilobytes
        except (ValueError, OSError, AttributeError):
            return os.cpu_count() or 1
        if resident <= 0:
            return os.cpu_count() or 1
        return int(physical * UnificationRefactorEngine.PARALLEL_MEMORY_SHARE // resident)

    def _should_use_parallel(self, pair_count: int) -> bool:
        """Whether pair evaluation should fork workers.

        Workers are forked after parsing, so they inherit the ASTs, the
        function context, and every cache copy-on-write; nothing is pickled
        in, and only accepted proposals are pickled out. That is what made the
        earlier pool, which pickled ASTs per task, slower than serial. Pairs
        already memoized in this engine are served serially from the cache.
        """
        return pair_count >= self.PARALLEL_PAIR_THRESHOLD and self._parallel_workers() > 1

    @staticmethod
    def _start_inline_status(label: str, enabled: bool) -> None:
        """Emit the leading inline progress label when progress is enabled."""

        if enabled:
            print(label, end=" ", flush=True)

    @classmethod
    def _update_inline_status(
        cls, label: str, pct: int, *, bar_len: int = 24, suffix: str = ""
    ) -> None:
        """Print an inline progress update with consistent formatting."""

        bar = render_inline_bar(pct, bar_len=bar_len)
        suffix_text = f" {suffix}" if suffix else ""
        print(f"\r{label} [{bar}] {pct:3d}%{suffix_text}", end="", flush=True)

    @staticmethod
    def _finish_inline_status(enabled: bool) -> None:
        """Terminate the inline status line so subsequent logs stay readable."""

        if enabled:
            print()

    @staticmethod
    def _pop_next_proposal(queue: List[RefactoringProposal]) -> Optional[RefactoringProposal]:
        """Remove and return the oldest queued proposal."""

        if not queue:
            return None
        return queue.pop(0)

    def _resolve_progress_backend(
        self, progress: str
    ) -> Tuple[str, Optional[ProgressBarFactory], bool]:
        """Resolve the progress mode and load tqdm if it is available."""

        allowed = {"auto", "tqdm", "none", "detail"}
        normalized = progress if progress in allowed else "tqdm"
        use_tqdm = normalized in {"auto", "tqdm"}
        tqdm_cls: Optional[ProgressBarFactory] = None
        if use_tqdm:
            tqdm_cls = load_tqdm()
            use_tqdm = tqdm_cls is not None
        return normalized, tqdm_cls, use_tqdm

    @staticmethod
    def _function_contains_nonlocal(func: FunctionNode) -> bool:
        """Return True if the function body contains any nonlocal declarations."""

        for stmt in func.body:
            # Skip nested definitions; only consider nonlocal statements that belong
            # to the function itself. Nonlocals inside nested functions do not impact
            # whether the outer function may be safely extracted.
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            for node in ast.walk(stmt):
                if isinstance(node, ast.Nonlocal):
                    return True
        return False

    def _declares_nonlocal(
        self, func: Optional[FunctionNode], scope_analyzer: Optional[ScopeAnalyzer]
    ) -> bool:
        """Return True if ``func`` declares any nonlocal variables of its own.

        Prefers the scope analyzer's precomputed nonlocal set for the function's
        scope; falls back to a direct body scan when no analyzer is available.
        """
        if func is None:
            return False
        if scope_analyzer:
            scope_id = scope_analyzer.node_scopes.get(func)
            if scope_id:
                return bool(scope_analyzer.nonlocal_vars.get(scope_id.scope_id, set()))
            return False
        return self._function_contains_nonlocal(func)

    @staticmethod
    def _decorator_name(decorator: ast.expr) -> Optional[str]:
        """Return the simple name for a decorator expression if it can be resolved."""

        if isinstance(decorator, ast.Name):
            return decorator.id
        if isinstance(decorator, ast.Attribute):
            return decorator.attr
        if isinstance(decorator, ast.Call):
            return UnificationRefactorEngine._decorator_name(decorator.func)
        return None

    @staticmethod
    def _has_decorator(fn: ast.FunctionDef, name: str) -> bool:
        """Return True when the function already carries a decorator with the given name."""

        return any(
            UnificationRefactorEngine._decorator_name(dec) == name for dec in fn.decorator_list
        )

    @staticmethod
    def _strip_decorator(fn: ast.FunctionDef, name: str) -> None:
        """Remove any decorator whose resolved name matches ``name``."""

        fn.decorator_list = [
            dec
            for dec in fn.decorator_list
            if UnificationRefactorEngine._decorator_name(dec) != name
        ]

    @staticmethod
    def _ensure_leading_param(fn: ast.FunctionDef, param_name: str) -> None:
        """Ensure the positional-args list starts with ``param_name`` (preserving annotations)."""

        existing: Optional[ast.arg] = None
        remaining: List[ast.arg] = []
        for arg in fn.args.args:
            if arg.arg == param_name and existing is None:
                existing = arg
                continue
            if arg.arg == param_name:
                # Drop duplicate occurrences beyond the first
                continue
            remaining.append(arg)

        if existing is None:
            existing = ast.arg(arg=param_name)

        fn.args.args = [existing] + remaining

    @staticmethod
    def _retarget_helper_calls(node: ast.AST, original_name: str, final_name: str) -> ast.AST:
        """Rewrite the helper call sites to match a renamed extracted helper."""

        if original_name == final_name:
            return node

        class _CallRenamer(ast.NodeTransformer):
            def __init__(self, old: str, new: str) -> None:
                self.old = old
                self.new = new

            def visit_Call(self, call: ast.Call) -> ast.AST:
                updated = cast(ast.Call, self.generic_visit(call))
                if isinstance(updated.func, ast.Name) and updated.func.id == self.old:
                    updated.func.id = self.new
                return updated

        return cast(ast.AST, _CallRenamer(original_name, final_name).visit(node))

    @staticmethod
    def _scan_module_docstring_and_imports(lines: List[str]) -> Tuple[int, int]:
        """Return the line after the last import and the module docstring boundary."""

        in_docstring = False
        docstring_char: Optional[str] = None
        last_import_line = 0
        after_docstring = 0
        in_multiline_import = False

        for i, line in enumerate(lines):
            stripped = line.strip()

            if i == 0 and (stripped.startswith('"""') or stripped.startswith("'''")):
                docstring_char = stripped[:3]
                if stripped.count(docstring_char) < 2:
                    in_docstring = True
                else:
                    after_docstring = i + 1
                continue

            if in_docstring:
                assert docstring_char is not None
                if docstring_char in stripped:
                    in_docstring = False
                    after_docstring = i + 1
                continue

            # Check for start of multi-line import (has opening paren but no closing paren)
            if stripped.startswith("import ") or stripped.startswith("from "):
                last_import_line = i + 1
                # Check if this is a multi-line import
                if "(" in line and ")" not in line:
                    in_multiline_import = True
                continue

            # Inside a multi-line import - continue until we see closing paren
            if in_multiline_import:
                last_import_line = i + 1
                if ")" in line:
                    in_multiline_import = False
                continue

            if last_import_line > 0 and stripped and not stripped.startswith("#"):
                break

        return last_import_line, after_docstring

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

    def _prepare_extracted_method_signature(
        self,
        fn: ast.FunctionDef,
        method_kind: Literal["instance", "classmethod", "staticmethod"],
        implicit_param: Optional[str],
    ) -> None:
        """Normalize the extracted helper so it behaves like the requested method type."""

        if method_kind == "instance":
            name = implicit_param or "self"
            self._ensure_leading_param(fn, name)
            # Strip any conflicting decorators that might have been synthesized earlier
            self._strip_decorator(fn, "staticmethod")
            self._strip_decorator(fn, "classmethod")
        elif method_kind == "classmethod":
            name = implicit_param or "cls"
            self._ensure_leading_param(fn, name)
            self._strip_decorator(fn, "staticmethod")
            if not self._has_decorator(fn, "classmethod"):
                fn.decorator_list.insert(0, ast.Name(id="classmethod", ctx=ast.Load()))
        elif method_kind == "staticmethod":
            self._strip_decorator(fn, "classmethod")
            if not self._has_decorator(fn, "staticmethod"):
                fn.decorator_list.insert(0, ast.Name(id="staticmethod", ctx=ast.Load()))
        else:
            raise ValueError(f"Unsupported method kind: {method_kind}")

    def _rewrite_call_for_method(
        self,
        node: ast.AST,
        original_name: str,
        new_name: str,
        method_kind: Optional[Literal["instance", "classmethod", "staticmethod"]],
        implicit_param: Optional[str],
        class_name: Optional[str],
        receiver_parameter_index: Optional[int] = None,
        helper_parameter_count: Optional[int] = None,
    ) -> ast.AST:
        """Rewrite calls to the extracted helper so they use method dispatch semantics."""

        if method_kind is None:
            return node

        def remove_receiver(args: List[ast.expr], implicit_name: str) -> List[ast.expr]:
            if receiver_parameter_index is None:
                # Legacy manually constructed proposals may include a leading
                # receiver outside the helper signature. Its extra arity makes
                # it distinguishable from a receiver used as an ordinary operand.
                if (
                    helper_parameter_count is not None
                    and len(args) == helper_parameter_count + 1
                    and isinstance(args[0], ast.Name)
                    and args[0].id == implicit_name
                ):
                    return args[1:]
                return args
            if receiver_parameter_index >= len(args):
                return args
            receiver = args[receiver_parameter_index]
            if not isinstance(receiver, ast.Name) or receiver.id != implicit_name:
                raise ValueError("Helper receiver argument does not match method dispatch")
            return args[:receiver_parameter_index] + args[receiver_parameter_index + 1 :]

        rewriter = MethodCallRewriter(
            remove_receiver,
            (
                self._drop_implicit_keyword
                if receiver_parameter_index is not None
                else lambda keywords, _: keywords
            ),
            original_name=original_name,
            new_name=new_name,
            method_kind=method_kind,
            implicit_name=implicit_param,
            class_name=class_name,
        )
        return cast(ast.AST, rewriter.visit(node))

    @staticmethod
    def _drop_implicit_positional(args: List[ast.expr], implicit_name: str) -> List[ast.expr]:
        """Drop the first positional argument matching ``implicit_name`` if present."""

        result: List[ast.expr] = []
        dropped = False
        for arg in args:
            if not dropped and isinstance(arg, ast.Name) and arg.id == implicit_name:
                dropped = True
                continue
            result.append(arg)
        return result

    @staticmethod
    def _drop_implicit_keyword(
        keywords: List[ast.keyword], implicit_name: str
    ) -> List[ast.keyword]:
        """Drop the first keyword argument whose name matches ``implicit_name``."""

        result: List[ast.keyword] = []
        dropped = False
        for kw in keywords:
            if not dropped and kw.arg == implicit_name:
                dropped = True
                continue
            result.append(kw)
        return result

    @staticmethod
    def _method_class(
        func: Optional[FunctionNode],
        class_name: Optional[str],
        analyzer: Optional[ScopeAnalyzer],
    ) -> Optional[str]:
        """The class ``func`` is a method of, or None when it is merely nested in one."""
        if func is None or class_name is None:
            return None
        if analyzer is not None and func in analyzer.node_scopes and not analyzer.is_method(func):
            return None
        return class_name

    def _get_method_context(
        self, func: Optional[FunctionNode], class_name: Optional[str]
    ) -> MethodInfo:
        """Return method metadata for ``func`` when it is defined inside ``class_name``."""

        if func is None or class_name is None:
            return MethodInfo(kind=None, implicit_param=None)

        kind: Optional[Literal["instance", "classmethod", "staticmethod"]] = None
        receiver_known = func.name not in _IMPLICIT_RECEIVER_SPECIAL_METHODS
        for decorator in func.decorator_list:
            name = self._decorator_name(decorator)
            if name == "staticmethod":
                kind = "staticmethod"
                break
            if name == "classmethod":
                kind = "classmethod"
                break
            if not _preserves_receiver(decorator):
                # A descriptor such as a lazy class property may pass the
                # class where the source spells ``self`` or ``cls``. The helper
                # must then receive that object explicitly, not by dispatch.
                receiver_known = False

        if kind is None:
            kind = "instance"

        implicit_param = None
        if kind in {"instance", "classmethod"}:
            positional = [*func.args.posonlyargs, *func.args.args]
            if positional:
                implicit_param = positional[0].arg
            else:
                # Nothing to dispatch on: a parameterless function in a class
                # body is a helper called while the body runs (pygments'
                # ``gen_rubystrings_rules()``), not a method.
                implicit_param = "self" if kind == "instance" else "cls"
                receiver_known = False
            # A function in a class body whose first parameter is not ``self``
            # is often a plain helper called while the class body runs
            # (pygments' ``fstring_rules(ttype)``); dispatching on that
            # parameter would call a method on an arbitrary object.
            if kind == "instance" and implicit_param != "self":
                receiver_known = False

        return MethodInfo(kind=kind, implicit_param=implicit_param, receiver_known=receiver_known)

    @staticmethod
    def _resolve_base_name(expr: ast.expr) -> Optional[str]:
        """Resolve a base-class expression into its dotted name when feasible."""

        if isinstance(expr, ast.Name):
            return expr.id
        if isinstance(expr, ast.Attribute):
            parts: List[str] = []
            current: ast.expr = expr
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
                return ".".join(reversed(parts))
        return None

    @staticmethod
    def _class_info_key(info: ClassInfo) -> Tuple[str, str]:
        """Return a stable identifier for a class definition."""

        return (info.file_path, info.qualname)

    def _find_class_info_by_name(
        self, class_infos: List[ClassInfo], file_path: str, class_name: str
    ) -> Optional[ClassInfo]:
        """Locate class metadata using its defining file and simple name."""

        matches = [
            info for info in class_infos if info.file_path == file_path and info.name == class_name
        ]
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]

        # Prefer the innermost definition (longest qualname) when duplicates exist.
        matches.sort(key=lambda info: info.qualname.count("."), reverse=True)
        return matches[0]

    def _find_class_info_for_base(
        self,
        class_infos: List[ClassInfo],
        base_name: str,
        *,
        referencing_file: str,
    ) -> Optional[ClassInfo]:
        """Resolve a base-class reference as the referencing module would.

        A base written as ``Outer.Inner`` is the class of that qualname in
        the same module; otherwise the name must be bound by one of the
        module's own imports, and the class is looked up in the module that
        import denotes. A project may define several classes with one name
        (oauthlib has a ``BaseEndpoint`` per protocol), so a name is never
        matched across the project, and a reference that resolves to no
        single class contributes no ancestor.
        """
        local = [
            info
            for info in class_infos
            if info.file_path == referencing_file and info.qualname == base_name
        ]
        if local:
            return local[0] if len(local) == 1 else None
        sites = imported_definition_sites(referencing_file, base_name)
        if not sites:
            return None
        matches = [
            info for info in class_infos if (Path(info.file_path).resolve(), info.qualname) in sites
        ]
        return matches[0] if len(matches) == 1 else None

    def _collect_class_ancestors(
        self, class_info: ClassInfo, class_infos: List[ClassInfo]
    ) -> List[ClassInfo]:
        """Return ancestors starting from the nearest base class."""

        ancestors: List[Tuple[int, ClassInfo]] = []
        visited: Set[Tuple[str, str]] = set()
        queue: deque[Tuple[ClassInfo, int]] = deque([(class_info, 0)])

        while queue:
            current, depth = queue.popleft()
            for base_name in current.bases:
                base_info = self._find_class_info_for_base(
                    class_infos, base_name, referencing_file=current.file_path
                )
                if base_info is None:
                    continue
                key = self._class_info_key(base_info)
                if key in visited:
                    continue
                visited.add(key)
                ancestors.append((depth + 1, base_info))
                queue.append((base_info, depth + 1))

        ancestors.sort(key=lambda item: item[0])
        return [info for _depth, info in ancestors]

    def _find_common_ancestor(
        self,
        class1: Tuple[str, str],
        class2: Tuple[str, str],
        class_infos: List[ClassInfo],
    ) -> Optional[ClassInfo]:
        """Return the nearest shared ancestor class for two class definitions."""

        file1, name1 = class1
        file2, name2 = class2

        info1 = self._find_class_info_by_name(class_infos, file1, name1)
        info2 = self._find_class_info_by_name(class_infos, file2, name2)
        if info1 is None or info2 is None:
            return None

        key1 = self._class_info_key(info1)
        key2 = self._class_info_key(info2)

        chain1 = [info1] + self._collect_class_ancestors(info1, class_infos)
        chain2 = [info2] + self._collect_class_ancestors(info2, class_infos)
        lookup2 = {self._class_info_key(info): info for info in chain2}

        for info in chain1:
            key = self._class_info_key(info)
            if key in lookup2:
                if key == key1 and key == key2:
                    # Identical class; handled elsewhere.
                    continue
                return lookup2[key]
        return None

    def _choose_class_insertion(
        self,
        pair: CodeBlockPair,
        method_info1: MethodInfo,
        method_info2: MethodInfo,
        class_infos: List[ClassInfo],
    ) -> Optional[ClassInsertionPlan]:
        """Determine whether the helper should be inserted into a class context."""
        # Both blocks must sit in methods of the same kind.
        if not (method_info1.receiver_known and method_info2.receiver_known):
            return None
        if pair.class1_name is not None and not _unique_module_level_class(
            class_infos, pair.file_path, pair.class1_name
        ):
            return None
        if pair.class2_name is not None and not _unique_module_level_class(
            class_infos, pair.file_path2 or pair.file_path, pair.class2_name
        ):
            return None
        k1, k2 = method_info1.kind, method_info2.kind
        # A block with no method context (a function nested inside a method, or
        # one outside any class) has no receiver to dispatch on; its helper
        # stays at module level even when the block lexically sits in a class.
        if k1 is None or k2 is None or k1 != k2:
            return None
        if pair.class1_name is None or pair.class2_name is None:
            return None

        file1 = pair.file_path
        file2 = pair.file_path2 or pair.file_path

        # At this point we know effective_kind is valid because we've already validated k1/k2
        effective_kind: Literal["instance", "classmethod", "staticmethod"] = k1
        if file1 == file2 and pair.class1_name == pair.class2_name:
            implicit_param = method_info1.implicit_param or method_info2.implicit_param
            if effective_kind == "instance" and not implicit_param:
                implicit_param = "self"
            if effective_kind == "classmethod" and not implicit_param:
                implicit_param = "cls"
            return ClassInsertionPlan(
                class_name=pair.class1_name,
                file_path=file1,
                method_kind=effective_kind,
                implicit_param=implicit_param,
            )

        ancestor = self._find_common_ancestor(
            (file1, pair.class1_name),
            (file2, pair.class2_name),
            class_infos,
        )
        if ancestor is None or not _unique_module_level_class(
            class_infos, ancestor.file_path, ancestor.name
        ):
            return None

        implicit_param = method_info1.implicit_param or method_info2.implicit_param
        if effective_kind == "instance" and not implicit_param:
            implicit_param = "self"
        if effective_kind == "classmethod" and not implicit_param:
            implicit_param = "cls"

        return ClassInsertionPlan(
            class_name=ancestor.name,
            file_path=ancestor.file_path,
            method_kind=effective_kind,
            implicit_param=implicit_param,
        )

    def _evaluate_pairs_serial(
        self,
        block_pairs: List[CodeBlockPair],
        all_functions: Sequence[FunctionArtifact],
        class_infos: List[ClassInfo],
        *,
        verbose: bool,
        progress: str,
    ) -> List[RefactoringProposal]:
        proposals: List[RefactoringProposal] = []

        progress_mode, tqdm_cls, use_tqdm = self._resolve_progress_backend(progress)
        tqdm_iter = None
        # Always show progress for pair evaluation when progress is enabled, even if verbose=False
        if use_tqdm and tqdm_cls is not None:
            tqdm_iter = tqdm_cls(
                block_pairs,
                total=len(block_pairs),
                desc="unify",
                unit="pair",
                leave=False,
            )

        if use_tqdm and tqdm_iter is not None:
            for pair in tqdm_iter:
                proposal = self._try_refactor_pair_multi_file(pair, all_functions, class_infos)
                if proposal:
                    proposals.append(proposal)
            return proposals

        use_inline_bar = progress_mode in ("auto", "tqdm") and len(block_pairs) > 0 and not use_tqdm
        last_pct = -1
        self._start_inline_status("Analyzing pairs (unify):", use_inline_bar)

        for idx, pair in enumerate(block_pairs, 1):
            proposal = self._try_refactor_pair_multi_file(pair, all_functions, class_infos)
            if proposal:
                proposals.append(proposal)
            if use_inline_bar:
                pct = int(100 * idx / len(block_pairs))
                if pct != last_pct:
                    last_pct = pct
                    self._update_inline_status("Analyzing pairs (unify):", pct)

        self._finish_inline_status(use_inline_bar)

        return proposals

    def _evaluate_pairs_parallel(
        self,
        block_pairs: List[CodeBlockPair],
        all_functions: Sequence[FunctionArtifact],
        class_infos: List[ClassInfo],
        *,
        verbose: bool,
        progress: str,
    ) -> List[RefactoringProposal]:
        """Evaluate pairs in forked workers; results are ordered as the serial path orders them.

        A serial probe over the first pairs measures the real per-pair cost;
        the rest is split into contiguous chunks so pairs sharing a template
        block land in one worker and hit that worker's own caches.
        """
        global _worker_engine, _worker_functions, _worker_class_infos, _worker_pairs

        # Every pair costs something even when its analyses are cached, and
        # most pairs are rejected before unification, so the probe samples the
        # whole list rather than the pairs the unification cache has not seen.
        cold = list(range(len(block_pairs)))
        workers = min(self._parallel_workers(), max(1, len(cold) // 64))
        if workers <= 1 or len(cold) < self.PARALLEL_PAIR_THRESHOLD:
            return self._evaluate_pairs_serial(
                block_pairs, all_functions, class_infos, verbose=verbose, progress=progress
            )
        # Probe: evaluate a strided sample of the pairs here and project the
        # rest. Pairs are ordered by function, and in a fixed-point iteration
        # the early functions are the untouched ones whose analyses are
        # cached, so a prefix would understate the cost; a stride samples
        # cached and cold regions alike.
        results: Dict[int, RefactoringProposal] = {}
        stride = max(1, len(cold) // self.PARALLEL_PROBE_PAIRS)
        probe = cold[::stride][: self.PARALLEL_PROBE_PAIRS]
        started = time.monotonic()
        for index in probe:
            probed = self._try_refactor_pair_multi_file(
                block_pairs[index], all_functions, class_infos
            )
            if probed is not None:
                results[index] = probed
        per_pair = (time.monotonic() - started) / max(1, len(probe))
        probed_indices = set(probe)
        cold = [index for index in cold if index not in probed_indices]
        if per_pair * len(cold) < self.PARALLEL_MIN_PROJECTED_SECONDS:
            for index in cold:
                serial_proposal = self._try_refactor_pair_multi_file(
                    block_pairs[index], all_functions, class_infos
                )
                if serial_proposal is not None:
                    results[index] = serial_proposal
            return [results[index] for index in sorted(results)]
        chunk_count = workers * 4
        chunk_size = max(1, -(-len(cold) // chunk_count))
        chunks = [
            (cold[offset], cold[min(offset + chunk_size, len(cold)) - 1] + 1)
            for offset in range(0, len(cold), chunk_size)
        ]
        # Chunks are index ranges over ``block_pairs``; cold indices are
        # increasing, so a range may include warm or probed pairs, which the
        # worker then also evaluates cheaply. That keeps chunks contiguous.
        _worker_engine, _worker_functions, _worker_class_infos, _worker_pairs = (
            self,
            all_functions,
            class_infos,
            block_pairs,
        )
        try:
            context = multiprocessing.get_context("fork")
            with ProcessPoolExecutor(
                max_workers=workers, mp_context=context, initializer=_start_parent_watchdog
            ) as executor:
                for _bounds, accepted in zip(chunks, executor.map(_evaluate_pair_chunk, chunks)):
                    for index, proposal in accepted:
                        results[index] = proposal
        except (BrokenProcessPool, OSError, RuntimeError) as error:
            LOG.warning("Parallel pair evaluation unavailable (%s); evaluating serially", error)
            return self._evaluate_pairs_serial(
                block_pairs, all_functions, class_infos, verbose=verbose, progress=progress
            )
        finally:
            _worker_engine = _worker_functions = _worker_class_infos = _worker_pairs = None
        return [results[index] for index in sorted(results)]

    @staticmethod
    def _block_line_span(block: Sequence[ast.stmt]) -> Optional[Tuple[int, int]]:
        """Return the (start_line, end_line) span for a contiguous block of statements."""

        if not block:
            return None

        start_node = block[0]
        end_node = block[-1]

        start_line = getattr(start_node, "lineno", None)
        end_line = getattr(end_node, "end_lineno", None) or getattr(end_node, "lineno", None)
        if start_line is None or end_line is None:
            return None

        return int(start_line), int(end_line)

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
            and all(_encloses(entry.node, function) for function in inner)
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

    def _find_block_pairs_multi_file(
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
                for block1_range, block1_nodes, sig1 in signed_blocks[i]:
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

    def _add_clustered_replacements(
        self,
        template: "_HelperTemplate",
        dce_node: Optional[FunctionNode],
        all_functions: Sequence[FunctionArtifact],
        replacements: List[Replacement],
        cluster_contexts: Dict[int, Tuple[Optional[str], Optional[str], Optional[str], bool]],
    ) -> None:
        """Append same-file occurrences that can share the extracted helper.

        Scans every function in the pair's file for additional blocks that unify
        with the template and reproduce its helper, appending a call for each and
        recording the method context needed to decide, later, whether that call
        can dispatch through a receiver. Mutates ``replacements`` and
        ``cluster_contexts`` in place.
        """
        pair = template.pair
        func_def = template.func_def
        func_def_dump = template.func_def_dump
        param_order = template.param_order
        helper_preamble_length = template.preamble_length
        free_vars = template.free_vars
        enclosing_names = template.enclosing_names
        value_prod1 = template.is_value_producing
        globals_to_declare_in_extracted = template.globals_to_declare
        nonlocals_to_declare_in_extracted = template.nonlocals_to_declare
        from .block_signature import extract_block_signature, quick_filter as _qf

        # Build a set of already covered ranges to avoid duplicates
        covered = {
            (pair.file_path, pair.block1_range),
            (pair.file_path2 or pair.file_path, pair.block2_range),
        }
        # Template signature from block1
        tmpl_sig = extract_block_signature(pair.block1_nodes)
        func_def_dump = ast.dump(func_def)

        # Gather candidates from same file functions
        for entry in all_functions:
            fpath = entry.file_path
            fn = entry.node
            analyzerX = entry.scope_analyzer
            clsX = entry.class_name
            if fpath != pair.file_path:
                continue
            # A helper inserted into the pair's deepest common enclosing
            # function is visible only there and in its nested functions;
            # a block elsewhere in the file cannot call it (prompt_toolkit).
            if dce_node is not None and not _encloses(dce_node, fn):
                continue
            # Where the candidate sits decides, once the helper's home is
            # known, whether it can share a method call (see below).
            candidate_class = self._method_class(fn, clsX, analyzerX)
            candidate_info = self._get_method_context(fn, candidate_class)
            # Skip the original two functions
            if fn.name in (pair.function1_name, pair.function2_name):
                # Still scan, but avoid ranges we've already taken
                pass
            # Extract blocks and test quick filter against template
            for cand_range, cand_nodes, cand_sig in self._signed_blocks(fn):
                if any(
                    path == fpath and _line_ranges_intersect(cand_range, taken)
                    for path, taken in covered
                ):
                    continue
                # The size gate and signature filter are constant-time and
                # reject most blocks; the semantic guards below each walk the
                # candidate's function, so they run only on survivors. Every
                # check is independent, so the order changes cost, not outcome.
                start_line, end_line = cand_range
                if (end_line - start_line + 1) < self.min_lines:
                    continue
                if not _qf(tmpl_sig, cand_sig):
                    continue
                if self._block_rejected(requires_original_frame, cand_nodes, path=fpath):
                    continue
                if self._block_rejected(nested_bindings_escape, cand_nodes, fn):
                    continue
                if self._block_rejected(
                    snapshots_rebound_external_names, cand_nodes, fn, analyzerX
                ):
                    continue
                if self._block_rejected(nested_scopes_cross_block_boundary, cand_nodes, fn):
                    continue
                if self._block_rejected(moves_scope_declaration, cand_nodes, fn):
                    continue
                reassignX = self._get_assignment_reuse(fn)
                if self._per_block(
                    "reassignments",
                    fn,
                    cand_nodes,
                    lambda: has_reassignments_without_bindings(fn, cand_nodes, reassignX),
                )[0]:
                    continue
                candidate_snapshot = self._build_block_binding_snapshot(
                    fn, cand_nodes, cand_range, reassignX
                )
                if self._per_block(
                    "unbinds",
                    fn,
                    cand_nodes,
                    lambda: unbinds_external_name(
                        fn, cand_nodes, candidate_snapshot.bound_before_block
                    ),
                ):
                    continue
                # Try to unify template block with candidate
                memo_key = (
                    self._sid(pair.block1_nodes),
                    self._sid(cand_nodes),
                    self._sid([fn]),
                    self._module_digest(fn),
                    frozenset(free_vars),
                    frozenset(enclosing_names),
                    value_prod1,
                    tuple(sorted(globals_to_declare_in_extracted)),
                    tuple(sorted(nonlocals_to_declare_in_extracted)),
                    func_def.name,
                    func_def_dump,
                    tuple(sorted(param_order.items())),
                    helper_preamble_length,
                )
                if memo_key in self._cluster_cache:
                    cached_call = self._cluster_cache[memo_key]
                    self._cluster_cache.move_to_end(memo_key)
                    if cached_call is None:
                        continue
                    call_node2 = copy.deepcopy(cached_call)
                else:
                    computed = self._cluster_candidate_call(
                        template,
                        _ClusterCandidate(
                            file_path=fpath,
                            function=fn,
                            analyzer=analyzerX,
                            nodes=cand_nodes,
                            snapshot=candidate_snapshot,
                        ),
                    )
                    self._bounded_put(
                        self._cluster_cache,
                        memo_key,
                        None if computed is None else copy.deepcopy(computed),
                    )
                    if computed is None:
                        continue
                    call_node2 = computed
                cluster_contexts[len(replacements)] = (
                    candidate_class,
                    candidate_info.kind,
                    candidate_info.implicit_param,
                    candidate_info.receiver_known,
                )
                replacements.append(
                    Replacement(
                        line_range=cand_range,
                        node=call_node2,
                        file_path=fpath,
                        class_name=clsX,
                        method_kind=None,
                        implicit_param=None,
                    )
                )
                covered.add((fpath, cand_range))

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
                        self._debug_reject("module_data_lookup", pair)
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
    def _positional_parameter_names(function: ast.FunctionDef) -> Optional[List[str]]:
        """The parameters a positional call binds, in order; None when some cannot be."""
        arguments = function.args
        if arguments.vararg or arguments.kwarg or arguments.kwonlyargs:
            return None
        return [arg.arg for arg in arguments.posonlyargs + arguments.args]

    @staticmethod
    def _unwrap_helper_call(statement: ast.AST, helper_name: str) -> Optional[ast.Call]:
        """The plain positional helper call inside a generated statement, if that is its shape."""
        if not isinstance(statement, (ast.Return, ast.Expr)):
            return None
        call = statement.value
        if (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == helper_name
            and not call.keywords
            and not any(isinstance(arg, ast.Starred) for arg in call.args)
        ):
            return call
        return None

    @staticmethod
    def _same_absolute_import(left: ast.AST, right: ast.AST, name: str) -> bool:
        """Whether two import statements bind ``name`` to the same absolute target.

        ``import a.b`` in two modules binds the same module object; ``from a
        import b`` (absolute) binds the same attribute. A relative import means
        something different in each package, so it never counts.
        """
        if isinstance(left, ast.Import) and isinstance(right, ast.Import):
            left_targets = {
                alias.name
                for alias in left.names
                if (alias.asname or alias.name.split(".")[0]) == name
            }
            right_targets = {
                alias.name
                for alias in right.names
                if (alias.asname or alias.name.split(".")[0]) == name
            }
            return bool(left_targets) and left_targets == right_targets
        if isinstance(left, ast.ImportFrom) and isinstance(right, ast.ImportFrom):
            if left.level or right.level or left.module != right.module:
                return False
            left_targets = {
                alias.name for alias in left.names if (alias.asname or alias.name) == name
            }
            right_targets = {
                alias.name for alias in right.names if (alias.asname or alias.name) == name
            }
            return bool(left_targets) and left_targets == right_targets
        return False

    def _reuse_plan(self, call: ast.Call, target: FunctionArtifact) -> Optional["_ReusePlan"]:
        """How the helper's arguments map onto the target function, or None.

        The call at the function's own site must pass each of its positional
        parameters exactly once, by name. Any other argument must be a name
        that resolves there to a module-level binding of the target's module
        (a function, class, or import the body reads): the function reads it
        itself, so a caller need not pass it. Then the helper applied to any
        arguments is the function applied to the parameter arguments, provided
        every other site supplies the same module-level objects.
        """
        if not isinstance(target.node, ast.FunctionDef):
            return None
        parameters = self._positional_parameter_names(target.node)
        if parameters is None:
            return None
        names = [arg.id if isinstance(arg, ast.Name) else None for arg in call.args]
        if None in names or len(set(names)) != len(names):
            return None
        if not set(parameters) <= set(names):
            return None
        scope = target.scope_analyzer.node_scopes.get(target.node)
        if scope is None:
            return None
        ambient: Dict[int, Tuple[str, Optional[Binding]]] = {}
        for index, name in enumerate(names):
            if name in parameters:
                continue
            binding = scope.lookup(cast(str, name))
            if binding is not None and binding.scope_id != target.root_scope.scope_id:
                return None
            ambient[index] = (cast(str, name), binding)
        return _ReusePlan(
            parameter_positions=[names.index(parameter) for parameter in parameters],
            ambient=ambient,
        )

    @staticmethod
    def _module_deletes_name(tree: ast.Module, name: str) -> bool:
        """Whether a module-level statement (outside any definition) deletes ``name``."""
        for statement in tree.body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            for node in ast.walk(statement):
                if isinstance(node, ast.Delete) and any(
                    isinstance(target, ast.Name) and target.id == name for target in node.targets
                ):
                    return True
        return False

    def _reusable_function_at(
        self,
        replacement: Replacement,
        default_file: str,
        all_functions: Sequence[FunctionArtifact],
    ) -> Optional[FunctionArtifact]:
        """The plain module-level function whose whole body ``replacement`` covers, if any.

        Only a function that a call by name reproduces qualifies: defined once,
        unconditionally, at module level, without decorators, not async (the
        generated helper is synchronous), and never rebound or deleted through a
        ``global`` declaration or a module-level ``del``.
        """
        file_path = replacement.file_path or default_file
        for artifact in all_functions:
            function = artifact.node
            if (
                artifact.file_path != file_path
                or artifact.class_name is not None
                or artifact.enclosing_function is not None
                or not isinstance(function, ast.FunctionDef)
                or function.decorator_list
                or self._block_line_span(body_without_docstring(function.body))
                != tuple(replacement.line_range)
            ):
                continue
            tree = artifact.scope_analyzer.analyzed_tree
            binding = artifact.root_scope.bindings.get(function.name)
            if (
                not isinstance(tree, ast.Module)
                or not any(statement is function for statement in tree.body)
                or binding is None
                or binding.node is not function
                or any(
                    function.name in names for names in artifact.scope_analyzer.global_vars.values()
                )
                or self._module_deletes_name(tree, function.name)
            ):
                return None
            return artifact
        return None

    @staticmethod
    def _innermost_function_at(
        file_path: str,
        line_range: Tuple[int, int],
        all_functions: Sequence[FunctionArtifact],
    ) -> Optional[FunctionArtifact]:
        """The most deeply nested analyzed function whose span contains ``line_range``."""
        start, end = line_range
        enclosing = [
            artifact
            for artifact in all_functions
            if artifact.file_path == file_path
            and artifact.node.lineno <= start
            and end <= (artifact.node.end_lineno or artifact.node.lineno)
        ]
        if not enclosing:
            return None
        return max(enclosing, key=lambda artifact: artifact.node.lineno)

    def _existing_function_reachable(
        self,
        target: FunctionArtifact,
        replacement: Replacement,
        default_file: str,
        all_functions: Sequence[FunctionArtifact],
    ) -> bool:
        """Whether a call by name at the replacement site resolves to ``target``.

        In the target's own module the name must resolve through the site's
        enclosing scopes to that definition; in another module it must be free
        there, so the import the materializer adds is what binds it. A
        class-private spelling is mangled inside a class body either way.
        """
        file_path = replacement.file_path or default_file
        name = target.node.name
        if replacement.class_name is not None and name.startswith("__") and not name.endswith("__"):
            return False
        site = self._innermost_function_at(file_path, replacement.line_range, all_functions)
        if site is None:
            return False
        scope = site.scope_analyzer.node_scopes.get(site.node)
        if scope is None:
            return False
        binding = scope.lookup(name)
        if file_path == target.file_path:
            return binding is not None and binding.node is target.node
        return binding is None

    def _call_to_existing_function(
        self,
        replacement: Replacement,
        helper_name: str,
        target: FunctionArtifact,
        plan: "_ReusePlan",
        site: FunctionArtifact,
    ) -> Optional[Replacement]:
        """The replacement with its helper call retargeted at the existing function.

        Parameter arguments are reordered into the function's order; when that
        order differs from the helper's, only names and constants may move,
        since evaluating other expressions in a new order could be observed.
        An ambient argument is dropped, but only when it is the same name bound
        to the same module-level object as at the function's own site: the same
        definition in the same module, or an identical absolute import.
        """
        node = copy.deepcopy(replacement.node)
        call = self._unwrap_helper_call(node, helper_name)
        if call is None or len(call.args) != len(plan.parameter_positions) + len(plan.ambient):
            return None
        site_scope = site.scope_analyzer.node_scopes.get(site.node)
        if site_scope is None:
            return None
        for index, (name, target_binding) in plan.ambient.items():
            argument = call.args[index]
            if not isinstance(argument, ast.Name) or argument.id != name:
                return None
            site_binding = site_scope.lookup(name)
            if site.file_path == target.file_path:
                same = (
                    site_binding is target_binding
                    if target_binding is None
                    else site_binding is not None and site_binding.node is target_binding.node
                )
            else:
                same = (
                    site_binding is not None
                    and target_binding is not None
                    and site_binding.scope_id == site.root_scope.scope_id
                    and self._same_absolute_import(site_binding.node, target_binding.node, name)
                )
            if not same:
                return None
        parameter_arguments = [call.args[index] for index in plan.parameter_positions]
        identity = plan.parameter_positions == sorted(plan.parameter_positions)
        if not identity and not all(
            isinstance(arg, (ast.Name, ast.Constant)) for arg in parameter_arguments
        ):
            return None
        call.args = parameter_arguments
        call.func = ast.Name(id=target.node.name, ctx=ast.Load())
        return dataclasses.replace(replacement, node=node)

    def _redirect_to_existing_function(
        self,
        proposal: RefactoringProposal,
        all_functions: Sequence[FunctionArtifact],
    ) -> Optional[RefactoringProposal]:
        """The proposal rewritten to call a function that one duplicate already is.

        When a duplicate site is the whole body of a plain module-level function
        and the generated call there passes exactly that function's parameters,
        the helper applied to any arguments is that function applied to them.
        The fresh helper would only restate the function, so it is dropped: the
        function stays as it is and every other site calls it. Candidates are
        tried in source order; one the other sites cannot reach by name, or
        whose import would close a cycle, is skipped. None keeps the extraction.
        """
        if proposal.return_variables:
            return None
        helper_name = proposal.extracted_function.name
        candidates: List[Tuple[int, FunctionArtifact]] = []
        for index, replacement in enumerate(proposal.replacements):
            target = self._reusable_function_at(replacement, proposal.file_path, all_functions)
            if target is not None:
                candidates.append((index, target))
        candidates.sort(key=lambda item: (item[1].file_path, item[1].node.lineno))
        for index, target in candidates:
            call = self._unwrap_helper_call(proposal.replacements[index].node, helper_name)
            if call is None:
                continue
            plan = self._reuse_plan(call, target)
            if plan is None:
                continue
            others = [
                replacement
                for position, replacement in enumerate(proposal.replacements)
                if position != index
            ]
            sites = [
                self._innermost_function_at(
                    replacement.file_path or proposal.file_path,
                    replacement.line_range,
                    all_functions,
                )
                for replacement in others
            ]
            if any(site is None for site in sites):
                continue
            rewritten = [
                self._call_to_existing_function(
                    replacement, helper_name, target, plan, cast(FunctionArtifact, site)
                )
                for replacement, site in zip(others, sites)
            ]
            if any(replacement is None for replacement in rewritten):
                continue
            if not all(
                self._existing_function_reachable(
                    target, replacement, proposal.file_path, all_functions
                )
                for replacement in others
            ):
                continue
            participating = {target.file_path} | {
                replacement.file_path or proposal.file_path for replacement in others
            }
            if would_create_import_cycle(target.file_path, participating):
                continue
            callers = sorted({cast(FunctionArtifact, site).node.name for site in sites})
            location = Path(target.file_path).name
            return dataclasses.replace(
                proposal,
                file_path=target.file_path,
                extracted_function=cast(ast.FunctionDef, copy.deepcopy(target.node)),
                replacements=cast(List[Replacement], rewritten),
                description=(
                    f"Reuse {target.node.name} ({location}) for duplicated code in "
                    + ", ".join(callers)
                ),
                insert_into_class=None,
                insert_into_function=None,
                method_kind=None,
                method_param_name=None,
                reused_function=ReusedFunction(
                    name=target.node.name,
                    file_path=target.file_path,
                    line_range=(target.node.lineno, target.node.end_lineno or target.node.lineno),
                ),
            )
        return None

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
            self._debug_reject("frame_sensitive_block", pair)
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
            self._debug_reject("rebound_external_binding", pair)
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
            (nested_bindings_escape, "nested_binding_escapes"),
            (nested_scopes_cross_block_boundary, "closure_crosses_block_boundary"),
            (moves_scope_declaration, "moves_scope_declaration"),
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
                self._debug_reject("unsafe_reassignment_block1", pair, str(problematic_vars1))
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
                self._debug_reject("unsafe_reassignment_block2", pair, str(problematic_vars2))
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
                self._debug_reject("unbinds_external_name", pair)
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
            self._debug_reject("value_producing_mismatch", pair)
            return None

        # CRITICAL: If blocks are NATURALLY value-producing (have return statements),
        # ensure complete return coverage. This prevents extracting partial control flow.
        # Skip this check for blocks that will have return statements ADDED for return_variables
        if value_prod1 and not return_variables_block1:  # Naturally value-producing
            from .extractor import has_complete_return_coverage

            if not has_complete_return_coverage(cast(List[ast.stmt], pair.block1_nodes)):
                if debug_enabled:
                    VALIDATION.debug("  REJECTED: Block1 missing complete return coverage")
                self._debug_reject("incomplete_return_coverage_block1", pair)
                return None
            if not has_complete_return_coverage(cast(List[ast.stmt], pair.block2_nodes)):
                if debug_enabled:
                    VALIDATION.debug("  REJECTED: Block2 missing complete return coverage")
                self._debug_reject("incomplete_return_coverage_block2", pair)
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
            self._debug_reject("trivial_return_blocks", pair)
            return None

        # Check structural similarity
        if not self._are_structurally_similar(pair.block1_nodes, pair.block2_nodes):
            if debug_enabled:
                VALIDATION.debug("  REJECTED: Not structurally similar")
            self._debug_reject("not_structurally_similar", pair)
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
            self._debug_reject("unification_failed", pair)
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
            self._debug_reject("return_variables_not_aligned", pair)
            return None
        ordered_return_variables = aligned
        if ordered_return_variables[0] and (
            self._is_value_producing(pair.block1_nodes)
            or self._is_value_producing(pair.block2_nodes)
        ):
            # A call statement is either `x = helper()` or `return helper()`;
            # a block that both returns early and binds live variables needs both.
            self._debug_reject("mixed_return_and_variables", pair)
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
                self._debug_reject("conditionally_bound_return", pair, str(not_definite))
                return None
        if free_vars1 & bound_after_block1:
            incomplete_vars = free_vars1 & bound_after_block1
            if debug_enabled:
                VALIDATION.debug(
                    f"  REJECTED: Block1 uses variables defined AFTER the block: {incomplete_vars}"
                )
                VALIDATION.debug("    These variables would be used before they're defined")
            self._debug_reject("incomplete_lifetime_block1", pair, str(incomplete_vars))
            return None

        if free_vars2 & bound_after_block2:
            incomplete_vars = free_vars2 & bound_after_block2
            if debug_enabled:
                VALIDATION.debug(
                    f"  REJECTED: Block2 uses variables defined AFTER the block: {incomplete_vars}"
                )
            self._debug_reject("incomplete_lifetime_block2", pair, str(incomplete_vars))
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
            self._debug_reject("impure_eager_parameter", pair)
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
                        "orphaned_variables", pair, detail=str(sorted(orphans1 | orphans2))
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
                        "undefined_names_in_call",
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
                        "instantiation_mismatch", pair, detail=f"block{block_idx+1}: {mismatch}"
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
                _HelperTemplate(
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
            self._debug_reject("nonlocal_safety_skip", pair)
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
            self._debug_reject("cross_module_global_declaration", pair)
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
        if would_create_import_cycle(canonical_file, participating):
            safe_home = None
            if insert_into_class is None and insert_into_function is None:
                for candidate in sorted(participating - {canonical_file}):
                    if not would_create_import_cycle(candidate, participating):
                        safe_home = candidate
                        break
            if safe_home is None:
                self._debug_reject("import_cycle", pair)
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
                        self._debug_reject("private_name_lexical_class", pair)
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
            self._debug_reject("trivial_forwarding_helper", pair)
            return None
        if self.reuse_existing_functions:
            redirected = self._redirect_to_existing_function(proposal, all_functions)
            if redirected is not None:
                return redirected
        if self.annotate_helpers:
            proposal = self._with_helper_annotations(proposal, all_functions)
        return proposal

    def _with_helper_annotations(
        self, proposal: RefactoringProposal, all_functions: Sequence[FunctionArtifact]
    ) -> RefactoringProposal:
        """The proposal with its helper annotated from what the call sites declare.

        Runs after every verification, since annotations play no part in the
        instantiation check, and after clustering, which compares helper
        bodies structurally.
        """
        sites: List[CallSite] = []
        for replacement in proposal.replacements:
            file_path = replacement.file_path or proposal.file_path
            call = call_in_statement(replacement.node, proposal.extracted_function.name)
            function = self._innermost_function_at(file_path, replacement.line_range, all_functions)
            module = function.scope_analyzer.analyzed_tree if function is not None else None
            if call is None or function is None or not isinstance(module, ast.Module):
                return proposal
            sites.append(
                CallSite(
                    statement=cast(ast.stmt, replacement.node),
                    call=call,
                    function=function.node,
                    module=module,
                    file_path=file_path,
                )
            )
        annotated = annotate_helper(
            proposal.extracted_function, sites, proposal.file_path, proposal.return_variables
        )
        return dataclasses.replace(
            proposal,
            extracted_function=annotated,
            wants_type_inference=sites_use_annotations(sites),
        )

    def _infer_helper_annotations(self, proposal: RefactoringProposal) -> None:
        """Finish the helper's annotations in place when the proposal is applied.

        The type inferrer, when there is one, fills what the copied
        annotations could not; then a helper that carries any annotation gets
        ``Any`` on whatever is still bare, so its signature is complete. Runs
        once per applied proposal, on the files as they stand, so the cost is
        one incremental type-check per application rather than one per
        candidate.
        """
        if not proposal.wants_type_inference or proposal.reused_function is not None:
            return
        module_level = proposal.insert_into_class is None and proposal.insert_into_function is None
        host_source = self._read_source(proposal.file_path)
        bare_ok = self.placeable_after(host_source) if module_level and host_source else set()
        host = self._parsed_host(proposal.file_path)
        if self.type_inferrer is None:
            respelled = respell_bare(proposal.extracted_function, host, bare_ok)
            completed = complete_with_any(respelled, host)
            proposal.extracted_function = completed.helper
            proposal.required_imports = completed.required_imports
            return
        sites: List[ApplySite] = []
        sources: Dict[str, str] = {}
        for replacement in proposal.replacements:
            file_path = replacement.file_path or proposal.file_path
            call = call_in_statement(replacement.node, proposal.extracted_function.name)
            if call is None:
                return
            source = sources.get(file_path)
            if source is None:
                source = Path(file_path).read_text(encoding="utf-8")
                sources[file_path] = source
            lines = source.splitlines(keepends=True)
            start_line, end_line = replacement.line_range
            if not 1 <= start_line <= len(lines):
                return
            sites.append(
                ApplySite(
                    file_path=file_path,
                    source=source,
                    start_line=start_line,
                    end_line=end_line,
                    indent=self._get_indent(lines[start_line - 1]),
                    statement=cast(ast.stmt, replacement.node),
                    call=call,
                    declared_return=self._declared_return_at(source, start_line),
                )
            )
        inferred = infer_missing_annotations(
            respell_bare(proposal.extracted_function, host, bare_ok),
            sites,
            proposal.file_path,
            proposal.return_variables,
            self.type_inferrer,
            bare_ok,
        )
        completed = complete_with_any(respell_bare(inferred.helper, host, bare_ok), host)
        proposal.extracted_function = completed.helper
        proposal.required_imports = tuple(
            dict.fromkeys(inferred.required_imports + completed.required_imports)
        )

    @staticmethod
    def _annotation_names(helper: ast.FunctionDef) -> Set[str]:
        """Names the helper's unquoted annotations refer to."""
        names: Set[str] = set()
        annotations = [arg.annotation for arg in helper.args.posonlyargs + helper.args.args] + [
            helper.returns
        ]
        for annotation in annotations:
            if annotation is not None and not isinstance(annotation, ast.Constant):
                names |= {n.id for n in ast.walk(annotation) if isinstance(n, ast.Name)}
        return names

    @staticmethod
    def _read_source(file_path: str) -> Optional[str]:
        try:
            return Path(file_path).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return None

    @staticmethod
    def _declared_return_at(source: str, line: int) -> Optional[ast.expr]:
        """The return annotation of the innermost function containing ``line``."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None
        innermost: Optional[FunctionNode] = None
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.lineno <= line <= (node.end_lineno or node.lineno)
                and (innermost is None or node.lineno > innermost.lineno)
            ):
                innermost = node
        return innermost.returns if innermost is not None else None

    @staticmethod
    def _parsed_host(file_path: str) -> Optional[ast.Module]:
        try:
            return ast.parse(Path(file_path).read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeError):
            return None

    def apply_refactoring(self, file_path: str, proposal: RefactoringProposal) -> str:
        """
        Apply a refactoring proposal to a file.

        Args:
            file_path: Path to file
            proposal: Refactoring proposal

        Returns:
            Modified source code
        """
        # All proposals now use multi-file format
        modified_files = self.apply_refactoring_multi_file(proposal)
        # Backward-compat: handle tuple return (modified_files, changed_paths)
        if isinstance(modified_files, tuple):
            modified_files = modified_files[0]
        # Return the content for the requested file
        return modified_files.get(file_path, "")

    def plan_refactoring(self, proposal: RefactoringProposal) -> ChangePlan:
        """Materialize a proposal into an immutable, stale-checked byte plan."""
        paths = {
            proposal.file_path,
            *(rep.file_path or proposal.file_path for rep in proposal.replacements),
        }
        before = {path: Path(path).read_bytes() for path in paths}
        after = self.apply_refactoring_multi_file(proposal)
        for path in paths:
            if Path(path).read_bytes() != before[path]:
                raise ChangeConflict(f"Source changed during planning: {path}")
        return ChangePlan.from_sources(before, after)

    def apply_refactoring_multi_file(self, proposal: RefactoringProposal) -> Dict[str, str]:
        """Render without writing or mutating caller-owned proposal ASTs."""
        for path, digest in proposal.source_digests:
            if (
                hashlib.sha256(Path(path).read_text(encoding="utf-8").encode("utf-8")).hexdigest()
                != digest
            ):
                raise ChangeConflict(f"Stale proposal; analyze again: {path}")
        counters = self._helper_name_counters.copy()
        try:
            return self._materialize_refactoring(proposal)
        finally:
            self._helper_name_counters = counters

    def _render(self, node: ast.AST) -> str:
        """The source text inserted for a generated node, formatted when a formatter is set."""
        source = ast.unparse(node)
        if self.snippet_formatter is None:
            return source
        return self.snippet_formatter(source)

    def _materialize_refactoring(self, proposal: RefactoringProposal) -> Dict[str, str]:
        """
        Apply a cross-file refactoring proposal.

        Args:
            proposal: Refactoring proposal

        Returns:
            Dict mapping file paths to modified source code
        """
        # Materialization owns its ASTs; callers may reuse or inspect the proposal.
        proposal = copy.deepcopy(proposal)
        self._infer_helper_annotations(proposal)
        variants = [proposal]
        if self._checks_generated_types(proposal):
            variants += [
                self._with_every_annotation_any(proposal),
                self._without_annotations(proposal),
            ]
        counters = dict(self._helper_name_counters)
        for index, variant in enumerate(variants):
            mark = len(self._change_log)
            # Each attempt allocates the helper's name; restore the counters so
            # every attempt gets the same name and none is consumed by a retry.
            self._helper_name_counters = dict(counters)
            files = self._materialize_once(variant)
            if index == len(variants) - 1 or not self._introduces_type_errors(files):
                return files
            del self._change_log[mark:]
        raise AssertionError("unreachable: the bare variant is always accepted")

    def _checks_generated_types(self, proposal: RefactoringProposal) -> bool:
        """Whether the generated code is to be type-checked: a checker exists and the helper is annotated."""
        if self.type_inferrer is None or proposal.reused_function is not None:
            return False
        helper = proposal.extracted_function
        return helper.returns is not None or any(
            arg.annotation is not None for arg in helper.args.posonlyargs + helper.args.args
        )

    @staticmethod
    def _with_every_annotation_any(proposal: RefactoringProposal) -> RefactoringProposal:
        """The proposal with every helper annotation replaced by ``Any``."""
        variant = copy.deepcopy(proposal)
        helper = variant.extracted_function
        for arg in helper.args.posonlyargs + helper.args.args:
            arg.annotation = ast.Name(id="Any", ctx=ast.Load())
        helper.returns = ast.Name(id="Any", ctx=ast.Load())
        variant.required_imports = typing_imports_needed(
            helper, UnificationRefactorEngine._parsed_host(variant.file_path)
        )
        return variant

    @staticmethod
    def _without_annotations(proposal: RefactoringProposal) -> RefactoringProposal:
        """The proposal with the helper unannotated."""
        variant = copy.deepcopy(proposal)
        helper = variant.extracted_function
        for arg in helper.args.posonlyargs + helper.args.args:
            arg.annotation = None
        helper.returns = None
        variant.required_imports = ()
        return variant

    def _introduces_type_errors(self, modified_files: Dict[str, str]) -> bool:
        """Whether the checker reports an error in a modified file that its original lacks.

        Messages are compared without positions, as multisets, so errors the
        project already has do not count and moved lines do not confuse it.
        """
        assert self.type_inferrer is not None
        from collections import Counter

        for path, after_source in modified_files.items():
            before_source = self._read_source(path)
            if before_source is None or before_source == after_source:
                continue
            before = Counter(self.type_inferrer.check(path, before_source))
            after = Counter(self.type_inferrer.check(path, after_source))
            new = after - before
            if new:
                for message, count in new.items():
                    TYPES.debug("new error x%d in %s: %s", count, path, message)
                return True
        return False

    def _materialize_once(self, proposal: RefactoringProposal) -> Dict[str, str]:
        """Render one proposal into modified sources (see ``_materialize_refactoring``)."""
        # Group replacements by file
        replacements_by_file: Dict[str, List[Replacement]] = {}
        for repl in proposal.replacements:
            file_path = repl.file_path or proposal.file_path
            replacements_by_file.setdefault(file_path, []).append(repl)

        # Ensure canonical file is always processed so extracted helper is emitted
        replacements_by_file.setdefault(proposal.file_path, [])

        # Determine helper names ONCE to avoid mismatches across files
        # Capture the original helper name before any renaming, and compute the final name
        original_helper_name = proposal.extracted_function.name
        class_context = bool(proposal.insert_into_class) or any(
            replacement.class_name is not None for replacement in proposal.replacements
        )
        if proposal.reused_function is not None:
            pass  # the calls already name an existing function; it keeps its name
        elif original_helper_name == "__extracted_func" or (
            class_context and re.fullmatch(r"__extracted_func(?:_\d+)?", original_helper_name)
        ):
            proposal.extracted_function.name = self._allocate_helper_name(
                proposal.file_path,
                class_context=class_context,
                related_paths=list(replacements_by_file),
            )
        elif proposal.insert_into_class and not original_helper_name.startswith("_"):
            # Preserve user-provided helper names but keep them non-public inside classes
            proposal.extracted_function.name = f"_{original_helper_name}"
        final_func_name = proposal.extracted_function.name
        receiver_name = proposal.method_param_name or (
            "cls" if proposal.method_kind == "classmethod" else "self"
        )
        original_parameters = [arg.arg for arg in proposal.extracted_function.args.args]
        receiver_parameter_index = (
            original_parameters.index(receiver_name)
            if receiver_name in original_parameters
            else None
        )

        # Process each file
        modified_files: Dict[str, str] = {}

        for file_path, replacements in replacements_by_file.items():
            # Read original source
            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            # Sort replacements by line number (reverse order)
            replacements = sorted(replacements, key=lambda r: r.line_range[0], reverse=True)

            ascending = sorted(replacements, key=lambda replacement: replacement.line_range)
            if any(
                left.line_range[1] >= right.line_range[0]
                for left, right in zip(ascending, ascending[1:])
            ):
                raise ValueError(f"Overlapping replacements: {file_path}")
            # Apply each replacement (reverse sorted prevents earlier line shifts)
            for repl in replacements:
                start_line, end_line = repl.line_range
                replacement_node = copy.deepcopy(repl.node)
                class_name = repl.class_name

                if not 1 <= start_line <= end_line <= len(lines):
                    raise ValueError(
                        f"Invalid replacement range {start_line}-{end_line}: {file_path}"
                    )

                # Rewrite call sites for method extraction
                if proposal.insert_into_class and class_name:
                    replacement_node = self._rewrite_call_for_method(
                        replacement_node,
                        original_helper_name,
                        final_func_name,
                        repl.method_kind or proposal.method_kind,
                        repl.implicit_param or proposal.method_param_name,
                        class_name,
                        receiver_parameter_index,
                        len(original_parameters),
                    )
                elif proposal.insert_into_function:
                    # Rewrite to local function call (no attribute), but ensure call uses final_func_name
                    # Simple textual AST rewrite: replace original helper name with final
                    if original_helper_name != final_func_name:
                        replacement_node = self._retarget_helper_calls(
                            replacement_node,
                            original_helper_name,
                            final_func_name,
                        )
                else:
                    # Module-level insertion:
                    # If the helper was renamed (e.g., from '__extracted_func' to 'extracted_func' or custom),
                    # rewrite call sites accordingly.
                    if original_helper_name != final_func_name:
                        replacement_node = self._retarget_helper_calls(
                            replacement_node,
                            original_helper_name,
                            final_func_name,
                        )

                replacement_code = self._render(replacement_node)
                code_lines = replacement_code.split("\n")
                indent = self._get_indent(lines[start_line - 1])

                # Build indented replacement block: indent ALL non-empty lines consistently
                replacement_lines: List[str] = []
                for line in code_lines:
                    if line.strip():
                        replacement_lines.append(indent + line + "\n")
                    else:
                        replacement_lines.append("\n")

                # Record the true before/after for this call site before splicing.
                # A call to an existing function needs no naming, so it is not logged.
                if proposal.reused_function is None:
                    self._change_log.append(
                        {
                            "helper": final_func_name,
                            "path": file_path,
                            "line": start_line,
                            "before": textwrap.dedent(
                                "".join(lines[start_line - 1 : end_line])
                            ).rstrip("\n"),
                            "after": replacement_code,
                        }
                    )
                # Splice into source
                lines[start_line - 1 : end_line] = replacement_lines

            # Insert helper into canonical file or import into others
            if proposal.reused_function is not None and file_path == proposal.file_path:
                pass  # the function the calls target is already defined here
            elif file_path == proposal.file_path:
                # Names an inferred annotation needs that the module does not bind.
                for module_name, name in proposal.required_imports:
                    required_line = f"from {module_name} import {name}\n"
                    if not any(required_line.strip() == ln.strip() for ln in lines):
                        lines.insert(self._find_import_position(lines), required_line)
                if proposal.insert_into_function:
                    fn_insert_info = self._find_function_insert_position_before_body_statements(
                        "".join(lines), proposal.insert_into_function
                    )
                    fn_code = self._render(proposal.extracted_function)
                    fn_lines = [l + "\n" for l in fn_code.split("\n")]
                    if fn_insert_info is None:
                        insert_line = self._find_insert_position(lines)
                        lines[insert_line:insert_line] = fn_lines + ["\n", "\n"]
                    else:
                        insert_at_zero_based, indent = fn_insert_info
                        inner_indent = indent + "    "
                        indented: List[str] = []
                        for line in fn_lines:
                            if line.strip():
                                indented.append(inner_indent + line)
                            else:
                                indented.append(line)
                        prefix: List[str] = []
                        if insert_at_zero_based > 0 and lines[insert_at_zero_based - 1].strip():
                            prefix.append("\n")
                        suffix: List[str] = []
                        if (
                            insert_at_zero_based < len(lines)
                            and lines[insert_at_zero_based].strip()
                        ):
                            suffix.append("\n")
                        lines[insert_at_zero_based:insert_at_zero_based] = (
                            prefix + indented + suffix
                        )
                elif proposal.insert_into_class:
                    # Prepare method signature & decorator
                    method_kind = proposal.method_kind or "instance"
                    self._prepare_extracted_method_signature(
                        proposal.extracted_function,
                        method_kind,
                        proposal.method_param_name,
                    )
                    func_code = self._render(proposal.extracted_function)
                    method_lines = [line + "\n" for line in func_code.split("\n")]
                    insert_info = self._find_class_insert_position(
                        "".join(lines), proposal.insert_into_class
                    )
                    if insert_info is None:
                        raise RefactoringError(
                            f"Class {proposal.insert_into_class} is not a unique module-level "
                            f"class in {file_path}; cannot insert a method"
                        )
                    else:
                        insert_line_zero_based, method_indent = insert_info
                        indented_method: List[str] = [
                            _reindent(line, method_indent) if line.strip() else line
                            for line in method_lines
                        ]
                        insert_at = insert_line_zero_based + 1
                        method_prefix: List[str] = []
                        if insert_at > 0 and lines[insert_at - 1].strip():
                            method_prefix.append("\n")
                        method_suffix: List[str] = []
                        if insert_at < len(lines) and lines[insert_at].strip():
                            method_suffix.append("\n")
                        lines[insert_at:insert_at] = method_prefix + indented_method + method_suffix
                else:
                    func_code = self._render(proposal.extracted_function)
                    func_lines = [line + "\n" for line in func_code.split("\n")]
                    insert_line = self._find_insert_position(
                        lines, self._annotation_names(proposal.extracted_function)
                    )
                    lines_to_insert: List[str] = []
                    if insert_line > 0:
                        blank_lines_before = 0
                        check_line = insert_line - 1
                        while check_line >= 0 and not lines[check_line].strip():
                            blank_lines_before += 1
                            check_line -= 1
                        if blank_lines_before < 2:
                            lines_to_insert.extend(["\n"] * (2 - blank_lines_before))
                    lines_to_insert.extend(func_lines)
                    lines_to_insert.extend(["\n", "\n"])
                    lines[insert_line:insert_line] = lines_to_insert
            else:
                # Non-canonical file: insert import (module-level only)
                if not proposal.insert_into_class:
                    from_path = Path(proposal.file_path)
                    to_path = Path(file_path)
                    common_dir = Path(os.path.commonpath([str(from_path), str(to_path)]))

                    layout = ProjectLayout.discover(
                        common_dir,
                        prefer_absolute_imports=self.prefer_absolute_imports,
                        pep420_namespace_packages=self.pep420_namespace_packages,
                    )

                    abs_mod = layout.module_name_for(from_path)
                    relative = _relative_import_module(from_path, to_path)
                    # A relative import only resolves inside a classic package; flat
                    # modules on sys.path (no __init__.py) must use an absolute name.
                    importer_in_package = is_package_dir(to_path.parent, pep420=False)
                    # Prefer an absolute import only when the layout is anchored by
                    # real packaging metadata, so the name stays valid after an
                    # out-of-place output is adopted into its real location. Otherwise
                    # use a relative import when the file is in a package: it encodes
                    # only the intrinsic same-package relationship, is valid wherever
                    # the code lands, and matches the surrounding intra-package style.
                    if abs_mod and layout.prefer_absolute_imports and layout.metadata_root:
                        module_name = abs_mod
                    elif relative is not None and importer_in_package:
                        module_name = relative
                    else:
                        module_name = abs_mod or from_path.stem

                    func_name = proposal.extracted_function.name
                    import_line = f"from {module_name} import {func_name}\n"
                    if not any(import_line.strip() == ln.strip() for ln in lines):
                        import_pos = self._find_import_position(lines)
                        lines.insert(import_pos, import_line)

            assembled = "".join(lines)
            if self.file_finisher is not None:
                assembled = self.file_finisher(file_path, assembled)
            modified_files[file_path] = assembled

        for path, content in modified_files.items():
            compile(content, path, "exec")
        if proposal.reused_function is not None:
            self._verify_reused_function_calls(modified_files, proposal.reused_function, proposal)
        else:
            self._verify_helper_call_arity(modified_files, final_func_name, proposal.method_kind)
        return modified_files

    def _verify_reused_function_calls(
        self,
        modified_files: Dict[str, str],
        target: ReusedFunction,
        proposal: RefactoringProposal,
    ) -> None:
        """Fail loudly if the reused function is gone or a generated call cannot bind to it.

        Pre-existing calls are not checked: they may legitimately use keywords or
        rely on defaults. Only the calls this proposal generates must pass
        exactly the function's positional parameters.
        """
        source = modified_files.get(target.file_path)
        if source is None:
            source = Path(target.file_path).read_text(encoding="utf-8")
        definitions = [
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef) and node.name == target.name
        ]
        if not definitions:
            raise RefactoringError(
                f"Reused function {target.name} is no longer defined at module level: "
                f"{target.file_path}"
            )
        # ``@overload`` stubs precede the real definition; the last one is the
        # runtime binding the calls resolve to, as the redirect required.
        parameters = self._positional_parameter_names(definitions[-1])
        for replacement in proposal.replacements:
            call = self._unwrap_helper_call(replacement.node, target.name)
            if parameters is None or call is None or len(call.args) != len(parameters):
                raise RefactoringError(
                    f"Generated call to {target.name} does not bind its "
                    f"{len(parameters or [])} parameters: {replacement.file_path}"
                )

    @staticmethod
    def _verify_helper_call_arity(
        modified_files: Dict[str, str],
        helper_name: str,
        method_kind: Optional[Literal["instance", "classmethod", "staticmethod"]],
    ) -> None:
        """Fail loudly if any generated call cannot bind to the generated helper.

        Method conversion and receiver removal happen after the instantiation
        check, so this compares the rendered helper signature with every call
        that names it. Bound calls omit the receiver; static calls do not.
        """
        parameters: Optional[int] = None
        for source in modified_files.values():
            for node in ast.walk(ast.parse(source)):
                if (
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == helper_name
                ):
                    parameters = len(node.args.posonlyargs) + len(node.args.args)
        if parameters is None:
            raise RefactoringError(f"Helper {helper_name} was not emitted")
        for path, source in modified_files.items():
            for node in ast.walk(ast.parse(source)):
                if not isinstance(node, ast.Call):
                    continue
                function = node.func
                if isinstance(function, ast.Name) and function.id == helper_name:
                    expected = parameters
                elif isinstance(function, ast.Attribute) and function.attr == helper_name:
                    expected = parameters if method_kind == "staticmethod" else parameters - 1
                else:
                    continue
                if node.keywords or any(isinstance(arg, ast.Starred) for arg in node.args):
                    continue
                if len(node.args) != expected:
                    raise RefactoringError(
                        f"Generated call to {helper_name} passes {len(node.args)} arguments "
                        f"but the helper binds {expected}: {path}"
                    )

    def _find_import_position(self, lines: List[str]) -> int:
        """Return the 0-based line index at which to insert a new import.

        The position follows the module docstring and any leading imports,
        determined from the parsed module so that text inside comments or
        docstrings is never mistaken for an import.
        """
        body = ast.parse("".join(lines)).body
        position = 0
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            position = body[0].end_lineno or body[0].lineno
            body = body[1:]
        for statement in body:
            if not isinstance(statement, (ast.Import, ast.ImportFrom)):
                break
            position = statement.end_lineno or statement.lineno
        return position

    def _get_indent(self, line: str) -> str:
        """Get the indentation of a line."""
        return line[: len(line) - len(line.lstrip())]

    _DEFINITION_LIKE = (
        ast.Import,
        ast.ImportFrom,
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.ClassDef,
        ast.Assign,
        ast.AnnAssign,
    )

    @classmethod
    def _is_definition_like(cls, statement: ast.stmt) -> bool:
        """Whether a module-level statement runs no code of the module's own at import.

        Definitions, imports, assignments and docstrings; an ``if`` or ``try``
        whose bodies are all such statements (``TYPE_CHECKING`` guards,
        optional imports). Anything else may call into the module, so a helper
        must be defined before it.
        """
        if isinstance(statement, cls._DEFINITION_LIKE):
            return True
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
            return True
        if isinstance(statement, ast.If):
            return all(cls._is_definition_like(s) for s in statement.body + statement.orelse)
        if isinstance(statement, ast.Try):
            nested = statement.body + statement.orelse + statement.finalbody
            nested += [s for handler in statement.handlers for s in handler.body]
            return all(cls._is_definition_like(s) for s in nested)
        return False

    @classmethod
    def placeable_after(cls, source: str) -> Set[str]:
        """Names of module-level definitions a helper can safely be placed after.

        A helper placed after the definitions its annotations name can spell
        them bare. It may move past a definition only if everything from the
        top of the module to that definition is definition-like, so no code
        that could call the helper runs before it is defined.
        """
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return set()
        names: Set[str] = set()
        for statement in tree.body:
            if not cls._is_definition_like(statement):
                break
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(statement.name)
            elif isinstance(statement, ast.Assign):
                names.update(t.id for t in statement.targets if isinstance(t, ast.Name))
            elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
                names.add(statement.target.id)
        return names

    def _find_insert_position(
        self, lines: List[str], after_names: Optional[Set[str]] = None
    ) -> int:
        """Place a helper before the first definition, after any leading imports.

        Helpers have no evaluated defaults. When ``after_names`` is given (the
        names a helper's annotations refer to), the helper goes after the last
        module-level definition of one of them instead, so those annotations
        can be written bare; the caller guarantees through ``placeable_after``
        that nothing before that point runs code at import.
        """
        tree = ast.parse("".join(lines))
        after = 0
        after_names = after_names or set()
        for statement in tree.body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if statement.name in after_names:
                    after = max(after, statement.end_lineno or statement.lineno)
            elif isinstance(statement, ast.Assign):
                if any(isinstance(t, ast.Name) and t.id in after_names for t in statement.targets):
                    after = max(after, statement.end_lineno or statement.lineno)
            elif isinstance(statement, ast.AnnAssign):
                if isinstance(statement.target, ast.Name) and statement.target.id in after_names:
                    after = max(after, statement.end_lineno or statement.lineno)
        if after:
            return after
        for statement in tree.body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                return min([statement.lineno] + [d.lineno for d in statement.decorator_list]) - 1
        return len(lines)

    def _find_class_insert_position(
        self, source: str, class_name: str
    ) -> Optional[Tuple[int, str]]:
        """
        Find insertion position (0-based line index) at end of class body and class indentation.

        Returns (insert_line_index, class_indent_str) or None.
        """
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None

        locator = ClassLocator(source, class_name)
        locator.visit(tree)
        return locator.result

    def _find_function_insert_position_before_body_statements(
        self, source: str, function_name: str
    ) -> Optional[Tuple[int, str]]:
        """
        Find an insertion position (0-based line index) inside the given function BEFORE
        executable body statements (i.e., after any docstring and after any leading
        nested defs), along with the function's indentation.

        This ensures the inserted helper is bound before returns/calls are executed.
        Returns (insert_line_index, function_indent_str) or None if function not found.
        """
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None

        locator = FuncLocator(source, function_name)
        locator.visit(tree)
        return locator.result

    def _get_block_indices(
        self, function: FunctionNode, block_nodes: List[ast.AST]
    ) -> Optional[Tuple[int, int]]:
        """
        Find the indices of a block within a function body.

        Args:
            function: Function definition
            block_nodes: Block to find

        Returns:
            (start_index, end_index) or None if not found
        """
        if not block_nodes:
            return None

        # Get function body (skip docstring)
        body = body_without_docstring(function.body)

        # Match by line numbers
        first_node = cast(Union[ast.stmt, ast.expr], block_nodes[0])
        last_node = cast(Union[ast.stmt, ast.expr], block_nodes[-1])
        block_start_line = first_node.lineno
        block_end_line = (
            last_node.end_lineno
            if hasattr(last_node, "end_lineno") and last_node.end_lineno is not None
            else last_node.lineno
        )

        # Find matching range in body
        for i, stmt in enumerate(body):
            stmt_start = stmt.lineno
            stmt_end = stmt.end_lineno if hasattr(stmt, "end_lineno") else stmt.lineno

            if stmt_start == block_start_line:
                # Found start, now find end
                for j in range(i, len(body)):
                    stmt_end = (
                        body[j].end_lineno if hasattr(body[j], "end_lineno") else body[j].lineno
                    )
                    if stmt_end == block_end_line:
                        return (i, j)

        return None

    def _are_structurally_similar(
        self,
        block1: List[ast.AST],
        block2: List[ast.AST],
        threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    ) -> bool:
        """
        Check if two blocks are structurally similar enough to attempt unification.

        This does a rough structural comparison to filter out obviously different blocks.

        Args:
            block1: First block
            block2: Second block
            threshold: Similarity threshold (0.0 to 1.0, default: 0.6)

        Returns:
            True if blocks are similar enough
        """
        if len(block1) != len(block2):
            return False

        total_nodes = 0
        matching_nodes = 0

        for stmt1, stmt2 in zip(block1, block2):
            # Compare AST structure
            nodes1 = list(ast.walk(stmt1))
            nodes2 = list(ast.walk(stmt2))

            # Must have similar number of nodes
            if abs(len(nodes1) - len(nodes2)) / max(len(nodes1), len(nodes2)) > 0.3:
                return False

            # Count matching node types
            types1 = [type(n).__name__ for n in nodes1]
            types2 = [type(n).__name__ for n in nodes2]

            # Count common types
            from collections import Counter

            counter1 = Counter(types1)
            counter2 = Counter(types2)

            common = sum((counter1 & counter2).values())
            total = max(len(types1), len(types2))

            total_nodes += total
            matching_nodes += common

        if total_nodes == 0:
            return False

        similarity = matching_nodes / total_nodes
        return similarity >= threshold

    def refactor_to_fixed_point(
        self, file_path: str, max_iterations: int = 10
    ) -> Tuple[str, int, List[str]]:
        """
            Apply refactorings iteratively until a fixed point is reached.

        This method applies refactorings one at a time, re-analyzing after each
        application. This prevents the sequential corruption bug where applying
        multiple refactorings at once causes line number misalignment.

        max_iterations semantics:
        - If max_iterations > 0, stop after at most that many applied refactorings.
        - If max_iterations <= 0, run until a natural fixed point (no proposals found).

            Args:
                file_path: Path to file to refactor
                max_iterations: Maximum iterations to prevent infinite loops

            Returns:
                Tuple of (final_code, num_refactorings_applied, descriptions)
        """
        self._change_log = []
        current_bytes = Path(file_path).read_bytes()
        current_code = current_bytes.decode("utf-8")
        num_applied = 0
        descriptions = []

        iteration = 0
        while True:
            # Analyze for refactoring opportunities
            # Important: invalidate cached analysis for this path so we see latest edits
            proposals = self.analyze_files([file_path], invalidate_paths=[file_path])

            if not proposals:
                # Fixed point reached - no more refactorings found
                break

            # Apply only the first proposal
            proposal = proposals[0]
            new_code = self.apply_refactoring(file_path, proposal)
            # Idempotence guard: if no change, stop to avoid churn
            if new_code == current_code:
                break
            compile(new_code, file_path, "exec")
            apply_changes(
                ChangePlan.from_sources({file_path: current_bytes}, {file_path: new_code})
            )
            current_code = new_code
            current_bytes = new_code.encode("utf-8")
            num_applied += 1
            descriptions.append(proposal.description)

            iteration += 1
            if max_iterations > 0 and iteration >= max_iterations:
                break

        return current_code, num_applied, descriptions

    _FRAME_SENSITIVE_DESCRIPTION = {
        "frame": "inspect call frames",
        "traceback": "read exception tracebacks",
        "stacklevel-warning": "attribute warnings to a caller's frame",
        "source": "read Python source text",
    }

    def _warn_about_frame_sensitive_files(self, directory: str) -> None:
        """Warn that some modules observe frames, tracebacks, or their own source.

        Extraction adds a helper frame and shifts line numbers, so a program
        that reads any of these can observe the change even when the result it
        computes is unchanged (pluggy attributes a warning through the new
        frame, lark's standalone tool copies marked source regions, glom and
        rich assert on rendered tracebacks). The scan names files to review;
        it is not a proof of breakage, and a callee that inspects frames
        internally is invisible to it. Emitted on stderr like any diagnostic.
        """
        flagged: List[Tuple[str, FrozenSet[str]]] = []
        for path in self._find_python_files(directory):
            try:
                markers = frame_sensitivity_markers(Path(path).read_text(encoding="utf-8"))
            except OSError:
                continue
            if markers:
                flagged.append((path, markers))
        if not flagged:
            return
        kinds = sorted({m for _path, markers in flagged for m in markers})
        described = ", ".join(self._FRAME_SENSITIVE_DESCRIPTION.get(k, k) for k in kinds)
        directory_root = Path(directory)
        lines = [
            f"warning: {len(flagged)} module(s) in this project {described}; a "
            "transformation that adds a helper frame or shifts line numbers may "
            "change their observable behavior even when it preserves the "
            "program's result. Review these files' diffs or pass --exclude:"
        ]
        for path, markers in sorted(flagged):
            try:
                shown = str(Path(path).relative_to(directory_root))
            except ValueError:
                shown = path
            lines.append(f"    {shown} ({', '.join(sorted(markers))})")
        LOG.warning("\n".join(lines))

    def refactor_directory_to_fixed_point(
        self,
        input_dir: str,
        output_dir: str,
        max_iterations: int = 10,
        progress: str = "tqdm",
    ) -> Tuple[Dict[str, Tuple[int, List[str]]], str]:
        """
        Apply refactorings across a directory (recursively) until a fixed point.

        Unlike the per-file variant, this performs whole-project analysis on every
        iteration so it can apply BOTH same-file and cross-file proposals. One proposal
        is applied per iteration, then the directory is re-analyzed, up to max_iterations.

        Args:
            input_dir: Input directory path
            output_dir: Output directory path (results are written here)
            max_iterations: Maximum iterations to prevent infinite loops. If <= 0,
                run until a natural fixed point (no proposals remain).
            progress: Progress display mode: 'tqdm' for percentage bar if available,
                'auto' fallback to simple inline bar, 'none' disables progress output.

        Returns:
            (results_dict, termination_reason)
            termination_reason ∈ {"fixed_point", "iteration_cap"}
        """
        self._change_log = []
        from pathlib import Path
        import textwrap

        progress_mode, tqdm_wrapper, use_tqdm = self._resolve_progress_backend(progress)

        input_path = Path(input_dir)
        output_path = Path(output_dir)
        resolved_input = input_path.resolve()
        resolved_output = output_path.resolve()
        if resolved_input != resolved_output:
            if (
                resolved_input in resolved_output.parents
                or resolved_output in resolved_input.parents
            ):
                raise ValueError("Input and output must not contain one another")
            if output_path.exists() and any(output_path.iterdir()):
                raise ValueError("Output directory must be empty")

        if resolved_input != resolved_output:
            from towel.filesystem import copy_project

            copy_project(input_path, output_path, allow_empty=True)
        elif not output_path.is_dir():
            raise ValueError("Input directory does not exist")

        self._warn_about_frame_sensitive_files(output_dir)

        # Aggregate results per file
        results: Dict[str, Tuple[int, List[str]]] = {}

        def _bump_result(path: str, desc: str) -> None:
            count, descs = results.get(path, (0, []))
            results[path] = (count + 1, descs + [desc])

        # Proposal processing state
        proposal_queue: List[RefactoringProposal] = []
        # Files rewritten since the last global pass: the next global pass
        # re-pairs only functions in these (see ``incremental_global_passes``).
        changed_since_global: Set[str] = set()
        global_passes = 0
        iterations = 0
        total_applied = 0
        termination_reason = "fixed_point"

        # Timing / ETA state (for heuristic ETA when total unknown)
        per_proposal_durations: List[float] = []

        # Progress helpers -------------------------------------------------
        progress_bar = None

        # Fallback inline bar (only when not using tqdm and not in detail/none)
        def _fallback_bar(applied: int, queued: int, phase: str, desc: str) -> None:
            # Suppress inline fallback bar when tqdm is selected or active, or in 'none'/'detail' modes
            if progress_mode in ("none", "detail", "tqdm") or use_tqdm:
                return
            denom = max(applied + queued, 1)
            pct = int((applied / denom) * 100)
            bar = render_inline_bar(pct, bar_len=32)
            short = desc if len(desc) <= 48 else desc[:45] + "..."
            quietly(
                lambda: print(
                    f"\r[towel] {phase:<10} [{bar}] {pct:3d}% "
                    f"| applied={applied} queued={queued} | {short}",
                    end="",
                    flush=True,
                )
            )

        def _update_progress_postfix(applied: int, queued: int) -> None:
            """Keep tqdm postfix updates consistent."""
            if not (use_tqdm and progress_bar is not None):
                return
            bar = progress_bar
            quietly(lambda: bar.set_postfix({"A": applied, "Q": queued}, refresh=True))

        def _apply_proposal_and_refresh_queue(
            proposal: RefactoringProposal, queue: List[RefactoringProposal]
        ) -> List[RefactoringProposal]:
            """Apply a proposal, invalidate the affected caches, and refresh the queue."""
            before = {
                path: Path(path).read_bytes()
                for path in {
                    proposal.file_path,
                    *(rep.file_path or proposal.file_path for rep in proposal.replacements),
                }
            }
            result = self.apply_refactoring_multi_file(proposal)
            if isinstance(result, tuple):
                modified_files, changed_paths = result
            else:
                modified_files = result
                changed_paths = list(modified_files.keys())

            apply_changes(ChangePlan.from_sources(before, modified_files))
            for fpath in modified_files:
                _bump_result(fpath, proposal.description)
                changed_since_global.add(os.path.abspath(str(fpath)))

            if changed_paths:
                self.invalidate_paths(changed_paths)

            if changed_paths:
                changed_set = set(map(str, changed_paths))
                queue = [
                    p for p in queue if not ({path for path, _ in p.source_digests} & changed_set)
                ]

                localized = self.analyze_files(
                    list(changed_paths), invalidate_paths=list(changed_paths)
                )
                if localized:
                    localized = filter_overlapping_proposals(localized)
                    localized = [
                        p
                        for p in localized
                        if any(
                            (rep.file_path or p.file_path) in changed_set for rep in p.replacements
                        )
                    ]
                    if localized:
                        queue = localized + queue
                        _detail(f"Localized +{len(localized)} follow-up(s)")
                        _fallback_bar(
                            total_applied,
                            len(queue),
                            "localized",
                            f"+{len(localized)} follow-ups",
                        )

            return queue

        # Note: We defer tqdm progress bar creation until we have proposals to apply.
        # This avoids an early line like "analyzing: 0it" with unknown totals.

        def _detail(msg: str) -> None:
            if progress_mode == "detail":
                print(f"[towel] {msg}")

        # Main loop -------------------------------------------------------
        while True:
            if not proposal_queue:
                # Global analysis pass
                if progress_mode != "none":
                    try:
                        file_count = sum(1 for _ in output_path.rglob("*.py"))
                        _detail(f"Analyzing {file_count} file(s)...")
                    except OSError:
                        # Only the directory walk for a progress message; a real
                        # I/O problem will resurface in the analysis that follows.
                        pass
                # Show pairing progress during global analysis if user requested progress bars.
                analysis_progress_flag = (
                    progress_mode if progress_mode in ("tqdm", "auto") else "none"
                )
                restrict = (
                    frozenset(changed_since_global)
                    if self.incremental_global_passes and global_passes > 0 and changed_since_global
                    else None
                )
                proposals = self.analyze_directory(
                    str(output_path),
                    recursive=True,
                    verbose=False,
                    progress=analysis_progress_flag,
                    changed_files=restrict,
                )
                global_passes += 1
                changed_since_global.clear()
                if not proposals:
                    # Fixed point reached
                    if use_tqdm and progress_bar is not None:
                        # Ensure a clean newline so the last line doesn't meld with following prints
                        bar = progress_bar

                        def finish() -> None:
                            bar.refresh()
                            bar.close()

                        quietly(finish)
                    else:
                        if progress_mode not in ("none", "detail") and not use_tqdm:
                            print()  # finish inline bar line
                    break
                proposal_queue = filter_overlapping_proposals(proposals)
                _detail(f"Discovered {len(proposal_queue)} proposal(s)")
                if progress_mode == "detail":
                    for i, p in enumerate(proposal_queue[:25], 1):  # cap verbose listing
                        short = textwrap.shorten(p.description, width=100, placeholder="...")
                        print(f"    {i:2d}. {short}")
                    if len(proposal_queue) > 25:
                        print(f"    ... {len(proposal_queue)-25} more")
                _fallback_bar(total_applied, len(proposal_queue), "discovered", "proposals queued")
                if use_tqdm and progress_bar is None:
                    # Lazily create tqdm now that we have proposals to apply
                    assert tqdm_wrapper is not None
                    try:
                        total_known = max_iterations > 0
                        # Use leave=False so subsequent prints don't duplicate the bar line.
                        progress_bar = tqdm_wrapper(
                            total=max_iterations if total_known else None,
                            desc="apply",
                            unit="it",
                            dynamic_ncols=True,
                            leave=False,
                        )
                        queued_ct = len(proposal_queue)
                        _update_progress_postfix(0, queued_ct)
                    except Exception:
                        progress_bar = None

            if not proposal_queue:
                break

            proposal = self._pop_next_proposal(proposal_queue)
            if proposal is None:
                break
            iter_start = time.time()
            last_desc = proposal.description
            # Suppress separate applying log line when tqdm active to avoid duplicate lines
            if not (use_tqdm and progress_bar is not None):
                _fallback_bar(
                    total_applied, len(proposal_queue), "apply", f"#{iterations+1}: {last_desc}"
                )

            # Apply proposal. A proposal computed before an earlier application
            # changed one of its files is stale: drop it and re-analyze those
            # files so a fresh proposal can take its place.
            try:
                proposal_queue = _apply_proposal_and_refresh_queue(proposal, proposal_queue)
            except ChangeConflict as conflict:
                stale_paths = sorted({path for path, _ in proposal.source_digests})
                _detail(
                    f"Dropped stale proposal ({conflict}); re-analyzing {len(stale_paths)} file(s)"
                )
                if debugging(REJECTIONS):
                    REJECTIONS.debug(
                        "STALE: %s :: paths=%s :: replacements=%s",
                        proposal.description,
                        stale_paths,
                        [rep.file_path or proposal.file_path for rep in proposal.replacements],
                    )
                self.invalidate_paths(stale_paths)
                proposal_queue = [
                    p
                    for p in proposal_queue
                    if not ({path for path, _ in p.source_digests} & set(stale_paths))
                ]
                refreshed = self.analyze_files(stale_paths, invalidate_paths=stale_paths)
                if refreshed:
                    proposal_queue = filter_overlapping_proposals(refreshed) + proposal_queue
                continue

            # Record duration for this iteration (include localized follow-up analysis time)
            per_proposal_durations.append(time.time() - iter_start)
            iterations += 1
            total_applied += 1
            if use_tqdm and progress_bar is not None:
                bar = progress_bar

                def advance() -> None:
                    bar.update(1)
                    _update_progress_postfix(total_applied, len(proposal_queue))

                quietly(advance)
            else:
                _fallback_bar(
                    total_applied, len(proposal_queue), "applied", f"#{iterations}: {last_desc}"
                )

            if max_iterations > 0 and iterations >= max_iterations:
                termination_reason = "iteration_cap"
                if use_tqdm and progress_bar is not None:
                    progress_bar.close()
                else:
                    if progress_mode not in ("none", "detail") and not use_tqdm:
                        print()
                break

        return results, termination_reason

    # Optional analysis cache invalidation hook used by directory fixed-point runner
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


def _unique_module_level_class(class_infos: Sequence[ClassInfo], file_path: str, name: str) -> bool:
    """Whether ``name`` names exactly one class in ``file_path`` and it is module-level."""
    matches = [info for info in class_infos if info.file_path == file_path and info.name == name]
    return len(matches) == 1 and matches[0].qualname == name


def _reindent(line: str, prefix: str) -> str:
    """Prefix an ``ast.unparse`` line, converting its 4-space levels to the file's unit."""
    stripped = line.lstrip(" ")
    levels = (len(line) - len(stripped)) // 4
    unit = "\t" if "\t" in prefix else "    "
    return prefix + unit * levels + stripped


def _line_ranges_intersect(left: Tuple[int, int], right: Tuple[int, int]) -> bool:
    """Whether two inclusive line ranges share at least one line."""
    return left[0] <= right[1] and right[0] <= left[1]


def _relative_import_module(from_path: Path, to_path: Path) -> Optional[str]:
    """The relative-import module for reaching ``from_path`` from ``to_path``.

    Ascends from the importing file's own directory until it contains the helper
    file, using one leading dot for that package plus one more per level climbed:
    ``.helpers`` for a sibling module, ``.sub.helpers`` for one in a subpackage,
    ``..helpers`` for one a level up. Returns ``None`` when the two files share no
    directory tree, so a relative import cannot reach across.
    """
    helper = from_path.resolve()
    package_dir = to_path.resolve().parent
    dots = 1
    while True:
        try:
            relative = helper.relative_to(package_dir)
            break
        except ValueError:
            parent = package_dir.parent
            if parent == package_dir:
                return None
            package_dir = parent
            dots += 1
    parts = list(relative.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return "." * dots + ".".join(parts)


def get_affected_lines(proposal: RefactoringProposal) -> Set[Tuple[str, int]]:
    """
    Get all (file_path, line_number) tuples affected by a proposal.

    This is used for overlap detection - two proposals overlap if they
    affect any of the same lines in the same file.

    Args:
        proposal: A refactoring proposal

    Returns:
        Set of (file_path, line_number) tuples that would be modified
    """
    affected: Set[Tuple[str, int]] = set()
    for repl in proposal.replacements:
        file_path = repl.file_path or proposal.file_path
        start_line, end_line = repl.line_range
        for line_num in range(start_line, end_line + 1):
            affected.add((file_path, line_num))
    # The reused definition is not edited, but every call now depends on it
    # staying as it is, so a proposal that would rewrite it conflicts.
    if proposal.reused_function is not None:
        start_line, end_line = proposal.reused_function.line_range
        for line_num in range(start_line, end_line + 1):
            affected.add((proposal.reused_function.file_path, line_num))

    return affected


def filter_overlapping_proposals(proposals: List[RefactoringProposal]) -> List[RefactoringProposal]:
    """
    Filter proposals to remove overlaps, keeping the best ones.

    When multiple proposals affect overlapping lines, this function selects
    a subset of non-overlapping proposals. Larger proposals (affecting more
    lines) are preferred over smaller ones.

    Strategy:
    1. Sort proposals by size (total lines affected), largest first
    2. Greedily select proposals that don't overlap with already-selected ones

    Args:
        proposals: List of refactoring proposals

    Returns:
        List of non-overlapping proposals, sorted by size (largest first)

    Example:
        If proposals affect lines [1-7], [1-6], and [2-7], only [1-7]
        would be selected as it's the largest and the others overlap with it.
    """
    if not proposals:
        return []

    def proposal_size(p: RefactoringProposal) -> int:
        """Total lines a proposal covers, counting a reused definition as covered."""
        return len(get_affected_lines(p))

    # Optional debug diagnostics: env flag
    _debug_overlap = debugging(OVERLAP)

    # Helpers for deterministic ordering and interval extraction
    def first_span(p: RefactoringProposal) -> Tuple[str, int]:
        if not p.replacements:
            return (p.file_path, 0)
        starts = [r.line_range[0] for r in p.replacements]
        return (p.file_path, min(starts))

    # Map proposals to affected lines and per-file convex-hull intervals
    prop_affected: Dict[int, Set[Tuple[str, int]]] = {}
    prop_files: Dict[int, Set[str]] = {}
    per_file_interval: Dict[int, Dict[str, Tuple[int, int]]] = {}

    for idx, p in enumerate(proposals):
        affected = get_affected_lines(p)
        prop_affected[idx] = affected
        files: Set[str] = set(fp for (fp, _ln) in affected)
        prop_files[idx] = files
        per_file_interval[idx] = {}
        # Build convex hull interval per file for MWIS approximation
        by_file: Dict[str, List[int]] = {}
        for fp, ln in affected:
            by_file.setdefault(fp, []).append(ln)
        for fp, lines in by_file.items():
            per_file_interval[idx][fp] = (min(lines), max(lines))

    # Stage 1: Optimal non-overlapping selection within single-file proposals using MWIS
    selected_indices: Set[int] = set()
    used_lines: Set[Tuple[str, int]] = set()

    # Group single-file proposals by that file
    by_primary_file: Dict[str, List[int]] = {}
    for idx, files in prop_files.items():
        if len(files) == 1:
            fp = next(iter(files))
            by_primary_file.setdefault(fp, []).append(idx)

    def run_weighted_interval_scheduling(file_path: str, indices: List[int]) -> List[int]:
        # Build items: (start, end, weight, idx)
        items: List[Tuple[int, int, int, int, Tuple[str, int]]] = []
        for idx in indices:
            start, end = per_file_interval[idx][file_path]
            weight = proposal_size(proposals[idx])
            items.append((start, end, weight, idx, first_span(proposals[idx])))

        # Sort by end then tie-breaker to stabilize
        items.sort(key=lambda t: (t[1], t[0], -t[2], t[4]))

        n = len(items)
        if n == 0:
            return []

        # Precompute p[j]: rightmost non-overlapping interval index before j
        ends = [it[1] for it in items]
        starts = [it[0] for it in items]
        p = [-1] * n
        j = 0
        for j in range(n):
            # binary search for last i with ends[i] < starts[j]
            lo, hi = 0, j - 1
            last = -1
            while lo <= hi:
                mid = (lo + hi) // 2
                if ends[mid] < starts[j]:
                    last = mid
                    lo = mid + 1
                else:
                    hi = mid - 1
            p[j] = last

        # DP arrays
        dp = [0] * n
        take = [False] * n
        for j in range(n):
            wj = items[j][2]
            without = dp[j - 1] if j > 0 else 0
            withj = wj + (dp[p[j]] if p[j] != -1 else 0)
            if withj > without:
                dp[j] = withj
                take[j] = True
            elif withj == without:
                # Tie-breaker: prefer earlier ending interval set implicitly
                dp[j] = without
                take[j] = False
            else:
                dp[j] = without
                take[j] = False

        # Reconstruct
        sel: List[int] = []
        j = n - 1
        while j >= 0:
            if take[j]:
                sel.append(items[j][3])
                j = p[j]
            else:
                j -= 1
        sel.reverse()
        if _debug_overlap:
            OVERLAP.debug(
                "OVERLAP_OPTIMAL file=%s selected=%d total_weight=%s candidates=%d",
                file_path,
                len(sel),
                dp[n - 1],
                n,
            )
        return sel

    for fp, idxs in by_primary_file.items():
        chosen = run_weighted_interval_scheduling(fp, idxs)
        for idx in chosen:
            if idx not in selected_indices:
                selected_indices.add(idx)
                used_lines.update(prop_affected[idx])

    # Stage 2: Greedy add for remaining proposals (multi-file or leftover), respecting used_lines
    def sort_key(p: RefactoringProposal) -> Tuple[int, Tuple[str, int]]:
        return (-(proposal_size(p)), first_span(p))

    remaining = [i for i in range(len(proposals)) if i not in selected_indices]
    remaining_sorted = sorted(remaining, key=lambda i: sort_key(proposals[i]))

    selected: List[RefactoringProposal] = [proposals[i] for i in selected_indices]

    for idx in remaining_sorted:
        affected = prop_affected[idx]
        intersection = affected & used_lines
        if not intersection:
            selected.append(proposals[idx])
            used_lines.update(affected)
        elif _debug_overlap:
            by_file2: Dict[str, List[int]] = {}
            for fp, ln in intersection:
                by_file2.setdefault(fp, []).append(ln)
            parts = [
                f"{fp}:{min(lines)}-{max(lines)} ({len(lines)} lines)"
                for fp, lines in by_file2.items()
            ]
            OVERLAP.debug(
                "OVERLAP_DROP: size=%s first=%s because intersects %s",
                proposal_size(proposals[idx]),
                first_span(proposals[idx]),
                "; ".join(parts),
            )

    # Return selected sorted by size descending for external stability
    return sorted(selected, key=lambda p: (-(proposal_size(p)), first_span(p)))
