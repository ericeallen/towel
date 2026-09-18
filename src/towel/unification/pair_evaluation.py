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

"""Deciding one candidate pair: from two blocks to a verified proposal, or a reason.

The decision runs as a sequence of stages, each returning a typed result or
None for a rejection that was already traced through ``_debug_reject``:

1. guards on the blocks themselves (frames, rebound externals, closures);
2. binding analysis of each block within its function, and the variables
   later code reads that the helper must return;
3. the shape check: both blocks value-producing or neither, complete return
   coverage, not a trivial ``return name``, structurally similar;
4. unification, and alignment of the returned variables across the blocks;
5. where the helper will be visible from, for hygienic naming;
6. the helper's free variables and their lifetimes, declarations, thunks;
7. rendering the helper;
8. the orphan check on what the blocks leave behind;
9. the call sites, each verified by instantiation, plus clustered sites;
10. placement: function, class, or module, and a host that closes no cycle;
11. the proposal, the trivial-helper filter, reuse, and annotations.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Literal, Optional, Sequence, Set, Tuple, cast

from ..diagnostics import VALIDATION, debugging
from .definite_assignment import definitely_bound_after
from .assignment_analyzer import has_reassignments_without_bindings
from .definite_assignment import definitely_bound_before, locally_bound_names
from .semantic_safety import defer_impure_parameters, has_impure_eager_parameters
from .engine_state import EngineState
from .extractor import UnsupportedExtraction, has_complete_return_coverage
from .function_index import FunctionIndex
from .instantiation import instantiation_mismatch
from .models import (
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
)
from .orphan_detector import orphaned_variables
from .parameters import fresh_parameter_name
from .scope_analyzer import Scope, ScopeAnalyzer
from .semantic_safety import (
    moves_scope_declaration,
    nested_bindings_escape,
    nested_scopes_cross_block_boundary,
    requires_original_frame,
    snapshots_rebound_external_names,
    unbinds_external_name,
    uses_class_private_names,
    layout_is_known,
    would_create_import_cycle,
)
from .thunk_inlining import inline_leading_thunks
from .substitution import Substitution
from .visitors import body_without_docstring

_CALL_ARGUMENT_BUILTINS = frozenset(
    {
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
)
"""Builtins a generated call may name without them being bound at the site."""

MethodKind = Literal["instance", "classmethod", "staticmethod"]


@dataclass(frozen=True)
class _PairSetup:
    """Stage 1: the pair's functions, analyzers, and method contexts, once the block guards pass."""

    ctx: "_PairContext"
    method_info1: MethodInfo
    method_info2: MethodInfo


@dataclass(frozen=True)
class _BindingAnalysis:
    """Stage 2: what each block binds and reads, and the variables it must return."""

    snapshot1: BlockBindingSnapshot
    snapshot2: BlockBindingSnapshot
    return_variables1: Set[str]
    return_variables2: Set[str]


@dataclass(frozen=True)
class _Unified:
    """Stage 4: the substitution and the returned variables in one shared order."""

    substitution: Substitution
    hygienic_renames: List[Dict[str, str]]
    ordered_return_variables: Tuple[List[str], List[str]]


@dataclass(frozen=True)
class _HelperScope:
    """Stage 5: the deepest common enclosing function, if unique, and the names visible there."""

    dce_insert_func: Optional[str]
    dce_node: Optional[FunctionNode]
    enclosing_names: Set[str]


@dataclass(frozen=True)
class _FreeVariables:
    """Stage 6: the helper's parameters-to-be and the declarations it must carry."""

    free_vars: Set[str]
    free_vars1: Set[str]
    free_vars2: Set[str]
    globals_to_declare: Set[str]
    nonlocals_to_declare: Set[str]


@dataclass(frozen=True)
class _RenderedHelper:
    """Stage 7: the helper definition and how its parameters are ordered."""

    func_def: ast.FunctionDef
    param_order: Dict[str, int]
    preamble_length: int


@dataclass(frozen=True)
class _CallSites:
    """Stage 9: the generated calls, and the method context of each clustered one."""

    replacements: List[Replacement]
    cluster_contexts: Dict[int, Tuple[Optional[str], Optional[str], Optional[str], bool]]


@dataclass(frozen=True)
class _Placement:
    """Stage 10: where the helper goes and which call sites survive."""

    canonical_file: str
    insert_into_class: Optional[str]
    insert_into_function: Optional[str]
    method_kind: Optional[MethodKind]
    method_param_name: Optional[str]
    replacements: List[Replacement]


def _is_trivial_return_of_bound_name(
    block_nodes: Sequence[ast.AST], bound_before_block: Set[str], bound_in_block: Set[str]
) -> bool:
    """A one-statement block that only returns a name bound before it."""
    if len(block_nodes) != 1:
        return False
    stmt = block_nodes[0]
    return (
        isinstance(stmt, ast.Return)
        and isinstance(stmt.value, ast.Name)
        and stmt.value.id in bound_before_block
        and stmt.value.id not in bound_in_block
    )


@dataclass(frozen=True)
class _PairContext:
    """Each block's function, scope analyzer and root scope, and the pair's structural ids."""

    func1: FunctionNode
    func2: FunctionNode
    scope_analyzer: ScopeAnalyzer
    scope_analyzer2: ScopeAnalyzer
    root_scope: Scope
    # Structural ids of the functions and blocks, computed once: every guard
    # and per-block analysis of the pair is memoized under them.
    function1_id: str
    function2_id: str
    block1_id: str
    block2_id: str


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
    blocks: Sequence[Tuple[FunctionNode, Sequence[ast.AST]]],
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
    substitution: Substitution, blocks: Sequence[Tuple[FunctionNode, Sequence[ast.AST]]]
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


class PairEvaluation(EngineState):
    """From a candidate pair to a verified proposal; see the module docstring."""

    def _reject(
        self,
        pair: CodeBlockPair,
        reason: RejectReason,
        detail: Optional[str] = None,
        trace: Optional[str] = None,
    ) -> None:
        """Decline ``pair``: note ``trace`` in the validation log and ``reason`` in the rejection log."""
        if trace is not None and debugging(VALIDATION):
            VALIDATION.debug(trace)
        self._debug_reject(reason, pair, detail)
        return None

    def _try_refactor_pair_multi_file(
        self,
        pair: CodeBlockPair,
        all_functions: Sequence[FunctionArtifact],
        class_infos: List[ClassInfo],
    ) -> Optional[RefactoringProposal]:
        """The proposal for ``pair``, or None with the reason traced (cross-file aware)."""
        functions = self._function_index(all_functions)
        setup = self._guard_pair(pair, functions)
        if setup is None:
            return None
        ctx = setup.ctx
        debug_enabled = debugging(VALIDATION)
        if debug_enabled:
            VALIDATION.debug("\n=== Finding Functions ===")
            VALIDATION.debug(f"Looking for: {pair.function1_name} and {pair.function2_name}")

        analysis = self._analyze_bindings(pair, ctx)
        if analysis is None:
            return None
        value_producing = self._check_shape(pair, analysis)
        if value_producing is None:
            return None
        unified = self._unify_pair(pair, analysis)
        if unified is None:
            return None
        scope = self._helper_scope(pair, ctx, functions)
        free = self._free_variables(pair, ctx, analysis, unified)
        if free is None:
            return None
        rendered = self._render_helper(pair, ctx, unified, scope, free, value_producing)
        if rendered is None:
            return None
        if self._leaves_orphans(pair, functions, unified):
            return None
        sites = self._call_sites(
            pair, setup, analysis, unified, scope, free, rendered, value_producing, functions
        )
        if sites is None:
            return None
        placement = self._place_helper(pair, setup, scope, sites, functions, class_infos)
        if placement is None:
            return None
        return self._finish_proposal(pair, unified, rendered, placement, functions)

    # -- 1 ---------------------------------------------------------------------

    def _guard_pair(self, pair: CodeBlockPair, functions: FunctionIndex) -> Optional[_PairSetup]:
        """Reject a pair whose blocks cannot move at all; otherwise resolve their context."""
        ctx = self._resolve_pair_context(pair, functions)
        if self._block_rejected(
            requires_original_frame, pair.block1_nodes, path=pair.file_path, block_id=ctx.block1_id
        ) or self._block_rejected(
            requires_original_frame,
            pair.block2_nodes,
            path=pair.file_path2,
            block_id=ctx.block2_id,
        ):
            self._debug_reject(RejectReason.FRAME_SENSITIVE_BLOCK, pair)
            return None

        if debugging(VALIDATION):
            VALIDATION.debug("\n=== _try_refactor_pair_multi_file called ===")
            VALIDATION.debug(f"Functions: {pair.function1_name} and {pair.function2_name}")
            VALIDATION.debug(f"Block1 range: {pair.block1_range}")
            VALIDATION.debug(f"Block2 range: {pair.block2_range}")

        func1, func2 = ctx.func1, ctx.func2
        scope_analyzer, scope_analyzer2 = ctx.scope_analyzer, ctx.scope_analyzer2

        if (
            func1 is not None
            and scope_analyzer is not None
            and self._block_rejected(
                snapshots_rebound_external_names,
                pair.block1_nodes,
                func1,
                scope_analyzer,
                function_id=ctx.function1_id,
                block_id=ctx.block1_id,
            )
        ) or (
            func2 is not None
            and scope_analyzer2 is not None
            and self._block_rejected(
                snapshots_rebound_external_names,
                pair.block2_nodes,
                func2,
                scope_analyzer2,
                function_id=ctx.function2_id,
                block_id=ctx.block2_id,
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
            if (
                func1 is not None
                and self._block_rejected(
                    guard,
                    pair.block1_nodes,
                    func1,
                    function_id=ctx.function1_id,
                    block_id=ctx.block1_id,
                )
            ) or (
                func2 is not None
                and self._block_rejected(
                    guard,
                    pair.block2_nodes,
                    func2,
                    function_id=ctx.function2_id,
                    block_id=ctx.block2_id,
                )
            ):
                self._debug_reject(reason, pair)
                return None
        return _PairSetup(ctx=ctx, method_info1=method_info1, method_info2=method_info2)

    # -- 2 ---------------------------------------------------------------------

    def _analyze_bindings(
        self, pair: CodeBlockPair, ctx: "_PairContext"
    ) -> Optional[_BindingAnalysis]:
        """Each block's bindings within its function, and the variables it must return.

        A block that reassigns a name it did not bind (``result = result + 10``
        with ``result`` bound outside) cannot move; nor can one that unbinds a
        name bound before it. Without both functions there is nothing to
        analyze and the snapshots stay empty.
        """
        func1, func2 = ctx.func1, ctx.func2
        debug_enabled = debugging(VALIDATION)
        reassignments1 = self._get_assignment_reuse(func1)
        reassignments2 = self._get_assignment_reuse(func2)

        has_unsafe1, problematic_vars1 = self._per_block(
            "reassignments",
            func1,
            pair.block1_nodes,
            lambda: has_reassignments_without_bindings(func1, pair.block1_nodes, reassignments1),
            function_id=ctx.function1_id,
            block_id=ctx.block1_id,
        )
        if has_unsafe1:
            self._debug_reject(
                RejectReason.UNSAFE_REASSIGNMENT_BLOCK1, pair, str(problematic_vars1)
            )
            return None
        has_unsafe2, problematic_vars2 = self._per_block(
            "reassignments",
            func2,
            pair.block2_nodes,
            lambda: has_reassignments_without_bindings(func2, pair.block2_nodes, reassignments2),
            function_id=ctx.function2_id,
            block_id=ctx.block2_id,
        )
        if has_unsafe2:
            self._debug_reject(
                RejectReason.UNSAFE_REASSIGNMENT_BLOCK2, pair, str(problematic_vars2)
            )
            return None

        snapshot1 = self._build_block_binding_snapshot(
            func1,
            pair.block1_nodes,
            pair.block1_range,
            reassignments1,
            function_id=ctx.function1_id,
            block_id=ctx.block1_id,
        )
        snapshot2 = self._build_block_binding_snapshot(
            func2,
            pair.block2_nodes,
            pair.block2_range,
            reassignments2,
            function_id=ctx.function2_id,
            block_id=ctx.block2_id,
        )
        if self._per_block(
            "unbinds",
            func1,
            pair.block1_nodes,
            lambda: unbinds_external_name(func1, pair.block1_nodes, snapshot1.bound_before_block),
            function_id=ctx.function1_id,
            block_id=ctx.block1_id,
        ) or self._per_block(
            "unbinds",
            func2,
            pair.block2_nodes,
            lambda: unbinds_external_name(func2, pair.block2_nodes, snapshot2.bound_before_block),
            function_id=ctx.function2_id,
            block_id=ctx.block2_id,
        ):
            self._debug_reject(RejectReason.UNBINDS_EXTERNAL_NAME, pair)
            return None

        if debug_enabled:
            VALIDATION.debug("\n=== Block1 Validation Debug ===")
            VALIDATION.debug(f"Function: {pair.function1_name}")
            VALIDATION.debug(f"Block lines: {pair.block1_range}")
            VALIDATION.debug(f"Bound in block: {snapshot1.bound_in_block}")
            VALIDATION.debug(f"Bound before block: {snapshot1.bound_before_block}")
            VALIDATION.debug(f"Newly bound in block: {snapshot1.initially_bound}")
        return_variables1 = self._find_return_variables(
            func1,
            pair.block1_range,
            snapshot1.initially_bound,
            debug_label="Block1 Validation Debug" if debug_enabled else None,
        )
        if debug_enabled:
            VALIDATION.debug("\n=== Block2 Validation Debug ===")
            VALIDATION.debug(f"Function: {pair.function2_name}")
            VALIDATION.debug(f"Block lines: {pair.block2_range}")
            VALIDATION.debug(f"Bound in block: {snapshot2.bound_in_block}")
            VALIDATION.debug(f"Bound before block: {snapshot2.bound_before_block}")
            VALIDATION.debug(f"Newly bound in block: {snapshot2.initially_bound}")
        return_variables2 = self._find_return_variables(
            func2,
            pair.block2_range,
            snapshot2.initially_bound,
            debug_label="Block2 Validation Debug" if debug_enabled else None,
        )
        return _BindingAnalysis(snapshot1, snapshot2, return_variables1, return_variables2)

    # -- 3 ---------------------------------------------------------------------

    def _check_shape(self, pair: CodeBlockPair, analysis: _BindingAnalysis) -> Optional[bool]:
        """Whether the blocks produce a value, or None when their shapes disagree.

        Blocks with returned variables count as value-producing, since the
        helper will return them. A naturally value-producing block must
        return on every path, so no partial control flow is extracted.
        """
        debug_enabled = debugging(VALIDATION)
        value_prod1 = self._is_value_producing(pair.block1_nodes) or bool(
            analysis.return_variables1
        )
        value_prod2 = self._is_value_producing(pair.block2_nodes) or bool(
            analysis.return_variables2
        )
        if debug_enabled:
            VALIDATION.debug(f"  Value-producing check: block1={value_prod1}, block2={value_prod2}")
            if analysis.return_variables1:
                VALIDATION.debug(f"  Block1 has return_variables: {analysis.return_variables1}")
            if analysis.return_variables2:
                VALIDATION.debug(f"  Block2 has return_variables: {analysis.return_variables2}")
        if value_prod1 != value_prod2:
            self._reject(
                pair,
                RejectReason.VALUE_PRODUCING_MISMATCH,
                trace="  REJECTED: Value-producing mismatch",
            )
            return None
        if value_prod1 and not analysis.return_variables1:
            if not has_complete_return_coverage(pair.block1_nodes):
                self._reject(
                    pair,
                    RejectReason.INCOMPLETE_RETURN_COVERAGE_BLOCK1,
                    trace="  REJECTED: Block1 missing complete return coverage",
                )
                return None
            if not has_complete_return_coverage(pair.block2_nodes):
                self._reject(
                    pair,
                    RejectReason.INCOMPLETE_RETURN_COVERAGE_BLOCK2,
                    trace="  REJECTED: Block2 missing complete return coverage",
                )
                return None
        if _is_trivial_return_of_bound_name(
            pair.block1_nodes,
            analysis.snapshot1.bound_before_block,
            analysis.snapshot1.bound_in_block,
        ) and _is_trivial_return_of_bound_name(
            pair.block2_nodes,
            analysis.snapshot2.bound_before_block,
            analysis.snapshot2.bound_in_block,
        ):
            self._reject(
                pair,
                RejectReason.TRIVIAL_RETURN_BLOCKS,
                trace="  REJECTED: Trivial single-line return blocks (prefer extracting computation)",
            )
            return None
        if not self._are_structurally_similar(pair.block1_nodes, pair.block2_nodes):
            self._reject(
                pair,
                RejectReason.NOT_STRUCTURALLY_SIMILAR,
                trace="  REJECTED: Not structurally similar",
            )
            return None
        return value_prod1

    # -- 4 ---------------------------------------------------------------------

    def _unify_pair(self, pair: CodeBlockPair, analysis: _BindingAnalysis) -> Optional[_Unified]:
        """Anti-unify the blocks and align the variables each must return."""
        debug_enabled = debugging(VALIDATION)
        blocks = [pair.block1_nodes, pair.block2_nodes]
        hygienic_renames: List[Dict[str, str]] = [{}, {}]
        if debug_enabled:
            VALIDATION.debug("  Attempting unification...")
        substitution = self._unify_memoized(
            blocks, hygienic_renames, (pair.file_path, pair.file_path2)
        )
        if not substitution:
            self._reject(
                pair,
                RejectReason.UNIFICATION_FAILED,
                trace="  REJECTED: Unification failed (no substitution)",
            )
            return None
        if debug_enabled:
            VALIDATION.debug("  ✓ Unification successful")
            VALIDATION.debug(f"  Substitution: {substitution}")
        aligned = _align_return_variables(
            analysis.return_variables1,
            analysis.return_variables2,
            analysis.snapshot1.bound_in_block,
            analysis.snapshot2.bound_in_block,
            hygienic_renames,
        )
        if aligned is None:
            self._debug_reject(RejectReason.RETURN_VARIABLES_NOT_ALIGNED, pair)
            return None
        if aligned[0] and (
            self._is_value_producing(pair.block1_nodes)
            or self._is_value_producing(pair.block2_nodes)
        ):
            # A call statement is either `x = helper()` or `return helper()`;
            # a block that both returns early and binds live variables needs both.
            self._debug_reject(RejectReason.MIXED_RETURN_AND_VARIABLES, pair)
            return None
        return _Unified(substitution, hygienic_renames, aligned)

    # -- 5 ---------------------------------------------------------------------

    def _helper_scope(
        self, pair: CodeBlockPair, ctx: "_PairContext", functions: FunctionIndex
    ) -> _HelperScope:
        """The function the helper may go into, and the names it must not shadow there."""
        dce_insert_func: Optional[str] = None
        dce_node: Optional[FunctionNode] = None
        same_file = not pair.is_cross_file
        if same_file:
            dce_insert_func = self._deepest_common_ancestry(
                pair.function1_ancestry, pair.function2_ancestry
            )
            # Ancestry is a list of names, and methods of different classes
            # share names (prompt_toolkit: two ``_all_children``). The helper
            # may go into a function only when exactly one function of that
            # name encloses both blocks' functions.
            if dce_insert_func:
                dce_node = self._enclosing_function_named(
                    dce_insert_func, pair.file_path, functions, (ctx.func1, ctx.func2)
                )
                if dce_node is None:
                    dce_insert_func = None

        enclosing_names = set(ctx.root_scope.bindings.keys())
        if dce_insert_func:
            for artifact in functions.named(pair.file_path2, dce_insert_func)[:1]:
                func_scope = artifact.scope_analyzer.node_scopes.get(artifact.node)
                if func_scope is not None:
                    enclosing_names.update(func_scope.bindings.keys())
        # The same enrichment, keyed on the first file: kept as it always was, so
        # the hygiene set is identical to the original computation.
        target_insert_fn = (
            self._deepest_common_ancestry(pair.function1_ancestry, pair.function2_ancestry)
            if same_file
            else None
        )
        if target_insert_fn:
            for artifact in functions.named(pair.file_path, target_insert_fn)[:1]:
                func_scope = artifact.scope_analyzer.node_scopes.get(artifact.node)
                if func_scope is not None:
                    enclosing_names.update(func_scope.bindings.keys())
        return _HelperScope(dce_insert_func, dce_node, enclosing_names)

    # -- 6 ---------------------------------------------------------------------

    def _free_variables(
        self,
        pair: CodeBlockPair,
        ctx: "_PairContext",
        analysis: _BindingAnalysis,
        unified: _Unified,
    ) -> Optional[_FreeVariables]:
        """The helper's free variables, checked for lifetime, declared, and thunked as needed."""
        debug_enabled = debugging(VALIDATION)
        scope_analyzer, scope_analyzer2 = ctx.scope_analyzer, ctx.scope_analyzer2
        free_vars1 = (
            scope_analyzer.get_free_variables(pair.block1_nodes) if scope_analyzer else set()
        )
        free_vars2 = (
            scope_analyzer2.get_free_variables(pair.block2_nodes) if scope_analyzer2 else set()
        )
        # The helper ends with ``return (v, ...)``. A variable bound only on some
        # path through the block, such as inside a branch that raises, is unbound
        # there unless it entered as a parameter; the original block left the
        # caller's binding untouched on that path instead of raising.
        for index, (nodes, entering) in enumerate(
            ((pair.block1_nodes, free_vars1), (pair.block2_nodes, free_vars2))
        ):
            bound_at_exit = definitely_bound_after(nodes)
            if bound_at_exit is None:
                continue
            not_definite = [
                name
                for name in unified.ordered_return_variables[index]
                if name not in entering and name not in bound_at_exit
            ]
            if not_definite:
                self._debug_reject(RejectReason.CONDITIONALLY_BOUND_RETURN, pair, str(not_definite))
                return None
        # A free variable bound after the block would be read before it is defined.
        if free_vars1 & analysis.snapshot1.bound_after_block:
            incomplete_vars = free_vars1 & analysis.snapshot1.bound_after_block
            if debug_enabled:
                VALIDATION.debug(
                    f"  REJECTED: Block1 uses variables defined AFTER the block: {incomplete_vars}"
                )
                VALIDATION.debug("    These variables would be used before they're defined")
            self._debug_reject(RejectReason.INCOMPLETE_LIFETIME_BLOCK1, pair, str(incomplete_vars))
            return None
        if free_vars2 & analysis.snapshot2.bound_after_block:
            incomplete_vars = free_vars2 & analysis.snapshot2.bound_after_block
            if debug_enabled:
                VALIDATION.debug(
                    f"  REJECTED: Block2 uses variables defined AFTER the block: {incomplete_vars}"
                )
            self._debug_reject(RejectReason.INCOMPLETE_LIFETIME_BLOCK2, pair, str(incomplete_vars))
            return None

        substitution = unified.substitution
        aug_assign_vars = self._reserve_augassign_params(pair, substitution)
        self._strip_fstring_params(substitution)
        free_vars = self._working_free_vars(substitution, aug_assign_vars, free_vars1)
        if self._rejects_module_data_lookup(pair, pair.scope_analyzer1, pair.scope_analyzer2):
            return None
        # A parameter cannot also be declared global or nonlocal in the helper.
        globals_to_declare, nonlocals_to_declare, free_vars = self._global_nonlocal_declarations(
            pair, scope_analyzer, free_vars
        )
        defer_impure_parameters(substitution, pair.block1_nodes)
        free_vars = _thunk_uncertain_free_variables(
            substitution,
            free_vars,
            ((ctx.func1, pair.block1_nodes), (ctx.func2, pair.block2_nodes)),
            unified.hygienic_renames,
        )
        return _FreeVariables(
            free_vars, free_vars1, free_vars2, globals_to_declare, nonlocals_to_declare
        )

    # -- 7 ---------------------------------------------------------------------

    def _render_helper(
        self,
        pair: CodeBlockPair,
        ctx: "_PairContext",
        unified: _Unified,
        scope: _HelperScope,
        free: _FreeVariables,
        value_producing: bool,
    ) -> Optional[_RenderedHelper]:
        """The helper definition, with leading thunks inlined where nothing can observe it."""
        try:
            func_def, param_order = self.extractor.extract_function(
                template_block=pair.block1_nodes,
                substitution=unified.substitution,
                free_variables=free.free_vars,
                enclosing_names=scope.enclosing_names,
                is_value_producing=value_producing,
                return_variables=list(unified.ordered_return_variables[0]),
                global_decls=free.globals_to_declare if free.globals_to_declare else None,
                nonlocal_decls=free.nonlocals_to_declare if free.nonlocals_to_declare else None,
                # Use hygienic double-underscore name; engine will prefix underscore for methods.
                function_name="__extracted_func",
            )
        except UnsupportedExtraction:
            return None
        inline_leading_thunks(func_def, unified.substitution, param_order)
        if has_impure_eager_parameters(unified.substitution):
            self._debug_reject(RejectReason.IMPURE_EAGER_PARAMETER, pair)
            return None
        preamble_length = int(bool(free.globals_to_declare)) + int(bool(free.nonlocals_to_declare))
        return _RenderedHelper(func_def, param_order, preamble_length)

    # -- 8 ---------------------------------------------------------------------

    def _leaves_orphans(
        self, pair: CodeBlockPair, functions: FunctionIndex, unified: _Unified
    ) -> bool:
        """Whether moving the blocks would leave a later read without its binding.

        A name the helper returns is rebound by the generated call on every
        path out of the block, so a later read of it is not orphaned.
        """
        # The last function of each name, as the original list scan resolved it.
        found1 = functions.named(pair.file_path, pair.function1_name)
        found2 = functions.named(pair.file_path2, pair.function2_name)
        if not (found1 and found2):
            return False
        func1, func2 = found1[-1].node, found2[-1].node
        indices1 = self._get_block_indices(func1, pair.block1_nodes)
        indices2 = self._get_block_indices(func2, pair.block2_nodes)
        if not (indices1 and indices2):
            return False
        body1 = body_without_docstring(func1.body)
        body2 = body_without_docstring(func2.body)
        orphans1 = orphaned_variables(body1, indices1)
        orphans2 = orphaned_variables(body2, indices2)
        orphans1 -= set(unified.ordered_return_variables[0])
        orphans2 -= set(unified.ordered_return_variables[1])
        if orphans1 or orphans2:
            self._debug_reject(
                RejectReason.ORPHANED_VARIABLES, pair, detail=str(sorted(orphans1 | orphans2))
            )
            return True
        return False

    # -- 9 ---------------------------------------------------------------------

    def _call_sites(
        self,
        pair: CodeBlockPair,
        setup: _PairSetup,
        analysis: _BindingAnalysis,
        unified: _Unified,
        scope: _HelperScope,
        free: _FreeVariables,
        rendered: _RenderedHelper,
        value_producing: bool,
        functions: FunctionIndex,
    ) -> Optional[_CallSites]:
        """The generated call for each block, verified by instantiation, plus clustered sites."""
        replacements: List[Replacement] = []
        # Method context of each clustered call site, by index in ``replacements``
        cluster_contexts: Dict[int, Tuple[Optional[str], Optional[str], Optional[str], bool]] = {}
        return_vars_by_block = {
            0: list(unified.ordered_return_variables[0]),
            1: list(unified.ordered_return_variables[1]),
        }
        func_def = rendered.func_def
        for block_idx, (block_range, file_path) in enumerate(
            [
                (pair.block1_range, pair.file_path),
                (pair.block2_range, pair.file_path2),
            ]
        ):
            try:
                call_node = self.extractor.generate_call(
                    function_name=func_def.name,
                    block_idx=block_idx,
                    substitution=unified.substitution,
                    param_order=rendered.param_order,
                    free_variables=free.free_vars,
                    is_value_producing=value_producing,
                    return_variables=return_vars_by_block[block_idx],
                    hygienic_renames=unified.hygienic_renames,
                )
            except UnsupportedExtraction:
                return None
            # The call may name only what is bound before the block, the free
            # variables, and a few builtins; a leaked placeholder or an invented
            # local would be an undefined name at the site.
            if block_idx == 0:
                allowed_before = set(analysis.snapshot1.bound_before_block) | set(free.free_vars1)
            else:
                allowed_before = set(analysis.snapshot2.bound_before_block) | set(free.free_vars2)
            invalid_names = {
                name
                for name in self._get_used_names(call_node)
                if name != func_def.name
                and (
                    name.startswith("__param_")
                    or (name not in allowed_before and name not in _CALL_ARGUMENT_BUILTINS)
                )
            }
            if invalid_names:
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
                unified.hygienic_renames[0],
                unified.hygienic_renames[block_idx],
                preamble_length=rendered.preamble_length,
                returns_variables=bool(unified.ordered_return_variables[0]),
            )
            if mismatch is not None:
                self._debug_reject(
                    RejectReason.INSTANTIATION_MISMATCH,
                    pair,
                    detail=f"block{block_idx+1}: {mismatch}",
                )
                return None
            class_name = pair.class1_name if block_idx == 0 else pair.class2_name
            method_info = setup.method_info1 if block_idx == 0 else setup.method_info2
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

        # Same-file clustering: further identical blocks join this proposal, for
        # non-returning helpers only.
        same_file = not pair.is_cross_file
        if same_file and not analysis.return_variables1 and not analysis.return_variables2:
            self._add_clustered_replacements(
                HelperTemplate(
                    pair=pair,
                    func_def=func_def,
                    func_def_dump=ast.dump(func_def),
                    param_order=rendered.param_order,
                    preamble_length=rendered.preamble_length,
                    free_vars=free.free_vars,
                    enclosing_names=scope.enclosing_names,
                    is_value_producing=value_producing,
                    globals_to_declare=free.globals_to_declare,
                    nonlocals_to_declare=free.nonlocals_to_declare,
                ),
                scope.dce_node,
                functions,
                replacements,
                cluster_contexts,
            )
        return _CallSites(replacements, cluster_contexts)

    # -- 10 --------------------------------------------------------------------

    def _place_helper(
        self,
        pair: CodeBlockPair,
        setup: _PairSetup,
        scope: _HelperScope,
        sites: _CallSites,
        functions: FunctionIndex,
        class_infos: List[ClassInfo],
    ) -> Optional[_Placement]:
        """Where the helper lives: a unique enclosing function, a class, or a module that closes no cycle."""
        ctx = setup.ctx
        replacements, cluster_contexts = sites.replacements, sites.cluster_contexts
        canonical_file = pair.file_path
        insert_into_class: Optional[str] = None
        insert_into_function: Optional[str] = None
        method_kind_metadata: Optional[MethodKind] = None
        method_param_name: Optional[str] = None
        same_file = not pair.is_cross_file

        if same_file and scope.dce_insert_func:
            # The helper is placed textually by function name (FuncLocator),
            # so the name must identify one function in the file. When two
            # classes have a same-named method (tornado: several
            # ``get_handlers``), the deepest common enclosing function is a
            # real, unique node, but the name alone would resolve to the
            # wrong one and the call sites would not see the helper. Every
            # free variable is already a parameter, so a module-level helper
            # is equally correct; fall back to it when the name is ambiguous.
            if len(functions.named(canonical_file, scope.dce_insert_func)) == 1:
                insert_into_function = scope.dce_insert_func

        class_plan: Optional[ClassInsertionPlan] = None
        if insert_into_function is None:
            class_plan = self._choose_class_insertion(
                pair, setup.method_info1, setup.method_info2, class_infos
            )
        if class_plan is not None:
            # Both blocks in one class: insert there. Different classes with a
            # common ancestor that is neither: insert into the ancestor. A plan
            # naming one concrete sibling would hide the helper from the other,
            # so that falls back to module level.
            same_class = pair.class1_name is not None and pair.class1_name == pair.class2_name
            target_is_concrete_sibling = (
                class_plan.class_name in {pair.class1_name, pair.class2_name} and not same_class
            )
            if not target_is_concrete_sibling:
                insert_into_class = class_plan.class_name
                canonical_file = class_plan.file_path
                method_kind_metadata = class_plan.method_kind
                method_param_name = class_plan.implicit_param

        if insert_into_class is not None and cluster_contexts:
            # The helper is a method called through the receiver. A clustered
            # block in another class, in a module-level function, or in a
            # function merely nested in a method has no such receiver, so it
            # keeps its code (pyflakes: sibling TestCase classes).
            info = setup.method_info1
            expected = (info.kind, info.implicit_param, info.receiver_known)
            replacements = [
                replacement
                for index, replacement in enumerate(replacements)
                if index not in cluster_contexts
                or (
                    cluster_contexts[index][0] in {pair.class1_name, pair.class2_name}
                    and cluster_contexts[index][1:] == expected
                )
            ]

        # Closures with nonlocal variables are left alone.
        if self._declares_nonlocal(ctx.func1, ctx.scope_analyzer) or self._declares_nonlocal(
            ctx.func2, ctx.scope_analyzer2
        ):
            self._debug_reject(RejectReason.NONLOCAL_SAFETY_SKIP, pair)
            return None

        participating_paths = {canonical_file} | {
            replacement.file_path or canonical_file for replacement in replacements
        }
        if len(participating_paths) > 1 and any(
            functions.declares_global(path) for path in participating_paths
        ):
            self._debug_reject(RejectReason.CROSS_MODULE_GLOBAL_DECLARATION, pair)
            return None

        # The helper lives in ``canonical_file`` and every other participating
        # module imports it. When that closes an import cycle, a plain
        # module-level helper may move to another participating module that
        # the others already import; only a genuine cycle declines the pair.
        participating = {canonical_file} | {
            replacement.file_path or canonical_file for replacement in replacements
        }
        if len(participating) > 1 and not layout_is_known(canonical_file, self.import_graph):
            self._debug_reject(RejectReason.UNKNOWN_LAYOUT, pair)
            return None
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
                a.class_name for a in functions.named(canonical_file, insert_into_function)
            }
            destination_class = next(iter(destinations)) if len(destinations) == 1 else None
        for replacement in replacements:
            if replacement.class_name and replacement.class_name != destination_class:
                source_path = replacement.file_path or canonical_file
                for a in functions.in_file(source_path):
                    if (
                        a.class_name == replacement.class_name
                        and a.node.lineno <= replacement.line_range[0]
                        and (a.node.end_lineno or a.node.lineno) >= replacement.line_range[1]
                        and uses_class_private_names([a.node])
                    ):
                        self._debug_reject(RejectReason.PRIVATE_NAME_LEXICAL_CLASS, pair)
                        return None
        return _Placement(
            canonical_file,
            insert_into_class,
            insert_into_function,
            method_kind_metadata,
            method_param_name,
            replacements,
        )

    # -- 11 --------------------------------------------------------------------

    def _finish_proposal(
        self,
        pair: CodeBlockPair,
        unified: _Unified,
        rendered: _RenderedHelper,
        placement: _Placement,
        functions: FunctionIndex,
    ) -> Optional[RefactoringProposal]:
        """The proposal, unless the helper only forwards; redirected to an existing function or annotated."""
        is_cross_file = pair.is_cross_file
        desc = f"Extract common code from {pair.function1_name}"
        if is_cross_file:
            desc += f" ({Path(pair.file_path).name}) and {pair.function2_name} ({Path(pair.file_path2).name})"
        else:
            desc += f" and {pair.function2_name}"
        participating_paths = {placement.canonical_file} | {
            replacement.file_path or placement.canonical_file
            for replacement in placement.replacements
        }
        proposal = RefactoringProposal(
            file_path=placement.canonical_file,
            extracted_function=rendered.func_def,
            replacements=placement.replacements,
            description=desc,
            parameters_count=len(unified.substitution.param_expressions),
            return_variables=list(unified.ordered_return_variables[0]),
            insert_into_class=placement.insert_into_class,
            insert_into_function=placement.insert_into_function,
            method_kind=placement.method_kind,
            method_param_name=placement.method_param_name,
            source_digests=tuple(
                sorted(
                    (path, digest)
                    for path in participating_paths
                    if (digest := functions.source_digest(path)) is not None
                )
            ),
        )
        if self.skip_trivial_helpers and self._helper_is_trivial_forwarding(
            proposal.extracted_function
        ):
            self._debug_reject(RejectReason.TRIVIAL_FORWARDING_HELPER, pair)
            return None
        if self.reuse_existing_functions:
            redirected = self._redirect_to_existing_function(proposal, functions)
            if redirected is not None:
                return redirected
        if self.annotate_helpers:
            proposal = self._with_helper_annotations(proposal, functions)
        return proposal

    def _resolve_pair_context(
        self,
        pair: CodeBlockPair,
        functions: FunctionIndex,
    ) -> "_PairContext":
        """The pair's functions, analyzers and root scope, with their structural ids."""
        return _PairContext(
            func1=pair.function1_node,
            func2=pair.function2_node,
            scope_analyzer=pair.scope_analyzer1,
            scope_analyzer2=pair.scope_analyzer2,
            root_scope=pair.root_scope1,
            function1_id=self._sid([pair.function1_node]),
            function2_id=self._sid([pair.function2_node]),
            block1_id=self._sid(pair.block1_nodes),
            block2_id=self._sid(pair.block2_nodes),
        )
