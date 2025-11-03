#!/usr/bin/env python3
"""
Tests for the binding detector module.

Verifies that all Python variable binding constructs are correctly identified.
"""

import unittest
import ast
from src.towel.unification.binding_detector import (
    BindingDetector,
    BindingKind,
    detect_bindings,
    get_bound_variables,
    get_bindings_by_kind,
)


class TestBindingDetector(unittest.TestCase):
    """Test the binding detector."""

    def test_simple_assignment(self):
        """Test simple variable assignment."""
        code = "x = 1"
        tree = ast.parse(code)
        bindings = detect_bindings(tree)

        self.assertEqual(len(bindings), 1)
        self.assertEqual(bindings[0].name, "x")
        self.assertEqual(bindings[0].kind, BindingKind.ASSIGNMENT)

    def test_tuple_unpacking(self):
        """Test tuple unpacking assignment."""
        code = "x, y = 1, 2"
        tree = ast.parse(code)
        bindings = detect_bindings(tree)

        names = {b.name for b in bindings}
        self.assertEqual(names, {"x", "y"})
        self.assertTrue(all(b.kind == BindingKind.ASSIGNMENT for b in bindings))

    def test_nested_unpacking(self):
        """Test nested tuple unpacking."""
        code = "(x, (y, z)) = (1, (2, 3))"
        tree = ast.parse(code)
        bindings = detect_bindings(tree)

        names = {b.name for b in bindings}
        self.assertEqual(names, {"x", "y", "z"})

    def test_starred_assignment(self):
        """Test starred assignment."""
        code = "x, *rest = [1, 2, 3, 4]"
        tree = ast.parse(code)
        bindings = detect_bindings(tree)

        names = {b.name for b in bindings}
        self.assertEqual(names, {"x", "rest"})

    def test_for_loop(self):
        """Test for loop variable."""
        code = """
for i in range(10):
    pass
"""
        tree = ast.parse(code)
        bindings = detect_bindings(tree)

        self.assertEqual(len(bindings), 1)
        self.assertEqual(bindings[0].name, "i")
        self.assertEqual(bindings[0].kind, BindingKind.FOR_LOOP)

    def test_list_comprehension(self):
        """Test list comprehension variable."""
        code = "[x for x in range(10)]"
        tree = ast.parse(code)
        bindings = detect_bindings(tree)

        self.assertEqual(len(bindings), 1)
        self.assertEqual(bindings[0].name, "x")
        self.assertEqual(bindings[0].kind, BindingKind.COMPREHENSION)

    def test_dict_comprehension(self):
        """Test dict comprehension with multiple variables."""
        code = "{k: v for k, v in items}"
        tree = ast.parse(code)
        bindings = detect_bindings(tree)

        names = {b.name for b in bindings}
        self.assertEqual(names, {"k", "v"})
        self.assertTrue(all(b.kind == BindingKind.COMPREHENSION for b in bindings))

    def test_exception_handler(self):
        """Test exception handler binding."""
        code = """
try:
    pass
except Exception as e:
    pass
"""
        tree = ast.parse(code)
        bindings = detect_bindings(tree)

        self.assertEqual(len(bindings), 1)
        self.assertEqual(bindings[0].name, "e")
        self.assertEqual(bindings[0].kind, BindingKind.EXCEPTION)

    def test_with_statement(self):
        """Test with statement binding."""
        code = """
with open('file.txt') as f:
    pass
"""
        tree = ast.parse(code)
        bindings = detect_bindings(tree)

        self.assertEqual(len(bindings), 1)
        self.assertEqual(bindings[0].name, "f")
        self.assertEqual(bindings[0].kind, BindingKind.WITH_STMT)

    def test_function_definition(self):
        """Test function definition and parameters."""
        code = """
def foo(x, y):
    pass
"""
        tree = ast.parse(code)
        bindings = detect_bindings(tree)

        names = {b.name for b in bindings}
        self.assertEqual(names, {"foo", "x", "y"})

        # Function name is FUNCTION_DEF, parameters are FUNCTION_PARAM
        func_defs = [b for b in bindings if b.kind == BindingKind.FUNCTION_DEF]
        params = [b for b in bindings if b.kind == BindingKind.FUNCTION_PARAM]

        self.assertEqual(len(func_defs), 1)
        self.assertEqual(func_defs[0].name, "foo")
        self.assertEqual(len(params), 2)
        self.assertEqual({p.name for p in params}, {"x", "y"})

    def test_lambda(self):
        """Test lambda parameters."""
        code = "lambda x, y: x + y"
        tree = ast.parse(code)
        bindings = detect_bindings(tree)

        names = {b.name for b in bindings}
        self.assertEqual(names, {"x", "y"})
        self.assertTrue(all(b.kind == BindingKind.FUNCTION_PARAM for b in bindings))

    def test_named_expression(self):
        """Test walrus operator."""
        code = "if (x := foo()):\n    pass"
        tree = ast.parse(code)
        bindings = detect_bindings(tree)

        self.assertEqual(len(bindings), 1)
        self.assertEqual(bindings[0].name, "x")
        self.assertEqual(bindings[0].kind, BindingKind.NAMED_EXPR)

    def test_import(self):
        """Test import statement."""
        code = "import os"
        tree = ast.parse(code)
        bindings = detect_bindings(tree)

        self.assertEqual(len(bindings), 1)
        self.assertEqual(bindings[0].name, "os")
        self.assertEqual(bindings[0].kind, BindingKind.IMPORT)

    def test_import_as(self):
        """Test import with alias."""
        code = "import numpy as np"
        tree = ast.parse(code)
        bindings = detect_bindings(tree)

        self.assertEqual(len(bindings), 1)
        self.assertEqual(bindings[0].name, "np")  # Should use alias
        self.assertEqual(bindings[0].kind, BindingKind.IMPORT)

    def test_from_import(self):
        """Test from-import statement."""
        code = "from os import path"
        tree = ast.parse(code)
        bindings = detect_bindings(tree)

        self.assertEqual(len(bindings), 1)
        self.assertEqual(bindings[0].name, "path")
        self.assertEqual(bindings[0].kind, BindingKind.IMPORT)

    def test_class_definition(self):
        """Test class definition."""
        code = """
class Foo:
    pass
"""
        tree = ast.parse(code)
        bindings = detect_bindings(tree)

        self.assertEqual(len(bindings), 1)
        self.assertEqual(bindings[0].name, "Foo")
        self.assertEqual(bindings[0].kind, BindingKind.CLASS_DEF)

    def test_attribute_not_binding(self):
        """Test that attribute assignment is NOT a binding."""
        code = "obj.attr = 1"
        tree = ast.parse(code)
        bindings = detect_bindings(tree)

        # Should be no bindings - attribute assignment doesn't create new variables
        self.assertEqual(len(bindings), 0)

    def test_subscript_not_binding(self):
        """Test that subscript assignment is NOT a binding."""
        code = "lst[0] = 1"
        tree = ast.parse(code)
        bindings = detect_bindings(tree)

        # Should be no bindings - subscript assignment doesn't create new variables
        self.assertEqual(len(bindings), 0)

    def test_get_bound_variables(self):
        """Test convenience function get_bound_variables."""
        code = """
x = 1
for i in range(10):
    y = i + 1
"""
        tree = ast.parse(code)
        bound_vars = get_bound_variables(tree)

        self.assertEqual(bound_vars, {"x", "i", "y"})

    def test_get_bindings_by_kind(self):
        """Test filtering bindings by kind."""
        code = """
x = 1
for i in range(10):
    y = i + 1
"""
        tree = ast.parse(code)

        assignments = get_bindings_by_kind(tree, BindingKind.ASSIGNMENT)
        loops = get_bindings_by_kind(tree, BindingKind.FOR_LOOP)

        self.assertEqual({b.name for b in assignments}, {"x", "y"})
        self.assertEqual({b.name for b in loops}, {"i"})

    def test_complex_example(self):
        """Test a complex example with multiple binding types."""
        code = """
def process_data(data):
    result = []
    for item in data:
        if (value := item.get('value')):
            result.append(value)
    return result

class Handler:
    def handle(self, x):
        return x + 1
"""
        tree = ast.parse(code)
        bindings = detect_bindings(tree)

        names = {b.name for b in bindings}
        # process_data, data, result, item, value, Handler, handle, self, x
        expected = {
            "process_data",
            "data",
            "result",
            "item",
            "value",
            "Handler",
            "handle",
            "self",
            "x",
        }
        self.assertEqual(names, expected)


def main():
    """Run the tests."""
    unittest.main(verbosity=2)


if __name__ == "__main__":
    main()
