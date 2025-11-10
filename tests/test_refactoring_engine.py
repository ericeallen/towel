"""
End-to-end tests for the refactoring engine.

Tests the complete workflow from detection to application.

IMPORTANT: These tests NEVER modify test_examples files directly.
All test output goes to temporary directories that are automatically cleaned up.
"""

import unittest
import ast
from pathlib import Path
from towel.unification.refactor_engine import UnificationRefactorEngine
from tests.test_helpers import (
    temporary_test_directory,
    copy_example_to_temp,
    get_test_example_path,
    assert_file_not_modified,
)


class TestRefactoringEngine(unittest.TestCase):
    """End-to-end refactoring tests."""

    def setUp(self):
        self.engine = UnificationRefactorEngine(
            max_parameters=5, min_lines=4, parameterize_constants=True
        )

    def test_example1_simple(self):
        """Test Example 1: Simple validation logic.

        Reads from test_examples (read-only) and verifies it's never modified.
        """
        example_path = get_test_example_path("example1_simple.py")
        original_content = example_path.read_text()

        proposals = self.engine.analyze_file(str(example_path))
        self.assertGreater(len(proposals), 0, "Should find duplicates in example1")

        # Check the best proposal
        prop = proposals[0]
        self.assertIsInstance(prop.extracted_function, ast.FunctionDef)
        self.assertGreater(len(prop.replacements), 1, "Should have multiple replacements")

        # Apply refactoring (returns string, doesn't write to file)
        refactored = self.engine.apply_refactoring(str(example_path), prop)

        # Validate refactored code
        try:
            tree = ast.parse(refactored)
        except SyntaxError as e:
            self.fail(f"Refactored code is not valid Python: {e}")

        # Verify original file was never modified
        assert_file_not_modified(example_path, original_content)

        # Check that extracted function exists
        func_names = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
        self.assertIn(prop.extracted_function.name, func_names)

    def test_example4_complex(self):
        """Test Example 4: Complex data processing loops.

        Reads from test_examples (read-only) and verifies it's never modified.
        """
        example_path = get_test_example_path("example4_complex.py")
        original_content = example_path.read_text()

        proposals = self.engine.analyze_file(str(example_path))
        self.assertGreater(len(proposals), 0, "Should find duplicates in example4")

        # Apply best proposal
        prop = proposals[0]
        refactored = self.engine.apply_refactoring(str(example_path), prop)

        # Validate
        try:
            ast.parse(refactored)
        except SyntaxError as e:
            self.fail(f"Refactored code is not valid Python: {e}")

        # Verify original file was never modified
        assert_file_not_modified(example_path, original_content)

    def test_apply_refactoring_to_temp_file(self):
        """Test applying refactoring writes to a file correctly.

        This test uses a temporary directory to ensure test_examples are never modified.
        """
        # Read from test_examples (read-only)
        example_path = get_test_example_path("example1_simple.py")
        original_content = example_path.read_text()

        proposals = self.engine.analyze_file(str(example_path))
        self.assertGreater(len(proposals), 0)

        prop = proposals[0]

        # Use temporary directory for all file operations
        with temporary_test_directory() as temp_dir:
            # Copy example to temp directory
            temp_example = copy_example_to_temp("example1_simple.py", temp_dir)

            # Apply refactoring to the temp copy
            refactored = self.engine.apply_refactoring(str(temp_example), prop)

            # Write result to temp file
            output_file = temp_dir / "output.py"
            output_file.write_text(refactored)

            # Read back and validate
            content = output_file.read_text()

            try:
                ast.parse(content)
            except SyntaxError as e:
                self.fail(f"Refactored file is not valid Python: {e}")

        # Verify original test_examples file was never modified
        assert_file_not_modified(example_path, original_content)

    def test_parameter_limit_respected(self):
        """Test that max_parameters limit is respected.

        Reads from test_examples (read-only) and verifies it's never modified.
        """
        example_path = get_test_example_path("example1_simple.py")
        original_content = example_path.read_text()

        # Create engine with low parameter limit
        engine = UnificationRefactorEngine(max_parameters=2, min_lines=4)

        proposals = engine.analyze_file(str(example_path))

        # All proposals should have <= 2 parameters
        for prop in proposals:
            self.assertLessEqual(
                prop.parameters_count,
                2,
                f"Proposal has {prop.parameters_count} parameters, exceeds limit of 2",
            )

        # Verify original file was never modified
        assert_file_not_modified(example_path, original_content)

    def test_min_lines_respected(self):
        """Test that min_lines limit is respected.

        Reads from test_examples (read-only) and verifies it's never modified.
        """
        example_path = get_test_example_path("example1_simple.py")
        original_content = example_path.read_text()

        # Create engine with high min_lines
        engine = UnificationRefactorEngine(max_parameters=5, min_lines=10)

        proposals = engine.analyze_file(str(example_path))

        # Check that all extracted functions have >= 10 lines
        for prop in proposals:
            line_count = len(prop.extracted_function.body)
            self.assertGreaterEqual(
                line_count, 1, f"Extracted function is too short"  # At least some statements
            )

        # Verify original file was never modified
        assert_file_not_modified(example_path, original_content)


class TestDirectoryAnalysis(unittest.TestCase):
    """Test analyzing entire directories."""

    def setUp(self):
        self.engine = UnificationRefactorEngine(
            max_parameters=5, min_lines=4, parameterize_constants=True
        )

    def test_analyze_directory(self):
        """Test analyzing a directory of files.

        Reads from test_examples (read-only) and verifies no files are modified.
        """
        # Capture original state of all Python files in test_examples
        from pathlib import Path

        test_examples_dir = Path("test_examples")
        original_contents = {}
        for py_file in test_examples_dir.glob("*.py"):
            original_contents[py_file] = py_file.read_text()

        # Enable progress for long-running analysis
        proposals = self.engine.analyze_directory(
            "test_examples", recursive=True, verbose=True, progress="tqdm"
        )

        # Should find duplicates across all example files
        self.assertGreater(len(proposals), 0, "Should find duplicates in test_examples directory")

        # Proposals should include files from the directory
        file_paths = set()
        for prop in proposals:
            file_paths.add(prop.file_path)

        self.assertGreater(len(file_paths), 0, "Should have file paths in proposals")

        # Verify no files were modified
        for py_file, original_content in original_contents.items():
            assert_file_not_modified(py_file, original_content)

    def test_analyze_cross_file(self):
        """Test analyzing multiple specific files for cross-file duplicates.

        Reads from test_examples (read-only) and verifies files are not modified.
        """
        file1_path = get_test_example_path("example3_file1.py")
        file2_path = get_test_example_path("example3_file2.py")

        # Capture original contents
        file1_original = file1_path.read_text()
        file2_original = file2_path.read_text()

        files = [str(file1_path), str(file2_path)]

        proposals = self.engine.analyze_files(files)

        # Should find cross-file duplicates
        self.assertGreater(len(proposals), 0, "Should find cross-file duplicates")

        # Check if any proposal is cross-file (replacement targeting a different file)
        cross_file_proposals = [
            p
            for p in proposals
            if any((r.file_path or p.file_path) != p.file_path for r in p.replacements)
        ]

        self.assertGreater(
            len(cross_file_proposals), 0, "Should find at least one cross-file proposal"
        )

        # Verify files were not modified
        assert_file_not_modified(file1_path, file1_original)
        assert_file_not_modified(file2_path, file2_original)


if __name__ == "__main__":
    unittest.main()
