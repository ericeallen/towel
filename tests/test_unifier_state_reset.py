"""
Regression tests for Unifier internal state reset and end-to-end stability.

These tests ensure that:
- The Unifier does not leak alpha-renamings or parameter counters across calls
- analyze_file repeatedly returns stable proposals (e.g., format_number and mixed_fstring)
"""

import ast
import unittest

from towel.unification.unifier import Unifier
from towel.unification.refactor_engine import UnificationRefactorEngine
from tests.test_helpers import get_test_example_path


class TestUnifierStateReset(unittest.TestCase):
    def setUp(self):
        self.unifier = Unifier(max_parameters=5, parameterize_constants=True)

    def _fn_body(self, src: str):
        """Parse a single-function source and return its body statements."""
        import textwrap

        tree = ast.parse(textwrap.dedent(src).strip())
        func = next(n for n in tree.body if isinstance(n, ast.FunctionDef))
        return func.body

    def test_unify_blocks_twice_no_cross_contamination(self):
        """Unifier.unify_blocks should be independent per call."""
        # First pair: different names require parameterization and alpha-renaming
        f1_a = """
        def f1():
            a = x
            return a
        """
        f1_b = """
        def f2():
            b = y
            return b
        """
        blocks1 = [self._fn_body(f1_a), self._fn_body(f1_b)]
        subst1 = self.unifier.unify_blocks(blocks1, hygienic_renames=[{}, {}])
        self.assertIsNotNone(subst1, "First unification should succeed")

        # Second pair: identical names and constants (should unify trivially)
        f2_a = """
        def g1():
            result = 42
            return result
        """
        f2_b = """
        def g2():
            result = 42
            return result
        """
        blocks2 = [self._fn_body(f2_a), self._fn_body(f2_b)]
        subst2 = self.unifier.unify_blocks(blocks2, hygienic_renames=[{}, {}])
        self.assertIsNotNone(subst2, "Second unification should succeed independently")
        # Ensure no parameters were introduced for identical blocks
        self.assertEqual(
            len(subst2.param_expressions), 0, "Identical blocks should not introduce params"
        )


class TestEngineEndToEndStability(unittest.TestCase):
    """Ensure analyze_file returns expected proposals consistently across repeated runs."""

    def setUp(self):
        self.engine = UnificationRefactorEngine(
            max_parameters=5, min_lines=1, parameterize_constants=True
        )
        self.example_path = get_test_example_path("fstrings_constants.py")

    def _assert_expected_proposals(self, proposals):
        descs = [p.description.lower() for p in proposals]
        self.assertTrue(
            any("format_number" in d for d in descs), "Should include format_number proposals"
        )
        self.assertTrue(
            any("mixed_fstring" in d for d in descs), "Should include mixed f-string proposals"
        )

    def test_analyze_file_multiple_runs_stable(self):
        # Run analyze_file multiple times with the same engine instance
        for _ in range(3):
            proposals = self.engine.analyze_file(str(self.example_path))
            self._assert_expected_proposals(proposals)


if __name__ == "__main__":
    unittest.main()
