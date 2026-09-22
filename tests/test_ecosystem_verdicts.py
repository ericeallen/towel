"""The release harness must distinguish test outcomes from incomplete commands."""

from __future__ import annotations

import concurrent.futures
from concurrent.futures import ThreadPoolExecutor
import json
import os
import time
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from typing import Callable, Optional, Sequence

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
        ("REFUSAL_MALFORMED", 1),
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
    refactor_retry: Optional[tuple[int, str]] = None,
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
            outcome = refactor
            if refactor_calls == 1:
                assert command.count("--no-types") == int(no_types)
            else:
                # The only retry there is: a project Towel declined to verify,
                # rerun on the untyped path so its behaviour is still covered.
                assert refactor_retry is not None, "an unexpected second attempt"
                assert refactor_calls == 2, "at most one retry"
                assert command.count("--no-types") == 1
                outcome = refactor_retry
            # Write where the command says, so the layout stays the harness's
            # business and this stub cannot drift from it.
            cleaned = Path(command[command.index("dry") + 2])
            cleaned.parent.mkdir(parents=True, exist_ok=True)
            cleaned.write_text("value = 2\n" if changed else "value = 1\n")
            return _phase(log, *outcome)
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
        _phase(tmp_path / "initial-before.log", 0, "1 passed in 0.01s\n"),
        _phase(tmp_path / "initial-after.log", 0, "1 passed in 0.01s\n"),
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
            False,
        ),
        (
            (0, "3 passed, 5 subtests passed in 0.01s\n"),
            (0, "3 passed, 5 subtests passed in 0.03s\n"),
            False,
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
    phases = iter([before, after, before, after])

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
            _phase(tmp_path / "initial-before.log", 0, "1 passed in 0.01s\n"),
            _phase(tmp_path / "initial-after.log", 0, "1 passed in 0.01s\n"),
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
    expected = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
    ]
    if "--" in selection:
        expected += ["--verbosity=0", "-ra", "--", "tests/test_case.py::test_selected"]
    else:
        expected += ["tests/test_case.py::test_selected", "--verbosity=0", "-ra"]
    assert ecosystem._retest_command(command, ["tests/test_case.py::test_selected"]) == expected


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
    retest = ecosystem._retest_command(prepared, sorted(failed))
    assert retest is not None
    narrowed = ecosystem.run(
        retest,
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
        _phase(tmp_path / "initial-before.log", 0, "1 passed in 0.01s\n"),
        _phase(tmp_path / "initial-after.log", 0, "1 passed in 0.01s\n"),
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
            _phase(tmp_path / "initial-before.log", 0, "1 passed in 0.01s\n"),
            _phase(tmp_path / "initial-after.log", 0, "1 passed in 0.01s\n"),
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
    assert f"Typing mode requested: `{mode}`" in (tmp_path / "report/summary.md").read_text()


PRE_EXISTING_ERRORS = (
    "Error: Original project check reported 2 type error(s):\n"
    "  module.py: incompatible type\n"
    "Fix the existing errors or rerun with --no-types "
    "(library: type_oracle=None, annotate_helpers=False).\n"
)
CHECKER_FAILED = (
    "Error: Original project type check failed: timed out\n"
    "rerun with --no-types (library: type_oracle=None, annotate_helpers=False).\n"
)


@pytest.mark.parametrize(
    "diagnostic,expected",
    [
        ("Traceback (most recent call last):\nRuntimeError: fixture\n", "CRASH"),
        ("Error: Unsupported build backend fixture\n", "UNSUPPORTED"),
    ],
)
def test_a_failed_transformation_is_never_retried_without_types(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, diagnostic: str, expected: str
) -> None:
    """Only a refusal to verify earns a retry; a crash is a crash on either path."""
    result = _check(
        tmp_path,
        monkeypatch,
        (0, "3 passed in 0.01s\n"),
        (0, "3 passed in 0.01s\n"),
        refactor=(1, diagnostic),
    )
    assert result.verdict == expected and result.typing_mode == "default"
    assert result.fallback == "" and result.after is None
    assert result.refactor is not None and result.refactor.returncode == 1


@pytest.mark.parametrize(
    "diagnostic,kind",
    [(PRE_EXISTING_ERRORS, "pre-existing-errors"), (CHECKER_FAILED, "checker-failed")],
)
def test_a_project_towel_declines_to_verify_is_rerun_without_types(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, diagnostic: str, kind: str
) -> None:
    """The verdict then comes from the untyped path, and the row says so."""
    result = _check(
        tmp_path,
        monkeypatch,
        (0, "3 passed in 0.01s\n"),
        (0, "3 passed in 0.01s\n"),
        refactor=(1, diagnostic),
        refactor_retry=(0, "Applied 1 refactoring\n"),
    )
    assert result.verdict == "PASS"
    assert result.fallback == kind and result.typing_mode == "no-types"
    assert result.after is not None


def test_a_rerun_without_types_that_still_fails_keeps_its_own_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _check(
        tmp_path,
        monkeypatch,
        (0, "3 passed in 0.01s\n"),
        (0, "3 passed in 0.01s\n"),
        refactor=(1, PRE_EXISTING_ERRORS),
        refactor_retry=(1, "Traceback (most recent call last):\nRuntimeError: fixture\n"),
    )
    assert result.verdict == "CRASH" and result.fallback == "pre-existing-errors"


@pytest.mark.parametrize(
    "diagnostic,missing",
    [
        (
            "Error: Original project check reported 2 type error(s):\n"
            "  module.py: incompatible type\n",
            "way forward",
        ),
        (
            "Error: Original project check reported 2 type error(s):\n" "rerun with --no-types\n",
            "showed no diagnostic",
        ),
        ("Error: Original project type check failed: timed out\n", "way forward"),
    ],
)
def test_a_refusal_that_does_not_say_what_to_do_fails_the_corpus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, diagnostic: str, missing: str
) -> None:
    """The refusal is all such a user ever sees, so the corpus is where it is tested.

    Lark and Voluptuous, whose mypy configs name a Python version mypy 1.19 has
    dropped, are the reason: the refusal they produced named no way out.
    """
    result = _check(
        tmp_path,
        monkeypatch,
        (0, "3 passed in 0.01s\n"),
        (0, "3 passed in 0.01s\n"),
        refactor=(1, diagnostic),
    )
    assert result.verdict == "REFUSAL_MALFORMED"
    assert missing in result.detail


def _git_fixture(root: Path) -> Path:
    source = root / "repository"
    (source / "package").mkdir(parents=True)
    (source / "package/original.py").write_text("value = 1\n")
    for arguments in [
        ["init", "-q"],
        ["add", "package"],
        [
            "-c",
            "user.name=Audit Fixture",
            "-c",
            "user.email=audit@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
    ]:
        subprocess.run(
            ["git", "-C", str(source), *arguments], check=True, capture_output=True, timeout=20
        )
    return source


def _fail_git_command(root: Path, monkeypatch: pytest.MonkeyPatch, command: str) -> None:
    actual = shutil.which("git")
    assert actual is not None
    binaries = root / "bin"
    binaries.mkdir()
    wrapper = binaries / "git"
    wrapper.write_text(
        '#!/bin/sh\nfor argument in "$@"; do\n'
        f'  if [ "$argument" = {shlex.quote(command)} ]; then exit 73; fi\n'
        "done\n"
        f'exec {shlex.quote(actual)} "$@"\n'
    )
    wrapper.chmod(0o700)
    monkeypatch.setenv("PATH", str(binaries) + os.pathsep + os.environ["PATH"])


@pytest.mark.parametrize("command", ["status", "diff"])
def test_changed_never_interprets_git_failure_as_zero_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    source = _git_fixture(tmp_path)
    (source / "package/original.py").write_text("value = 2\n")
    _fail_git_command(tmp_path, monkeypatch, command)
    with pytest.raises(subprocess.CalledProcessError) as failure:
        ecosystem.changed(source, "package")
    assert failure.value.returncode == 73


@pytest.mark.parametrize(
    "operation,expected", [("modify", 1), ("new", 1), ("delete", 1), ("rename", 2), ("type", 1)]
)
def test_changed_counts_every_affected_path_including_newlines(
    tmp_path: Path, operation: str, expected: int
) -> None:
    source = _git_fixture(tmp_path)
    original = source / "package/original.py"
    if operation == "modify":
        original.write_text("value = 2\n")
    elif operation == "new":
        nested = source / "package/new directory"
        nested.mkdir()
        (nested / "new\nhelper.py").write_text("helper = 3\n")
    elif operation == "delete":
        original.unlink()
    elif operation == "rename":
        original.rename(source / "package/new\nname.py")
    else:
        original.unlink()
        original.symlink_to("target.py")
    assert ecosystem.changed(source, "package")[0] == expected


@pytest.mark.parametrize("command", ["rev-parse", "status"])
def test_checkout_revision_and_cleanliness_require_successful_git_observations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    source = _git_fixture(tmp_path)
    _fail_git_command(tmp_path, monkeypatch, command)
    with pytest.raises(subprocess.CalledProcessError) as failure:
        ecosystem.source_revision(source / "package")
    assert failure.value.returncode == 73


def test_source_archive_revision_is_explicitly_unavailable(tmp_path: Path) -> None:
    assert ecosystem.source_revision(tmp_path) == (None, None)


def test_revision_describes_the_requested_source_checkout(tmp_path: Path) -> None:
    source = _git_fixture(tmp_path)
    (source / "package/original.py").write_text("value = 2\n")
    commit, dirty = ecosystem.source_revision(source / "package")
    expected = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True, timeout=20
    ).strip()
    assert commit == expected
    assert dirty and "package/original.py" in dirty


@pytest.mark.parametrize(
    "arguments,options",
    [
        (["tests", "-q", "other_tests"], ["-q"]),
        (["tests", "-q", "-c", "config.ini", "other_tests"], ["-q", "-c", "config.ini"]),
        (
            ["tests", "-k", "selected and not slow", "-o", "addopts=-q"],
            ["-k", "selected and not slow", "-o", "addopts=-q"],
        ),
        (
            ["tests", "--maxfail=1", "--plugin-value=setting"],
            ["--maxfail=1", "--plugin-value=setting"],
        ),
        (["tests", "-q", "--", "-literal.py"], ["-q", "--"]),
    ],
)
def test_retest_removes_selectors_anywhere_and_preserves_unambiguous_option_values(
    arguments: list[str], options: list[str]
) -> None:
    prefix = [sys.executable, "-m", "pytest"]
    selected = ["tests/test_cases.py::test_selected"]
    expected = ecosystem._prepare_test_command([*prefix, *options, *selected])
    assert ecosystem._retest_command([*prefix, *arguments], selected) == expected


@pytest.mark.parametrize(
    "arguments",
    [["tests", "--plugin-option", "setting"], ["tests", "--unknown-flag"], ["tests", "-c"]],
)
def test_retest_declines_ambiguous_options(arguments: list[str]) -> None:
    assert (
        ecosystem._retest_command(["pytest", *arguments], ["test_cases.py::test_selected"]) is None
    )


def test_actual_retest_does_not_run_the_original_selector_before_an_option(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_cases.py").write_text(
        "def test_selected():\n    assert True\n\n"
        "def test_unrelated():\n    assert False, 'broad selector retained'\n"
    )
    command = ecosystem._retest_command(
        [sys.executable, "-m", "pytest", "tests", "-q"],
        ["tests/test_cases.py::test_selected"],
    )
    assert command is not None
    phase = ecosystem.run(
        command, tmp_path, {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}, 20, tmp_path / "retest.log"
    )
    outcome = ecosystem._completed_test_run(phase)
    assert outcome is not None and outcome.returncode == 0 and outcome.collected == 1
    assert "test_unrelated" not in Path(phase.log).read_text()


@pytest.mark.parametrize("regression", [False, True])
def test_actual_order_dependent_regression_cannot_hide_in_isolated_retests(
    tmp_path: Path, regression: bool
) -> None:
    source, ready, logs = (tmp_path / name for name in ("before", "after", "logs"))
    common = "armed = False\n\ndef prepare():\n    global armed\n    armed = True\n\n"
    tests = (
        "import stateful\n\n"
        "def test_prepare():\n    stateful.prepare()\n    assert stateful.armed\n\n"
        "def test_result():\n    assert stateful.result() == 42\n"
    )
    for tree in (source, ready, logs):
        tree.mkdir()
    for tree in (source, ready):
        body = "return 0 if armed else 42" if regression and tree == ready else "return 42"
        (tree / "stateful.py").write_text(common + f"def result():\n    {body}\n")
        (tree / "test_state.py").write_text(tests)
    env = {"PYTHONPATH": ".", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    command = ecosystem._prepare_test_command(
        [sys.executable, "-m", "pytest", "test_state.py", "-q"]
    )
    initial_before = ecosystem.run(command, source, env, 20, logs / "initial-before.log")
    initial_after = ecosystem.run(command, ready, env, 20, logs / "initial-after.log")
    assert initial_before.returncode == 0 and initial_after.returncode == int(regression)
    assert (
        ecosystem._retest_agrees(
            command,
            ["test_state.py::test_result"],
            source,
            ready,
            env,
            20,
            logs,
            ecosystem.Project("stateful", "unused", "pinned", "stateful.py"),
            initial_before,
            initial_after,
        )
        is not regression
    )
    evidence = json.loads((logs / "stateful-retest.json").read_text())
    assert evidence["before"]["returncode"] == evidence["after"]["returncode"] == 0
    assert evidence["before"]["summary"] == evidence["after"]["summary"] == "1 passed"
    full = evidence["full"]
    assert full["command"] == command
    assert full["initial_before"]["log"] == initial_before.log
    assert full["initial_after"]["log"] == initial_after.log
    assert full["before"]["returncode"] == 0
    assert full["after"]["returncode"] == int(regression)
    for side in ("before", "after"):
        outcome = ecosystem._completed_test_run(ecosystem.Phase(**full[side]))
        assert outcome is not None and outcome.collected == 2


@pytest.mark.parametrize(
    "before,after,expected",
    [
        ((0, "2 passed in 0.01s\n"), (0, "2 passed in 0.01s\n"), True),
        (
            (0, "2 passed in 0.01s\n"),
            (1, "FAILED test_case.py::test_a\n1 failed, 1 passed in 0.01s\n"),
            False,
        ),
        (
            (1, "FAILED test_case.py::test_a\n1 failed, 1 passed in 0.01s\n"),
            (1, "FAILED test_case.py::test_b\n1 failed, 1 passed in 0.01s\n"),
            False,
        ),
        ((0, "1 passed in 0.01s\n"), (0, "1 passed in 0.01s\n"), False),
        ((0, "3 passed in 0.01s\n"), (0, "3 passed in 0.01s\n"), False),
        ((1, "2 passed in 0.01s\n"), (1, "2 passed in 0.01s\n"), False),
        ((1, "internal error\n"), (1, "internal error\n"), False),
        ((-9, "TIMEOUT\n"), (0, "2 passed in 0.01s\n"), False),
        (
            (1, "FAILED test_case.py::test_a\n1 failed, 1 passed in 0.01s\n"),
            (1, "FAILED test_case.py::test_a\n1 failed, 1 passed in 0.01s\n"),
            True,
        ),
    ],
)
def test_full_context_confirmation_requires_complete_equal_counted_outcomes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    before: tuple[int, str],
    after: tuple[int, str],
    expected: bool,
) -> None:
    phases = iter([(0, "1 passed in 0.01s\n"), (0, "1 passed in 0.01s\n"), before, after])
    command = ecosystem._prepare_test_command([sys.executable, "-m", "pytest", "tests", "-q"])
    calls: list[Sequence[str]] = []

    def run(
        arguments: Sequence[str], cwd: Path, env: dict[str, str], timeout: int, log: Path
    ) -> ecosystem.Phase:
        calls.append(arguments)
        return _phase(log, *next(phases))

    monkeypatch.setattr(ecosystem, "run", run)
    assert (
        ecosystem._retest_agrees(
            command,
            ["test_case.py::test_a"],
            tmp_path,
            tmp_path,
            {},
            10,
            tmp_path,
            ecosystem.Project("fixture", "unused", "pinned", "package.py"),
            _phase(tmp_path / "initial-before.log", 0, "2 passed in 0.01s\n"),
            _phase(
                tmp_path / "initial-after.log",
                1,
                "FAILED test_case.py::test_a\n1 failed, 1 passed in 0.01s\n",
            ),
        )
        is expected
    )
    assert calls[2:] == [command, command]
    evidence = json.loads((tmp_path / "fixture-retest.json").read_text())
    assert evidence["full"]["command"] == command
    assert evidence["full"]["before"]["returncode"] == before[0]
    assert evidence["full"]["after"]["returncode"] == after[0]


@pytest.mark.parametrize(
    "original", [(0, "1 passed in 0.01s\n"), (0, ""), (2, "2 errors in 0.01s\n")]
)
def test_retest_cannot_replace_incomplete_or_differently_sized_initial_suites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, original: tuple[int, str]
) -> None:
    def unexpected_run(*arguments: object) -> ecosystem.Phase:
        raise AssertionError("Retesting cannot repair an invalid initial comparison")

    monkeypatch.setattr(ecosystem, "run", unexpected_run)
    assert not ecosystem._retest_agrees(
        [sys.executable, "-m", "pytest"],
        ["test_case.py::test_a"],
        tmp_path,
        tmp_path,
        {},
        10,
        tmp_path,
        ecosystem.Project("fixture", "unused", "pinned", "package.py"),
        _phase(tmp_path / "initial-before.log", *original),
        _phase(tmp_path / "initial-after.log", 0, "2 passed in 0.01s\n"),
    )


@pytest.mark.parametrize("regression", [False, True])
def test_check_project_requires_full_context_before_accepting_isolated_agreement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, regression: bool
) -> None:
    source = _git_fixture(tmp_path)
    common = "armed = False\n\ndef prepare():\n    global armed\n    armed = True\n\n"
    original = common + "def result():\n    return 42\n"
    (source / "package/original.py").write_text(original)
    (source / "test_state.py").write_text(
        "from package import original as stateful\n\n"
        "def test_prepare():\n    stateful.prepare()\n    assert stateful.armed\n\n"
        "def test_result():\n    assert stateful.result() == 42\n"
    )
    subprocess.run(
        ["git", "-C", str(source), "add", "package/original.py", "test_state.py"],
        check=True,
        capture_output=True,
        timeout=20,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=Audit Fixture",
            "-c",
            "user.email=audit@example.invalid",
            "commit",
            "-qm",
            "Commit the complete original test context",
        ],
        check=True,
        capture_output=True,
        timeout=20,
    )
    commit = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True, timeout=20
    ).strip()
    monkeypatch.setattr(ecosystem, "clone", lambda *_: commit)
    monkeypatch.setattr(ecosystem, "environment", lambda *_: Path(sys.executable))
    actual_run = ecosystem.run
    refactor_calls = 0

    def generate_refactoring_or_run_tests(
        command: Sequence[str], cwd: Path, env: dict[str, str], timeout: int, log: Path
    ) -> ecosystem.Phase:
        nonlocal refactor_calls
        if "towel.cli" not in command:
            return actual_run(
                command, cwd, dict(env, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1"), timeout, log
            )
        refactor_calls += 1
        assert command[3:5] == ["dry", "package/original.py"]
        assert cwd == tmp_path / "repository-ready"
        body = "return 0 if armed else 42" if regression else "return 40 + 2"
        Path(command[5]).write_text(common + f"def result():\n    {body}\n")
        return _phase(log, 0, "Applied 1 refactoring\n")

    monkeypatch.setattr(ecosystem, "run", generate_refactoring_or_run_tests)
    project = ecosystem.Project(
        "repository",
        "unused",
        commit,
        "package/original.py",
        test=("{python}", "-m", "pytest", "test_state.py", "-q", "-p", "no:cacheprovider"),
    )
    result = ecosystem.check_project(project, tmp_path, tmp_path / "unused-towel-src", 20)
    assert result.verdict == ("BROKEN" if regression else "PASS")
    assert result.commit == commit and result.changed_files == 1 and refactor_calls == 1
    assert (source / "package/original.py").read_text() == original
    assert result.baseline is not None and result.baseline.returncode == 0
    assert result.after is not None and result.after.returncode == int(regression)
    for phase in (result.baseline, result.after):
        outcome = ecosystem._completed_test_run(phase)
        assert outcome is not None and outcome.collected == 2
    evidence_path = tmp_path / "logs/repository-retest.json"
    if not regression:
        assert not evidence_path.exists()
        return
    evidence = json.loads(evidence_path.read_text())
    for side in ("before", "after"):
        assert evidence[side]["returncode"] == 0 and evidence[side]["summary"] == "1 passed"
    full = evidence["full"]
    assert full["initial_before"]["log"] == result.baseline.log
    assert full["initial_after"]["log"] == result.after.log
    assert full["command"] == ecosystem._prepare_test_command(
        [argument.format(python=sys.executable) for argument in project.test]
    )
    assert full["before"]["returncode"] == 0 and full["before"]["summary"] == "2 passed"
    assert full["after"]["returncode"] == 1 and full["after"]["summary"] == "1 failed, 1 passed"
    assert ecosystem.failed_tests(full["after"]["log"]) == {"test_state.py::test_result"}


@pytest.mark.parametrize(
    "reported,node",
    [
        # The separator pytest puts between the id and the message.
        ("test_flip.py::test_case - assert 1 != 1", "test_flip.py::test_case"),
        # The same separator inside a parameter id, which belongs to the id.
        (
            "test_flip.py::test_case[same - before] - assert 1 != 1",
            "test_flip.py::test_case[same - before]",
        ),
        (
            "test_flip.py::test_case[same - after] - assert 2 != 2",
            "test_flip.py::test_case[same - after]",
        ),
        # No message at all.
        ("test_flip.py::test_case[a - b]", "test_flip.py::test_case[a - b]"),
        # Nested brackets, and a message that itself contains the separator.
        ("t.py::test[x[1 - 2]] - E - detail", "t.py::test[x[1 - 2]]"),
    ],
)
def test_a_parameter_containing_the_separator_stays_part_of_the_node_id(
    reported: str, node: str
) -> None:
    """Truncating at the first " - " made different failures look like one.

    ``test_case[same - before]`` and ``test_case[same - after]`` both became
    ``test_case[same``, so a run failing one and a run failing the other
    presented identical failure sets. The harness compares those sets to award
    PASS, which is how a failure Towel introduced could hide behind a
    pre-existing failure in the same parametrized test.
    """
    assert ecosystem._node_id(reported) == node


def test_two_runs_failing_different_parameters_are_not_the_same_failure() -> None:
    before = "FAILED test_flip.py::test_case[same - before] - assert 1 != 1\n1 failed, 1 passed\n"
    after = "FAILED test_flip.py::test_case[same - after] - assert 2 != 2\n1 failed, 1 passed\n"
    assert ecosystem._failed_test_ids(before) != ecosystem._failed_test_ids(after)


def test_a_phase_that_times_out_takes_its_descendants_with_it(tmp_path: Path) -> None:
    """Killing the immediate process left its children running.

    A test suite's own workers, a server it started, a build it spawned: they
    survived the phase and went on writing into the scratch tree that later
    phases of the same project read. The phase now leads a process group, and
    the group ends when the phase does.
    """
    marker = tmp_path / "survived"
    script = tmp_path / "parent.py"
    script.write_text(
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', \"import time; time.sleep(3);\"\n"
        f"    \"open({str(marker)!r}, 'w').write('survived')\"])\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    phase = ecosystem.run(
        [sys.executable, str(script)], tmp_path, dict(os.environ), 1, tmp_path / "phase.log"
    )
    assert phase.returncode == -9, phase
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        assert not marker.exists(), "a descendant outlived the phase that timed out"
        time.sleep(0.25)
