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

from typing import Dict, List, Sequence, Set, Tuple
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


class Parameterization(UnifierState):
    """See the module docstring."""

    def _try_parameterize(
        self, exprs: Sequence[ast.AST], subst: Substitution, block_indices: Sequence[int]
    ) -> bool:
        """
        Try to parameterize differing expressions.

        Args:
            exprs: List of expressions that differ
            subst: Current substitution
            block_indices: Block index for each expression

        Returns:
            True if parameterization succeeded
        """
        # F-strings (JoinedStr) must not be parameterized as a whole; only their
        # internal expressions (FormattedValue.value) are eligible. Prevent turning
        # entire f-strings into a single parameter to preserve structure.
        if any(isinstance(expr, ast.JoinedStr) for expr in exprs):
            return False

        # CRITICAL: Cannot parameterize statement nodes (only expression nodes)
        # Statements (If, For, FunctionDef, etc.) must have the same type to unify
        # Only expressions (Name, Constant, Call, etc.) can be parameterized
        if any(isinstance(expr, ast.stmt) for expr in exprs):
            return False
        # Only an expression can be replaced by a parameter. An import alias,
        # an argument, a keyword or a with-item that differs is a different
        # binding or a different signature, not a value (jsonschema: two
        # ``from jsonschema import X`` with different X).
        if any(not isinstance(expr, ast.expr) for expr in exprs):
            return False

        # Slices and starred items are syntax fragments of their container, not
        # values: ``seq[start:stop]`` versus ``seq[i]`` cannot share a parameter.
        if any(isinstance(expr, (ast.Slice, ast.Starred)) for expr in exprs):
            return False

        # An assignment expression binds in the scope that evaluates it. A
        # thunk would bind it in the lambda instead of the caller.
        if any(isinstance(node, ast.NamedExpr) for expr in exprs for node in ast.walk(expr)):
            return False

        # Check if all expressions are already mapped to the same parameter
        existing_params = [
            subst.get_param_for_expr(idx, expr) for idx, expr in zip(block_indices, exprs)
        ]

        if all(p is not None for p in existing_params):
            # All mapped - check they map to the same parameter
            if len(set(existing_params)) == 1:
                return True
            else:
                # Mapped to different parameters - can't unify
                return False

        # Check we haven't exceeded max parameters
        if len(subst.param_expressions) >= self.max_parameters:
            return False

        # Analyze bound variables in each expression
        # For each expression, find which variables it references that are bound in context
        bound_vars_per_expr = []
        for idx, expr in zip(block_indices, exprs):
            if self.current_blocks and idx < len(self.current_blocks):
                # Find which variables in the expression are bound in the block context
                bound_in_context = bound_variables_in_block(self.current_blocks[idx], expr)
                # Get variables referenced in the expression
                vars_in_expr = get_free_variables(expr)
                # Intersection: bound variables that are actually used in expression
                bound_vars = bound_in_context & vars_in_expr

                bound_vars_per_expr.append(bound_vars)
            else:
                bound_vars_per_expr.append(set())

        # Check if all expressions reference the same bound variables
        # If they do, this should be a function parameter
        all_bound_vars = [sorted(bv) for bv in bound_vars_per_expr]

        # Determine common bound variables (should be same across all expressions)
        if all_bound_vars and len(set(tuple(bv) for bv in all_bound_vars)) == 1:
            # All expressions reference the same set of bound variables
            common_bound_vars = all_bound_vars[0] if all_bound_vars[0] else None
        else:
            # Different bound variables - use None (not a function parameter)
            common_bound_vars = None

        # CRITICAL: Check if bound variables are accessible at call site
        # Comprehension variables (for r in results) are NOT accessible at call site
        # Only function-level free variables can be lambda-lifted
        if common_bound_vars:
            # Check if these bound variables are accessible at the function call site
            # (i.e., they're free variables of the block, not just comprehension variables)
            for idx, expr in zip(block_indices, exprs):
                if self.current_blocks is not None and idx < len(self.current_blocks):
                    block_free_vars = ScopeAnalyzer().get_free_variables(self.current_blocks[idx])

                    # Check if all bound variables used in the expression are free variables
                    for var in common_bound_vars:
                        if var not in block_free_vars:
                            # Bound variable (like comprehension var) not accessible at call site
                            # Cannot lambda-lift - refuse to parameterize
                            return False

        # CRITICAL: If expressions are simple names without bound variables,
        # they must be validated - they need to exist at the call site
        # (Unless they're being lambda-lifted, in which case common_bound_vars is not None)
        if common_bound_vars is None:
            # Not using lambda lifting - check if expressions reference undefined variables
            for idx, expr in zip(block_indices, exprs):
                if isinstance(expr, ast.Name):
                    # Simple variable reference - needs to exist at call site
                    # Get free variables of the entire block to see what's available
                    if self.current_blocks is not None and idx < len(self.current_blocks):
                        block_free_vars = ScopeAnalyzer().get_free_variables(
                            self.current_blocks[idx]
                        )

                        # Check if this variable is available at call site
                        if expr.id not in block_free_vars:
                            # Variable not available at call site - can't parameterize
                            UNIFIER.debug(
                                f"Skipping refactoring: Variable '{expr.id}' is not accessible at function scope. "
                                f"It may be defined inside a nested function or be an unbound variable reference."
                            )
                            return False

        # Create a new parameter
        # Use __ prefix to avoid name collisions (Python convention)
        param_name = self._fresh_parameter_name()

        # Add mappings for each block
        for idx, expr in zip(block_indices, exprs):
            subst.add_mapping(idx, expr, param_name, bound_vars=common_bound_vars)

        return True

    def _setup_bound_variable_alpha_renamings(self, blocks: Sequence[Sequence[ast.AST]]) -> None:
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
