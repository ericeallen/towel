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

from towel.unification.refactor_engine import UnificationRefactorEngine
from tests.test_helpers import TemporaryModuleTestCase, assert_file_not_modified


class TestRefactorEngineEdgeCases(TemporaryModuleTestCase):
    """Exercise UnificationRefactorEngine edge cases for coverage."""

    def setUp(self):
        super().setUp()
        self.engine = UnificationRefactorEngine(max_parameters=5, min_lines=1)

    def test_analyze_directory_nonexistent(self):
        """Engine gracefully handles analyzing a missing directory."""
        proposals = self.engine.analyze_directory("/nonexistent/path")
        self.assertEqual(len(proposals), 0)

    def test_analyze_directory_non_recursive(self):
        """Non-recursive analysis uses read-only access to canonical fixtures."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            top = root / "top.py"
            child = root / "nested" / "child.py"
            child.parent.mkdir()
            top.write_text("def one(x):\n    return x\n")
            child.write_text("def two(x):\n    return x\n")
            original = {path: path.read_text() for path in (top, child)}
            self.assertEqual(self.engine._find_python_files(directory, False), [str(top)])
            self.engine.analyze_directory(directory, recursive=False)
            for path, contents in original.items():
                assert_file_not_modified(path, contents)

    def test_analyze_file_with_syntax_error(self):
        """A file that does not parse is skipped with a warning naming it, not analyzed."""
        temp_path = self._write_temp("def broken(\n")

        with self.assertLogs("towel", level="WARNING") as logs:
            proposals = self.engine.analyze_file(temp_path)
        self.assertEqual(proposals, [])
        (message,) = logs.output
        self.assertIn(f"Skipping {temp_path}: '(' was never closed", message)

    def test_analyze_files_with_no_functions(self):
        """Files that lack candidate functions yield no proposals."""
        temp_path = self._write_temp("# Just a comment\nx = 10\n")

        proposals = self.engine.analyze_file(temp_path)
        self.assertEqual(len(proposals), 0)

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
        temp_path = self._write_temp("")

        proposals = self.engine.analyze_file(temp_path)
        self.assertEqual(len(proposals), 0)

    def test_file_with_only_docstring(self):
        """Files containing only a docstring yield no proposals."""
        temp_path = self._write_temp('"""Module docstring."""\n')

        proposals = self.engine.analyze_file(temp_path)
        self.assertEqual(len(proposals), 0)

    def _analyze_and_apply(self, source: str) -> str:
        """Analyze temporary module source, apply first proposal, and return code."""
        temp_path = self._write_temp(textwrap.dedent(source))

        proposals = self.engine.analyze_file(temp_path)
        self.assertTrue(proposals, "Expected at least one proposal")
        proposal = proposals[0]
        return self.engine.apply_refactoring(str(temp_path), proposal)

    def test_instance_methods_extracted_into_class(self):
        """Duplicate instance methods should extract helper into the same class."""

        result = self._analyze_and_apply("""
            class Example:
                def alpha(self, value):
                    tmp = value + self.offset
                    return tmp * 2

                def beta(self, value):
                    tmp = value + self.offset
                    return tmp * 2
            """)

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
        self.assertEqual([arg.arg for arg in helper.args.args], ["self", "value"])

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
            self.assertEqual(len(returns), 1, "the method returns the helper call")
            for ret in returns:
                call = ret.value
                assert isinstance(call, ast.Call)
                self.assertIsInstance(call.func, ast.Attribute)
                assert isinstance(call.func, ast.Attribute)
                self.assertIsInstance(call.func.value, ast.Name)
                assert isinstance(call.func.value, ast.Name)
                self.assertEqual(call.func.value.id, "self")
                self.assertEqual(call.func.attr, helper_name)

    def test_classmethods_extracted_into_class(self):
        """Duplicate class methods should place helper inside the class with @classmethod."""

        result = self._analyze_and_apply("""
            class Example:
                step = 1

                @classmethod
                def alpha(cls, value):
                    tmp = value + cls.step
                    return tmp * 2

                @classmethod
                def beta(cls, value):
                    tmp = value + cls.step
                    return tmp * 2
            """)

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
        self.assertEqual([arg.arg for arg in helper.args.args], ["cls", "value"])

        for method_name in ("alpha", "beta"):
            method = next(
                node
                for node in cls.body
                if isinstance(node, ast.FunctionDef) and node.name == method_name
            )
            call_sites = [ast.unparse(n) for n in ast.walk(method) if isinstance(n, ast.Call)]
            self.assertEqual(call_sites, [f"cls.{helper_name}(value)"])

    def test_staticmethods_share_a_module_level_helper(self):
        """Duplicate static methods call a module function: a static helper had to be
        reached through the class's name, which the method cannot be sure of."""

        result = self._analyze_and_apply("""
            class Example:
                @staticmethod
                def alpha(value):
                    tmp = value + 1
                    return tmp * 2

                @staticmethod
                def beta(value):
                    tmp = value + 1
                    return tmp * 2
            """)

        tree = ast.parse(result)
        cls = next(
            node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Example"
        )
        self.assertEqual(
            [node.name for node in cls.body if isinstance(node, ast.FunctionDef)],
            ["alpha", "beta"],
        )
        helper = next(node for node in tree.body if isinstance(node, ast.FunctionDef))
        helper_name = helper.name
        self.assertEqual(helper.decorator_list, [])
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
            call_sites = [ast.unparse(n) for n in ast.walk(method) if isinstance(n, ast.Call)]
            self.assertEqual(call_sites, [f"{helper_name}(value)"])

    def test_sibling_instance_methods_promote_to_common_base(self):
        """Sibling instance methods should extract helpers into their nearest shared base class."""

        result = self._analyze_and_apply("""
            class Base:
                pass

            class First(Base):
                def alpha(self, value):
                    tmp = value + self.offset
                    return tmp * 2

            class Second(Base):
                def beta(self, value):
                    tmp = value + self.offset
                    return tmp * 2
            """)

        tree = ast.parse(result)
        base = next(
            node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Base"
        )
        helper = next(node for node in base.body if isinstance(node, ast.FunctionDef))
        self.assertFalse(helper.decorator_list, "Base helper should default to instance semantics")
        self.assertEqual([arg.arg for arg in helper.args.args], ["self", "value"])

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
            self.assertEqual(len(helper_calls), 1, "each sibling method calls the helper once")
            for call in helper_calls:
                assert isinstance(call.func, ast.Attribute)
                self.assertIsInstance(call.func.value, ast.Name)
                assert isinstance(call.func.value, ast.Name)
                self.assertEqual(call.func.value.id, method.args.args[0].arg)


if __name__ == "__main__":
    unittest.main()
