#!/usr/bin/env python3
"""Run public projects' own test suites before and after Towel refactors them.

For every project in the manifest: clone at its requested revision, build its
own environment, run its suite as a baseline, copy it, refactor the package out
of place with the requested typing mode, adopt the cleaned copy back over the
package (the documented workflow, which exercises import paths that survive
relocation), run the suite again, and compare. A project qualifies when both
runs have recognized, nonempty pytest or unittest outcomes that agree in exit
status and normalized summary. Matching pre-existing test failures are allowed.
Progress is printed as each phase completes; only PASS, NO_CHANGE and
explicitly matched BROKEN_KNOWN verdicts satisfy the gate. Setup failures,
incomplete test runs and unknown verdicts make it fail. An isolated retest
match requires matching full-suite confirmations with the original test count
before it can qualify.

Towel runs the way a user runs it: inside the project's own environment. That
environment holds the project's test dependencies, the project itself installed
editable from the tree under test, Towel's types extra (mypy and pyright, unless
the project's own requirements already installed one), and the candidate: one
wheel, built from ``--towel-src`` or named by ``--towel-wheel``, verified to be
that source, and verified again in every environment it is installed into. The
type checkers therefore see the project's dependencies, and Towel's import model
sees the project installed from the tree it refactors, not an installed copy
elsewhere. The editable install follows the tree each test run exercises -- the
clone for the baseline, the refactored copy for Towel and the run after it, the
original package again whenever a retest runs the original -- so a test that
imports the project other than through ``PYTHONPATH`` tests the same code as the
rest of its run. Each report records the interpreter, the candidate, and the
mypy and pyright that were there, and who installed them.

Types stay enabled unless the caller explicitly requests ``--no-types``. A
project whose own sources do not type-check is declined by Towel rather than
refactored unverified, which is its documented behaviour and the answer it
gives such a user is to rerun without types. The corpus does exactly that: it
holds the refusal to its promised wording -- the count, a diagnostic naming its
file, and the way forward -- and then reruns that project with ``--no-types``
so its behaviour is still covered. The report says which projects that was, and
their verdicts are evidence about the untyped path only.

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
import contextlib
import dataclasses
import email.message
import email.parser
import fcntl
import hashlib
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
import urllib.parse
import zipfile
from pathlib import Path
from typing import (
    Callable,
    ContextManager,
    Dict,
    Iterator,
    List,
    Literal,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
)

REPO = Path(__file__).resolve().parents[1]
DEFAULT_TEST = ["{python}", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
SUMMARY_PATTERNS = (
    re.compile(r"^=+ .*(passed|failed|error|skipped|no tests ran).* =+$"),
    re.compile(r"^\d+ (passed|failed|error).*$"),
    re.compile(r"^(OK|FAILED)( \(.*\))?$"),
    re.compile(r"^Ran \d+ tests? in .*$"),
)
TypingMode = Literal["default", "no-types"]
CheckerSource = Literal["project", "towel[types]"]
TOWEL_PACKAGE = "towel"
"""The import package the candidate wheel provides."""
ENVIRONMENT_LAYOUT = 2
"""What a project environment holds; raised when that changes, so an older one is rebuilt."""


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
    install: bool = True
    """Install the project editable from the tree under test, as its developers have it."""
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


@dataclasses.dataclass(frozen=True)
class Checker:
    """A type checker in a project's environment, and who put it there."""

    name: str
    version: str
    source: CheckerSource
    """``project`` when the project's own requirements installed it, else Towel's types extra."""


@dataclasses.dataclass(frozen=True)
class Environment:
    """The environment Towel ran in for one project, as the report records it."""

    python: str
    towel: str
    checkers: Tuple[Checker, ...]
    installed_from: str = ""
    """The tree the project was installed from, editable, when Towel ran; empty if it was not."""


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
    fallback: str = ""
    """Why the typed attempt was declined, when the verdict came from a retry without types."""
    environment: Optional[Environment] = None
    """What Towel ran with; absent only when the environment could not be built."""


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
            install=bool(entry.get("install", True)),
            expect_broken=entry.get("expect_broken", ""),
            known_failures=tuple(entry.get("known_failures", [])),
            timeout=entry.get("timeout"),
            exclude=tuple(entry.get("exclude", [])),
            failure_exit_codes=tuple(entry.get("failure_exit_codes", (1,))),
        )
        if not only or project.name in only:
            projects.append(project)
    return projects


_PHASE_GROUPS: Set[int] = set()
"""Process groups of the phases this worker is running, for the watchdog to end."""
_PHASE_GROUPS_LOCK = threading.Lock()


def _end_group(pgid: int) -> None:
    """Terminate every process in ``pgid``, then make sure of it."""
    for signal_number in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, signal_number)
        except (ProcessLookupError, PermissionError):
            return
        if signal_number is signal.SIGTERM:
            time.sleep(0.2)


def run(command: Sequence[str], cwd: Path, env: Dict[str, str], timeout: int, log: Path) -> Phase:
    """Run one phase, streaming its output to ``log`` so progress is visible while it runs.

    The phase leads its own process group, and the group is ended when the
    phase is. Killing the immediate process alone left its descendants running:
    a test suite's own workers, a server it started, a build it spawned. They
    survived a timeout and went on consuming the machine and writing into the
    scratch tree while later phases of the same project read it. The worker's
    watchdog ends these groups too, so a killed harness still takes them with
    it (see ``_isolate_worker``).
    """
    start = time.monotonic()
    with log.open("w", encoding="utf-8") as handle:
        process = subprocess.Popen(
            list(command),
            cwd=cwd,
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        with _PHASE_GROUPS_LOCK:
            _PHASE_GROUPS.add(process.pid)
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            handle.write("\nTIMEOUT\n")
            returncode = -9
        finally:
            # On a timeout this is the kill; otherwise it reaps whatever the
            # phase left behind, which is equally not allowed to outlive it.
            _end_group(process.pid)
            process.wait()
            with _PHASE_GROUPS_LOCK:
                _PHASE_GROUPS.discard(process.pid)
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


class EnvironmentFailure(Exception):
    """A project environment that is not what the harness built it to hold."""


@dataclasses.dataclass(frozen=True)
class Candidate:
    """The Towel under test: one wheel, installed into every project's own environment."""

    wheel: Path
    distribution: str
    version: str
    sha256: str
    types_requirements: Tuple[str, ...]
    """What the wheel's ``types`` extra installs (mypy and pyright), as the wheel declares it."""
    files: Tuple[Tuple[str, str], ...]
    """Each file of the ``towel`` package in the wheel, relative to the package, and its SHA-256."""


_TYPES_EXTRA = re.compile(r"""^\s*extra\s*==\s*["']types["']\s*$""")
_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)")


def _canonical(name: str) -> str:
    """A distribution name as installers compare them (PEP 503)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_name(requirement: str) -> str:
    """The distribution a requirement names, canonically: ``mypy`` for ``mypy>=1.0.0``."""
    match = _REQUIREMENT_NAME.match(requirement)
    if match is None:
        raise ValueError(f"not a requirement: {requirement!r}")
    return _canonical(match[1])


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _header(metadata: email.message.Message, name: str) -> str:
    value = metadata.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"package metadata has no {name}")
    return value.strip()


def load_candidate(path: Path) -> Candidate:
    """The candidate in the wheel ``path``, or in the only wheel in the directory ``path``."""
    wheels = sorted(path.glob("*.whl")) if path.is_dir() else [path]
    if len(wheels) != 1:
        raise ValueError(f"{path} holds {len(wheels)} wheels; name the candidate itself")
    wheel = wheels[0].resolve(strict=True)
    prefix = f"{TOWEL_PACKAGE}/"
    with zipfile.ZipFile(wheel) as archive:
        members = archive.namelist()
        records = [name for name in members if re.fullmatch(r"[^/]+\.dist-info/METADATA", name)]
        if len(records) != 1:
            raise ValueError(f"{wheel.name} holds {len(records)} METADATA files, not one")
        metadata = email.parser.Parser().parsestr(archive.read(records[0]).decode("utf-8"))
        files = tuple(
            sorted(
                (name[len(prefix) :], _digest(archive.read(name)))
                for name in members
                if name.startswith(prefix) and not name.endswith("/")
            )
        )
    declared = (str(value).partition(";") for value in metadata.get_all("Requires-Dist", []))
    types = tuple(
        requirement.strip() for requirement, _, marker in declared if _TYPES_EXTRA.match(marker)
    )
    if not files:
        raise ValueError(f"{wheel.name} holds no {TOWEL_PACKAGE} package")
    if not types:
        raise ValueError(f"{wheel.name} declares no types extra, so nothing names its checkers")
    return Candidate(
        wheel,
        _header(metadata, "Name"),
        _header(metadata, "Version"),
        _digest(wheel.read_bytes()),
        types,
        files,
    )


def candidate_differences(candidate: Candidate, towel_src: Path) -> List[str]:
    """How the candidate's Python files differ from those under ``towel_src``; empty if none do.

    The report names the commit of ``--towel-src`` as the Towel that ran, so the wheel has to
    be that source: one built from another checkout, from a stale ``build`` directory, or
    before a later edit is evidence about code that no commit describes.
    """
    package = towel_src / TOWEL_PACKAGE
    source = {
        path.relative_to(package).as_posix(): _digest(path.read_bytes())
        for path in package.rglob("*.py")
        if path.is_file()
    }
    wheel = {path: digest for path, digest in candidate.files if path.endswith(".py")}
    only_source = sorted(source.keys() - wheel.keys())
    only_wheel = sorted(wheel.keys() - source.keys())
    changed = sorted(path for path in source.keys() & wheel.keys() if source[path] != wheel[path])
    return [
        *(f"{TOWEL_PACKAGE}/{path} is in the source but not in the wheel" for path in only_source),
        *(f"{TOWEL_PACKAGE}/{path} is in the wheel but not in the source" for path in only_wheel),
        *(f"{TOWEL_PACKAGE}/{path} differs" for path in changed),
    ]


def build_candidate(towel_src: Path, out: Path, log: Path) -> Path:
    """Build the candidate wheel from the checkout that holds ``towel_src``, into ``out``."""
    root = towel_src.resolve().parent
    if not (root / "pyproject.toml").is_file():
        raise ValueError(f"{root} has no pyproject.toml to build a candidate from")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    command = ["uv", "build", "--wheel", "--out-dir", str(out), str(root)]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    log.write_text(f"$ {shlex.join(command)}\n{completed.stdout}{completed.stderr}", "utf-8")
    if completed.returncode != 0:
        raise ValueError(
            f"building a candidate from {root} failed (exit {completed.returncode}, see {log})"
        )
    return out


def _uv(command: Sequence[str], log: Path) -> None:
    """Run one ``uv`` command, keeping what it said in ``log``, and fail loudly."""
    completed = subprocess.run(list(command), capture_output=True, text=True, check=False)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(f"$ {shlex.join(command)}\n{completed.stdout}{completed.stderr}\n")
    completed.check_returncode()


def _install(python: Path, log: Path, *arguments: str) -> None:
    _uv(["uv", "pip", "install", "-p", str(python), *arguments], log)


def _site_packages(python: Path) -> Path:
    found = sorted((python.parent.parent / "lib").glob("python*/site-packages"))
    if len(found) != 1:
        raise EnvironmentFailure(f"{python.parent.parent} has {len(found)} site-packages, not one")
    return found[0]


def editable_installs(python: Path) -> Dict[str, Path]:
    """Each distribution installed editable in ``python``'s environment, and its tree.

    An editable install records its tree in ``direct_url.json`` whatever its build
    backend does to make it importable -- a ``.pth`` path, a finder, a redirect.
    """
    installs: Dict[str, Path] = {}
    for record in sorted(_site_packages(python).glob("*.dist-info/direct_url.json")):
        data = json.loads(record.read_text(encoding="utf-8"))
        url = urllib.parse.urlparse(str(data.get("url", "")))
        editable = isinstance(data.get("dir_info"), dict) and data["dir_info"].get("editable")
        if url.scheme == "file" and editable:
            metadata = email.parser.Parser().parsestr(
                (record.parent / "METADATA").read_text(encoding="utf-8"), headersonly=True
            )
            installs[_canonical(_header(metadata, "Name"))] = Path(urllib.parse.unquote(url.path))
    return installs


def _install_editable(python: Path, tree: Path, log: Path, *, dependencies: bool) -> str:
    """Install the project editable from ``tree``; the distribution it was installed as."""
    _install(python, log, *([] if dependencies else ["--no-deps"]), "-e", str(tree))
    names = [
        name
        for name, location in editable_installs(python).items()
        if location.resolve() == tree.resolve()
    ]
    if len(names) != 1:
        raise EnvironmentFailure(
            f"installing {tree} left {len(names)} distributions installed from it"
        )
    return names[0]


_ENVIRONMENT_PROBE = """\
import hashlib, importlib.metadata, importlib.util, json, os, sys

package, *names = sys.argv[1:]
versions = {}
for name in names:
    try:
        versions[name] = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        versions[name] = None
spec = importlib.util.find_spec(package)
files = {}
for root in (spec.submodule_search_locations or []) if spec is not None else []:
    for directory, subdirectories, found in os.walk(root):
        subdirectories[:] = [name for name in subdirectories if name != "__pycache__"]
        for file in found:
            path = os.path.join(directory, file)
            with open(path, "rb") as handle:
                digest = hashlib.sha256(handle.read()).hexdigest()
            files[os.path.relpath(path, root).replace(os.sep, "/")] = digest
print(json.dumps({"python": sys.version.split()[0], "versions": versions, "files": files}))
"""
"""Run by an environment's own interpreter: what it would import, without importing any of it."""


@dataclasses.dataclass(frozen=True)
class EnvironmentProbe:
    """What one environment's interpreter sees."""

    python: str
    versions: Mapping[str, Optional[str]]
    """The installed version of each distribution asked about, or ``None``."""
    files: Tuple[Tuple[str, str], ...]
    """Each file of the ``towel`` package it would import, with its SHA-256."""


def probe_environment(python: Path, names: Sequence[str]) -> EnvironmentProbe:
    """Ask ``python`` what it sees, isolated as Towel isolates the checkers it starts."""
    completed = subprocess.run(
        [str(python), "-I", "-c", _ENVIRONMENT_PROBE, TOWEL_PACKAGE, *names],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
        cwd=python.parent,
    )
    data = json.loads(completed.stdout)
    return EnvironmentProbe(
        str(data["python"]),
        {
            str(name): None if value is None else str(value)
            for name, value in data["versions"].items()
        },
        tuple(sorted((str(path), str(digest)) for path, digest in data["files"].items())),
    )


def _verified(python: Path, candidate: Candidate, names: Sequence[str]) -> EnvironmentProbe:
    """What ``python`` sees, once it is confirmed that the Towel it runs is the candidate."""
    distribution = _canonical(candidate.distribution)
    probe = probe_environment(python, [distribution, *names])
    installed = probe.versions.get(distribution)
    if installed != candidate.version or probe.files != candidate.files:
        differing = len(set(probe.files) ^ set(candidate.files))
        raise EnvironmentFailure(
            f"the Towel that {python} runs is not the candidate {candidate.wheel.name}: "
            f"{candidate.distribution} is {installed or 'not installed'}, "
            f"and {differing} of its files differ from the wheel's"
        )
    return probe


def _install_checkers(python: Path, candidate: Candidate, log: Path) -> Dict[str, CheckerSource]:
    """Install the types extra's checkers that the project's own requirements did not."""
    names = [requirement_name(requirement) for requirement in candidate.types_requirements]
    present = probe_environment(python, names).versions
    missing = [
        requirement
        for requirement, name in zip(candidate.types_requirements, names)
        if present.get(name) is None
    ]
    if missing:
        _install(python, log, *missing)
    provenance: Dict[str, CheckerSource] = {}
    for name in names:
        provenance[name] = "project" if present.get(name) is not None else "towel[types]"
    return provenance


def _read_provenance(path: Path) -> Optional[Dict[str, CheckerSource]]:
    """The recorded source of each checker in an environment, or ``None`` if unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    provenance: Dict[str, CheckerSource] = {}
    for name, source in data.items():
        if source == "project":
            provenance[str(name)] = "project"
        elif source == "towel[types]":
            provenance[str(name)] = "towel[types]"
        else:
            return None
    return provenance


@dataclasses.dataclass(frozen=True)
class ProjectEnvironment:
    """One project's environment as the harness drives it."""

    python: Path
    distribution: Optional[str]
    """The project's own distribution when it is installed editable, else ``None``."""
    record: Environment


def environment(
    project: Project, work: Path, source: Path, candidate: Candidate, log: Path
) -> ProjectEnvironment:
    """The project's own environment, with the project installed from ``source``.

    It holds pytest and the manifest's dependencies, the project installed editable,
    the types extra's checkers that the project did not bring itself, and the
    candidate, installed with ``--no-deps`` and then verified. It is built once and
    reused while the manifest entry and the types extra are unchanged. The candidate
    is reinstalled on every use, and a reused environment is pointed back at
    ``source``: the last run left the project installed from its refactored copy.
    """
    env_dir = work / f"{project.name}-env"
    python = env_dir / "bin" / "python"
    fingerprint = env_dir / "towel-deps.txt"
    provenance_file = env_dir / "towel-checkers.json"
    wanted = "\n".join(
        [
            *sorted(project.deps),
            f"install={project.install}",
            *candidate.types_requirements,
            f"layout={ENVIRONMENT_LAYOUT}",
        ]
    )
    provenance = _read_provenance(provenance_file)
    reusable = (
        python.exists()
        and fingerprint.exists()
        and fingerprint.read_text() == wanted
        and provenance is not None
    )
    if not reusable and env_dir.exists():
        # The manifest or the layout changed, or an earlier build never finished.
        shutil.rmtree(env_dir)
    if not reusable:
        _uv(["uv", "venv", "-p", sys.executable, str(env_dir)], log)
        _install(python, log, "pytest", *project.deps)
    # The editable install is how the project's developers have it, and it supplies
    # package metadata and generated version modules; PYTHONPATH still selects the
    # copy under test.
    distribution = (
        _install_editable(python, source, log, dependencies=not reusable)
        if project.install
        else None
    )
    # After the project and its own requirements, so a checker they install is theirs.
    if not reusable or provenance is None:
        provenance = _install_checkers(python, candidate, log)
        provenance_file.write_text(json.dumps(provenance), encoding="utf-8")
        # Written last: an environment without it is one whose build never finished.
        fingerprint.write_text(wanted)
    _install(
        python,
        log,
        "--no-deps",
        "--reinstall-package",
        candidate.distribution,
        str(candidate.wheel),
    )
    probe = _verified(python, candidate, list(provenance))
    checkers = []
    for name, installer in provenance.items():
        version = probe.versions.get(name)
        if version is None:
            raise EnvironmentFailure(f"{name} is not installed in {env_dir}")
        checkers.append(Checker(name, version, installer))
    if distribution == _canonical(candidate.distribution):
        # One of Towel's own checkouts: its distribution is the candidate's, and the
        # candidate now holds it, so the checkout is not installed.
        distribution = None
    return ProjectEnvironment(
        python,
        distribution,
        Environment(
            probe.python,
            candidate.version,
            tuple(checkers),
            str(source) if distribution is not None else "",
        ),
    )


def install_from(
    installed: ProjectEnvironment,
    distribution: str,
    tree: Path,
    candidate: Candidate,
    log: Path,
) -> Environment:
    """Point the project's editable install at ``tree``; the candidate must be untouched."""
    moved = _install_editable(installed.python, tree, log, dependencies=False)
    if moved != distribution:
        raise EnvironmentFailure(f"{tree} installed as {moved}, not as {distribution}")
    _verified(installed.python, candidate, [checker.name for checker in installed.record.checkers])
    return dataclasses.replace(installed.record, installed_from=str(tree))


@contextlib.contextmanager
def original_installed(installed: Path, original: Path, aside: Path) -> Iterator[None]:
    """Hold the original package where the project is installed from, while the original runs.

    The editable install names the tree Towel refactored, which holds Towel's output
    once it is adopted. A test that imports the project other than through
    ``PYTHONPATH`` -- a subprocess started in another directory, or given an
    environment of its own -- gets that installed copy. A retest of the original would
    therefore run Towel's output in exactly those tests, and a regression there would
    fail on both sides and pass as a flaky difference. Towel's output is set aside,
    not copied, and put back however the run ends.
    """
    os.rename(installed, aside)
    try:
        if original.is_dir():
            shutil.copytree(original, installed, symlinks=True)
        else:
            shutil.copy2(original, installed)
        yield
    finally:
        if installed.is_dir() and not installed.is_symlink():
            shutil.rmtree(installed)
        elif installed.is_symlink() or installed.exists():
            installed.unlink()
        os.rename(aside, installed)


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


def _setup_failed(result: Result, error: Exception) -> Result:
    """Record that the project's clone or environment could not be made what it must be."""
    result.verdict = "SETUP_ERROR"
    said = error.stderr if isinstance(error, subprocess.CalledProcessError) else None
    if isinstance(said, bytes):
        said = said.decode("utf-8", "replace")
    result.detail = (said or str(error))[-500:]
    return result


def check_project(
    project: Project, work: Path, candidate: Candidate, timeout: int, no_types: bool = False
) -> Result:
    # A project may carry its own per-phase budget when it is far larger
    # than the rest of the corpus (networkx: 198k lines with its tests).
    timeout = project.timeout or timeout
    logs = work / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    source = work / project.name
    setup_log = logs / f"{project.name}-environment.log"
    result = Result(project.name, "PENDING", typing_mode="no-types" if no_types else "default")
    try:
        result.commit = clone(project, source, logs)
        installed = environment(project, work, source, candidate, setup_log)
    except (subprocess.CalledProcessError, EnvironmentFailure) as error:
        return _setup_failed(result, error)
    result.environment = installed.record
    python = installed.python
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
    if installed.distribution is not None:
        # Towel refactors this copy, so this is where the project is installed from
        # while it does, as a developer's own checkout is: Towel's import model counts
        # an installed copy anywhere else as a second provider of the project's names.
        # The run after tests the adopted output here too, installed copy included.
        try:
            result.environment = install_from(
                installed, installed.distribution, ready, candidate, setup_log
            )
        except (subprocess.CalledProcessError, EnvironmentFailure) as error:
            return _setup_failed(result, error)
    # Towel runs as its user runs it: the environment's own ``towel`` from the
    # project root, with nothing on PYTHONPATH, so the checkers it starts see the
    # project's dependencies and its import model sees the project's installation.
    towel = python.parent / "towel"
    towel_env = {name: value for name, value in env.items() if name != "PYTHONPATH"}
    # Each refactor may fork workers for a large analysis; with several
    # projects in flight the caller caps that through TOWEL_WORKERS.
    if "TOWEL_WORKERS" in os.environ:
        towel_env["TOWEL_WORKERS"] = os.environ["TOWEL_WORKERS"]
    # Refactor OUT OF PLACE to a differently named directory, then adopt the
    # cleaned copy back over the package. This mirrors the documented workflow
    # ("write to a new directory, diff, then adopt") and exercises import paths
    # that survive relocation, which an in-place refactor cannot check.
    # The output goes in a directory of its own, never directly under ``work``.
    # Towel writes its recovery journal to the common parent of the files a
    # transaction changes, and refuses to start while a journal that may cover
    # its targets is pending. For a one-module project the single output file
    # sits directly in its parent, so a shared parent would put that journal at
    # an ancestor of every other project's files and make concurrent projects
    # refuse each other (peewee's journal once stopped astroid at --workers 4).
    # One directory per project means no project's output shares a parent with
    # another's, whatever its shape.
    cleaned_root = work / f"{project.name}-out"
    if cleaned_root.exists():
        shutil.rmtree(cleaned_root)
    cleaned_root.mkdir(parents=True)
    cleaned = cleaned_root / f"{project.name}-cleaned"

    def refactor(without_types: bool, log_name: str) -> Phase:
        if cleaned_root.exists():
            shutil.rmtree(cleaned_root)
        cleaned_root.mkdir(parents=True)
        return run(
            [
                str(towel),
                "dry",
                project.package,
                str(cleaned),
                "--no-interactive",
                "--progress",
                "none",
                *(["--no-types"] if without_types else []),
                *(argument for name in project.exclude for argument in ("--exclude", name)),
            ],
            ready,
            towel_env,
            timeout,
            logs / log_name,
        )

    log_name = f"{project.name}-refactor.log"
    result.refactor = refactor(no_types, log_name)
    if result.refactor.returncode != 0 and result.refactor.returncode != -9 and not no_types:
        # A project whose own sources do not check is one Towel declines to
        # verify, which is the documented behaviour and not a defect. The
        # answer it gives its user is to rerun without types, so that is what
        # the corpus does: the refusal is held to its promised wording, and the
        # project then goes through the rest of the run on the untyped path, so
        # its behaviour is still covered by evidence. Both attempts keep their
        # logs; the run's typing mode records which one produced the verdict.
        refused = _refusal(logs / log_name)
        declined = _baseline_refusal(refused)
        if declined is not None:
            malformed = _malformed_refusal(declined, logs / log_name)
            if malformed is not None:
                result.verdict = "REFUSAL_MALFORMED"
                result.detail = malformed
                return result
            result.fallback = declined.kind
            result.detail = declined.summary
            result.typing_mode = "no-types"
            log_name = f"{project.name}-refactor-no-types.log"
            result.refactor = refactor(True, log_name)
    if result.refactor.returncode == -9:
        result.verdict = "TIMEOUT"
        result.detail = f"refactor exceeded {timeout}s"
        return result
    if result.refactor.returncode != 0:
        refused = _refusal(logs / log_name)
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
    # A retest runs the original again, and it must find the original wherever the
    # project is installed from (see ``original_installed``).
    original: Callable[[], ContextManager[None]] = (
        functools.partial(
            original_installed, package_path, source / project.package, cleaned_root / "adopted"
        )
        if installed.distribution is not None
        else contextlib.nullcontext
    )
    if differing and _retest_agrees(
        test,
        differing,
        source,
        ready,
        env,
        timeout,
        logs,
        project,
        result.baseline,
        result.after,
        original_installed=original,
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
    original_installed: Callable[[], ContextManager[None]] = contextlib.nullcontext,
) -> bool:
    """Confirm a difference disappears both alone and in its original suite.

    Each run of the original is made inside ``original_installed``, which holds the
    original package wherever the project is installed from (see ``original_installed``
    at module level); a project that is not installed needs nothing.
    """
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
    with original_installed():
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
    with original_installed():
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
    r"Original project type check failed: .*|"
    r"Unsupported build backend .*|.*cannot infer safe imports.*)$",
    re.M,
)

PRE_EXISTING_ERRORS = re.compile(r"^Original project check reported ([1-9]\d*) type error\(s\):$")
CHECKER_FAILED = re.compile(r"^Original project type check failed: (.+)$")
DIAGNOSTIC_LINE = re.compile(r"^  (?P<path>[^:]+.*?): (?P<message>.+)$", re.M)
REMEDY = "rerun with --no-types"


def _refusal(log_path: Path) -> str:
    """The engine's own refusal message when it declined the project up front."""
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    match = REFUSAL.search(text)
    return match.group(1) if match else ""


@dataclasses.dataclass(frozen=True)
class BaselineRefusal:
    """A refusal to verify, because the project's own sources do not check."""

    kind: str
    summary: str


def _baseline_refusal(refused: str) -> Optional[BaselineRefusal]:
    """Whether this refusal is about the project's own type baseline, and which kind."""
    errors = PRE_EXISTING_ERRORS.match(refused)
    if errors is not None:
        return BaselineRefusal("pre-existing-errors", f"{errors.group(1)} pre-existing type errors")
    failed = CHECKER_FAILED.match(refused)
    if failed is not None:
        return BaselineRefusal("checker-failed", failed.group(1)[:200])
    return None


def _malformed_refusal(declined: BaselineRefusal, log_path: Path) -> Optional[str]:
    """What the refusal failed to tell its reader, or ``None`` when it told them everything.

    A refusal is the only thing a user of an unchecked project ever sees, so the
    corpus holds it to its promise: say how many errors there are, show some of
    them with the file they are in, and name the way forward. Asserting it here
    means every project in the corpus that trips it is a test of the wording.
    """
    text = log_path.read_text(encoding="utf-8", errors="replace")
    if REMEDY not in text:
        return f"refusal did not name the way forward ({REMEDY!r} absent)"
    if declined.kind != "pre-existing-errors":
        return None
    shown = DIAGNOSTIC_LINE.findall(text)
    if not shown:
        return "refusal reported a count but showed no diagnostic with its file"
    return None


FAILED_LINE = re.compile(r"^(?:FAILED|ERROR) ([^(].*)$")
UNITTEST_FAILED_LINE = re.compile(r"^(?:FAIL|ERROR|UNEXPECTED SUCCESS): (.+)$")


def _node_id(reported: str) -> str:
    """The node id at the head of a ``FAILED <id> - <message>`` line.

    Splitting at the first " - " is wrong, because a parametrized id can
    contain one: ``test_case[same - before]`` and ``test_case[same - after]``
    both truncate to ``test_case[same``, and two runs failing *different*
    tests then present identical failure sets. The harness compares those sets
    to decide PASS, so a failure Towel introduced could be hidden by a
    pre-existing failure in the same parametrized test.

    A separator inside brackets belongs to the id, so only one outside them
    ends it. pytest writes the id first and the message after, never the
    reverse, so the first such separator is the right one.
    """
    depth = 0
    for index, character in enumerate(reported):
        if character in "[(":
            depth += 1
        elif character in "])":
            depth = max(0, depth - 1)
        elif depth == 0 and reported.startswith(" - ", index):
            return reported[:index].strip()
    return reported.strip()


def _failed_test_ids(output: str) -> Set[str]:
    """Full pytest node ids and unittest failure headers, without diagnostic suffixes."""
    identities: Set[str] = set()
    for line in _plain_lines(output):
        pytest_failure = FAILED_LINE.fullmatch(line)
        if pytest_failure is not None:
            identities.add(_node_id(pytest_failure[1]))
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
        with _PHASE_GROUPS_LOCK:
            groups = list(_PHASE_GROUPS)
        for pgid in groups:
            # Each phase leads its own group, so the worker's group no longer
            # contains them and killing it alone would leave them running.
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
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
`prepare` command, installs the projects and their test dependencies, and runs
their test suites, all with your privileges and your environment. A hijacked or
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


def prepare_candidate(towel_src: Path, wheel: Optional[Path], work: Path) -> Candidate:
    """The candidate: the wheel named, or one built from ``towel_src``'s checkout.

    Raises ``ValueError`` when there is no usable wheel, or when the wheel is not
    the source under ``towel_src``, whose commit the report names.
    """
    if wheel is None:
        wheel = build_candidate(towel_src, work / "candidate", work / "logs" / "candidate.log")
    candidate = load_candidate(wheel)
    differences = candidate_differences(candidate, towel_src)
    if differences:
        shown = "; ".join(differences[:5])
        more = f" and {len(differences) - 5} more" if len(differences) > 5 else ""
        raise ValueError(
            f"{candidate.wheel.name} is not the source under {towel_src}: {shown}{more}"
        )
    return candidate


def _checkers_cell(environment: Optional[Environment]) -> str:
    """``mypy 1.19.1 (project), pyright 1.1.414`` for one row of the summary."""
    if environment is None:
        return ""
    return ", ".join(
        f"{checker.name} {checker.version}" + (" (project)" if checker.source == "project" else "")
        for checker in environment.checkers
    )


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
    parser.add_argument(
        "--towel-src",
        type=Path,
        default=REPO / "src",
        help="the Towel source under test, whose commit the report names (default: this "
        "checkout's src)",
    )
    parser.add_argument(
        "--towel-wheel",
        type=Path,
        default=None,
        help="the candidate wheel, or a directory holding only it, installed into every "
        "project's environment; it must be the source under --towel-src (default: build "
        "one from the checkout that holds --towel-src)",
    )
    parser.add_argument(
        "--no-types",
        action="store_true",
        help="disable Towel type inference/checking for every project in this corpus; "
        "default: retain Towel's type policy, and rerun without types only those "
        "projects Towel declines to verify, which the report names",
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
    try:
        projects = load_manifest(args.manifest, args.only)
    except ValueError as error:
        parser.error(f"{args.manifest}: {error}")
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
    (args.work / "logs").mkdir(exist_ok=True)
    towel_commit, dirty = source_revision(args.towel_src)
    displayed_commit = towel_commit[:12] if towel_commit else "unavailable (source archive)"
    try:
        candidate = prepare_candidate(args.towel_src, args.towel_wheel, args.work)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(f"refusing to run: no candidate to test: {error}", file=sys.stderr)
        return 2
    print(
        f"ecosystem check: {len(projects)} projects, towel {displayed_commit}, "
        f"candidate {candidate.wheel.name} (sha256 {candidate.sha256[:12]}), "
        f"typing mode: {typing_mode}",
        flush=True,
    )
    # Every project runs the candidate wheel, fixed before the first one starts, so
    # a later edit to --towel-src cannot change the subject mid-run. What a dirty
    # tree still changes is the provenance: the wheel holds edits no commit names.
    if dirty is None:
        print(
            "WARNING: Towel source is outside a Git checkout; revision and dirty-state "
            "provenance are unavailable. Retain an independent source manifest.",
            flush=True,
        )
    elif dirty:
        print(
            "WARNING: the Towel checkout under test has uncommitted changes, which the "
            "candidate holds, so the recorded commit does not describe what ran. Prefer a "
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
                check_project, project, args.work, candidate, args.timeout, args.no_types
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
            mode = f"[{result.typing_mode}]" if result.fallback else ""
            print(
                f"{result.verdict:15} {result.name:18} files={result.changed_files:<3} "
                f"refactor={seconds:6.1f}s {mode}{detail_line[:80]}",
                flush=True,
            )
            (report_dir / f"{result.name}.json").write_text(
                json.dumps(dataclasses.asdict(result), indent=2), encoding="utf-8"
            )
    results.sort(key=lambda item: item.name)
    counts: Dict[str, int] = {}
    for result in results:
        counts[result.verdict] = counts.get(result.verdict, 0) + 1
    fallbacks: Dict[str, int] = {}
    for result in results:
        if result.fallback:
            fallbacks[result.fallback] = fallbacks.get(result.fallback, 0) + 1
    lines = [
        f"# Ecosystem check — towel `{towel_commit or 'unavailable (source archive)'}`",
        "",
        f"Candidate: `{candidate.wheel.name}` ({candidate.distribution} {candidate.version}, "
        f"sha256 `{candidate.sha256}`), installed into each project's own environment "
        "with the project installed editable from the tree under test. The Checkers "
        "column names the mypy and pyright there; `(project)` marks one the project's "
        "own requirements installed, the rest came from the candidate's types extra.",
        "",
        f"Typing mode requested: `{typing_mode}`. A project whose own sources do not "
        "check is declined by Towel and rerun here without types; its row says so, and "
        "its verdict is evidence about the untyped path only. Runtime test outcomes do "
        "not establish type-checking coverage either way.",
        "",
        "| Project | Commit | Verdict | Typing | Checkers | Changed files | Refactor s | Before "
        "| After |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for result in results:
        typing = result.typing_mode + (f" (declined: {result.fallback})" if result.fallback else "")
        lines.append(
            f"| {result.name} | `{result.commit[:10]}` | {result.verdict} | {typing} | "
            f"{_checkers_cell(result.environment)} | {result.changed_files} | "
            f"{result.refactor.seconds if result.refactor else 0:.0f} | "
            f"{result.baseline.summary if result.baseline else ''} | "
            f"{result.after.summary if result.after else ''} |"
        )
    lines += ["", "Totals: " + ", ".join(f"{key} {value}" for key, value in sorted(counts.items()))]
    if fallbacks:
        lines += [
            "",
            "Declined the typed path and rerun without types: "
            + ", ".join(f"{key} {value}" for key, value in sorted(fallbacks.items())),
        ]
    (report_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (report_dir / "summary.json").write_text(
        json.dumps(
            {
                "towel": towel_commit,
                "candidate": {
                    "wheel": candidate.wheel.name,
                    "distribution": candidate.distribution,
                    "version": candidate.version,
                    "sha256": candidate.sha256,
                },
                "typing_mode": typing_mode,
                "counts": counts,
                "declined_typed_path": fallbacks,
                "results": [dataclasses.asdict(r) for r in results],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        "\n".join(
            line for line in lines if line.startswith("Totals:") or line.startswith("Declined")
        ),
        flush=True,
    )
    accepted = {"PASS", "NO_CHANGE", "BROKEN_KNOWN"}
    return 0 if results and all(result.verdict in accepted for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
