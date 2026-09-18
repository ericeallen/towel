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
import ast
import unittest
import tempfile
import re
from pathlib import Path
from typing import Dict

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from towel.unification.refactor_engine import UnificationRefactorEngine
from tests.automatic_equivalence_tester import AutomaticEquivalenceTester
from tests.crossfile_equivalence_tester import CrossFileEquivalenceTester


def normalize_generated_names(code: str) -> str:
    """Return an AST fingerprint modulo generated identifier names and formatting.

    Parse both versions with the running interpreter, so unparser differences such
    as optional tuple-target parentheses cannot masquerade as a regression. Rename
    identifier fields only: literal values, operators, statement order and binding
    structure remain part of the comparison. Simultaneous AST renaming also avoids
    cascading string replacements when two helper numbers exchange positions.
    """
    function_pattern = re.compile(r"_{0,2}extracted_func(?:tion)?(?:_\d+)?")
    parameter_pattern = re.compile(r"__param(?:_\d+)?")
    function_mapping: dict[str, str] = {}
    parameter_mapping: dict[str, str] = {}

    def identifier(name: str) -> str:
        if function_pattern.fullmatch(name):
            return function_mapping.setdefault(name, f"__extracted_func_{len(function_mapping)}")
        if parameter_pattern.fullmatch(name):
            return parameter_mapping.setdefault(name, f"__param_{len(parameter_mapping)}")
        return name

    class NormalizeIdentifiers(ast.NodeTransformer):
        def generic_visit(self, node: ast.AST) -> ast.AST:
            for field, value in ast.iter_fields(node):
                if field in {"id", "name", "arg", "attr", "asname"} and isinstance(value, str):
                    setattr(node, field, identifier(value))
            if isinstance(node, (ast.Global, ast.Nonlocal)):
                node.names = [identifier(name) for name in node.names]
            return super().generic_visit(node)

    tree = NormalizeIdentifiers().visit(ast.parse(code))
    return ast.dump(tree, include_attributes=False)


class TestOutputNormalization(unittest.TestCase):
    def test_printer_parentheses_spacing_and_comments_are_ignored(self) -> None:
        before = "for key, value in pairs:\n    f = lambda: value  # comment\n"
        after = "for (key, value) in pairs:\n    f = lambda : value\n"
        self.assertEqual(normalize_generated_names(before), normalize_generated_names(after))

    def test_operator_and_replacement_structure_remain_significant(self) -> None:
        source = "def f(x):\n    value = x + 1\n    return value\n"
        for changed in (
            source.replace("x + 1", "x - 1"),
            "def f(x):\n    helper(x)\n    return value\n",
        ):
            with self.subTest(changed=changed):
                self.assertNotEqual(
                    normalize_generated_names(source), normalize_generated_names(changed)
                )

    def test_generated_names_in_literals_are_not_erased(self) -> None:
        self.assertNotEqual(
            normalize_generated_names("value = '__param_0'"),
            normalize_generated_names("value = '__param_1'"),
        )

    def test_generated_names_inside_fstring_expressions_are_identifiers(self) -> None:
        self.assertEqual(
            normalize_generated_names("def f(__param_7): return f'{__param_7}'"),
            normalize_generated_names("def f(__param_2): return f'{__param_2}'"),
        )

    def test_alpha_renaming_preserves_distinct_helper_bindings(self) -> None:
        source = (
            "def __extracted_func_1(): return __extracted_func_0()\n"
            "def __extracted_func_0(): return 3\n"
        )
        renamed = (
            "def __extracted_func_8(): return __extracted_func_9()\n"
            "def __extracted_func_9(): return 3\n"
        )
        self.assertEqual(normalize_generated_names(source), normalize_generated_names(renamed))
        wrong_call = renamed.replace("return __extracted_func_9()", "return __extracted_func_8()")
        self.assertNotEqual(
            normalize_generated_names(source), normalize_generated_names(wrong_call)
        )


def find_duplicate_helpers(root: Path) -> list[str]:
    """Return human-readable entries for files containing duplicate helper names."""

    if not root.exists():
        return []

    helper_pattern = re.compile(r"^\s*def\s+(_{1,2}extracted_func(?:_\d+)?)\b", re.MULTILINE)
    duplicates: list[str] = []

    for py_file in sorted(root.rglob("*.py")):
        text = py_file.read_text(encoding="utf-8")
        names = helper_pattern.findall(text)
        if not names:
            continue
        counts: Dict[str, int] = {}
        for name in names:
            counts[name] = counts.get(name, 0) + 1
        dup_names = [name for name, count in counts.items() if count > 1]
        if dup_names:
            rel_path = py_file.relative_to(root)
            duplicates.append(f"{rel_path}: {', '.join(sorted(dup_names))}")

    return duplicates


class TestSingleFileRegression(unittest.TestCase):
    """
    Regression tests for single-file refactorings.

    Compares current refactoring output against baseline expected output.
    """

    @classmethod
    def setUpClass(cls):
        """Set up test fixtures."""
        cls.engine = UnificationRefactorEngine(max_parameters=5, min_lines=3)
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

    def test_expected_output_helper_names_unique(self):
        """Ensure no fixed-point file defines the same helper name twice."""

        duplicates = find_duplicate_helpers(self.expected_output)
        if duplicates:
            formatted = "\n".join(f"  - {entry}" for entry in duplicates)
            self.fail(
                "Duplicate extracted helper names detected in expected output files:\n"
                f"{formatted}\n"
                "Regenerate baselines or audit the engine to ensure helper names remain unique."
            )

    def test_observational_equivalence_all_examples(self):
        """
        Test that all refactored code is observationally equivalent to original.

        This runs the comprehensive observational equivalence suite on all
        test examples. Any failures indicate a regression in refactoring quality.
        """
        results = self.tester.test_all_examples(str(self.test_examples), verbose=True)
        self.assertGreater(results["total_proposals_tested"], 0, "no proposal was tested")

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

        files_sorted = sorted(python_files)
        total_files = len(files_sorted)

        for idx, py_file in enumerate(files_sorted, 1):
            print(
                f"[Stability {idx}/{total_files}] Comparing {py_file.name}...", end=" ", flush=True
            )
            # Check if baseline exists for this file
            baseline_file = self.expected_output / py_file.name

            if not baseline_file.exists():
                # No baseline for this file (skip)
                print("no baseline", flush=True)
                continue

            # Compute current fixed-point refactoring output using a temp copy
            try:
                with tempfile.TemporaryDirectory(prefix="towel-regression-") as directory:
                    tmp_path = Path(directory) / py_file.name
                    tmp_path.write_text(py_file.read_text())
                    final_code, num_applied, _ = self.engine.refactor_to_fixed_point(str(tmp_path))
                    current_output = final_code
            except Exception as e:
                differences.append(f"{py_file.name}: Fixed-point refactoring failed: {e}")
                print("error", flush=True)
                continue

            # Read baseline
            baseline_output = baseline_file.read_text()

            # Normalize generated names for alpha-equivalence comparison
            # This allows comparison despite different numeric suffixes in
            # __extracted_func_<N> and __param_<N> names
            normalized_current = normalize_generated_names(current_output)
            normalized_baseline = normalize_generated_names(baseline_output)

            # A golden that differs from its input records an extraction; the
            # engine must still make one, or an engine that does nothing passes.
            baseline_changed = normalized_baseline != normalize_generated_names(py_file.read_text())
            if (num_applied > 0) != baseline_changed:
                differences.append(
                    f"{py_file.name}: applied {num_applied} refactoring(s) but the baseline "
                    f"{'differs from' if baseline_changed else 'equals'} the input"
                )
                print("count", flush=True)
                continue

            if normalized_current != normalized_baseline:
                differences.append(
                    f"{py_file.name}: Output differs from baseline (after normalization)\n"
                    f"  Baseline length: {len(baseline_output)} chars\n"
                    f"  Current length: {len(current_output)} chars"
                )
                print("differs", flush=True)
            else:
                print("ok", flush=True)

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
        cls.engine = UnificationRefactorEngine(max_parameters=5, min_lines=3)
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

    def test_crossfile_expected_output_helper_names_unique(self):
        """Ensure cross-file outputs do not reuse helper names in the same module."""

        duplicates = find_duplicate_helpers(self.expected_output)
        if duplicates:
            formatted = "\n".join(f"  - {entry}" for entry in duplicates)
            self.fail(
                "Duplicate extracted helper names detected in cross-file expected outputs:\n"
                f"{formatted}\n"
                "Regenerate baselines or audit the engine to ensure helper names remain unique."
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

        self.assertTrue(project_dirs, "no cross-file test projects found")

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

    def test_crossfile_output_stability(self):
        """Each project's fixed point matches its golden tree, up to generated names."""
        project_dirs = sorted(
            d
            for d in self.crossfile_examples.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        )
        self.assertTrue(project_dirs, "no cross-file test projects found")
        differences = []
        for project_dir in project_dirs:
            golden_dir = self.expected_output / project_dir.name
            self.assertTrue(golden_dir.is_dir(), f"no cross-file golden for {project_dir.name}")
            with tempfile.TemporaryDirectory(prefix="towel-regression-") as directory:
                out = Path(directory) / project_dir.name
                engine = UnificationRefactorEngine(max_parameters=5, min_lines=3)
                results, reason = engine.refactor_directory_to_fixed_point(
                    str(project_dir), str(out), progress="none"
                )
                self.assertEqual(reason, "fixed_point", project_dir.name)
                self.assertTrue(results, f"{project_dir.name}: nothing was refactored")
                current = {
                    p.relative_to(out): normalize_generated_names(p.read_text())
                    for p in sorted(out.rglob("*.py"))
                }
            golden = {
                p.relative_to(golden_dir): normalize_generated_names(p.read_text())
                for p in sorted(golden_dir.rglob("*.py"))
            }
            if current != golden:
                changed = sorted(
                    str(k)
                    for k in set(current) ^ set(golden)
                    | {k for k in current if k in golden and current[k] != golden[k]}
                )
                differences.append(f"{project_dir.name}: {', '.join(changed)}")
        if differences:
            self.fail(
                "Cross-file output changed:\n  "
                + "\n  ".join(differences)
                + "\nIf intentional: just regenerate-baseline"
            )


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
