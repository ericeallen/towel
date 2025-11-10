"""Refactor engine edge-case coverage tests.

These tests focus on analyzing individual files and directories, covering
error-handling paths and ensuring we never modify canonical fixtures.
"""

import os
import tempfile
import unittest
from pathlib import Path
import textwrap
import ast

from src.towel.unification.refactor_engine import UnificationRefactorEngine
from tests.test_helpers import assert_file_not_modified


class TestRefactorEngineEdgeCases(unittest.TestCase):
    """Exercise UnificationRefactorEngine edge cases for coverage."""

    def setUp(self):
        self.engine = UnificationRefactorEngine(max_parameters=5, min_lines=1)

    def test_analyze_directory_nonexistent(self):
        """Engine gracefully handles analyzing a missing directory."""
        proposals = self.engine.analyze_directory("/nonexistent/path")
        self.assertEqual(len(proposals), 0)

    def test_analyze_directory_non_recursive(self):
        """Non-recursive analysis uses read-only access to canonical fixtures."""
        test_examples_dir = Path("test_examples")
        original_contents = {py: py.read_text() for py in test_examples_dir.glob("*.py")}

        proposals = self.engine.analyze_directory("test_examples", recursive=False, verbose=True)
        self.assertGreaterEqual(len(proposals), 0)

        for py_file, original_content in original_contents.items():
            assert_file_not_modified(py_file, original_content)

    def test_analyze_file_with_syntax_error(self):
        """Engine returns no proposals when a syntax error blocks parsing."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as handle:
            handle.write("def broken(\n")
            handle.flush()
            temp_path = handle.name

        try:
            proposals = self.engine.analyze_file(temp_path)
            self.assertEqual(len(proposals), 0)
        finally:
            os.unlink(temp_path)

    def test_analyze_files_with_no_functions(self):
        """Files that lack candidate functions yield no proposals."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as handle:
            handle.write("# Just a comment\nx = 10\n")
            handle.flush()
            temp_path = handle.name

        try:
            proposals = self.engine.analyze_file(temp_path)
            self.assertEqual(len(proposals), 0)
        finally:
            os.unlink(temp_path)

    def test_apply_refactoring_cross_file_with_imports(self):
        """Cross-file refactoring writes changes into temporary copies only."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file1 = os.path.join(tmpdir, "module1.py")
            file2 = os.path.join(tmpdir, "module2.py")

            with open(file1, "w", encoding="utf-8") as handle:
                handle.write(
                    "def process_a(x):\n" "    y = x * 2\n" "    z = y + 10\n" "    return z\n"
                )
            with open(file2, "w", encoding="utf-8") as handle:
                handle.write(
                    "def process_b(x):\n" "    y = x * 2\n" "    z = y + 10\n" "    return z\n"
                )

            proposals = self.engine.analyze_files([file1, file2])

            if proposals:
                modified_files = self.engine.apply_refactoring_multi_file(proposals[0])

                self.assertIn(file1, modified_files)
                self.assertIn(file2, modified_files)

                if file1 != file2:
                    self.assertIn("from module1 import", modified_files[file2])

    def test_structural_similarity_check(self):
        """Strong structural differences should block duplicate detection."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file1 = os.path.join(tmpdir, "file1.py")

            with open(file1, "w", encoding="utf-8") as handle:
                handle.write(
                    "def foo():\n"
                    "    x = 1\n"
                    "    y = 2\n"
                    "    return x + y\n\n"
                    "def bar():\n"
                    "    for i in range(100):\n"
                    "        print(i)\n"
                    "        process(i)\n"
                    "        validate(i)\n"
                )

            proposals = self.engine.analyze_file(file1)
            self.assertEqual(len(proposals), 0)

    def test_empty_file_analysis(self):
        """Empty files do not produce proposals."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as handle:
            handle.write("")
            handle.flush()
            temp_path = handle.name

        try:
            proposals = self.engine.analyze_file(temp_path)
            self.assertEqual(len(proposals), 0)
        finally:
            os.unlink(temp_path)

    def test_file_with_only_docstring(self):
        """Files containing only a docstring yield no proposals."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as handle:
            handle.write('"""Module docstring."""\n')
            handle.flush()
            temp_path = handle.name

        try:
            proposals = self.engine.analyze_file(temp_path)
            self.assertEqual(len(proposals), 0)
        finally:
            os.unlink(temp_path)

    def _analyze_and_apply(self, source: str) -> str:
        """Analyze temporary module source, apply first proposal, and return code."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as handle:
            handle.write(textwrap.dedent(source))
            handle.flush()
            temp_path = handle.name

        try:
            proposals = self.engine.analyze_file(temp_path)
            self.assertTrue(proposals, "Expected at least one proposal")
            proposal = proposals[0]
            return self.engine.apply_refactoring(str(temp_path), proposal)
        finally:
            os.unlink(temp_path)

    def test_instance_methods_extracted_into_class(self):
        """Duplicate instance methods should extract helper into the same class."""

        result = self._analyze_and_apply(
            """
            class Example:
                def alpha(self, value):
                    tmp = value + 1
                    return tmp * 2

                def beta(self, value):
                    tmp = value + 1
                    return tmp * 2
            """
        )

        tree = ast.parse(result)
        cls = next(
            node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Example"
        )
        helper = next(
            node
            for node in cls.body
            if isinstance(node, ast.FunctionDef) and node.name not in {"alpha", "beta"}
        )
        helper_name = helper.name
        self.assertFalse(helper.decorator_list, "Instance helper should have no decorators")
        self.assertGreater(len(helper.args.args), 0)
        self.assertEqual(helper.args.args[0].arg, "self")

        for method_name in ("alpha", "beta"):
            method = next(
                node
                for node in cls.body
                if isinstance(node, ast.FunctionDef) and node.name == method_name
            )
            returns = [
                n
                for n in ast.walk(method)
                if isinstance(n, ast.Return) and isinstance(n.value, ast.Call)
            ]
            self.assertTrue(returns)
            for ret in returns:
                call = ret.value
                self.assertIsInstance(call.func, ast.Attribute)
                self.assertIsInstance(call.func.value, ast.Name)
                self.assertEqual(call.func.value.id, "self")
                self.assertEqual(call.func.attr, helper_name)

    def test_classmethods_extracted_into_class(self):
        """Duplicate class methods should place helper inside the class with @classmethod."""

        result = self._analyze_and_apply(
            """
            class Example:
                @classmethod
                def alpha(cls, value):
                    tmp = value + 1
                    return tmp * 2

                @classmethod
                def beta(cls, value):
                    tmp = value + 1
                    return tmp * 2
            """
        )

        tree = ast.parse(result)
        cls = next(
            node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Example"
        )
        helper = next(
            node
            for node in cls.body
            if isinstance(node, ast.FunctionDef) and node.name not in {"alpha", "beta"}
        )
        helper_name = helper.name
        decorator_ids = [dec.id for dec in helper.decorator_list if isinstance(dec, ast.Name)]
        self.assertIn("classmethod", decorator_ids)
        self.assertGreater(len(helper.args.args), 0)
        self.assertEqual(helper.args.args[0].arg, "cls")

        for method_name in ("alpha", "beta"):
            method = next(
                node
                for node in cls.body
                if isinstance(node, ast.FunctionDef) and node.name == method_name
            )
            call_sites = [n for n in ast.walk(method) if isinstance(n, ast.Call)]
            self.assertTrue(call_sites)
            for call in call_sites:
                if isinstance(call.func, ast.Attribute) and call.func.attr == helper_name:
                    self.assertIsInstance(call.func.value, ast.Name)
                    self.assertEqual(call.func.value.id, method.args.args[0].arg)

    def test_staticmethods_extracted_into_class(self):
        """Duplicate static methods should place helper inside the class with @staticmethod."""

        result = self._analyze_and_apply(
            """
            class Example:
                @staticmethod
                def alpha(value):
                    tmp = value + 1
                    return tmp * 2

                @staticmethod
                def beta(value):
                    tmp = value + 1
                    return tmp * 2
            """
        )

        tree = ast.parse(result)
        cls = next(
            node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Example"
        )
        helper = next(
            node
            for node in cls.body
            if isinstance(node, ast.FunctionDef) and node.name not in {"alpha", "beta"}
        )
        helper_name = helper.name
        decorator_ids = [dec.id for dec in helper.decorator_list if isinstance(dec, ast.Name)]
        self.assertIn("staticmethod", decorator_ids)
        helper_args = [arg.arg for arg in helper.args.args]
        self.assertTrue(helper_args, "Static helper should retain explicit parameters")
        self.assertNotIn("self", helper_args)
        self.assertNotIn("cls", helper_args)

        for method_name in ("alpha", "beta"):
            method = next(
                node
                for node in cls.body
                if isinstance(node, ast.FunctionDef) and node.name == method_name
            )
            call_sites = [n for n in ast.walk(method) if isinstance(n, ast.Call)]
            self.assertTrue(call_sites)
            for call in call_sites:
                if isinstance(call.func, ast.Attribute) and call.func.attr == helper_name:
                    self.assertIsInstance(call.func.value, ast.Name)
                    self.assertEqual(call.func.value.id, "Example")

    def test_sibling_instance_methods_promote_to_common_base(self):
        """Sibling instance methods should extract helpers into their nearest shared base class."""

        result = self._analyze_and_apply(
            """
            class Base:
                pass

            class First(Base):
                def alpha(self, value):
                    tmp = value + 1
                    return tmp * 2

            class Second(Base):
                def beta(self, value):
                    tmp = value + 1
                    return tmp * 2
            """
        )

        tree = ast.parse(result)
        base = next(
            node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Base"
        )
        helper = next(node for node in base.body if isinstance(node, ast.FunctionDef))
        self.assertFalse(helper.decorator_list, "Base helper should default to instance semantics")
        self.assertGreater(len(helper.args.args), 0)
        self.assertEqual(helper.args.args[0].arg, "self")

        for cls_name, method_name in (("First", "alpha"), ("Second", "beta")):
            cls = next(
                node
                for node in tree.body
                if isinstance(node, ast.ClassDef) and node.name == cls_name
            )
            method = next(
                node
                for node in cls.body
                if isinstance(node, ast.FunctionDef) and node.name == method_name
            )
            helper_calls = [
                call
                for call in ast.walk(method)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == helper.name
            ]
            self.assertTrue(helper_calls)
            for call in helper_calls:
                self.assertIsInstance(call.func.value, ast.Name)
                self.assertEqual(call.func.value.id, method.args.args[0].arg)


if __name__ == "__main__":
    unittest.main()
