#!/usr/bin/env python3
"""Run public projects' own test suites before and after Towel refactors them.

For every project in the manifest: clone at its requested revision, install its
test dependencies into a private environment, run its suite as a baseline,
copy it, refactor the package out of place with the requested typing mode, adopt the
cleaned copy back over the package (the documented workflow, which exercises
import paths that survive relocation), run the suite again, and compare. A
project qualifies when both runs have recognized, nonempty pytest or unittest
outcomes that agree in exit status and normalized summary. Matching pre-existing
test failures are allowed. Progress is printed as each phase completes; only
PASS, NO_CHANGE and explicitly matched BROKEN_KNOWN verdicts satisfy the gate.
Setup failures, incomplete test runs and unknown verdicts make it fail.
An isolated retest match requires matching full-suite confirmations with the
original test count before it can qualify. Types stay enabled unless the caller
explicitly requests ``--no-types``; a type refusal never triggers a fallback.

Trust boundary: this script executes code it does not review. It clones
public repositories, runs each manifest entry's ``prepare`` command, installs
the projects' test dependencies, and runs their test suites, all with the
invoking user's privileges. That is remote code execution by design, so the
script refuses to run unless the caller opts in with ``--run-untrusted-code``
or ``TOWEL_ECOSYSTEM_RUN_UNTRUSTED=1``, and it belongs on a disposable
machine, a container, or an ephemeral CI runner, never on a workstation
holding credentials. The manifest pins projects to reviewed commits except the
intentional ``towel-main`` HEAD compatibility entry;
``--print-pins`` lists the commits the last run tested so the pins can be
refreshed after review.

    python scripts/ecosystem_check.py --run-untrusted-code --work /tmp/towel-ecosystem --workers 4
"""

from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import fcntl
import json
import functools
import os
import tempfile
import signal
import threading
import re
import shlex
import shutil
import subprocess
import sys
import time
import tomllib
import traceback
from pathlib import Path
from typing import Dict, List, Literal, Optional, Sequence, Set, Tuple

REPO = Path(__file__).resolve().parents[1]
DEFAULT_TEST = ["{python}", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
SUMMARY_PATTERNS = (
    re.compile(r"^=+ .*(passed|failed|error|skipped|no tests ran).* =+$"),
    re.compile(r"^\d+ (passed|failed|error).*$"),
    re.compile(r"^(OK|FAILED)( \(.*\))?$"),
    re.compile(r"^Ran \d+ tests? in .*$"),
)
TypingMode = Literal["default", "no-types"]


@dataclasses.dataclass(frozen=True)
class Project:
    name: str
    url: str
    rev: str
    package: str
    pythonpath: str = "."
    deps: Tuple[str, ...] = ()
    test: Tuple[str, ...] = tuple(DEFAULT_TEST)
    prepare: str = ""
    install: bool = False
    expect_broken: str = ""
    known_failures: Tuple[str, ...] = ()
    timeout: Optional[int] = None
    exclude: Tuple[str, ...] = ()
    failure_exit_codes: Tuple[int, ...] = (1,)

    def __post_init__(self) -> None:
        if not self.failure_exit_codes or any(
            type(code) is not int or not 1 <= code <= 255 for code in self.failure_exit_codes
        ):
            raise ValueError("failure_exit_codes must contain positive process exit codes (1-255)")


@dataclasses.dataclass(frozen=True)
class Phase:
    returncode: int
    seconds: float
    summary: str
    log: str


@dataclasses.dataclass(frozen=True)
class FullRetestEvidence:
    command: Tuple[str, ...]
    initial_before: Phase
    initial_after: Phase
    before: Phase
    after: Phase


@dataclasses.dataclass(frozen=True)
class RetestEvidence:
    command: Tuple[str, ...]
    test_ids: Tuple[str, ...]
    failure_exit_codes: Tuple[int, ...]
    before: Phase
    after: Phase
    full: Optional[FullRetestEvidence] = None


@dataclasses.dataclass
class Result:
    name: str
    verdict: str
    commit: str = ""
    baseline: Optional[Phase] = None
    refactor: Optional[Phase] = None
    after: Optional[Phase] = None
    changed_files: int = 0
    diff_stat: str = ""
    detail: str = ""
    typing_mode: TypingMode = "default"


def load_manifest(path: Path, only: Sequence[str]) -> List[Project]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    projects = []
    for entry in data["project"]:
        project = Project(
            name=entry["name"],
            url=entry["url"],
            rev=entry.get("rev", "HEAD"),
            package=entry["package"],
            pythonpath=entry.get("pythonpath", "."),
            deps=tuple(entry.get("deps", ())),
            test=tuple(entry.get("test", DEFAULT_TEST)),
            prepare=entry.get("prepare", ""),
            install=bool(entry.get("install", False)),
            expect_broken=entry.get("expect_broken", ""),
            known_failures=tuple(entry.get("known_failures", [])),
            timeout=entry.get("timeout"),
            exclude=tuple(entry.get("exclude", [])),
            failure_exit_codes=tuple(entry.get("failure_exit_codes", (1,))),
        )
        if not only or project.name in only:
            projects.append(project)
    return projects


def run(command: Sequence[str], cwd: Path, env: Dict[str, str], timeout: int, log: Path) -> Phase:
    """Run one phase, streaming its output to ``log`` so progress is visible while it runs."""
    start = time.monotonic()
    with log.open("w", encoding="utf-8") as handle:
        process = subprocess.Popen(
            list(command), cwd=cwd, env=env, stdout=handle, stderr=subprocess.STDOUT, text=True
        )
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            handle.write("\nTIMEOUT\n")
            returncode = -9
    output = log.read_text(encoding="utf-8", errors="replace")
    return Phase(returncode, time.monotonic() - start, summarize(output), str(log))


ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
PYTEST_TALLY = re.compile(
    r"(\d+) (passed|failed|errors?|skipped|xfailed|xpassed|deselected|warnings?|"
    r"subtests passed|subtests failed)"
)
UNITTEST_COUNT = re.compile(r"^Ran (\d+) tests? in .+$")
UNITTEST_RESULT = re.compile(r"^(OK|FAILED)( \(.*\))?$")


@dataclasses.dataclass(frozen=True)
class TestOutcome:
    """A recognized completed suite and the exit status its result requires."""

    summary: str
    returncode: int
    collected: int


def _plain_lines(output: str) -> List[str]:
    plain = ANSI.sub("", output.replace("\r", "\n"))
    return [line.strip() for line in plain.splitlines() if line.strip()]


def _normalize_summary(line: str) -> str:
    without_time = re.sub(r" in [\d.]+s(?: \(\d+:\d+:\d+\))?", "", line).strip("= ")
    # Moving a warning changes pytest's grouping but not the test outcome.
    return re.sub(r",? \d+ warnings?\b", "", without_time).strip(", ")


def _test_outcome(output: str) -> Optional[TestOutcome]:
    """Recognize actual tallies; arbitrary command output is not a test result.

    All-skipped or expected-failure suites still have collected tests. A
    unittest run needs both its positive count and its closing OK/FAILED;
    include every completed run for commands that chain multiple suites.
    """
    lines = _plain_lines(output)
    for line in reversed(lines):
        summary = _normalize_summary(line)
        counts: Dict[str, int] = {}
        for item in summary.split(", "):
            match = PYTEST_TALLY.fullmatch(item)
            if match is None:
                break
            counts[match[2]] = counts.get(match[2], 0) + int(match[1])
        else:
            tests = sum(
                count
                for label, count in counts.items()
                if label in {"passed", "failed", "error", "errors", "skipped", "xfailed", "xpassed"}
            )
            if tests:
                failed = any(
                    counts.get(label, 0)
                    for label in ("failed", "error", "errors", "subtests failed")
                )
                return TestOutcome(summary, int(failed), tests)
    pending: Optional[int] = None
    completed: List[str] = []
    failed = False
    collected = 0
    for line in lines:
        count = UNITTEST_COUNT.fullmatch(line)
        if count is not None:
            if pending is not None or int(count[1]) == 0:
                return None
            pending = int(count[1])
        elif UNITTEST_RESULT.fullmatch(line):
            if pending is None:
                return None
            completed.append(f"Ran {pending} tests; {line}")
            collected += pending
            failed = failed or line.startswith("FAILED")
            pending = None
    if completed and pending is None:
        return TestOutcome(" | ".join(completed), int(failed), collected)
    return None


def _completed_test_run(
    phase: Phase, failure_exit_codes: Tuple[int, ...] = (1,)
) -> Optional[TestOutcome]:
    """An outcome only if the log and process status both describe a completed suite."""
    try:
        output = Path(phase.log).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    outcome = _test_outcome(output)
    if outcome is None:
        return None
    if outcome.returncode == 0 and phase.returncode != 0:
        return None
    if outcome.returncode != 0 and (
        phase.returncode <= 0 or phase.returncode not in failure_exit_codes
    ):
        return None
    # Equal red tallies are not evidence of equal failures without identities.
    if outcome.returncode != 0 and not _failed_test_ids(output):
        return None
    # Keep the runner's actual status so a changed failure convention cannot
    # be mistaken for an identical before/after result.
    return dataclasses.replace(outcome, returncode=phase.returncode)


def summarize(output: str) -> str:
    """The suite's final tally, without color codes or elapsed time."""
    outcome = _test_outcome(output)
    if outcome is not None:
        return outcome.summary
    lines = _plain_lines(output)
    for line in reversed(lines):
        if any(pattern.match(line) for pattern in SUMMARY_PATTERNS):
            return _normalize_summary(line)
    return lines[-1] if lines else ""


@functools.lru_cache(maxsize=None)
def _scratch_home() -> str:
    """A HOME for the subjects' processes, so none reads or writes the caller's."""
    return tempfile.mkdtemp(prefix="towel-ecosystem-home-")


def base_env(pythonpath: str, python_bin: Path) -> Dict[str, str]:
    env = {
        "PATH": f"{python_bin}:{os.environ.get('PATH', '')}",
        "HOME": _scratch_home(),
        "PYTHONPATH": pythonpath,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TERM": "dumb",
        "NO_COLOR": "1",
        "PY_COLORS": "0",
    }
    return env


def clone(project: Project, source: Path, log_dir: Path) -> str:
    if not source.exists():
        subprocess.run(
            ["git", "clone", "-q", "--filter=blob:none", project.url, str(source)],
            check=True,
            capture_output=True,
        )
    if project.rev != "HEAD":
        subprocess.run(
            ["git", "-C", str(source), "checkout", "-q", project.rev],
            check=True,
            capture_output=True,
        )
    if project.prepare:
        # The command is split into an argument vector and run without a shell.
        # The manifest's only ``prepare`` is ``git submodule update --init
        # --quiet``; no entry needs pipes, globs, or variable expansion, and
        # going through a shell would let a manifest edit smuggle in more than
        # one command. A future entry that needs shell features should be
        # rewritten as a small script the manifest names instead.
        subprocess.run(shlex.split(project.prepare), cwd=source, check=True, capture_output=True)
    return subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


def environment(project: Project, work: Path, source: Path) -> Path:
    env_dir = work / f"{project.name}-env"
    python = env_dir / "bin" / "python"
    fingerprint = env_dir / "towel-deps.txt"
    wanted = "\n".join([*sorted(project.deps), f"install={project.install}"])
    if python.exists() and (not fingerprint.exists() or fingerprint.read_text() != wanted):
        shutil.rmtree(env_dir)  # the manifest changed; rebuild the environment
    if not python.exists():
        subprocess.run(["uv", "venv", "-q", "-p", sys.executable, str(env_dir)], check=True)
        subprocess.run(
            ["uv", "pip", "install", "-q", "-p", str(python), "pytest", *project.deps],
            check=True,
            capture_output=True,
        )
        if project.install:
            # Editable install supplies package metadata and generated version
            # modules; PYTHONPATH still selects the copy under test.
            subprocess.run(
                ["uv", "pip", "install", "-q", "-p", str(python), "-e", str(source)],
                check=True,
                capture_output=True,
            )
        fingerprint.write_text(wanted)
    return python


def changed(ready: Path, package: str) -> Tuple[int, str]:
    """Count affected package paths, failing if Git cannot observe them."""
    status = subprocess.run(
        [
            "git",
            "-c",
            "status.renames=false",
            "-C",
            str(ready),
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--",
            package,
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout
    count = sum(bool(record) for record in status.split("\0"))
    stat = (
        subprocess.run(
            ["git", "-C", str(ready), "diff", "--stat", "--", package],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        .stdout.strip()
        .splitlines()
    )
    return count, stat[-1] if stat else ""


def source_revision(source: Path) -> Tuple[Optional[str], Optional[str]]:
    """Observe the actual source checkout, or explicitly identify an archive."""
    source = source.resolve(strict=True)
    if not any((parent / ".git").exists() for parent in (source, *source.parents)):
        return None, None
    commit = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "-C", str(source), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout.strip()
    return commit, dirty


def _pytest_arguments_start(command: Sequence[str]) -> Optional[int]:
    """The argument boundary for an explicit pytest executable or Python module call."""
    if not command:
        return None
    executable = Path(command[0]).name
    if executable in {"pytest", "py.test"}:
        return 1
    if (
        re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", executable)
        and len(command) >= 3
        and list(command[1:3]) == ["-m", "pytest"]
    ):
        return 3
    return None


def _prepare_test_command(command: Sequence[str]) -> List[str]:
    """Request pytest tallies and identities after project reporting defaults.

    Keep selections and configuration intact. A literal ``--`` ends option
    parsing, so insert the reporting option immediately before it. Other
    runners are left alone and still need recognizable outcomes to qualify.
    """
    prepared = list(command)
    start = _pytest_arguments_start(command)
    if start is None:
        return prepared
    end = prepared.index("--", start) if "--" in prepared[start:] else len(prepared)
    reporting = ["--verbosity=0", "-ra"]
    if prepared[max(start, end - len(reporting)) : end] != reporting:
        prepared[end:end] = reporting
    return prepared


def check_project(
    project: Project, work: Path, towel_src: Path, timeout: int, no_types: bool = False
) -> Result:
    # A project may carry its own per-phase budget when it is far larger
    # than the rest of the corpus (networkx: 198k lines with its tests).
    timeout = project.timeout or timeout
    logs = work / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    source = work / project.name
    result = Result(project.name, "PENDING", typing_mode="no-types" if no_types else "default")
    try:
        result.commit = clone(project, source, logs)
        python = environment(project, work, source)
    except subprocess.CalledProcessError as error:
        result.verdict = "SETUP_ERROR"
        result.detail = (
            (error.stderr or b"").decode("utf-8", "replace")[-500:]
            if isinstance(error.stderr, bytes)
            else str(error.stderr)[-500:]
        )
        return result
    test = _prepare_test_command([part.format(python=python) for part in project.test])
    env = base_env(project.pythonpath, python.parent)
    result.baseline = run(test, source, env, timeout, logs / f"{project.name}-before.log")
    baseline_outcome = _completed_test_run(result.baseline, project.failure_exit_codes)
    if baseline_outcome is None:
        result.verdict = "BASELINE_ERROR"
        result.detail = (
            f"incomplete test run (exit {result.baseline.returncode}): {result.baseline.summary}"
        )
        return result
    ready = work / f"{project.name}-ready"
    if ready.exists():
        shutil.rmtree(ready)
    shutil.copytree(source, ready, symlinks=True)
    towel_env = dict(env, PYTHONPATH=str(towel_src))
    # Each refactor may fork workers for a large analysis; with several
    # projects in flight the caller caps that through TOWEL_WORKERS.
    if "TOWEL_WORKERS" in os.environ:
        towel_env["TOWEL_WORKERS"] = os.environ["TOWEL_WORKERS"]
    # Refactor OUT OF PLACE to a differently named directory, then adopt the
    # cleaned copy back over the package. This mirrors the documented workflow
    # ("write to a new directory, diff, then adopt") and exercises import paths
    # that survive relocation, which an in-place refactor cannot check.
    cleaned = work / f"{project.name}-cleaned"
    # ``cleaned`` is a directory for a package but a single file for a
    # one-module project (six, xmltodict, pycodestyle, ...). A leftover from a
    # previous run in a reused work directory may therefore be either, and
    # ``rmtree`` raises NotADirectoryError on a file, so remove it by kind.
    if cleaned.is_dir():
        shutil.rmtree(cleaned)
    elif cleaned.exists():
        cleaned.unlink()
    result.refactor = run(
        [
            sys.executable,
            "-m",
            "towel.cli",
            "dry",
            project.package,
            str(cleaned),
            "--no-interactive",
            "--progress",
            "none",
            *(["--no-types"] if no_types else []),
            *(argument for name in project.exclude for argument in ("--exclude", name)),
        ],
        ready,
        towel_env,
        timeout,
        logs / f"{project.name}-refactor.log",
    )
    if result.refactor.returncode == -9:
        result.verdict = "TIMEOUT"
        result.detail = f"refactor exceeded {timeout}s"
        return result
    if result.refactor.returncode != 0:
        refused = _refusal(logs / f"{project.name}-refactor.log")
        if refused.startswith("Original project check reported "):
            result.verdict = "TYPE_BASELINE_ERROR"
        else:
            result.verdict = "UNSUPPORTED" if refused else "CRASH"
        result.detail = refused or result.refactor.summary
        return result
    # Adopt: replace the package with the cleaned copy in place. A package
    # directory is swapped wholesale (dropping the non-source helper sidecar the
    # CLI writes for naming); a single-file module is copied over directly.
    package_path = ready / project.package
    if cleaned.is_dir():
        (cleaned / ".towel-helpers.json").unlink(missing_ok=True)
        shutil.rmtree(package_path)
        shutil.copytree(cleaned, package_path, symlinks=True)
    else:
        shutil.copyfile(cleaned, package_path)
    result.changed_files, result.diff_stat = changed(ready, project.package)
    if result.changed_files == 0:
        result.verdict = "NO_CHANGE"
        return result
    result.after = run(test, ready, env, timeout, logs / f"{project.name}-after.log")
    after_outcome = _completed_test_run(result.after, project.failure_exit_codes)
    if after_outcome is None:
        result.verdict = "AFTER_ERROR"
        result.detail = (
            f"incomplete test run (exit {result.after.returncode}): {result.after.summary}"
        )
        return result
    before_failed = failed_tests(result.baseline.log)
    after_failed = failed_tests(result.after.log)
    if after_outcome == baseline_outcome and before_failed == after_failed:
        result.verdict = "PASS"
        return result
    if after_outcome.collected != baseline_outcome.collected:
        # Retesting a few differing failures cannot account for tests missing
        # from the full run, nor can a named frame/source limitation.
        result.verdict = "BROKEN"
        result.detail = (
            f"test count changed from {baseline_outcome.collected} to {after_outcome.collected}: "
            f"before: {result.baseline.summary} | after: {result.after.summary}"
        )
        return result
    differing = sorted(before_failed ^ after_failed)
    if differing and _retest_agrees(
        test, differing, source, ready, env, timeout, logs, project, result.baseline, result.after
    ):
        # Removing earlier tests can hide a deterministic stateful regression.
        # Both isolated and full-suite confirmations must agree.
        result.verdict = "PASS"
        result.detail = f"same full-suite outcome on rerun: {' '.join(differing)[:200]}"
        return result
    unexpected = [
        test for test in differing if not any(pattern in test for pattern in project.known_failures)
    ]
    if project.expect_broken and differing and not unexpected:
        # Every test that differs, in either direction, is one the manifest
        # names; the documented limitation explains the difference (a
        # frame-sensitive assertion may fail before and pass after just as
        # well as the reverse), and nothing else changed.
        result.verdict = "BROKEN_KNOWN"
        result.detail = project.expect_broken
    else:
        result.verdict = "BROKEN"
        result.detail = f"before: {result.baseline.summary} | after: {result.after.summary}"
        if unexpected:
            result.detail = f"{len(unexpected)} unexpected: {' '.join(unexpected)[:200]}"
    return result


_OPTIONS_WITH_VALUES = {
    "-o",
    "-p",
    "-k",
    "-m",
    "-c",
    "-W",
    "-r",
    "--tb",
    "--rootdir",
    "--verbosity",
    "--override-ini",
    "--ignore",
    "--ignore-glob",
    "--deselect",
    "--maxfail",
    "--confcutdir",
    "--basetemp",
    "--junitxml",
    "--junit-xml",
    "--import-mode",
    "--assert",
    "--capture",
    "--color",
    "--durations",
    "--durations-min",
}
_OPTIONS_WITHOUT_VALUES = {
    "--verbose",
    "--quiet",
    "--disable-warnings",
    "--disable-pytest-warnings",
    "--no-header",
    "--no-summary",
    "--strict-markers",
    "--strict-config",
    "--continue-on-collection-errors",
}


def _retest_command(test: Sequence[str], test_ids: Sequence[str]) -> Optional[List[str]]:
    """The project's test command narrowed to the given node ids.

    Remove positional selectors wherever they appear. Preserve known options
    and their values, and decline ambiguous plugin options instead of guessing
    whether the following token is a value or another selector.
    """
    start = _pytest_arguments_start(test)
    if start is None:
        return None
    command = list(test[:start])
    index = start
    separator = False
    while index < len(test):
        argument = test[index]
        if argument == "--":
            separator = True
            break
        if argument in _OPTIONS_WITH_VALUES:
            if index + 1 == len(test):
                return None
            command.extend(test[index : index + 2])
            index += 2
            continue
        if argument.startswith("-"):
            attached = argument.startswith("--") and "=" in argument
            short_value = argument[:2] in _OPTIONS_WITH_VALUES and len(argument) > 2
            if not (
                attached
                or short_value
                or argument in _OPTIONS_WITHOUT_VALUES
                or re.fullmatch(r"-[qvsx]+", argument)
            ):
                return None
            command.append(argument)
        index += 1
    if command[-2:] == ["--verbosity=0", "-ra"]:
        del command[-2:]
    elif command[-1:] == ["-ra"]:
        command.pop()
    if separator or any(identity.startswith("-") for identity in test_ids):
        command.append("--")
    return _prepare_test_command([*command, *test_ids])


def _retest_agrees(
    test: Sequence[str],
    test_ids: Sequence[str],
    source: Path,
    ready: Path,
    env: Dict[str, str],
    timeout: int,
    logs: Path,
    project: Project,
    initial_before: Phase,
    initial_after: Phase,
) -> bool:
    """Confirm a difference disappears both alone and in its original suite."""
    if _pytest_arguments_start(test) is None or any(
        test_id.startswith("unittest:") for test_id in test_ids
    ):
        # unittest (including custom runners) does not accept pytest node ids
        # or options. Preserve the observed difference instead of guessing.
        return False
    command = _retest_command(test, test_ids)
    if command is None:
        return False
    initial_before_outcome = _completed_test_run(initial_before, project.failure_exit_codes)
    initial_after_outcome = _completed_test_run(initial_after, project.failure_exit_codes)
    if (
        initial_before_outcome is None
        or initial_after_outcome is None
        or initial_before_outcome.collected != initial_after_outcome.collected
    ):
        return False
    before = run(command, source, env, timeout, logs / f"{project.name}-retest-before.log")
    after = run(command, ready, env, timeout, logs / f"{project.name}-retest-after.log")
    evidence = RetestEvidence(
        tuple(command), tuple(test_ids), project.failure_exit_codes, before, after
    )
    (logs / f"{project.name}-retest.json").write_text(
        json.dumps(dataclasses.asdict(evidence), indent=2) + "\n", encoding="utf-8"
    )
    before_outcome = _completed_test_run(before, project.failure_exit_codes)
    after_outcome = _completed_test_run(after, project.failure_exit_codes)
    if (
        before_outcome is None
        or before_outcome != after_outcome
        or before_outcome.collected != len(set(test_ids))
    ):
        return False
    before_failed = failed_tests(before.log)
    if before_failed != failed_tests(after.log) or not before_failed <= set(test_ids):
        return False
    full_command = _prepare_test_command(test)
    full_before = run(
        full_command, source, env, timeout, logs / f"{project.name}-retest-full-before.log"
    )
    full_after = run(
        full_command, ready, env, timeout, logs / f"{project.name}-retest-full-after.log"
    )
    evidence = dataclasses.replace(
        evidence,
        full=FullRetestEvidence(
            tuple(full_command), initial_before, initial_after, full_before, full_after
        ),
    )
    (logs / f"{project.name}-retest.json").write_text(
        json.dumps(dataclasses.asdict(evidence), indent=2) + "\n", encoding="utf-8"
    )
    full_before_outcome = _completed_test_run(full_before, project.failure_exit_codes)
    full_after_outcome = _completed_test_run(full_after, project.failure_exit_codes)
    return (
        full_before_outcome is not None
        and full_before_outcome == full_after_outcome
        and full_before_outcome.collected == initial_before_outcome.collected
        and failed_tests(full_before.log) == failed_tests(full_after.log)
    )


REFUSAL = re.compile(
    r"^Error: (Original project check reported [1-9]\d* type error\(s\):|"
    r"Unsupported build backend .*|.*cannot infer safe imports.*)$",
    re.M,
)


def _refusal(log_path: Path) -> str:
    """The engine's own refusal message when it declined the project up front."""
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    match = REFUSAL.search(text)
    return match.group(1) if match else ""


FAILED_LINE = re.compile(r"^(?:FAILED|ERROR) ([^(].*)$")
UNITTEST_FAILED_LINE = re.compile(r"^(?:FAIL|ERROR|UNEXPECTED SUCCESS): (.+)$")


def _failed_test_ids(output: str) -> Set[str]:
    """Full pytest node ids and unittest failure headers, without diagnostic suffixes."""
    identities: Set[str] = set()
    for line in _plain_lines(output):
        pytest_failure = FAILED_LINE.fullmatch(line)
        if pytest_failure is not None:
            identities.add(pytest_failure[1].split(" - ", 1)[0].strip())
        unittest_failure = UNITTEST_FAILED_LINE.fullmatch(line)
        if unittest_failure is not None:
            identities.add(f"unittest:{unittest_failure[1]}")
    return identities


def failed_tests(log_path: str) -> Set[str]:
    """Test ids reported as failed or errored by pytest or unittest."""
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    return _failed_test_ids(text)


def _isolate_worker() -> None:
    """Run in each pool worker: own process group, ended with the harness.

    A worker forked by the pool outlives a killed harness and keeps cloning,
    testing and refactoring in the shared work directory, where a second
    harness then races it (one such race produced a false BROKEN verdict).
    The worker leads its own process group so that, when the harness dies,
    a watchdog thread can end the worker and every subprocess it started.
    """
    os.setsid()
    parent = os.getppid()

    def watch() -> None:
        while os.getppid() == parent:
            time.sleep(1)
        os.killpg(os.getpgrp(), signal.SIGKILL)

    threading.Thread(target=watch, name="harness-parent-watchdog", daemon=True).start()


def _lock_work_directory(work: Path) -> int:
    """Hold an exclusive lock for the run; two harnesses must never share a work directory."""
    handle = os.open(work / ".harness.lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(f"another ecosystem check is using {work}; choose a different --work")
    return handle


OPT_IN_FLAG = "--run-untrusted-code"
OPT_IN_ENV = "TOWEL_ECOSYSTEM_RUN_UNTRUSTED"

REFUSAL_MESSAGE = f"""\
refusing to run: this script executes code it does not review.

It clones the public repositories named in the manifest, runs each entry's
`prepare` command, installs the projects' test dependencies, and runs their
test suites, all with your privileges and your environment. A hijacked or
malicious upstream could do anything you can do from this account.

Run it only on a disposable machine, a container, or an ephemeral CI runner,
and opt in explicitly with {OPT_IN_FLAG} or {OPT_IN_ENV}=1.
"""


def untrusted_code_allowed(flag: bool, environ: Dict[str, str]) -> bool:
    """Whether the caller opted in to executing the manifest's projects."""
    return flag or environ.get(OPT_IN_ENV) == "1"


def print_pins(report_dir: Path) -> int:
    """Print ``name = sha`` for every project in the last run's report.

    Reads only the per-project JSON the harness wrote; nothing is executed. The
    output is the input for refreshing the manifest's ``rev`` fields after the
    new commits have been reviewed (towel-main deliberately stays at HEAD).
    """
    reports = sorted(path for path in report_dir.glob("*.json") if path.stem != "summary")
    if not reports:
        print(f"no per-project reports under {report_dir}", file=sys.stderr)
        return 1
    for path in reports:
        entry = json.loads(path.read_text(encoding="utf-8"))
        print(f"{entry['name']} = {entry.get('commit', '')}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--manifest", type=Path, default=REPO / "scripts" / "ecosystem" / "manifest.toml"
    )
    parser.add_argument(
        "--work",
        type=Path,
        default=None,
        help="directory to clone and test in (default: a fresh private temporary directory; "
        "pass one to reuse clones or to read a previous run's report)",
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--only", nargs="*", default=[])
    parser.add_argument("--timeout", type=int, default=1800, help="seconds per phase")
    parser.add_argument("--towel-src", type=Path, default=REPO / "src")
    parser.add_argument(
        "--no-types",
        action="store_true",
        help="explicitly disable Towel type inference/checking for this runtime-test corpus; "
        "default: retain Towel's type policy, with no automatic fallback",
    )
    parser.add_argument(
        OPT_IN_FLAG,
        action="store_true",
        help=f"acknowledge that the manifest's projects run with your privileges "
        f"(or set {OPT_IN_ENV}=1); use a disposable machine or container",
    )
    parser.add_argument(
        "--print-pins",
        action="store_true",
        help="print `name = commit` from the last run's report under --work and exit; "
        "executes nothing",
    )
    args = parser.parse_args()
    typing_mode: TypingMode = "no-types" if args.no_types else "default"
    if args.print_pins:
        if args.work is None:
            parser.error("--print-pins needs --work, the directory of the run to read")
        return print_pins(args.work / "report")
    if not untrusted_code_allowed(args.run_untrusted_code, dict(os.environ)):
        print(REFUSAL_MESSAGE, file=sys.stderr, end="")
        return 2
    projects = load_manifest(args.manifest, args.only)
    missing = set(args.only) - {project.name for project in projects}
    if missing:
        parser.error(f"unknown projects: {', '.join(sorted(missing))}")
    if args.work is None:
        # A shared /tmp path could be pre-created by another local user, who
        # would then own the tree this run clones into and executes from.
        args.work = Path(tempfile.mkdtemp(prefix="towel-ecosystem-"))
        print(f"work directory: {args.work}", flush=True)
    args.work.mkdir(parents=True, exist_ok=True)
    _lock_work_directory(args.work)
    report_dir = args.work / "report"
    report_dir.mkdir(exist_ok=True)
    towel_commit, dirty = source_revision(args.towel_src)
    displayed_commit = towel_commit[:12] if towel_commit else "unavailable (source archive)"
    print(
        f"ecosystem check: {len(projects)} projects, towel {displayed_commit}, "
        f"typing mode: {typing_mode}",
        flush=True,
    )
    # Workers import Towel from ``--towel-src`` for the whole run, so an edit or a
    # commit (pre-commit stashes the tree) in that checkout changes the subject
    # mid-run and invalidates the results. Say so up front when the tree is dirty,
    # and recommend a detached worktree for the source under test.
    if dirty is None:
        print(
            "WARNING: Towel source is outside a Git checkout; revision and dirty-state "
            "provenance are unavailable. Retain an independent source manifest.",
            flush=True,
        )
    elif dirty:
        print(
            "WARNING: the Towel checkout under test has uncommitted changes; edits or "
            "commits during the run will change what the workers import. Prefer a "
            "detached worktree: git worktree add --detach <dir> <commit> and "
            "--towel-src <dir>/src.",
            flush=True,
        )
    results: List[Result] = []
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=args.workers, initializer=_isolate_worker
    ) as pool:
        futures = {
            pool.submit(
                check_project, project, args.work, args.towel_src, args.timeout, args.no_types
            ): project
            for project in projects
        }
        for future in concurrent.futures.as_completed(futures):
            project = futures[future]
            try:
                result = future.result()
            except Exception as error:  # noqa: BLE001 - report and continue
                # Keep the traceback: a bare ``repr`` of, say, a
                # NotADirectoryError names neither the path nor the call site,
                # which is what made this class of harness bug hard to diagnose.
                result = Result(
                    project.name,
                    "HARNESS_ERROR",
                    detail=f"{error!r}\n{traceback.format_exc()}",
                    typing_mode=typing_mode,
                )
            results.append(result)
            seconds = result.refactor.seconds if result.refactor else 0.0
            detail_line = result.detail.splitlines()[0] if result.detail else ""
            print(
                f"{result.verdict:15} {result.name:18} files={result.changed_files:<3} "
                f"refactor={seconds:6.1f}s {detail_line[:80]}",
                flush=True,
            )
            (report_dir / f"{result.name}.json").write_text(
                json.dumps(dataclasses.asdict(result), indent=2), encoding="utf-8"
            )
    results.sort(key=lambda item: item.name)
    counts: Dict[str, int] = {}
    for result in results:
        counts[result.verdict] = counts.get(result.verdict, 0) + 1
    lines = [
        f"# Ecosystem check — towel `{towel_commit or 'unavailable (source archive)'}`",
        "",
        f"Typing mode: `{typing_mode}`. This records the requested mode; runtime test "
        "outcomes do not establish type-checking coverage.",
        "",
        "| Project | Commit | Verdict | Changed files | Refactor s | Before | After |",
        "|---|---|---|---|---|---|---|",
    ]
    for result in results:
        lines.append(
            f"| {result.name} | `{result.commit[:10]}` | {result.verdict} | {result.changed_files} | "
            f"{result.refactor.seconds if result.refactor else 0:.0f} | "
            f"{result.baseline.summary if result.baseline else ''} | "
            f"{result.after.summary if result.after else ''} |"
        )
    lines += ["", "Totals: " + ", ".join(f"{key} {value}" for key, value in sorted(counts.items()))]
    (report_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (report_dir / "summary.json").write_text(
        json.dumps(
            {
                "towel": towel_commit,
                "typing_mode": typing_mode,
                "counts": counts,
                "results": [dataclasses.asdict(r) for r in results],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print("\n".join(lines[-1:]), flush=True)
    accepted = {"PASS", "NO_CHANGE", "BROKEN_KNOWN"}
    return 0 if results and all(result.verdict in accepted for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
