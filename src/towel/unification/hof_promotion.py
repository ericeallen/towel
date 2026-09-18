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
``promote_equal_hof_literals``, off by default, and it is rolled back whole
if it fails.
"""

from __future__ import annotations

import ast

from typing import Any, List, Optional, Tuple, cast, Sequence
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


class LiteralPromotion(UnifierState):
    """See the module docstring."""

    def _promote_hof_literals(
        self, blocks: Sequence[Sequence[ast.AST]], subst: Substitution
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

        num_blocks = len(blocks)

        # Utility: collect whether a variable is used later in a Call context (as a callee or as an argument)
        def is_used_as_callable_or_value_later(
            block: Sequence[ast.AST], start_stmt_idx: int, var_name: str
        ) -> bool:

            finder = _CallContextFinder(var_name)
            for sidx in range(start_stmt_idx + 1, len(block)):
                finder.visit(block[sidx])
                if finder.found:
                    return True
            return False

        # Utility: yield all (path, call_node) pairs within a statement in block 0
        def iter_calls_with_paths(stmt: ast.AST) -> List[Tuple[Tuple[Any, ...], ast.Call]]:
            result: List[Tuple[Tuple[Any, ...], ast.Call]] = []

            def walk(node: ast.AST, path: Tuple[Any, ...]) -> None:
                if isinstance(node, ast.Call):
                    result.append((path, node))
                for field_name, value in self._iter_child_fields(node):
                    if isinstance(value, list):
                        for i, item in enumerate(value):
                            if isinstance(item, ast.AST):
                                walk(item, path + (field_name, i))
                    elif isinstance(value, ast.AST):
                        walk(value, path + (field_name,))

            walk(stmt, ("$root",))
            return result

        # Utility: follow a path within a statement to retrieve the corresponding node
        def get_node_by_path(stmt: ast.AST, path: Tuple[Any, ...]) -> Optional[ast.AST]:
            node: ast.AST = stmt
            # path starts with ("$root",), skip first marker
            for p in path[1:]:
                if isinstance(p, str):
                    if not hasattr(node, p):
                        return None
                    node = getattr(node, p)
                elif isinstance(p, int):
                    # indexing into a list; prior element must have been a list field
                    # find the previous step to access the list; handled by caller structure
                    return None  # we only use (field, index) pairs, so int alone shouldn't appear
                else:
                    # we expect (field_name, index) pairs encoded sequentially
                    return None
            return node

        # Utility: get child by (field, index) sequence from current node
        def get_node_by_field_index_path(stmt: ast.AST, path: Tuple[Any, ...]) -> Optional[ast.AST]:
            node: ast.AST = stmt
            # Skip "$root"
            idx = 1
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
            if not is_used_as_callable_or_value_later(blocks[0], stmt_idx, target_name):
                continue

            # For each arg in the call in block 0, if Constant, attempt to promote
            call_paths = iter_calls_with_paths(stmt0)
            # Find the specific call path corresponding to stmt0.value
            # Since stmt0.value is a Call, find its path (should exist)
            call_path = None
            for path, call_node in call_paths:
                if call_node is stmt0.value:
                    call_path = path
                    break
            if call_path is None:
                continue

            call0 = stmt0.value
            for arg_pos, arg0 in enumerate(call0.args):
                if not isinstance(arg0, ast.AST):
                    continue
                # Only consider literal constants for now
                if not isinstance(arg0, ast.Constant):
                    continue

                # Build path to this arg: call_path + ("args", arg_pos)
                arg_path = call_path + ("args", arg_pos)

                # Collect per-block corresponding arg expressions
                per_block_exprs: List[Optional[ast.AST]] = []
                missing = False
                for bidx in range(num_blocks):
                    stmt_b = blocks[bidx][stmt_idx] if stmt_idx < len(blocks[bidx]) else None
                    if not isinstance(stmt_b, ast.Assign):
                        missing = True
                        break
                    val_b = stmt_b.value
                    if not isinstance(val_b, ast.AST):
                        missing = True
                        break
                    # Retrieve the node at arg_path within this statement
                    node_b = get_node_by_field_index_path(stmt_b, arg_path)
                    if node_b is None or not isinstance(node_b, ast.expr):
                        missing = True
                        break
                    per_block_exprs.append(node_b)

                if missing or len(per_block_exprs) != num_blocks:
                    continue

                # At this point, all per_block_exprs are non-None (validated above)
                valid_exprs = cast(List[ast.AST], per_block_exprs)

                # Skip if any of these expressions are already parameterized
                already_param = False
                for bidx, expr_b in enumerate(valid_exprs):
                    if subst.get_param_for_expr(bidx, expr_b) is not None:
                        already_param = True
                        break
                if already_param:
                    continue

                # Create fresh parameter name and record mappings for all blocks
                param_name = self._fresh_parameter_name()

                for bidx, expr_b in enumerate(valid_exprs):
                    subst.add_mapping(bidx, expr_b, param_name, bound_vars=None)

                # Also store per-block expr under promoted_literal_args for clarity
                promoted = subst.promoted_literal_args.setdefault(param_name, {})
                for bidx, expr_b in enumerate(valid_exprs):
                    promoted[bidx] = expr_b
