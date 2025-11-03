#!/usr/bin/env python3
"""
Comprehensive unit tests for the builtins module.

These tests verify that builtin tracking works correctly.
"""

import unittest
from src.towel.unification.builtins import PYTHON_BUILTINS, is_builtin, filter_builtins


class TestPythonBuiltinsConstant(unittest.TestCase):
    """Test the PYTHON_BUILTINS constant."""

    def test_is_set(self):
        """Test that PYTHON_BUILTINS is a set."""
        self.assertIsInstance(PYTHON_BUILTINS, set, "PYTHON_BUILTINS should be a set")

    def test_contains_common_functions(self):
        """Test that common builtin functions are included."""
        common_builtins = {"print", "len", "range", "str", "int", "list", "dict", "set"}

        for builtin in common_builtins:
            self.assertIn(
                builtin, PYTHON_BUILTINS, f"Common builtin '{builtin}' should be in PYTHON_BUILTINS"
            )

    def test_contains_common_constants(self):
        """Test that common builtin constants are included."""
        constants = {"True", "False", "None"}

        for const in constants:
            self.assertIn(
                const, PYTHON_BUILTINS, f"Builtin constant '{const}' should be in PYTHON_BUILTINS"
            )

    def test_contains_common_exceptions(self):
        """Test that common exceptions are included."""
        exceptions = {
            "Exception",
            "ValueError",
            "TypeError",
            "KeyError",
            "IndexError",
            "AttributeError",
            "RuntimeError",
        }

        for exc in exceptions:
            self.assertIn(
                exc, PYTHON_BUILTINS, f"Builtin exception '{exc}' should be in PYTHON_BUILTINS"
            )

    def test_not_empty(self):
        """Test that PYTHON_BUILTINS is not empty."""
        self.assertGreater(len(PYTHON_BUILTINS), 0, "PYTHON_BUILTINS should not be empty")

    def test_all_strings(self):
        """Test that all entries are strings."""
        for item in PYTHON_BUILTINS:
            self.assertIsInstance(
                item, str, f"All builtin names should be strings, found {type(item)}"
            )


class TestIsBuiltin(unittest.TestCase):
    """Test is_builtin function."""

    def test_common_builtins_return_true(self):
        """Test that common builtins return True."""
        common_builtins = [
            "print",
            "len",
            "range",
            "str",
            "int",
            "list",
            "dict",
            "set",
            "tuple",
            "bool",
            "float",
            "type",
            "isinstance",
            "hasattr",
        ]

        for builtin in common_builtins:
            self.assertTrue(is_builtin(builtin), f"is_builtin('{builtin}') should return True")

    def test_constants_return_true(self):
        """Test that builtin constants return True."""
        constants = ["True", "False", "None", "Ellipsis", "NotImplemented"]

        for const in constants:
            self.assertTrue(is_builtin(const), f"is_builtin('{const}') should return True")

    def test_exceptions_return_true(self):
        """Test that builtin exceptions return True."""
        exceptions = [
            "Exception",
            "ValueError",
            "TypeError",
            "KeyError",
            "RuntimeError",
            "AttributeError",
            "IndexError",
        ]

        for exc in exceptions:
            self.assertTrue(is_builtin(exc), f"is_builtin('{exc}') should return True")

    def test_user_names_return_false(self):
        """Test that user-defined names return False."""
        user_names = [
            "my_function",
            "user_var",
            "CustomClass",
            "foo",
            "bar",
            "calculate_result",
            "process_data",
            "x",
            "y",
            "z",
        ]

        for name in user_names:
            self.assertFalse(is_builtin(name), f"is_builtin('{name}') should return False")

    def test_empty_string(self):
        """Test that empty string returns False."""
        self.assertFalse(is_builtin(""), "Empty string should not be a builtin")

    def test_case_sensitive(self):
        """Test that matching is case-sensitive."""
        # 'print' is a builtin, but 'Print' and 'PRINT' are not
        self.assertTrue(is_builtin("print"), "print should be a builtin")
        self.assertFalse(is_builtin("Print"), "Print should not be a builtin")
        self.assertFalse(is_builtin("PRINT"), "PRINT should not be a builtin")

    def test_stdlib_module_names_return_false(self):
        """Test that standard library module names return False (they're not builtins)."""
        stdlib_modules = [
            "os",
            "sys",
            "math",
            "json",
            "random",
            "datetime",
            "collections",
            "itertools",
            "functools",
        ]

        for module in stdlib_modules:
            self.assertFalse(
                is_builtin(module), f"is_builtin('{module}') should return False (it's a module)"
            )

    def test_async_builtins(self):
        """Test that async iteration builtins are recognized."""
        async_builtins = ["aiter", "anext"]

        for builtin in async_builtins:
            self.assertTrue(is_builtin(builtin), f"is_builtin('{builtin}') should return True")


class TestFilterBuiltins(unittest.TestCase):
    """Test filter_builtins function."""

    def test_removes_all_builtins(self):
        """Test that all builtins are removed from input set."""
        names = {"print", "len", "my_var", "user_function", "str", "int"}

        result = filter_builtins(names)

        self.assertEqual(
            result,
            {"my_var", "user_function"},
            "Should remove all builtins, keeping only user names",
        )

    def test_empty_input(self):
        """Test handling of empty input set."""
        result = filter_builtins(set())

        self.assertEqual(result, set(), "Empty input should return empty set")

    def test_all_builtins_returns_empty(self):
        """Test that set with only builtins returns empty set."""
        all_builtins = {"print", "len", "str", "int", "list", "dict"}

        result = filter_builtins(all_builtins)

        self.assertEqual(result, set(), "Set with only builtins should return empty")

    def test_no_builtins_returns_same(self):
        """Test that set with no builtins returns unchanged."""
        user_names = {"my_var", "user_function", "custom_class", "process_data"}

        result = filter_builtins(user_names)

        self.assertEqual(result, user_names, "Set with no builtins should be unchanged")

    def test_preserves_user_names_exactly(self):
        """Test that user names are preserved exactly."""
        names = {"Print", "LEN", "my_print", "length"}

        result = filter_builtins(names)

        # None of these should match the lowercase builtins due to case sensitivity
        self.assertEqual(result, names, "Case-different names should be preserved")

    def test_mixed_names(self):
        """Test filtering mixed set of builtins and user names."""
        names = {
            "x",
            "y",
            "print",
            "len",
            "result",
            "ValueError",
            "calculate",
            "Exception",
            "data",
            "str",
        }

        result = filter_builtins(names)

        expected = {"x", "y", "result", "calculate", "data"}
        self.assertEqual(result, expected, "Should filter out builtins correctly")

    def test_returns_new_set(self):
        """Test that a new set is returned, not the input set."""
        names = {"x", "print", "y"}

        result = filter_builtins(names)

        self.assertIsNot(result, names, "Should return a new set")
        self.assertEqual(names, {"x", "print", "y"}, "Original set should be unchanged")

    def test_filters_constants(self):
        """Test filtering of builtin constants."""
        names = {"x", "True", "False", "None", "result"}

        result = filter_builtins(names)

        self.assertEqual(result, {"x", "result"}, "Should filter out builtin constants")

    def test_filters_exceptions(self):
        """Test filtering of builtin exceptions."""
        names = {"error_handler", "ValueError", "TypeError", "result"}

        result = filter_builtins(names)

        self.assertEqual(
            result, {"error_handler", "result"}, "Should filter out builtin exceptions"
        )


class TestSpecificBuiltins(unittest.TestCase):
    """Test specific categories of builtins."""

    def test_iteration_builtins(self):
        """Test that iteration-related builtins are recognized."""
        iteration_builtins = [
            "iter",
            "next",
            "enumerate",
            "zip",
            "map",
            "filter",
            "reversed",
            "sorted",
            "range",
        ]

        for builtin in iteration_builtins:
            self.assertTrue(
                is_builtin(builtin), f"Iteration builtin '{builtin}' should be recognized"
            )

    def test_type_conversion_builtins(self):
        """Test that type conversion builtins are recognized."""
        type_builtins = [
            "int",
            "float",
            "str",
            "bool",
            "list",
            "tuple",
            "set",
            "dict",
            "frozenset",
            "bytes",
            "bytearray",
        ]

        for builtin in type_builtins:
            self.assertTrue(is_builtin(builtin), f"Type builtin '{builtin}' should be recognized")

    def test_introspection_builtins(self):
        """Test that introspection builtins are recognized."""
        intro_builtins = [
            "type",
            "isinstance",
            "issubclass",
            "hasattr",
            "getattr",
            "setattr",
            "delattr",
            "dir",
            "vars",
            "id",
            "callable",
        ]

        for builtin in intro_builtins:
            self.assertTrue(
                is_builtin(builtin), f"Introspection builtin '{builtin}' should be recognized"
            )

    def test_io_builtins(self):
        """Test that I/O builtins are recognized."""
        io_builtins = ["print", "input", "open"]

        for builtin in io_builtins:
            self.assertTrue(is_builtin(builtin), f"I/O builtin '{builtin}' should be recognized")

    def test_math_builtins(self):
        """Test that math-related builtins are recognized."""
        math_builtins = ["abs", "min", "max", "sum", "pow", "round", "divmod"]

        for builtin in math_builtins:
            self.assertTrue(is_builtin(builtin), f"Math builtin '{builtin}' should be recognized")

    def test_debugging_builtins(self):
        """Test that debugging builtins are recognized."""
        self.assertTrue(is_builtin("breakpoint"), "breakpoint should be recognized as a builtin")


class TestIntegration(unittest.TestCase):
    """Integration tests for builtin handling."""

    def test_realistic_variable_filtering(self):
        """Test filtering variables from a realistic code scenario."""
        # Variables that might appear in actual code
        variables = {
            "data",
            "result",
            "x",
            "y",
            "i",
            "j",  # user variables
            "print",
            "len",
            "range",
            "str",  # builtins used
            "ValueError",
            "TypeError",  # exceptions caught
            "process",
            "calculate",
            "transform",  # user functions
        }

        filtered = filter_builtins(variables)

        expected = {"data", "result", "x", "y", "i", "j", "process", "calculate", "transform"}
        self.assertEqual(filtered, expected, "Should correctly filter realistic variable set")

    def test_preserves_similar_but_different_names(self):
        """Test that names similar to builtins are preserved."""
        names = {
            "my_print",
            "print_result",
            "string",
            "integer",
            "list_items",
            "dictionary",
            "maximum",
            "minimum",
        }

        filtered = filter_builtins(names)

        # None of these should be filtered (they're not exact matches)
        self.assertEqual(filtered, names, "Names similar to builtins should be preserved")


def main():
    """Run the tests."""
    unittest.main(verbosity=2)


if __name__ == "__main__":
    main()
