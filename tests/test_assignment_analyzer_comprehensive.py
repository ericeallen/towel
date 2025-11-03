#!/usr/bin/env python3
"""
Comprehensive unit tests for the assignment_analyzer module.

These tests isolate and test individual functions and methods to ensure
correct behavior of assignment classification and reassignment detection.
"""

import unittest
import ast
from src.towel.unification.assignment_analyzer import (
    analyze_assignments,
    AssignmentAnalyzer,
    has_reassignments_without_bindings,
    _collect_bindings_and_reassignments,
)


class TestAnalyzeAssignmentsBasics(unittest.TestCase):
    """Test basic analyze_assignments functionality."""

    def test_simple_initial_binding(self):
        """Test simple initial binding."""
        code = """
def foo():
    x = 1
"""
        tree = ast.parse(code)
        func = tree.body[0]

        result = analyze_assignments(func)

        # There should be one assignment
        self.assertEqual(len(result), 1, "Should have one assignment")

        # The assignment should be an initial binding (False = not a reassignment)
        for node_id, is_reassignment in result.items():
            self.assertFalse(is_reassignment, "First assignment should be initial binding")

    def test_simple_reassignment(self):
        """Test simple reassignment."""
        code = """
def foo():
    x = 1
    x = 2
"""
        tree = ast.parse(code)
        func = tree.body[0]

        result = analyze_assignments(func)

        # There should be two assignments
        self.assertEqual(len(result), 2, "Should have two assignments")

        # Count initial bindings and reassignments
        initial_bindings = sum(1 for is_reassign in result.values() if not is_reassign)
        reassignments = sum(1 for is_reassign in result.values() if is_reassign)

        self.assertEqual(initial_bindings, 1, "Should have one initial binding")
        self.assertEqual(reassignments, 1, "Should have one reassignment")

    def test_parameter_is_bound(self):
        """Test that function parameters are treated as bound variables."""
        code = """
def foo(x):
    x = 1
"""
        tree = ast.parse(code)
        func = tree.body[0]

        result = analyze_assignments(func)

        # The assignment to x is a reassignment (parameter already binds x)
        self.assertEqual(len(result), 1, "Should have one assignment")
        for node_id, is_reassignment in result.items():
            self.assertTrue(is_reassignment, "Assignment to parameter should be reassignment")

    def test_multiple_parameters(self):
        """Test handling of multiple parameters."""
        code = """
def foo(a, b, c):
    a = 1
    b = 2
    d = 3
"""
        tree = ast.parse(code)
        func = tree.body[0]

        result = analyze_assignments(func)

        self.assertEqual(len(result), 3, "Should have three assignments")

        # a and b are reassignments, d is initial binding
        reassignments = sum(1 for is_reassign in result.values() if is_reassign)
        initial_bindings = sum(1 for is_reassign in result.values() if not is_reassign)

        self.assertEqual(reassignments, 2, "a and b should be reassignments")
        self.assertEqual(initial_bindings, 1, "d should be initial binding")


class TestAssignmentAnalyzerAdvancedParameters(unittest.TestCase):
    """Test handling of advanced parameter types."""

    def test_varargs_parameter(self):
        """Test that *args parameter is bound."""
        code = """
def foo(*args):
    args = []
"""
        tree = ast.parse(code)
        func = tree.body[0]

        result = analyze_assignments(func)

        # Assignment to args is a reassignment
        for node_id, is_reassignment in result.items():
            self.assertTrue(is_reassignment, "Assignment to *args should be reassignment")

    def test_kwargs_parameter(self):
        """Test that **kwargs parameter is bound."""
        code = """
def foo(**kwargs):
    kwargs = {}
"""
        tree = ast.parse(code)
        func = tree.body[0]

        result = analyze_assignments(func)

        # Assignment to kwargs is a reassignment
        for node_id, is_reassignment in result.items():
            self.assertTrue(is_reassignment, "Assignment to **kwargs should be reassignment")

    def test_kwonly_parameters(self):
        """Test keyword-only parameters."""
        code = """
def foo(*, x, y):
    x = 1
    z = 2
"""
        tree = ast.parse(code)
        func = tree.body[0]

        result = analyze_assignments(func)

        self.assertEqual(len(result), 2, "Should have two assignments")

        # x is reassignment, z is initial binding
        reassignments = sum(1 for is_reassign in result.values() if is_reassign)
        initial_bindings = sum(1 for is_reassign in result.values() if not is_reassign)

        self.assertEqual(reassignments, 1, "x should be reassignment")
        self.assertEqual(initial_bindings, 1, "z should be initial binding")


class TestAssignmentAnalyzerComplexTargets(unittest.TestCase):
    """Test handling of complex assignment targets."""

    def test_tuple_unpacking_initial(self):
        """Test tuple unpacking as initial binding."""
        code = """
def foo():
    a, b = (1, 2)
"""
        tree = ast.parse(code)
        func = tree.body[0]

        result = analyze_assignments(func)

        # Should be initial binding
        for node_id, is_reassignment in result.items():
            self.assertFalse(is_reassignment, "Tuple unpacking should be initial binding")

    def test_tuple_unpacking_reassignment(self):
        """Test tuple unpacking with one variable already bound."""
        code = """
def foo():
    a = 1
    a, b = (2, 3)
"""
        tree = ast.parse(code)
        func = tree.body[0]

        result = analyze_assignments(func)

        self.assertEqual(len(result), 2, "Should have two assignments")

        # First is initial, second is reassignment (because 'a' was bound)
        assignments = list(result.values())
        self.assertFalse(assignments[0], "First should be initial binding")
        self.assertTrue(assignments[1], "Second should be reassignment (a is already bound)")

    def test_list_unpacking(self):
        """Test list unpacking."""
        code = """
def foo():
    [x, y, z] = [1, 2, 3]
"""
        tree = ast.parse(code)
        func = tree.body[0]

        result = analyze_assignments(func)

        # Should be initial binding
        for node_id, is_reassignment in result.items():
            self.assertFalse(is_reassignment, "List unpacking should be initial binding")

    def test_nested_unpacking(self):
        """Test nested unpacking."""
        code = """
def foo():
    (a, (b, c)) = (1, (2, 3))
"""
        tree = ast.parse(code)
        func = tree.body[0]

        result = analyze_assignments(func)

        # Should be initial binding
        for node_id, is_reassignment in result.items():
            self.assertFalse(is_reassignment, "Nested unpacking should be initial binding")


class TestAugmentedAssignment(unittest.TestCase):
    """Test augmented assignment handling."""

    def test_augmented_assignment_always_reassignment(self):
        """Test that augmented assignments are always reassignments."""
        code = """
def foo():
    x = 1
    x += 2
"""
        tree = ast.parse(code)
        func = tree.body[0]

        result = analyze_assignments(func)

        self.assertEqual(len(result), 2, "Should have two assignments")

        # Both should be marked (first as initial, second as reassignment)
        assignments = list(result.values())
        self.assertFalse(assignments[0], "First should be initial binding")
        self.assertTrue(assignments[1], "Augmented assignment should be reassignment")

    def test_augmented_assignment_without_initial(self):
        """Test augmented assignment without prior binding."""
        code = """
def foo():
    x += 1
"""
        tree = ast.parse(code)
        func = tree.body[0]

        result = analyze_assignments(func)

        # Should be marked as reassignment (even though it would error at runtime)
        self.assertEqual(len(result), 1, "Should have one assignment")
        for node_id, is_reassignment in result.items():
            self.assertTrue(is_reassignment, "Augmented assignment should be reassignment")


class TestForLoopHandling(unittest.TestCase):
    """Test for loop variable handling."""

    def test_for_loop_creates_binding(self):
        """Test that for loop variable creates a binding."""
        code = """
def foo():
    for i in range(10):
        pass
    i = 5
"""
        tree = ast.parse(code)
        func = tree.body[0]

        result = analyze_assignments(func)

        # Only the explicit assignment is recorded, and it's a reassignment
        # because the for loop binds 'i'
        self.assertEqual(len(result), 1, "Should have one explicit assignment")
        for node_id, is_reassignment in result.items():
            self.assertTrue(is_reassignment, "Assignment after for loop should be reassignment")

    def test_for_loop_tuple_unpacking(self):
        """Test for loop with tuple unpacking."""
        code = """
def foo():
    for x, y in [(1, 2), (3, 4)]:
        pass
    x = 10
"""
        tree = ast.parse(code)
        func = tree.body[0]

        result = analyze_assignments(func)

        # Assignment to x is a reassignment
        for node_id, is_reassignment in result.items():
            self.assertTrue(is_reassignment, "Assignment after for loop should be reassignment")


class TestWithStatementHandling(unittest.TestCase):
    """Test with statement 'as' clause handling."""

    def test_with_statement_creates_binding(self):
        """Test that with statement 'as' clause creates a binding."""
        code = """
def foo():
    with open('file.txt') as f:
        pass
    f = None
"""
        tree = ast.parse(code)
        func = tree.body[0]

        result = analyze_assignments(func)

        # Assignment to f is a reassignment
        for node_id, is_reassignment in result.items():
            self.assertTrue(is_reassignment, "Assignment after with should be reassignment")

    def test_with_statement_no_as_clause(self):
        """Test with statement without 'as' clause."""
        code = """
def foo():
    with open('file.txt'):
        x = 1
"""
        tree = ast.parse(code)
        func = tree.body[0]

        result = analyze_assignments(func)

        # x is initial binding
        for node_id, is_reassignment in result.items():
            self.assertFalse(is_reassignment, "Should be initial binding")


class TestNestedFunctionHandling(unittest.TestCase):
    """Test that nested functions are not descended into."""

    def test_nested_function_not_analyzed(self):
        """Test that nested function assignments are not analyzed."""
        code = """
def outer():
    x = 1
    def inner():
        y = 2
        y = 3
    x = 4
"""
        tree = ast.parse(code)
        func = tree.body[0]

        result = analyze_assignments(func)

        # Should only have 2 assignments (x=1 and x=4), not the inner function's assignments
        self.assertEqual(len(result), 2, "Should only analyze outer function assignments")

        assignments = list(result.values())
        self.assertFalse(assignments[0], "First x assignment is initial")
        self.assertTrue(assignments[1], "Second x assignment is reassignment")


class TestComprehensionHandling(unittest.TestCase):
    """Test that comprehensions are not descended into."""

    def test_list_comprehension_not_analyzed(self):
        """Test that list comprehension variables are not analyzed."""
        code = """
def foo():
    result = [x * 2 for x in range(10)]
    x = 5
"""
        tree = ast.parse(code)
        func = tree.body[0]

        result = analyze_assignments(func)

        # Should have 2 assignments: result and x
        self.assertEqual(len(result), 2, "Should have two assignments")

        # Both are initial bindings (comprehension scope is separate)
        for node_id, is_reassignment in result.items():
            self.assertFalse(is_reassignment, "Both should be initial bindings")

    def test_dict_comprehension(self):
        """Test dict comprehension handling."""
        code = """
def foo():
    result = {k: v for k, v in items}
"""
        tree = ast.parse(code)
        func = tree.body[0]

        result = analyze_assignments(func)

        # Only result assignment
        self.assertEqual(len(result), 1, "Should have one assignment")
        for node_id, is_reassignment in result.items():
            self.assertFalse(is_reassignment, "Should be initial binding")


class TestHasReassignmentsWithoutBindings(unittest.TestCase):
    """Test has_reassignments_without_bindings function."""

    def test_safe_block_no_reassignments(self):
        """Test block with no reassignments is safe."""
        code = """
def foo():
    x = 1
    y = 2
    return x + y
"""
        tree = ast.parse(code)
        func = tree.body[0]

        reassignments = analyze_assignments(func)
        block_nodes = func.body  # All statements

        has_unsafe, problematic = has_reassignments_without_bindings(
            func, block_nodes, reassignments
        )

        self.assertFalse(has_unsafe, "Block with no reassignments should be safe")
        self.assertEqual(len(problematic), 0, "Should have no problematic variables")

    def test_safe_block_with_local_binding_and_reassignment(self):
        """Test block with both binding and reassignment is safe."""
        code = """
def foo():
    x = 1
    x = 2
    return x
"""
        tree = ast.parse(code)
        func = tree.body[0]

        reassignments = analyze_assignments(func)
        block_nodes = func.body  # All statements

        has_unsafe, problematic = has_reassignments_without_bindings(
            func, block_nodes, reassignments
        )

        self.assertFalse(has_unsafe, "Block with local binding+reassignment should be safe")
        self.assertEqual(len(problematic), 0, "Should have no problematic variables")

    def test_unsafe_block_reassignment_without_binding(self):
        """Test block with reassignment but no binding is unsafe."""
        code = """
def foo():
    x = 1      # Line 2: initial binding
    y = 2      # Line 3: block starts here
    x = 3      # Line 4: reassignment of x (bound on line 2)
    return x + y
"""
        tree = ast.parse(code)
        func = tree.body[0]

        reassignments = analyze_assignments(func)

        # Extract lines 3-5 (y=2, x=3, return)
        block_nodes = func.body[1:]  # Skip first statement (x=1)

        has_unsafe, problematic = has_reassignments_without_bindings(
            func, block_nodes, reassignments
        )

        self.assertTrue(has_unsafe, "Block should be unsafe")
        self.assertIn("x", problematic, "x should be problematic (reassigned without binding)")

    def test_safe_partial_block(self):
        """Test partial block that includes the initial binding."""
        code = """
def foo():
    x = 1
    x = 2
    return x
"""
        tree = ast.parse(code)
        func = tree.body[0]

        reassignments = analyze_assignments(func)

        # Extract all statements (includes both x=1 and x=2)
        block_nodes = func.body

        has_unsafe, problematic = has_reassignments_without_bindings(
            func, block_nodes, reassignments
        )

        self.assertFalse(has_unsafe, "Block including initial binding should be safe")
        self.assertEqual(len(problematic), 0, "Should have no problematic variables")


class TestCollectBindingsAndReassignments(unittest.TestCase):
    """Test _collect_bindings_and_reassignments helper function."""

    def test_collects_initial_bindings(self):
        """Test collecting initial bindings."""
        code = """
def foo():
    x = 1
    y = 2
"""
        tree = ast.parse(code)
        func = tree.body[0]

        reassignments = analyze_assignments(func)
        bound_vars = set()
        reassigned_vars = set()

        for node in func.body:
            _collect_bindings_and_reassignments(node, reassignments, bound_vars, reassigned_vars)

        self.assertEqual(bound_vars, {"x", "y"}, "Should collect both bindings")
        self.assertEqual(len(reassigned_vars), 0, "Should have no reassignments")

    def test_collects_reassignments(self):
        """Test collecting reassignments."""
        code = """
def foo():
    x = 1
    x = 2
"""
        tree = ast.parse(code)
        func = tree.body[0]

        reassignments = analyze_assignments(func)
        bound_vars = set()
        reassigned_vars = set()

        for node in func.body:
            _collect_bindings_and_reassignments(node, reassignments, bound_vars, reassigned_vars)

        self.assertIn("x", bound_vars, "x should be in bound_vars")
        self.assertIn("x", reassigned_vars, "x should be in reassigned_vars")

    def test_collects_for_loop_bindings(self):
        """Test collecting for loop variable bindings."""
        code = """
def foo():
    for i in range(10):
        x = i
"""
        tree = ast.parse(code)
        func = tree.body[0]

        reassignments = analyze_assignments(func)
        bound_vars = set()
        reassigned_vars = set()

        for node in func.body:
            _collect_bindings_and_reassignments(node, reassignments, bound_vars, reassigned_vars)

        self.assertIn("i", bound_vars, "i should be bound by for loop")
        self.assertIn("x", bound_vars, "x should be bound in loop body")

    def test_collects_with_bindings(self):
        """Test collecting with statement bindings."""
        code = """
def foo():
    with open('file.txt') as f:
        x = f.read()
"""
        tree = ast.parse(code)
        func = tree.body[0]

        reassignments = analyze_assignments(func)
        bound_vars = set()
        reassigned_vars = set()

        for node in func.body:
            _collect_bindings_and_reassignments(node, reassignments, bound_vars, reassigned_vars)

        self.assertIn("f", bound_vars, "f should be bound by with statement")
        self.assertIn("x", bound_vars, "x should be bound in with body")

    def test_ignores_nested_functions(self):
        """Test that nested functions are ignored."""
        code = """
def foo():
    x = 1
    def inner():
        y = 2
"""
        tree = ast.parse(code)
        func = tree.body[0]

        reassignments = analyze_assignments(func)
        bound_vars = set()
        reassigned_vars = set()

        for node in func.body:
            _collect_bindings_and_reassignments(node, reassignments, bound_vars, reassigned_vars)

        self.assertEqual(bound_vars, {"x"}, "Should only collect outer function bindings")
        self.assertNotIn("y", bound_vars, "Should not collect nested function bindings")


class TestEdgeCases(unittest.TestCase):
    """Test edge cases and boundary conditions."""

    def test_empty_function(self):
        """Test analyzing empty function."""
        code = """
def foo():
    pass
"""
        tree = ast.parse(code)
        func = tree.body[0]

        result = analyze_assignments(func)

        self.assertEqual(len(result), 0, "Empty function should have no assignments")

    def test_function_with_only_expression(self):
        """Test function with only expression statement."""
        code = """
def foo():
    42
"""
        tree = ast.parse(code)
        func = tree.body[0]

        result = analyze_assignments(func)

        self.assertEqual(len(result), 0, "Expression-only function should have no assignments")

    def test_multiple_targets_in_assignment(self):
        """Test assignment with multiple targets."""
        code = """
def foo():
    x = y = z = 1
"""
        tree = ast.parse(code)
        func = tree.body[0]

        result = analyze_assignments(func)

        # This creates one Assign node with multiple targets
        # Should be tracked as initial binding
        self.assertEqual(len(result), 1, "Should have one assignment node")
        for node_id, is_reassignment in result.items():
            self.assertFalse(is_reassignment, "Should be initial binding")

    def test_attribute_assignment_ignored(self):
        """Test that attribute assignments don't create bindings."""
        code = """
def foo():
    obj = SomeClass()
    obj.attr = 1
    obj.attr = 2
"""
        tree = ast.parse(code)
        func = tree.body[0]

        result = analyze_assignments(func)

        # Should have 3 assignments total
        # First is initial (obj), second is initial (obj.attr first time),
        # third is reassignment (obj.attr second time)
        # Actually, attribute assignments create assignment nodes but don't
        # bind variable names, so they're tracked based on whether any Name
        # in the target is a reassignment
        self.assertEqual(len(result), 3, "Should have three assignment nodes")

    def test_subscript_assignment_ignored(self):
        """Test that subscript assignments don't create bindings."""
        code = """
def foo():
    lst = []
    lst[0] = 1
"""
        tree = ast.parse(code)
        func = tree.body[0]

        result = analyze_assignments(func)

        # Should have 2 assignments
        self.assertEqual(len(result), 2, "Should have two assignment nodes")


def main():
    """Run the tests."""
    unittest.main(verbosity=2)


if __name__ == "__main__":
    main()
