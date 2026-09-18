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
        """F-strings whose literal text differs are never folded into one helper.

        ``log_user`` and ``log_admin`` differ only in the literal text of an
        f-string. The engine may still share the statements around it, but
        the f-string line itself stays at each site and no helper contains it.
        """
        proposals = self.engine.analyze_file(str(self.example_path))

        log_props = [
            p
            for p in proposals
            if "log_user" in p.description.lower() and "log_admin" in p.description.lower()
        ]
        self.assertGreater(len(log_props), 0, "the shared tail of log_user/log_admin")

        fstring_lines = {
            node.lineno
            for node in ast.walk(ast.parse(self.original_content))
            if isinstance(node, ast.JoinedStr)
        }
        for prop in log_props:
            joined = [n for n in ast.walk(prop.extracted_function) if isinstance(n, ast.JoinedStr)]
            self.assertEqual(joined, [], ast.unparse(prop.extracted_function))
            for replacement in prop.replacements:
                start, end = replacement.line_range
                covered = {line for line in fstring_lines if start <= line <= end}
                self.assertEqual(covered, set(), "f-string line must stay at the site")

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

        # With constant parameterization disabled, functions that differ only in
        # their constants are not duplicates.
        const_props = [p for p in proposals if "const_parameterization" in p.description.lower()]
        self.assertEqual(const_props, [], [p.description for p in const_props])

        # The same limits with the flag on do find them, so the flag is what
        # made the difference.
        engine_const = UnificationRefactorEngine(
            max_parameters=5, min_lines=4, parameterize_constants=True
        )
        with_flag = [
            p
            for p in engine_const.analyze_file(str(self.example_path))
            if "const_parameterization" in p.description.lower()
        ]
        self.assertEqual(
            [(p.description, p.parameters_count) for p in with_flag],
            [
                (
                    "Extract common code from const_parameterization_a and "
                    "const_parameterization_b",
                    2,
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()
