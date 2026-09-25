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
from .models import FunctionNode
from .parameters import parameter_names
from .statement_facts import import_binding_names, pattern_capture_names
from functools import cached_property
from typing import Dict, FrozenSet, Iterator, List, Optional, Sequence, Set, Tuple, cast
from weakref import WeakKeyDictionary, ref

# ``None`` stands for "every name": the position is unreachable, so any
# claim about it is vacuously true. It is the identity of intersection.
Definite = Optional[FrozenSet[str]]


class _FunctionFacts:
    """Per-function results computed once and queried per block.

    A function with n statements has O(n^2) candidate blocks, and each guard
    runs per block, so anything that walks the whole function per block is
    cubic in n. These facts are built on first use and cached on the function
    node for as long as the analyzed tree lives. The function is held weakly:
    the facts are the value of a weak-keyed table whose key is that function,
    and a strong reference here would keep the key, and its tree, alive for
    the life of the process.
    """

    def __init__(self, function: FunctionNode) -> None:
        self._function = ref(function)

    @property
    def function(self) -> FunctionNode:
        function = self._function()
        if function is None:
            raise RuntimeError("the analyzed function these facts describe is gone")
        return function

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
    names bound before a child are those bound on entry to the list
    (``_field_entry``), less what every preceding sibling may unbind, plus
    what it definitely binds. After a sibling that never falls through,
    nothing is claimed.
    """
    for field in _STATEMENT_LIST_FIELDS:
        children = getattr(container, field, None)
        if not isinstance(children, list) or not children:
            continue
        current: Definite = _field_entry(container, field, entry)
        for child in children:
            bound = current if current is not None else frozenset()
            before[child] = bound
            _collect_definite_before(child, bound, before)
            if current is not None:
                result = _definite_statement(child)
                current = None if result is None else (current - _may_unbind(child)) | result


_FACTS: "WeakKeyDictionary[ast.AST, _FunctionFacts]" = WeakKeyDictionary()


def _facts(function: FunctionNode) -> _FunctionFacts:
    facts = _FACTS.get(function)
    if facts is None:
        facts = _FunctionFacts(function)
        _FACTS[function] = facts
    return facts


def definitely_bound_before(function: FunctionNode, statement: ast.stmt) -> Set[str]:
    """Names bound on every path from the function's entry to ``statement``."""
    known = _facts(function).definite_before.get(statement)
    if known is not None:
        return set(known)
    return _definitely_bound_before_uncached(function, statement)


def _definitely_bound_before_uncached(function: FunctionNode, statement: ast.stmt) -> Set[str]:
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
    current: FrozenSet[str] = frozenset(bound)
    for child in path:
        for field, value in ast.iter_fields(container):
            if isinstance(value, list) and child in value:
                reached: Definite = _field_entry(container, field, current)
                for sibling in value[: value.index(child)]:
                    if reached is None:
                        break
                    result = _definite_statement(sibling)
                    reached = None if result is None else (reached - _may_unbind(sibling)) | result
                current = reached if reached is not None else frozenset()
                break
        container = child
    return set(current)


def _field_entry(container: ast.AST, field: str, entry: FrozenSet[str]) -> FrozenSet[str]:
    """Names bound on entry to ``container``'s statement list ``field``, given ``entry``.

    A loop's body may run again after a ``del`` or an ``except ... as`` in
    it, and its ``else`` after any of them; a handler or ``finally`` may start
    after any statement of the ``try``, and the ``else`` runs after them all.
    Whatever the statement may unbind anywhere is therefore not bound on
    entry to those lists. The container then binds its own targets.
    """
    if isinstance(container, (ast.For, ast.AsyncFor, ast.While) + _TRY_STATEMENTS) and field in (
        "body",
        "orelse",
        "handlers",
        "finalbody",
    ):
        entry = entry - _may_unbind(cast(ast.stmt, container))
    return entry | frozenset(_bindings_on_entry(container, field))


_TRY_STATEMENTS: Tuple[type, ...] = tuple(
    kind for kind in (ast.Try, getattr(ast, "TryStar", None)) if isinstance(kind, type)
)


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


def locally_bound_names(function: FunctionNode) -> Set[str]:
    """Names the function's own scope binds anywhere: parameters and local statements.

    Only these names have a path-dependent binding state inside the function.
    Any other name resolves lexically to an enclosing scope, a global, or a
    builtin, and reading it early cannot change which binding it sees.
    """
    return set(_facts(function).locally_bound)


def _locally_bound_names(function: FunctionNode) -> Set[str]:
    names: Set[str] = set(parameter_names(function.args))
    pending: List[ast.AST] = list(function.body)
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            # What a definition evaluates where it stands runs in this scope:
            # decorators, defaults, annotations, bases and keywords.
            pending.extend(node.decorator_list)
            if isinstance(node, ast.ClassDef):
                pending.extend([*node.bases, *node.keywords])
            else:
                pending.extend([node.args, *([node.returns] if node.returns else [])])
            continue  # a nested scope binds its own names
        if isinstance(node, ast.Lambda):
            pending.append(node.args)
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(import_binding_names(node))
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.match_case):
            names.update(pattern_capture_names(node.pattern))
        pending.extend(ast.iter_child_nodes(node))
    return names


def _bindings_on_entry(container: ast.AST, field: str) -> Set[str]:
    """Names a compound statement binds before running the listed field."""
    if isinstance(container, (ast.For, ast.AsyncFor)) and field == "body":
        return stored_names(container.target)
    if isinstance(container, (ast.With, ast.AsyncWith)) and field == "body":
        names: Set[str] = set()
        for item in container.items:
            if item.optional_vars is not None:
                names |= stored_names(item.optional_vars)
        return names
    if isinstance(container, ast.ExceptHandler) and field == "body" and container.name:
        return {container.name}
    if isinstance(container, ast.match_case) and field == "body":
        return pattern_capture_names(container.pattern)
    return set()


def _meet(left: Definite, right: Definite) -> Definite:
    if left is None:
        return right
    if right is None:
        return left
    return left & right


def _definite(statements: Sequence[ast.stmt]) -> Definite:
    """Names bound on every path through the sequence, or None if it never falls through."""
    return _then(frozenset(), statements)


def _then(bound: Definite, statements: Sequence[ast.stmt]) -> Definite:
    """``bound`` carried through ``statements``: what they unbind on some path goes, what they bind on every path comes."""
    for statement in statements:
        if bound is None:
            return None
        result = _definite_statement(statement)
        # A name the statement may unbind on some path is no longer definite,
        # unless the statement itself rebinds it on every path.
        bound = None if result is None else (bound - _may_unbind(statement)) | result
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
    if isinstance(statement, _TRY_STATEMENTS):
        handlers: List[ast.ExceptHandler] = getattr(statement, "handlers")
        names.update(handler.name for handler in handlers if handler.name)
        for handler in handlers:
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
            names |= stored_names(target)
        return frozenset(names)
    if isinstance(statement, ast.AnnAssign):
        return (
            frozenset(stored_names(statement.target))
            if statement.value is not None
            else frozenset()
        )
    if isinstance(statement, (ast.Import, ast.ImportFrom)):
        return frozenset(import_binding_names(statement))
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
                names |= stored_names(item.optional_vars)
        # The body may delete a target it was given.
        return _then(frozenset(names), statement.body)
    if isinstance(statement, ast.Try):
        # The else clause runs after the body and may delete what it bound;
        # so may the finally clause, after whichever path came before it.
        normal = _then(_definite(statement.body), statement.orelse)
        for handler in statement.handlers:
            entry = frozenset({handler.name}) if handler.name else frozenset()
            handled = _then(entry, handler.body)
            if handled is not None and handler.name:
                # ``except E as e`` deletes ``e`` when the handler exits, so it
                # is unbound on that path even if it was bound before the try.
                handled = handled - {handler.name}
            normal = _meet(normal, handled)
        return _then(normal, statement.finalbody)
    if isinstance(statement, ast.Match):
        if not any(_irrefutable(case.pattern) and case.guard is None for case in statement.cases):
            return frozenset()
        result: Definite = None
        for case in statement.cases:
            # A case body may delete what its pattern captured.
            captured = _then(frozenset(pattern_capture_names(case.pattern)), case.body)
            result = _meet(result, captured)
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
