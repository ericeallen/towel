#!/usr/bin/env python3
"""
Comprehensive unit tests for the extractor module.

These tests isolate and test individual functions and methods to ensure
correct behavior of code extraction, hygiene checking, and helper functions.
"""

import unittest
import ast
from typing import cast
from src.towel.unification.extractor import (
    contains_return,
    is_value_producing,
    has_complete_return_coverage,
    get_enclosing_names,
    HygienicExtractor,
)
from src.towel.unification.scope_analyzer import Scope, ScopeAnalyzer
from src.towel.unification.unifier import Substitution


class TestContainsReturn(unittest.TestCase):
    """Test contains_return function."""

    def test_simple_return(self):
        """Test detecting a simple return statement."""
        code = "return 42"
        tree = ast.parse(code)

        result = contains_return(tree.body)

        self.assertTrue(result, "Should detect return statement")

    def test_no_return(self):
        """Test code without return statement."""
        code = "x = 1\ny = 2"
        tree = ast.parse(code)

        result = contains_return(tree.body)

        self.assertFalse(result, "Should not detect return in code without return")

    def test_return_in_if(self):
        """Test detecting return inside if statement."""
        code = """
if condition:
    return 42
"""
        tree = ast.parse(code)

        result = contains_return(tree.body)

        self.assertTrue(result, "Should detect return inside if statement")

    def test_return_in_nested_structure(self):
        """Test detecting return in nested structures."""
        code = """
for i in range(10):
    if i > 5:
        return i
"""
        tree = ast.parse(code)

        result = contains_return(tree.body)

        self.assertTrue(result, "Should detect return in nested structure")

    def test_no_return_in_nested_function(self):
        """Test that returns in nested functions are not detected."""
        code = """
def inner():
    return 42
x = 1
"""
        tree = ast.parse(code)

        result = contains_return(tree.body)

        self.assertFalse(result, "Should not detect return in nested function")

    def test_empty_block(self):
        """Test empty block."""
        result = contains_return([])

        self.assertFalse(result, "Empty block should not contain return")


class TestIsValueProducing(unittest.TestCase):
    """Test is_value_producing function."""

    def test_block_with_return(self):
        """Test that block with return is value-producing."""
        code = "x = 1\nreturn x"
        tree = ast.parse(code)

        result = is_value_producing(tree.body)

        self.assertTrue(result, "Block with return should be value-producing")

    def test_single_expression(self):
        """Test that single expression is value-producing."""
        code = "42"
        tree = ast.parse(code)

        result = is_value_producing(tree.body)

        self.assertTrue(result, "Single expression should be value-producing")

    def test_multiple_statements_no_return(self):
        """Test that multiple statements without return is not value-producing."""
        code = "x = 1\ny = 2\nz = x + y"
        tree = ast.parse(code)

        result = is_value_producing(tree.body)

        self.assertFalse(result, "Multiple statements without return should not be value-producing")

    def test_empty_block(self):
        """Test that empty block is not value-producing."""
        result = is_value_producing([])

        self.assertFalse(result, "Empty block should not be value-producing")

    def test_return_in_nested_structure(self):
        """Test that return in nested structure makes block value-producing."""
        code = """
for i in range(10):
    if i > 5:
        return i
"""
        tree = ast.parse(code)

        result = is_value_producing(tree.body)

        self.assertTrue(result, "Block with nested return should be value-producing")


class TestHasCompleteReturnCoverage(unittest.TestCase):
    """Test has_complete_return_coverage function."""

    def test_simple_return(self):
        """Test simple return has complete coverage."""
        code = "return 42"
        tree = ast.parse(code)

        result = has_complete_return_coverage(tree.body)

        self.assertTrue(result, "Simple return should have complete coverage")

    def test_if_with_both_returns(self):
        """Test if statement with returns in both branches."""
        code = """
if condition:
    return 1
else:
    return 2
"""
        tree = ast.parse(code)

        result = has_complete_return_coverage(tree.body)

        self.assertTrue(result, "If with returns in both branches should have complete coverage")

    def test_if_with_only_if_return(self):
        """Test if statement with return only in if branch."""
        code = """
if condition:
    return 1
"""
        tree = ast.parse(code)

        result = has_complete_return_coverage(tree.body)

        self.assertFalse(
            result, "If with return only in if branch should not have complete coverage"
        )

    def test_if_with_only_else_return(self):
        """Test if statement with return only in else branch."""
        code = """
if condition:
    x = 1
else:
    return 2
"""
        tree = ast.parse(code)

        result = has_complete_return_coverage(tree.body)

        self.assertFalse(
            result, "If with return only in else branch should not have complete coverage"
        )

    def test_no_return(self):
        """Test block without return."""
        code = "x = 1\ny = 2"
        tree = ast.parse(code)

        result = has_complete_return_coverage(tree.body)

        self.assertFalse(result, "Block without return should not have complete coverage")


class TestParameterSubstitution(unittest.TestCase):
    """Targeted tests for the parameter substitution logic inside the extractor."""

    def test_rebinding_stops_parameter_substitution(self) -> None:
        code = """
if result > threshold:
    return result
result = result + 10
return result
"""

        tree = ast.parse(code)
        block = tree.body

        subst = Substitution()
        name_expr = ast.parse("result").body[0].value  # type: ignore[assignment]
        subst.add_mapping(0, name_expr, "__param_0")

        extractor = HygienicExtractor()
        transformed = extractor._substitute_parameters(  # pylint: disable=protected-access
            block,
            substitution=subst,
            param_names=["__param_0"],
            rename_mapping={"__param_0": "__param_0"},
        )

        if_stmt = cast(ast.If, transformed[0])
        first_return = cast(ast.Return, if_stmt.body[0])
        self.assertIsInstance(first_return.value, ast.Name)
        self.assertEqual(first_return.value.id, "__param_0")

        reassignment = cast(ast.Assign, transformed[1])
        bin_op = cast(ast.BinOp, reassignment.value)
        self.assertIsInstance(bin_op.left, ast.Name)
        self.assertEqual(cast(ast.Name, bin_op.left).id, "__param_0")

        final_return = cast(ast.Return, transformed[2])
        self.assertIsInstance(final_return.value, ast.Name)
        self.assertEqual(final_return.value.id, "result")


class TestGetEnclosingNames(unittest.TestCase):
    """Test get_enclosing_names function."""

    def test_root_scope_no_enclosing(self):
        """Test that root scope has no enclosing names."""
        scope = Scope(scope_id=0, parent=None)

        names = get_enclosing_names(scope, scope)

        self.assertEqual(len(names), 0, "Root scope should have no enclosing names")

    def test_child_scope_sees_parent(self):
        """Test that child scope sees parent's bindings."""
        parent_scope = Scope(scope_id=0, parent=None)
        parent_scope.add_binding("x", ast.Name(id="x"))
        parent_scope.add_binding("y", ast.Name(id="y"))

        child_scope = Scope(scope_id=1, parent=parent_scope)

        names = get_enclosing_names(parent_scope, child_scope)

        self.assertIn("x", names, "Should see x from parent")
        self.assertIn("y", names, "Should see y from parent")

    def test_deeply_nested_scope(self):
        """Test deeply nested scopes."""
        root = Scope(scope_id=0, parent=None)
        root.add_binding("a", ast.Name(id="a"))

        level1 = Scope(scope_id=1, parent=root)
        level1.add_binding("b", ast.Name(id="b"))

        level2 = Scope(scope_id=2, parent=level1)
        level2.add_binding("c", ast.Name(id="c"))

        names = get_enclosing_names(root, level2)

        self.assertIn("a", names, "Should see a from root")
        self.assertIn("b", names, "Should see b from level1")
        self.assertNotIn("c", names, "Should not see c from current scope")


class TestHygienicExtractorEnsureUniqueName(unittest.TestCase):
    """Test HygienicExtractor._ensure_unique_name method."""

    def setUp(self):
        """Set up test fixtures."""
        self.extractor = HygienicExtractor()

    def test_unique_name_no_conflict(self):
        """Test that unique name is returned as-is."""
        enclosing_names = {"foo", "bar"}

        result = self.extractor._ensure_unique_name("baz", enclosing_names)

        self.assertEqual(result, "baz", "Unique name should be returned as-is")

    def test_shadowing_name_gets_suffix(self):
        """Test that shadowing name gets numeric suffix."""
        enclosing_names = {"foo"}

        result = self.extractor._ensure_unique_name("foo", enclosing_names)

        self.assertNotEqual(result, "foo", "Should not return shadowing name")
        self.assertTrue(result.startswith("__foo_"), "Should add __ prefix and suffix")

    def test_multiple_conflicts(self):
        """Test handling multiple conflicts."""
        enclosing_names = {"foo", "__foo_1", "__foo_2"}

        result = self.extractor._ensure_unique_name("foo", enclosing_names)

        self.assertEqual(result, "__foo_3", "Should use next available number")

    def test_tracks_used_names(self):
        """Test that used names are tracked."""
        enclosing_names = set()

        name1 = self.extractor._ensure_unique_name("foo", enclosing_names)
        name2 = self.extractor._ensure_unique_name("foo", enclosing_names)

        self.assertEqual(name1, "foo")
        self.assertNotEqual(name2, "foo", "Second call should return different name")


class TestHygienicExtractorBasics(unittest.TestCase):
    """Test basic HygienicExtractor functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.extractor = HygienicExtractor()

    def test_extract_function_creates_function_def(self):
        """Test that extract_function creates a FunctionDef node."""
        code = "x = 1\ny = 2\nreturn x + y"
        tree = ast.parse(code)

        substitution = Substitution()
        free_variables = set()
        enclosing_names = set()

        func_def, param_order = self.extractor.extract_function(
            tree.body, substitution, free_variables, enclosing_names, is_value_producing=True
        )

        self.assertIsInstance(func_def, ast.FunctionDef, "Should return FunctionDef")
        self.assertEqual(func_def.name, "extracted_function", "Should have default name")

    def test_extract_function_with_custom_name(self):
        """Test extraction with custom function name."""
        code = "x = 1"
        tree = ast.parse(code)

        substitution = Substitution()
        free_variables = set()
        enclosing_names = set()

        func_def, param_order = self.extractor.extract_function(
            tree.body,
            substitution,
            free_variables,
            enclosing_names,
            is_value_producing=False,
            function_name="my_function",
        )

        self.assertEqual(func_def.name, "my_function", "Should use custom name")

    def test_extract_function_avoids_name_collision(self):
        """Test that function name avoids collision with enclosing names."""
        code = "x = 1"
        tree = ast.parse(code)

        substitution = Substitution()
        free_variables = set()
        enclosing_names = {"extracted_function"}

        func_def, param_order = self.extractor.extract_function(
            tree.body, substitution, free_variables, enclosing_names, is_value_producing=False
        )

        self.assertNotEqual(func_def.name, "extracted_function", "Should avoid name collision")

    def test_extract_function_with_free_variables(self):
        """Test extraction with free variables."""
        code = "y = x + 1"
        tree = ast.parse(code)

        substitution = Substitution()
        free_variables = {"x"}
        enclosing_names = set()

        func_def, param_order = self.extractor.extract_function(
            tree.body, substitution, free_variables, enclosing_names, is_value_producing=False
        )

        # Check that 'x' is a parameter
        param_names = [arg.arg for arg in func_def.args.args]
        self.assertIn("x", param_names, "Free variable should be parameter")

    def test_extract_function_empty_body(self):
        """Test extraction with empty body."""
        substitution = Substitution()
        free_variables = set()
        enclosing_names = set()

        func_def, param_order = self.extractor.extract_function(
            [],  # Empty body
            substitution,
            free_variables,
            enclosing_names,
            is_value_producing=False,
        )

        # Empty body should get a pass statement
        self.assertEqual(len(func_def.body), 1)
        self.assertIsInstance(func_def.body[0], ast.Pass)


class TestGenerateCall(unittest.TestCase):
    """Test HygienicExtractor.generate_call method."""

    def setUp(self):
        """Set up test fixtures."""
        self.extractor = HygienicExtractor()

    def test_generate_call_creates_call_node(self):
        """Test that generate_call creates appropriate call structure."""
        substitution = Substitution()
        param_order = {}
        free_variables = set()

        result = self.extractor.generate_call(
            "my_function", 0, substitution, param_order, free_variables, is_value_producing=False
        )

        # Should be Expr wrapping a Call
        self.assertIsInstance(result, ast.Expr)
        self.assertIsInstance(result.value, ast.Call)
        self.assertEqual(result.value.func.id, "my_function")

    def test_generate_call_value_producing(self):
        """Test that value-producing calls are wrapped in Return."""
        substitution = Substitution()
        param_order = {}
        free_variables = set()

        result = self.extractor.generate_call(
            "my_function", 0, substitution, param_order, free_variables, is_value_producing=True
        )

        # Should be Return wrapping a Call
        self.assertIsInstance(result, ast.Return)
        self.assertIsInstance(result.value, ast.Call)

    def test_generate_call_with_free_variables(self):
        """Test generating call with free variables as arguments."""
        substitution = Substitution()
        param_order = {"x": 0, "y": 1}
        free_variables = {"x", "y"}

        result = self.extractor.generate_call(
            "my_function", 0, substitution, param_order, free_variables, is_value_producing=False
        )

        call = result.value
        self.assertEqual(len(call.args), 2, "Should have 2 arguments")


class TestIntegration(unittest.TestCase):
    """Integration tests for extraction workflow."""

    def test_simple_extraction_workflow(self):
        """Test complete extraction workflow for simple case."""
        extractor = HygienicExtractor()

        code = "result = x + 1\nreturn result"
        tree = ast.parse(code)

        substitution = Substitution()
        free_variables = {"x"}
        enclosing_names = set()

        # Extract function
        func_def, param_order = extractor.extract_function(
            tree.body, substitution, free_variables, enclosing_names, is_value_producing=True
        )

        # Verify function has parameter
        param_names = [arg.arg for arg in func_def.args.args]
        self.assertIn("x", param_names)

        # Generate call
        call = extractor.generate_call(
            func_def.name, 0, substitution, param_order, free_variables, is_value_producing=True
        )

        # Verify call structure
        self.assertIsInstance(call, ast.Return)
        self.assertIsInstance(call.value, ast.Call)


def main():
    """Run the tests."""
    unittest.main(verbosity=2)


if __name__ == "__main__":
    main()
