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

"""The names each Python scope binds for itself, and which scope a name resolves in.

Python decides a name's scope statically: a name bound anywhere in a
function, lambda, class body or comprehension is that scope's own
throughout it, unless the scope declares it ``global`` or ``nonlocal``. A
lambda binds its parameters and what an assignment expression in its body
binds; a comprehension binds its targets, while an assignment expression in
it binds in the enclosing function. A class body's names are visible to the
class body's own code only: the functions, lambdas and comprehensions
inside it look past it.

Parameter substitution (``extractor.ParameterSubstituter``) and the
instantiation check (``instantiation``) both ask, of a name at some
position, which enclosing scope binds it; they share these answers, so a
name the substituter leaves alone as another scope's is the name the check
resolves to that scope.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import FrozenSet, Iterable, List, Optional, Sequence, Set, Union

from .parameters import parameter_names
from .statement_facts import bindings_of
from .visitors import annotation_expressions, evaluated_before_definition

Comprehension = Union[ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp]
COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)

NestedScope = Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef, Comprehension]
NESTED_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef, *COMPREHENSIONS)


@dataclass(frozen=True)
class ScopeNames:
    """What one scope binds for itself and what it declares to be another scope's.

    ``local`` excludes the declared names. ``is_class`` marks a class body,
    which only its own code sees.
    """

    local: FrozenSet[str]
    declared_global: FrozenSet[str] = frozenset()
    declared_nonlocal: FrozenSet[str] = frozenset()
    is_class: bool = False

    def mentions(self, name: str) -> bool:
        """Whether the scope binds ``name`` or declares where it lives."""
        return name in self.local or name in self.declared_global or name in self.declared_nonlocal


def statement_list_scope(statements: Sequence[ast.stmt], *, is_class: bool = False) -> ScopeNames:
    """The scope a function or class body forms from ``statements``, parameters aside."""
    declared_global: Set[str] = set()
    declared_nonlocal: Set[str] = set()
    for node in _own_scope_nodes(statements):
        if isinstance(node, ast.Global):
            declared_global.update(node.names)
        elif isinstance(node, ast.Nonlocal):
            declared_nonlocal.update(node.names)
    bound: Set[str] = set()
    for statement in statements:
        bound.update(bindings_of(statement, into_nested_scopes=False))
    declared = declared_global | declared_nonlocal
    return ScopeNames(
        local=frozenset(bound - declared),
        declared_global=frozenset(declared_global),
        declared_nonlocal=frozenset(declared_nonlocal),
        is_class=is_class,
    )


def nested_scope_names(node: NestedScope) -> ScopeNames:
    """The scope ``node`` opens: a function's, a lambda's, a class body's or a comprehension's."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        body = statement_list_scope(node.body)
        declared = body.declared_global | body.declared_nonlocal
        return ScopeNames(
            local=body.local | (frozenset(parameter_names(node.args)) - declared),
            declared_global=body.declared_global,
            declared_nonlocal=body.declared_nonlocal,
        )
    if isinstance(node, ast.Lambda):
        return ScopeNames(
            local=frozenset(parameter_names(node.args))
            | bindings_of(node.body, into_nested_scopes=False)
        )
    if isinstance(node, ast.ClassDef):
        return statement_list_scope(node.body, is_class=True)
    return ScopeNames(local=comprehension_targets(node))


def comprehension_targets(node: Comprehension) -> FrozenSet[str]:
    """The names a comprehension's targets bind, in its own scope."""
    return frozenset(
        name.id
        for generator in node.generators
        for name in ast.walk(generator.target)
        if isinstance(name, ast.Name) and isinstance(name.ctx, ast.Store)
    )


def free_reads(node: ast.AST, own: FrozenSet[str] = frozenset()) -> Set[str]:
    """The names ``node`` reads that neither ``own`` nor a scope inside ``node`` binds.

    A comprehension's targets, a lambda's parameters and a function's
    parameters and locals are theirs; a class body binds nothing its reads
    are counted against. A ``del`` and an augmented assignment read the
    name they unbind or rebind.
    """
    if isinstance(node, ast.Name):
        return (
            {node.id} if isinstance(node.ctx, (ast.Load, ast.Del)) and node.id not in own else set()
        )
    found: Set[str] = set()
    if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
        if node.target.id not in own:
            found.add(node.target.id)
    if isinstance(node, ast.Lambda):
        for default in evaluated_before_definition(node):
            found |= free_reads(default, own)
        return found | free_reads(node.body, own | nested_scope_names(node).local)
    if isinstance(node, COMPREHENSIONS):
        found |= free_reads(node.generators[0].iter, own)
        inside = own | comprehension_targets(node)
        parts: List[ast.AST] = [
            *(generator.target for generator in node.generators),
            *(generator.iter for generator in node.generators[1:]),
            *(condition for generator in node.generators for condition in generator.ifs),
            *comprehension_results(node),
        ]
        for part in parts:
            found |= free_reads(part, inside)
        return found
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for expression in [*evaluated_before_definition(node), *annotation_expressions(node)]:
            found |= free_reads(expression, own)
        inside = own | nested_scope_names(node).local
        for statement in node.body:
            found |= free_reads(statement, inside)
        return found
    for child in ast.iter_child_nodes(node):
        found |= free_reads(child, own)
    return found


def comprehension_results(node: Comprehension) -> List[ast.expr]:
    """What a comprehension produces: the key and value of a dict, else the element."""
    if isinstance(node, ast.DictComp):
        return [node.key, node.value]
    return [node.elt]


def innermost_mentioning(scopes: Sequence[ScopeNames], name: str) -> Optional[int]:
    """The index in ``scopes`` (outermost first) of the scope a read of ``name`` stops at.

    A class body counts only when it is the innermost scope; a scope that
    declares the name ``nonlocal`` passes the lookup outward. None when no
    scope binds or declares it ``global``: the name is free in them all.
    """
    innermost = len(scopes) - 1
    for index in range(innermost, -1, -1):
        scope = scopes[index]
        if scope.is_class and index != innermost:
            continue
        if name in scope.local or name in scope.declared_global:
            return index
    return None


def _own_scope_nodes(statements: Iterable[ast.AST]) -> Iterable[ast.AST]:
    """Every node of ``statements`` outside the functions, lambdas and classes they define."""
    pending = list(statements)
    while pending:
        node = pending.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        pending.extend(ast.iter_child_nodes(node))
