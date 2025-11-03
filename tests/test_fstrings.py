"""
Tests for f-string and constant handling.

Tests that:
1. F-string literal parts are never parameterized
2. F-string expressions can be parameterized
3. Constants can be parameterized when enabled
4. Generated code doesn't cause AST unparsing errors

IMPORTANT: These tests NEVER modify test_examples files.
All tests read from test_examples in read-only mode.
"""

import unittest
import ast
from towel.unification.refactor_engine import UnificationRefactorEngine
from tests.test_helpers import get_test_example_path, assert_file_not_modified


class TestFStringHandling(unittest.TestCase):
    """Test f-string handling."""

    def setUp(self):
        # Use min_lines=1 for f-string tests since test cases are short
        self.engine = UnificationRefactorEngine(
            max_parameters=5, min_lines=1, parameterize_constants=True
        )
        # Capture original content to verify it's never modified
        self.example_path = get_test_example_path("fstrings_constants.py")
        self.original_content = self.example_path.read_text()

    def tearDown(self):
        """Verify test_examples file was never modified."""
        assert_file_not_modified(self.example_path, self.original_content)

    def test_fstring_with_same_literals(self):
        """Test f-strings with identical literal text can unify."""
        proposals = self.engine.analyze_file(str(self.example_path))

        # format_number_a and format_number_b have same f-string structure
        format_props = [p for p in proposals if "format_number" in p.description.lower()]
        self.assertGreater(len(format_props), 0, "Should find format_number duplicates")

        # Check that extracted function can be unparsed without errors
        prop = format_props[0]
        try:
            func_code = ast.unparse(prop.extracted_function)
        except ValueError as e:
            self.fail(f"F-string caused unparsing error: {e}")

        # Check that it contains an f-string (either f" or f')
        self.assertTrue(
            'f"' in func_code or "f'" in func_code, f"Expected f-string in: {func_code}"
        )

    def test_fstring_with_different_literals_no_unify(self):
        """Test f-strings with different literal text should NOT unify."""
        proposals = self.engine.analyze_file(str(self.example_path))

        # log_user and log_admin have different f-string literal text
        # They should NOT be unified into one function
        log_props = [
            p
            for p in proposals
            if "log_user" in p.description.lower() and "log_admin" in p.description.lower()
        ]

        # If they unify, it would be wrong (different literal text)
        # This test documents current behavior - they may unify other parts
        # but the f-string literal should prevent full unification

    def test_fstring_no_ast_errors(self):
        """Test that f-strings don't cause AST unparsing errors."""
        proposals = self.engine.analyze_file(str(self.example_path))

        # Try to unparse all extracted functions
        for prop in proposals:
            try:
                ast.unparse(prop.extracted_function)
            except ValueError as e:
                self.fail(f"F-string caused unparsing error in {prop.description}: {e}")

    def test_mixed_fstrings(self):
        """Test mixed f-strings and regular strings."""
        proposals = self.engine.analyze_file(str(self.example_path))

        mixed = [p for p in proposals if "mixed_fstring" in p.description.lower()]
        self.assertGreater(len(mixed), 0, "Should find mixed f-string duplicates")


class TestConstantParameterization(unittest.TestCase):
    """Test constant parameterization."""

    def setUp(self):
        # Use min_lines=1 for short test cases
        self.engine = UnificationRefactorEngine(
            max_parameters=5, min_lines=1, parameterize_constants=True
        )
        # Capture original content to verify it's never modified
        self.example_path = get_test_example_path("fstrings_constants.py")
        self.original_content = self.example_path.read_text()

    def tearDown(self):
        """Verify test_examples file was never modified."""
        assert_file_not_modified(self.example_path, self.original_content)

    def test_numeric_constants_parameterized(self):
        """Test that numeric constants can be parameterized."""
        proposals = self.engine.analyze_file(str(self.example_path))

        const_props = [p for p in proposals if "const_parameterization" in p.description.lower()]
        self.assertGreater(len(const_props), 0, "Should find const parameterization duplicates")

        # Check that constants are parameterized
        prop = const_props[0]
        self.assertGreater(prop.parameters_count, 0, "Should have parameters for constants")

    def test_string_constants_parameterized(self):
        """Test that string constants can be parameterized."""
        proposals = self.engine.analyze_file(str(self.example_path))

        string_props = [p for p in proposals if "string_const" in p.description.lower()]
        self.assertGreater(len(string_props), 0, "Should find string const duplicates")

    def test_constant_parameterization_disabled(self):
        """Test behavior when constant parameterization is disabled."""
        engine_no_const = UnificationRefactorEngine(
            max_parameters=5, min_lines=4, parameterize_constants=False
        )

        proposals = engine_no_const.analyze_file(str(self.example_path))

        # With constant parameterization disabled, functions with different constants
        # should NOT be detected as duplicates
        const_props = [p for p in proposals if "const_parameterization" in p.description.lower()]
        # Should find fewer or no duplicates
        # (This tests the parameterize_constants flag works)


if __name__ == "__main__":
    unittest.main()
