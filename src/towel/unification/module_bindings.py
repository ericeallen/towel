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

"""What a module's top level binds, statement by statement.

A module binds its globals as it runs, so what a name denotes is a question
about a position in the module. The model here answers it for helper
placement (which class a base name denotes, whether a class can take a
helper) and for the import-graph questions that resolve names through the
module's own imports.
"""

from __future__ import annotations

import ast

from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterator, List, Mapping, Optional, Sequence, Set, Tuple, Union

from .bounded_cache import BoundedCache
from .statement_facts import imported_binding_name
from .visitors import body_shares_header_line
from ..source_text import source_lines


@dataclass(frozen=True)
class GlobalBinding:
    """One top-level statement of a module binding one of the module's globals."""

    order: int
    """Index within the module body of the top-level statement that binds it."""
    certain: bool
    """False when the statement sits inside a branch, a loop, a ``with``, or a ``try``."""
    class_qualname: Optional[str]
    """The class the statement defines, when the binding is a ``class`` statement."""
    is_import: bool
    """Whether the statement is an import."""
    origin: Optional[str] = None
    """What the binding copies, as a dotted name: the imported ``module.name``
    of an absolute import, or the dotted name a plain ``alias = a.b`` assigns,
    which is spelled in the module and resolved where the assignment runs."""


@dataclass(frozen=True)
class ClassHost:
    """What a module-level class statement shows about taking a helper into its body."""

    decorators: Tuple[Optional[str], ...]
    """Each decorator's dotted name, a call's callee included; None for any other expression."""
    bases: Tuple[Optional[str], ...]
    """Each base's dotted name, a subscript's value included (``Protocol[T]``)."""
    body_on_header_line: bool
    """The body is written after the header's colon, where no statement can follow it."""


# Class decorators known to return the class they are given, or (``slots=True``)
# a class built from its namespace, with every function in that namespace
# unwrapped. Anything else may drop, wrap, or replace a helper placed there.
NAMESPACE_PRESERVING_DECORATORS = frozenset(
    {
        "dataclasses.dataclass",
        "functools.total_ordering",
        "typing.final",
        "typing_extensions.final",
        "enum.unique",
    }
)

PROTOCOL_BASES = frozenset({"typing.Protocol", "typing_extensions.Protocol"})


@dataclass(frozen=True)
class ModuleBindings:
    """How one module binds its global names, in the order it binds them.

    A module's globals are bound as it runs, so what a name denotes is a
    question about a position in the module, not about the module as a
    whole. Each name maps to the top-level statements that bind it, in
    order; ``class_orders`` places every class of the module among those
    statements, so a base-class reference can be resolved where the class
    statement making it runs. A name a function declares ``global`` is left
    out of every answer, because any call may rebind it, and a ``from m
    import *`` is recorded as a point past which no name is known.
    """

    bindings: Mapping[str, Tuple[GlobalBinding, ...]]
    class_orders: Mapping[str, int]
    rebound_by_global: FrozenSet[str]
    star_imports: Tuple[int, ...]
    class_hosts: Mapping[str, ClassHost]

    def in_effect(self, name: str, order: int) -> Optional[GlobalBinding]:
        """The binding ``name`` holds where top-level statement ``order`` runs.

        None whenever that cannot be established: the name is never bound
        before that point, a function may rebind it through ``global``, the
        last binding before it is conditional, a star import since could
        have replaced it, or the binding shares a top-level statement with
        the reference, whose internal order this does not model.
        """
        if name in self.rebound_by_global:
            return None
        events = self.bindings.get(name, ())
        if any(event.order == order for event in events):
            return None
        earlier = [event for event in events if event.order < order]
        if not earlier:
            return None
        binding = earlier[-1]
        if not binding.certain:
            return None
        if any(binding.order <= star <= order for star in self.star_imports):
            return None
        return binding

    def may_bind(self, name: str) -> bool:
        """Whether some path through the module could leave ``name`` in its namespace.

        Any statement of the module's own scope that binds it counts, however
        conditional, and so does a ``global`` declaration anywhere, since a
        call may then bind it. A star import may bind any name, and so may
        rebinding ``__builtins__``, where every builtin lookup of the module's
        functions then goes.
        """
        if self.star_imports or "__builtins__" in self.bindings:
            return True
        if "__builtins__" in self.rebound_by_global:
            return True
        return name in self.bindings or name in self.rebound_by_global

    def resolve(self, dotted: str, order: int, depth: int = 0) -> Optional[str]:
        """The absolute dotted name ``dotted`` denotes where statement ``order`` runs.

        Only a name the module certainly binds there by an absolute import, or
        by a plain assignment of such a name, resolves; anything else is None.
        """
        head, _, rest = dotted.partition(".")
        binding = self.in_effect(head, order)
        if binding is None or binding.origin is None or depth > 8:
            return None
        target = f"{binding.origin}.{rest}" if rest else binding.origin
        if binding.is_import:
            return target
        return self.resolve(target, binding.order, depth + 1)

    def possible_origins(self, dotted: str, depth: int = 0) -> FrozenSet[str]:
        """Every absolute name ``dotted`` could denote, whatever path the module took."""
        head, _, rest = dotted.partition(".")
        found: Set[str] = set()
        for binding in self.bindings.get(head, ()):
            if binding.origin is None or depth > 8:
                continue
            target = f"{binding.origin}.{rest}" if rest else binding.origin
            found.update(
                {target} if binding.is_import else self.possible_origins(target, depth + 1)
            )
        return frozenset(found)

    def keeps_namespace(self, qualname: str) -> bool:
        """Whether the class statement's name ends up bound to a class with its namespace.

        True only when every decorator resolves, where the class statement
        runs, to one known to return the class, or a copy of its namespace,
        with every function in it unwrapped.
        """
        host = self.class_hosts.get(qualname)
        order = self.class_orders.get(qualname)
        return (
            host is not None
            and order is not None
            and all(
                decorator is not None
                and self.resolve(decorator, order) in NAMESPACE_PRESERVING_DECORATORS
                for decorator in host.decorators
            )
        )

    def refuses_helper(self, qualname: str) -> Optional[str]:
        """Why the module-level class ``qualname`` cannot take a helper into its body, if it cannot.

        A body on the header's line takes no statement after it. A decorator
        not known to preserve the namespace may drop the helper, wrap it, or
        bind the class's name to something else entirely. And a helper placed
        in a ``Protocol`` becomes one of its members, which every structural
        implementer lacks: an ``isinstance`` check against a runtime-checkable
        protocol turns false, and the checker stops accepting implementers
        that conformed. The protocol test is conservative: a base that could
        be ``Protocol`` on any path through the module, or is spelled so,
        counts.
        """
        host = self.class_hosts.get(qualname)
        order = self.class_orders.get(qualname)
        if host is None or order is None:
            return "unknown class"
        if host.body_on_header_line:
            return "body on the header line"
        if not self.keeps_namespace(qualname):
            return "decorator"
        for base in host.bases:
            if base is None:
                continue
            if (
                base.rsplit(".", 1)[-1] == "Protocol"
                or self.resolve(base, order) in PROTOCOL_BASES
                or self.possible_origins(base) & PROTOCOL_BASES
            ):
                return "protocol"
        return None


# Suites: the fields whose statements a compound statement may or may not run.
_SUITE_FIELDS = frozenset({"body", "orelse", "finalbody", "handlers", "cases"})

# One module's bindings, keyed by its source rather than its path, so a
# rewritten module never answers from the version it replaced. Placement asks
# the same few modules once per candidate helper home, and each answer is a
# pure function of the text it was built from.
_MODULE_BINDINGS: BoundedCache[str, Optional[ModuleBindings]] = BoundedCache(128)


def _child_nodes(value: object) -> Iterator[ast.AST]:
    """The AST nodes an ``iter_fields`` value holds, whether it holds one, many, or none."""
    if isinstance(value, ast.AST):
        yield value
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, ast.AST):
                yield item


class _Collector:
    """Builds a module's :class:`ModuleBindings` by walking its top level.

    Only the module's own scope binds its globals, so the walk descends into
    a function or class body for two things alone: the classes defined there,
    which the class index must agree with the analysis about, and any
    ``global`` declaration, which puts a name out of reach because a call
    anywhere may rebind it. Within the module's scope a binding is certain
    only on the unconditional spine; anything a branch, loop, ``with``, or
    ``try`` holds is recorded as a binding that may or may not have happened.
    """

    def __init__(self, lines: Sequence[str]) -> None:
        self._lines = lines
        self._class_hosts: Dict[str, ClassHost] = {}
        self._bindings: Dict[str, List[GlobalBinding]] = {}
        self._class_orders: Dict[str, int] = {}
        self._class_counts: Dict[str, int] = {}
        self._rebound_by_global: Set[str] = set()
        self._star_imports: List[int] = []
        self._order = 0

    def collect(self, tree: ast.Module) -> ModuleBindings:
        """Walk ``tree`` and return what its top level binds."""
        for order, stmt in enumerate(tree.body):
            self._order = order
            self._statement(stmt, True, ())
        return ModuleBindings(
            bindings={name: tuple(events) for name, events in self._bindings.items()},
            # A qualname two classes share identifies neither of them.
            class_orders={
                qualname: order
                for qualname, order in self._class_orders.items()
                if self._class_counts[qualname] == 1
            },
            rebound_by_global=frozenset(self._rebound_by_global),
            star_imports=tuple(self._star_imports),
            class_hosts={
                qualname: host
                for qualname, host in self._class_hosts.items()
                if self._class_counts[qualname] == 1
            },
        )

    def _bind(
        self,
        name: str,
        certain: bool,
        *,
        class_qualname: Optional[str] = None,
        is_import: bool = False,
        origin: Optional[str] = None,
    ) -> None:
        self._bindings.setdefault(name, []).append(
            GlobalBinding(
                order=self._order,
                certain=certain,
                class_qualname=class_qualname,
                is_import=is_import,
                origin=origin,
            )
        )

    def _note_class(self, qualname: str) -> None:
        self._class_counts[qualname] = self._class_counts.get(qualname, 0) + 1
        self._class_orders[qualname] = self._order

    def _statement(self, stmt: ast.stmt, certain: bool, class_stack: Tuple[str, ...]) -> None:
        """Record what ``stmt``, running in the module's own scope, binds."""
        if isinstance(stmt, ast.ClassDef):
            qualname = ".".join((*class_stack, stmt.name))
            self._note_class(qualname)
            if not class_stack:
                self._class_hosts[qualname] = ClassHost(
                    decorators=tuple(
                        dotted_name(d.func if isinstance(d, ast.Call) else d)
                        for d in stmt.decorator_list
                    ),
                    bases=tuple(
                        dotted_name(b.value if isinstance(b, ast.Subscript) else b)
                        for b in stmt.bases
                    ),
                    body_on_header_line=body_shares_header_line(self._lines, stmt),
                )
            for expr in (*stmt.decorator_list, *stmt.bases, *(kw.value for kw in stmt.keywords)):
                self._expression(expr, certain)
            self._nested_scope(stmt.body, (*class_stack, stmt.name))
            self._bind(stmt.name, certain, class_qualname=qualname)
            return
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in stmt.decorator_list:
                self._expression(decorator, certain)
            self._nested_scope(stmt.body, class_stack)
            self._bind(stmt.name, certain)
            return
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            if any(alias.name == "*" for alias in stmt.names):
                self._star_imports.append(self._order)
            for alias in stmt.names:
                name = imported_binding_name(alias)
                if name is not None:
                    self._bind(name, certain, is_import=True, origin=import_origin(stmt, alias))
            return
        if (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
            and (alias_of := dotted_name(stmt.value)) is not None
        ):
            self._bind(stmt.targets[0].id, certain, origin=alias_of)
            return
        if isinstance(stmt, (ast.Global, ast.Nonlocal)):
            return  # Both are declarations about other scopes, and bind nothing.
        branching = any(field in _SUITE_FIELDS for field, _ in ast.iter_fields(stmt))
        for field, value in ast.iter_fields(stmt):
            if field in _SUITE_FIELDS:
                for child in _child_nodes(value):
                    self._clause(child, class_stack)
                continue
            for child in _child_nodes(value):
                self._expression(child, certain and not branching)

    def _clause(self, node: ast.AST, class_stack: Tuple[str, ...]) -> None:
        """A member of a suite: a statement, an ``except`` clause, or a ``match`` case."""
        if isinstance(node, ast.stmt):
            self._statement(node, False, class_stack)
            return
        if isinstance(node, ast.ExceptHandler):
            if node.name:
                self._bind(node.name, False)
            if node.type is not None:
                self._expression(node.type, False)
            for inner in node.body:
                self._statement(inner, False, class_stack)
            return
        if isinstance(node, ast.match_case):
            self._expression(node.pattern, False)
            if node.guard is not None:
                self._expression(node.guard, False)
            for inner in node.body:
                self._statement(inner, False, class_stack)

    def _expression(self, node: ast.AST, certain: bool) -> None:
        """Record the names an expression stores into the scope it is evaluated in."""
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                self._bind(node.id, certain)
            return
        if isinstance(node, (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return  # Whatever their bodies bind belongs to a scope of their own.
        if isinstance(node, (ast.MatchAs, ast.MatchStar)):
            if node.name:
                self._bind(node.name, certain)
        elif isinstance(node, ast.MatchMapping):
            if node.rest:
                self._bind(node.rest, certain)
        for field, value in ast.iter_fields(node):
            if isinstance(node, ast.comprehension) and field == "target":
                continue  # A comprehension's loop variable lives in its own scope.
            for child in _child_nodes(value):
                self._expression(child, certain)

    def _nested_scope(self, body: Sequence[ast.stmt], class_stack: Tuple[str, ...]) -> None:
        """Take from a function or class body only what the module's own scope must know."""
        for stmt in body:
            if isinstance(stmt, ast.ClassDef):
                self._note_class(".".join((*class_stack, stmt.name)))
                self._nested_scope(stmt.body, (*class_stack, stmt.name))
                continue
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._nested_scope(stmt.body, class_stack)
                continue
            if isinstance(stmt, ast.Global):
                self._rebound_by_global.update(stmt.names)
                continue
            for _field, value in ast.iter_fields(stmt):
                for child in _child_nodes(value):
                    if isinstance(child, ast.stmt):
                        self._nested_scope([child], class_stack)
                    elif isinstance(child, (ast.ExceptHandler, ast.match_case)):
                        self._nested_scope(child.body, class_stack)


def dotted_name(node: ast.expr) -> Optional[str]:
    """``a.b.c`` for a chain of attributes on a name, None for any other expression."""
    parts: List[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def import_origin(stmt: Union[ast.Import, ast.ImportFrom], alias: ast.alias) -> Optional[str]:
    """The absolute dotted name an import alias binds; None for a relative import.

    ``import a.b`` binds ``a``, so its origin is ``a``; ``import a.b as c``
    binds ``a.b`` itself.
    """
    if isinstance(stmt, ast.Import):
        return alias.name if alias.asname else alias.name.split(".")[0]
    if stmt.level or not stmt.module:
        return None
    return f"{stmt.module}.{alias.name}"


def global_bindings(source: str) -> Optional[ModuleBindings]:
    """What ``source`` binds at its top level, or None when it will not parse."""
    if source in _MODULE_BINDINGS:
        return _MODULE_BINDINGS[source]
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return _MODULE_BINDINGS.put(source, None)
    return _MODULE_BINDINGS.put(source, _Collector(source_lines(source)).collect(tree))
