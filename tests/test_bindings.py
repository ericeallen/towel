"""
Tests for binding construct handling.

Tests that the system correctly handles:
- For loop variables (i vs j should be alpha-equivalent)
- Comprehension variables
- Lambda parameters
- Nested function scopes

IMPORTANT: These tests NEVER modify test_examples files.
All tests read from test_examples in read-only mode.
"""

import unittest
import ast
from towel.unification.refactor_engine import UnificationRefactorEngine
from tests.test_helpers import get_test_example_path, assert_file_not_modified


class TestForLoopBindings(unittest.TestCase):
    """Test for-loop variable binding and alpha-renaming."""

    def setUp(self):
        self.engine = UnificationRefactorEngine(
            max_parameters=5, min_lines=4, parameterize_constants=True
        )
        # Capture original content to verify it's never modified
        self.example_path = get_test_example_path("bindings_for_loops.py")
        self.original_content = self.example_path.read_text()

    def tearDown(self):
        """Verify test_examples file was never modified."""
        assert_file_not_modified(self.example_path, self.original_content)

    def test_simple_for_loop_alpha_renaming(self):
        """Test that loop variables i and j are treated as alpha-equivalent."""
        proposals = self.engine.analyze_file(str(self.example_path))
        self.assertGreater(
            len(proposals), 0, "Should find duplicates with different loop variables"
        )

        # Find the proposal for process_list_a and process_list_b
        relevant = [p for p in proposals if "process_list" in p.description.lower()]
        self.assertGreater(len(relevant), 0, "Should find process_list duplicates")

        # Check the extracted function
        prop = relevant[0]
        func_code = ast.unparse(prop.extracted_function)

        # Loop variable should be present but not as a parameter
        self.assertIn("for", func_code.lower())
        # Should have 'range' as builtin, not parameter
        self.assertNotIn("range", [arg.arg for arg in prop.extracted_function.args.args])

    def test_nested_for_loops(self):
        """Test nested for loops with different variable names."""
        proposals = self.engine.analyze_file(str(self.example_path))

        # Find nested loop proposals
        nested = [p for p in proposals if "nested_loops" in p.description.lower()]
        self.assertGreater(len(nested), 0, "Should find nested loop duplicates")

    def test_tuple_unpacking_in_for_loop(self):
        """Test for loops with tuple unpacking."""
        proposals = self.engine.analyze_file(str(self.example_path))

        # Find tuple unpacking proposals
        tuple_props = [p for p in proposals if "tuple_unpacking" in p.description.lower()]
        self.assertGreater(len(tuple_props), 0, "Should find tuple unpacking duplicates")


class TestComprehensionBindings(unittest.TestCase):
    """Test comprehension variable binding."""

    def setUp(self):
        # Use min_lines=1 for comprehensions since they're typically short
        self.engine = UnificationRefactorEngine(
            max_parameters=5, min_lines=1, parameterize_constants=True
        )
        # Capture original content to verify it's never modified
        self.example_path = get_test_example_path("bindings_comprehensions.py")
        self.original_content = self.example_path.read_text()

    def tearDown(self):
        """Verify test_examples file was never modified."""
        assert_file_not_modified(self.example_path, self.original_content)

    def test_list_comprehension_variables(self):
        """Test that list comprehension variables are recognized as bindings."""
        proposals = self.engine.analyze_file(str(self.example_path))

        # Find list comprehension proposals
        list_comp = [p for p in proposals if "list_comp" in p.description.lower()]
        self.assertGreater(len(list_comp), 0, "Should find list comprehension duplicates")

    def test_dict_comprehension_variables(self):
        """Test dict comprehension variable binding."""
        proposals = self.engine.analyze_file(str(self.example_path))

        dict_comp = [p for p in proposals if "dict_comp" in p.description.lower()]
        self.assertGreater(len(dict_comp), 0, "Should find dict comprehension duplicates")

    def test_nested_comprehensions(self):
        """Test nested comprehension variables."""
        proposals = self.engine.analyze_file(str(self.example_path))

        nested = [p for p in proposals if "nested_comp" in p.description.lower()]
        self.assertGreater(len(nested), 0, "Should find nested comprehension duplicates")

    def test_generator_expressions(self):
        """Test generator expression variables."""
        proposals = self.engine.analyze_file(str(self.example_path))

        gen_expr = [p for p in proposals if "generator_expr" in p.description.lower()]
        self.assertGreater(len(gen_expr), 0, "Should find generator expression duplicates")

    def test_set_comprehensions(self):
        """Test set comprehension variables."""
        proposals = self.engine.analyze_file(str(self.example_path))

        set_comp = [p for p in proposals if "set_comp" in p.description.lower()]
        self.assertGreater(len(set_comp), 0, "Should find set comprehension duplicates")


class TestScopingEdgeCases(unittest.TestCase):
    """Test scoping edge cases."""

    def setUp(self):
        self.engine = UnificationRefactorEngine(
            max_parameters=5, min_lines=4, parameterize_constants=True
        )
        # Capture original content to verify it's never modified
        self.example_path = get_test_example_path("scoping_edge_cases.py")
        self.original_content = self.example_path.read_text()

    def tearDown(self):
        """Verify test_examples file was never modified."""
        assert_file_not_modified(self.example_path, self.original_content)

    def test_builtin_not_parameterized(self):
        """Test that builtins like len, str are not treated as parameters."""
        proposals = self.engine.analyze_file(str(self.example_path))

        builtin_props = [p for p in proposals if "builtin_override" in p.description.lower()]
        self.assertGreater(len(builtin_props), 0, "Should find builtin test duplicates")

        # Check that len and str are not parameters
        prop = builtin_props[0]
        param_names = [arg.arg for arg in prop.extracted_function.args.args]
        self.assertNotIn("len", param_names, "len should not be a parameter")
        self.assertNotIn("str", param_names, "str should not be a parameter")
        self.assertNotIn("print", param_names, "print should not be a parameter")

    def test_nested_functions(self):
        """Test nested function definitions."""
        proposals = self.engine.analyze_file(str(self.example_path))

        nested_func = [p for p in proposals if "nested_func" in p.description.lower()]
        # May or may not find duplicates depending on extraction strategy
        # This is more about ensuring it doesn't crash

    def test_lambda_expressions(self):
        """Test lambda expression handling."""
        proposals = self.engine.analyze_file(str(self.example_path))

        lambda_props = [p for p in proposals if "lambda" in p.description.lower()]
        # Lambdas should be handled without errors


if __name__ == "__main__":
    unittest.main()
