#!/usr/bin/env python3
"""
Tests for variable capture bug in generated function calls.

This bug occurs when unified bound variables have different names across blocks.
The generated function calls incorrectly use the canonical variable name instead
of the original variable name from each block.

Example:
    Block 1: user = {...}; validate(user)
    Block 2: admin = {...}; validate(admin)

    After unification, both 'user' and 'admin' map to canonical name 'user'.

    Generated calls should be:
        extracted_func(user)   # In block 1
        extracted_func(admin)  # In block 2  ← BUG: currently generates extracted_func(user)
"""

import unittest
import ast
from src.towel.unification.unifier import Unifier
from src.towel.unification.extractor import HygienicExtractor


class TestVariableCaptureBug(unittest.TestCase):
    """Test variable capture bug in generated function calls."""

    def setUp(self):
        """Create unifier and extractor instances."""
        self.unifier = Unifier()
        self.extractor = HygienicExtractor()

    def test_different_variable_names_in_calls(self):
        """
        Test that generated calls use correct variable names from each block.

        This is the core bug: when blocks use different variable names (user, admin),
        the generated function calls should use the original names, not the canonical name.
        """
        # Two blocks with identical structure but different variable names
        code1 = """
user = get_user()
if not user.get("id"):
    raise ValueError("ID required")
"""
        code2 = """
admin = get_admin()
if not admin.get("id"):
    raise ValueError("ID required")
"""

        block1 = ast.parse(code1).body
        block2 = ast.parse(code2).body

        # Unify the blocks
        hygienic_renames = [{}, {}]
        substitution = self.unifier.unify_blocks([block1, block2], hygienic_renames)

        self.assertIsNotNone(substitution, "Blocks should unify")

        # Extract function (only the validation part for simplicity)
        validation_code = """
if not var.get("id"):
    raise ValueError("ID required")
"""
        template_block = ast.parse(validation_code).body

        # Extract function with 'var' as parameter
        free_variables = {"var"}
        enclosing_names = set()

        func_def, param_order = self.extractor.extract_function(
            template_block=template_block,
            substitution=substitution,
            free_variables=free_variables,
            enclosing_names=enclosing_names,
            is_value_producing=False,
            return_variables=[],
            function_name="validate",
        )

        # Generate calls for both blocks
        call1 = self.extractor.generate_call(
            function_name="validate",
            block_idx=0,
            substitution=substitution,
            param_order=param_order,
            free_variables=free_variables,
            is_value_producing=False,
            return_variables=[],
        )

        call2 = self.extractor.generate_call(
            function_name="validate",
            block_idx=1,
            substitution=substitution,
            param_order=param_order,
            free_variables=free_variables,
            is_value_producing=False,
            return_variables=[],
        )

        # Check that calls use correct variable names
        call1_code = ast.unparse(call1)
        call2_code = ast.unparse(call2)

        # Call 1 should use 'user'
        self.assertIn("user", call1_code, f"Call 1 should use 'user', got: {call1_code}")

        # Call 2 should use 'admin' (NOT 'user')
        self.assertIn("admin", call2_code, f"Call 2 should use 'admin', got: {call2_code}")
        self.assertNotIn("user", call2_code, f"Call 2 should NOT use 'user', got: {call2_code}")

    def test_example1_simple_scenario(self):
        """
        Test the exact scenario from example1_simple.py.

        Three functions with different variable names (user, admin, guest)
        all have identical validation code.
        """
        # Simplified version of the validation blocks
        code_user = """
if not user.get("id"):
    raise ValueError("User ID is required")
if not user.get("name"):
    raise ValueError("User name is required")
"""

        code_admin = """
if not admin.get("id"):
    raise ValueError("User ID is required")
if not admin.get("name"):
    raise ValueError("User name is required")
"""

        code_guest = """
if not guest.get("id"):
    raise ValueError("User ID is required")
if not guest.get("name"):
    raise ValueError("User name is required")
"""

        block_user = ast.parse(code_user).body
        block_admin = ast.parse(code_admin).body
        block_guest = ast.parse(code_guest).body

        # Unify all three blocks
        hygienic_renames = [{}, {}, {}]
        substitution = self.unifier.unify_blocks(
            [block_user, block_admin, block_guest], hygienic_renames
        )

        self.assertIsNotNone(substitution, "All three blocks should unify")

        # The unified template uses a canonical name (e.g., 'user')
        # But hygienic_renames should map admin→user and guest→user

        # Extract function
        free_variables = {"user"}  # Canonical name
        enclosing_names = set()

        func_def, param_order = self.extractor.extract_function(
            template_block=block_user,  # Use first block as template
            substitution=substitution,
            free_variables=free_variables,
            enclosing_names=enclosing_names,
            is_value_producing=False,
            return_variables=[],
            function_name="__extracted_func_2",
        )

        # Generate calls for each block
        calls = []
        for block_idx in range(3):
            call = self.extractor.generate_call(
                function_name="__extracted_func_2",
                block_idx=block_idx,
                substitution=substitution,
                param_order=param_order,
                free_variables=free_variables,
                is_value_producing=False,
                return_variables=[],
            )
            calls.append(ast.unparse(call))

        # Verify each call uses the correct variable name
        self.assertIn("user", calls[0], f"Block 0 should use 'user', got: {calls[0]}")
        self.assertIn("admin", calls[1], f"Block 1 should use 'admin', got: {calls[1]}")
        self.assertIn("guest", calls[2], f"Block 2 should use 'guest', got: {calls[2]}")

        # Verify wrong names are NOT used
        self.assertNotIn("admin", calls[0], f"Block 0 should NOT use 'admin', got: {calls[0]}")
        self.assertNotIn("guest", calls[0], f"Block 0 should NOT use 'guest', got: {calls[0]}")
        self.assertNotIn("user", calls[1], f"Block 1 should NOT use 'user', got: {calls[1]}")
        self.assertNotIn("guest", calls[1], f"Block 1 should NOT use 'guest', got: {calls[1]}")
        self.assertNotIn("user", calls[2], f"Block 2 should NOT use 'user', got: {calls[2]}")
        self.assertNotIn("admin", calls[2], f"Block 2 should NOT use 'admin', got: {calls[2]}")


def main():
    """Run the tests."""
    unittest.main(verbosity=2)


if __name__ == "__main__":
    main()
