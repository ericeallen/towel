# Copyright 2025 Eric Allen
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

"""
AST Normalizer - Convert assignments to augmented assignments when appropriate.

This module provides a normalization pass that identifies assignments of the form:
    x = x + y
and converts them to augmented assignments:
    x += y

This helps the unification algorithm distinguish between fresh bindings and mutations.
"""

import ast
from typing import Set, cast
from .visitor_utils import make_defensive_generic_visit


class AssignToAugAssignNormalizer(ast.NodeTransformer):
    """
    Normalize assignments to augmented assignments when the LHS variable
    appears on the RHS and is already in scope.

    Example transformations:
        x = x + 1    →  x += 1
        result = result + 10  →  result += 10
        output = output - 5   →  output -= 5
    """

    generic_visit = make_defensive_generic_visit("AssignToAugAssignNormalizer")  # type: ignore[assignment]

    def __init__(self) -> None:
        self.scopes: list[Set[str]] = [set()]  # Stack of scopes

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
        """Enter a new scope for function definitions."""
        # Add function parameters to scope
        new_scope = set()
        for arg in node.args.args:
            new_scope.add(arg.arg)

        self.scopes.append(new_scope)

        # Visit the body
        node.body = [self.visit(stmt) for stmt in node.body]

        # Exit scope
        self.scopes.pop()

        return node

    def visit_Assign(self, node: ast.Assign) -> ast.AST:
        """
        Check if this assignment should be converted to an augmented assignment.

        Conditions for conversion:
        1. Single target (e.g., x = ...)
        2. Target is a simple Name node (not subscript or attribute)
        3. Target variable is already in scope
        4. Value is a BinOp where one operand is the target variable
        """
        # Visit children first
        node = cast(ast.Assign, self.generic_visit(node))

        # Check if we have a single target
        if len(node.targets) != 1:
            return node

        target = node.targets[0]

        # Check if target is a simple Name node
        if not isinstance(target, ast.Name):
            return node

        target_name = target.id

        # Check if target is already in scope
        if not self._is_in_scope(target_name):
            # This is a fresh binding, add to current scope
            self.scopes[-1].add(target_name)
            return node

        # Target is in scope, check if value is a BinOp with target on LHS
        if not isinstance(node.value, ast.BinOp):
            return node

        binop = node.value

        # Check if left operand is the target variable
        if isinstance(binop.left, ast.Name) and binop.left.id == target_name:
            # Convert to augmented assignment: x = x + y  →  x += y
            aug_assign = ast.AugAssign(
                target=ast.Name(id=target_name, ctx=ast.Store()), op=binop.op, value=binop.right
            )
            return ast.copy_location(aug_assign, node)

        # Check if right operand is the target variable (for commutative ops)
        if (
            self._is_commutative(binop.op)
            and isinstance(binop.right, ast.Name)
            and binop.right.id == target_name
        ):
            # Convert: x = y + x  →  x += y
            aug_assign = ast.AugAssign(
                target=ast.Name(id=target_name, ctx=ast.Store()), op=binop.op, value=binop.left
            )
            return ast.copy_location(aug_assign, node)

        return node

    def visit_AugAssign(self, node: ast.AugAssign) -> ast.AugAssign:
        """Visit augmented assignment and track the variable."""
        # The target of an augmented assignment must already be in scope
        # (Python will raise NameError if it's not)
        if isinstance(node.target, ast.Name):
            # Ensure it's in scope (should already be, but add it if not)
            self.scopes[-1].add(node.target.id)

        return cast(ast.AugAssign, self.generic_visit(node))

    def visit_For(self, node: ast.For) -> ast.For:
        """Track loop variables."""
        if isinstance(node.target, ast.Name):
            self.scopes[-1].add(node.target.id)
        return cast(ast.For, self.generic_visit(node))

    def visit_With(self, node: ast.With) -> ast.With:
        """Track context manager variables."""
        for item in node.items:
            if item.optional_vars and isinstance(item.optional_vars, ast.Name):
                self.scopes[-1].add(item.optional_vars.id)
        return cast(ast.With, self.generic_visit(node))

    def visit_comprehension(self, node: ast.comprehension) -> ast.comprehension:
        """Track comprehension variables."""
        if isinstance(node.target, ast.Name):
            self.scopes[-1].add(node.target.id)
        return cast(ast.comprehension, self.generic_visit(node))

    def _is_in_scope(self, name: str) -> bool:
        """Check if a variable is in any of the current scopes."""
        for scope in self.scopes:
            if name in scope:
                return True
        return False

    def _is_commutative(self, op: ast.operator) -> bool:
        """Check if an operator is commutative."""
        return isinstance(op, (ast.Add, ast.Mult, ast.BitOr, ast.BitXor, ast.BitAnd))


def normalize_assigns_to_augassigns(tree: ast.AST) -> ast.AST:
    """
    Normalize an AST by converting assignments to augmented assignments
    where appropriate.

    Args:
        tree: The AST to normalize

    Returns:
        A new AST with assignments normalized to augmented assignments
    """
    normalizer = AssignToAugAssignNormalizer()
    return cast(ast.AST, normalizer.visit(tree))


class ArithmeticCanonicalizer(ast.NodeTransformer):
    """Canonicalize arithmetic expressions for easier unification."""

    generic_visit = make_defensive_generic_visit("ArithmeticCanonicalizer")  # type: ignore[assignment]

    def visit_UnaryOp(self, node: ast.UnaryOp) -> ast.AST:  # noqa: N802
        node = cast(ast.UnaryOp, self.generic_visit(node))
        if isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant):
            value = node.operand.value
            if isinstance(value, (int, float, complex)):
                return ast.copy_location(ast.Constant(value=-value), node)
        return node

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:  # noqa: N802
        node = cast(ast.BinOp, self.generic_visit(node))
        if isinstance(node.op, ast.Sub) and isinstance(node.right, ast.Constant):
            value = node.right.value
            if isinstance(value, (int, float, complex)):
                new_const = ast.Constant(value=-value)
                new_node = ast.BinOp(left=node.left, op=ast.Add(), right=new_const)
                return ast.copy_location(new_node, node)
        return node


def canonicalize_arithmetic(tree: ast.AST) -> ast.AST:
    """Apply arithmetic canonicalization for additive/subtractive expressions."""

    canon = ArithmeticCanonicalizer()
    return cast(ast.AST, canon.visit(tree))


def normalize_code(code: str) -> str:
    """
    Normalize Python code by converting assignments to augmented assignments.

    Args:
        code: Python source code

    Returns:
        Normalized Python source code
    """
    tree = ast.parse(code)
    normalized_tree = normalize_assigns_to_augassigns(tree)
    normalized_tree = canonicalize_arithmetic(normalized_tree)
    return ast.unparse(normalized_tree)
