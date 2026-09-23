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

"""Promotion of equal literal arguments of higher-order factory calls.

When every block passes the same literal to a call whose result is later
used as a callable or a value, the literal may still be worth a parameter,
so a helper can serve a family of factories. This is a feature behind
``promote_equal_hof_literals``, off by default; a promotion is applied only
after every block has been checked, so one that cannot apply changes
nothing. A literal a tool reads where it stands, such as the message of
``label = _("Total")``, is never promoted (see ``static_positions``).
"""

from __future__ import annotations

import ast

from typing import Any, List, Optional, Tuple, Sequence, Callable, Iterable
from .visitors import OwnScopeVisitor

from .substitution import Substitution
from .unifier_state import UnifierState


class _CallContextFinder(OwnScopeVisitor):
    """Whether a name is called, or passed to a call, anywhere in the statements visited."""

    def __init__(self, var_name: str) -> None:
        self.var_name = var_name
        self.found = False

    def visit_Call(self, node: ast.Call) -> None:
        # Used as a callee
        if isinstance(node.func, ast.Name) and node.func.id == self.var_name:
            self.found = True
        # Used as an argument to a call (higher-order usage)
        for arg in node.args:
            if isinstance(arg, ast.Name) and arg.id == self.var_name:
                self.found = True
        for kw in node.keywords:
            if (
                kw.arg is not None
                and isinstance(kw.value, ast.Name)
                and kw.value.id == self.var_name
            ):
                self.found = True
        self.generic_visit(node)

    def _nested_class(self, node: ast.ClassDef) -> None:
        """A use inside a nested class body is not a use in this scope."""


def _is_used_as_callable_or_value_later(
    block: Sequence[ast.AST], start_stmt_idx: int, var_name: str
) -> bool:
    """Whether ``var_name`` is read in a call context by a later statement of ``block``."""
    finder = _CallContextFinder(var_name)
    for sidx in range(start_stmt_idx + 1, len(block)):
        finder.visit(block[sidx])
        if finder.found:
            return True
    return False


def _calls_with_paths(
    stmt: ast.AST, iter_child_fields: Callable[[ast.AST], Iterable[Tuple[str, Any]]]
) -> List[Tuple[Tuple[Any, ...], ast.Call]]:
    """Every call in ``stmt`` with its field-and-index path from the statement root."""
    result: List[Tuple[Tuple[Any, ...], ast.Call]] = []

    def walk(node: ast.AST, path: Tuple[Any, ...]) -> None:
        if isinstance(node, ast.Call):
            result.append((path, node))
        for field_name, value in iter_child_fields(node):
            if isinstance(value, list):
                for i, item in enumerate(value):
                    if isinstance(item, ast.AST):
                        walk(item, path + (field_name, i))
            elif isinstance(value, ast.AST):
                walk(value, path + (field_name,))

    walk(stmt, ("$root",))
    return result


def _node_at_path(stmt: ast.AST, path: Tuple[Any, ...]) -> Optional[ast.AST]:
    """The node ``path`` (as ``_calls_with_paths`` spells it) names in ``stmt``, or None."""
    node: ast.AST = stmt
    idx = 1  # past "$root"
    while idx < len(path):
        part = path[idx]
        if not isinstance(part, str):
            return None
        field = part
        idx += 1
        if idx < len(path) and isinstance(path[idx], int):
            list_index = path[idx]
            idx += 1
        else:
            list_index = None
        value = getattr(node, field, None)
        if list_index is None:
            if not isinstance(value, ast.AST):
                return None
            node = value
        else:
            if not isinstance(value, list) or list_index >= len(value):
                return None
            next_node = value[list_index]
            if not isinstance(next_node, ast.AST):
                return None
            node = next_node
    return node


class LiteralPromotion(UnifierState):
    """See the module docstring."""

    def _promote_hof_literals(
        self, blocks: Sequence[Sequence[ast.AST]], substitution: Substitution
    ) -> None:
        """
        Promote literal arguments in higher-order factory calls into parameters,
        even when equal across blocks (Option B).

        Heuristic:
        - Look for assignments of the form: name = Call(...)
        - If 'name' is later used in Call position (i.e., as a callee), treat the
          assignment as constructing a higher-order function.
        - For each Constant argument to that Call, promote it to a parameter by
          mapping the corresponding per-block expression to a fresh parameter name.

        Implementation notes:
        - Uses block 0 as the structural template and assumes unified blocks share
          the same AST shape for corresponding statements.
        - Skips any expression already parameterized by the unifier.
        - Generates fresh parameter names via self.param_counter to avoid collisions
          with existing unified parameters (names like __param_N).
        """

        if not blocks or len(blocks) < 2:
            return
        # Iterate over statements in block 0 and attempt promotions
        for stmt_idx, stmt0 in enumerate(blocks[0]):
            # Consider only simple assignments to a single Name
            if not isinstance(stmt0, ast.Assign) or len(stmt0.targets) != 1:
                continue
            target0 = stmt0.targets[0]
            if not isinstance(target0, ast.Name):
                continue
            if not isinstance(stmt0.value, ast.Call):
                continue

            target_name = target0.id
            # Only promote when the assigned variable is used later in a call context
            if not _is_used_as_callable_or_value_later(blocks[0], stmt_idx, target_name):
                continue

            # For each arg in the call in block 0, if Constant, attempt to promote
            call_paths = _calls_with_paths(stmt0, self._iter_child_fields)
            # Since stmt0.value is a Call, find its path (should exist)
            call_path = None
            for path, call_node in call_paths:
                if call_node is stmt0.value:
                    call_path = path
                    break
            if call_path is None:
                continue

            for arg_pos, arg0 in enumerate(stmt0.value.args):
                if isinstance(arg0, ast.Constant):
                    self._promote_argument(
                        blocks, stmt_idx, call_path + ("args", arg_pos), substitution
                    )

    def _promote_argument(
        self,
        blocks: Sequence[Sequence[ast.AST]],
        stmt_idx: int,
        arg_path: Tuple[Any, ...],
        substitution: Substitution,
    ) -> None:
        """Promote the literal at ``arg_path`` of statement ``stmt_idx`` to a fresh parameter.

        Every block must carry an expression at that path, none of them may
        already be parameterized, and no tool may read one where it stands;
        then each block's expression is mapped to one new parameter and
        recorded as its promoted argument.
        """
        per_block_exprs: List[ast.AST] = []
        for block in blocks:
            statement = block[stmt_idx] if stmt_idx < len(block) else None
            if not isinstance(statement, ast.Assign):
                return
            node = _node_at_path(statement, arg_path)
            if node is None or not isinstance(node, ast.expr):
                return
            per_block_exprs.append(node)
        if any(
            substitution.get_param_for_expr(bidx, expr) is not None
            for bidx, expr in enumerate(per_block_exprs)
        ):
            return
        if self._read_where_it_stands(per_block_exprs, range(len(per_block_exprs))):
            return
        param_name = self._fresh_parameter_name()
        for bidx, expr in enumerate(per_block_exprs):
            substitution.add_mapping(bidx, expr, param_name, bound_vars=None)
        promoted = substitution.promoted_literal_args.setdefault(param_name, {})
        for bidx, expr in enumerate(per_block_exprs):
            promoted[bidx] = expr
