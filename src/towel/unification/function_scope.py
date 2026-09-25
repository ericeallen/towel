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

"""Which names a block alone makes local to its function, and what else in it reads them.

Python decides a name's scope once per function, before any of it runs: a
name the function binds anywhere in its own code is its local everywhere in
it. The binders are every assignment target, ``del``, augmented and bare
annotated assignment, ``for``, ``with`` and ``except ... as`` targets,
imports, walrus (inside a comprehension too), ``match`` captures, and the
``def``, ``class`` and ``type`` statements. So a read of the name before the
binding raises ``UnboundLocalError``, and a nested function or class that
reads it reads that local, not a module name or a builtin.

Moving a block into a helper moves its bindings with it. Where the block
held the function's only binding of a name, the name stops being local to
the function. Every other read of it in the function then finds whatever an
enclosing scope, the module or the builtins bind: ``return total`` on an
early path returns the module's ``total`` where it raised
``UnboundLocalError``, and a nested function's ``nonlocal total`` names
another function's variable or no longer compiles. ``scope_moving_names``
names these for one block, so the pair can be declined unless the call
statement binds the name again.

Counting, not walking: each statement's binding and read counts are
memoized, a function's totals are their sum, and what the function's code
outside a block does is the function's totals less the block's. A name the
block binds as often as the whole function does has no binding outside it.
"""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
from typing import FrozenSet, Iterable, List, Optional, Sequence, Set, Union
from weakref import WeakKeyDictionary

from .models import FunctionNode
from .parameters import parameter_names
from .scope_analyzer import type_parameter_names
from .statement_facts import import_binding_names, memoized_per_node
from .visitors import (
    annotation_expressions,
    evaluated_before_definition,
    type_parameter_expressions,
    visit_each,
)

_Comprehension = Union[ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp]

_TYPE_ALIAS: Optional[type] = getattr(ast, "TypeAlias", None)
"""``type X = ...`` (Python 3.12+)."""


@dataclass(frozen=True)
class _NestedScope:
    """What a scope nested in another leaves to it.

    ``free`` are the names its code, and the code of the scopes inside it,
    looks up outside it: read and not bound there, or declared ``nonlocal``.
    ``binds_enclosing`` are the names it binds in the enclosing function,
    which only a walrus in a comprehension does.
    """

    free: FrozenSet[str]
    binds_enclosing: FrozenSet[str] = frozenset()


@dataclass(frozen=True)
class ScopeNames:
    """What some code of one scope does with names, counted by occurrence.

    ``bound`` counts the bindings that make a name local to the scope,
    ``read`` the scope's own loads of it, and ``nested`` the scopes nested
    directly in the code that leave it free. ``declared_global`` and
    ``declared_nonlocal`` hold the names the code declares so, which no
    binding makes local. The counters are never mutated once built.
    """

    bound: "Counter[str]"
    read: "Counter[str]"
    nested: "Counter[str]"
    declared_global: FrozenSet[str]
    declared_nonlocal: FrozenSet[str]

    def __add__(self, other: "ScopeNames") -> "ScopeNames":
        return ScopeNames(
            self.bound + other.bound,
            self.read + other.read,
            self.nested + other.nested,
            self.declared_global | other.declared_global,
            self.declared_nonlocal | other.declared_nonlocal,
        )

    @property
    def local(self) -> FrozenSet[str]:
        """The names this code makes local to its scope."""
        return frozenset(self.bound) - self.declared_global - self.declared_nonlocal

    @property
    def references(self) -> "Counter[str]":
        """Every read of a name through this scope: its own, and each nested scope's."""
        return self.read + self.nested


_NO_NAMES = ScopeNames(Counter(), Counter(), Counter(), frozenset(), frozenset())


class _OwnScopeNames(ast.NodeVisitor):
    """Count what one scope's own code binds and reads; a nested scope counts once, by its free names.

    ``evaluates_annotations`` says whether a variable's annotation runs:
    never in a function body (``total: Final = 0``), and in a class body
    unless the module postpones annotations, where it is counted either way.
    A definition's own annotations are counted where they are evaluated
    unless postponed, likewise. A read counted that never happens only
    declines. ``in_comprehension`` makes a walrus bind in the enclosing
    function, as Python does, instead of here.
    """

    def __init__(self, *, evaluates_annotations: bool, in_comprehension: bool = False) -> None:
        self.bound: "Counter[str]" = Counter()
        self.read: "Counter[str]" = Counter()
        self.nested: "Counter[str]" = Counter()
        self.declared_global: Set[str] = set()
        self.declared_nonlocal: Set[str] = set()
        self.walrus_targets: Set[str] = set()
        self.evaluates_annotations = evaluates_annotations
        self.in_comprehension = in_comprehension

    def result(self) -> ScopeNames:
        return ScopeNames(
            Counter(self.bound),
            Counter(self.read),
            Counter(self.nested),
            frozenset(self.declared_global),
            frozenset(self.declared_nonlocal),
        )

    def bind(self, names: Iterable[str]) -> None:
        self.bound.update(names)

    # -- names -------------------------------------------------------------

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.read[node.id] += 1
        else:  # a store or a ``del``: either makes the name local
            self.bound[node.id] += 1

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        if self.in_comprehension and isinstance(node.target, ast.Name):
            self.walrus_targets.add(node.target.id)
        else:
            self.visit(node.target)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self.visit(node.value)
        # A bare ``total: int`` makes ``total`` local as an assignment does.
        self.visit(node.target)
        if self.evaluates_annotations:
            self.visit(node.annotation)

    def visit_Global(self, node: ast.Global) -> None:
        self.declared_global.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.declared_nonlocal.update(node.names)

    def visit_Import(self, node: Union[ast.Import, ast.ImportFrom]) -> None:
        self.bind(import_binding_names(node))

    visit_ImportFrom = visit_Import

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is not None:
            self.visit(node.type)
        if node.name:
            self.bound[node.name] += 1
        visit_each(self, node.body)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.pattern is not None:
            self.visit(node.pattern)
        if node.name:
            self.bound[node.name] += 1

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name:
            self.bound[node.name] += 1

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        visit_each(self, node.keys)
        visit_each(self, node.patterns)
        if node.rest:
            self.bound[node.rest] += 1

    # -- nested scopes: what runs here, then what the scope leaves free ---------

    def _nested(self, scope: _NestedScope) -> None:
        self.nested.update(scope.free)
        self.bind(scope.binds_enclosing)

    def visit_FunctionDef(self, node: FunctionNode) -> None:
        visit_each(self, evaluated_before_definition(node))
        if not type_parameter_names(node):
            # A generic definition evaluates these in its annotation scope.
            visit_each(self, annotation_expressions(node))
        self.bound[node.name] += 1
        self._nested(_nested_scope(node))

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        visit_each(self, evaluated_before_definition(node))
        if not type_parameter_names(node):
            visit_each(self, [*node.bases, *(keyword.value for keyword in node.keywords)])
        self.bound[node.name] += 1
        self._nested(_nested_scope(node))

    def visit_Lambda(self, node: ast.Lambda) -> None:
        visit_each(self, evaluated_before_definition(node))
        self._nested(_nested_scope(node))

    def visit_ListComp(self, node: _Comprehension) -> None:
        # Only the first iterable is evaluated where the comprehension stands.
        if node.generators:
            self.visit(node.generators[0].iter)
        scope = _nested_scope(node)
        if self.in_comprehension:
            # A walrus binds past every enclosing comprehension.
            self.walrus_targets.update(scope.binds_enclosing)
            self.nested.update(scope.free)
        else:
            self._nested(scope)

    visit_SetComp = visit_ListComp
    visit_DictComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp

    def visit_TypeAlias(self, node: ast.AST) -> None:
        name = getattr(node, "name", None)
        if isinstance(name, ast.AST):
            self.visit(name)
        self._nested(_nested_scope(node))


def _function_like_free(names: ScopeNames) -> FrozenSet[str]:
    """What a function, lambda, comprehension or annotation scope looks up outside itself.

    A name it declares ``global`` is the module's for every scope inside it
    too; one it declares ``nonlocal`` is looked up outside, as is anything it
    or a scope inside it reads and it does not bind.
    """
    looked_up = frozenset(names.references)
    return (looked_up - names.local - names.declared_global) | names.declared_nonlocal


def _class_free(names: ScopeNames) -> FrozenSet[str]:
    """What a class body looks up outside itself.

    A class body reads its own names from its namespace and never from an
    enclosing function, so only the names it reads and does not bind leave
    it. The scopes nested in it look past it entirely: a method's free name
    is the enclosing function's whatever the class binds or declares.
    """
    own = frozenset(names.read) - names.local - names.declared_global
    return own | names.declared_nonlocal | frozenset(names.nested)


def _code_names(
    nodes: Iterable[ast.AST], *, evaluates_annotations: bool, in_comprehension: bool = False
) -> _OwnScopeNames:
    collector = _OwnScopeNames(
        evaluates_annotations=evaluates_annotations, in_comprehension=in_comprehension
    )
    visit_each(collector, nodes)
    return collector


def _in_annotation_scope(
    node: ast.AST, free: FrozenSet[str], head: Iterable[ast.AST]
) -> FrozenSet[str]:
    """``free`` and what ``head`` reads, seen from outside a generic definition's annotation scope."""
    parameters = type_parameter_names(node)
    evaluated = [*head, *type_parameter_expressions(node)]
    if not parameters and not evaluated:
        return free
    head_names = _code_names(evaluated, evaluates_annotations=True).result()
    return (free | _function_like_free(head_names)) - parameters


def _compute_nested_scope(node: ast.AST) -> _NestedScope:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        body = _code_names(node.body, evaluates_annotations=False)
        body.bind(parameter_names(node.args))
        free = _function_like_free(body.result())
        generic_head = annotation_expressions(node) if type_parameter_names(node) else []
        return _NestedScope(_in_annotation_scope(node, free, generic_head))
    if isinstance(node, ast.Lambda):
        body = _code_names([node.body], evaluates_annotations=False)
        body.bind(parameter_names(node.args))
        return _NestedScope(_function_like_free(body.result()))
    if isinstance(node, ast.ClassDef):
        free = _class_free(_code_names(node.body, evaluates_annotations=True).result())
        generic_head = (
            [*node.bases, *(keyword.value for keyword in node.keywords)]
            if type_parameter_names(node)
            else []
        )
        return _NestedScope(_in_annotation_scope(node, free, generic_head))
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        return _comprehension_scope(node)
    if _TYPE_ALIAS is not None and isinstance(node, _TYPE_ALIAS):
        value = getattr(node, "value", None)
        return _NestedScope(
            _in_annotation_scope(node, frozenset(), [value] if isinstance(value, ast.AST) else [])
        )
    raise TypeError(f"not a nested scope: {type(node).__name__}")


def _comprehension_scope(node: _Comprehension) -> _NestedScope:
    """A comprehension: its targets are its own, and a walrus in it binds in the enclosing function."""
    parts: List[ast.AST] = []
    for index, generator in enumerate(node.generators):
        parts.append(generator.target)
        if index:
            parts.append(generator.iter)
        parts.extend(generator.ifs)
    if isinstance(node, ast.DictComp):
        parts.extend([node.key, node.value])
    else:
        parts.append(node.elt)
    code = _code_names(parts, evaluates_annotations=False, in_comprehension=True)
    walrus = frozenset(code.walrus_targets)
    # The walrus targets are looked up outside, like ``nonlocal`` names.
    return _NestedScope(_function_like_free(code.result()) | walrus, walrus)


_NESTED_SCOPES: "WeakKeyDictionary[ast.AST, _NestedScope]" = WeakKeyDictionary()


def _nested_scope(node: ast.AST) -> _NestedScope:
    """``_compute_nested_scope(node)``, once per node."""
    return memoized_per_node(_NESTED_SCOPES, node, _compute_nested_scope)


def _compute_statement_names(statement: ast.AST) -> ScopeNames:
    return _code_names([statement], evaluates_annotations=False).result()


_STATEMENT_NAMES: "WeakKeyDictionary[ast.AST, ScopeNames]" = WeakKeyDictionary()


def statement_names(statement: ast.AST) -> ScopeNames:
    """What one statement of a function's own code does with names, once per statement."""
    return memoized_per_node(_STATEMENT_NAMES, statement, _compute_statement_names)


def code_names(statements: Iterable[ast.AST]) -> ScopeNames:
    """What these statements of one function's own code do with names, summed."""
    total = _NO_NAMES
    for statement in statements:
        total = total + statement_names(statement)
    return total


def _compute_identifiers(statement: ast.AST) -> FrozenSet[str]:
    found: Set[str] = set()
    for node in ast.walk(statement):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.add(node.name)
        elif isinstance(node, ast.arg):
            found.add(node.arg)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            found.update(node.names)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            found.update(import_binding_names(node))
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
            found.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            found.add(node.rest)
    return frozenset(found)


_IDENTIFIERS: "WeakKeyDictionary[ast.AST, FrozenSet[str]]" = WeakKeyDictionary()


def identifiers(statements: Iterable[ast.AST]) -> FrozenSet[str]:
    """Every name these statements spell, as a use, a binding or a declaration, nested scopes included.

    ``statement_facts.mentioned_names`` sees only ``Name`` nodes, not the
    names an import, a ``match`` capture, an ``except`` clause, a definition
    or a declaration binds.
    """
    found: Set[str] = set()
    for statement in statements:
        spelled: FrozenSet[str] = memoized_per_node(_IDENTIFIERS, statement, _compute_identifiers)
        found |= spelled
    return frozenset(found)


def _compute_function_names(function: ast.AST) -> ScopeNames:
    assert isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
    parameters = ScopeNames(
        Counter(parameter_names(function.args)), Counter(), Counter(), frozenset(), frozenset()
    )
    return parameters + code_names(function.body)


_FUNCTION_NAMES: "WeakKeyDictionary[ast.AST, ScopeNames]" = WeakKeyDictionary()


def function_names(function: FunctionNode) -> ScopeNames:
    """What ``function``'s own scope does with names, its parameters bound, once per function."""
    return memoized_per_node(_FUNCTION_NAMES, function, _compute_function_names)


def scope_moving_names(function: FunctionNode, block: Sequence[ast.stmt]) -> FrozenSet[str]:
    """The names only ``block`` makes local to ``function`` that the function's other code reads.

    A name qualifies when every binding the function has of it is in the
    block, the function declares it neither ``global`` nor ``nonlocal``, and
    code of the function outside the block reads it through the function's
    scope: directly, or from a nested function, lambda, comprehension, class
    body or ``type`` statement that does not bind it itself, or by declaring
    it ``nonlocal``. Moved into a helper, the block takes the name's
    locality with it and each of those reads resolves somewhere else, unless
    the statement that replaces the block binds the name again.
    """
    whole = function_names(function)
    part = code_names(block)
    only_here = {name for name, count in part.bound.items() if whole.bound[name] == count} - (
        whole.declared_global | whole.declared_nonlocal
    )
    if not only_here:
        return frozenset()
    outside = whole.references
    inside = part.references
    return frozenset(name for name in only_here if outside[name] > inside[name])
