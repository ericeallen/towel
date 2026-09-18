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
that cubic in the body's length. Every fact here is a count or a truth
value over one statement's subtree, so a block's answer is the sum or the
disjunction over its statements. The memo is weak: a fact vanishes with its
statement. Analysis never mutates a parsed tree, which is what keys the
engine's other node-identity caches too.
"""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
from typing import Callable, FrozenSet, Sequence, TypeVar
from weakref import WeakKeyDictionary

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
    """
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


def block_contains_return(block: Sequence[ast.AST]) -> bool:
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
