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

"""Parameters, and the alpha renaming of the binders that are not parameters.

A differing sub-expression becomes a helper parameter when it is a value
that can be passed (never a slice, a starred item, or an expression that
binds); names the blocks bind at the same position are alpha-renamed to one
canonical spelling instead, so ``result`` in one block and ``output`` in the
other are the same variable, not a parameter.
"""

from __future__ import annotations

import ast

from typing import Dict, List, Sequence, Set, Tuple, Optional
from weakref import WeakKeyDictionary
from .parameters import fresh_parameter_name
from .scope_analyzer import ScopeAnalyzer
from ..diagnostics import UNIFIER

from .substitution import Substitution
from .binding_context import bound_variables_in_block, get_free_variables
from .statement_facts import memoized_per_node
from .unifier_state import UnifierState


def _named_expr_targets(statement: ast.AST) -> Tuple[ast.AST, ...]:
    """Walrus targets in ``statement`` that bind in its own scope, in source order.

    An assignment expression inside a lambda, function, or class body binds
    there instead, so those scopes are not entered; one inside a
    comprehension binds in the enclosing scope and is included. Memoized per
    statement: every unification of a block asks this of each statement.
    """
    return memoized_per_node(_NAMED_EXPR_TARGETS, statement, _compute_named_expr_targets)


_NAMED_EXPR_TARGETS: "WeakKeyDictionary[ast.AST, Tuple[ast.AST, ...]]" = WeakKeyDictionary()


def _compute_named_expr_targets(statement: ast.AST) -> Tuple[ast.AST, ...]:
    found: List[ast.AST] = []
    pending: List[ast.AST] = [statement]
    while pending:
        node = pending.pop()
        if node is not statement and isinstance(
            node, (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        if isinstance(node, ast.NamedExpr):
            found.append(node.target)
        pending.extend(reversed(list(ast.iter_child_nodes(node))))
    return tuple(found)


def _parameterizable(exprs: Sequence[ast.AST]) -> bool:
    """Whether the differing nodes are values a parameter can stand for.

    Only expressions qualify, and not a whole f-string (its literal parts
    must stay), a slice or starred item (fragments of their container), or
    anything holding an assignment expression (which would bind inside a
    thunk instead of the caller). A statement, import alias, argument,
    keyword or with-item that differs is a different binding or signature.
    """
    if any(isinstance(expr, (ast.JoinedStr, ast.stmt, ast.Slice, ast.Starred)) for expr in exprs):
        return False
    if any(not isinstance(expr, ast.expr) for expr in exprs):
        return False
    return not any(isinstance(node, ast.NamedExpr) for expr in exprs for node in ast.walk(expr))


class Parameterization(UnifierState):
    """See the module docstring."""

    def _try_parameterize(
        self, exprs: Sequence[ast.AST], substitution: Substitution, block_indices: Sequence[int]
    ) -> bool:
        """
        Try to parameterize differing expressions.

        Args:
            exprs: List of expressions that differ
            substitution: Current substitution
            block_indices: Block index for each expression

        Returns:
            True if parameterization succeeded
        """
        if not _parameterizable(exprs):
            return False
        # Expressions already mapped must all map to one parameter.
        existing_params = [
            substitution.get_param_for_expr(idx, expr) for idx, expr in zip(block_indices, exprs)
        ]
        if all(p is not None for p in existing_params):
            return len(set(existing_params)) == 1
        if len(substitution.param_expressions) >= self.max_parameters:
            return False
        common_bound_vars = self._common_bound_variables(exprs, block_indices)
        if common_bound_vars:
            # A thunk can only close over names the call site can pass: the
            # block's free variables, not a comprehension's own targets.
            if not all(
                set(common_bound_vars) <= self._block_free_variables(idx) for idx in block_indices
            ):
                return False
        else:
            # A bare name passed as an argument must exist at the call site.
            for idx, expr in zip(block_indices, exprs):
                if isinstance(expr, ast.Name) and expr.id not in self._block_free_variables(idx):
                    UNIFIER.debug(
                        "Skipping refactoring: variable %r is not accessible at function scope; "
                        "it may be defined inside a nested function or be unbound.",
                        expr.id,
                    )
                    return False
        param_name = self._fresh_parameter_name()
        for idx, expr in zip(block_indices, exprs):
            substitution.add_mapping(idx, expr, param_name, bound_vars=common_bound_vars)
        return True

    def _common_bound_variables(
        self, exprs: Sequence[ast.AST], block_indices: Sequence[int]
    ) -> Optional[List[str]]:
        """The block-bound names every expression reads, when they all read the same ones.

        An expression that reads a name its block binds (a loop or
        comprehension target, say) becomes a function parameter that takes
        those names; the expressions must agree on them. None when they do
        not, or when none of them reads a bound name.
        """
        bound_per_expr: List[List[str]] = []
        for idx, expr in zip(block_indices, exprs):
            if self.current_blocks is not None and idx < len(self.current_blocks):
                bound = bound_variables_in_block(self.current_blocks[idx], expr)
                bound_per_expr.append(sorted(bound & get_free_variables(expr)))
            else:
                bound_per_expr.append([])
        if bound_per_expr and len({tuple(bound) for bound in bound_per_expr}) == 1:
            return bound_per_expr[0] or None
        return None

    def _block_free_variables(self, idx: int) -> Set[str]:
        """The free variables of block ``idx``: the names its call site can supply."""
        if self.current_blocks is None or idx >= len(self.current_blocks):
            return set()
        return ScopeAnalyzer().get_free_variables(self.current_blocks[idx])

    def _setup_bound_variable_alpha_renamings(self, blocks: Sequence[Sequence[ast.stmt]]) -> None:
        """
        Setup alpha-renamings for block-level bound variables.

        This allows unification of blocks with structurally identical code but different
        bound variable names (e.g., 'result' vs 'output').

        Strategy:
        1. For each block, collect variables that are first bound (assigned) in that block
        2. Match variables across blocks by their structural position (where they're first assigned)
        3. Add alpha-renamings to map corresponding variables to canonical names

        Example:
            Block1: result = x + 1; result = result + 2; return result
            Block2: output = y + 1; output = output + 2; return output

            -> result and output both bound at position (0, 0)
            -> Add mapping: result → temp, output → temp
            -> Existing Name node handling will use these mappings
        """
        # Collect binding information for each block
        binding_info = []
        for block_idx, block in enumerate(blocks):
            bindings = {}  # var_name → (stmt_idx, target_idx)
            seen_vars = set()

            for stmt_idx, stmt in enumerate(block):
                # Assignment targets first, then walrus targets in source order:
                # an assignment expression binds in the enclosing scope, so its
                # name is a block-level binding like any assignment's.
                targets = [*self._get_assignment_targets(stmt), *_named_expr_targets(stmt)]

                for target_idx, target in enumerate(targets):
                    if isinstance(target, ast.Name) and isinstance(target.ctx, ast.Store):
                        var_name = target.id
                        # Track first binding position
                        if var_name not in seen_vars:
                            bindings[var_name] = (stmt_idx, target_idx)
                            seen_vars.add(var_name)

            binding_info.append(bindings)

        # Match variables across blocks by structural position
        # position_to_vars maps (stmt_idx, target_idx) → list of (block_idx, var_name) pairs
        position_to_vars: Dict[Tuple[int, int], List[Tuple[int, str]]] = {}

        for block_idx, bindings in enumerate(binding_info):
            for var_name, position in bindings.items():
                if position not in position_to_vars:
                    position_to_vars[position] = []
                position_to_vars[position].append((block_idx, var_name))

        # Generate alpha-renamings for variables at same position
        canonical_counter = 0
        used_canonical_names = set()

        for position, var_list in position_to_vars.items():
            # Only create alpha-renaming if multiple blocks have a variable at this position
            if len(var_list) < 2:
                continue

            # Check if variables at this position have different names
            var_names = [var_name for _, var_name in var_list]
            if len(set(var_names)) <= 1:
                # All same name - no renaming needed
                continue

            # Generate canonical name
            canonical_name = f"__temp_{canonical_counter}"
            while canonical_name in used_canonical_names:
                canonical_counter += 1
                canonical_name = f"__temp_{canonical_counter}"
            used_canonical_names.add(canonical_name)
            canonical_counter += 1

            # Add alpha-renamings for all blocks
            for block_idx, var_name in var_list:
                key = (block_idx, var_name)
                self.alpha_renamings[key] = canonical_name

    def _get_assignment_targets(self, stmt: ast.AST) -> List[ast.AST]:
        """
        Extract assignment targets from a statement.

        Returns list of target AST nodes (may be Name, Tuple, List, etc.)
        """
        targets = []

        if isinstance(stmt, ast.Assign):
            # Regular assignment: x = 1 or x, y = 1, 2
            for target in stmt.targets:
                targets.extend(self._flatten_assignment_target(target))
        elif isinstance(stmt, ast.AugAssign):
            # Augmented assignment: x += 1
            targets.append(stmt.target)
        elif isinstance(stmt, ast.AnnAssign):
            # Annotated assignment: x: int = 1
            if stmt.target:
                targets.append(stmt.target)
        elif isinstance(stmt, ast.With):
            # With bindings: with expr as target
            for item in stmt.items:
                if item.optional_vars is not None:
                    targets.extend(self._flatten_assignment_target(item.optional_vars))
        elif isinstance(stmt, ast.Try):
            # Except handler names are bindings
            for handler in stmt.handlers:
                if handler.name:
                    targets.append(ast.Name(id=handler.name, ctx=ast.Store()))

        return targets

    def _flatten_assignment_target(self, target: ast.AST) -> List[ast.AST]:
        """
        Flatten assignment target to individual names.

        Examples:
            x → [x]
            (x, y) → [x, y]
            [x, (y, z)] → [x, y, z]
        """
        if isinstance(target, ast.Name):
            return [target]
        elif isinstance(target, (ast.Tuple, ast.List)):
            result = []
            for elt in target.elts:
                result.extend(self._flatten_assignment_target(elt))
            return result
        else:
            # Other complex targets (subscript, attribute, etc.) - don't extract names
            return []

    def _fresh_parameter_name(self) -> str:
        """Return the next ``__param_N`` that no block identifier already uses."""
        reserved: Set[str] = getattr(self, "_reserved_parameter_names", set())
        name, self.param_counter = fresh_parameter_name(reserved, self.param_counter)
        return name

    def _assign_alpha_mapping(
        self,
        key: Tuple[int, str],
        canonical: str,
        old_mappings: Dict[Tuple[int, str], str],
    ) -> None:
        if key in self.alpha_renamings:
            old_mappings[key] = self.alpha_renamings[key]
        self.alpha_renamings[key] = canonical

    def _restore_alpha_mapping(
        self,
        key: Tuple[int, str],
        old_mappings: Dict[Tuple[int, str], str],
    ) -> None:
        if key in old_mappings:
            self.alpha_renamings[key] = old_mappings[key]
        else:
            self.alpha_renamings.pop(key, None)
