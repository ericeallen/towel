import ast
import io
import textwrap
import unittest
from contextlib import redirect_stdout

from towel.unification.ast_pretty_printer import (
    ASTPrettyPrinter,
    print_ast,
    compare_asts,
)


class TestASTPrettyPrinter(unittest.TestCase):
    def test_format_with_line_numbers_and_lists(self) -> None:
        code = """
        def foo(x):
            y = x + 1
            return y
        """
        tree = ast.parse(textwrap.dedent(code))
        printer = ASTPrettyPrinter(indent_size=2, show_line_numbers=True)
        output = printer.format(tree)

        # Should include node names with line numbers
        self.assertIn("Module(", output)
        self.assertTrue("FunctionDef@L2(" in output or "AsyncFunctionDef@L2(" in output)

        # Ensure list fields are rendered with brackets and nested items
        self.assertIn("body=[", output)
        self.assertTrue("]\n)" in output or output.rstrip().endswith(")"))

    def test_format_primitives_and_non_ast_list(self) -> None:
        printer = ASTPrettyPrinter(indent_size=2, show_line_numbers=False)

        # Primitive string should be repr quoted
        out_str = printer.format(ast.Constant("hello"))
        self.assertIn("Constant(", out_str)
        self.assertIn("value='hello'", out_str)

        # Primitive non-string should use str(value)
        out_int = printer.format(ast.Constant(42))
        self.assertIn("value=42", out_int)

        # Non-AST list should be handled
        out_list = printer.format([ast.Constant(1), ast.Constant(2)])
        self.assertTrue(out_list.strip().startswith("["))
        self.assertIn("Constant(", out_list)

    def test_print_with_title_writes_separators(self) -> None:
        tree = ast.parse("a = 1\n")
        f = io.StringIO()
        with redirect_stdout(f):
            printer = ASTPrettyPrinter()
            printer.print(tree, title="Demo Title")
        s = f.getvalue()
        self.assertIn("Demo Title", s)
        self.assertIn("=" * 80, s)

    def test_print_ast_and_compare_asts_helpers(self) -> None:
        tree1 = ast.parse("a = 1\n")
        tree2 = ast.parse("b = 2\n")

        buf = io.StringIO()
        with redirect_stdout(buf):
            print_ast(tree1, title="One", indent_size=4, show_line_numbers=True)
            compare_asts(tree1, tree2, title1="Left", title2="Right")

        out = buf.getvalue()
        # Titles from both helpers should appear
        self.assertIn("One", out)
        self.assertIn("Left", out)
        self.assertIn("Right", out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
