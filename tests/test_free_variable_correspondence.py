#!/usr/bin/env python3
"""
Unit tests for free variable correspondence tracking.

These tests demonstrate the bug where free variables with different names across
blocks are not tracked in hygienic_renames, leading to incorrect function calls.
"""

import unittest
import ast
from src.towel.unification.unifier import Unifier
from src.towel.unification.extractor import HygienicExtractor


class TestFreeVariableCorrespondence(unittest.TestCase):
    """Test that free variables with different names are tracked correctly."""

    def setUp(self):
        """Create unifier and extractor instances."""
        self.unifier = Unifier()
        self.extractor = HygienicExtractor()

    def test_simple_free_variable_correspondence(self):
        """
        Test the simplest case: free variables with different names.

        Block 0: user.get("id")
        Block 1: admin.get("id")

        'user' and 'admin' are both free variables (not assigned in these blocks).
        They should be tracked in hygienic_renames so generated calls use correct names.
        """
        code0 = 'user.get("id")'
        code1 = 'admin.get("id")'

        block0 = [ast.parse(code0, mode="eval").body]
        block1 = [ast.parse(code1, mode="eval").body]

        # Unify the blocks
        hygienic_renames = [{}, {}]
        substitution = self.unifier.unify_blocks([block0, block1], hygienic_renames)

        self.assertIsNotNone(substitution, "Blocks should unify")

        # Check hygienic_renames tracks the correspondence
        print(f"\nTest 1 - Hygienic renames:")
        print(f"  Block 0: {hygienic_renames[0]}")
        print(f"  Block 1: {hygienic_renames[1]}")

        # Block 1 should map 'admin' to 'user' (the canonical name)
        self.assertIn("admin", hygienic_renames[1], "hygienic_renames[1] should contain 'admin'")
        self.assertEqual(
            hygienic_renames[1]["admin"], "user", "'admin' should map to canonical name 'user'"
        )

    def test_free_variable_in_if_statement(self):
        """
        Test free variables in if statements (the example1_simple.py case).

        Block 0: if not user.get("id"): raise ValueError(...)
        Block 1: if not admin.get("id"): raise ValueError(...)
        """
        code0 = """
if not user.get("id"):
    raise ValueError("ID required")
"""
        code1 = """
if not admin.get("id"):
    raise ValueError("ID required")
"""

        block0 = ast.parse(code0).body
        block1 = ast.parse(code1).body

        # Unify
        hygienic_renames = [{}, {}]
        substitution = self.unifier.unify_blocks([block0, block1], hygienic_renames)

        self.assertIsNotNone(substitution, "Blocks should unify")

        print(f"\nTest 2 - Hygienic renames:")
        print(f"  Block 0: {hygienic_renames[0]}")
        print(f"  Block 1: {hygienic_renames[1]}")

        # Verify correspondence
        self.assertIn("admin", hygienic_renames[1], "hygienic_renames[1] should track 'admin'")
        self.assertEqual(hygienic_renames[1]["admin"], "user", "'admin' should map to 'user'")

    def test_multiple_free_variables(self):
        """
        Test multiple free variables with different names.

        Block 0: user.id + config.value
        Block 1: admin.id + settings.value
        """
        code0 = "user.id + config.value"
        code1 = "admin.id + settings.value"

        block0 = [ast.parse(code0, mode="eval").body]
        block1 = [ast.parse(code1, mode="eval").body]

        hygienic_renames = [{}, {}]
        substitution = self.unifier.unify_blocks([block0, block1], hygienic_renames)

        self.assertIsNotNone(substitution, "Blocks should unify")

        print(f"\nTest 3 - Hygienic renames:")
        print(f"  Block 0: {hygienic_renames[0]}")
        print(f"  Block 1: {hygienic_renames[1]}")

        # Both variables should be tracked
        self.assertIn("admin", hygienic_renames[1], "Should track 'admin' → 'user'")
        self.assertIn("settings", hygienic_renames[1], "Should track 'settings' → 'config'")
        self.assertEqual(hygienic_renames[1]["admin"], "user")
        self.assertEqual(hygienic_renames[1]["settings"], "config")

    def test_free_variable_used_multiple_times(self):
        """
        Test free variable used multiple times in same block.

        Block 0: user.id + user.name
        Block 1: admin.id + admin.name
        """
        code0 = "user.id + user.name"
        code1 = "admin.id + admin.name"

        block0 = [ast.parse(code0, mode="eval").body]
        block1 = [ast.parse(code1, mode="eval").body]

        hygienic_renames = [{}, {}]
        substitution = self.unifier.unify_blocks([block0, block1], hygienic_renames)

        self.assertIsNotNone(substitution, "Blocks should unify")

        print(f"\nTest 4 - Hygienic renames:")
        print(f"  Block 0: {hygienic_renames[0]}")
        print(f"  Block 1: {hygienic_renames[1]}")

        # Should still track even though used multiple times
        self.assertIn(
            "admin", hygienic_renames[1], "Should track 'admin' even when used multiple times"
        )
        self.assertEqual(hygienic_renames[1]["admin"], "user")

    def test_three_blocks_different_names(self):
        """
        Test three blocks with different variable names (example1_simple.py case).

        Block 0: user.get("id")
        Block 1: admin.get("id")
        Block 2: guest.get("id")
        """
        code0 = 'user.get("id")'
        code1 = 'admin.get("id")'
        code2 = 'guest.get("id")'

        block0 = [ast.parse(code0, mode="eval").body]
        block1 = [ast.parse(code1, mode="eval").body]
        block2 = [ast.parse(code2, mode="eval").body]

        hygienic_renames = [{}, {}, {}]
        substitution = self.unifier.unify_blocks([block0, block1, block2], hygienic_renames)

        self.assertIsNotNone(substitution, "All blocks should unify")

        print(f"\nTest 5 - Hygienic renames:")
        print(f"  Block 0: {hygienic_renames[0]}")
        print(f"  Block 1: {hygienic_renames[1]}")
        print(f"  Block 2: {hygienic_renames[2]}")

        # Block 0 uses canonical name (no mapping needed)
        self.assertEqual(
            len(hygienic_renames[0]), 0, "Block 0 should have no renames (uses canonical names)"
        )

        # Blocks 1 and 2 should map to canonical
        self.assertIn("admin", hygienic_renames[1])
        self.assertEqual(hygienic_renames[1]["admin"], "user")

        self.assertIn("guest", hygienic_renames[2])
        self.assertEqual(hygienic_renames[2]["guest"], "user")

    def test_integration_with_generate_call(self):
        """
        Integration test: verify generated calls use correct variable names.

        This is the end-to-end test showing the bug and the fix.
        """
        # Validation code blocks
        code0 = """
if not user.get("id"):
    raise ValueError("ID required")
if not user.get("name"):
    raise ValueError("Name required")
"""
        code1 = """
if not admin.get("id"):
    raise ValueError("ID required")
if not admin.get("name"):
    raise ValueError("Name required")
"""

        block0 = ast.parse(code0).body
        block1 = ast.parse(code1).body

        # Unify
        hygienic_renames = [{}, {}]
        substitution = self.unifier.unify_blocks([block0, block1], hygienic_renames)

        self.assertIsNotNone(substitution, "Blocks should unify")

        # Extract function
        free_variables = {"user"}  # Canonical name
        func_def, param_order = self.extractor.extract_function(
            template_block=block0,
            substitution=substitution,
            free_variables=free_variables,
            enclosing_names=set(),
            is_value_producing=False,
            return_variables=[],
            function_name="validate",
        )

        # Generate calls
        call0 = self.extractor.generate_call(
            function_name="validate",
            block_idx=0,
            substitution=substitution,
            param_order=param_order,
            free_variables=free_variables,
            is_value_producing=False,
            return_variables=[],
            hygienic_renames=hygienic_renames,
        )

        call1 = self.extractor.generate_call(
            function_name="validate",
            block_idx=1,
            substitution=substitution,
            param_order=param_order,
            free_variables=free_variables,
            is_value_producing=False,
            return_variables=[],
            hygienic_renames=hygienic_renames,
        )

        # Unparse and check
        call0_str = ast.unparse(call0)
        call1_str = ast.unparse(call1)

        print(f"\nTest 6 - Generated calls:")
        print(f"  Call 0: {call0_str}")
        print(f"  Call 1: {call1_str}")

        # Call 0 should use 'user'
        self.assertIn("user", call0_str, f"Call 0 should use 'user': {call0_str}")

        # Call 1 should use 'admin', NOT 'user'
        self.assertIn("admin", call1_str, f"Call 1 should use 'admin': {call1_str}")
        # This is the key assertion that currently fails:
        self.assertNotIn("user", call1_str, f"Call 1 should NOT use 'user': {call1_str}")


def main():
    """Run the tests."""
    unittest.main(verbosity=2)


if __name__ == "__main__":
    main()
