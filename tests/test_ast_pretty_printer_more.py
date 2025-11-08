import ast
import unittest

from towel.unification.ast_pretty_printer import ASTPrettyPrinter


class TestASTPrettyPrinterMore(unittest.TestCase):
    def test_format_with_line_numbers_and_list(self):
        src = """
def f(x):
    return (x := x + 1)
"""
        tree = ast.parse(src)
        printer = ASTPrettyPrinter(indent_size=2, show_line_numbers=True)
        out = printer.format(tree)
        # Basic sanity checks: contains node names and line numbers, and walrus operator structure
        # Module nodes don't have lineno; check for FunctionDef and Return line numbers
        self.assertIn("FunctionDef@L2", out)
        self.assertIn("Return@L3", out)
        self.assertIn("NamedExpr", out)
        # list handling
        self.assertIn("body=[", out)


if __name__ == "__main__":
    unittest.main()
