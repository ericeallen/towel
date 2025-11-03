#!/usr/bin/env python3
"""
Comprehensive unit tests for the Unifier class.

These tests isolate and test individual methods to identify where
Block 0 unification fails for higher_order_function_c/d.
"""

import unittest
import ast
from src.towel.unification.unifier import Unifier, Substitution


class TestUnifierBasics(unittest.TestCase):
    """Test basic unification functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.unifier = Unifier(max_parameters=5)

    def test_unify_identical_simple_blocks(self):
        """Test unifying two identical simple blocks."""
        code = """
x = 1
y = 2
return x + y
"""
        tree1 = ast.parse(code)
        tree2 = ast.parse(code)

        result = self.unifier.unify_blocks(
            [tree1.body, tree2.body], [{}, {}]  # No hygienic renames
        )

        self.assertIsNotNone(result, "Unification of identical blocks should succeed")
        self.assertEqual(
            len(result.param_expressions), 0, "Identical blocks should require 0 parameters"
        )

    def test_unify_blocks_with_single_difference(self):
        """Test unifying blocks with a single constant difference."""
        code1 = "x = 1\nreturn x"
        code2 = "x = 2\nreturn x"

        tree1 = ast.parse(code1)
        tree2 = ast.parse(code2)

        result = self.unifier.unify_blocks([tree1.body, tree2.body], [{}, {}])

        self.assertIsNotNone(result, "Unification with single difference should succeed")
        self.assertEqual(
            len(result.param_expressions),
            1,
            "Single constant difference should require 1 parameter",
        )

    def test_unify_blocks_with_nested_function_definitions(self):
        """Test unifying blocks containing nested function definitions."""
        code = """
def helper(x):
    return x * 2

result = helper(5)
return result
"""
        tree1 = ast.parse(code)
        tree2 = ast.parse(code)

        result = self.unifier.unify_blocks([tree1.body, tree2.body], [{}, {}])

        self.assertIsNotNone(
            result, "Unification of identical blocks with nested functions should succeed"
        )

        # Identical blocks should require 0 parameters
        self.assertEqual(
            len(result.param_expressions),
            0,
            "Identical blocks with nested functions should require 0 parameters",
        )


class TestUnifierWithTwoNestedFunctions(unittest.TestCase):
    """Test unification of blocks with multiple nested function definitions."""

    def setUp(self):
        """Set up test fixtures."""
        self.unifier = Unifier(max_parameters=5)

    def test_unify_blocks_with_two_identical_nested_functions(self):
        """Test unifying blocks with TWO identical nested function definitions."""
        code = """
def make_validator(limit):
    return lambda x: x > limit

def make_transformer(factor):
    return lambda x: x * factor

validator = make_validator(5)
transformer = make_transformer(2)
result = list(filter(validator, data))
return result
"""
        tree1 = ast.parse(code)
        tree2 = ast.parse(code)

        result = self.unifier.unify_blocks([tree1.body, tree2.body], [{}, {}])

        self.assertIsNotNone(
            result, "Unification of blocks with TWO nested functions should succeed"
        )

        # Identical blocks should have 0 parameters
        self.assertEqual(
            len(result.param_expressions),
            0,
            "Identical blocks with nested functions should require 0 parameters",
        )

    def test_unify_higher_order_function_cd_blocks(self):
        """Test unifying Block 0 from higher_order_function_c and _d."""
        # This is the actual code from functional_patterns.py
        code_c = """
def make_validator(limit):
    return lambda x: x > limit and x < limit * 10

def make_transformer(factor):
    return lambda x: x * factor + threshold

validator = make_validator(5)
transformer = make_transformer(2)
filtered = list(filter(validator, data))
transformed = list(map(transformer, filtered))
return transformed
"""

        code_d = """
def make_validator(limit):
    return lambda x: x > limit and x < limit * 10

def make_transformer(factor):
    return lambda x: x * factor + threshold

validator = make_validator(5)
transformer = make_transformer(2)
filtered = list(filter(validator, data))
transformed = list(map(transformer, filtered))
return transformed
"""

        tree_c = ast.parse(code_c)
        tree_d = ast.parse(code_d)

        result = self.unifier.unify_blocks([tree_c.body, tree_d.body], [{}, {}])

        self.assertIsNotNone(
            result, "Unification of higher_order_function_c/d Block 0 should succeed"
        )

        # Identical blocks should have 0 parameters
        self.assertEqual(
            len(result.param_expressions),
            0,
            "Identical blocks with nested functions should require 0 parameters",
        )


class TestUnifierParameterCounting(unittest.TestCase):
    """Test parameter counting logic."""

    def setUp(self):
        """Set up test fixtures."""
        self.unifier = Unifier(max_parameters=5)

    def test_parameter_limit_respected(self):
        """Test that unification fails when parameter limit is exceeded."""
        # Create blocks with more differences than max_parameters
        code1 = "x = 1\ny = 2\nz = 3\na = 4\nb = 5\nc = 6"
        code2 = "x = 10\ny = 20\nz = 30\na = 40\nb = 50\nc = 60"

        tree1 = ast.parse(code1)
        tree2 = ast.parse(code2)

        result = self.unifier.unify_blocks([tree1.body, tree2.body], [{}, {}])

        self.assertIsNone(result, "Unification should fail when too many parameters needed")

    def test_parameter_count_for_nested_functions_with_differences(self):
        """Test parameter counting when nested functions differ."""
        code1 = """
def helper(x):
    return x * 2

result = helper(5)
return result
"""

        code2 = """
def helper(x):
    return x * 2

result = helper(10)
return result
"""

        tree1 = ast.parse(code1)
        tree2 = ast.parse(code2)

        result = self.unifier.unify_blocks([tree1.body, tree2.body], [{}, {}])

        self.assertIsNotNone(result, "Should unify with one parameter")
        self.assertEqual(
            len(result.param_expressions),
            1,
            "Should require 1 parameter for the differing constant",
        )


class TestUnifierEdgeCases(unittest.TestCase):
    """Test edge cases and error conditions."""

    def setUp(self):
        """Set up test fixtures."""
        self.unifier = Unifier(max_parameters=5)

    def test_empty_blocks(self):
        """Test unifying empty blocks."""
        result = self.unifier.unify_blocks([[], []], [{}, {}])

        self.assertIsNotNone(result, "Empty blocks should unify")
        self.assertEqual(
            len(result.param_expressions), 0, "Empty blocks should require 0 parameters"
        )

    def test_mismatched_block_lengths(self):
        """Test unifying blocks of different lengths."""
        code1 = "x = 1\nreturn x"
        code2 = "x = 1"

        tree1 = ast.parse(code1)
        tree2 = ast.parse(code2)

        result = self.unifier.unify_blocks([tree1.body, tree2.body], [{}, {}])

        self.assertIsNone(result, "Blocks with different lengths should not unify")

    def test_mismatched_statement_types(self):
        """Test unifying blocks with different statement types."""
        code1 = "x = 1\nreturn x"
        code2 = "x = 1\nif True: pass"

        tree1 = ast.parse(code1)
        tree2 = ast.parse(code2)

        result = self.unifier.unify_blocks([tree1.body, tree2.body], [{}, {}])

        self.assertIsNone(result, "Blocks with different statement types should not unify")


class TestUnifierWithActualFunctionalPatternsCode(unittest.TestCase):
    """Test with the actual code from functional_patterns.py."""

    def setUp(self):
        """Set up test fixtures."""
        self.unifier = Unifier(max_parameters=5)

        # Read the actual test file
        with open("test_examples/functional_patterns.py", "r") as f:
            self.source = f.read()

        self.tree = ast.parse(self.source)

        # Find the functions
        self.func_c = None
        self.func_d = None
        for node in ast.walk(self.tree):
            if isinstance(node, ast.FunctionDef):
                if node.name == "higher_order_function_c":
                    self.func_c = node
                elif node.name == "higher_order_function_d":
                    self.func_d = node

    def test_actual_block_0_unification(self):
        """Test unifying actual Block 0 from higher_order_function_c/d."""
        self.assertIsNotNone(self.func_c, "Function c should be found")
        self.assertIsNotNone(self.func_d, "Function d should be found")

        # Get function bodies (skip docstring)
        body_c = self.func_c.body
        body_d = self.func_d.body

        # Skip docstring if present
        if (
            body_c
            and isinstance(body_c[0], ast.Expr)
            and isinstance(body_c[0].value, ast.Constant)
            and isinstance(body_c[0].value.value, str)
        ):
            body_c = body_c[1:]

        if (
            body_d
            and isinstance(body_d[0], ast.Expr)
            and isinstance(body_d[0].value, ast.Constant)
            and isinstance(body_d[0].value.value, str)
        ):
            body_d = body_d[1:]

        # Verify both have 7 statements
        self.assertEqual(len(body_c), 7, "Function c body should have 7 statements")
        self.assertEqual(len(body_d), 7, "Function d body should have 7 statements")

        # Attempt unification
        result = self.unifier.unify_blocks([body_c, body_d], [{}, {}])

        # This is the critical test
        self.assertIsNotNone(
            result, "Block 0 from higher_order_function_c/d should unify successfully"
        )

        # Verify minimal parameters (should be 0 since blocks are identical)
        self.assertEqual(
            len(result.param_expressions),
            0,
            f"Identical blocks should require 0 parameters, "
            f"but got {len(result.param_expressions)}",
        )


def main():
    """Run the tests."""
    unittest.main(verbosity=2)


if __name__ == "__main__":
    main()
