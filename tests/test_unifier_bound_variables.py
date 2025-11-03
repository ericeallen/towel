#!/usr/bin/env python3
"""
Tests for unifier enhancement: hygienic renaming of bound variables.

These tests capture the desired behavior where the unifier should successfully
unify blocks with structurally identical code but different bound variable names.

Example:
    Block 1: result = x + 1; return result
    Block 2: output = y + 1; return output

    Should unify with:
    - x → parameter 'value'
    - y → parameter 'value'
    - result → hygienic rename 'temp'
    - output → hygienic rename 'temp'
"""

import unittest
import ast
from src.towel.unification.unifier import Unifier


class TestUnifierBoundVariables(unittest.TestCase):
    """Test unifier handles different bound variable names."""

    def setUp(self):
        """Create unifier instance for tests."""
        self.unifier = Unifier()

    def test_unify_single_bound_variable_different_names(self):
        """
        Test unification of blocks with single bound variable with different names.

        Block1: result = x + 1; return result
        Block2: output = y + 1; return output

        Expected: Unification succeeds with:
        - x, y → parameter (free variables)
        - result, output → hygienic rename 'temp' (bound variables)
        """
        code1 = """
result = x + 1
return result
"""
        code2 = """
output = y + 1
return output
"""

        block1 = ast.parse(code1).body
        block2 = ast.parse(code2).body

        # Attempt unification
        hygienic_renames = [{}, {}]
        substitution = self.unifier.unify_blocks([block1, block2], hygienic_renames)

        # Should succeed
        self.assertIsNotNone(
            substitution, "Unification should succeed for structurally identical blocks"
        )

        # Check hygienic renames were applied
        self.assertIn("result", hygienic_renames[0], "Block1's 'result' should be renamed")
        self.assertIn("output", hygienic_renames[1], "Block2's 'output' should be renamed")

        # Both should map to the same canonical name
        canonical_name = hygienic_renames[0]["result"]
        self.assertEqual(
            hygienic_renames[1]["output"],
            canonical_name,
            "Both bound variables should map to same canonical name",
        )

        # Canonical name should be 'temp' or '__temp' (with private prefix)
        self.assertTrue(
            canonical_name.startswith("temp") or canonical_name.startswith("__temp"),
            f"Canonical name should start with 'temp' or '__temp', got: {canonical_name}",
        )

    def test_unify_multiple_bound_variables(self):
        """
        Test unification with multiple bound variables.

        Block1: a = x + 1; b = a * 2; return b
        Block2: p = y + 1; q = p * 2; return q

        Expected: Both pairs of variables get hygienic renames:
        - a, p → 'temp'
        - b, q → 'temp_2'
        """
        code1 = """
a = x + 1
b = a * 2
return b
"""
        code2 = """
p = y + 1
q = p * 2
return q
"""

        block1 = ast.parse(code1).body
        block2 = ast.parse(code2).body

        # Attempt unification
        hygienic_renames = [{}, {}]
        substitution = self.unifier.unify_blocks([block1, block2], hygienic_renames)

        # Should succeed
        self.assertIsNotNone(
            substitution, "Unification should succeed for multiple bound variables"
        )

        # Check both variables are renamed in each block
        self.assertIn("a", hygienic_renames[0], "Block1's 'a' should be renamed")
        self.assertIn("b", hygienic_renames[0], "Block1's 'b' should be renamed")
        self.assertIn("p", hygienic_renames[1], "Block2's 'p' should be renamed")
        self.assertIn("q", hygienic_renames[1], "Block2's 'q' should be renamed")

        # Corresponding variables should map to same canonical names
        self.assertEqual(
            hygienic_renames[0]["a"],
            hygienic_renames[1]["p"],
            "'a' and 'p' should map to same canonical name",
        )
        self.assertEqual(
            hygienic_renames[0]["b"],
            hygienic_renames[1]["q"],
            "'b' and 'q' should map to same canonical name",
        )

        # Canonical names should be different
        self.assertNotEqual(
            hygienic_renames[0]["a"],
            hygienic_renames[0]["b"],
            "Different bound variables should have different canonical names",
        )

    def test_unify_bound_variable_reassignments(self):
        """
        Test unification with multiple reassignments of same variable.

        Block1: result = x; result = result + 1; result = result * 2
        Block2: output = y; output = output + 1; output = output * 2

        Expected: Single hygienic rename for both:
        - result, output → 'temp'
        All reassignments should unify correctly.
        """
        code1 = """
result = x
result = result + 1
result = result * 2
"""
        code2 = """
output = y
output = output + 1
output = output * 2
"""

        block1 = ast.parse(code1).body
        block2 = ast.parse(code2).body

        # Attempt unification
        hygienic_renames = [{}, {}]
        substitution = self.unifier.unify_blocks([block1, block2], hygienic_renames)

        # Should succeed
        self.assertIsNotNone(substitution, "Unification should succeed with reassignments")

        # Check hygienic renames
        self.assertIn("result", hygienic_renames[0], "Block1's 'result' should be renamed")
        self.assertIn("output", hygienic_renames[1], "Block2's 'output' should be renamed")

        # Should map to same canonical name
        self.assertEqual(
            hygienic_renames[0]["result"],
            hygienic_renames[1]["output"],
            "Reassigned variables should map to same canonical name",
        )

    def test_literals_a_b_unification(self):
        """
        Integration test: Test full literals_a and literals_b functions.

        This is the real-world test case from syntactic_coverage_comprehensive.py.
        """
        code1 = """
result = x + 42
result = result + 3.14
result = result + 1j
result = result + len("string")
result = result + len(b"bytes")
result = result + (1 if True else 0)
result = result + (0 if None else 1)
return result
"""
        code2 = """
output = y + 42
output = output + 3.14
output = output + 1j
output = output + len("string")
output = output + len(b"bytes")
output = output + (1 if True else 0)
output = output + (0 if None else 1)
return output
"""

        block1 = ast.parse(code1).body
        block2 = ast.parse(code2).body

        # Attempt unification
        hygienic_renames = [{}, {}]
        substitution = self.unifier.unify_blocks([block1, block2], hygienic_renames)

        # Should succeed
        self.assertIsNotNone(substitution, "literals_a and literals_b should unify successfully")

        # Check hygienic renames
        self.assertIn("result", hygienic_renames[0], "Block1's 'result' should be renamed")
        self.assertIn("output", hygienic_renames[1], "Block2's 'output' should be renamed")

        # Should map to same canonical name
        self.assertEqual(
            hygienic_renames[0]["result"],
            hygienic_renames[1]["output"],
            "Both variables should map to same canonical name",
        )

    def test_no_false_positives_different_structure(self):
        """
        Test that blocks with different structure do NOT unify.

        Block1: result = x + 1; return result
        Block2: output = y + 1; other = y * 2; return other

        Expected: Unification fails (different structure)
        """
        code1 = """
result = x + 1
return result
"""
        code2 = """
output = y + 1
other = y * 2
return other
"""

        block1 = ast.parse(code1).body
        block2 = ast.parse(code2).body

        # Attempt unification
        hygienic_renames = [{}, {}]
        substitution = self.unifier.unify_blocks([block1, block2], hygienic_renames)

        # Should fail - different structure
        self.assertIsNone(substitution, "Should NOT unify blocks with different structure")

    def test_preserve_identical_bound_variable_names(self):
        """
        Test that existing functionality is preserved.

        When bound variables have the same name, they should still unify correctly
        without needing hygienic renaming.

        Block1: result = x + 1; return result
        Block2: result = y + 1; return result

        Expected: Unification succeeds, no hygienic rename needed for 'result'
        """
        code1 = """
result = x + 1
return result
"""
        code2 = """
result = y + 1
return result
"""

        block1 = ast.parse(code1).body
        block2 = ast.parse(code2).body

        # Attempt unification
        hygienic_renames = [{}, {}]
        substitution = self.unifier.unify_blocks([block1, block2], hygienic_renames)

        # Should succeed
        self.assertIsNotNone(
            substitution, "Should still unify blocks with identical bound variable names"
        )

        # 'result' should NOT be in hygienic_renames (doesn't need renaming)
        # This preserves backwards compatibility

    def test_bound_variable_used_in_multiple_contexts(self):
        """
        Test bound variable used in various contexts.

        Block1: result = x + 1; temp = result * 2; result = temp + result; return result
        Block2: output = y + 1; aux = output * 2; output = aux + output; return output

        Expected: Proper hygienic renaming throughout
        """
        code1 = """
result = x + 1
temp = result * 2
result = temp + result
return result
"""
        code2 = """
output = y + 1
aux = output * 2
output = aux + output
return output
"""

        block1 = ast.parse(code1).body
        block2 = ast.parse(code2).body

        # Attempt unification
        hygienic_renames = [{}, {}]
        substitution = self.unifier.unify_blocks([block1, block2], hygienic_renames)

        # Should succeed
        self.assertIsNotNone(substitution, "Should unify with complex variable usage")

        # Check renames for all bound variables
        self.assertIn("result", hygienic_renames[0])
        self.assertIn("temp", hygienic_renames[0])
        self.assertIn("output", hygienic_renames[1])
        self.assertIn("aux", hygienic_renames[1])

        # Corresponding variables should map to same names
        self.assertEqual(hygienic_renames[0]["result"], hygienic_renames[1]["output"])
        self.assertEqual(hygienic_renames[0]["temp"], hygienic_renames[1]["aux"])


def main():
    """Run the tests."""
    # Run with verbose output to see each test
    unittest.main(verbosity=2)


if __name__ == "__main__":
    main()
