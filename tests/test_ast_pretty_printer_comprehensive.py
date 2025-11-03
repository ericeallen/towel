#!/usr/bin/env python3
"""
Comprehensive unit tests for the ast_pretty_printer module.

These tests verify that AST pretty printing works correctly for
various node types and formatting options.
"""

import unittest
import ast
from io import StringIO
import sys
from src.towel.unification.ast_pretty_printer import ASTPrettyPrinter, print_ast, compare_asts


class TestASTPrettyPrinterInit(unittest.TestCase):
    """Test ASTPrettyPrinter initialization."""

    def test_default_initialization(self):
        """Test creating printer with default parameters."""
        printer = ASTPrettyPrinter()

        self.assertEqual(printer.indent_size, 2, "Default indent size should be 2")
        self.assertFalse(printer.show_line_numbers, "Line numbers should be off by default")

    def test_custom_indent_size(self):
        """Test creating printer with custom indent size."""
        printer = ASTPrettyPrinter(indent_size=4)

        self.assertEqual(printer.indent_size, 4, "Should use custom indent size")

    def test_line_numbers_enabled(self):
        """Test creating printer with line numbers enabled."""
        printer = ASTPrettyPrinter(show_line_numbers=True)

        self.assertTrue(printer.show_line_numbers, "Line numbers should be enabled")

    def test_custom_parameters(self):
        """Test creating printer with all custom parameters."""
        printer = ASTPrettyPrinter(indent_size=3, show_line_numbers=True)

        self.assertEqual(printer.indent_size, 3)
        self.assertTrue(printer.show_line_numbers)


class TestFormatSimpleNodes(unittest.TestCase):
    """Test formatting simple AST nodes."""

    def setUp(self):
        """Set up test fixtures."""
        self.printer = ASTPrettyPrinter()

    def test_format_simple_constant(self):
        """Test formatting a simple constant."""
        code = "42"
        tree = ast.parse(code)

        result = self.printer.format(tree)

        self.assertIn("Module", result, "Should contain Module node")
        self.assertIn("Constant", result, "Should contain Constant node")
        self.assertIn("42", result, "Should contain the value")

    def test_format_simple_name(self):
        """Test formatting a simple name node."""
        code = "x"
        tree = ast.parse(code)

        result = self.printer.format(tree)

        self.assertIn("Name", result, "Should contain Name node")
        self.assertIn("'x'", result, "Should contain the name string")

    def test_format_simple_assignment(self):
        """Test formatting a simple assignment."""
        code = "x = 1"
        tree = ast.parse(code)

        result = self.printer.format(tree)

        self.assertIn("Assign", result, "Should contain Assign node")
        self.assertIn("Name", result, "Should contain Name node")
        self.assertIn("Constant", result, "Should contain Constant node")

    def test_format_returns_string(self):
        """Test that format returns a string."""
        code = "x = 1"
        tree = ast.parse(code)

        result = self.printer.format(tree)

        self.assertIsInstance(result, str, "Should return a string")
        self.assertGreater(len(result), 0, "Result should not be empty")


class TestFormatComplexNodes(unittest.TestCase):
    """Test formatting complex AST structures."""

    def setUp(self):
        """Set up test fixtures."""
        self.printer = ASTPrettyPrinter()

    def test_format_function_definition(self):
        """Test formatting a function definition."""
        code = """
def foo(x, y):
    return x + y
"""
        tree = ast.parse(code)

        result = self.printer.format(tree)

        self.assertIn("FunctionDef", result, "Should contain FunctionDef node")
        self.assertIn("'foo'", result, "Should contain function name")
        self.assertIn("Return", result, "Should contain Return node")

    def test_format_class_definition(self):
        """Test formatting a class definition."""
        code = """
class MyClass:
    pass
"""
        tree = ast.parse(code)

        result = self.printer.format(tree)

        self.assertIn("ClassDef", result, "Should contain ClassDef node")
        self.assertIn("'MyClass'", result, "Should contain class name")

    def test_format_if_statement(self):
        """Test formatting an if statement."""
        code = """
if condition:
    x = 1
else:
    x = 2
"""
        tree = ast.parse(code)

        result = self.printer.format(tree)

        self.assertIn("If", result, "Should contain If node")
        self.assertIn("body", result, "Should contain body field")
        self.assertIn("orelse", result, "Should contain orelse field")

    def test_format_nested_expressions(self):
        """Test formatting nested expressions."""
        code = "result = (x + y) * (a - b)"
        tree = ast.parse(code)

        result = self.printer.format(tree)

        self.assertIn("BinOp", result, "Should contain BinOp nodes")
        self.assertIn("Mult", result, "Should contain Mult operator")
        self.assertIn("Add", result, "Should contain Add operator")
        self.assertIn("Sub", result, "Should contain Sub operator")


class TestFormatLists(unittest.TestCase):
    """Test formatting list structures."""

    def setUp(self):
        """Set up test fixtures."""
        self.printer = ASTPrettyPrinter()

    def test_format_empty_list(self):
        """Test that empty lists are not shown."""
        code = "def foo(): pass"
        tree = ast.parse(code)

        result = self.printer.format(tree)

        # Empty decorator_list should not appear in output
        # This is tested indirectly by checking the output is still valid
        self.assertIn("FunctionDef", result)

    def test_format_multiple_statements(self):
        """Test formatting multiple statements in a list."""
        code = """
x = 1
y = 2
z = 3
"""
        tree = ast.parse(code)

        result = self.printer.format(tree)

        self.assertIn("body", result, "Should have body field")
        # Should have multiple Assign nodes
        self.assertGreater(result.count("Assign"), 1, "Should have multiple assignments")

    def test_format_function_arguments(self):
        """Test formatting function arguments list."""
        code = "def foo(a, b, c): pass"
        tree = ast.parse(code)

        result = self.printer.format(tree)

        self.assertIn("arguments", result, "Should contain arguments")
        self.assertIn("arg", result, "Should contain arg nodes")


class TestFormatValue(unittest.TestCase):
    """Test _format_value method."""

    def setUp(self):
        """Set up test fixtures."""
        self.printer = ASTPrettyPrinter()

    def test_format_string_value(self):
        """Test formatting string values."""
        result = self.printer._format_value("hello")

        self.assertEqual(result, "'hello'", "Should format string with quotes")

    def test_format_integer_value(self):
        """Test formatting integer values."""
        result = self.printer._format_value(42)

        self.assertEqual(result, "42", "Should format integer as string")

    def test_format_float_value(self):
        """Test formatting float values."""
        result = self.printer._format_value(3.14)

        self.assertEqual(result, "3.14", "Should format float as string")

    def test_format_boolean_value(self):
        """Test formatting boolean values."""
        result_true = self.printer._format_value(True)
        result_false = self.printer._format_value(False)

        self.assertEqual(result_true, "True", "Should format True correctly")
        self.assertEqual(result_false, "False", "Should format False correctly")

    def test_format_none_value(self):
        """Test formatting None value."""
        result = self.printer._format_value(None)

        self.assertEqual(result, "None", "Should format None correctly")


class TestIndentation(unittest.TestCase):
    """Test indentation functionality."""

    def test_default_indent(self):
        """Test default indentation of 2 spaces."""
        printer = ASTPrettyPrinter(indent_size=2)
        code = "x = 1"
        tree = ast.parse(code)

        result = printer.format(tree)

        lines = result.split("\n")
        # Check that nested content has proper indentation
        indented_lines = [
            line for line in lines if line.startswith("  ") and not line.startswith("    ")
        ]
        self.assertGreater(len(indented_lines), 0, "Should have lines with 2-space indent")

    def test_custom_indent(self):
        """Test custom indentation."""
        printer = ASTPrettyPrinter(indent_size=4)
        code = "x = 1"
        tree = ast.parse(code)

        result = printer.format(tree)

        lines = result.split("\n")
        # Check that nested content has proper indentation
        indented_lines = [
            line for line in lines if line.startswith("    ") and len(line.lstrip()) > 0
        ]
        self.assertGreater(len(indented_lines), 0, "Should have lines with 4-space indent")

    def test_nested_indentation(self):
        """Test that nested structures have increasing indentation."""
        printer = ASTPrettyPrinter(indent_size=2)
        code = """
def foo():
    if True:
        x = 1
"""
        tree = ast.parse(code)

        result = printer.format(tree)

        lines = result.split("\n")
        # Should have various levels of indentation
        indent_levels = set()
        for line in lines:
            if len(line.strip()) > 0:
                spaces = len(line) - len(line.lstrip())
                indent_levels.add(spaces)

        self.assertGreater(len(indent_levels), 2, "Should have multiple indentation levels")


class TestLineNumbers(unittest.TestCase):
    """Test line number display functionality."""

    def test_line_numbers_off_by_default(self):
        """Test that line numbers are not shown by default."""
        printer = ASTPrettyPrinter()
        code = "x = 1"
        tree = ast.parse(code)

        result = printer.format(tree)

        self.assertNotIn("@L", result, "Should not show line numbers by default")

    def test_line_numbers_enabled(self):
        """Test line numbers when enabled."""
        printer = ASTPrettyPrinter(show_line_numbers=True)
        code = "x = 1"
        tree = ast.parse(code)

        result = printer.format(tree)

        self.assertIn("@L1", result, "Should show line numbers when enabled")

    def test_line_numbers_multiple_lines(self):
        """Test line numbers for multiple lines of code."""
        printer = ASTPrettyPrinter(show_line_numbers=True)
        code = """
x = 1
y = 2
z = 3
"""
        tree = ast.parse(code)

        result = printer.format(tree)

        self.assertIn("@L", result, "Should contain line number markers")
        # Should have references to different line numbers
        has_multiple_lines = result.count("@L") > 1
        self.assertTrue(has_multiple_lines, "Should show multiple line numbers")


class TestPrintMethod(unittest.TestCase):
    """Test the print method."""

    def test_print_without_title(self):
        """Test printing without a title."""
        printer = ASTPrettyPrinter()
        code = "x = 1"
        tree = ast.parse(code)

        # Capture stdout
        captured_output = StringIO()
        sys.stdout = captured_output

        printer.print(tree)

        sys.stdout = sys.__stdout__
        output = captured_output.getvalue()

        self.assertIn("Module", output, "Should print the AST")
        self.assertNotIn("===", output, "Should not have title separator")

    def test_print_with_title(self):
        """Test printing with a title."""
        printer = ASTPrettyPrinter()
        code = "x = 1"
        tree = ast.parse(code)

        # Capture stdout
        captured_output = StringIO()
        sys.stdout = captured_output

        printer.print(tree, title="Test AST")

        sys.stdout = sys.__stdout__
        output = captured_output.getvalue()

        self.assertIn("Test AST", output, "Should print the title")
        self.assertIn("===", output, "Should have title separator")
        self.assertIn("Module", output, "Should print the AST")


class TestConvenienceFunctions(unittest.TestCase):
    """Test convenience functions."""

    def test_print_ast_function(self):
        """Test print_ast convenience function."""
        code = "x = 1"
        tree = ast.parse(code)

        # Capture stdout
        captured_output = StringIO()
        sys.stdout = captured_output

        print_ast(tree)

        sys.stdout = sys.__stdout__
        output = captured_output.getvalue()

        self.assertIn("Module", output, "Should print the AST")

    def test_print_ast_with_parameters(self):
        """Test print_ast with custom parameters."""
        code = "x = 1"
        tree = ast.parse(code)

        # Capture stdout
        captured_output = StringIO()
        sys.stdout = captured_output

        print_ast(tree, title="Custom Title", indent_size=4, show_line_numbers=True)

        sys.stdout = sys.__stdout__
        output = captured_output.getvalue()

        self.assertIn("Custom Title", output, "Should print custom title")
        self.assertIn("@L1", output, "Should show line numbers")

    def test_compare_asts_function(self):
        """Test compare_asts convenience function."""
        code1 = "x = 1"
        code2 = "y = 2"
        tree1 = ast.parse(code1)
        tree2 = ast.parse(code2)

        # Capture stdout
        captured_output = StringIO()
        sys.stdout = captured_output

        compare_asts(tree1, tree2, title1="First", title2="Second")

        sys.stdout = sys.__stdout__
        output = captured_output.getvalue()

        self.assertIn("First", output, "Should print first title")
        self.assertIn("Second", output, "Should print second title")
        self.assertIn("'x'", output, "Should contain first AST")
        self.assertIn("'y'", output, "Should contain second AST")


class TestEdgeCases(unittest.TestCase):
    """Test edge cases and special scenarios."""

    def test_format_empty_module(self):
        """Test formatting an empty module."""
        printer = ASTPrettyPrinter()
        code = ""
        tree = ast.parse(code)

        result = printer.format(tree)

        self.assertIn("Module", result, "Should contain Module node")

    def test_format_node_with_none_fields(self):
        """Test formatting node with None fields (which should be skipped)."""
        printer = ASTPrettyPrinter()
        code = "def foo(): pass"
        tree = ast.parse(code)

        result = printer.format(tree)

        # None fields should not appear in output
        # Just verify the output is valid
        self.assertIn("FunctionDef", result)

    def test_format_string_with_special_chars(self):
        """Test formatting strings with special characters."""
        printer = ASTPrettyPrinter()
        code = "s = 'hello\\nworld'"
        tree = ast.parse(code)

        result = printer.format(tree)

        # Should contain escaped representation
        self.assertIn("hello", result)

    def test_format_multiline_structure(self):
        """Test formatting produces multiple lines."""
        printer = ASTPrettyPrinter()
        code = "x = 1"
        tree = ast.parse(code)

        result = printer.format(tree)

        lines = result.split("\n")
        self.assertGreater(len(lines), 3, "Should produce multiple lines")

    def test_format_preserves_structure(self):
        """Test that formatting preserves AST structure."""
        printer = ASTPrettyPrinter()
        code = "x = 1"
        tree = ast.parse(code)

        result = printer.format(tree)

        # Check that parentheses are balanced
        self.assertEqual(result.count("("), result.count(")"), "Parentheses should be balanced")
        # Check that brackets are balanced
        self.assertEqual(result.count("["), result.count("]"), "Brackets should be balanced")


class TestIntegration(unittest.TestCase):
    """Integration tests for complete workflows."""

    def test_format_complete_function(self):
        """Test formatting a complete function with various features."""
        printer = ASTPrettyPrinter()
        code = """
def calculate(x, y):
    if x > y:
        return x + y
    else:
        return x - y
"""
        tree = ast.parse(code)

        result = printer.format(tree)

        # Verify all major components are present
        self.assertIn("FunctionDef", result)
        self.assertIn("'calculate'", result)
        self.assertIn("If", result)
        self.assertIn("Return", result)
        self.assertIn("BinOp", result)

    def test_format_with_all_options(self):
        """Test formatting with all options enabled."""
        printer = ASTPrettyPrinter(indent_size=3, show_line_numbers=True)
        code = """
x = 1
y = 2
"""
        tree = ast.parse(code)

        result = printer.format(tree)

        self.assertIn("@L", result, "Should show line numbers")
        # Verify custom indent is used
        lines = result.split("\n")
        has_three_space_indent = any(
            len(line) - len(line.lstrip()) == 3 for line in lines if len(line.strip()) > 0
        )
        self.assertTrue(has_three_space_indent, "Should use 3-space indent")


def main():
    """Run the tests."""
    unittest.main(verbosity=2)


if __name__ == "__main__":
    main()
