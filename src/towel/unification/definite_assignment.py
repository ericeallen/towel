"""Names that are bound on every path before a given statement executes.

An argument passed eagerly is read at the call site. If the original block
read that name only on some paths, and the name might be unbound, the
eager read raises where the original would not have. This analysis is a
conservative approximation of CPython's binding rules: it reports a name
as definitely bound only when every path reaching the statement binds it.
"""

from __future__ import annotations

import ast
from .assignment_analyzer import stored_names
from .parameters import parameter_names
from .scope_analyzer import pattern_capture_names
from functools import cached_property
from typing import Dict, FrozenSet, Iterator, List, Optional, Sequence, Set, Union
from weakref import WeakKeyDictionary

Function = Union[ast.FunctionDef, ast.AsyncFunctionDef]

# ``None`` stands for "every name": the position is unreachable, so any
# claim about it is vacuously true. It is the identity of intersection.
Definite = Optional[FrozenSet[str]]


class _FunctionFacts:
    """Per-function results computed once and queried per block.

    A function with n statements has O(n^2) candidate blocks, and each guard
    runs per block, so anything that walks the whole function per block is
    cubic in n. These facts are built on first use and cached on the function
    node for as long as the analyzed tree lives.
    """

    def __init__(self, function: Function) -> None:
        self.function = function

    @cached_property
    def locally_bound(self) -> FrozenSet[str]:
        """Names the function's own scope binds anywhere; see ``locally_bound_names``."""
        return frozenset(_locally_bound_names(self.function))

    @cached_property
    def definite_before(self) -> Dict[ast.AST, FrozenSet[str]]:
        """Names definitely bound on entry to every statement, at every depth."""
        before: Dict[ast.AST, FrozenSet[str]] = {}
        _collect_definite_before(
            self.function, frozenset(parameter_names(self.function.args)), before
        )
        return before


_STATEMENT_LIST_FIELDS = ("body", "orelse", "finalbody", "handlers", "cases")


def _collect_definite_before(
    container: ast.AST, entry: FrozenSet[str], before: Dict[ast.AST, FrozenSet[str]]
) -> None:
    """One pass over the statement lists a path from the function root can follow.

    Mirrors :func:`_definitely_bound_before_uncached`: at each level the
    names bound before a child are those bound on entry to the container,
    what the container binds before the list runs, and what every preceding
    sibling definitely binds, with a sibling that never falls through making
    the rest of the list contribute nothing.
    """
    for field in _STATEMENT_LIST_FIELDS:
        children = getattr(container, field, None)
        if not isinstance(children, list) or not children:
            continue
        on_entry = entry | frozenset(_bindings_on_entry(container, field))
        accumulated: Definite = frozenset()
        for child in children:
            bound = on_entry | (accumulated or frozenset())
            before[child] = bound
            _collect_definite_before(child, bound, before)
            if accumulated is not None:
                result = _definite_statement(child)
                accumulated = (
                    None if result is None else (accumulated - _may_unbind(child)) | result
                )


_FACTS: "WeakKeyDictionary[ast.AST, _FunctionFacts]" = WeakKeyDictionary()


def _facts(function: Function) -> _FunctionFacts:
    facts = _FACTS.get(function)
    if facts is None:
        facts = _FunctionFacts(function)
        _FACTS[function] = facts
    return facts


def definitely_bound_before(function: Function, statement: ast.stmt) -> Set[str]:
    """Names bound on every path from the function's entry to ``statement``."""
    known = _facts(function).definite_before.get(statement)
    if known is not None:
        return set(known)
    return _definitely_bound_before_uncached(function, statement)


def _definitely_bound_before_uncached(function: Function, statement: ast.stmt) -> Set[str]:
    """The path-walking form, kept as the reference and as the fallback."""
    parents: Dict[ast.AST, ast.AST] = {
        child: parent for parent in ast.walk(function) for child in ast.iter_child_nodes(parent)
    }
    bound: Set[str] = set(parameter_names(function.args))
    path: List[ast.AST] = []
    node: ast.AST = statement
    while node is not function:
        path.append(node)
        node = parents[node]
    path.reverse()
    container: ast.AST = function
    for child in path:
        for field, value in ast.iter_fields(container):
            if isinstance(value, list) and child in value:
                index = value.index(child)
                bound |= _definite(value[:index]) or set()
                bound |= _bindings_on_entry(container, field)
                break
        container = child
    return bound


def definitely_bound_after(statements: Sequence[ast.stmt]) -> Optional[Set[str]]:
    """Names bound on every path that falls through ``statements``.

    ``None`` means no path falls through: every path returns, raises, breaks,
    or continues, so nothing after the sequence can read its bindings.
    """
    result = _definite(statements)
    return None if result is None else set(result)


def definitely_bound_before_each(statements: Sequence[ast.stmt]) -> Iterator[Optional[Set[str]]]:
    """For each statement in turn, the names bound on every path that reaches it.

    Equivalent to ``definitely_bound_after(statements[:index])`` for each
    index, computed in one pass instead of one pass per prefix. ``None`` means
    no path reaches the statement.
    """
    accumulated: Definite = frozenset()
    for statement in statements:
        yield None if accumulated is None else set(accumulated)
        if accumulated is not None:
            result = _definite_statement(statement)
            accumulated = (
                None if result is None else (accumulated - _may_unbind(statement)) | result
            )


def locally_bound_names(function: Function) -> Set[str]:
    """Names the function's own scope binds anywhere: parameters and local statements.

    Only these names have a path-dependent binding state inside the function.
    Any other name resolves lexically to an enclosing scope, a global, or a
    builtin, and reading it early cannot change which binding it sees.
    """
    return set(_facts(function).locally_bound)


def _locally_bound_names(function: Function) -> Set[str]:
    names: Set[str] = set(parameter_names(function.args))
    pending: List[ast.AST] = list(function.body)
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            continue  # a nested scope binds its own names
        if isinstance(node, ast.Lambda):
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(
                alias.asname or alias.name.split(".")[0]
                for alias in node.names
                if alias.name != "*"
            )
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.match_case):
            names.update(pattern_capture_names(node.pattern))
        pending.extend(ast.iter_child_nodes(node))
    return names


def _bindings_on_entry(container: ast.AST, field: str) -> Set[str]:
    """Names a compound statement binds before running the listed field."""
    if isinstance(container, (ast.For, ast.AsyncFor)) and field == "body":
        return _targets(container.target)
    if isinstance(container, (ast.With, ast.AsyncWith)) and field == "body":
        names: Set[str] = set()
        for item in container.items:
            if item.optional_vars is not None:
                names |= _targets(item.optional_vars)
        return names
    if isinstance(container, ast.ExceptHandler) and field == "body" and container.name:
        return {container.name}
    if isinstance(container, ast.match_case) and field == "body":
        return pattern_capture_names(container.pattern)
    return set()


def _targets(target: ast.AST) -> Set[str]:
    return stored_names(target)


def _meet(left: Definite, right: Definite) -> Definite:
    if left is None:
        return right
    if right is None:
        return left
    return left & right


def _join(left: Definite, right: Definite) -> Definite:
    if left is None or right is None:
        return None
    return left | right


def _definite(statements: Sequence[ast.stmt]) -> Definite:
    """Names bound on every path through the sequence, or None if it never falls through."""
    bound: FrozenSet[str] = frozenset()
    for statement in statements:
        result = _definite_statement(statement)
        if result is None:
            return None
        # A name the statement may unbind on some path is no longer definite,
        # unless the statement itself rebinds it on every path.
        bound = (bound - _may_unbind(statement)) | result
    return bound


def _targets_of_delete(statement: ast.Delete) -> Set[str]:
    return {
        node.id
        for target in statement.targets
        for node in ast.walk(target)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Del)
    }


def _may_unbind(statement: ast.stmt) -> FrozenSet[str]:
    """Names some path through the statement leaves unbound: ``del`` targets and
    ``except ... as name`` names, which Python deletes when the handler exits.
    Nested definitions are other scopes and are not entered."""
    if isinstance(statement, ast.Delete):
        return frozenset(_targets_of_delete(statement))
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return frozenset()
    names: Set[str] = set()
    if isinstance(statement, ast.Try):
        names.update(handler.name for handler in statement.handlers if handler.name)
        for handler in statement.handlers:
            for child in handler.body:
                names |= _may_unbind(child)
    if isinstance(statement, ast.Match):
        for case in statement.cases:
            for child in case.body:
                names |= _may_unbind(child)
    for field in ("body", "orelse", "finalbody"):
        children = getattr(statement, field, None)
        if isinstance(children, list):
            for child in children:
                if isinstance(child, ast.stmt):
                    names |= _may_unbind(child)
    return frozenset(names)


def _definite_statement(statement: ast.stmt) -> Definite:
    if isinstance(statement, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
        return None
    if isinstance(statement, ast.Assign):
        names: Set[str] = set()
        for target in statement.targets:
            names |= _targets(target)
        return frozenset(names)
    if isinstance(statement, ast.AnnAssign):
        return frozenset(_targets(statement.target)) if statement.value is not None else frozenset()
    if isinstance(statement, (ast.Import, ast.ImportFrom)):
        return frozenset(
            alias.asname or alias.name.split(".")[0]
            for alias in statement.names
            if alias.name != "*"
        )
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return frozenset({statement.name})
    if isinstance(statement, ast.If):
        return _meet(_definite(statement.body), _definite(statement.orelse))
    if isinstance(statement, (ast.With, ast.AsyncWith)):
        if any(_suppresses(item.context_expr) for item in statement.items):
            return frozenset()
        names = set()
        for item in statement.items:
            if item.optional_vars is not None:
                names |= _targets(item.optional_vars)
        body = _definite(statement.body)
        return None if body is None else frozenset(names) | body
    if isinstance(statement, ast.Try):
        normal = _join(_definite(statement.body), _definite(statement.orelse))
        for handler in statement.handlers:
            handled = _definite(handler.body)
            if handled is not None and handler.name:
                # ``except E as e`` deletes ``e`` when the handler exits, so it
                # is unbound on that path even if it was bound before the try.
                handled = handled - {handler.name}
            normal = _meet(normal, handled)
        final = _definite(statement.finalbody)
        return _join(normal, final) if final is not None else None
    if isinstance(statement, ast.Match):
        if not any(_irrefutable(case.pattern) and case.guard is None for case in statement.cases):
            return frozenset()
        result: Definite = None
        for case in statement.cases:
            body = _definite(case.body)
            case_names = (
                None if body is None else body | frozenset(pattern_capture_names(case.pattern))
            )
            result = _meet(result, case_names)
        return result if result is not None else frozenset()
    # Loops may run zero times; a while-else or for-else without break would
    # be definite, but that refinement is not needed for soundness.
    return frozenset()


def _suppresses(expression: ast.AST) -> bool:
    callee = expression.func if isinstance(expression, ast.Call) else expression
    name = callee.id if isinstance(callee, ast.Name) else getattr(callee, "attr", "")
    return name == "suppress"


def _irrefutable(pattern: ast.AST) -> bool:
    if isinstance(pattern, ast.MatchAs):
        return pattern.pattern is None or _irrefutable(pattern.pattern)
    if isinstance(pattern, ast.MatchOr):
        return any(_irrefutable(alternative) for alternative in pattern.patterns)
    return False
