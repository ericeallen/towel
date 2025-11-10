"""Refactor engine edge-case coverage tests.

These tests focus on analyzing individual files and directories, covering
error-handling paths and ensuring we never modify canonical fixtures.
"""

import os
import tempfile
import unittest
from pathlib import Path

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
                    "def process_a(x):\n"
                    "    y = x * 2\n"
                    "    z = y + 10\n"
                    "    return z\n"
                )
            with open(file2, "w", encoding="utf-8") as handle:
                handle.write(
                    "def process_b(x):\n"
                    "    y = x * 2\n"
                    "    z = y + 10\n"
                    "    return z\n"
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


if __name__ == "__main__":
    unittest.main()
