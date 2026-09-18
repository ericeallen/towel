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

"""The state and cross-cutting operations the engine's mixins rely on.

``UnificationRefactorEngine`` is assembled from mixins, one per
responsibility (block analysis, pair evaluation, placement, reuse,
insertion, annotation wiring, materialization, clustering, parallel
evaluation, the fixed-point drivers). Each mixin is a class in its own
module and reaches the rest of the engine only through the attributes and
methods declared here, so a reader of one module sees exactly what it
depends on, and mypy checks that the engine provides it. The engine's
constructor assigns every attribute; each method stub names the class that
implements it.
"""

from __future__ import annotations

import ast
from collections import OrderedDict
from typing import (
    Any,
    Callable,
    Dict,
    FrozenSet,
    Iterable,
    List,
    Literal,
    MutableMapping,
    Optional,
    Sequence,
    Set,
    Tuple,
)
from weakref import WeakKeyDictionary

from ..diagnostics import Settings
from ..type_inference import TypeOracle
from .block_signature import DEFAULT_SIMILARITY_THRESHOLD, BlockSignature
from .extractor import HygienicExtractor
from .function_index import FunctionIndex
from .models import (
    AppliedChange,
    BlockBindingSnapshot,
    ClassInfo,
    ClassInsertionPlan,
    CodeBlockPair,
    FunctionArtifact,
    FunctionNode,
    HelperTemplate,
    MethodInfo,
    RefactoringProposal,
    RejectReason,
    Replacement,
    ReusedFunction,
)
from .scope_analyzer import ScopeAnalyzer
from .substitution import Substitution
from .structural_memo import StoredSubstitution
from .unifier import Unifier
from .progress import DEFAULT_PROGRESS, ProgressBarFactory, ProgressMode
from .semantic_safety import ImportGraphCache


class EngineState:
    """Attributes and operations shared across the engine's mixins (declarations only)."""

    _source_lines_cache: Dict[str, Tuple[Tuple[int, int], Tuple[str, ...]]]
    """Lines of files the apply path read, keyed by path, with the stat they were read at."""

    import_graph: ImportGraphCache
    """What this run has learned about the project's import graph."""

    _helper_name_counters: Dict[str, int]
    """Next helper number per file, so generated names are unique across a run."""

    incremental_global_passes: bool
    """Whether later global passes re-pair only the files rewritten since the last one."""

    _settings: Settings
    """What Towel read from the environment at construction."""

    min_lines: int
    """Minimum source lines a duplicated block must span."""

    extractor: HygienicExtractor
    """Renders helpers and call sites."""

    _cluster_cache: "OrderedDict[Tuple[Any, ...], Optional[ast.AST]]"
    """Memo of the per-candidate clustering pipeline."""

    skip_trivial_helpers: bool
    """Whether a helper that only forwards, renames, or unpacks is declined."""

    reuse_existing_functions: bool
    """Whether a whole-body duplicate calls the function it already is."""

    annotate_helpers: bool
    """Whether helpers carry the annotations their call sites declare."""

    _change_log: List[AppliedChange]
    """Every call site rewritten so far in the current run."""

    snippet_formatter: Optional[Callable[[str], str]]
    """Formats each inserted snippet, or None to insert the rendering as is."""

    file_finisher: Optional[Callable[[str, str], str]]
    """Finishes each modified file (imports sorted), or None."""

    prefer_absolute_imports: Optional[bool]
    """Cross-file helper import style; None lets the discovered layout decide."""

    pep420_namespace_packages: Optional[bool]
    """Whether directories without __init__.py are packages; None infers it."""

    @staticmethod
    def _block_line_span(block: Sequence[ast.stmt]) -> Optional[Tuple[int, int]]:
        """The (start_line, end_line) of a contiguous block; provided by InsertionPoints."""
        raise NotImplementedError

    type_oracle: Optional[TypeOracle]
    # The unifier every pair is matched with; its options are fixed at
    # construction.
    unifier: Unifier
    # Every function of the current analysis, keyed by its node, to the file
    # it was parsed from.
    _function_paths: Dict[FunctionNode, str]
    # Memoization caches keyed by the identity of AST nodes parsed for this
    # engine run; the weak ones vanish with their trees.
    _assignment_cache: WeakKeyDictionary[ast.AST, Dict[int, bool]]
    _used_names_cache: WeakKeyDictionary[ast.AST, FrozenSet[str]]
    _value_producing_cache: WeakKeyDictionary[ast.AST, Dict[int, bool]]
    _signed_block_cache: WeakKeyDictionary[
        FunctionNode, List[Tuple[Tuple[int, int], List[ast.AST], BlockSignature]]
    ]
    # The index of the current analysis's functions, keyed by the list it
    # was built from; see ``_function_index``.
    _function_index_cache: Optional[Tuple[Sequence[FunctionArtifact], FunctionIndex]]
    # Bounded, path-registered caches: guards per (guard, function, block),
    # unification results per block-structure pair, and the per-block analyses.
    _block_guard_cache: "OrderedDict[Tuple[Any, ...], bool]"
    _unify_cache: "OrderedDict[Tuple[str, str], Optional[StoredSubstitution]]"
    _per_block_cache: "OrderedDict[Tuple[str, str, str], Any]"
    """The project's type checker, when one is installed and wanted."""

    def _get_indent(self, line: str) -> str:
        """The indentation of a line; provided by InsertionPoints."""
        raise NotImplementedError

    @classmethod
    def placeable_after(cls, source: str) -> Set[str]:
        """Module-level definitions a helper may follow; provided by InsertionPoints."""
        raise NotImplementedError

    def _allocate_helper_name(
        self,
        file_path: str,
        *,
        class_context: bool = False,
        related_paths: Sequence[str] = (),
    ) -> str:
        """Provided by UnificationRefactorEngine."""
        raise NotImplementedError

    @staticmethod
    def _annotation_names(helper: ast.FunctionDef) -> Set[str]:
        """Provided by HelperAnnotationWiring."""
        raise NotImplementedError

    def _checks_generated_types(self, proposal: RefactoringProposal) -> bool:
        """Provided by HelperAnnotationWiring."""
        raise NotImplementedError

    def _find_class_insert_position(
        self, source: str, class_name: str
    ) -> Optional[Tuple[int, str]]:
        """Provided by InsertionPoints."""
        raise NotImplementedError

    def _find_function_insert_position_before_body_statements(
        self, source: str, function_name: str
    ) -> Optional[Tuple[int, str]]:
        """Provided by InsertionPoints."""
        raise NotImplementedError

    def _find_import_position(self, lines: List[str]) -> int:
        """Provided by InsertionPoints."""
        raise NotImplementedError

    def _find_insert_position(
        self, lines: List[str], after_names: Optional[Set[str]] = None
    ) -> int:
        """Provided by InsertionPoints."""
        raise NotImplementedError

    def _infer_helper_annotations(self, proposal: RefactoringProposal) -> None:
        """Provided by HelperAnnotationWiring."""
        raise NotImplementedError

    def _introduces_type_errors(self, modified_files: Dict[str, str]) -> bool:
        """Provided by HelperAnnotationWiring."""
        raise NotImplementedError

    def _prepare_extracted_method_signature(
        self,
        fn: ast.FunctionDef,
        method_kind: Literal["instance", "classmethod", "staticmethod"],
        implicit_param: Optional[str],
    ) -> None:
        """Provided by HelperPlacement."""
        raise NotImplementedError

    @staticmethod
    def _retarget_helper_calls(node: ast.AST, original_name: str, final_name: str) -> ast.AST:
        """Provided by HelperPlacement."""
        raise NotImplementedError

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
        """Provided by HelperPlacement."""
        raise NotImplementedError

    def _source_lines(self, file_path: str) -> Sequence[str]:
        """Provided by InsertionPoints."""
        raise NotImplementedError

    def _verify_reused_function_calls(
        self,
        modified_files: Dict[str, str],
        target: ReusedFunction,
        proposal: RefactoringProposal,
    ) -> None:
        """Provided by ExistingFunctionReuse."""
        raise NotImplementedError

    @staticmethod
    def _with_every_annotation_any(proposal: RefactoringProposal) -> RefactoringProposal:
        """Provided by HelperAnnotationWiring."""
        raise NotImplementedError

    @staticmethod
    def _without_annotations(proposal: RefactoringProposal) -> RefactoringProposal:
        """Provided by HelperAnnotationWiring."""
        raise NotImplementedError

    def _find_python_files(self, directory: str, recursive: bool = True) -> List[str]:
        """Provided by UnificationRefactorEngine."""
        raise NotImplementedError

    def analyze_directory(
        self,
        directory: str,
        recursive: bool = True,
        *,
        verbose: bool = False,
        progress: ProgressMode = DEFAULT_PROGRESS,
        changed_files: Optional[FrozenSet[str]] = None,
    ) -> List[RefactoringProposal]:
        """Provided by UnificationRefactorEngine."""
        raise NotImplementedError

    def analyze_files(
        self,
        file_paths: List[str],
        *,
        verbose: bool = False,
        progress: ProgressMode = DEFAULT_PROGRESS,
        invalidate_paths: Optional[List[str]] = None,
        changed_files: Optional[FrozenSet[str]] = None,
    ) -> List[RefactoringProposal]:
        """Provided by UnificationRefactorEngine."""
        raise NotImplementedError

    def apply_refactoring_multi_file(self, proposal: RefactoringProposal) -> Dict[str, str]:
        """Provided by Materialization."""
        raise NotImplementedError

    def apply_refactoring(self, file_path: str, proposal: RefactoringProposal) -> str:
        """Provided by Materialization."""
        raise NotImplementedError

    def invalidate_paths(self, paths: List[str]) -> None:
        """Provided by UnificationRefactorEngine."""
        raise NotImplementedError

    @staticmethod
    def _finish_inline_status(enabled: bool) -> None:
        """Provided by FixedPointDrivers."""
        raise NotImplementedError

    def _resolve_progress_backend(
        self, progress: ProgressMode
    ) -> Tuple[ProgressMode, Optional[ProgressBarFactory], bool]:
        """Provided by FixedPointDrivers."""
        raise NotImplementedError

    @staticmethod
    def _start_inline_status(label: str, enabled: bool) -> None:
        """Provided by FixedPointDrivers."""
        raise NotImplementedError

    def _try_refactor_pair_multi_file(
        self,
        pair: CodeBlockPair,
        all_functions: Sequence[FunctionArtifact],
        class_infos: List[ClassInfo],
    ) -> Optional[RefactoringProposal]:
        """Provided by PairEvaluation."""
        raise NotImplementedError

    @classmethod
    def _update_inline_status(
        cls, label: str, pct: int, *, bar_len: int = 24, suffix: str = ""
    ) -> None:
        """Provided by FixedPointDrivers."""
        raise NotImplementedError

    def _block_rejected(
        self,
        guard: Callable[..., bool],
        nodes: Sequence[ast.AST],
        func: Optional[FunctionNode] = None,
        analyzer: Optional[ScopeAnalyzer] = None,
        path: Optional[str] = None,
    ) -> bool:
        """Provided by BlockAnalysis."""
        raise NotImplementedError

    @staticmethod
    def _bounded_put(cache: "OrderedDict[Any, Any]", key: Any, value: Any) -> None:
        """Provided by UnificationRefactorEngine."""
        raise NotImplementedError

    def _build_block_binding_snapshot(
        self,
        func: FunctionNode,
        block_nodes: List[ast.AST],
        block_range: Tuple[int, int],
        reassignments: Dict[int, bool],
    ) -> BlockBindingSnapshot:
        """Provided by BlockAnalysis."""
        raise NotImplementedError

    def _get_assignment_reuse(self, func: FunctionNode) -> Dict[int, bool]:
        """Provided by BlockAnalysis."""
        raise NotImplementedError

    def _get_block_indices(
        self, function: FunctionNode, block_nodes: List[ast.AST]
    ) -> Optional[Tuple[int, int]]:
        """Provided by InsertionPoints."""
        raise NotImplementedError

    def _get_method_context(
        self, func: Optional[FunctionNode], class_name: Optional[str]
    ) -> MethodInfo:
        """Provided by HelperPlacement."""
        raise NotImplementedError

    def _get_used_names(self, node: ast.AST) -> Set[str]:
        """Provided by BlockAnalysis."""
        raise NotImplementedError

    @staticmethod
    def _method_class(
        func: Optional[FunctionNode],
        class_name: Optional[str],
        analyzer: Optional[ScopeAnalyzer],
    ) -> Optional[str]:
        """Provided by HelperPlacement."""
        raise NotImplementedError

    def _module_digest(self, func: Optional[FunctionNode]) -> Optional[str]:
        """Provided by UnificationRefactorEngine."""
        raise NotImplementedError

    def _per_block(
        self,
        name: str,
        func: FunctionNode,
        block_nodes: Sequence[ast.AST],
        compute: Callable[[], Any],
    ) -> Any:
        """Provided by BlockAnalysis."""
        raise NotImplementedError

    def _sid(self, nodes: Sequence[ast.AST]) -> str:
        """Provided by UnificationRefactorEngine."""
        raise NotImplementedError

    def _signed_blocks(
        self, function: FunctionNode
    ) -> List[Tuple[Tuple[int, int], List[ast.AST], BlockSignature]]:
        """Provided by BlockAnalysis."""
        raise NotImplementedError

    def _unify_memoized(
        self,
        blocks: List[List[ast.AST]],
        hygienic_renames: List[Dict[str, str]],
        paths: Sequence[Optional[str]] = (),
    ) -> Optional[Substitution]:
        """Provided by BlockAnalysis."""
        raise NotImplementedError

    def _add_clustered_replacements(
        self,
        template: "HelperTemplate",
        dce_node: Optional[FunctionNode],
        functions: FunctionIndex,
        replacements: List[Replacement],
        cluster_contexts: Dict[int, Tuple[Optional[str], Optional[str], Optional[str], bool]],
    ) -> None:
        """Provided by Clustering."""
        raise NotImplementedError

    def _are_structurally_similar(
        self,
        block1: List[ast.AST],
        block2: List[ast.AST],
        threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    ) -> bool:
        """Provided by Clustering."""
        raise NotImplementedError

    def _choose_class_insertion(
        self,
        pair: CodeBlockPair,
        method_info1: MethodInfo,
        method_info2: MethodInfo,
        class_infos: List[ClassInfo],
    ) -> Optional[ClassInsertionPlan]:
        """Provided by HelperPlacement."""
        raise NotImplementedError

    def _debug_reject(
        self, reason: RejectReason, pair: "CodeBlockPair", detail: Optional[str] = None
    ) -> None:
        """Provided by UnificationRefactorEngine."""
        raise NotImplementedError

    def _declares_nonlocal(
        self, func: Optional[FunctionNode], scope_analyzer: Optional[ScopeAnalyzer]
    ) -> bool:
        """Provided by HelperPlacement."""
        raise NotImplementedError

    def _deepest_common_ancestry(
        self, anc1: Optional[List[str]], anc2: Optional[List[str]]
    ) -> Optional[str]:
        """Provided by BlockAnalysis."""
        raise NotImplementedError

    @staticmethod
    def _enclosing_function_named(
        name: str,
        file_path: str,
        functions: FunctionIndex,
        inner: Sequence[FunctionNode],
    ) -> Optional[FunctionNode]:
        """Provided by BlockAnalysis."""
        raise NotImplementedError

    def _find_return_variables(
        self,
        func: FunctionNode,
        block_range: Tuple[int, int],
        initially_bound: Set[str],
        *,
        debug_label: Optional[str] = None,
    ) -> Set[str]:
        """Provided by BlockAnalysis."""
        raise NotImplementedError

    def _global_nonlocal_declarations(
        self,
        pair: CodeBlockPair,
        scope_analyzer: ScopeAnalyzer,
        free_vars: Set[str],
    ) -> Tuple[Set[str], Set[str], Set[str]]:
        """Provided by BlockAnalysis."""
        raise NotImplementedError

    @staticmethod
    def _helper_is_trivial_forwarding(func: ast.FunctionDef) -> bool:
        """Provided by BlockAnalysis."""
        raise NotImplementedError

    def _is_value_producing(self, block: Sequence[ast.AST]) -> bool:
        """Provided by BlockAnalysis."""
        raise NotImplementedError

    def _redirect_to_existing_function(
        self,
        proposal: RefactoringProposal,
        functions: FunctionIndex,
    ) -> Optional[RefactoringProposal]:
        """Provided by ExistingFunctionReuse."""
        raise NotImplementedError

    def _rejects_module_data_lookup(
        self,
        pair: CodeBlockPair,
        analyzer1: Optional[ScopeAnalyzer],
        analyzer2: Optional[ScopeAnalyzer],
    ) -> bool:
        """Provided by BlockAnalysis."""
        raise NotImplementedError

    @staticmethod
    def _reserve_augassign_params(pair: CodeBlockPair, substitution: Substitution) -> Set[str]:
        """Provided by BlockAnalysis."""
        raise NotImplementedError

    @staticmethod
    def _strip_fstring_params(substitution: Substitution) -> None:
        """Provided by BlockAnalysis."""
        raise NotImplementedError

    def _with_helper_annotations(
        self, proposal: RefactoringProposal, functions: FunctionIndex
    ) -> RefactoringProposal:
        """Provided by HelperAnnotationWiring."""
        raise NotImplementedError

    @staticmethod
    def _working_free_vars(
        substitution: Substitution, aug_assign_vars: Set[str], free_vars1: Set[str]
    ) -> Set[str]:
        """Provided by BlockAnalysis."""
        raise NotImplementedError

    def _remember(
        self, paths: Iterable[Optional[str]], cache: MutableMapping[Any, Any], key: Any
    ) -> None:
        """Provided by UnificationRefactorEngine."""
        raise NotImplementedError

    def _function_index(self, all_functions: Sequence[FunctionArtifact]) -> FunctionIndex:
        """Provided by UnificationRefactorEngine."""
        raise NotImplementedError
