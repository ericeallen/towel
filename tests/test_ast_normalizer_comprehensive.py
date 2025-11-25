#!/usr/bin/env python3
"""
Comprehensive unit tests for the ast_normalizer module.

These tests isolate and test individual functions and methods to ensure
correct behavior of assignment to augmented assignment normalization.
"""

import unittest
import ast
from src.towel.unification.ast_normalizer import (
    AssignToAugAssignNormalizer,
    normalize_assigns_to_augassigns,
    normalize_code,
    canonicalize_arithmetic,
)


class TestBasicNormalization(unittest.TestCase):
    """Test basic normalization functionality."""

    def test_simple_addition_normalization(self):
        """Test x = x + 1 converts to x += 1."""
        code = """
def foo():
    x = 1
    x = x + 1
"""
        tree = ast.parse(code)
        normalized = normalize_assigns_to_augassigns(tree)

        # Find the second assignment in the function
        func = normalized.body[0]
        stmt = func.body[1]

        self.assertIsInstance(stmt, ast.AugAssign, "Should be augmented assignment")
        self.assertIsInstance(stmt.op, ast.Add, "Should use Add operator")

    def test_subtraction_normalization(self):
        """Test x = x - 5 converts to x -= 5."""
        code = """
def foo():
    x = 10
    x = x - 5
"""
        tree = ast.parse(code)
        normalized = normalize_assigns_to_augassigns(tree)

        func = normalized.body[0]
        stmt = func.body[1]

        self.assertIsInstance(stmt, ast.AugAssign, "Should be augmented assignment")
        self.assertIsInstance(stmt.op, ast.Sub, "Should use Sub operator")

    def test_multiplication_normalization(self):
        """Test x = x * 2 converts to x *= 2."""
        code = """
def foo():
    x = 5
    x = x * 2
"""
        tree = ast.parse(code)
        normalized = normalize_assigns_to_augassigns(tree)

        func = normalized.body[0]
        stmt = func.body[1]

        self.assertIsInstance(stmt, ast.AugAssign, "Should be augmented assignment")
        self.assertIsInstance(stmt.op, ast.Mult, "Should use Mult operator")

    def test_division_normalization(self):
        """Test x = x / 2 converts to x /= 2."""
        code = """
def foo():
    x = 10
    x = x / 2
"""
        tree = ast.parse(code)
        normalized = normalize_assigns_to_augassigns(tree)

        func = normalized.body[0]
        stmt = func.body[1]

        self.assertIsInstance(stmt, ast.AugAssign, "Should be augmented assignment")
        self.assertIsInstance(stmt.op, ast.Div, "Should use Div operator")


class TestCommutativeOperations(unittest.TestCase):
    """Test commutative operator handling."""

    def test_commutative_addition_right_side(self):
        """Test x = 1 + x converts to x += 1."""
        code = """
def foo():
    x = 5
    x = 1 + x
"""
        tree = ast.parse(code)
        normalized = normalize_assigns_to_augassigns(tree)

        func = normalized.body[0]
        stmt = func.body[1]

        self.assertIsInstance(stmt, ast.AugAssign, "Should be augmented assignment")
        self.assertIsInstance(stmt.op, ast.Add, "Should use Add operator")

    def test_commutative_multiplication_right_side(self):
        """Test x = 2 * x converts to x *= 2."""
        code = """
def foo():
    x = 5
    x = 2 * x
"""
        tree = ast.parse(code)
        normalized = normalize_assigns_to_augassigns(tree)

        func = normalized.body[0]
        stmt = func.body[1]

        self.assertIsInstance(stmt, ast.AugAssign, "Should be augmented assignment")
        self.assertIsInstance(stmt.op, ast.Mult, "Should use Mult operator")

    def test_non_commutative_subtraction_not_converted(self):
        """Test x = 10 - x does NOT convert (subtraction is not commutative)."""
        code = """
def foo():
    x = 5
    x = 10 - x
"""
        tree = ast.parse(code)
        normalized = normalize_assigns_to_augassigns(tree)

        func = normalized.body[0]
        stmt = func.body[1]

        # Should remain a regular assignment
        self.assertIsInstance(stmt, ast.Assign, "Should remain regular assignment")

    def test_non_commutative_division_not_converted(self):
        """Test x = 10 / x does NOT convert (division is not commutative)."""
        code = """
def foo():
    x = 2
    x = 10 / x
"""
        tree = ast.parse(code)
        normalized = normalize_assigns_to_augassigns(tree)

        func = normalized.body[0]
        stmt = func.body[1]

        # Should remain a regular assignment
        self.assertIsInstance(stmt, ast.Assign, "Should remain regular assignment")


class TestScopeTracking(unittest.TestCase):
    """Test scope tracking functionality."""

    def test_fresh_binding_not_converted(self):
        """Test that fresh bindings are not converted."""
        code = """
def foo():
    x = x + 1
"""
        tree = ast.parse(code)
        normalized = normalize_assigns_to_augassigns(tree)

        func = normalized.body[0]
        stmt = func.body[0]

        # Should remain a regular assignment (x is not in scope yet)
        self.assertIsInstance(stmt, ast.Assign, "Fresh binding should remain assignment")

    def test_parameter_is_in_scope(self):
        """Test that function parameters are tracked in scope."""
        code = """
def foo(x):
    x = x + 1
"""
        tree = ast.parse(code)
        normalized = normalize_assigns_to_augassigns(tree)

        func = normalized.body[0]
        stmt = func.body[0]

        # Should be converted (x is a parameter, so it's in scope)
        self.assertIsInstance(stmt, ast.AugAssign, "Parameter assignment should convert")

    def test_multiple_variables_tracked(self):
        """Test that multiple variables are tracked correctly."""
        code = """
def foo():
    x = 1
    y = 2
    x = x + 1
    y = y + 2
"""
        tree = ast.parse(code)
        normalized = normalize_assigns_to_augassigns(tree)

        func = normalized.body[0]
        x_stmt = func.body[2]
        y_stmt = func.body[3]

        self.assertIsInstance(x_stmt, ast.AugAssign, "x assignment should convert")
        self.assertIsInstance(y_stmt, ast.AugAssign, "y assignment should convert")

    def test_for_loop_variable_in_scope(self):
        """Test that for loop variables are tracked in scope."""
        code = """
def foo():
    for i in range(10):
        i = i + 1
"""
        tree = ast.parse(code)
        normalized = normalize_assigns_to_augassigns(tree)

        func = normalized.body[0]
        for_stmt = func.body[0]
        i_stmt = for_stmt.body[0]

        self.assertIsInstance(i_stmt, ast.AugAssign, "Loop variable assignment should convert")

    def test_with_variable_in_scope(self):
        """Test that with statement variables are tracked in scope."""
        code = """
def foo():
    with open('file.txt') as f:
        f = f + 'extra'
"""
        tree = ast.parse(code)
        normalized = normalize_assigns_to_augassigns(tree)

        func = normalized.body[0]
        with_stmt = func.body[0]
        f_stmt = with_stmt.body[0]

        self.assertIsInstance(f_stmt, ast.AugAssign, "With variable assignment should convert")


class TestNestedScopes(unittest.TestCase):
    """Test nested scope handling."""

    def test_nested_function_new_scope(self):
        """Test that nested functions have their own scope."""
        code = """
def outer():
    def inner():
        x = x + 1
    x = 5
    x = x + 1
"""
        tree = ast.parse(code)
        normalized = normalize_assigns_to_augassigns(tree)

        outer_func = normalized.body[0]
        inner_func = outer_func.body[0]
        inner_x_stmt = inner_func.body[0]
        outer_x_stmt = outer_func.body[2]

        # Inner function's x assignment should NOT convert (x not in scope)
        self.assertIsInstance(inner_x_stmt, ast.Assign, "Inner x should remain assignment")

        # Outer function's x assignment should convert (x was bound)
        self.assertIsInstance(outer_x_stmt, ast.AugAssign, "Outer x should convert")

    def test_outer_variable_visible_in_nested(self):
        """Test that outer variables are visible in nested scopes."""
        code = """
def outer(x):
    def inner():
        pass
    x = x + 1
"""
        tree = ast.parse(code)
        normalized = normalize_assigns_to_augassigns(tree)

        outer_func = normalized.body[0]
        x_stmt = outer_func.body[1]

        # x is a parameter, so assignment should convert
        self.assertIsInstance(x_stmt, ast.AugAssign, "Parameter assignment should convert")


class TestEdgeCases(unittest.TestCase):
    """Test edge cases and boundary conditions."""

    def test_multiple_targets_not_converted(self):
        """Test that multi-target assignments are not converted."""
        code = """
def foo():
    x = y = 1
    x = y = x + 1
"""
        tree = ast.parse(code)
        normalized = normalize_assigns_to_augassigns(tree)

        func = normalized.body[0]
        stmt = func.body[1]

        # Should NOT convert (multiple targets)
        self.assertIsInstance(stmt, ast.Assign, "Multi-target should remain assignment")

    def test_attribute_assignment_not_converted(self):
        """Test that attribute assignments are not converted."""
        code = """
def foo():
    obj.x = obj.x + 1
"""
        tree = ast.parse(code)
        normalized = normalize_assigns_to_augassigns(tree)

        func = normalized.body[0]
        stmt = func.body[0]

        # Should NOT convert (target is attribute, not simple name)
        self.assertIsInstance(stmt, ast.Assign, "Attribute assignment should remain assignment")

    def test_subscript_assignment_not_converted(self):
        """Test that subscript assignments are not converted."""
        code = """
def foo():
    lst = [1, 2, 3]
    lst[0] = lst[0] + 1
"""
        tree = ast.parse(code)
        normalized = normalize_assigns_to_augassigns(tree)

        func = normalized.body[0]
        stmt = func.body[1]

        # Should NOT convert (target is subscript, not simple name)
        self.assertIsInstance(stmt, ast.Assign, "Subscript assignment should remain assignment")

    def test_complex_rhs_expression(self):
        """Test that complex RHS expressions work correctly."""
        code = """
def foo():
    x = 1
    y = 2
    x = x + (y * 3)
"""
        tree = ast.parse(code)
        normalized = normalize_assigns_to_augassigns(tree)

        func = normalized.body[0]
        stmt = func.body[2]

        self.assertIsInstance(stmt, ast.AugAssign, "Should convert despite complex RHS")

    def test_variable_not_on_binop_side(self):
        """Test x = y + z does NOT convert."""
        code = """
def foo():
    x = 1
    y = 2
    z = 3
    x = y + z
"""
        tree = ast.parse(code)
        normalized = normalize_assigns_to_augassigns(tree)

        func = normalized.body[0]
        stmt = func.body[3]

        # Should NOT convert (x is not in y + z)
        self.assertIsInstance(stmt, ast.Assign, "Should remain assignment")

    def test_augmented_assignment_tracked(self):
        """Test that existing augmented assignments are tracked."""
        code = """
def foo():
    x = 1
    x += 1
    x = x + 1
"""
        tree = ast.parse(code)
        normalized = normalize_assigns_to_augassigns(tree)

        func = normalized.body[0]
        aug_stmt = func.body[1]
        assign_stmt = func.body[2]

        self.assertIsInstance(aug_stmt, ast.AugAssign, "Should remain augmented assignment")
        self.assertIsInstance(assign_stmt, ast.AugAssign, "Should convert to augmented assignment")


class TestArithmeticCanonicalization(unittest.TestCase):
    """Test canonicalization of additive/subtractive expressions."""

    def test_subtraction_with_constant_becomes_add_negative(self):
        code = """
def foo(x):
    return x * 3 - 5
"""
        tree = ast.parse(code)
        canon = canonicalize_arithmetic(tree)

        func = canon.body[0]
        ret = func.body[0]
        expr = ret.value

        self.assertIsInstance(expr, ast.BinOp, "Expression should remain BinOp")
        self.assertIsInstance(expr.op, ast.Add, "Subtraction should convert to addition")
        self.assertIsInstance(expr.right, ast.Constant, "Right operand should be folded constant")
        self.assertEqual(expr.right.value, -5)

    def test_unary_minus_constant_folded_into_constant(self):
        code = """
def foo():
    return -10
"""
        tree = ast.parse(code)
        canon = canonicalize_arithmetic(tree)

        func = canon.body[0]
        ret = func.body[0]
        expr = ret.value

        self.assertIsInstance(expr, ast.Constant, "Unary minus constant should fold into Constant")
        self.assertEqual(expr.value, -10)


class TestBitwiseOperators(unittest.TestCase):
    """Test bitwise operator normalization."""

    def test_bitwise_or(self):
        """Test x = x | 1 converts to x |= 1."""
        code = """
def foo():
    x = 0
    x = x | 1
"""
        tree = ast.parse(code)
        normalized = normalize_assigns_to_augassigns(tree)

        func = normalized.body[0]
        stmt = func.body[1]

        self.assertIsInstance(stmt, ast.AugAssign, "Should be augmented assignment")
        self.assertIsInstance(stmt.op, ast.BitOr, "Should use BitOr operator")

    def test_bitwise_and(self):
        """Test x = x & 1 converts to x &= 1."""
        code = """
def foo():
    x = 15
    x = x & 1
"""
        tree = ast.parse(code)
        normalized = normalize_assigns_to_augassigns(tree)

        func = normalized.body[0]
        stmt = func.body[1]

        self.assertIsInstance(stmt, ast.AugAssign, "Should be augmented assignment")
        self.assertIsInstance(stmt.op, ast.BitAnd, "Should use BitAnd operator")

    def test_bitwise_xor(self):
        """Test x = x ^ 1 converts to x ^= 1."""
        code = """
def foo():
    x = 5
    x = x ^ 1
"""
        tree = ast.parse(code)
        normalized = normalize_assigns_to_augassigns(tree)

        func = normalized.body[0]
        stmt = func.body[1]

        self.assertIsInstance(stmt, ast.AugAssign, "Should be augmented assignment")
        self.assertIsInstance(stmt.op, ast.BitXor, "Should use BitXor operator")

    def test_bitwise_commutative(self):
        """Test that bitwise operators work commutatively."""
        code = """
def foo():
    x = 5
    x = 1 | x
"""
        tree = ast.parse(code)
        normalized = normalize_assigns_to_augassigns(tree)

        func = normalized.body[0]
        stmt = func.body[1]

        self.assertIsInstance(stmt, ast.AugAssign, "Should convert commutatively")
        self.assertIsInstance(stmt.op, ast.BitOr, "Should use BitOr operator")


class TestNormalizeCode(unittest.TestCase):
    """Test the normalize_code convenience function."""

    def test_normalize_code_simple(self):
        """Test normalizing code string."""
        code = """
def foo():
    x = 1
    x = x + 1
"""
        normalized = normalize_code(code)

        # Parse the normalized code and check structure
        tree = ast.parse(normalized)
        func = tree.body[0]
        stmt = func.body[1]

        self.assertIsInstance(stmt, ast.AugAssign, "Should be augmented assignment")

    def test_normalize_code_returns_valid_python(self):
        """Test that normalized code is valid Python."""
        code = """
def foo():
    x = 5
    x = x * 2
    return x
"""
        normalized = normalize_code(code)

        # Should be able to parse without errors
        tree = ast.parse(normalized)
        self.assertIsNotNone(tree, "Normalized code should be valid Python")

        # Should be able to compile without errors
        compiled = compile(normalized, "<string>", "exec")
        self.assertIsNotNone(compiled, "Normalized code should compile")


class TestIsCommutative(unittest.TestCase):
    """Test the _is_commutative helper method."""

    def test_add_is_commutative(self):
        """Test that Add is commutative."""
        normalizer = AssignToAugAssignNormalizer()
        self.assertTrue(normalizer._is_commutative(ast.Add()), "Add should be commutative")

    def test_mult_is_commutative(self):
        """Test that Mult is commutative."""
        normalizer = AssignToAugAssignNormalizer()
        self.assertTrue(normalizer._is_commutative(ast.Mult()), "Mult should be commutative")

    def test_bitor_is_commutative(self):
        """Test that BitOr is commutative."""
        normalizer = AssignToAugAssignNormalizer()
        self.assertTrue(normalizer._is_commutative(ast.BitOr()), "BitOr should be commutative")

    def test_bitand_is_commutative(self):
        """Test that BitAnd is commutative."""
        normalizer = AssignToAugAssignNormalizer()
        self.assertTrue(normalizer._is_commutative(ast.BitAnd()), "BitAnd should be commutative")

    def test_bitxor_is_commutative(self):
        """Test that BitXor is commutative."""
        normalizer = AssignToAugAssignNormalizer()
        self.assertTrue(normalizer._is_commutative(ast.BitXor()), "BitXor should be commutative")

    def test_sub_not_commutative(self):
        """Test that Sub is not commutative."""
        normalizer = AssignToAugAssignNormalizer()
        self.assertFalse(normalizer._is_commutative(ast.Sub()), "Sub should not be commutative")

    def test_div_not_commutative(self):
        """Test that Div is not commutative."""
        normalizer = AssignToAugAssignNormalizer()
        self.assertFalse(normalizer._is_commutative(ast.Div()), "Div should not be commutative")


class TestIsInScope(unittest.TestCase):
    """Test the _is_in_scope helper method."""

    def test_variable_in_scope(self):
        """Test checking if variable is in scope."""
        normalizer = AssignToAugAssignNormalizer()
        normalizer.scopes[-1].add("x")

        self.assertTrue(normalizer._is_in_scope("x"), "x should be in scope")
        self.assertFalse(normalizer._is_in_scope("y"), "y should not be in scope")

    def test_variable_in_nested_scope(self):
        """Test checking variable across nested scopes."""
        normalizer = AssignToAugAssignNormalizer()
        normalizer.scopes[-1].add("x")
        normalizer.scopes.append(set())
        normalizer.scopes[-1].add("y")

        # Both x and y should be visible
        self.assertTrue(normalizer._is_in_scope("x"), "x should be visible from outer scope")
        self.assertTrue(normalizer._is_in_scope("y"), "y should be in current scope")
        self.assertFalse(normalizer._is_in_scope("z"), "z should not be in any scope")


class TestIntegration(unittest.TestCase):
    """Integration tests for complete normalization."""

    def test_complete_function_normalization(self):
        """Test normalizing a complete function."""
        code = """
def calculate(x, y):
    result = x
    result = result + y
    result = result * 2
    return result
"""
        tree = ast.parse(code)
        normalized = normalize_assigns_to_augassigns(tree)

        func = normalized.body[0]
        result_add = func.body[1]
        result_mult = func.body[2]

        self.assertIsInstance(result_add, ast.AugAssign, "Should convert result + y")
        self.assertIsInstance(result_mult, ast.AugAssign, "Should convert result * 2")

    def test_mixed_operations(self):
        """Test function with mixed operations."""
        code = """
def foo():
    x = 1
    y = 2
    x = x + y
    z = x + y
    x = x * 2
"""
        tree = ast.parse(code)
        normalized = normalize_assigns_to_augassigns(tree)

        func = normalized.body[0]
        x_add = func.body[2]
        z_assign = func.body[3]
        x_mult = func.body[4]

        self.assertIsInstance(x_add, ast.AugAssign, "x = x + y should convert")
        self.assertIsInstance(z_assign, ast.Assign, "z = x + y should remain assignment")
        self.assertIsInstance(x_mult, ast.AugAssign, "x = x * 2 should convert")


def main():
    """Run the tests."""
    unittest.main(verbosity=2)


if __name__ == "__main__":
    main()
