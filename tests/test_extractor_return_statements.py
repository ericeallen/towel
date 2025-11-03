#!/usr/bin/env python3
"""
Tests for extractor enhancement: adding return statements for value-producing extraction.

These tests capture the desired behavior where the extractor should add return
statements for variables that are bound in the extracted block but used after it.

Example:
    Original function:
        def foo(x):
            result = x + 1
            return result

    Should extract to:
        def extracted(value):
            temp = value + 1
            return temp  # <-- return statement added

        def foo(x):
            result = extracted(x)  # <-- capture return value
            return result
"""

import unittest
import ast
from src.towel.unification.unifier import Unifier, Substitution
from src.towel.unification.extractor import HygienicExtractor


class TestExtractorReturnStatements(unittest.TestCase):
    """Test extractor adds return statements for value-producing extraction."""

    def setUp(self):
        """Create extractor instance for tests."""
        self.extractor = HygienicExtractor()

    def test_single_return_variable(self):
        """
        Test extraction with single return variable.

        Input block:
            result = x + 1
            result = result * 2

        Expected output:
            def extracted(x):
                result = x + 1
                result = result * 2
                return result  # <-- Added
        """
        code = """
result = x + 1
result = result * 2
"""
        block = ast.parse(code).body

        # Create simple substitution (no parameters from unification)
        substitution = Substitution()

        # x is a free variable
        free_variables = {"x"}
        enclosing_names = set()

        # result is a return variable
        return_variables = ["result"]

        # Extract function
        func_def, param_order = self.extractor.extract_function(
            template_block=block,
            substitution=substitution,
            free_variables=free_variables,
            enclosing_names=enclosing_names,
            is_value_producing=True,
            return_variables=return_variables,
            function_name="extracted",
        )

        # Verify function structure
        self.assertEqual(func_def.name, "extracted")
        self.assertEqual(len(func_def.args.args), 1)
        self.assertEqual(func_def.args.args[0].arg, "x")

        # Verify body has return statement at the end
        self.assertEqual(len(func_def.body), 3)  # 2 assignments + 1 return

        last_stmt = func_def.body[-1]
        self.assertIsInstance(last_stmt, ast.Return)

        # Verify return value is the return variable
        self.assertIsInstance(last_stmt.value, ast.Name)
        self.assertEqual(last_stmt.value.id, "result")

    def test_multiple_return_variables(self):
        """
        Test extraction with multiple return variables.

        Input block:
            a = x + 1
            b = y + 1

        Expected output:
            def extracted(x, y):
                a = x + 1
                b = y + 1
                return (a, b)  # <-- Tuple return
        """
        code = """
a = x + 1
b = y + 1
"""
        block = ast.parse(code).body

        substitution = Substitution()
        free_variables = {"x", "y"}
        enclosing_names = set()
        return_variables = ["a", "b"]

        func_def, param_order = self.extractor.extract_function(
            template_block=block,
            substitution=substitution,
            free_variables=free_variables,
            enclosing_names=enclosing_names,
            is_value_producing=True,
            return_variables=return_variables,
            function_name="extracted",
        )

        # Verify body has return statement at the end
        self.assertEqual(len(func_def.body), 3)  # 2 assignments + 1 return

        last_stmt = func_def.body[-1]
        self.assertIsInstance(last_stmt, ast.Return)

        # Verify return value is a tuple
        self.assertIsInstance(last_stmt.value, ast.Tuple)

        # Verify tuple contains both variables
        tuple_elts = last_stmt.value.elts
        self.assertEqual(len(tuple_elts), 2)
        self.assertEqual(tuple_elts[0].id, "a")
        self.assertEqual(tuple_elts[1].id, "b")

    def test_no_return_variables_backward_compatibility(self):
        """
        Test that extraction without return variables works as before.

        This ensures backward compatibility - existing functionality preserved.
        """
        code = """
print(x)
"""
        block = ast.parse(code).body

        substitution = Substitution()
        free_variables = {"x"}
        enclosing_names = set()
        return_variables = []  # No return variables

        func_def, param_order = self.extractor.extract_function(
            template_block=block,
            substitution=substitution,
            free_variables=free_variables,
            enclosing_names=enclosing_names,
            is_value_producing=False,
            return_variables=return_variables,
            function_name="extracted",
        )

        # Verify body does NOT have return statement
        self.assertEqual(len(func_def.body), 1)  # Just the print statement
        self.assertNotIsInstance(func_def.body[-1], ast.Return)

    def test_return_variable_with_hygienic_rename(self):
        """
        Test that return statement uses hygienic renamed variable.

        When unifier renames bound variables (e.g., result → __temp_0),
        the return statement should use the renamed variable.
        """
        code = """
__temp_0 = x + 1
__temp_0 = __temp_0 * 2
"""
        block = ast.parse(code).body

        substitution = Substitution()
        free_variables = {"x"}
        enclosing_names = set()

        # The hygienic rename is __temp_0 (from unifier)
        return_variables = ["__temp_0"]

        func_def, param_order = self.extractor.extract_function(
            template_block=block,
            substitution=substitution,
            free_variables=free_variables,
            enclosing_names=enclosing_names,
            is_value_producing=True,
            return_variables=return_variables,
            function_name="extracted",
        )

        # Verify return statement uses hygienic name
        last_stmt = func_def.body[-1]
        self.assertIsInstance(last_stmt, ast.Return)
        self.assertEqual(last_stmt.value.id, "__temp_0")

    def test_return_variables_maintain_order(self):
        """
        Test that multiple return variables maintain their order.

        Important for tuple unpacking at call site.
        """
        code = """
first = x + 1
second = y + 1
third = z + 1
"""
        block = ast.parse(code).body

        substitution = Substitution()
        free_variables = {"x", "y", "z"}
        enclosing_names = set()

        # Order matters!
        return_variables = ["first", "second", "third"]

        func_def, param_order = self.extractor.extract_function(
            template_block=block,
            substitution=substitution,
            free_variables=free_variables,
            enclosing_names=enclosing_names,
            is_value_producing=True,
            return_variables=return_variables,
            function_name="extracted",
        )

        # Verify return tuple maintains order
        last_stmt = func_def.body[-1]
        tuple_elts = last_stmt.value.elts

        self.assertEqual(tuple_elts[0].id, "first")
        self.assertEqual(tuple_elts[1].id, "second")
        self.assertEqual(tuple_elts[2].id, "third")

    def test_return_statement_ast_correctness(self):
        """
        Test that generated return statement is valid AST.

        Should be compilable and executable Python code.
        """
        code = """
result = x + 1
"""
        block = ast.parse(code).body

        substitution = Substitution()
        free_variables = {"x"}
        enclosing_names = set()
        return_variables = ["result"]

        func_def, param_order = self.extractor.extract_function(
            template_block=block,
            substitution=substitution,
            free_variables=free_variables,
            enclosing_names=enclosing_names,
            is_value_producing=True,
            return_variables=return_variables,
            function_name="extracted",
        )

        # Verify the function is valid AST and can be compiled
        module = ast.Module(body=[func_def], type_ignores=[])
        ast.fix_missing_locations(module)

        try:
            code_obj = compile(module, "<test>", "exec")
            self.assertIsNotNone(code_obj)
        except SyntaxError as e:
            self.fail(f"Generated AST is not valid Python: {e}")

    def test_empty_block_with_return_variables(self):
        """
        Test edge case: empty block with return variables.

        Should handle gracefully (though this shouldn't happen in practice).
        """
        block = []  # Empty block

        substitution = Substitution()
        free_variables = set()
        enclosing_names = set()
        return_variables = ["result"]

        func_def, param_order = self.extractor.extract_function(
            template_block=block,
            substitution=substitution,
            free_variables=free_variables,
            enclosing_names=enclosing_names,
            is_value_producing=True,
            return_variables=return_variables,
            function_name="extracted",
        )

        # Should have pass statement or return statement
        # (Implementation detail - either is acceptable)
        self.assertGreaterEqual(len(func_def.body), 1)


def main():
    """Run the tests."""
    unittest.main(verbosity=2)


if __name__ == "__main__":
    main()
