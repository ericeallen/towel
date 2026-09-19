"""The release harness must distinguish test outcomes from incomplete commands."""

from __future__ import annotations

import concurrent.futures
from concurrent.futures import ThreadPoolExecutor
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
) -> ecosystem.Result:
    source = root / "fixture"
    source.mkdir()
    (source / "package.py").write_text("value = 1\n")
    monkeypatch.setattr(ecosystem, "clone", lambda *_: "pinned")
    monkeypatch.setattr(ecosystem, "environment", lambda *_: Path(sys.executable))
    monkeypatch.setattr(ecosystem, "base_env", lambda *_: {})
    monkeypatch.setattr(ecosystem, "changed", lambda *_: (int(changed), "fixture"))
    phases = iter([before, after])

    def run(
        command: Sequence[str], cwd: Path, env: dict[str, str], timeout: int, log: Path
    ) -> ecosystem.Phase:
        if "towel.cli" in command:
            (root / "fixture-cleaned").write_text("value = 2\n" if changed else "value = 1\n")
            return _phase(log, 0, "Applied 1 refactoring\n")
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
    )
    return ecosystem.check_project(project, root, root / "towel-src", 10)


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
