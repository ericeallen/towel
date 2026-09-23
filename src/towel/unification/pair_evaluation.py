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

1. guards on the blocks themselves (frame reads, escaping nested bindings,
   closures crossing the block boundary, moved ``global``/``nonlocal``
   declarations);
2. binding analysis of each block within its function, and the variables
   later code reads that the helper must return;
3. the shape check: both blocks value-producing or neither, complete return
   coverage, not a trivial ``return name``, structurally similar;
4. unification, and alignment of the returned variables across the blocks;
5. where the helper will be visible from, for hygienic naming;
6. the helper's free variables: the shared names a same-file helper reads
   bare because both sites resolve them at module scope, the module-data
   and rebound-external guards on the rest, lifetimes, declarations, thunks;
7. rendering the helper, inlining leading thunks, and declining impure eager
   parameters and helpers that only forward;
8. the orphan check on what the blocks leave behind;
9. the call sites, each verified by instantiation, plus clustered sites;
10. placement: function, class, or module, and a host that closes no cycle;
11. the proposal: the filter that declines reducing an earlier pass's
    helper to a forwarder, and annotations.
"""

from __future__ import annotations

import ast
import builtins
import dataclasses
import os
import symtable
from dataclasses import dataclass
from pathlib import Path
from typing import AbstractSet, Dict, List, Mapping, Optional, Sequence, Set, Tuple, FrozenSet

from ..diagnostics import VALIDATION, debugging
from ..source_text import read_source
from .definite_assignment import definitely_bound_after
from .statement_facts import loaded_names
from .assignment_analyzer import has_reassignments_without_bindings
from .block_analysis import align_return_variables
from .builtins import CALL_ARGUMENT_BUILTINS, is_builtin
from .semantic_safety import (
    available_argument_names,
    module_resolved_names,
    defer_impure_parameters,
    has_impure_eager_parameters,
    thunk_reads_possibly_unbound_local,
)
from .clustering import Clustering
from .reuse import ExistingFunctionReuse
from .annotation_wiring import HelperAnnotationWiring
from .placement import HelperPlacement
from .block_analysis import BlockAnalysis
from .extractor import UnsupportedExtraction, has_complete_return_coverage
from .function_index import FunctionIndex
from .instantiation import instantiation_mismatch
from .narrowing import narrowing_lost_at_call_site
from .models import (
    HelperHome,
    proposal_identity,
    span_contains,
    BlockBindingSnapshot,
    ClassInfo,
    ClusterContext,
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
    block_requires_original_frame,
    frame_read_outside_block,
    unbinds_external_name,
    uses_class_private_names,
)
from .import_graph import (
    ImportChange,
    host_has_stub,
    import_change,
    layout_is_known,
    relative_import_levels,
    relative_imports_resolve_alike,
    would_create_import_cycle,
)
from .thunk_inlining import inline_leading_thunks
from .substitution import Substitution
from .visitors import FreeNameCollector, body_without_docstring


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
    # Per block, the names its call site can resolve; an argument that is a
    # bare name is hoisted only when it is one of these.
    available_names: Tuple[FrozenSet[str], FrozenSet[str]]
    # Shared free names the helper reads as bare references instead of taking
    # as parameters: every read at both sites resolves at module scope (or
    # nowhere), and the helper lives in that module, so the lookup is the same.
    module_names: FrozenSet[str]


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
    cluster_contexts: Dict[int, ClusterContext]


@dataclass(frozen=True)
class _Placement:
    """Stage 10: where the helper goes and which call sites survive."""

    home: HelperHome
    replacements: List[Replacement]


@dataclass(frozen=True)
class _Placed:
    """Stages 4 to 10: the unified pair, its rendered helper, and where it goes."""

    unified: _Unified
    rendered: _RenderedHelper
    placement: _Placement
    # The helper reads module names bare (stage 6) but placement put it in a
    # module other than the one both sites resolved them in.
    reads_bare_elsewhere: bool
    # Names the helper reads bare from its host module's namespace that a site
    # in another module may resolve differently (``_reads_differing_by_module``).
    differing_reads: FrozenSet[str] = frozenset()


# Names every module's namespace defines for itself, each with that module's
# own value; read bare in another module, each is the other module's.
_MODULE_OWN_NAMES = frozenset(
    {
        "__annotations__",
        "__builtins__",
        "__cached__",
        "__doc__",
        "__file__",
        "__loader__",
        "__name__",
        "__package__",
        "__path__",
        "__spec__",
    }
)

# What a bare read finds in the builtins when no namespace between binds it:
# the one lookup that can be the same from every module.
_BUILTIN_NAMES = frozenset(vars(builtins)) - _MODULE_OWN_NAMES


def _module_namespace_names(
    helper: ast.FunctionDef,
) -> Optional[Tuple[FrozenSet[str], FrozenSet[str]]]:
    """The names the helper uses from its module's namespace, and those it declares ``global``.

    CPython's own symbol table answers, so a name read inside a lambda or a
    comprehension of the helper that nothing in the helper binds is among
    them, and a parameter or a local is not. None when the helper does not
    compile to a table.
    """
    try:
        table = symtable.symtable(ast.unparse(helper), "<helper>", "exec")
    except SyntaxError:
        return None
    names: Set[str] = set()
    declared: Set[str] = set()
    pending = list(table.get_children())
    while pending:
        scope = pending.pop()
        for symbol in scope.get_symbols():
            if symbol.is_global():
                names.add(symbol.get_name())
                if symbol.is_declared_global():
                    declared.add(symbol.get_name())
        pending.extend(scope.get_children())
    return frozenset(names), frozenset(declared)


# How a refused host is reported, by what its import would change.
_IMPORT_CHANGE_REASONS = {
    ImportChange.UNKNOWN: RejectReason.IMPORT_TIME_EFFECTS,
    ImportChange.RUNS_CODE: RejectReason.IMPORT_TIME_EFFECTS,
    ImportChange.NEW_REQUIREMENT: RejectReason.NEW_IMPORT_REQUIREMENT,
    ImportChange.NEW_TOP_LEVEL_PACKAGE: RejectReason.NEW_TOP_LEVEL_PACKAGE,
    ImportChange.RUN_BY_PATH: RejectReason.RUN_BY_PATH_IMPORT,
}


def _analyzed_module(analyzer: Optional[ScopeAnalyzer]) -> Optional[ast.Module]:
    """The module ``analyzer`` analyzed, when it analyzed a whole module."""
    tree = analyzer.analyzed_tree if analyzer is not None else None
    return tree if isinstance(tree, ast.Module) else None


def _is_trivial_return_of_bound_name(
    block_nodes: Sequence[ast.stmt], bound_before_block: Set[str], bound_in_block: Set[str]
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


def _is_forwarding_lambda(node: ast.AST) -> bool:
    """Whether ``node`` is the extractor's ``lambda *args, **kwargs: callee(*args, **kwargs)``."""
    return (
        isinstance(node, ast.Lambda)
        and node.args.vararg is not None
        and node.args.kwarg is not None
        and isinstance(node.body, ast.Call)
    )


def _call_site_reads(call: ast.stmt) -> Set[str]:
    """The names the call statement reads at the site; a forwarding lambda's own parameters are not among them."""
    collector = FreeNameCollector()
    collector.visit(call)
    return collector.used


def _thunk_uncertain_free_variables(
    substitution: Substitution,
    free_variables: Set[str],
    blocks: Sequence[Tuple[FunctionNode, Sequence[ast.stmt]]],
    renames: Sequence[Dict[str, str]],
    available: Sequence[AbstractSet[str]],
) -> Set[str]:
    """Pass free variables that the call site may not resolve as thunks.

    A free variable read only on some path inside the block must be read
    where the block read it unless the call site resolves it on every path
    (``available``, see ``available_argument_names``): a local bound only on
    some path before the block, a module name the module binds later or
    nowhere, or a cell of an enclosing function not yet filled would raise
    at the eager call where the block raised only on the path that read it.
    The thunk keeps the timing.
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
        return spelled not in available[index]

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
    substitution: Substitution, blocks: Sequence[Tuple[FunctionNode, Sequence[ast.stmt]]]
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


class PairEvaluation(
    Clustering, HelperPlacement, BlockAnalysis, ExistingFunctionReuse, HelperAnnotationWiring
):
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
        placed = self._unify_and_place(
            pair, setup, analysis, value_producing, functions, class_infos, keep_module_names=True
        )
        if placed is not None and (placed.reads_bare_elsewhere or placed.differing_reads):
            # The names are the same lookup only from the sites' own module;
            # a helper hosted in an ancestor class defined elsewhere would
            # resolve them in that module, and a builtin that one of the
            # modules rebinds is not the same lookup from the others. Decide
            # the pair again with those names parameters, and with every
            # module name one when the helper left the sites' module.
            # Unification runs again because the later stages rewrite the
            # substitution in place.
            placed = self._unify_and_place(
                pair,
                setup,
                analysis,
                value_producing,
                functions,
                class_infos,
                keep_module_names=not placed.reads_bare_elsewhere,
                forced_parameters=placed.differing_reads,
            )
            if placed is not None and (placed.reads_bare_elsewhere or placed.differing_reads):
                self._debug_reject(
                    RejectReason.BARE_NAME_DIFFERS_BY_MODULE,
                    pair,
                    detail=str(sorted(placed.differing_reads)),
                )
                return None
        if placed is None:
            return None
        return self._finish_proposal(
            pair, placed.unified, placed.rendered, placed.placement, functions
        )

    def _unify_and_place(
        self,
        pair: CodeBlockPair,
        setup: _PairSetup,
        analysis: _BindingAnalysis,
        value_producing: bool,
        functions: FunctionIndex,
        class_infos: List[ClassInfo],
        *,
        keep_module_names: bool,
        forced_parameters: FrozenSet[str] = frozenset(),
    ) -> Optional[_Placed]:
        """Stages 4 to 10, with or without reading shared module names bare.

        Every name in ``forced_parameters`` the template reads is a parameter.
        """
        ctx = setup.ctx
        unified = self._unify_pair(pair, analysis)
        if unified is None:
            return None
        scope = self._helper_scope(pair, ctx, functions)
        free = self._free_variables(
            pair,
            ctx,
            analysis,
            unified,
            keep_module_names=keep_module_names,
            forced_parameters=forced_parameters,
        )
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
        elsewhere = bool(free.module_names) and os.path.abspath(
            placement.home.file_path
        ) != os.path.abspath(pair.file_path)
        differing = self._reads_differing_by_module(pair, placement, rendered.func_def, functions)
        if differing is None:
            self._debug_reject(RejectReason.BARE_NAME_DIFFERS_BY_MODULE, pair)
            return None
        return _Placed(unified, rendered, placement, elsewhere, differing)

    def _reads_differing_by_module(
        self,
        pair: CodeBlockPair,
        placement: _Placement,
        helper: ast.FunctionDef,
        functions: FunctionIndex,
    ) -> Optional[FrozenSet[str]]:
        """The names the helper reads bare that a site in another module may not resolve alike.

        A bare read in the helper looks in its host module's namespace, then
        the builtins; the block it replaced looked in its own module's. From
        a site in another module that is the same lookup only for a builtin
        that no participating module may bind (``ModuleBindings.may_bind``)
        and whose namespace no reflection may rebind: a module's own
        function, class or import of that name, a star import, or a
        ``global`` declaration makes the host's answer differ from the
        site's. Anything else read bare, a module name or a dunder every
        module defines for itself, differs too. None when the helper's
        names cannot be listed, or when a differing name is one the helper
        declares ``global`` and so cannot take as a parameter.
        """
        host = placement.home.file_path
        paths = {host} | {replacement.file_path or host for replacement in placement.replacements}
        if len({os.path.abspath(path) for path in paths}) == 1:
            return frozenset()
        found = _module_namespace_names(helper)
        if found is None:
            return None
        names, declared = found
        sources = {pair.file_path: pair.source1, pair.file_path2: pair.source2}
        tables = [self._module_bindings(path, sources) for path in sorted(paths)]
        unreliable = any(table is None for table in tables) or any(
            self._namespace_is_reflective(path, sources, functions) for path in paths
        )
        differing = frozenset(
            name
            for name in names
            if name not in _BUILTIN_NAMES
            or unreliable
            or any(table is not None and table.may_bind(name) for table in tables)
        )
        if differing & declared:
            return None
        return differing

    @staticmethod
    def _namespace_is_reflective(
        path: str, sources: Mapping[str, str], functions: FunctionIndex
    ) -> bool:
        """Whether the module reaches its namespace through reflection (``globals()``, ``exec``, ...).

        Its scope analysis says so (``external_binding_hazards``); a module
        with no analyzed function is analyzed here, and one that cannot be
        read or parsed counts as reflective.
        """
        artifacts = functions.in_file(path)
        analyzer = artifacts[0].scope_analyzer if artifacts else None
        if analyzer is None:
            source = sources.get(path)
            try:
                tree = ast.parse(source if source is not None else read_source(path))
            except (OSError, UnicodeError, ValueError, SyntaxError):
                return True
            analyzer = ScopeAnalyzer()
            analyzer.analyze(tree)
        hazards = analyzer.external_binding_hazards
        return hazards is None or hazards.reflective

    # -- 1 ---------------------------------------------------------------------

    def _guard_pair(self, pair: CodeBlockPair, functions: FunctionIndex) -> Optional[_PairSetup]:
        """Reject a pair whose blocks cannot move at all; otherwise resolve their context."""
        ctx = self._resolve_pair_context(pair, functions)
        blocks = (
            (pair.block1_nodes, ctx.func1, ctx.scope_analyzer, ctx.function1_id, ctx.block1_id),
            (pair.block2_nodes, ctx.func2, ctx.scope_analyzer2, ctx.function2_id, ctx.block2_id),
        )
        for nodes, function, analyzer, function_id, block_id in blocks:
            if self._block_rejected(
                block_requires_original_frame,
                nodes,
                function,
                analyzer,
                function_id=function_id,
                block_id=block_id,
            ):
                self._debug_reject(RejectReason.FRAME_SENSITIVE_BLOCK, pair)
                return None
            if self._block_rejected(
                frame_read_outside_block,
                nodes,
                function,
                analyzer,
                function_id=function_id,
                block_id=block_id,
            ):
                self._debug_reject(RejectReason.FRAME_READ_IN_FUNCTION, pair)
                return None

        if debugging(VALIDATION):
            VALIDATION.debug("\n=== _try_refactor_pair_multi_file called ===")
            VALIDATION.debug(f"Functions: {pair.function1_name} and {pair.function2_name}")
            VALIDATION.debug(f"Block1 range: {pair.block1_range}")
            VALIDATION.debug(f"Block2 range: {pair.block2_range}")

        func1, func2 = ctx.func1, ctx.func2
        scope_analyzer, scope_analyzer2 = ctx.scope_analyzer, ctx.scope_analyzer2

        # A function nested inside a method shares the class for name mangling
        # but has no receiver; only a function defined directly in the class
        # body dispatches as a method.
        method_info1 = self._get_method_context(
            func1,
            self._method_class(func1, pair.class1_name, scope_analyzer),
            _analyzed_module(scope_analyzer),
        )
        method_info2 = self._get_method_context(
            func2,
            self._method_class(func2, pair.class2_name, scope_analyzer2),
            _analyzed_module(scope_analyzer2),
        )

        for guard, reason in (
            (nested_bindings_escape, RejectReason.NESTED_BINDING_ESCAPES),
            (nested_scopes_cross_block_boundary, RejectReason.CLOSURE_CROSSES_BLOCK_BOUNDARY),
            (moves_scope_declaration, RejectReason.MOVES_SCOPE_DECLARATION),
        ):
            if (
                self._block_rejected(
                    guard,
                    pair.block1_nodes,
                    func1,
                    function_id=ctx.function1_id,
                    block_id=ctx.block1_id,
                )
            ) or (
                self._block_rejected(
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
        name bound before it.
        """
        first = self._block_bindings(pair, ctx, 0)
        if first is None:
            return None
        second = self._block_bindings(pair, ctx, 1)
        if second is None:
            return None
        if self._per_block(
            "unbinds",
            ctx.func1,
            pair.block1_nodes,
            lambda: unbinds_external_name(
                ctx.func1, pair.block1_nodes, first[0].bound_before_block
            ),
            function_id=ctx.function1_id,
            block_id=ctx.block1_id,
        ) or self._per_block(
            "unbinds",
            ctx.func2,
            pair.block2_nodes,
            lambda: unbinds_external_name(
                ctx.func2, pair.block2_nodes, second[0].bound_before_block
            ),
            function_id=ctx.function2_id,
            block_id=ctx.block2_id,
        ):
            self._debug_reject(RejectReason.UNBINDS_EXTERNAL_NAME, pair)
            return None
        return _BindingAnalysis(first[0], second[0], first[1], second[1])

    def _block_bindings(
        self, pair: CodeBlockPair, ctx: "_PairContext", block_idx: int
    ) -> Optional[Tuple[BlockBindingSnapshot, Set[str]]]:
        """Block ``block_idx``'s binding snapshot and the variables it must return; None if it reassigns unsafely."""
        if block_idx == 0:
            func, nodes, block_range = ctx.func1, pair.block1_nodes, pair.block1_range
            ids = (ctx.function1_id, ctx.block1_id)
            name, reason = pair.function1_name, RejectReason.UNSAFE_REASSIGNMENT_BLOCK1
        else:
            func, nodes, block_range = ctx.func2, pair.block2_nodes, pair.block2_range
            ids = (ctx.function2_id, ctx.block2_id)
            name, reason = pair.function2_name, RejectReason.UNSAFE_REASSIGNMENT_BLOCK2
        reassignments = self._get_assignment_reuse(func)
        has_unsafe, problematic_vars = self._per_block(
            "reassignments",
            func,
            nodes,
            lambda: has_reassignments_without_bindings(func, nodes, reassignments),
            function_id=ids[0],
            block_id=ids[1],
        )
        if has_unsafe:
            self._debug_reject(reason, pair, str(problematic_vars))
            return None
        snapshot = self._build_block_binding_snapshot(
            func, nodes, block_range, reassignments, function_id=ids[0], block_id=ids[1]
        )
        debug_label = f"Block{block_idx + 1} Validation Debug"
        debug_enabled = debugging(VALIDATION)
        if debug_enabled:
            VALIDATION.debug(f"\n=== {debug_label} ===")
            VALIDATION.debug(f"Function: {name}")
            VALIDATION.debug(f"Block lines: {block_range}")
            VALIDATION.debug(f"Bound in block: {snapshot.bound_in_block}")
            VALIDATION.debug(f"Bound before block: {snapshot.bound_before_block}")
            VALIDATION.debug(f"Newly bound in block: {snapshot.initially_bound}")
        return_variables = self._find_return_variables(
            func,
            block_range,
            snapshot.initially_bound,
            nodes,
            debug_label=debug_label if debug_enabled else None,
        )
        return snapshot, return_variables

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
        substitution = self._unify_memoized(blocks, hygienic_renames)
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
        aligned = align_return_variables(
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
        *,
        keep_module_names: bool = True,
        forced_parameters: FrozenSet[str] = frozenset(),
    ) -> Optional[_FreeVariables]:
        """The helper's free variables, checked for lifetime, declared, and thunked as needed.

        With ``keep_module_names`` false every shared free name is a parameter,
        as for a helper whose module is not the sites' own; a name in
        ``forced_parameters`` that the template reads is one either way.
        """
        debug_enabled = debugging(VALIDATION)
        scope_analyzer, scope_analyzer2 = ctx.scope_analyzer, ctx.scope_analyzer2
        free_vars1 = scope_analyzer.free_variables(pair.block1_nodes) if scope_analyzer else set()
        free_vars2 = scope_analyzer2.free_variables(pair.block2_nodes) if scope_analyzer2 else set()
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
        # The template's free variables come from block 1's module, which
        # keeps a builtin spelling only where it binds the name; block 2's
        # module or function may bind it where block 1's does not. The forced
        # names are ones the rendered helper read bare.
        also_free = set(forced_parameters)
        if pair.is_cross_file:
            also_free |= {
                name for name in free_vars2 - free_vars1 if is_builtin(name)
            } & self._template_reads(pair)
        free_vars1, free_vars2 = free_vars1 | also_free, free_vars2 | also_free
        free_vars = self._working_free_vars(substitution, aug_assign_vars, free_vars1)
        # A parameter cannot also be declared global or nonlocal in the helper.
        globals_to_declare, nonlocals_to_declare, free_vars = self._global_nonlocal_declarations(
            pair, scope_analyzer, free_vars
        )
        module_names = (
            self._names_kept_free(pair, ctx, free_vars - forced_parameters)
            if keep_module_names
            else frozenset()
        )
        free_vars -= module_names
        if self._rejects_module_data_lookup(
            pair, pair.scope_analyzer1, pair.scope_analyzer2, deferred=module_names
        ):
            return None
        # An external name another function may rebind is snapshotted by the
        # call unless the helper reads it bare.
        if any(
            self._rebound_external_names(func, nodes, analyzer, function_id, block_id)
            - module_names
            for func, nodes, analyzer, function_id, block_id in (
                (ctx.func1, pair.block1_nodes, scope_analyzer, ctx.function1_id, ctx.block1_id),
                (ctx.func2, pair.block2_nodes, scope_analyzer2, ctx.function2_id, ctx.block2_id),
            )
        ):
            self._debug_reject(RejectReason.REBOUND_EXTERNAL_BINDING, pair)
            return None
        available = (
            available_argument_names(ctx.func1, pair.block1_nodes, ctx.scope_analyzer),
            available_argument_names(ctx.func2, pair.block2_nodes, ctx.scope_analyzer2),
        )
        defer_impure_parameters(substitution, pair.block1_nodes, available)
        free_vars = _thunk_uncertain_free_variables(
            substitution,
            free_vars,
            ((ctx.func1, pair.block1_nodes), (ctx.func2, pair.block2_nodes)),
            unified.hygienic_renames,
            available,
        )
        return _FreeVariables(
            free_vars,
            free_vars1,
            free_vars2,
            globals_to_declare,
            nonlocals_to_declare,
            available,
            module_names,
        )

    @staticmethod
    def _template_reads(pair: CodeBlockPair) -> FrozenSet[str]:
        """The names the template block reads outside the functions it defines."""
        collector = FreeNameCollector()
        for statement in pair.block1_nodes:
            collector.visit(statement)
        return frozenset(collector.used)

    def _names_kept_free(
        self, pair: CodeBlockPair, ctx: "_PairContext", free_vars: Set[str]
    ) -> FrozenSet[str]:
        """The shared free names a same-file helper reads as bare references.

        A name every read of which, at both sites, the module binds or nothing
        binds is the same lookup from a helper in that module: the helper's
        home encloses both sites (module level, their common function, or
        their class), so no scope between it and the module binds the name,
        and the helper reads it where the block did, as late and as
        conditionally. Passing it instead would snapshot module data at the
        call. Across files the other module's same-named binding may differ,
        so the name stays a parameter there.
        """
        if pair.is_cross_file or not free_vars:
            return frozenset()
        # A shared free name is spelled the same at both sites: hygienic
        # renaming touches only the names a block binds.
        kept = module_resolved_names(ctx.func1, ctx.scope_analyzer, free_vars)
        return module_resolved_names(ctx.func2, ctx.scope_analyzer2, kept)

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
                enclosing_names=scope.enclosing_names | free.module_names,
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
        if has_impure_eager_parameters(unified.substitution, free.available_names):
            self._debug_reject(RejectReason.IMPURE_EAGER_PARAMETER, pair)
            return None
        # A helper that only forwards (a lone raise, a return of one call, a
        # bare call, or an assignment returned as is) adds indirection and
        # shares no logic; decline it here, before the call sites, clustering
        # and placement are worked out for a helper that will be dropped.
        if self.skip_trivial_helpers and self._helper_is_trivial_forwarding(func_def):
            self._debug_reject(RejectReason.TRIVIAL_FORWARDING_HELPER, pair)
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
        for block_idx in (0, 1):
            replacement = self._call_for_block(
                pair, setup, analysis, unified, free, rendered, value_producing, block_idx
            )
            if replacement is None:
                return None
            replacements.append(replacement)
        # Same-file clustering: further identical blocks join this proposal.
        cluster_contexts: Dict[int, ClusterContext] = {}
        if not pair.is_cross_file:
            self._add_clustered_replacements(
                HelperTemplate(
                    pair=pair,
                    func_def=rendered.func_def,
                    func_def_dump=ast.dump(rendered.func_def),
                    param_order=rendered.param_order,
                    preamble_length=rendered.preamble_length,
                    free_vars=free.free_vars,
                    enclosing_names=scope.enclosing_names,
                    is_value_producing=value_producing,
                    globals_to_declare=free.globals_to_declare,
                    nonlocals_to_declare=free.nonlocals_to_declare,
                    # Only the names the template block reads can matter to a
                    # candidate's eager-argument check; the rest would make
                    # the template's key differ per function position.
                    # Only the names the template block reads can matter to a
                    # candidate's eager-argument check; the rest would make
                    # the template's key differ per function position.
                    available_names=free.available_names[0]
                    & set().union(*(loaded_names(node) for node in pair.block1_nodes)),
                    return_variables=tuple(unified.ordered_return_variables[0]),
                    bound_in_block=frozenset(analysis.snapshot1.bound_in_block),
                    module_names=free.module_names,
                ),
                scope.dce_node,
                functions,
                replacements,
                cluster_contexts,
            )
        return _CallSites(replacements, cluster_contexts)

    def _call_for_block(
        self,
        pair: CodeBlockPair,
        setup: _PairSetup,
        analysis: _BindingAnalysis,
        unified: _Unified,
        free: _FreeVariables,
        rendered: _RenderedHelper,
        value_producing: bool,
        block_idx: int,
    ) -> Optional[Replacement]:
        """The call that replaces block ``block_idx``, or None when it cannot be generated soundly.

        The call may name only what is bound before the block, the free
        variables, and a few builtins (a leaked placeholder or an invented
        local would be an undefined name at the site), and instantiating the
        helper with it must give back the block.
        """
        func_def = rendered.func_def
        if block_idx == 0:
            block_range, file_path, nodes = pair.block1_range, pair.file_path, pair.block1_nodes
            snapshot, free_here = analysis.snapshot1, free.free_vars1
            class_name, method_info = pair.class1_name, setup.method_info1
        else:
            block_range, file_path, nodes = pair.block2_range, pair.file_path2, pair.block2_nodes
            snapshot, free_here = analysis.snapshot2, free.free_vars2
            class_name, method_info = pair.class2_name, setup.method_info2
        try:
            call_node = self.extractor.generate_call(
                function_name=func_def.name,
                block_idx=block_idx,
                substitution=unified.substitution,
                param_order=rendered.param_order,
                free_variables=free.free_vars,
                is_value_producing=value_producing,
                return_variables=list(unified.ordered_return_variables[block_idx]),
                hygienic_renames=unified.hygienic_renames,
            )
        except UnsupportedExtraction:
            return None
        if any(_is_forwarding_lambda(node) for node in ast.walk(call_node)):
            # ``lambda *args, **kwargs: callee(*args, **kwargs)`` keeps the
            # callee's timing but re-evaluates its expression on every call
            # and reads worse than the duplication it removes. Declined; a
            # callee the site resolves is passed as a thunk or inlined.
            self._debug_reject(RejectReason.FORWARDED_CALLEE, pair, detail=f"block{block_idx+1}")
            return None
        if thunk_reads_possibly_unbound_local(
            call_node,
            setup.ctx.func1 if block_idx == 0 else setup.ctx.func2,
            free.available_names[block_idx],
        ):
            # See :func:`thunk_reads_possibly_unbound_local`.
            self._debug_reject(
                RejectReason.THUNK_OF_POSSIBLY_UNBOUND_LOCAL, pair, detail=f"block{block_idx+1}"
            )
            return None
        allowed_before = set(snapshot.bound_before_block) | set(free_here)
        invalid_names = {
            name
            for name in _call_site_reads(call_node)
            if name != func_def.name
            and (
                name.startswith("__param_")
                or (name not in allowed_before and name not in CALL_ARGUMENT_BUILTINS)
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
            nodes,
            unified.hygienic_renames[0],
            unified.hygienic_renames[block_idx],
            preamble_length=rendered.preamble_length,
            returns_variables=bool(unified.ordered_return_variables[0]),
        )
        if mismatch is not None:
            self._debug_reject(
                RejectReason.INSTANTIATION_MISMATCH, pair, detail=f"block{block_idx+1}: {mismatch}"
            )
            return None
        return Replacement(
            line_range=block_range,
            node=call_node,
            file_path=file_path,
            class_name=class_name,
            method_kind=method_info.kind,
            implicit_param=method_info.implicit_param,
        )

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
        home = self._helper_home(pair, setup, scope, functions, class_infos)
        replacements = sites.replacements
        if home.insert_into_class is not None and sites.cluster_contexts:
            # The helper is a method called through the receiver. A clustered
            # block in another class, in a module-level function, or in a
            # function merely nested in a method has no such receiver, so it
            # keeps its code (pyflakes: sibling TestCase classes).
            replacements = [
                replacement
                for index, replacement in enumerate(replacements)
                if index not in sites.cluster_contexts
                or (
                    sites.cluster_contexts[index].class_name in {pair.class1_name, pair.class2_name}
                    and sites.cluster_contexts[index].method == setup.method_info1
                )
            ]
        # Closures with nonlocal variables are left alone.
        if self._declares_nonlocal(ctx.func1, ctx.scope_analyzer) or self._declares_nonlocal(
            ctx.func2, ctx.scope_analyzer2
        ):
            self._debug_reject(RejectReason.NONLOCAL_SAFETY_SKIP, pair)
            return None
        canonical_file = self._safe_home_across_modules(pair, home, replacements, functions)
        if canonical_file is None:
            return None
        destination_class = home.insert_into_class
        if home.insert_into_function:
            destinations = {
                a.class_name for a in functions.named(canonical_file, home.insert_into_function)
            }
            destination_class = next(iter(destinations)) if len(destinations) == 1 else None
        for replacement in replacements:
            if replacement.class_name and replacement.class_name != destination_class:
                source_path = replacement.file_path or canonical_file
                for a in functions.in_file(source_path):
                    if (
                        a.class_name == replacement.class_name
                        and span_contains(a.node, replacement.line_range)
                        and uses_class_private_names([a.node])
                    ):
                        self._debug_reject(RejectReason.PRIVATE_NAME_LEXICAL_CLASS, pair)
                        return None
        # The cross-module check may move the helper to another file.
        return _Placement(dataclasses.replace(home, file_path=canonical_file), replacements)

    def _helper_home(
        self,
        pair: CodeBlockPair,
        setup: _PairSetup,
        scope: _HelperScope,
        functions: FunctionIndex,
        class_infos: List[ClassInfo],
    ) -> "HelperHome":
        """The function or class the helper goes into, if any; otherwise the pair's own module."""
        canonical_file = pair.file_path
        if not pair.is_cross_file and scope.dce_insert_func:
            # The helper is placed textually by function name (FuncLocator),
            # so the name must identify one function in the file. When two
            # classes have a same-named method (tornado: several
            # ``get_handlers``), the deepest common enclosing function is a
            # real, unique node, but the name alone would resolve to the
            # wrong one and the call sites would not see the helper. Every
            # free variable is already a parameter, so a module-level helper
            # is equally correct; fall back to it when the name is ambiguous.
            if len(functions.named(canonical_file, scope.dce_insert_func)) == 1:
                return HelperHome(canonical_file, None, scope.dce_insert_func, None, None)
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
                return HelperHome(
                    class_plan.file_path,
                    class_plan.class_name,
                    None,
                    class_plan.method_kind,
                    class_plan.implicit_param,
                )
        return HelperHome(canonical_file, None, None, None, None)

    def _safe_home_across_modules(
        self,
        pair: CodeBlockPair,
        home: "HelperHome",
        replacements: Sequence[Replacement],
        functions: FunctionIndex,
    ) -> Optional[str]:
        """The module the helper is defined in once every participating module can import it.

        A helper shared across modules is refused when a participating module
        declares a global, when a relative import the helper runs would name
        a different module from some participating module
        (``relative_imports_resolve_alike``), when the project's layout is
        unknown, or when the import would close a cycle that no other
        participating module can host instead.
        """
        canonical_file = home.file_path
        participating = {canonical_file} | {
            replacement.file_path or canonical_file for replacement in replacements
        }
        if len(participating) == 1:
            return canonical_file
        if any(functions.declares_global(path) for path in participating):
            self._debug_reject(RejectReason.CROSS_MODULE_GLOBAL_DECLARATION, pair)
            return None
        # The helper runs the template's imports in whichever module hosts
        # it, and every participating module is a candidate host, so each
        # must resolve them alike.
        if not relative_imports_resolve_alike(
            participating, relative_import_levels(pair.block1_nodes)
        ):
            self._debug_reject(RejectReason.RELATIVE_IMPORT_ACROSS_PACKAGES, pair)
            return None
        if not layout_is_known(canonical_file, self.import_graph):
            self._debug_reject(RejectReason.UNKNOWN_LAYOUT, pair)
            return None
        # The helper lives in ``canonical_file`` and every other participating
        # module imports it. When that closes an import cycle, a plain
        # module-level helper may move to another participating module that
        # the others already import; only a genuine cycle declines the pair.
        candidates = [canonical_file]
        if home.insert_into_class is None and home.insert_into_function is None:
            candidates += sorted(participating - {canonical_file})
        refusal: Optional[RejectReason] = None
        for candidate in candidates:
            if would_create_import_cycle(candidate, participating, self.import_graph):
                continue
            # The other modules would import the helper through the host's
            # stub, which a type checker reads in its place and lacks it.
            if host_has_stub(candidate, self.import_graph):
                refusal = refusal or RejectReason.HOST_HAS_STUB
                continue
            # The new import must not change what importing a borrower does: a
            # host that prints or registers at import time would do so wherever
            # the borrower is imported, one that needs an absent package or one
            # that does not ship with the borrower would stop its import.
            changes = [
                change
                for borrower in sorted(participating - {candidate})
                if (change := import_change(candidate, borrower, self.import_graph)) is not None
            ]
            if changes:
                refusal = refusal or _IMPORT_CHANGE_REASONS[changes[0]]
                continue
            return candidate
        self._debug_reject(refusal or RejectReason.IMPORT_CYCLE, pair)
        return None

    # -- 11 --------------------------------------------------------------------

    def _finish_proposal(
        self,
        pair: CodeBlockPair,
        unified: _Unified,
        rendered: _RenderedHelper,
        placement: _Placement,
        functions: FunctionIndex,
    ) -> Optional[RefactoringProposal]:
        """The proposal, annotated as configured.

        A site that is the whole body of an existing function is never
        redirected to call another existing function: that call would look
        the other up in its module at every call, so patching or rebinding it
        would change this one too. Declined when a site is the whole body of
        a helper an earlier pass inserted while another site is not a whole
        body (``_helper_reduced_to_forwarder``): the helper would keep only
        the new call, one more layer of indirection with no logic of its own.
        """
        is_cross_file = pair.is_cross_file
        desc = f"Extract common code from {pair.function1_name}"
        if is_cross_file:
            desc += f" ({Path(pair.file_path).name}) and {pair.function2_name} ({Path(pair.file_path2).name})"
        else:
            desc += f" and {pair.function2_name}"
        home = placement.home
        participating_paths = {home.file_path} | {
            replacement.file_path or home.file_path for replacement in placement.replacements
        }
        proposal = RefactoringProposal(
            file_path=home.file_path,
            extracted_function=rendered.func_def,
            replacements=placement.replacements,
            description=desc,
            parameters_count=len(unified.substitution.param_expressions),
            return_variables=list(unified.ordered_return_variables[0]),
            insert_into_class=home.insert_into_class,
            insert_into_function=home.insert_into_function,
            method_kind=home.method_kind,
            method_param_name=home.method_param_name,
            source_digests=tuple(
                sorted(
                    (path, digest)
                    for path in participating_paths
                    if (digest := functions.source_digest(path)) is not None
                )
            ),
        )
        separated = narrowing_lost_at_call_site(rendered.func_def, placement.replacements)
        if separated is not None:
            # Both halves are well typed where they were written and the pair
            # is not; no signature on the helper can repair it. Refusing here
            # spares a whole-project check per candidate signature.
            self._debug_reject(RejectReason.NARROWING_LOST_AT_CALL_SITE, pair, detail=separated)
            return None
        identity = proposal_identity(proposal)
        if identity in self._seen_proposals:
            # The same helper over the same sites, found through another pair:
            # nothing the rest of this stage computes would differ.
            self._debug_reject(RejectReason.DUPLICATE_PROPOSAL, pair)
            return None
        self._seen_proposals.add(identity)
        if self.skip_trivial_helpers:
            forwarder = self._helper_reduced_to_forwarder(proposal, functions)
            if forwarder is not None:
                self._debug_reject(
                    RejectReason.EXISTING_HELPER_BECOMES_FORWARDER, pair, detail=forwarder
                )
                return None
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
