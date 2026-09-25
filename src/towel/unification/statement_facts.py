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

"""Facts about one statement, computed once per statement node.

Block enumeration forms every contiguous sub-block of a body and asks each
for its signature and whether it returns; walking every block in full made
that cubic in the body's length. The counts and truth values in
``StatementFacts`` sum or disjoin over a block's statements; the module also
holds each statement's name sets (what it binds, reads, or mentions), its
import and match-capture names, and its node-type shape, the shape and the
mentioned names memoized the same way. The memo is weak: a fact vanishes
with its statement. Analysis never mutates a parsed tree, which is what keys
the engine's other node-identity caches too.
"""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
from typing import Callable, FrozenSet, List, Optional, Sequence, Set, TypeVar, Union
from weakref import WeakKeyDictionary

from .bounded_cache import memoizing
from .parameters import parameter_nodes

_SIGNATURE_SKIPS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
"""Nested scopes the block signature does not look into."""

_RETURN_SKIPS = (ast.FunctionDef, ast.AsyncFunctionDef)
"""Scopes whose ``return`` is their own, not the enclosing statement's."""

_T = TypeVar("_T")


def memoized_per_node(
    memo: "WeakKeyDictionary[ast.AST, _T]", node: ast.AST, compute: Callable[[ast.AST], _T]
) -> _T:
    """``compute(node)`` once per node, for as long as the node lives.

    The memo is weak, so an entry vanishes with its node; the result must
    depend on the node's structure alone, which analysis never mutates.
    Under ``memoization_disabled`` it is computed every time.
    """
    if not memoizing():
        return compute(node)
    cached = memo.get(node)
    if cached is None:
        cached = compute(node)
        try:
            memo[node] = cached
        except TypeError:  # a node type that cannot be weakly referenced
            pass
    return cached


@dataclass(frozen=True)
class StatementFacts:
    """What the block signature and the return check need from one statement."""

    has_with: bool
    has_try: bool
    name_load_count: int
    name_store_count: int
    call_count: int
    contains_return: bool


def _compute_facts(statement: ast.AST) -> StatementFacts:
    has_with = False
    has_try = False
    name_load_count = 0
    name_store_count = 0
    call_count = 0
    contains_return = False
    # Each entry carries whether the signature still counts this subtree: a
    # class body or lambda is left out of the signature but is still searched
    # for a return, as the return finder enters it.
    stack = [(statement, True)]
    while stack:
        node, counted = stack.pop()
        if isinstance(node, _RETURN_SKIPS):
            continue
        if counted and isinstance(node, _SIGNATURE_SKIPS):
            counted = False
        if counted:
            if isinstance(node, ast.With):
                has_with = True
            elif isinstance(node, ast.Try):
                has_try = True
            elif isinstance(node, ast.Name):
                if isinstance(node.ctx, ast.Load):
                    name_load_count += 1
                elif isinstance(node.ctx, ast.Store):
                    name_store_count += 1
            elif isinstance(node, ast.Call):
                call_count += 1
        if isinstance(node, ast.Return):
            contains_return = True
        stack.extend((child, counted) for child in ast.iter_child_nodes(node))
    return StatementFacts(
        has_with=has_with,
        has_try=has_try,
        name_load_count=name_load_count,
        name_store_count=name_store_count,
        call_count=call_count,
        contains_return=contains_return,
    )


_FACTS: "WeakKeyDictionary[ast.AST, StatementFacts]" = WeakKeyDictionary()


def statement_facts(statement: ast.AST) -> StatementFacts:
    """The facts of one statement, computed on first request."""
    return memoized_per_node(_FACTS, statement, _compute_facts)


def imported_binding_name(alias: ast.alias) -> Optional[str]:
    """The local name an import alias binds, or None for a star import.

    ``import a.b`` binds ``a``, ``import a.b as c`` binds ``c``, ``from m
    import x`` binds ``x``; ``from m import *`` binds nothing nameable.
    """
    if alias.name == "*":
        return None
    return alias.asname or alias.name.split(".")[0]


def import_binding_names(node: Union[ast.Import, ast.ImportFrom]) -> List[str]:
    """The local names an import statement binds, in order, star imports aside."""
    return [name for alias in node.names if (name := imported_binding_name(alias)) is not None]


def pattern_capture_names(pattern: ast.AST) -> Set[str]:
    """Names bound by a match pattern, including nested captures."""
    names: Set[str] = set()
    for node in ast.walk(pattern):
        if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names.add(node.rest)
    return names


_NESTED_DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def bindings_of(statement: ast.AST, *, into_nested_scopes: bool) -> FrozenSet[str]:
    """The names ``statement`` binds or unbinds: assignment, loop, ``with`` and walrus
    targets, ``del`` targets, ``except ... as`` and match-capture names, imports, and the
    names of definitions.

    With ``into_nested_scopes`` every binding at any depth counts, comprehension
    targets and the locals of nested functions included: an over-approximation
    for guards that only reject. Without it, only the statement's own scope
    counts: a nested definition contributes its name and nothing inside it, a
    lambda nothing, and a comprehension only what an assignment expression in
    it binds, since its targets are its own.
    """
    names: Set[str] = set()
    pending: List[ast.AST] = [statement]
    while pending:
        node = pending.pop()
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                names.add(node.id)
            continue
        if isinstance(node, _NESTED_DEFINITIONS):
            names.add(node.name)
            if not into_nested_scopes:
                # What the definition evaluates where it stands runs in this
                # scope: an assignment expression there binds here.
                pending.extend(_evaluated_where_defined(node))
                continue
        elif isinstance(node, ast.Lambda) and not into_nested_scopes:
            pending.extend(_evaluated_where_defined(node))
            continue
        elif isinstance(node, ast.comprehension) and not into_nested_scopes:
            pending.append(node.iter)
            pending.extend(node.ifs)
            continue
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.match_case):
            names.update(pattern_capture_names(node.pattern))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(import_binding_names(node))
            continue
        pending.extend(ast.iter_child_nodes(node))
    return frozenset(names)


def _evaluated_where_defined(
    node: Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda],
) -> List[ast.AST]:
    """A definition's decorators, defaults, annotations, bases and keywords."""
    found: List[ast.AST] = []
    if not isinstance(node, ast.Lambda):
        found.extend(node.decorator_list)
    if isinstance(node, ast.ClassDef):
        found.extend(node.bases)
        found.extend(keyword.value for keyword in node.keywords)
        return found
    found.extend(node.args.defaults)
    found.extend(default for default in node.args.kw_defaults if default is not None)
    if not isinstance(node, ast.Lambda):
        found.extend(
            argument.annotation
            for argument in parameter_nodes(node.args)
            if argument.annotation is not None
        )
        if node.returns is not None:
            found.append(node.returns)
    return found


def loaded_names(node: ast.AST) -> Set[str]:
    """Every name read anywhere under ``node``, nested scopes included.

    A read is whatever needs the name's binding: a load, the target of an
    augmented assignment (``count += 1`` loads ``count`` before it stores
    it), and a ``del`` target (``del count`` raises ``UnboundLocalError``
    without one).
    """
    names: Set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Load, ast.Del)):
            names.add(child.id)
        elif isinstance(child, ast.AugAssign) and isinstance(child.target, ast.Name):
            names.add(child.target.id)
    return names


def block_contains_return(block: Sequence[ast.stmt]) -> bool:
    """Whether any statement of ``block`` returns, outside nested functions."""
    return any(statement_facts(statement).contains_return for statement in block)


@dataclass(frozen=True)
class StatementShape:
    """How many nodes of each type one statement holds, nested scopes included."""

    node_count: int
    type_counts: "Counter[str]"
    """Shared between callers; never mutated."""


def _compute_shape(statement: ast.AST) -> StatementShape:
    type_counts = Counter(type(node).__name__ for node in ast.walk(statement))
    return StatementShape(node_count=sum(type_counts.values()), type_counts=type_counts)


_SHAPES: "WeakKeyDictionary[ast.AST, StatementShape]" = WeakKeyDictionary()


def statement_shape(statement: ast.AST) -> StatementShape:
    """The shape of one statement, computed on first request."""
    return memoized_per_node(_SHAPES, statement, _compute_shape)


_MENTIONED_NAMES: "WeakKeyDictionary[ast.AST, FrozenSet[str]]" = WeakKeyDictionary()


def _compute_mentioned_names(statement: ast.AST) -> FrozenSet[str]:
    return frozenset(node.id for node in ast.walk(statement) if isinstance(node, ast.Name))


def mentioned_names(statement: ast.AST) -> FrozenSet[str]:
    """Every identifier ``statement`` names anywhere, nested scopes included."""
    return memoized_per_node(_MENTIONED_NAMES, statement, _compute_mentioned_names)


__all__ = [
    "StatementFacts",
    "StatementShape",
    "block_contains_return",
    "memoized_per_node",
    "mentioned_names",
    "statement_facts",
    "statement_shape",
]
