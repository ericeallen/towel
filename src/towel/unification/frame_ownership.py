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

"""Conservative ownership facts for locals split across an extracted helper frame.

An exceptional return bypasses the transfer of new locals to their caller;
a value return may release them before caller-held values. Count ownership
rather than explicit calls: every parameter and every opaque binding can
own an observable object. Only a literal constant binding proves otherwise.
Facts are immutable and weakly cached on the original parsed statements.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import FrozenSet, Sequence
from weakref import WeakKeyDictionary

from .assignment_analyzer import Binding, own_scope_bindings
from .function_scope import function_names
from .models import FunctionNode
from .parameters import parameter_names
from .statement_facts import memoized_per_node
from .visitors import body_without_docstring


@dataclass(frozen=True)
class FrameOwnership:
    """Potentially owned local values on each side of a helper boundary."""

    inside: FrozenSet[str]
    outside: FrozenSet[str]
    whole_body: bool

    @property
    def split(self) -> bool:
        return bool(self.inside and self.outside)


@dataclass(frozen=True)
class _StatementOwnership:
    bindings: tuple[Binding, ...]
    owned: FrozenSet[int]


def _statement_ownership(statement: ast.AST) -> _StatementOwnership:
    bindings = tuple(own_scope_bindings((statement,)))
    literal_targets: set[int] = set()
    for node in ast.walk(statement):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign) and (
            node.value is None or isinstance(node.value, ast.Constant)
        ):
            targets = [node.target]
        elif isinstance(node, ast.NamedExpr) and isinstance(node.value, ast.Constant):
            targets = [node.target]
        else:
            continue
        literal_targets.update(
            id(target)
            for root in targets
            for target in ast.walk(root)
            if isinstance(target, ast.Name) and isinstance(target.ctx, ast.Store)
        )
    return _StatementOwnership(
        bindings, frozenset(binding.node_id for binding in bindings) - literal_targets
    )


_OWNERSHIP: "WeakKeyDictionary[ast.AST, _StatementOwnership]" = WeakKeyDictionary()


def _facts(statements: Sequence[ast.stmt]) -> tuple[_StatementOwnership, ...]:
    return tuple(
        memoized_per_node(_OWNERSHIP, statement, _statement_ownership) for statement in statements
    )


def frame_ownership(function: FunctionNode, block: Sequence[ast.stmt]) -> FrameOwnership:
    """Original own-scope bindings partitioned by exact binding identity, not name alone.

    The same name can be rebound on both sides of a block. Nested scopes'
    own bindings are excluded by ``own_scope_bindings``; definitions and
    their evaluated defaults, imports, patterns and handler targets remain
    opaque. Global/nonlocal values are not this frame's owned locals.
    """
    local = function_names(function).local
    inside_facts, all_facts = _facts(block), _facts(function.body)
    inside_ids = {binding.node_id for fact in inside_facts for binding in fact.bindings}
    inside = frozenset(
        binding.name
        for fact in inside_facts
        for binding in fact.bindings
        if binding.node_id in fact.owned and binding.name in local
    )
    outside = frozenset(parameter_names(function.args)) | frozenset(
        binding.name
        for fact in all_facts
        for binding in fact.bindings
        if binding.node_id not in inside_ids
        and binding.node_id in fact.owned
        and binding.name in local
    )
    body = body_without_docstring(function.body)
    whole = len(body) == len(block) and all(left is right for left, right in zip(body, block))
    return FrameOwnership(inside, outside, whole)
