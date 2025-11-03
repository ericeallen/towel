#!/usr/bin/env python3
"""
Regression tests for Towel refactoring engine.

This module ensures that the refactoring output remains stable over time by:
1. Comparing current output against baseline expected output
2. Allowing for alpha-renaming, comments, and whitespace differences
3. Running observational equivalence tests on all proposals
4. Failing if output differs or equivalence checks fail
"""
import sys
import unittest
import tempfile
import shutil
import re
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.towel.unification.refactor_engine import UnificationRefactorEngine
from tests.automatic_equivalence_tester import AutomaticEquivalenceTester
from tests.crossfile_equivalence_tester import CrossFileEquivalenceTester


def normalize_generated_names(code: str) -> str:
    """
    Normalize generated function and parameter names for alpha-equivalence checking.

    Replaces __extracted_func_<N> and __param_<N> with canonical names
    (__extracted_func_0, __param_0, etc.) in order of first appearance.

    This allows comparison of semantically equivalent code that differs only
    in the numeric suffixes of generated names.
    """
    # Track mappings from original names to normalized names
    func_mapping = {}
    param_mapping = {}

    # Find all function names and create canonical mapping
    func_names = re.findall(r"__extracted_func_\d+", code)
    for func_name in func_names:
        if func_name not in func_mapping:
            func_mapping[func_name] = f"__extracted_func_{len(func_mapping)}"

    # Find all parameter names and create canonical mapping
    param_names = re.findall(r"__param_\d+", code)
    for param_name in param_names:
        if param_name not in param_mapping:
            param_mapping[param_name] = f"__param_{len(param_mapping)}"

    # Apply replacements (sort by length descending to avoid partial replacements)
    result = code
    for original, normalized in sorted(func_mapping.items(), key=lambda x: len(x[0]), reverse=True):
        result = result.replace(original, normalized)
    for original, normalized in sorted(
        param_mapping.items(), key=lambda x: len(x[0]), reverse=True
    ):
        result = result.replace(original, normalized)

    return result


class TestSingleFileRegression(unittest.TestCase):
    """
    Regression tests for single-file refactorings.

    Compares current refactoring output against baseline expected output.
    """

    @classmethod
    def setUpClass(cls):
        """Set up test fixtures."""
        cls.engine = UnificationRefactorEngine(max_parameters=5, min_lines=4)
        cls.tester = AutomaticEquivalenceTester(cls.engine)
        cls.test_examples = project_root / "test_examples"
        cls.expected_output = project_root / "test_examples_expected_output"

    def test_expected_output_exists(self):
        """Verify that expected output directory exists."""
        self.assertTrue(
            self.expected_output.exists(),
            f"Expected output directory not found: {self.expected_output}\n"
            "Run: python tests/generate_baseline.py",
        )

    def test_observational_equivalence_all_examples(self):
        """
        Test that all refactored code is observationally equivalent to original.

        This runs the comprehensive observational equivalence suite on all
        test examples. Any failures indicate a regression in refactoring quality.
        """
        results = self.tester.test_all_examples(str(self.test_examples), verbose=True)

        # Collect failed files
        failed_files = []
        for filename, file_result in results["file_results"].items():
            if file_result["failed"] > 0:
                # Add whitespace before each failed file for clarity
                if failed_files:
                    failed_files.append("")  # Blank line separator
                failed_files.append(
                    f"  {filename}: {file_result['failed']}/{file_result['passed'] + file_result['failed']} failed"
                )
                # Show only first error example (rest is clutter)
                for error in file_result["errors"][:1]:
                    failed_files.append(f"    - {error}")

        # Assert all tests passed
        if failed_files:
            failure_msg = (
                f"\nObservational equivalence failures detected:\n"
                f"Total proposals tested: {results['total_proposals_tested']}\n"
                f"Passed: {results['total_passed']}\n"
                f"Failed: {results['total_failed']}\n"
                f"Success rate: {100 * results['total_passed'] / results['total_proposals_tested']:.1f}%\n\n"
                f"Failed files:\n"
            ) + "\n".join(failed_files)
            self.fail(failure_msg)

    def test_refactoring_output_stability(self):
        """
        Test that refactoring output matches baseline (up to alpha-renaming).

        This test ensures that changes to the refactoring engine don't
        unintentionally change the output on known test cases.
        """
        # Get all test example Python files
        python_files = [
            f for f in self.test_examples.glob("*.py") if f.is_file() and not f.name.startswith("_")
        ]

        differences = []

        for py_file in sorted(python_files):
            # Check if baseline exists for this file
            baseline_file = self.expected_output / py_file.name

            if not baseline_file.exists():
                # No baseline for this file (skip)
                continue

            # Compute current fixed-point refactoring output using a temp copy
            try:
                with tempfile.NamedTemporaryFile(mode="w+", suffix=".py", delete=False) as tmp:
                    tmp.write(py_file.read_text())
                    tmp.flush()
                    tmp_path = Path(tmp.name)

                final_code, num_applied, _ = self.engine.refactor_to_fixed_point(str(tmp_path))
                current_output = final_code
            except Exception as e:
                differences.append(f"{py_file.name}: Fixed-point refactoring failed: {e}")
                continue
            finally:
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass

            # Read baseline
            baseline_output = baseline_file.read_text()

            # Normalize generated names for alpha-equivalence comparison
            # This allows comparison despite different numeric suffixes in
            # __extracted_func_<N> and __param_<N> names
            normalized_current = normalize_generated_names(current_output)
            normalized_baseline = normalize_generated_names(baseline_output)

            if normalized_current != normalized_baseline:
                differences.append(
                    f"{py_file.name}: Output differs from baseline (after normalization)\n"
                    f"  Baseline length: {len(baseline_output)} chars\n"
                    f"  Current length: {len(current_output)} chars"
                )

        if differences:
            failure_msg = (
                "\n\nRegression detected - refactoring output changed:\n\n"
                + "\n".join(differences)
                + "\n\nIf this change is intentional, regenerate baseline with:\n"
                "  python tests/generate_baseline.py\n"
            )
            self.fail(failure_msg)


class TestCrossFileRegression(unittest.TestCase):
    """
    Regression tests for cross-file refactorings.

    Ensures cross-file refactorings remain stable and correct.
    """

    @classmethod
    def setUpClass(cls):
        """Set up test fixtures."""
        cls.engine = UnificationRefactorEngine(max_parameters=5, min_lines=4)
        cls.tester = CrossFileEquivalenceTester(cls.engine)
        cls.crossfile_examples = project_root / "test_examples_crossfile"
        cls.expected_output = project_root / "test_examples_crossfile_expected_output"

    def test_expected_output_exists(self):
        """Verify that expected cross-file output directory exists."""
        self.assertTrue(
            self.expected_output.exists(),
            f"Expected cross-file output directory not found: {self.expected_output}\n"
            "Run: python tests/generate_baseline.py",
        )

    def test_crossfile_observational_equivalence(self):
        """
        Test that all cross-file refactorings preserve observational equivalence.

        This runs comprehensive tests on all cross-file test projects.
        """
        # Get all project directories
        project_dirs = [
            d
            for d in self.crossfile_examples.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ]

        if not project_dirs:
            self.skipTest("No cross-file test projects found")

        total_passed = 0
        total_failed = 0
        failures = []

        for project_dir in sorted(project_dirs):
            passed, failed, errors = self.tester.test_project(str(project_dir), verbose=True)

            total_passed += passed
            total_failed += failed

            if failed > 0:
                failures.append(f"\n{project_dir.name}:")
                failures.append(f"  Passed: {passed}, Failed: {failed}")
                for error in errors[:5]:
                    failures.append(f"    - {error}")

        if total_failed > 0:
            failure_msg = (
                f"\nCross-file observational equivalence failures:\n"
                f"Total passed: {total_passed}\n"
                f"Total failed: {total_failed}\n"
                f"Success rate: {100 * total_passed / (total_passed + total_failed):.1f}%\n"
            ) + "\n".join(failures)
            self.fail(failure_msg)


def main():
    """Run regression tests."""
    # Create test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add test classes
    suite.addTests(loader.loadTestsFromTestCase(TestSingleFileRegression))
    suite.addTests(loader.loadTestsFromTestCase(TestCrossFileRegression))

    # Run with verbose output
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Return exit code
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
