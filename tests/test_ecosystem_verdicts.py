"""The release harness must distinguish test outcomes from incomplete commands."""

from __future__ import annotations

import concurrent.futures
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
from typing import Callable, Sequence

import pytest

from scripts import ecosystem_check as ecosystem


def _phase(path: Path, code: int, output: str) -> ecosystem.Phase:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(output)
    return ecosystem.Phase(code, 0.0, ecosystem.summarize(output), str(path))


@pytest.mark.parametrize(
    "verdict,expected",
    [
        ("PASS", 0),
        ("NO_CHANGE", 0),
        ("BROKEN_KNOWN", 0),
        ("SETUP_ERROR", 1),
        ("BASELINE_ERROR", 1),
        ("TYPE_BASELINE_ERROR", 1),
        ("AFTER_ERROR", 1),
        ("UNSUPPORTED", 1),
        ("PENDING", 1),
        ("FUTURE_UNKNOWN_VERDICT", 1),
        ("BROKEN", 1),
        ("CRASH", 1),
        ("TIMEOUT", 1),
        ("HARNESS_ERROR", 1),
        (None, 1),
    ],
)
def test_main_exit_status_requires_an_accepted_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verdict: str | None, expected: int
) -> None:
    project = ecosystem.Project("fixture", "unused", "pinned", "package.py")
    monkeypatch.setattr(ecosystem, "load_manifest", lambda *_: [project] if verdict else [])
    monkeypatch.setattr(
        ecosystem, "check_project", lambda *_: ecosystem.Result("fixture", verdict or "PENDING")
    )
    monkeypatch.setattr(ecosystem, "_lock_work_directory", lambda _: 0)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, stdout="", stderr=""),
    )

    def executor(max_workers: int, initializer: Callable[[], None]) -> ThreadPoolExecutor:
        # The worker only returns a fixture; no clone, install, or project code runs.
        return ThreadPoolExecutor(max_workers=max_workers)

    monkeypatch.setattr(concurrent.futures, "ProcessPoolExecutor", executor)
    monkeypatch.setattr(
        sys, "argv", ["ecosystem_check.py", "--run-untrusted-code", "--work", str(tmp_path)]
    )
    assert ecosystem.main() == expected


def test_unknown_project_selection_fails_before_any_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = ecosystem.Project("fixture", "unused", "pinned", "package.py")
    monkeypatch.setattr(ecosystem, "load_manifest", lambda *_: [project])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ecosystem_check.py",
            "--run-untrusted-code",
            "--work",
            str(tmp_path),
            "--only",
            "fixture",
            "misspelled-project",
        ],
    )
    with pytest.raises(SystemExit) as error:
        ecosystem.main()
    assert error.value.code == 2
    assert not (tmp_path / "report").exists()


def _check(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    before: tuple[int, str],
    after: tuple[int, str],
    *,
    changed: bool = True,
    known: bool = False,
    retest_agrees: bool = False,
    failure_exit_codes: tuple[int, ...] = (1,),
    test: tuple[str, ...] = tuple(ecosystem.DEFAULT_TEST),
    no_types: bool = False,
    refactor: tuple[int, str] = (0, "Applied 1 refactoring\n"),
) -> ecosystem.Result:
    source = root / "fixture"
    source.mkdir()
    (source / "package.py").write_text("value = 1\n")
    monkeypatch.setattr(ecosystem, "clone", lambda *_: "pinned")
    monkeypatch.setattr(ecosystem, "environment", lambda *_: Path(sys.executable))
    monkeypatch.setattr(ecosystem, "base_env", lambda *_: {})
    monkeypatch.setattr(ecosystem, "changed", lambda *_: (int(changed), "fixture"))
    phases = iter([before, after])
    refactor_calls = 0

    def run(
        command: Sequence[str], cwd: Path, env: dict[str, str], timeout: int, log: Path
    ) -> ecosystem.Phase:
        nonlocal refactor_calls
        if "towel.cli" in command:
            refactor_calls += 1
            assert (
                refactor_calls == 1
            ), "A failed transformation must not trigger an automatic retry"
            assert command.count("--no-types") == int(no_types)
            (root / "fixture-cleaned").write_text("value = 2\n" if changed else "value = 1\n")
            return _phase(log, *refactor)
        if ecosystem._pytest_arguments_start(command) is not None:
            assert command[-2:] == ["--verbosity=0", "-ra"]
        code, output = next(phases)
        return _phase(log, code, output)

    monkeypatch.setattr(ecosystem, "run", run)
    monkeypatch.setattr(ecosystem, "_retest_agrees", lambda *_: retest_agrees)
    project = ecosystem.Project(
        "fixture",
        "unused",
        "pinned",
        "package.py",
        expect_broken="known frame difference" if known else "",
        known_failures=("test_case.py::test_frame",) if known else (),
        failure_exit_codes=failure_exit_codes,
        test=test,
    )
    return ecosystem.check_project(project, root, root / "towel-src", 10, no_types)


@pytest.mark.parametrize(
    "code,output",
    [
        (0, "command ended without running tests\n"),
        (1, "ModuleNotFoundError: No module named pytest\n"),
        (0, ""),
        (5, "no tests ran in 0.01s\n"),
        (0, "Ran 0 tests in 0.000s\n\nOK\n"),
        (0, "Ran 4 tests in 0.000s\n"),
        (0, "OK\n"),
        (0, "0 passed in 0.01s\n"),
        (0, "4 deselected in 0.01s\n"),
        (1, "3 passed in 0.01s\n"),
        (0, "1 failed, 2 passed in 0.01s\n"),
        (1, "1 failed, 2 passed in 0.01s\n"),
        (1, "Ran 3 tests in 0.001s\nFAILED (failures=1)\n"),
        (2, "1 error in 0.01s\n"),
    ],
)
@pytest.mark.parametrize("changed", [False, True])
def test_incomplete_baseline_never_qualifies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int, output: str, changed: bool
) -> None:
    result = _check(tmp_path, monkeypatch, (code, output), (code, output), changed=changed)
    assert result.verdict == "BASELINE_ERROR"
    assert result.refactor is None and result.after is None
    assert result.baseline is not None and result.baseline.returncode == code


@pytest.mark.parametrize(
    "code,output",
    [
        (0, "3 passed in 0.01s\n"),
        (1, "FAILED test_case.py::test_frame - failure\n1 failed, 2 passed in 0.01s\n"),
        (0, "3 skipped in 0.01s\n"),
        (0, "3 xfailed in 0.01s\n"),
        (0, "Ran 3 tests in 0.001s\n\nOK\n"),
        (1, "FAIL: test_frame (tests.Case)\nRan 3 tests in 0.001s\nFAILED (failures=1)\n"),
        (0, "Ran 3 tests in 0.001s\n\nOK (skipped=3)\n"),
        (0, "Ran 2 tests in 0.001s\nOK\nRan 3 tests in 0.002s\nOK\n"),
    ],
)
@pytest.mark.parametrize("changed", [False, True])
def test_matching_real_test_outcomes_retain_existing_good_verdicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int, output: str, changed: bool
) -> None:
    result = _check(tmp_path, monkeypatch, (code, output), (code, output), changed=changed)
    assert result.verdict == ("PASS" if changed else "NO_CHANGE")
    assert result.baseline is not None and result.baseline.returncode == code


def test_known_failure_verdict_still_requires_a_complete_after_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = "FAILED test_case.py::test_frame - frame differs\n"
    result = _check(tmp_path, monkeypatch, (0, "3 passed in 0.01s\n"), (1, output), known=True)
    assert result.verdict == "AFTER_ERROR"


def test_complete_known_failure_retains_its_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = "FAILED test_case.py::test_frame - frame differs\n1 failed, 2 passed in 0.01s\n"
    result = _check(tmp_path, monkeypatch, (0, "3 passed in 0.01s\n"), (1, output), known=True)
    assert result.verdict == "BROKEN_KNOWN"


@pytest.mark.parametrize("retest_agrees", [False, True])
def test_known_or_flaky_failure_cannot_hide_two_missing_tests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, retest_agrees: bool
) -> None:
    output = "FAILED test_case.py::test_frame - frame differs\n1 failed in 0.01s\n"
    result = _check(
        tmp_path,
        monkeypatch,
        (0, "3 passed in 0.01s\n"),
        (1, output),
        known=True,
        retest_agrees=retest_agrees,
    )
    assert result.verdict == "BROKEN"
    assert "test count changed from 3 to 1" in result.detail


@pytest.mark.parametrize(
    "output,collected",
    [
        ("3 passed, 4 deselected, 5 subtests passed, 2 warnings in 0.01s\n", 3),
        ("Ran 2 tests in 0.001s\nOK\nRan 3 tests in 0.002s\nOK\n", 5),
    ],
)
def test_collected_count_ignores_non_test_tallies_and_sums_unittest_runs(
    tmp_path: Path, output: str, collected: int
) -> None:
    outcome = ecosystem._completed_test_run(_phase(tmp_path / "result.log", 0, output))
    assert outcome is not None
    assert outcome.collected == collected


def test_unittest_count_changes_are_not_identical_outcomes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _check(
        tmp_path,
        monkeypatch,
        (0, "Ran 4 tests in 0.001s\nOK\n"),
        (0, "Ran 2 tests in 0.001s\nOK\n"),
    )
    assert result.verdict == "BROKEN"


@pytest.mark.parametrize(
    "before,after",
    [
        (
            "FAILED test_case.py::test_one - wrong\n1 failed, 2 passed in 0.01s\n",
            "FAILED test_case.py::test_two - wrong\n1 failed, 2 passed in 0.01s\n",
        ),
        (
            "FAIL: test_one (tests.Case)\nRan 3 tests in 0.001s\nFAILED (failures=1)\n",
            "FAIL: test_two (tests.Case)\nRan 3 tests in 0.001s\nFAILED (failures=1)\n",
        ),
    ],
)
def test_equal_red_counts_with_different_test_identities_do_not_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, before: str, after: str
) -> None:
    result = _check(tmp_path, monkeypatch, (1, before), (1, after))
    assert result.verdict == "BROKEN"


def test_failure_identities_preserve_spaces_and_strip_ansi(tmp_path: Path) -> None:
    log = tmp_path / "failures.log"
    log.write_text(
        "\x1b[31mFAILED tests/test_case.py::test_case[value with spaces] - assertion\x1b[0m\n"
        "ERROR tests/test_case.py::test_setup[other value] - setup failure\n"
        "FAIL: test_case (tests.Case) (value='has spaces')\n"
        "ERROR: test_setup (tests.Case)\n"
        "UNEXPECTED SUCCESS: test_expected (tests.Case)\n"
        "FAILED (failures=1, errors=1, unexpected successes=1)\n"
    )
    assert ecosystem.failed_tests(str(log)) == {
        "tests/test_case.py::test_case[value with spaces]",
        "tests/test_case.py::test_setup[other value]",
        "unittest:test_case (tests.Case) (value='has spaces')",
        "unittest:test_setup (tests.Case)",
        "unittest:test_expected (tests.Case)",
    }


def test_unittest_differences_never_use_a_pytest_retest_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_run(*_: object) -> ecosystem.Phase:
        raise AssertionError("A unittest failure was sent to a pytest retest command")

    monkeypatch.setattr(ecosystem, "run", unexpected_run)
    assert not ecosystem._retest_agrees(
        [sys.executable, "-m", "unittest"],
        ["unittest:test_case (tests.Case)"],
        tmp_path,
        tmp_path,
        {},
        10,
        tmp_path,
        ecosystem.Project("fixture", "unused", "pinned", "package.py"),
    )


@pytest.mark.parametrize(
    "before,after,expected",
    [
        ((1, "No module named pytest\n"), (1, "No module named pytest\n"), False),
        ((2, "usage error\n"), (2, "usage error\n"), False),
        ((0, "1 passed in 0.01s\n"), (0, "1 passed in 0.03s\n"), True),
        ((0, "1 passed in 0.01s\n"), (0, "1 skipped in 0.01s\n"), False),
        (
            (0, "\x1b[32m=== 3 passed, 2 warnings in 0.01s ===\x1b[0m\n"),
            (0, "=== 3 passed, 5 warnings in 4.00s ===\n"),
            True,
        ),
        (
            (0, "3 passed, 5 subtests passed in 0.01s\n"),
            (0, "3 passed, 5 subtests passed in 0.03s\n"),
            True,
        ),
        (
            (1, "FAILED test_case.py::test_frame\n1 failed in 0.01s\n"),
            (1, "FAILED test_case.py::test_frame\n1 failed in 0.02s\n"),
            True,
        ),
    ],
)
def test_retest_requires_matching_recognized_outcomes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    before: tuple[int, str],
    after: tuple[int, str],
    expected: bool,
) -> None:
    phases = iter([before, after])

    def run(
        command: Sequence[str], cwd: Path, env: dict[str, str], timeout: int, log: Path
    ) -> ecosystem.Phase:
        assert command[-1] == "-ra", "Retests must request the same failure identities"
        code, output = next(phases)
        return _phase(log, code, output)

    monkeypatch.setattr(ecosystem, "run", run)
    project = ecosystem.Project("fixture", "unused", "pinned", "package.py")
    assert (
        ecosystem._retest_agrees(
            [sys.executable, "-m", "pytest", "-q"],
            ["test_case.py::test_frame"],
            tmp_path,
            tmp_path,
            {},
            10,
            tmp_path,
            project,
        )
        is expected
    )


@pytest.mark.parametrize("runner", ["pytest", "unittest"])
@pytest.mark.parametrize("failing", [False, True])
def test_recognition_matches_real_local_runner_output(
    tmp_path: Path, runner: str, failing: bool
) -> None:
    source = tmp_path / "test_cases.py"
    source.write_text(
        "import unittest\n\n"
        "class Cases(unittest.TestCase):\n"
        "    def test_pass(self):\n        self.assertTrue(True)\n"
        "    @unittest.skip('fixture')\n"
        "    def test_skip(self):\n        self.fail()\n"
        "    @unittest.expectedFailure\n"
        "    def test_expected(self):\n        self.fail()\n"
        f"    def test_result(self):\n        self.assertFalse({failing!r})\n"
    )
    arguments = (
        ["-m", "pytest", "-q", str(source)]
        if runner == "pytest"
        else ["-m", "unittest", "discover", "-q", "-s", str(tmp_path)]
    )
    phase = ecosystem.run(
        [sys.executable, *arguments],
        tmp_path,
        {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        20,
        tmp_path / "runner.log",
    )
    outcome = ecosystem._completed_test_run(phase)
    assert outcome is not None, Path(phase.log).read_text()
    assert outcome.returncode == phase.returncode == int(failing)
    assert outcome.summary == phase.summary
    assert outcome.collected == 4


@pytest.mark.parametrize(
    "command,expected",
    [
        (
            ["python", "-m", "pytest", "-q", "tests"],
            ["python", "-m", "pytest", "-q", "tests", "--verbosity=0", "-ra"],
        ),
        (
            ["/env/bin/pytest", "-q", "tests"],
            ["/env/bin/pytest", "-q", "tests", "--verbosity=0", "-ra"],
        ),
        (
            ["python3.13", "-m", "pytest", "-rs"],
            ["python3.13", "-m", "pytest", "-rs", "--verbosity=0", "-ra"],
        ),
        (
            ["python", "-m", "pytest", "-k", "selected", "-o", "addopts=", "--", "tests"],
            [
                "python",
                "-m",
                "pytest",
                "-k",
                "selected",
                "-o",
                "addopts=",
                "--verbosity=0",
                "-ra",
                "--",
                "tests",
            ],
        ),
        (["python", "-m", "unittest", "discover"], ["python", "-m", "unittest", "discover"]),
        (["python", "runtests.py"], ["python", "runtests.py"]),
        (["sh", "-c", "python -m pytest"], ["sh", "-c", "python -m pytest"]),
    ],
)
def test_test_command_preparation_preserves_runner_and_selection(
    command: list[str], expected: list[str]
) -> None:
    original = list(command)
    prepared = ecosystem._prepare_test_command(command)
    assert prepared == expected
    assert command == original
    assert ecosystem._prepare_test_command(prepared) == prepared


@pytest.mark.parametrize("selection", [["tests"], ["--", "tests"]])
def test_retest_retains_reporting_without_repeating_the_original_selection(
    selection: list[str],
) -> None:
    command = ecosystem._prepare_test_command(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *selection]
    )
    assert ecosystem._retest_command(command, ["tests/test_case.py::test_selected"]) == [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "tests/test_case.py::test_selected",
        "--verbosity=0",
        "-ra",
    ]


@pytest.mark.parametrize("reporting", ["-rs", "-rxXs"])
def test_project_reporting_defaults_cannot_hide_failed_test_identities(
    tmp_path: Path, reporting: str
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        f'[tool.pytest.ini_options]\naddopts = "{reporting}"\n'
    )
    source = tmp_path / "test_cases.py"
    source.write_text(
        "def test_fail():\n    assert False, 'fixture'\n\n" "def test_pass():\n    assert True\n"
    )
    command = [sys.executable, "-m", "pytest", "-q", str(source)]
    env = {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    hidden = ecosystem.run(command, tmp_path, env, 20, tmp_path / "hidden.log")
    assert hidden.returncode == 1
    assert ecosystem.failed_tests(hidden.log) == set()
    assert ecosystem._completed_test_run(hidden) is None
    prepared = ecosystem._prepare_test_command(command)
    visible = ecosystem.run(prepared, tmp_path, env, 20, tmp_path / "visible.log")
    assert visible.returncode == 1
    failed = ecosystem.failed_tests(visible.log)
    assert failed == {"test_cases.py::test_fail"}
    outcome = ecosystem._completed_test_run(visible)
    assert outcome is not None and outcome.collected == 2
    narrowed = ecosystem.run(
        ecosystem._retest_command(prepared, sorted(failed)),
        tmp_path,
        env,
        20,
        tmp_path / "retest.log",
    )
    retested = ecosystem._completed_test_run(narrowed)
    assert retested is not None and retested.collected == 1
    assert ecosystem.failed_tests(narrowed.log) == failed


def test_unrecognized_custom_runner_never_receives_pytest_retest_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_run(*_: object) -> ecosystem.Phase:
        raise AssertionError("A custom command was rewritten as a pytest command")

    monkeypatch.setattr(ecosystem, "run", unexpected_run)
    assert not ecosystem._retest_agrees(
        ["sh", "-c", "python -m pytest"],
        ["test_cases.py::test_fail"],
        tmp_path,
        tmp_path,
        {},
        10,
        tmp_path,
        ecosystem.Project("fixture", "unused", "pinned", "package.py"),
    )


@pytest.mark.parametrize("codes", [(), (0,), (-9,), (256,)])
def test_project_failure_exit_contract_requires_positive_process_codes(
    codes: tuple[int, ...],
) -> None:
    with pytest.raises(ValueError, match="failure_exit_codes"):
        ecosystem.Project("fixture", "unused", "pinned", "package.py", failure_exit_codes=codes)


@pytest.mark.parametrize(
    "code,output,recognized",
    [
        (2, "ERROR: test_error (tests.Case)\nRan 3 tests in 0.01s\nFAILED (errors=1)\n", True),
        (2, "Ran 3 tests in 0.01s\nOK\n", False),
        (2, "3 passed in 0.01s\n", False),
        (2, "Ran 3 tests in 0.01s\nFAILED (errors=1)\n", False),
        (2, "ERROR: test_error (tests.Case)\n", False),
        (0, "ERROR: test_error (tests.Case)\nRan 3 tests in 0.01s\nFAILED (errors=1)\n", False),
        (-9, "ERROR: test_error (tests.Case)\nRan 3 tests in 0.01s\nFAILED (errors=1)\n", False),
    ],
)
def test_custom_failure_status_still_requires_a_completed_identified_failure(
    tmp_path: Path, code: int, output: str, recognized: bool
) -> None:
    phase = _phase(tmp_path / "result.log", code, output)
    assert ecosystem._completed_test_run(phase) is None
    outcome = ecosystem._completed_test_run(phase, (1, 2))
    assert (outcome is not None) is recognized
    if outcome is not None:
        assert outcome.returncode == 2 and outcome.collected == 3


@pytest.mark.parametrize(
    "before,after,codes,expected",
    [
        (2, 2, (1,), "BASELINE_ERROR"),
        (2, 2, (1, 2), "PASS"),
        (1, 2, (1, 2), "BROKEN"),
        (1, 2, (1,), "AFTER_ERROR"),
    ],
)
def test_project_failure_exit_policy_reaches_both_suite_comparisons(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    before: int,
    after: int,
    codes: tuple[int, ...],
    expected: str,
) -> None:
    output = "ERROR: test_error (tests.Case)\nRan 3 tests in 0.01s\nFAILED (errors=1)\n"
    result = _check(
        tmp_path,
        monkeypatch,
        (before, output),
        (after, output),
        failure_exit_codes=codes,
        test=("{python}", "runtests.py"),
    )
    assert result.verdict == expected


def test_only_peewee_declares_the_custom_failure_exit_contract() -> None:
    projects = ecosystem.load_manifest(ecosystem.REPO / "scripts/ecosystem/manifest.toml", [])
    assert len(projects) == 141
    assert [
        (project.name, project.failure_exit_codes)
        for project in projects
        if project.failure_exit_codes != (1,)
    ] == [("peewee", (1, 2))]


def test_actual_custom_unittest_runner_failure_status_requires_its_contract(tmp_path: Path) -> None:
    runner = tmp_path / "runtests.py"
    runner.write_text(
        "import sys\nimport unittest\n\n"
        "class Cases(unittest.TestCase):\n"
        "    def test_pass(self):\n        self.assertTrue(True)\n"
        "    def test_failure(self):\n        self.fail('fixture')\n"
        "    def test_error(self):\n        raise ValueError('fixture')\n"
        "    @unittest.skip('fixture')\n"
        "    def test_skip(self):\n        self.fail()\n"
        "result = unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(Cases))\n"
        "sys.exit(2 if result.errors else 1 if result.failures else 0)\n"
    )
    phase = ecosystem.run([sys.executable, str(runner)], tmp_path, {}, 20, tmp_path / "run.log")
    assert phase.returncode == 2
    assert ecosystem._completed_test_run(phase) is None
    outcome = ecosystem._completed_test_run(phase, (1, 2))
    assert outcome is not None and outcome.returncode == 2 and outcome.collected == 4
    assert len(ecosystem.failed_tests(phase.log)) == 2


def test_pytest_collection_error_remains_incomplete_under_the_default_contract(
    tmp_path: Path,
) -> None:
    (tmp_path / "test_broken.py").write_text("raise RuntimeError('collection fixture')\n")
    phase = ecosystem.run(
        ecosystem._prepare_test_command([sys.executable, "-m", "pytest", "-q"]),
        tmp_path,
        {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        20,
        tmp_path / "result.log",
    )
    assert phase.returncode == 2 and "1 error" in phase.summary
    assert ecosystem.failed_tests(phase.log)
    assert ecosystem._completed_test_run(phase) is None


@pytest.mark.parametrize("codes,expected", [((1,), False), ((1, 2), True)])
def test_retests_apply_the_contract_and_persist_independently_checkable_statuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codes: tuple[int, ...], expected: bool
) -> None:
    def run(
        command: Sequence[str], cwd: Path, env: dict[str, str], timeout: int, log: Path
    ) -> ecosystem.Phase:
        return _phase(log, 2, "ERROR test_case.py::test_error\n1 error in 0.01s\n")

    monkeypatch.setattr(ecosystem, "run", run)
    project = ecosystem.Project(
        "fixture", "unused", "pinned", "package.py", failure_exit_codes=codes
    )
    assert (
        ecosystem._retest_agrees(
            [sys.executable, "-m", "pytest", "-q"],
            ["test_case.py::test_error"],
            tmp_path,
            tmp_path,
            {},
            10,
            tmp_path,
            project,
        )
        is expected
    )
    evidence = json.loads((tmp_path / "fixture-retest.json").read_text())
    assert evidence["before"]["returncode"] == evidence["after"]["returncode"] == 2
    assert evidence["failure_exit_codes"] == list(codes)
    assert evidence["test_ids"] == ["test_case.py::test_error"]
    assert evidence["command"] == [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "test_case.py::test_error",
        "--verbosity=0",
        "-ra",
    ]
    for side in ["before", "after"]:
        phase = ecosystem.Phase(**evidence[side])
        outcome = ecosystem._completed_test_run(phase, tuple(evidence["failure_exit_codes"]))
        assert (outcome is not None) is expected


@pytest.mark.parametrize("reporting", ["-q", "-qq"])
@pytest.mark.parametrize("failing", [False, True])
def test_reporting_restores_quiet_project_tallies_without_changing_selection(
    tmp_path: Path, reporting: str, failing: bool
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        f'[tool.pytest.ini_options]\naddopts = "{reporting} -k selected"\n'
    )
    (tmp_path / "test_cases.py").write_text(
        f"def test_selected():\n    assert {not failing!r}\n\n"
        "def test_other():\n    assert False, 'must stay deselected'\n"
    )
    command = [sys.executable, "-m", "pytest", "-q"]
    env = {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    hidden = ecosystem.run(command, tmp_path, env, 20, tmp_path / "hidden.log")
    assert ecosystem._completed_test_run(hidden) is None
    prepared = ecosystem._prepare_test_command(command)
    visible = ecosystem.run(prepared, tmp_path, env, 20, tmp_path / "visible.log")
    outcome = ecosystem._completed_test_run(visible)
    assert outcome is not None and outcome.collected == 1
    assert outcome.returncode == int(failing)
    assert "1 deselected" in outcome.summary
    assert ecosystem.failed_tests(visible.log) == (
        {"test_cases.py::test_selected"} if failing else set()
    )


@pytest.mark.parametrize("configuration", [True, False])
def test_no_summary_cannot_certify_a_failure_without_identities(
    tmp_path: Path, configuration: bool
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\naddopts = "' + ("--no-summary" if configuration else "") + '"\n'
    )
    (tmp_path / "test_cases.py").write_text("def test_fail():\n    assert False, 'fixture'\n")
    command = [sys.executable, "-m", "pytest", "-q", *([] if configuration else ["--no-summary"])]
    phase = ecosystem.run(
        ecosystem._prepare_test_command(command),
        tmp_path,
        {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        20,
        tmp_path / "result.log",
    )
    assert phase.returncode == 1 and "1 failed" in phase.summary
    assert ecosystem.failed_tests(phase.log) == set()
    assert ecosystem._completed_test_run(phase) is None


@pytest.mark.parametrize("no_types", [False, True])
@pytest.mark.parametrize("complete_baseline", [False, True])
def test_project_typing_mode_is_explicit_and_recorded_even_before_refactoring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_types: bool, complete_baseline: bool
) -> None:
    before = (0, "3 passed in 0.01s\n") if complete_baseline else (2, "unrecognized\n")
    result = _check(tmp_path, monkeypatch, before, before, no_types=no_types)
    assert result.verdict == ("PASS" if complete_baseline else "BASELINE_ERROR")
    assert result.typing_mode == ("no-types" if no_types else "default")


@pytest.mark.parametrize("no_types", [False, True])
@pytest.mark.parametrize("worker_fails", [False, True])
def test_main_forwards_and_records_typing_mode_in_every_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_types: bool, worker_fails: bool
) -> None:
    project = ecosystem.Project("fixture", "unused", "pinned", "package.py")
    monkeypatch.setattr(ecosystem, "load_manifest", lambda *_: [project])
    monkeypatch.setattr(ecosystem, "_lock_work_directory", lambda _: 0)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, stdout="", stderr=""),
    )

    def check_project(
        project: ecosystem.Project,
        work: Path,
        source: Path,
        timeout: int,
        requested_no_types: bool = False,
    ) -> ecosystem.Result:
        assert requested_no_types is no_types
        if worker_fails:
            raise RuntimeError("fixture worker failed")
        return ecosystem.Result(
            "fixture", "PASS", typing_mode="no-types" if no_types else "default"
        )

    def executor(max_workers: int, initializer: Callable[[], None]) -> ThreadPoolExecutor:
        return ThreadPoolExecutor(max_workers=max_workers)

    monkeypatch.setattr(ecosystem, "check_project", check_project)
    monkeypatch.setattr(concurrent.futures, "ProcessPoolExecutor", executor)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ecosystem_check.py",
            "--run-untrusted-code",
            "--work",
            str(tmp_path),
            *(["--no-types"] if no_types else []),
        ],
    )
    assert ecosystem.main() == int(worker_fails)
    mode = "no-types" if no_types else "default"
    result = json.loads((tmp_path / "report/fixture.json").read_text())
    summary = json.loads((tmp_path / "report/summary.json").read_text())
    assert result["typing_mode"] == summary["typing_mode"] == mode
    assert summary["results"][0]["typing_mode"] == mode
    assert f"Typing mode: `{mode}`" in (tmp_path / "report/summary.md").read_text()


@pytest.mark.parametrize(
    "diagnostic,expected",
    [
        (
            "Error: Original project check reported 2 type error(s):\n"
            "  module.py: incompatible type\nFix the existing errors or rerun with --no-types\n",
            "TYPE_BASELINE_ERROR",
        ),
        ("Error: Original project type check failed: timed out\n", "CRASH"),
        ("Traceback (most recent call last):\nRuntimeError: fixture\n", "CRASH"),
        ("Error: Unsupported build backend fixture\n", "UNSUPPORTED"),
    ],
)
def test_type_baseline_refusal_is_distinct_and_never_retried_without_types(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, diagnostic: str, expected: str
) -> None:
    result = _check(
        tmp_path,
        monkeypatch,
        (0, "3 passed in 0.01s\n"),
        (0, "3 passed in 0.01s\n"),
        refactor=(1, diagnostic),
    )
    assert result.verdict == expected and result.typing_mode == "default"
    assert result.after is None
    assert result.refactor is not None and result.refactor.returncode == 1
