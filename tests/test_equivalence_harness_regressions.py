"""Regression tests for the observational equivalence measurement itself."""

import sys
import tempfile
import types
import unittest
from pathlib import Path

from tests.automatic_equivalence_tester import test_all_refactored_functions as check_proposal
from tests.crossfile_equivalence_tester import CrossFileEquivalenceTester
from tests.test_observational_equivalence import compare_function_behavior
from towel.unification.refactor_engine import UnificationRefactorEngine


class TestExecutionObservations(unittest.TestCase):
    def test_stdout_and_stderr_differences_are_observable(self) -> None:
        for stream in ("stdout", "stderr"):
            with self.subTest(stream=stream):
                original = f'import sys\ndef f():\n print("before", file=sys.{stream})\n'
                changed = original.replace('"before"', '"after"')
                passed, differences = compare_function_behavior(original, changed, "f", [((), {})])
                self.assertFalse(passed)
                self.assertTrue(differences)

    def test_output_before_same_exception_is_observable(self) -> None:
        original = 'def f():\n print("before")\n raise ValueError("same")\n'
        changed = original.replace('"before"', '"after"')
        self.assertFalse(compare_function_behavior(original, changed, "f", [((), {})])[0])

    def test_module_initialization_output_is_observable(self) -> None:
        original = 'print("before")\ndef f():\n return None\n'
        self.assertFalse(
            compare_function_behavior(
                original, original.replace("before", "after"), "f", [((), {})]
            )[0]
        )

    def test_positional_and_keyword_mutations_are_observable(self) -> None:
        original = "def f(values):\n values.append(1)\n"
        changed = original.replace("append(1)", "append(2)")
        cases: tuple[tuple[tuple[object, ...], dict[str, object]], ...] = (
            (([],), {}),
            ((), {"values": []}),
        )
        for case in cases:
            with self.subTest(case=case):
                self.assertFalse(compare_function_behavior(original, changed, "f", [case])[0])

    def test_equal_mutations_pass_without_changing_test_inputs(self) -> None:
        source = "def f(values):\n values.append(1)\n print(values)\n return values\n"
        values: list[int] = []
        self.assertTrue(compare_function_behavior(source, source, "f", [((values,), {})])[0])
        self.assertEqual(values, [])

    def test_argument_aliasing_is_preserved_between_args_and_kwargs(self) -> None:
        original = "def f(a, b):\n return a is b\n"
        changed = "def f(a, b):\n return False\n"
        shared: list[int] = []
        self.assertFalse(
            compare_function_behavior(original, changed, "f", [((shared,), {"b": shared})])[0]
        )

    def test_scalar_return_types_are_observable_in_nested_values(self) -> None:
        for original_value, changed_value in (
            ("1", "True"),
            ("[1]", "[True]"),
            ('{"v": 1}', '{"v": True}'),
        ):
            with self.subTest(value=original_value):
                original = f"def f():\n return {original_value}\n"
                changed = f"def f():\n return {changed_value}\n"
                self.assertFalse(compare_function_behavior(original, changed, "f", [((), {})])[0])

    def test_no_test_cases_is_not_success(self) -> None:
        source = "def f():\n return 1\n"
        self.assertFalse(compare_function_behavior(source, source, "f", [])[0])

    def test_unrecognized_proposal_is_not_success(self) -> None:
        source = "def f():\n return 1\n"
        passed, differences = check_proposal(source, source, "unrecognized proposal")
        self.assertFalse(passed)
        self.assertIn("No functions identified", differences[0])


class TestCrossFileImportIsolation(unittest.TestCase):
    def compare_projects(
        self, original: Path, changed: Path, entry: str = "entry.py"
    ) -> tuple[bool, list[str]]:
        tester = CrossFileEquivalenceTester(UnificationRefactorEngine())
        return tester._compare_project_behavior(original, changed, {str(original / entry): ["f"]})

    def test_imported_and_dynamic_dependencies_are_measured_independently(self) -> None:
        for dynamic in (False, True):
            with self.subTest(dynamic=dynamic), tempfile.TemporaryDirectory() as temporary:
                original, changed = Path(temporary) / "original", Path(temporary) / "changed"
                for root, value in ((original, 1), (changed, 2)):
                    root.mkdir()
                    (root / "audit_dependency.py").write_text(f"def value():\n return {value}\n")
                    source = (
                        "def f():\n from audit_dependency import value\n return value()\n"
                        if dynamic
                        else "from audit_dependency import value\ndef f():\n return value()\n"
                    )
                    (root / "entry.py").write_text(source)
                self.assertFalse(self.compare_projects(original, changed)[0])

    def test_relative_imports_and_preexisting_modules_are_isolated_and_restored(self) -> None:
        saved_path = sys.path[:]
        sentinel = types.ModuleType("audit_package")
        previous = sys.modules.get("audit_package")
        sys.modules["audit_package"] = sentinel
        try:
            with tempfile.TemporaryDirectory() as temporary:
                original, changed = Path(temporary) / "original", Path(temporary) / "changed"
                for root in (original, changed):
                    package = root / "audit_package"
                    package.mkdir(parents=True)
                    (package / "__init__.py").write_text("")
                    (package / "dependency.py").write_text("def value():\n return 7\n")
                    (package / "entry.py").write_text(
                        "from .dependency import value\ndef f():\n return value()\n"
                    )
                self.assertTrue(
                    self.compare_projects(original, changed, "audit_package/entry.py")[0]
                )
                self.assertIs(sys.modules["audit_package"], sentinel)
                self.assertEqual(sys.path, saved_path)
                self.assertNotIn("audit_package.entry", sys.modules)
        finally:
            if previous is None:
                sys.modules.pop("audit_package", None)
            else:
                sys.modules["audit_package"] = previous

    def test_returned_closures_fail_explicitly_until_isolation_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            original, changed = Path(temporary) / "original", Path(temporary) / "changed"
            for root in (original, changed):
                root.mkdir()
                (root / "entry.py").write_text("def f():\n return lambda: 1\n")
            passed, differences = self.compare_projects(original, changed)
            self.assertFalse(passed)
            self.assertIn("persistent import isolation", differences[0])

    def test_matching_import_errors_do_not_count_as_executed_functions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            original, changed = Path(temporary) / "original", Path(temporary) / "changed"
            for root in (original, changed):
                root.mkdir()
                (root / "entry.py").write_text(
                    "import nonexistent_towel_audit_dependency\ndef f():\n return 1\n"
                )
            passed, differences = self.compare_projects(original, changed)
            self.assertFalse(passed)
            self.assertIn("Could not load", differences[0])

    def test_empty_function_selection_is_not_success(self) -> None:
        tester = CrossFileEquivalenceTester(UnificationRefactorEngine())
        self.assertFalse(tester._compare_project_behavior(Path("."), Path("."), {})[0])
