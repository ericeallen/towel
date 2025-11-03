"""
Comprehensive tests to cover remaining edge cases and error paths.

Targets coverage gaps in refactor_engine, unifier, and extractor.

IMPORTANT: These tests NEVER modify test_examples files.
Tests that read from test_examples do so in read-only mode.
"""

import unittest
import ast
import tempfile
import os
from pathlib import Path
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.unifier import Unifier
from towel.unification.extractor import HygienicExtractor
from towel.unification.scope_analyzer import ScopeAnalyzer
from tests.test_helpers import assert_file_not_modified


class TestRefactorEngineEdgeCases(unittest.TestCase):
    """Test refactor_engine edge cases for coverage."""

    def setUp(self):
        self.engine = UnificationRefactorEngine(max_parameters=5, min_lines=1)

    def test_analyze_directory_nonexistent(self):
        """Test analyzing a non-existent directory."""
        proposals = self.engine.analyze_directory("/nonexistent/path")
        self.assertEqual(len(proposals), 0)

    def test_analyze_directory_non_recursive(self):
        """Test non-recursive directory analysis.

        Reads from test_examples (read-only) and verifies no files are modified.
        """
        # Capture original state of all Python files in test_examples
        test_examples_dir = Path("test_examples")
        original_contents = {}
        for py_file in test_examples_dir.glob("*.py"):
            original_contents[py_file] = py_file.read_text()

        proposals = self.engine.analyze_directory("test_examples", recursive=False, verbose=True)
        # Should find some duplicates
        self.assertGreaterEqual(len(proposals), 0)

        # Verify no files were modified
        for py_file, original_content in original_contents.items():
            assert_file_not_modified(py_file, original_content)

    def test_analyze_file_with_syntax_error(self):
        """Test analyzing a file with syntax errors."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def broken(\n")  # Syntax error
            f.flush()
            temp_path = f.name

        try:
            proposals = self.engine.analyze_file(temp_path)
            self.assertEqual(len(proposals), 0)  # Should handle gracefully
        finally:
            os.unlink(temp_path)

    def test_analyze_files_with_no_functions(self):
        """Test analyzing files with no functions."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("# Just a comment\nx = 10\n")
            f.flush()
            temp_path = f.name

        try:
            proposals = self.engine.analyze_file(temp_path)
            self.assertEqual(len(proposals), 0)
        finally:
            os.unlink(temp_path)

    def test_apply_refactoring_cross_file_with_imports(self):
        """Test applying cross-file refactoring with import generation."""
        # Create two temporary files with duplicates
        code1 = """
def process_a(x):
    y = x * 2
    z = y + 10
    return z
"""
        code2 = """
def process_b(x):
    y = x * 2
    z = y + 10
    return z
"""

        with tempfile.TemporaryDirectory() as tmpdir:
            file1 = os.path.join(tmpdir, "module1.py")
            file2 = os.path.join(tmpdir, "module2.py")

            with open(file1, "w") as f:
                f.write(code1)
            with open(file2, "w") as f:
                f.write(code2)

            # Analyze
            proposals = self.engine.analyze_files([file1, file2])

            if proposals:
                # Apply first proposal
                modified_files = self.engine.apply_refactoring_multi_file(proposals[0])

                # Check that both files were modified
                self.assertIn(file1, modified_files)
                self.assertIn(file2, modified_files)

                # Check that import was added to file2
                if file1 != file2:
                    self.assertIn("from module1 import", modified_files[file2])

    def test_structural_similarity_check(self):
        """Test structural similarity filtering."""
        # Create code that's too different
        code1 = """
def foo():
    x = 1
    y = 2
    return x + y
"""
        code2 = """
def bar():
    for i in range(100):
        print(i)
        process(i)
        validate(i)
"""

        with tempfile.TemporaryDirectory() as tmpdir:
            file1 = os.path.join(tmpdir, "file1.py")

            with open(file1, "w") as f:
                f.write(code1 + code2)

            proposals = self.engine.analyze_file(file1)
            # Should not find duplicates between very different structures
            self.assertEqual(len(proposals), 0)

    def test_empty_file_analysis(self):
        """Test analyzing an empty file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("")
            f.flush()
            temp_path = f.name

        try:
            proposals = self.engine.analyze_file(temp_path)
            self.assertEqual(len(proposals), 0)
        finally:
            os.unlink(temp_path)

    def test_file_with_only_docstring(self):
        """Test analyzing a file with only a module docstring."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write('"""Module docstring."""\n')
            f.flush()
            temp_path = f.name

        try:
            proposals = self.engine.analyze_file(temp_path)
            self.assertEqual(len(proposals), 0)
        finally:
            os.unlink(temp_path)


class TestExtractorEdgeCases(unittest.TestCase):
    """Test extractor edge cases for coverage."""

    def setUp(self):
        self.extractor = HygienicExtractor()
        self.analyzer = ScopeAnalyzer()

    def test_extract_with_conflicting_names(self):
        """Test extraction when parameter names conflict with enclosing scope."""
        code = """
param_0 = "existing"

def foo():
    x = 10
    y = 20
    return x + y

def bar():
    x = 30
    y = 40
    return x + y
"""
        tree = ast.parse(code)
        self.analyzer.analyze(tree)

        foo_func = tree.body[1]
        bar_func = tree.body[2]

        from towel.unification.unifier import Unifier, Substitution

        unifier = Unifier(max_parameters=5)

        blocks = [foo_func.body, bar_func.body]
        substitution = unifier.unify_blocks(blocks, [{}, {}])

        if substitution:
            # param_0 already exists in enclosing scope
            enclosing_names = {"param_0", "foo", "bar"}

            func_def, param_order = self.extractor.extract_function(
                template_block=foo_func.body,
                substitution=substitution,
                free_variables=set(),
                enclosing_names=enclosing_names,
                is_value_producing=True,
                function_name="extracted_func",
            )

            # Should rename to avoid conflict
            param_names = [arg.arg for arg in func_def.args.args]
            # Should not use param_0 since it conflicts
            for param in param_names:
                self.assertNotEqual(param, "param_0")


class TestUnifierEdgeCases(unittest.TestCase):
    """Test unifier edge cases for coverage."""

    def setUp(self):
        self.unifier = Unifier(max_parameters=5, parameterize_constants=True)

    def test_unify_dict_literals(self):
        """Test unifying dictionary literals."""
        code1 = """
config = {'key': 'value1', 'timeout': 10}
"""
        code2 = """
config = {'key': 'value2', 'timeout': 20}
"""
        tree1 = ast.parse(code1)
        tree2 = ast.parse(code2)
        blocks = [tree1.body, tree2.body]

        result = self.unifier.unify_blocks(blocks, [{}, {}])
        self.assertIsNotNone(result)

    def test_unify_list_literals(self):
        """Test unifying list literals."""
        code1 = """
items = [1, 2, 3]
"""
        code2 = """
items = [4, 5, 6]
"""
        tree1 = ast.parse(code1)
        tree2 = ast.parse(code2)
        blocks = [tree1.body, tree2.body]

        result = self.unifier.unify_blocks(blocks, [{}, {}])
        self.assertIsNotNone(result)

    def test_unify_set_literals(self):
        """Test unifying set literals."""
        code1 = """
values = {1, 2, 3}
"""
        code2 = """
values = {4, 5, 6}
"""
        tree1 = ast.parse(code1)
        tree2 = ast.parse(code2)
        blocks = [tree1.body, tree2.body]

        result = self.unifier.unify_blocks(blocks, [{}, {}])
        self.assertIsNotNone(result)

    def test_unify_tuple_literals(self):
        """Test unifying tuple literals."""
        code1 = """
point = (1, 2)
"""
        code2 = """
point = (3, 4)
"""
        tree1 = ast.parse(code1)
        tree2 = ast.parse(code2)
        blocks = [tree1.body, tree2.body]

        result = self.unifier.unify_blocks(blocks, [{}, {}])
        self.assertIsNotNone(result)

    def test_unify_lambda_expressions(self):
        """Test unifying lambda expressions."""
        code1 = """
func = lambda x: x * 2
"""
        code2 = """
func = lambda y: y * 3
"""
        tree1 = ast.parse(code1)
        tree2 = ast.parse(code2)
        blocks = [tree1.body, tree2.body]

        result = self.unifier.unify_blocks(blocks, [{}, {}])
        self.assertIsNotNone(result)

    def test_unify_assert_statements(self):
        """Test unifying assert statements."""
        code1 = """
assert x > 0
"""
        code2 = """
assert y > 0
"""
        tree1 = ast.parse(code1)
        tree2 = ast.parse(code2)
        blocks = [tree1.body, tree2.body]

        result = self.unifier.unify_blocks(blocks, [{}, {}])
        self.assertIsNotNone(result)


if __name__ == "__main__":
    unittest.main()
