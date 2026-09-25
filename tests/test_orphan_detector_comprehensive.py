#!/usr/bin/env python3
# Copyright 2025-2026 Eric Allen
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Comprehensive unit tests for the orphan_detector module.

These tests verify that orphaned variable detection works correctly
for various code extraction scenarios.
"""

import unittest
import ast
from towel.unification.orphan_detector import (
    bound_names_in_block,
    used_names,
    orphaned_variables,
)


class TestGetBoundVariablesBasic(unittest.TestCase):
    """Test bound_names_in_block with basic binding types."""

    def test_simple_assignment(self):
        """Test simple assignment binding."""
        code = "x = 1"
        tree = ast.parse(code)

        result = bound_names_in_block(tree.body)

        self.assertIn("x", result, "Should find x as bound variable")
        self.assertEqual(len(result), 1, "Should have exactly one binding")

    def test_multiple_assignments(self):
        """Test multiple separate assignments."""
        code = """
x = 1
y = 2
z = 3
"""
        tree = ast.parse(code)

        result = bound_names_in_block(tree.body)

        self.assertEqual(result, {"x", "y", "z"}, "Should find all bound variables")

    def test_multiple_targets_in_one_assignment(self):
        """Test assignment with multiple targets."""
        code = "x = y = 1"
        tree = ast.parse(code)

        result = bound_names_in_block(tree.body)

        self.assertIn("x", result, "Should find x")
        self.assertIn("y", result, "Should find y")

    def test_tuple_unpacking(self):
        """Test tuple unpacking assignment."""
        code = "x, y = (1, 2)"
        tree = ast.parse(code)

        result = bound_names_in_block(tree.body)

        self.assertEqual(result, {"x", "y"}, "Should find all unpacked variables")

    def test_nested_tuple_unpacking(self):
        """Test nested tuple unpacking."""
        code = "x, (y, z) = (1, (2, 3))"
        tree = ast.parse(code)

        result = bound_names_in_block(tree.body)

        self.assertEqual(result, {"x", "y", "z"}, "Should find all nested variables")

    def test_starred_assignment(self):
        """Test starred assignment unpacking."""
        code = "x, *y, z = [1, 2, 3, 4, 5]"
        tree = ast.parse(code)

        result = bound_names_in_block(tree.body)

        self.assertEqual(result, {"x", "y", "z"}, "Should find all variables including starred")


class TestGetBoundVariablesAdvanced(unittest.TestCase):
    """Test bound_names_in_block with advanced binding types."""

    def test_for_loop_binding(self):
        """Test for loop target binding."""
        code = """
for i in range(10):
    pass
"""
        tree = ast.parse(code)

        result = bound_names_in_block(tree.body)

        self.assertIn("i", result, "Should find loop variable")

    def test_for_loop_tuple_unpacking(self):
        """Test for loop with tuple unpacking."""
        code = """
for x, y in pairs:
    pass
"""
        tree = ast.parse(code)

        result = bound_names_in_block(tree.body)

        self.assertEqual(result, {"x", "y"}, "Should find all loop variables")

    def test_function_definition(self):
        """Test function definition creates binding."""
        code = """
def foo():
    pass
"""
        tree = ast.parse(code)

        result = bound_names_in_block(tree.body)

        self.assertIn("foo", result, "Should find function name")

    def test_async_function_definition(self):
        """Test async function definition creates binding."""
        code = """
async def foo():
    pass
"""
        tree = ast.parse(code)

        result = bound_names_in_block(tree.body)

        self.assertIn("foo", result, "Should find async function name")

    def test_class_definition(self):
        """Test class definition creates binding."""
        code = """
class MyClass:
    pass
"""
        tree = ast.parse(code)

        result = bound_names_in_block(tree.body)

        self.assertIn("MyClass", result, "Should find class name")

    def test_annotated_assignment(self):
        """Test annotated assignment."""
        code = "x: int = 1"
        tree = ast.parse(code)

        result = bound_names_in_block(tree.body)

        self.assertIn("x", result, "Should find annotated variable")

    def test_augmented_assignment(self):
        """Test augmented assignment."""
        code = "x += 1"
        tree = ast.parse(code)

        result = bound_names_in_block(tree.body)

        self.assertIn("x", result, "Should find augmented assignment target")


class TestGetBoundVariablesComprehensions(unittest.TestCase):
    """Test that comprehension variables are not collected."""

    def test_list_comprehension_not_bound(self):
        """Test that list comprehension variables are not collected."""
        code = "result = [x * 2 for x in range(10)]"
        tree = ast.parse(code)

        result = bound_names_in_block(tree.body)

        self.assertIn("result", result, "Should find result variable")
        self.assertNotIn("x", result, "Should NOT find comprehension variable")

    def test_set_comprehension_not_bound(self):
        """Test that set comprehension variables are not collected."""
        code = "result = {x * 2 for x in range(10)}"
        tree = ast.parse(code)

        result = bound_names_in_block(tree.body)

        self.assertIn("result", result)
        self.assertNotIn("x", result, "Should NOT find set comp variable")

    def test_dict_comprehension_not_bound(self):
        """Test that dict comprehension variables are not collected."""
        code = "result = {x: x*2 for x in range(10)}"
        tree = ast.parse(code)

        result = bound_names_in_block(tree.body)

        self.assertIn("result", result)
        self.assertNotIn("x", result, "Should NOT find dict comp variable")

    def test_generator_expression_not_bound(self):
        """Test that generator expression variables are not collected."""
        code = "result = (x * 2 for x in range(10))"
        tree = ast.parse(code)

        result = bound_names_in_block(tree.body)

        self.assertIn("result", result)
        self.assertNotIn("x", result, "Should NOT find generator variable")


class TestGetBoundVariablesIgnoresNonBindings(unittest.TestCase):
    """Test that non-binding constructs are ignored."""

    def test_ignores_subscript_targets(self):
        """Test that subscript assignments don't create bindings."""
        code = "arr[0] = 1"
        tree = ast.parse(code)

        result = bound_names_in_block(tree.body)

        self.assertNotIn("arr", result, "Subscript target should not create binding")
        self.assertEqual(len(result), 0, "Should have no bindings")

    def test_ignores_attribute_targets(self):
        """Test that attribute assignments don't create bindings."""
        code = "obj.attr = 1"
        tree = ast.parse(code)

        result = bound_names_in_block(tree.body)

        self.assertNotIn("obj", result, "Attribute target should not create binding")
        self.assertEqual(len(result), 0, "Should have no bindings")

    def test_nested_function_not_traversed(self):
        """Test that nested functions are not traversed."""
        code = """
def outer():
    x = 1
"""
        tree = ast.parse(code)

        result = bound_names_in_block(tree.body)

        self.assertIn("outer", result, "Should find outer function name")
        self.assertNotIn("x", result, "Should NOT traverse into nested function")


class TestGetUsedVariables(unittest.TestCase):
    """Test used_names function."""

    def test_simple_usage(self):
        """Test simple variable usage."""
        code = "y = x"
        tree = ast.parse(code)
        block: list[ast.AST] = list(tree.body)

        result = used_names(block)

        self.assertIn("x", result, "Should find used variable x")
        self.assertNotIn("y", result, "Should not include assignment target")

    def test_multiple_uses(self):
        """Test multiple variable uses."""
        code = "result = x + y - z"
        tree = ast.parse(code)
        block: list[ast.AST] = list(tree.body)

        result = used_names(block)

        self.assertEqual(result, {"x", "y", "z"}, "Should find all used variables")

    def test_expression_usage(self):
        """Test variable usage in expressions."""
        code = "result = (a + b) * c"
        tree = ast.parse(code)
        block: list[ast.AST] = list(tree.body)

        result = used_names(block)

        self.assertEqual(result, {"a", "b", "c"}, "Should find all expression variables")

    def test_function_call_usage(self):
        """Test variable usage in function calls."""
        code = "result = foo(x, y)"
        tree = ast.parse(code)
        block: list[ast.AST] = list(tree.body)

        result = used_names(block)

        self.assertIn("foo", result, "Should find function name")
        self.assertIn("x", result, "Should find argument x")
        self.assertIn("y", result, "Should find argument y")

    def test_no_store_context(self):
        """Test that Store context names are not collected."""
        code = "x = 1"
        tree = ast.parse(code)
        block: list[ast.AST] = list(tree.body)

        result = used_names(block)

        self.assertNotIn("x", result, "Should not collect Store context name")

    def test_empty_code(self):
        """Test empty code block."""
        result = used_names([])

        self.assertEqual(len(result), 0, "Empty code should have no uses")


class TestHasOrphanedVariablesBasic(unittest.TestCase):
    """Test orphaned_variables basic functionality."""

    def test_no_orphans_when_no_remaining_code(self):
        """Test no orphans when extracting to end of function."""
        code = """
def foo():
    x = 1
    y = x + 1
"""
        tree = ast.parse(code)
        func = tree.body[0]
        assert isinstance(func, ast.FunctionDef)

        orphaned = orphaned_variables(func.body, (0, 1))
        self.assertEqual(orphaned, set())

    def test_orphan_detected(self):
        """Test orphan is detected when variable is used after extraction."""
        code = """
def foo():
    x = 1
    y = x + 1
"""
        tree = ast.parse(code)
        func = tree.body[0]
        assert isinstance(func, ast.FunctionDef)

        orphaned = orphaned_variables(func.body, (0, 0))
        self.assertEqual(orphaned, {"x"})

    def test_no_orphan_when_rebound_in_remaining(self):
        """Test no orphan when variable is rebound in remaining code."""
        code = """
def foo():
    x = 1
    x = 2
    y = x + 1
"""
        tree = ast.parse(code)
        func = tree.body[0]
        assert isinstance(func, ast.FunctionDef)

        orphaned = orphaned_variables(func.body, (0, 0))
        self.assertEqual(orphaned, set())

    def test_no_orphan_when_not_used(self):
        """Test no orphan when variable is bound but not used."""
        code = """
def foo():
    x = 1
    y = 2
"""
        tree = ast.parse(code)
        func = tree.body[0]
        assert isinstance(func, ast.FunctionDef)

        orphaned = orphaned_variables(func.body, (0, 0))
        self.assertEqual(orphaned, set())


class TestHasOrphanedVariablesAdvanced(unittest.TestCase):
    """Test orphaned_variables with advanced scenarios."""

    def test_multiple_orphans(self):
        """Test detection of multiple orphaned variables."""
        code = """
def foo():
    x = 1
    y = 2
    z = x + y
"""
        tree = ast.parse(code)
        func = tree.body[0]
        assert isinstance(func, ast.FunctionDef)

        orphaned = orphaned_variables(func.body, (0, 1))
        self.assertEqual(orphaned, {"x", "y"})

    def test_partial_rebinding(self):
        """Test when only some variables are rebound."""
        code = """
def foo():
    x = 1
    y = 2
    x = 3
    z = x + y
"""
        tree = ast.parse(code)
        func = tree.body[0]
        assert isinstance(func, ast.FunctionDef)

        orphaned = orphaned_variables(func.body, (0, 1))
        self.assertEqual(orphaned, {"y"})

    def test_for_loop_binding_in_extracted(self):
        """Test for loop variable in extracted block."""
        code = """
def foo():
    for i in range(10):
        pass
    x = i
"""
        tree = ast.parse(code)
        func = tree.body[0]
        assert isinstance(func, ast.FunctionDef)

        orphaned = orphaned_variables(func.body, (0, 0))
        self.assertEqual(orphaned, {"i"})

    def test_function_def_binding_in_extracted(self):
        """Test function definition in extracted block."""
        code = """
def foo():
    def helper():
        pass
    result = helper()
"""
        tree = ast.parse(code)
        func = tree.body[0]
        assert isinstance(func, ast.FunctionDef)

        orphaned = orphaned_variables(func.body, (0, 0))
        self.assertEqual(orphaned, {"helper"})

    def test_extraction_range_boundaries(self):
        """Test extraction range is inclusive of end index."""
        code = """
def foo():
    x = 1
    y = 2
    z = 3
    result = x + y + z
"""
        tree = ast.parse(code)
        func = tree.body[0]
        assert isinstance(func, ast.FunctionDef)

        # Extract first 3 lines (indices 0, 1, 2)
        orphaned = orphaned_variables(func.body, (0, 2))
        self.assertEqual(orphaned, {"x", "y", "z"})


class TestHasOrphanedVariablesEdgeCases(unittest.TestCase):
    """Test edge cases for orphaned_variables."""

    def test_empty_extracted_block(self):
        """Test with empty extracted block (single statement)."""
        code = """
def foo():
    x = 1
"""
        tree = ast.parse(code)
        func = tree.body[0]
        assert isinstance(func, ast.FunctionDef)

        orphaned = orphaned_variables(func.body, (0, 0))
        self.assertEqual(orphaned, set())

    def test_complex_expression_usage(self):
        """Test orphan detection in complex expressions."""
        code = """
def foo():
    x = 1
    result = x * 2 + x * 3
"""
        tree = ast.parse(code)
        func = tree.body[0]
        assert isinstance(func, ast.FunctionDef)

        orphaned = orphaned_variables(func.body, (0, 0))
        self.assertEqual(orphaned, {"x"})

    def test_augmented_assignment_in_remaining(self):
        """Test augmented assignment in remaining code."""
        code = """
def foo():
    x = 1
    x += 1
"""
        tree = ast.parse(code)
        func = tree.body[0]
        assert isinstance(func, ast.FunctionDef)

        orphaned = orphaned_variables(func.body, (0, 0))
        # An augmented assignment reads x before it rebinds it: moving the
        # block's binding away leaves that read without one, unless the
        # helper returns x.
        self.assertEqual(orphaned, {"x"})

    def test_no_bindings_in_extracted(self):
        """Test when extracted block has no bindings."""
        code = """
def foo():
    print("hello")
    x = 1
"""
        tree = ast.parse(code)
        func = tree.body[0]
        assert isinstance(func, ast.FunctionDef)

        orphaned = orphaned_variables(func.body, (0, 0))
        self.assertEqual(orphaned, set())


class TestIntegration(unittest.TestCase):
    """Integration tests combining multiple features."""

    def test_realistic_extraction_scenario(self):
        """Test realistic code extraction scenario."""
        code = """
def process_data():
    data = load_data()
    cleaned = clean_data(data)
    transformed = transform_data(cleaned)
    result = analyze_data(transformed)
    return result
"""
        tree = ast.parse(code)
        func = tree.body[0]
        assert isinstance(func, ast.FunctionDef)

        # Extract middle operations (lines 1-2: cleaned and transformed)
        orphaned = orphaned_variables(func.body, (1, 2))
        self.assertEqual(orphaned, {"transformed"})

    def test_no_orphans_when_extracting_independent_code(self):
        """Test extracting independent code has no orphans."""
        code = """
def foo():
    x = 1
    independent_operation()
    y = 2
"""
        tree = ast.parse(code)
        func = tree.body[0]
        assert isinstance(func, ast.FunctionDef)

        orphaned = orphaned_variables(func.body, (1, 1))
        self.assertEqual(orphaned, set())

    def test_all_bound_variables_used(self):
        """Test when all bound variables are used after extraction."""
        code = """
def foo():
    a = 1
    b = 2
    c = 3
    result = a + b + c
"""
        tree = ast.parse(code)
        func = tree.body[0]
        assert isinstance(func, ast.FunctionDef)

        orphaned = orphaned_variables(func.body, (0, 2))
        self.assertEqual(orphaned, {"a", "b", "c"})

    def test_mixed_binding_types(self):
        """Test extraction with mixed binding types."""
        code = """
def foo():
    x = 1
    for i in range(10):
        pass
    def helper():
        pass
    result = x + i + helper()
"""
        tree = ast.parse(code)
        func = tree.body[0]
        assert isinstance(func, ast.FunctionDef)

        orphaned = orphaned_variables(func.body, (0, 2))
        self.assertEqual(orphaned, {"helper", "i", "x"})


def main():
    """Run the tests."""
    unittest.main(verbosity=2)


if __name__ == "__main__":
    main()
