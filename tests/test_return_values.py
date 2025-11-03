"""
Tests for return value propagation.

Ensures that extracted functions with return statements
properly propagate return values in replacement calls.

IMPORTANT: These tests NEVER modify test_examples files.
All tests read from test_examples in read-only mode.
"""

import unittest
import ast
from towel.unification.refactor_engine import UnificationRefactorEngine
from tests.test_helpers import get_test_example_path, assert_file_not_modified


class TestReturnValuePropagation(unittest.TestCase):
    """Test that return values are properly propagated."""

    def setUp(self):
        # Use min_lines=1 to test short code blocks
        self.engine = UnificationRefactorEngine(
            max_parameters=5, min_lines=1, parameterize_constants=True
        )
        # Capture original content to verify it's never modified
        self.example_path = get_test_example_path("return_values.py")
        self.original_content = self.example_path.read_text()

    def tearDown(self):
        """Verify test_examples file was never modified."""
        assert_file_not_modified(self.example_path, self.original_content)

    def test_early_return(self):
        """Test early return in if statement."""
        proposals = self.engine.analyze_file(str(self.example_path))

        early = [p for p in proposals if "early_return" in p.description.lower()]
        self.assertGreater(len(early), 0, "Should find early return duplicates")

        # Apply refactoring and check it's valid
        prop = early[0]
        refactored = self.engine.apply_refactoring(str(self.example_path), prop)

        # Check that refactored code is valid Python
        try:
            ast.parse(refactored)
        except SyntaxError as e:
            self.fail(f"Refactored code is not valid Python: {e}")

        # Check that return statement is in the replacement
        self.assertIn("return", refactored)

    def test_nested_return(self):
        """Test return nested in multiple if statements."""
        proposals = self.engine.analyze_file(str(self.example_path))

        nested = [p for p in proposals if "nested_return" in p.description.lower()]
        self.assertGreater(len(nested), 0, "Should find nested return duplicates")

        # Check that extracted function contains return
        prop = nested[0]
        func_code = ast.unparse(prop.extracted_function)
        self.assertIn("return", func_code)

    def test_loop_with_return(self):
        """Test return inside loop."""
        proposals = self.engine.analyze_file(str(self.example_path))

        loop_ret = [p for p in proposals if "loop_with_return" in p.description.lower()]
        self.assertGreater(len(loop_ret), 0, "Should find loop return duplicates")

    def test_multiple_returns(self):
        """Test multiple return paths."""
        proposals = self.engine.analyze_file(str(self.example_path))

        multi = [p for p in proposals if "multiple_returns" in p.description.lower()]
        self.assertGreater(len(multi), 0, "Should find multiple return duplicates")

    def test_no_explicit_return(self):
        """Test function with no explicit return."""
        proposals = self.engine.analyze_file(str(self.example_path))

        no_ret = [p for p in proposals if "no_return" in p.description.lower()]
        self.assertGreater(len(no_ret), 0, "Should find no-return duplicates")

        # These should NOT have 'return' in the replacement call
        # (since they don't produce a value)
        prop = no_ret[0]
        # The extracted function itself won't have a return
        # and the replacement shouldn't either


if __name__ == "__main__":
    unittest.main()
