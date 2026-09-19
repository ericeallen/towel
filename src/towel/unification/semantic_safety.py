"""Conservative guards for extractions that change execution context.

Reads that depend on the frame (``locals``, ``eval``, ``warnings.warn`` stack
levels, and the module's imported or assigned aliases of them), external
names another function may rebind while the block runs, nested scopes and
loop control that cross the block boundary, names the block unbinds or
declares ``global``/``nonlocal``, arguments that cannot be evaluated eagerly,
and the names a block resolves at module scope, which a same-module helper
may read bare.
"""

from __future__ import annotations

import ast
from typing import (
    Dict,
    Optional,
    AbstractSet,
    TYPE_CHECKING,
    FrozenSet,
    Iterable,
    Sequence,
    Set,
    Tuple,
    Union,
)
from dataclasses import dataclass
from weakref import WeakKeyDictionary

from .binding_detector import BindingDetector
from .builtins import is_builtin
from .definite_assignment import (
    definitely_bound_after,
    definitely_bound_before,
    locally_bound_names,
)
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


UNKNOWABLE_HAZARD = "*"
"""Stands for every external name when the module's scopes cannot be trusted."""


def rebound_external_names(
    analyzer: ScopeAnalyzer,
    function: FunctionNode,
    nodes: Iterable[ast.AST],
) -> FrozenSet[str]:
    """The external names the block reads that another function may rebind meanwhile.

    Passing such a name to the helper snapshots it before the block's later
    reads; a helper that reads it bare, where the block did, sees the
    rebinding as the block did. Explicit global/nonlocal declarations
    identify the bindings another function can update. Namespace reflection
    makes that identification unreliable, so its presence makes every external
    read a hazard, and a function the analyzer could not place is
    ``UNKNOWABLE_HAZARD``. Opaque mutation from outside the module is not
    modeled.
    """
    scope = analyzer.node_scopes.get(function)
    root = analyzer.root_scope
    if scope is None or root is None:
        return frozenset({UNKNOWABLE_HAZARD})
    hazards = analyzer.external_binding_hazards
    if hazards is None:
        return frozenset({UNKNOWABLE_HAZARD})
    rebound = hazards.rebound
    unreliable = hazards.unresolved_nonlocal or hazards.reflective
    found: Set[str] = set()
    for statement in nodes:
        for node in ast.walk(statement):
            if not isinstance(node, ast.Name) or not isinstance(node.ctx, ast.Load):
                continue
            binding = scope.lookup(node.id)
            if binding is None:
                if unreliable or (root.scope_id, node.id) in rebound:
                    found.add(node.id)
                continue
            # A local captured by a nested function is a mutable cell too.
            # Passing it to the helper snapshots it before that function runs.
            if (binding.scope_id, node.id) in rebound:
                found.add(node.id)
            elif binding.scope_id != scope.scope_id and unreliable:
                found.add(node.id)
    return frozenset(found)


def snapshots_rebound_external_names(
    analyzer: ScopeAnalyzer,
    function: FunctionNode,
    nodes: Iterable[ast.AST],
    deferred: AbstractSet[str] = frozenset(),
) -> bool:
    """Whether passing the block's external names would snapshot a rebinding hazard.

    Names in ``deferred`` are read bare by the helper and are no hazard.
    """
    return bool(rebound_external_names(analyzer, function, nodes) - deferred)


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


_NAMESPACE_CALLEES = frozenset({"locals", "globals", "eval", "exec"})
_NAMESPACE_CALLEES_NO_ARGS = frozenset({"vars", "dir"})


@dataclass(frozen=True)
class FrameAliases:
    """Local names through which a module reaches the frame-sensitive builtins.

    ``import builtins as bi`` makes ``bi.locals()`` a namespace read;
    ``from builtins import locals as l`` makes ``l()`` one; ``import warnings
    as w`` and ``from warnings import warn as w`` make ``w.warn(...)`` and
    ``w(...)`` warnings. Resolved from the module's import statements at any
    depth, so a shadowing local of the same name is over-approximated as the
    alias, which only declines.
    """

    builtins_modules: FrozenSet[str] = frozenset()
    namespace_functions: FrozenSet[str] = frozenset()
    warnings_modules: FrozenSet[str] = frozenset()
    warn_functions: FrozenSet[str] = frozenset()
    # ``gf = sys._getframe``: names bound to a frame- or stack-reading function.
    frame_functions: FrozenSet[str] = frozenset()


NO_ALIASES = FrameAliases()
_FRAME_ALIASES: "WeakKeyDictionary[ast.AST, FrameAliases]" = WeakKeyDictionary()


def frame_aliases(module: Optional[ast.AST]) -> FrameAliases:
    """The module's aliases of the frame-sensitive builtins, computed once per tree."""
    if module is None:
        return NO_ALIASES
    known = _FRAME_ALIASES.get(module)
    if known is not None:
        return known
    builtins_modules: Set[str] = set()
    namespace_functions: Set[str] = set()
    warnings_modules: Set[str] = set()
    warn_functions: Set[str] = set()
    frame_functions: Set[str] = set()
    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "builtins":
                    builtins_modules.add(alias.asname or alias.name)
                elif alias.name == "warnings":
                    warnings_modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            for alias in node.names:
                if node.module == "builtins" and (
                    alias.name in _NAMESPACE_CALLEES or alias.name in _NAMESPACE_CALLEES_NO_ARGS
                ):
                    namespace_functions.add(alias.asname or alias.name)
                elif node.module == "warnings" and alias.name == "warn":
                    warn_functions.add(alias.asname or alias.name)
    # ``e = eval`` and ``warn = warnings.warn`` are aliases too, wherever they
    # are written; the pass is a fixed point, since an alias of an alias is
    # one. Rebinding elsewhere makes this conservative, never unsound.
    grew = True
    while grew:
        grew = False
        for node in ast.walk(module):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if len(targets) != 1 or not isinstance(targets[0], ast.Name):
                continue
            aliased = targets[0].id
            value = node.value
            if isinstance(value, ast.Name):
                frame_builtin = (
                    value.id in _NAMESPACE_CALLEES
                    or value.id in _NAMESPACE_CALLEES_NO_ARGS
                    or value.id in namespace_functions
                )
                warning = value.id in warn_functions
                frame_reader = value.id in frame_functions
            elif isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name):
                frame_builtin = value.value.id in builtins_modules and (
                    value.attr in _NAMESPACE_CALLEES or value.attr in _NAMESPACE_CALLEES_NO_ARGS
                )
                warning = value.value.id in warnings_modules and value.attr == "warn"
                # Any receiver: the attribute name alone identifies these.
                frame_reader = value.attr in _FRAME_RELATIVE_CALLEES
            else:
                continue
            if frame_builtin and aliased not in namespace_functions:
                namespace_functions.add(aliased)
                grew = True
            if warning and aliased not in warn_functions:
                warn_functions.add(aliased)
                grew = True
            if frame_reader and aliased not in frame_functions:
                frame_functions.add(aliased)
                grew = True
    result = FrameAliases(
        frozenset(builtins_modules),
        frozenset(namespace_functions),
        frozenset(warnings_modules),
        frozenset(warn_functions),
        frozenset(frame_functions),
    )
    _FRAME_ALIASES[module] = result
    return result


def _callee_name(call: ast.Call, aliases: FrameAliases) -> Optional[str]:
    """The builtin a call reaches, by name or through a builtins-module alias."""
    callee = call.func
    if isinstance(callee, ast.Name):
        if callee.id in aliases.namespace_functions:
            return callee.id
        return callee.id
    if (
        isinstance(callee, ast.Attribute)
        and isinstance(callee.value, ast.Name)
        and callee.value.id in aliases.builtins_modules
    ):
        return callee.attr
    return None


def is_namespace_access_call(node: ast.AST, aliases: FrameAliases = NO_ALIASES) -> bool:
    """Whether ``node`` is a call that reads or writes the caller's namespace.

    That is ``locals()``, ``globals()``, ``eval()``, ``exec()``, or the
    no-argument ``vars()`` and ``dir()`` (which read ``locals()``), spelled
    directly or through an alias of the ``builtins`` module. A local
    variable, parameter, or attribute that merely shares one of these names is
    not such a call, so callers can rely on this to avoid false positives on
    shadowing.
    """
    if not isinstance(node, ast.Call):
        return False
    name = _callee_name(node, aliases)
    if name is None:
        return False
    if isinstance(node.func, ast.Name) and node.func.id in aliases.namespace_functions:
        # ``from builtins import vars as v``: the alias stands for the builtin.
        return True
    if name in _NAMESPACE_CALLEES:
        return True
    return name in _NAMESPACE_CALLEES_NO_ARGS and not node.args and not node.keywords


def _is_warning_call(call: ast.Call, aliases: FrameAliases) -> bool:
    """``warnings.warn(...)`` however it is spelled.

    The warnings registry deduplicates by the calling site, so two sites that
    warn become one site that warns once; and a ``stacklevel`` attributes the
    warning to a caller a fixed number of frames up, which a helper shifts.
    A ``stacklevel`` keyword on any call is taken as forwarding to ``warn``.
    """
    if any(keyword.arg == "stacklevel" for keyword in call.keywords):
        return True
    callee = call.func
    if isinstance(callee, ast.Name):
        return callee.id == "warn" or callee.id in aliases.warn_functions
    if isinstance(callee, ast.Attribute) and callee.attr == "warn":
        return isinstance(callee.value, ast.Name) and (
            callee.value.id == "warnings" or callee.value.id in aliases.warnings_modules
        )
    return False


def requires_original_frame(nodes: Iterable[ast.AST], aliases: FrameAliases = NO_ALIASES) -> bool:
    """Reject suspension and operations that inspect the original call frame.

    Generator delegation needs a separate transformation preserving send/throw
    and return values. Moving frame inspection into a helper is not equivalent.
    Unknown shadowing of these call names is deliberately treated conservatively.
    Each statement's verdict is memoized: it is a property of that statement
    and its module alone, and a statement belongs to every block that spans it.
    """
    return any(
        memoized_per_node(
            _FRAME_SENSITIVE,
            statement,
            lambda item: _statement_requires_original_frame(item, aliases),
        )
        for statement in nodes
    )


def block_requires_original_frame(
    analyzer: "ScopeAnalyzer", function: FunctionNode, nodes: Sequence[ast.stmt]
) -> bool:
    """``requires_original_frame`` with the module's aliases resolved (the guard-memo form)."""
    return requires_original_frame(nodes, frame_aliases(analyzer.analyzed_tree))


def frame_read_outside_block(
    analyzer: "ScopeAnalyzer", function: FunctionNode, nodes: Sequence[ast.stmt]
) -> bool:
    """Whether the function reads its own frame anywhere outside the block.

    ``locals()``, ``vars()``, ``dir()``, a direct ``eval``/``exec`` or a frame
    walk after the block sees the block's locals; moved into a helper, they
    are gone (``dir()`` after the block lists fewer names; ``eval("total")``
    raises). Only the function's own scope counts: a nested function's
    ``locals()`` is its own frame.
    """
    aliases = frame_aliases(analyzer.analyzed_tree)
    inside = set(map(id, nodes))
    for statement in function.body:
        if id(statement) in inside:
            continue
        for node in walk_own_scope(statement):
            if id(node) in inside:
                break
            if isinstance(node, ast.Call) and (
                is_namespace_access_call(node, aliases) or _reads_own_frame(node, aliases)
            ):
                return True
    return False


def _reads_own_frame(call: ast.Call, aliases: FrameAliases) -> bool:
    """``sys._getframe()`` or ``inspect.currentframe()``: a handle to this frame's locals.

    Stack listings and warnings attribute to frames above the call and are
    unchanged by a helper that has already returned; a frame object read
    later sees whatever locals are still there. A name bound to one of these
    (``gf = sys._getframe``) is over-approximated as the function itself.
    """
    callee = call.func
    if isinstance(callee, ast.Name) and callee.id in aliases.frame_functions:
        return True
    name = callee.attr if isinstance(callee, ast.Attribute) else getattr(callee, "id", "")
    return name in {"_getframe", "currentframe"}


_FRAME_SENSITIVE: "WeakKeyDictionary[ast.AST, bool]" = WeakKeyDictionary()


def _statement_requires_original_frame(statement: ast.AST, aliases: FrameAliases) -> bool:
    block = (statement,)
    if has_external_loop_control(block) or _has_comprehension_assignment(block):
        return True
    for node in ast.walk(statement):
        if isinstance(node, (ast.Yield, ast.YieldFrom, ast.Await, ast.AsyncFor, ast.AsyncWith)):
            return True
        if isinstance(node, ast.comprehension) and node.is_async:
            # An async comprehension is only valid inside an async function.
            return True
        if isinstance(node, ast.Call):
            if is_namespace_access_call(node, aliases):
                return True
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "super"
                and not node.args
                and not node.keywords
            ):
                return True
            if _is_frame_relative_call(node, aliases) or _is_warning_call(node, aliases):
                return True
    return False


_FRAME_RELATIVE_CALLEES = frozenset(
    {"_getframe", "currentframe", "stack", "getouterframes", "extract_stack", "print_stack"}
)


def _is_frame_relative_call(call: ast.Call, aliases: FrameAliases = NO_ALIASES) -> bool:
    """Calls whose result depends on how many frames sit above them.

    ``warnings.warn(..., stacklevel=n)`` attributes the warning to the n-th
    caller; a helper adds one frame. Frame and stack inspection is likewise
    relative to the current frame. Only direct, recognizably named calls and
    the module's assigned aliases of them are detected; a callee that
    inspects frames internally is not.
    """
    callee = call.func
    if isinstance(callee, ast.Name) and callee.id in aliases.frame_functions:
        return True
    name = callee.attr if isinstance(callee, ast.Attribute) else getattr(callee, "id", "")
    if any(keyword.arg == "stacklevel" for keyword in call.keywords):
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

    A name is available only when every path that can reach the call has
    bound it and none can have unbound it since:

    - the function's own locals bound on every path from its entry to the
      block (``definitely_bound_before``);
    - module-level names bound on every path through the module body before
      the top-level statement that contains the function, since the function
      cannot be called before its definition has run, unless a module-level
      ``del`` or a ``global`` declaration that deletes them exists anywhere;
    - an enclosing function's locals bound on every path from its entry to
      the statement that defines the inner function, unless the enclosing
      function deletes them anywhere: the inner function may run at any point
      after its definition, and a name the enclosing function binds later, or
      only on some path, is an empty cell until then.

    Class bodies never count: a method reads a class attribute through the
    instance or the class, never as a bare name. Builtins are always available
    and are not listed.
    """
    names: Set[str] = set()
    if block:
        names |= definitely_bound_before(function, block[0])
    module = analyzer.analyzed_tree
    if not isinstance(module, ast.Module):
        return frozenset(names)
    parents = _module_parents(module)
    if _within_class(parents, function):
        # The compiler fills the ``__class__`` cell before any method runs.
        names.add("__class__")
    inner: ast.AST = function
    while True:
        enclosing = _enclosing_function(parents, inner)
        if enclosing is None:
            names |= _module_names_bound_before(module, _top_level_statement(parents, inner))
            return frozenset(names)
        statement = _statement_within(parents, enclosing, inner)
        names |= definitely_bound_before(enclosing, statement) - _deleted_names(enclosing.body)
        inner = enclosing


_MODULE_PARENTS: "WeakKeyDictionary[ast.AST, Dict[ast.AST, ast.AST]]" = WeakKeyDictionary()


def module_resolved_names(
    function: FunctionNode, analyzer: "ScopeAnalyzer", names: AbstractSet[str]
) -> FrozenSet[str]:
    """Of ``names``, those a read inside ``function`` resolves at module scope or nowhere.

    A name the function binds anywhere is its local everywhere in it, and a
    name an enclosing function binds is a cell; either read is not the
    module's. Class bodies do not count: a method's bare names skip them.
    Anything else reaches the module's namespace, then the builtins, and is
    the same lookup from any function of the module.
    """
    # ``__class__`` is the cell the compiler gives a method for zero-argument
    # ``super()``; it names the defining class, not a module binding.
    resolved = set(names) - locally_bound_names(function) - {"__class__"}
    module = analyzer.analyzed_tree
    if not isinstance(module, ast.Module):
        return frozenset(resolved)
    parents = _module_parents(module)
    enclosing = _enclosing_function(parents, function)
    while enclosing is not None and resolved:
        resolved -= locally_bound_names(enclosing)
        enclosing = _enclosing_function(parents, enclosing)
    return frozenset(resolved)


def _module_parents(module: ast.Module) -> Dict[ast.AST, ast.AST]:
    """Each node's parent in ``module``, computed once per tree.

    Top-level statements are absent: their parent is the module, and holding
    the module as a value would pin the weakly held key forever.
    """
    parents = _MODULE_PARENTS.get(module)
    if parents is None:
        parents = {
            child: parent
            for parent in ast.walk(module)
            if parent is not module
            for child in ast.iter_child_nodes(parent)
        }
        _MODULE_PARENTS[module] = parents
    return parents


_SCOPE_NODES = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _within_class(parents: Dict[ast.AST, ast.AST], node: ast.AST) -> bool:
    """Whether a class body encloses ``node`` at any depth."""
    current = parents.get(node)
    while current is not None:
        if isinstance(current, ast.ClassDef):
            return True
        current = parents.get(current)
    return False


def _enclosing_function(parents: Dict[ast.AST, ast.AST], node: ast.AST) -> Optional[FunctionNode]:
    """The nearest enclosing function, skipping class bodies and lambdas; None at module level."""
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current
        current = parents.get(current)
    return None


def _statement_within(
    parents: Dict[ast.AST, ast.AST], enclosing: ast.AST, node: ast.AST
) -> ast.stmt:
    """The statement of ``enclosing``'s own flow (at any depth) that contains ``node``.

    Climbs from ``node`` until the next parent is ``enclosing``; a class body on
    the way is a statement of the enclosing function like any other.
    """
    current = node
    while parents.get(current) is not enclosing:
        current = parents[current]
    if not isinstance(current, ast.stmt):
        raise ValueError("a definition is a statement of its enclosing function")
    return current


def _top_level_statement(parents: Dict[ast.AST, ast.AST], node: ast.AST) -> ast.stmt:
    current = node
    while current in parents:
        current = parents[current]
    if not isinstance(current, ast.stmt):
        raise ValueError("a definition is a statement of its module")
    return current


_MODULE_BOUND_BEFORE: "WeakKeyDictionary[ast.AST, Dict[int, FrozenSet[str]]]" = WeakKeyDictionary()


def _module_names_bound_before(module: ast.Module, statement: ast.stmt) -> FrozenSet[str]:
    """Module names bound on every path before ``statement`` and never deleted.

    A binding inside ``try``, a branch without an else (``if TYPE_CHECKING``),
    a loop, or a ``match`` case is not on every path; an ``except ... as``
    name is unbound when its handler exits. A name a module-level ``del``
    removes, or that a function declares ``global`` and deletes, may be
    unbound whenever the function runs.
    """
    by_index = _MODULE_BOUND_BEFORE.get(module)
    if by_index is None:
        by_index = {}
        _MODULE_BOUND_BEFORE[module] = by_index
    index = next(i for i, item in enumerate(module.body) if item is statement)
    known = by_index.get(index)
    if known is not None:
        return known
    bound = definitely_bound_after(module.body[:index]) or set()
    deleted = set(_deleted_names(module.body))
    for node in ast.walk(module):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            declared = {
                name
                for item in walk_own_scope(node)
                if isinstance(item, ast.Global)
                for name in item.names
            }
            if declared:
                deleted |= declared & set(_deleted_names(node.body))
    result = frozenset(bound - deleted)
    by_index[index] = result
    return result


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
