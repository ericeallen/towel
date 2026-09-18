"""Conservative guards for extractions that change execution context."""

from __future__ import annotations

import ast
from typing import (
    AbstractSet,
    TYPE_CHECKING,
    FrozenSet,
    Iterable,
    Sequence,
    Set,
    Tuple,
    Union,
)
from weakref import WeakKeyDictionary

from .binding_detector import BindingDetector
from .builtins import is_builtin
from .definite_assignment import definitely_bound_before
from .models import FunctionNode
from .bounded_cache import BoundedCache
from .scope_analyzer import ScopeAnalyzer
from .statement_facts import (
    bindings_of,
    loaded_names,
    memoized_per_node,
)
from .structural_memo import structural_id
from .visitors import OwnScopeVisitor

# The import-graph half lives in import_graph.py; the tests still import it here.
from .import_graph import (  # noqa: F401
    ImportGraphCache,
    imported_definition_sites,
    would_create_import_cycle,
)

if TYPE_CHECKING:
    from .substitution import Substitution


_PRIVATE_NAME_USE: BoundedCache[str, bool] = BoundedCache(65_536)
"""Whether nodes use a class-private name, by structural id.

Asked of every function that hosts a clustered site, once per proposal
that clusters into it; the answer depends only on the names the nodes
spell. Per process; the workers fork after parsing and each keeps its own
copy.
"""


def uses_class_private_names(nodes: Iterable[ast.AST]) -> bool:
    """Whether moving these nodes to a different class changes name mangling."""
    block = tuple(nodes)
    key = structural_id(block)
    cached = _PRIVATE_NAME_USE.get(key)
    if cached is None:
        cached = _PRIVATE_NAME_USE.put(key, _uses_class_private_names(block))
    return cached


def _uses_class_private_names(nodes: Sequence[ast.AST]) -> bool:
    for statement in nodes:
        for node in ast.walk(statement):
            name = (
                node.id
                if isinstance(node, ast.Name)
                else (node.attr if isinstance(node, ast.Attribute) else "")
            )
            if name.startswith("__") and not name.endswith("__"):
                return True
    return False


def nested_bindings_escape(function: FunctionNode, nodes: Iterable[ast.AST]) -> bool:
    """Reject nested extraction whose local writes are observable outside it.

    The engine's return-variable analysis covers top-level statement slices.
    A loop/branch body needs control-flow liveness, including reads on the next
    loop iteration. Until that analysis exists, require its bindings to remain
    entirely inside the extracted block. Attribute/subscript mutations do not
    rebind their base objects and are not counted as local writes.
    """
    block = tuple(nodes)
    if all(node in function.body for node in block):
        return False
    extracted = {child for statement in block for child in ast.walk(statement)}
    detector = BindingDetector()
    for statement in block:
        detector.visit(statement)
    bound = {binding.name for binding in detector.bindings}
    # Deletion changes the original local binding too, and is not a binding
    # construct reported by BindingDetector. Store contexts also conservatively
    # include declarations that do not initialize a value.
    bound.update(
        node.id
        for node in extracted
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del))
    )
    if not bound:
        return False
    for node in ast.walk(function):
        if node in extracted:
            continue
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, (ast.Load, ast.Del))
            and node.id in bound
        ):
            return True
        if (
            isinstance(node, ast.AugAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id in bound
        ):
            return True
    return False


def snapshots_rebound_external_names(
    analyzer: ScopeAnalyzer,
    function: FunctionNode,
    nodes: Iterable[ast.AST],
) -> bool:
    """Reject snapshots of external bindings with visible rebinding hazards.

    Explicit global/nonlocal declarations identify bindings another function can
    update during the extraction. Namespace reflection makes that identification
    unreliable, so its presence conservatively disqualifies external snapshots.
    Opaque mutation originating outside the analyzed module is not modeled.
    """
    scope = analyzer.node_scopes.get(function)
    root = analyzer.root_scope
    if scope is None or root is None:
        return True
    hazards = analyzer.external_binding_hazards
    if hazards is None:
        return True
    rebound = hazards.rebound
    unresolved = hazards.unresolved_nonlocal
    reflective = hazards.reflective
    for statement in nodes:
        for node in ast.walk(statement):
            if not isinstance(node, ast.Name) or not isinstance(node.ctx, ast.Load):
                continue
            binding = scope.lookup(node.id)
            if binding is None:
                if unresolved or reflective or (root.scope_id, node.id) in rebound:
                    return True
                continue
            # A local captured by a nested function is a mutable cell too.
            # Passing it to the helper snapshots it before that function runs.
            if (binding.scope_id, node.id) in rebound:
                return True
            if binding.scope_id == scope.scope_id:
                continue
            if unresolved or reflective:
                return True
    return False


def _has_comprehension_assignment(nodes: Iterable[ast.AST]) -> bool:
    """Whether a comprehension writes a binding in its containing function.

    This requires return/liveness analysis across the comprehension boundary;
    until that is modeled, such a comprehension must remain in its caller.
    """
    return any(
        isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp))
        and any(isinstance(child, ast.NamedExpr) for child in ast.walk(node))
        for statement in nodes
        for node in ast.walk(statement)
    )


# Attributes and callees whose behavior depends on the call stack, the active
# traceback, or a module's own source text. Moving code into a helper adds a
# frame and shifts line numbers, so a program that reads any of these can
# observe the refactoring even when its result is unchanged. These power the
# pre-run warning; they are names to look for, not a guarantee of breakage.
_FRAME_SENSITIVE_ATTRS = frozenset(
    {
        "f_back",
        "f_locals",
        "f_globals",
        "f_lineno",
        "f_code",
        "tb_frame",
        "tb_next",
        "tb_lineno",
        "__traceback__",
    }
)
_FRAME_SENSITIVE_CALLEES = frozenset(
    {
        "_getframe",
        "currentframe",
        "stack",
        "getouterframes",
        "getframeinfo",
        "extract_stack",
        "print_stack",
        "extract_tb",
        "walk_tb",
        "walk_stack",
        "format_exc",
        "format_stack",
        "print_exc",
    }
)
_SOURCE_OBSERVING_CALLEES = frozenset(
    {"getsource", "getsourcelines", "getsourcefile", "findsource", "getframeinfo"}
)


def frame_sensitivity_markers(source: str) -> FrozenSet[str]:
    """Frame-, traceback-, and source-observing constructs a module contains.

    A module in the returned-empty case is not proof of safety; a callee that
    inspects frames internally is invisible here. This exists to warn a person
    which files to review, not to decide any single extraction.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return frozenset()
    markers: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if node.attr in _FRAME_SENSITIVE_ATTRS:
                markers.add("frame")
            if node.attr == "__traceback__":
                markers.add("traceback")
        elif isinstance(node, ast.Call):
            callee = node.func
            name = callee.attr if isinstance(callee, ast.Attribute) else getattr(callee, "id", "")
            if name == "warn" and any(kw.arg == "stacklevel" for kw in node.keywords):
                markers.add("stacklevel-warning")
            if name in _FRAME_SENSITIVE_CALLEES:
                markers.add("frame")
            if name in _SOURCE_OBSERVING_CALLEES:
                markers.add("source")
    return frozenset(markers)


def is_namespace_access_call(node: ast.AST) -> bool:
    """Whether ``node`` is a call that reads or writes the caller's namespace.

    That is ``locals()``, ``globals()``, ``eval()``, ``exec()``, or the
    no-argument ``vars()`` and ``dir()`` (which read ``locals()``). A local
    variable, parameter, or attribute that merely shares one of these names is
    not such a call, so callers can rely on this to avoid false positives on
    shadowing.
    """
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
        return False
    name = node.func.id
    if name in {"locals", "globals", "eval", "exec"}:
        return True
    return name in {"vars", "dir"} and not node.args and not node.keywords


def requires_original_frame(nodes: Iterable[ast.AST]) -> bool:
    """Reject suspension and operations that inspect the original call frame.

    Generator delegation needs a separate transformation preserving send/throw
    and return values. Moving frame inspection into a helper is not equivalent.
    Unknown shadowing of these call names is deliberately treated conservatively.
    Each statement's verdict is memoized: it is a property of that statement
    alone, and a statement belongs to every block that spans it.
    """
    return any(
        memoized_per_node(_FRAME_SENSITIVE, statement, _statement_requires_original_frame)
        for statement in nodes
    )


_FRAME_SENSITIVE: "WeakKeyDictionary[ast.AST, bool]" = WeakKeyDictionary()


def _statement_requires_original_frame(statement: ast.AST) -> bool:
    block = (statement,)
    if has_external_loop_control(block) or _has_comprehension_assignment(block):
        return True
    for node in ast.walk(statement):
        if isinstance(node, (ast.Yield, ast.YieldFrom, ast.Await, ast.AsyncFor, ast.AsyncWith)):
            return True
        if isinstance(node, ast.Call):
            if is_namespace_access_call(node):
                return True
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "super"
                and not node.args
                and not node.keywords
            ):
                return True
            if _is_frame_relative_call(node):
                return True
    return False


_FRAME_RELATIVE_CALLEES = frozenset(
    {"_getframe", "currentframe", "stack", "getouterframes", "extract_stack", "print_stack"}
)


def _is_frame_relative_call(call: ast.Call) -> bool:
    """Calls whose result depends on how many frames sit above them.

    ``warnings.warn(..., stacklevel=n)`` attributes the warning to the n-th
    caller; a helper adds one frame. Frame and stack inspection is likewise
    relative to the current frame. Only direct, recognizably named calls are
    detected; a callee that inspects frames internally is not.
    """
    callee = call.func
    name = callee.attr if isinstance(callee, ast.Attribute) else getattr(callee, "id", "")
    if name == "warn" and any(keyword.arg == "stacklevel" for keyword in call.keywords):
        return True
    return name in _FRAME_RELATIVE_CALLEES


class _LoopControlVisitor(OwnScopeVisitor):
    def __init__(self) -> None:
        self.depth = 0
        self.external = False

    def visit_Break(self, node: ast.Break) -> None:
        if self.depth == 0:
            self.external = True

    def visit_Continue(self, node: ast.Continue) -> None:
        if self.depth == 0:
            self.external = True

    def _visit_loop(self, node: Union[ast.For, ast.AsyncFor, ast.While]) -> None:
        self.depth += 1
        for statement in node.body:
            self.visit(statement)
        self.depth -= 1
        # A loop's else-suite is outside that loop's break/continue scope.
        for statement in node.orelse:
            self.visit(statement)

    visit_For = _visit_loop
    visit_AsyncFor = _visit_loop
    visit_While = _visit_loop

    def _nested_class(self, node: ast.ClassDef) -> None:
        """A loop inside a nested class body is not the block's loop."""


def has_external_loop_control(nodes: Iterable[ast.AST]) -> bool:
    """Whether break/continue targets a loop outside the extraction boundary."""

    visitor = _LoopControlVisitor()
    for statement in nodes:
        visitor.visit(statement)
    return visitor.external


def bound_names(nodes: Iterable[ast.AST]) -> Set[str]:
    """Every name a node binds or unbinds, including inside nested scopes.

    This over-approximates scope: a nested function's local counts too. The
    guards below use it to decide whether a nested scope and the extracted
    block could share a binding, where over-approximation only rejects.
    """
    names: Set[str] = set()
    for statement in nodes:
        bound: FrozenSet[str] = memoized_per_node(_BOUND_NAMES, statement, _all_bindings_of)
        names.update(bound)
    return names


_BOUND_NAMES: "WeakKeyDictionary[ast.AST, FrozenSet[str]]" = WeakKeyDictionary()


def _all_bindings_of(statement: ast.AST) -> FrozenSet[str]:
    return bindings_of(statement, into_nested_scopes=True)


def _deleted_names(nodes: Iterable[ast.AST]) -> Set[str]:
    """Names a block unbinds: explicit ``del`` and implicit except-clause cleanup."""
    names: Set[str] = set()
    for statement in nodes:
        deleted: FrozenSet[str] = memoized_per_node(
            _DELETED_NAMES, statement, _statement_deleted_names
        )
        names.update(deleted)
    return names


_DELETED_NAMES: "WeakKeyDictionary[ast.AST, FrozenSet[str]]" = WeakKeyDictionary()


def _statement_deleted_names(statement: ast.AST) -> FrozenSet[str]:
    names: Set[str] = set()
    for node in ast.walk(statement):
        if isinstance(node, ast.Delete):
            for target in node.targets:
                names.update(
                    child.id
                    for child in ast.walk(target)
                    if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Del)
                )
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
    return frozenset(names)


def unbinds_external_name(
    function: FunctionNode,
    nodes: Iterable[ast.AST],
    bound_before: Set[str],
) -> bool:
    """Whether the block deletes a binding that outlives it.

    Deleting a helper parameter leaves the caller's variable bound; the
    original raised ``UnboundLocalError`` on the next read. ``except ... as e``
    deletes ``e`` when the handler exits, so it is a deletion too. Global and
    nonlocal names are rejected because the helper holds no such declaration.
    """
    deleted = _deleted_names(nodes)
    if not deleted:
        return False
    declared: Set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            declared.update(node.names)
    return bool(deleted & (bound_before | declared))


_NESTED_SCOPE_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.GeneratorExp)


class _ScopeFacts:
    """Nested scopes and top-level bindings of one function, computed once.

    The closure guard below runs once per candidate block; walking the whole
    function each time made it quadratic in the function's size per block.
    """

    def __init__(self, function: FunctionNode) -> None:
        self.nested: Tuple[Tuple[ast.AST, FrozenSet[str]], ...] = tuple(
            (node, frozenset(loaded_names(node)))
            for node in ast.walk(function)
            if isinstance(node, _NESTED_SCOPE_TYPES) and node is not function
        )
        self.top_level: Tuple[Tuple[ast.stmt, FrozenSet[str]], ...] = tuple(
            (statement, frozenset(bound_names([statement]))) for statement in function.body
        )


_SCOPE_FACTS: "WeakKeyDictionary[ast.AST, _ScopeFacts]" = WeakKeyDictionary()


def _scope_facts(function: FunctionNode) -> _ScopeFacts:
    facts = _SCOPE_FACTS.get(function)
    if facts is None:
        facts = _ScopeFacts(function)
        _SCOPE_FACTS[function] = facts
    return facts


def nested_scopes_cross_block_boundary(function: FunctionNode, nodes: Iterable[ast.AST]) -> bool:
    """Reject extraction when a closure and the block share a mutable binding.

    A nested function, lambda or generator reads its free names when it runs,
    not when it is defined. If it is defined outside the block and the block
    rebinds one of those names, the helper rebinds its own local instead of the
    caller's cell. If it is defined inside the block and the caller rebinds one
    of its free names after the block, the closure keeps the helper's cell
    while the original saw the caller's rebinding; that holds whether the
    block bound the name itself (a loop target the caller assigns again
    later) or read it from the caller. Both cases are rejected; reads of
    names bound only before a top-level block remain eligible.
    """
    block = tuple(nodes)
    extracted = {child for statement in block for child in ast.walk(statement)}
    written_in_block = bound_names(block)
    facts = _scope_facts(function)
    if written_in_block and any(
        loaded & written_in_block for scope, loaded in facts.nested if scope not in extracted
    ):
        return True
    captured: Set[str] = set()
    inside = False
    for scope, loaded in facts.nested:
        if scope in extracted:
            inside = True
            captured.update(loaded)
    if not inside or not captured:
        return False
    top_level = all(node in function.body for node in block)
    block_end = max(getattr(node, "end_lineno", 0) or 0 for node in block)
    for statement, bound in facts.top_level:
        if statement in extracted:
            continue
        if top_level and (getattr(statement, "lineno", 0) or 0) <= block_end:
            continue
        if bound & captured:
            return True
    return False


def is_eagerly_evaluable(expression: ast.AST, available: AbstractSet[str]) -> bool:
    """Whether hoisting this expression to the call site is unobservable.

    A parameter argument runs once, before the block, even when the block would
    have evaluated it later, repeatedly, conditionally, or not at all. Only
    expressions with no effects, no failure modes, and no fresh identity may
    move that way: literals, names the call site can resolve (``available``:
    a builtin, a module-level binding, a name bound on every path before the
    block, or a binding of an enclosing function), and tuples of those. A
    name the site cannot resolve raises NameError when hoisted out of a
    branch the block would not have taken. Attribute access can run a
    property, subscripts and operators can call arbitrary methods, calls are
    effects by definition, and mutable displays allocate.
    """
    if isinstance(expression, ast.Constant):
        return True
    if isinstance(expression, ast.Name):
        return expression.id in available or is_builtin(expression.id)
    # A tuple of such values is immutable, so one evaluation is as good as
    # many. List, set and dict displays create a fresh mutable object each
    # time they run; hoisting one out of a loop would alias every iteration.
    if isinstance(expression, ast.Tuple):
        return all(is_eagerly_evaluable(element, available) for element in expression.elts)
    if isinstance(expression, ast.UnaryOp) and isinstance(expression.operand, ast.Constant):
        return isinstance(expression.op, (ast.USub, ast.UAdd, ast.Invert, ast.Not))
    return False


def available_argument_names(
    function: FunctionNode, block: Sequence[ast.stmt], analyzer: "ScopeAnalyzer"
) -> FrozenSet[str]:
    """Names a call standing where ``block`` stands can read without raising.

    Module-level bindings, names bound on every path from the function's
    entry to the block, and bindings of the enclosing functions. Builtins are
    always available and are not listed.
    """
    names: Set[str] = set(analyzer.root_scope.bindings)
    if block:
        names |= definitely_bound_before(function, block[0])
    scope = analyzer.node_scopes.get(function)
    while scope is not None and scope.parent is not None:
        scope = scope.parent
        if scope is not analyzer.root_scope:
            names |= set(scope.bindings)
    return frozenset(names)


def has_impure_eager_parameters(
    substitution: "Substitution", available: Sequence[AbstractSet[str]]
) -> bool:
    """Whether any eagerly passed parameter argument may be observable when hoisted.

    ``available`` gives, per block, the names its call site can resolve (see
    ``available_argument_names``). Lambda-lifted parameters and forwarded
    callees are evaluated inside the helper at the original position, so any
    expression is acceptable there. Call this after extraction, which is when
    callee parameters are known.
    """
    deferred = (
        set(substitution.function_params)
        | set(substitution.params_used_as_callee)
        | set(substitution.inlined_parameters)
    )
    return any(
        not is_eagerly_evaluable(expression, available[block_idx])
        for name, expressions in substitution.param_expressions.items()
        if name not in deferred
        for block_idx, expression in expressions
    )


def defer_impure_parameters(
    substitution: "Substitution",
    template_block: Iterable[ast.AST],
    available: Sequence[AbstractSet[str]],
) -> None:
    """Turn parameters whose arguments cannot be hoisted into zero-argument thunks.

    The helper then evaluates ``__param_n()`` at the original position, as often
    and as conditionally as the block did. ``available`` gives, per block, the
    names its call site can resolve. Parameters already lambda-lifted keep
    their arguments; parameters used only as callees are forwarded lazily by
    the extractor and need no thunk.
    """
    callees: Set[str] = set()
    for statement in template_block:
        for node in ast.walk(statement):
            if isinstance(node, ast.Call):
                parameter = substitution.get_param_for_expr(0, node.func)
                if parameter is not None:
                    callees.add(parameter)
    for name, expressions in substitution.param_expressions.items():
        if name in substitution.function_params or name in callees:
            continue
        if any(
            not is_eagerly_evaluable(expression, available[block_idx])
            for block_idx, expression in expressions
        ):
            substitution.function_params[name] = []


def moves_scope_declaration(function: FunctionNode, nodes: Iterable[ast.AST]) -> bool:
    """Whether the block carries a ``global``/``nonlocal`` declaration the caller still needs.

    A declaration inside the block moves into the helper with it. Any remaining
    use of that name in the caller then silently becomes a local access. Only
    declarations at the block's own scope count; a nested function's own
    declarations move with that function.
    """
    block = tuple(nodes)
    extracted = {child for statement in block for child in ast.walk(statement)}
    declared: Set[str] = set()
    for statement in block:
        for node in walk_own_scope(statement):
            if isinstance(node, (ast.Global, ast.Nonlocal)):
                declared.update(node.names)
    if not declared:
        return False
    for node in ast.walk(function):
        if node in extracted or node is function:
            continue
        if isinstance(node, ast.Name) and node.id in declared:
            return True
        if isinstance(node, (ast.Global, ast.Nonlocal)) and declared & set(node.names):
            return True
    return False


def walk_own_scope(node: ast.AST) -> Iterable[ast.AST]:
    """Yield nodes of ``node`` without entering nested function or class scopes."""
    pending = [node]
    while pending:
        current = pending.pop()
        yield current
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        pending.extend(ast.iter_child_nodes(current))
