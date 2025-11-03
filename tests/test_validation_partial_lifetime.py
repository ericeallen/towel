#!/usr/bin/env python3
"""
Isolated unit tests for partial lifetime extraction validation.

This test file exposes the issue where the refactoring engine incorrectly
allows extracting the initial binding of a variable without its complete
lifetime, causing UnboundLocalError when the variable is used after the
extracted block.
"""

import unittest
import os
import ast
from src.towel.unification.refactor_engine import UnificationRefactorEngine


class TestPartialLifetimeValidation(unittest.TestCase):
    """Test validation prevents partial lifetime extraction."""

    def setUp(self):
        """Enable debug validation logging."""
        os.environ["DEBUG_VALIDATION"] = "1"

    def tearDown(self):
        """Clean up environment."""
        if "DEBUG_VALIDATION" in os.environ:
            del os.environ["DEBUG_VALIDATION"]

    def test_reject_partial_lifetime_extraction(self):
        """
        Test that engine rejects extracting initial binding without complete lifetime.

        This tests the specific pattern found in syntactic_coverage_comprehensive.py:

        def comparisons_a(x, y):
            result = 1 if x == y else 0      # Initial binding
            result += 1 if x != y else 0     # Augmented assignment (reassignment)
            result += 1 if x < y else 0      # Augmented assignment
            result += 1 if x <= y else 0     # Augmented assignment
            return result                     # Use after block

        The problem: If we extract lines 2-5 (initial binding + some reassignments),
        the extracted function creates a LOCAL `result` that never escapes.
        The original function still needs `result` for the return statement,
        causing UnboundLocalError.

        Expected: Engine should REJECT this extraction because:
        1. The block contains an initial binding of `result`
        2. `result` is used after the block (in the return statement)
        3. This violates the principle that bindings must include their complete lifetime
        """
        engine = UnificationRefactorEngine(max_parameters=5, min_lines=4)

        # Analyze the file that has the problematic pattern
        proposals = engine.analyze_file("test_examples/syntactic_coverage_comprehensive.py")

        # Look for the problematic proposal involving comparisons_a and calls_a
        # This proposal should NOT exist if validation is working
        problematic_proposal = None
        for proposal in proposals:
            desc = proposal.description.lower()
            if "comparisons_a" in desc and "calls_a" in desc:
                problematic_proposal = proposal
                break

        # The validation should have rejected this proposal
        self.assertIsNone(
            problematic_proposal,
            "Engine should reject extraction that binds variables used after the block. "
            "Found problematic proposal: "
            + (problematic_proposal.description if problematic_proposal else "None"),
        )

    def test_validation_logic_directly(self):
        """
        Test validation logic directly with a minimal example.

        This creates a simple test case to verify the validation works
        without relying on the full proposal generation pipeline.
        """
        # Create a minimal test case
        code = """
def test_func(x):
    result = x + 1      # Initial binding
    result += 2         # Reassignment
    result += 3         # Reassignment
    return result       # Use after block
"""

        tree = ast.parse(code)
        func = tree.body[0]

        # If we extract lines 2-4 (result = x + 1, result += 2, result += 3)
        # we should detect that:
        # 1. `result` is initially bound in the block
        # 2. `result` is used after the block (line 5: return result)
        # 3. This is unsafe

        # Test the helper method directly
        engine = UnificationRefactorEngine(max_parameters=5, min_lines=4)

        # Get the statements that would be extracted (lines 2-4)
        block_stmts = func.body[0:3]  # First 3 statements

        # Check if any initially bound variables are used after
        from src.towel.unification.assignment_analyzer import _collect_bindings_and_reassignments

        bound_in_block = set()
        reassigned_in_block = set()
        reassignments = {}

        for node in block_stmts:
            _collect_bindings_and_reassignments(
                node, reassignments, bound_in_block, reassigned_in_block
            )

        # Find variables bound BEFORE the block starts
        # In this test case, the block starts at the beginning of the function,
        # so there are no variables bound before the block
        bound_before_block = set()

        # Variables that are newly introduced in the block
        initially_bound = bound_in_block - bound_before_block

        # Check if result is initially bound
        self.assertIn(
            "result", initially_bound, "Should detect that 'result' is initially bound in the block"
        )

        # Check if result is used after the block
        block_end_line = block_stmts[-1].lineno
        uses_after = set()

        for stmt in func.body:
            if hasattr(stmt, "lineno") and stmt.lineno > block_end_line:
                uses = engine._get_used_names(stmt)
                uses_after.update(uses)

        self.assertIn("result", uses_after, "Should detect that 'result' is used after the block")

        # Check for conflict
        conflict = initially_bound & uses_after
        self.assertEqual(
            conflict, {"result"}, "Should detect conflict: variable bound in block but used after"
        )


def main():
    """Run the tests."""
    unittest.main(verbosity=2)


if __name__ == "__main__":
    main()
