"""The corpus runs Towel the way a user runs it: inside the project's own environment.

The harness used to run Towel in its own interpreter, which held Towel's tools and
none of the project's dependencies. mypy then checked with the wrong interpreter,
so the project's third-party imports were errors, and the import model found the
tools' own copies of click, packaging and the rest and called those projects'
names ambiguous. These tests pin what replaced that: one candidate wheel, verified
to be the source under test, installed with the types extra's checkers into each
project's environment, where the project is installed editable from the tree the
run is testing, and ``--cross-module`` on every refactor that does not say why not.
"""

from __future__ import annotations

import base64
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor
import dataclasses
import functools
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from typing import Callable, ContextManager, Dict, List, Mapping, Optional, Sequence, Tuple
import zipfile

import pytest

from scripts import ecosystem_check as ecosystem

CLI = """\
import sys


def main():
    if sys.argv[1:] == ["dry", "--help"]:
        print("usage: towel dry [-h] INPUT OUTPUT")
        return 0
    return 2
"""
TYPES = ('mypy>=1.0; extra == "types"', 'pyright>=1.1; extra == "types"')


def _record_line(path: str, data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"{path},sha256={digest},{len(data)}"


def _wheel(
    directory: Path,
    name: str,
    version: str,
    files: Mapping[str, str],
    *,
    requires: Sequence[str] = (),
    scripts: Optional[Mapping[str, str]] = None,
) -> Path:
    """A pure-Python wheel holding ``files``, as an installer expects one."""
    stem = name.replace("-", "_")
    info = f"{stem}-{version}.dist-info"
    contents = dict(files)
    contents[f"{info}/METADATA"] = (
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
        + "".join(f"Requires-Dist: {requirement}\n" for requirement in requires)
    )
    contents[f"{info}/WHEEL"] = (
        "Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    )
    if scripts:
        contents[f"{info}/entry_points.txt"] = "[console_scripts]\n" + "".join(
            f"{script} = {target}\n" for script, target in scripts.items()
        )
    directory.mkdir(parents=True, exist_ok=True)
    wheel = directory / f"{stem}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        lines = []
        for path, text in contents.items():
            archive.writestr(path, text.encode())
            lines.append(_record_line(path, text.encode()))
        archive.writestr(f"{info}/RECORD", "\n".join([*lines, f"{info}/RECORD,,"]) + "\n")
    return wheel


def _towel_files() -> Dict[str, str]:
    return {
        "towel/__init__.py": '"""A stand-in for the candidate."""\n',
        "towel/cli.py": CLI,
    }


def _candidate_wheel(directory: Path, *, requires: Sequence[str] = TYPES) -> Path:
    return _wheel(
        directory,
        "code-towel",
        "9.9.9",
        _towel_files(),
        requires=[*requires, 'black>=26; extra == "format"'],
        scripts={"towel": "towel.cli:main"},
    )


def _source_tree(root: Path, files: Mapping[str, str]) -> Path:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


# -- The candidate -------------------------------------------------------------


def test_the_candidate_is_what_its_wheel_declares(tmp_path: Path) -> None:
    wheel = _candidate_wheel(tmp_path / "dist")
    candidate = ecosystem.load_candidate(wheel)
    assert (candidate.distribution, candidate.version) == ("code-towel", "9.9.9")
    # Only the types extra names checkers; the format extra and the rest are not asked for.
    assert candidate.types_requirements == ("mypy>=1.0", "pyright>=1.1")
    assert candidate.sha256 == hashlib.sha256(wheel.read_bytes()).hexdigest()
    assert [path for path, _ in candidate.files] == ["__init__.py", "cli.py"]
    # A directory holding exactly one wheel names it, as the corpus image's does.
    assert ecosystem.load_candidate(tmp_path / "dist") == candidate


@pytest.mark.parametrize(
    "arrange,message",
    [
        ("two wheels", "holds 2 wheels"),
        ("no types extra", "declares no types extra"),
        ("no towel package", "holds no towel package"),
    ],
)
def test_a_wheel_that_does_not_say_what_to_install_is_not_a_candidate(
    tmp_path: Path, arrange: str, message: str
) -> None:
    dist = tmp_path / "dist"
    if arrange == "two wheels":
        _candidate_wheel(dist)
        _wheel(dist, "code-towel", "9.9.8", _towel_files(), requires=TYPES)
    elif arrange == "no types extra":
        _candidate_wheel(dist, requires=())
    else:
        _wheel(dist, "code-towel", "9.9.9", {"other/__init__.py": ""}, requires=TYPES)
    with pytest.raises(ValueError, match=message):
        ecosystem.load_candidate(dist)


def test_the_candidate_must_be_the_source_the_report_names(tmp_path: Path) -> None:
    candidate = ecosystem.load_candidate(_candidate_wheel(tmp_path / "dist"))
    source = _source_tree(tmp_path / "src", _towel_files())
    assert ecosystem.candidate_differences(candidate, source) == []
    (source / "towel/cli.py").write_text("changed = True\n")
    (source / "towel/added.py").write_text("")
    (source / "towel/__init__.py").unlink()
    assert ecosystem.candidate_differences(candidate, source) == [
        "towel/added.py is in the source but not in the wheel",
        "towel/__init__.py is in the wheel but not in the source",
        "towel/cli.py differs",
    ]
    with pytest.raises(ValueError, match="is not the source under"):
        ecosystem.prepare_candidate(source, tmp_path / "dist", tmp_path / "work")


def test_a_run_whose_candidate_is_not_its_source_refuses_before_any_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _candidate_wheel(tmp_path / "dist")
    source = _source_tree(tmp_path / "src", {**_towel_files(), "towel/cli.py": "edited = 1\n"})
    project = ecosystem.Project("fixture", "unused", "pinned", "package.py")
    monkeypatch.setattr(ecosystem, "load_manifest", lambda *_: [project])

    def unexpected(*_: object) -> ecosystem.Result:
        raise AssertionError("a project ran against a candidate that is not the source")

    monkeypatch.setattr(ecosystem, "check_project", unexpected)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ecosystem_check.py",
            "--run-untrusted-code",
            "--work",
            str(tmp_path / "work"),
            "--towel-src",
            str(source),
            "--towel-wheel",
            str(tmp_path / "dist"),
        ],
    )
    assert ecosystem.main() == 2
    assert "towel/cli.py differs" in capsys.readouterr().err
    assert not (tmp_path / "work/report/summary.json").exists()


# -- The project's own environment, built for real ------------------------------

BACKEND = '''\
"""A build backend with no requirements, so an editable install needs no index."""

import base64
import hashlib
import os
import tomllib
import zipfile


def _record(path, data):
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"{path},sha256={digest},{len(data)}"


def get_requires_for_build_editable(config_settings=None):
    return []


def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    with open("pyproject.toml", "rb") as handle:
        project = tomllib.load(handle)["project"]
    name, version = project["name"], project["version"]
    stem = name.replace("-", "_")
    info = f"{stem}-{version}.dist-info"
    files = {
        f"_{stem}_editable.pth": os.path.abspath(os.getcwd()) + "\\n",
        f"{info}/METADATA": f"Metadata-Version: 2.1\\nName: {name}\\nVersion: {version}\\n",
        f"{info}/WHEEL": "Wheel-Version: 1.0\\nGenerator: fixture\\nRoot-Is-Purelib: true\\n"
        "Tag: py3-none-any\\n",
    }
    wheel = f"{stem}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(os.path.join(wheel_directory, wheel), "w") as archive:
        lines = []
        for path, text in files.items():
            archive.writestr(path, text.encode())
            lines.append(_record(path, text.encode()))
        archive.writestr(f"{info}/RECORD", "\\n".join([*lines, f"{info}/RECORD,,"]) + "\\n")
    return wheel


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    raise NotImplementedError("this fixture only installs editable")
'''

uv_required = pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv")


def _project_tree(root: Path, name: str, package: str, tree: str) -> Path:
    return _source_tree(
        root,
        {
            "pyproject.toml": (
                '[build-system]\nrequires = []\nbuild-backend = "backend"\n'
                f'backend-path = ["."]\n\n[project]\nname = "{name}"\nversion = "1.0"\n'
            ),
            "backend.py": BACKEND,
            f"{package}/__init__.py": f"TREE = {tree!r}\n",
        },
    )


@pytest.fixture
def offline_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """uv with no index: every requirement comes from a directory of fixture wheels."""
    links = tmp_path / "links"
    _wheel(links, "pytest", "0.0.1", {"pytest.py": ""})
    _wheel(links, "mypy", "1.0.0", {"mypy/__init__.py": ""})
    _wheel(links, "pyright", "1.1.0", {"pyright/__init__.py": ""})
    for name, value in {
        "UV_OFFLINE": "1",
        "UV_NO_INDEX": "1",
        "UV_FIND_LINKS": str(links),
        "UV_CACHE_DIR": str(tmp_path / "uv-cache"),
        "UV_PYTHON_DOWNLOADS": "never",
    }.items():
        monkeypatch.setenv(name, value)
    return links


def _imported_tree(python: Path, package: str) -> str:
    """Which tree ``python`` imports ``package`` from, asked from outside any tree."""
    return subprocess.run(
        [str(python), "-I", "-c", f"import {package}; print({package}.TREE)"],
        capture_output=True,
        text=True,
        check=True,
        cwd=python.parent,
    ).stdout.strip()


@uv_required
def test_the_environment_holds_the_project_its_checkers_and_the_candidate(
    tmp_path: Path, offline_index: Path
) -> None:
    candidate = ecosystem.load_candidate(_candidate_wheel(tmp_path / "dist"))
    work = tmp_path / "work"
    source = _project_tree(work / "sample", "sample", "sample", "source")
    log = tmp_path / "environment.log"
    # The project brings its own pyright; mypy it leaves to Towel's types extra.
    project = ecosystem.Project("sample", "unused", "pinned", "sample", deps=("pyright",))
    installed = ecosystem.environment(project, work, source, candidate, log)
    assert installed.python == work / "sample-env/bin/python"
    assert installed.distribution == "sample"
    assert installed.record == ecosystem.Environment(
        platform.python_version(),
        "9.9.9",
        (
            ecosystem.Checker("mypy", "1.0.0", "towel[types]"),
            ecosystem.Checker("pyright", "1.1.0", "project"),
        ),
        str(source),
    )
    assert _imported_tree(installed.python, "sample") == "source"
    # Towel refactors a copy, so that is where the project is installed from while it
    # does; its import model would count an installed copy anywhere else as a second
    # provider of the project's names.
    ready = work / "sample-ready"
    shutil.copytree(source, ready)
    (ready / "sample/__init__.py").write_text("TREE = 'ready'\n")
    moved = ecosystem.install_from(installed, "sample", ready, candidate, log)
    assert moved == dataclasses.replace(installed.record, installed_from=str(ready))
    assert _imported_tree(installed.python, "sample") == "ready"
    # The next run reuses the environment, pointed back at the clone for its baseline.
    marker = work / "sample-env/reused"
    marker.write_text("")
    again = ecosystem.environment(project, work, source, candidate, log)
    assert marker.exists() and again.record == installed.record
    assert _imported_tree(installed.python, "sample") == "source"
    commands = [line for line in log.read_text().splitlines() if line.startswith("$ uv pip")]
    mypy_installs = [command for command in commands if "mypy>=1.0" in command]
    assert len(mypy_installs) == 1, "a reused environment keeps the checkers it was given"
    # A changed manifest entry is a different environment, built from nothing.
    changed = dataclasses.replace(project, deps=("pyright", "mypy"))
    rebuilt = ecosystem.environment(changed, work, source, candidate, log)
    assert not marker.exists()
    assert [checker.source for checker in rebuilt.record.checkers] == ["project", "project"]


@uv_required
def test_a_towel_checkout_leaves_its_distribution_to_the_candidate(
    tmp_path: Path, offline_index: Path
) -> None:
    """Towel's own checkouts are Towel's distribution, which the candidate must hold."""
    candidate = ecosystem.load_candidate(_candidate_wheel(tmp_path / "dist"))
    work = tmp_path / "work"
    source = _project_tree(work / "towel-old", "code-towel", "towel", "checkout")
    project = ecosystem.Project("towel-old", "unused", "pinned", "towel")
    installed = ecosystem.environment(project, work, source, candidate, tmp_path / "log")
    assert installed.distribution is None and installed.record.installed_from == ""
    # What runs is the candidate, not the checkout the tests import through PYTHONPATH.
    assert ecosystem.editable_installs(installed.python) == {}
    towel = subprocess.run(
        [str(installed.python.parent / "towel"), "dry", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert towel.stdout.startswith("usage: towel dry")


@uv_required
def test_an_environment_whose_towel_is_not_the_candidate_is_refused(
    tmp_path: Path, offline_index: Path
) -> None:
    candidate = ecosystem.load_candidate(_candidate_wheel(tmp_path / "dist"))
    work = tmp_path / "work"
    source = _project_tree(work / "sample", "sample", "sample", "source")
    project = ecosystem.Project("sample", "unused", "pinned", "sample")
    installed = ecosystem.environment(project, work, source, candidate, tmp_path / "log")
    names = [checker.name for checker in installed.record.checkers]
    (ecosystem._site_packages(installed.python) / "towel/cli.py").write_text("tampered = 1\n")
    with pytest.raises(ecosystem.EnvironmentFailure, match="is not the candidate"):
        ecosystem._verified(installed.python, candidate, names)
    # Every use reinstalls the candidate, so the next run is back on it.
    ecosystem.environment(project, work, source, candidate, tmp_path / "log")
    ecosystem._verified(installed.python, candidate, names)


# -- How Towel is run ----------------------------------------------------------


def _fake_towel(directory: Path, help_text: str, status: int = 0) -> Path:
    towel = directory / "towel"
    directory.mkdir(parents=True, exist_ok=True)
    towel.write_text(f"#!/bin/sh\ncat <<'EOF'\n{help_text}\nEOF\nexit {status}\n")
    towel.chmod(0o755)
    return towel


@pytest.mark.parametrize(
    "help_text,accepted",
    [
        ("usage: towel dry [-h] [--cross-module] INPUT OUTPUT", True),
        ("  --cross-module        extract helpers shared across modules", True),
        ("usage: towel dry [-h] [--no-types] INPUT OUTPUT", False),
        ("  --no-cross-module     keep helpers inside one module", False),
        ("  --cross-module-limit N", False),
    ],
)
def test_cross_module_support_is_read_from_the_towel_that_runs(
    tmp_path: Path, help_text: str, accepted: bool
) -> None:
    towel = _fake_towel(tmp_path / "bin", help_text)
    assert ecosystem.accepts_cross_module(towel, tmp_path, {"PATH": "/usr/bin:/bin"}) is accepted


def test_a_towel_whose_help_fails_is_a_setup_failure(tmp_path: Path) -> None:
    towel = _fake_towel(tmp_path / "bin", "Traceback: broken", status=1)
    with pytest.raises(ecosystem.EnvironmentFailure, match="dry --help failed"):
        ecosystem.accepts_cross_module(towel, tmp_path, {"PATH": "/usr/bin:/bin"})


@pytest.mark.parametrize(
    "enabled,reason,accepted,arguments,record",
    [
        (True, "", True, ("--cross-module",), (True, True, "--cross-module passed")),
        (True, "", False, (), (True, True, "has no --cross-module option")),
        (False, "hangs its suite", True, (), (False, False, "hangs its suite")),
        (False, "hangs its suite", False, (), (False, True, "turns it off (hangs its suite)")),
    ],
)
def test_every_refactor_extracts_across_modules_unless_the_manifest_says_why_not(
    enabled: bool,
    reason: str,
    accepted: bool,
    arguments: Tuple[str, ...],
    record: Tuple[bool, bool, str],
) -> None:
    project = ecosystem.Project(
        "fixture", "unused", "pinned", "p.py", cross_module=enabled, cross_module_reason=reason
    )
    passed, cross_module = ecosystem.cross_module_arguments(project, accepted)
    assert passed == arguments
    assert (cross_module.requested, cross_module.enabled) == record[:2]
    assert record[2] in cross_module.reason


@pytest.mark.parametrize(
    "fields,message",
    [
        ({"cross_module": False}, "needs a cross_module_reason"),
        ({"cross_module": False, "cross_module_reason": "  "}, "needs a cross_module_reason"),
        ({"cross_module_reason": "stale"}, "is not turned off"),
        ({"cross_module": "no"}, "must be a boolean"),
    ],
)
def test_a_manifest_entry_cannot_turn_cross_module_off_without_a_reason(
    tmp_path: Path, fields: Dict[str, object], message: str
) -> None:
    manifest = tmp_path / "manifest.toml"
    extra = "".join(f"{key} = {json.dumps(value)}\n" for key, value in fields.items())
    manifest.write_text(
        '[[project]]\nname = "fixture"\nurl = "unused"\nrev = "pinned"\n'
        f'package = "p.py"\n{extra}'
    )
    with pytest.raises(ValueError, match=message):
        ecosystem.load_manifest(manifest, [])


def test_the_corpus_turns_cross_module_off_for_no_project() -> None:
    """Turning it off is a recorded decision; this pins that none has been made yet."""
    projects = ecosystem.load_manifest(ecosystem.REPO / "scripts/ecosystem/manifest.toml", [])
    assert [project.name for project in projects if not project.cross_module] == []


def test_every_project_is_installed_unless_its_entry_says_why_not() -> None:
    """An editable install of the project is how its developers have it, and what Towel's
    import model expects. The exceptions cannot be built, or compile an extension into the
    tree that the suite would exercise in place of the Python code Towel changes."""
    manifest = ecosystem.REPO / "scripts/ecosystem/manifest.toml"
    projects = ecosystem.load_manifest(manifest, [])
    assert [project.name for project in projects if not project.install] == [
        "wrapt",
        "peewee",
        "tornado",
        "markupsafe",
        "html5lib",
        "pyrsistent",
    ]
    lines = manifest.read_text().splitlines()
    for index, line in enumerate(lines):
        if line == "install = false":
            assert lines[index - 1].startswith("# "), f"line {index + 1} gives no reason"


CANDIDATE = ecosystem.Candidate(
    Path("code_towel-0-py3-none-any.whl"), "code-towel", "0", "0", ("mypy", "pyright"), ()
)


@dataclasses.dataclass
class Trace:
    """What ``check_project`` did, in order, with each step's particulars."""

    steps: List[str] = dataclasses.field(default_factory=list)
    tests: List[Path] = dataclasses.field(default_factory=list)
    installs: List[Path] = dataclasses.field(default_factory=list)
    helps: List[Tuple[Path, Path, bool]] = dataclasses.field(default_factory=list)
    """The ``towel`` asked, where, and whether PYTHONPATH was set."""
    refactors: List[Tuple[List[str], Path, bool]] = dataclasses.field(default_factory=list)
    """The command, where it ran, and whether PYTHONPATH was set."""


def _orchestrated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    distribution: Optional[str],
    accepted: bool = True,
    project: Optional[ecosystem.Project] = None,
) -> Tuple[ecosystem.Result, Trace]:
    """``check_project`` with its environment stubbed, tracing each step it takes."""
    work = tmp_path / "work"
    source = _source_tree(work / "fixture", {"package.py": "value = 1\n"})
    trace = Trace()
    record = ecosystem.Environment("3.12.0", "0", (ecosystem.Checker("mypy", "2.0", "project"),))
    if distribution is not None:
        record = dataclasses.replace(record, installed_from=str(source))
    installed = ecosystem.ProjectEnvironment(tmp_path / "env/bin/python", distribution, record)
    monkeypatch.setattr(ecosystem, "clone", lambda *_: "pinned")
    monkeypatch.setattr(ecosystem, "environment", lambda *_: installed)
    monkeypatch.setattr(ecosystem, "base_env", lambda *_: {"PATH": "x", "PYTHONPATH": "."})
    monkeypatch.setattr(ecosystem, "changed", lambda *_: (1, "fixture"))

    def install_from(
        environment: ecosystem.ProjectEnvironment,
        name: str,
        tree: Path,
        candidate: ecosystem.Candidate,
        log: Path,
    ) -> ecosystem.Environment:
        trace.steps.append("install")
        trace.installs.append(tree)
        return dataclasses.replace(environment.record, installed_from=str(tree))

    def accepts(towel: Path, cwd: Path, env: Mapping[str, str]) -> bool:
        trace.steps.append("help")
        trace.helps.append((towel, cwd, "PYTHONPATH" in env))
        return accepted

    def run(
        command: Sequence[str], cwd: Path, env: Dict[str, str], timeout: int, log: Path
    ) -> ecosystem.Phase:
        log.parent.mkdir(parents=True, exist_ok=True)
        if Path(command[0]).name == "towel":
            trace.steps.append("refactor")
            trace.refactors.append((list(command), cwd, "PYTHONPATH" in env))
            output = Path(command[command.index("dry") + 2])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("value = 2\n")
            log.write_text("Applied 1 refactoring\n")
            return ecosystem.Phase(0, 0.0, "Applied 1 refactoring", str(log))
        trace.steps.append("test")
        trace.tests.append(cwd)
        log.write_text("3 passed in 0.01s\n")
        return ecosystem.Phase(0, 0.0, "3 passed", str(log))

    monkeypatch.setattr(ecosystem, "install_from", install_from)
    monkeypatch.setattr(ecosystem, "accepts_cross_module", accepts)
    monkeypatch.setattr(ecosystem, "run", run)
    project = project or ecosystem.Project("fixture", "unused", "pinned", "package.py")
    return ecosystem.check_project(project, work, CANDIDATE, 10), trace


def test_towel_runs_from_the_project_environment_on_the_tree_it_refactors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, trace = _orchestrated(tmp_path, monkeypatch, distribution="fixture")
    ready = tmp_path / "work/fixture-ready"
    towel = tmp_path / "env/bin/towel"
    # The baseline tests the clone as installed; then the installation moves to the
    # copy Towel refactors, before Towel runs, and stays there for the run after.
    assert trace.steps == ["test", "install", "help", "refactor", "test"]
    assert trace.tests == [tmp_path / "work/fixture", ready]
    assert trace.installs == [ready]
    # Towel is the environment's own, run from the project root, with no PYTHONPATH.
    assert trace.helps == [(towel, ready, False)]
    [(command, cwd, pythonpath)] = trace.refactors
    assert command[:3] == [str(towel), "dry", "package.py"] and "--cross-module" in command
    assert cwd == ready and pythonpath is False
    assert result.verdict == "PASS"
    assert result.environment is not None and result.environment.installed_from == str(ready)
    assert result.cross_module == ecosystem.CrossModule(True, True, "--cross-module passed")


def test_a_project_that_is_not_installed_is_not_moved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, trace = _orchestrated(tmp_path, monkeypatch, distribution=None, accepted=False)
    assert trace.steps == ["test", "help", "refactor", "test"]
    [(command, _, _)] = trace.refactors
    assert "--cross-module" not in command
    assert result.environment is not None and result.environment.installed_from == ""
    assert result.cross_module is not None and result.cross_module.enabled
    assert "extracts across modules by default" in result.cross_module.reason


def test_a_project_that_turns_cross_module_off_is_refactored_without_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = ecosystem.Project(
        "fixture", "unused", "pinned", "package.py", cross_module=False, cross_module_reason="why"
    )
    result, trace = _orchestrated(tmp_path, monkeypatch, distribution=None, project=project)
    [(command, _, _)] = trace.refactors
    assert "--cross-module" not in command
    assert result.cross_module == ecosystem.CrossModule(False, False, "why")


def executor(max_workers: int, initializer: Callable[[], None]) -> ThreadPoolExecutor:
    """The harness's worker pool, in threads: the stubbed workers run no project code."""
    return ThreadPoolExecutor(max_workers=max_workers)


def _main_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, results: Mapping[str, ecosystem.Result]
) -> int:
    """``main`` over stubbed projects whose results are ``results``."""
    projects = [ecosystem.Project(name, "unused", "pinned", "p.py") for name in results]
    monkeypatch.setattr(ecosystem, "load_manifest", lambda *_: projects)
    monkeypatch.setattr(ecosystem, "_lock_work_directory", lambda _: 0)
    monkeypatch.setattr(ecosystem, "prepare_candidate", lambda *_: CANDIDATE)
    monkeypatch.setattr(ecosystem, "source_revision", lambda _: ("0" * 40, ""))
    monkeypatch.setattr(ecosystem, "check_project", lambda project, *_: results[project.name])
    monkeypatch.setattr(concurrent.futures, "ProcessPoolExecutor", executor)
    monkeypatch.setattr(
        sys, "argv", ["ecosystem_check.py", "--run-untrusted-code", "--work", str(tmp_path)]
    )
    return ecosystem.main()


def test_the_summary_records_the_candidate_and_what_each_project_ran_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkers = (
        ecosystem.Checker("mypy", "1.19.1", "project"),
        ecosystem.Checker("pyright", "1.1.414", "towel[types]"),
    )
    environment = ecosystem.Environment("3.12.14", "0", checkers, "/work/fixture-ready")
    result = ecosystem.Result("fixture", "PASS", environment=environment)
    assert _main_with(tmp_path, monkeypatch, {"fixture": result}) == 0
    summary = json.loads((tmp_path / "report/summary.json").read_text())
    assert summary["candidate"] == {
        "wheel": CANDIDATE.wheel.name,
        "distribution": "code-towel",
        "version": "0",
        "sha256": "0",
    }
    recorded = summary["results"][0]["environment"]
    assert recorded["installed_from"] == "/work/fixture-ready"
    assert recorded["checkers"][0] == {"name": "mypy", "version": "1.19.1", "source": "project"}
    project = json.loads((tmp_path / "report/fixture.json").read_text())
    assert project["environment"] == recorded
    markdown = (tmp_path / "report/summary.md").read_text()
    assert "| mypy 1.19.1 (project), pyright 1.1.414 |" in markdown


def test_the_summary_names_every_project_refactored_without_cross_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    results = {
        "kept": ecosystem.Result(
            "kept", "PASS", cross_module=ecosystem.CrossModule(True, True, "--cross-module passed")
        ),
        "dropped": ecosystem.Result(
            "dropped", "PASS", cross_module=ecosystem.CrossModule(False, False, "its suite hangs")
        ),
    }
    assert _main_with(tmp_path, monkeypatch, results) == 0
    summary = json.loads((tmp_path / "report/summary.json").read_text())
    assert summary["cross_module_off"] == {"dropped": "its suite hangs"}
    assert summary["results"][0]["cross_module"]["reason"] == "its suite hangs"
    markdown = (tmp_path / "report/summary.md").read_text()
    assert "Cross-module extraction off: dropped (its suite hangs)" in markdown


# -- A retest of the original runs the original, installed copy included --------


def _trees(root: Path) -> Tuple[Path, Path]:
    source = _source_tree(root / "source", {"pkg/mod.py": "original\n"})
    ready = _source_tree(root / "ready", {"pkg/mod.py": "adopted\n"})
    return source, ready


def test_the_original_package_is_held_where_the_project_is_installed(tmp_path: Path) -> None:
    source, ready = _trees(tmp_path)
    aside = tmp_path / "aside"
    with ecosystem.original_installed(ready / "pkg", source / "pkg", aside):
        assert (ready / "pkg/mod.py").read_text() == "original\n"
        assert (aside / "mod.py").read_text() == "adopted\n"
    assert (ready / "pkg/mod.py").read_text() == "adopted\n" and not aside.exists()
    with pytest.raises(RuntimeError):
        with ecosystem.original_installed(ready / "pkg", source / "pkg", aside):
            raise RuntimeError("the run failed")
    assert (ready / "pkg/mod.py").read_text() == "adopted\n" and not aside.exists()


def test_each_retest_of_the_original_sees_the_original_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A regression in a test that imports the installed copy must not pass as flaky.

    With Towel's output installed on both sides, such a test fails before and after a
    retest alike, and the difference it showed would be excused as flakiness.
    """
    source, ready = _trees(tmp_path)
    seen: List[Tuple[Path, str]] = []
    failing = "FAILED test_case.py::test_a\n1 failed, 1 passed in 0.01s\n"
    phases = iter(
        [(1, "FAILED test_case.py::test_a\n1 failed in 0.01s\n")] * 2 + [(1, failing)] * 2
    )

    def run(
        command: Sequence[str], cwd: Path, env: Dict[str, str], timeout: int, log: Path
    ) -> ecosystem.Phase:
        seen.append((cwd, (ready / "pkg/mod.py").read_text()))
        code, output = next(phases)
        log.write_text(output)
        return ecosystem.Phase(code, 0.0, ecosystem.summarize(output), str(log))

    monkeypatch.setattr(ecosystem, "run", run)
    initial = tmp_path / "initial.log"
    initial.write_text(failing)
    phase = ecosystem.Phase(1, 0.0, "1 failed, 1 passed", str(initial))
    original: Callable[[], ContextManager[None]] = functools.partial(
        ecosystem.original_installed, ready / "pkg", source / "pkg", tmp_path / "aside"
    )
    assert ecosystem._retest_agrees(
        ecosystem._prepare_test_command([sys.executable, "-m", "pytest", "-q"]),
        ["test_case.py::test_a"],
        source,
        ready,
        {},
        10,
        tmp_path,
        ecosystem.Project("fixture", "unused", "pinned", "pkg"),
        phase,
        phase,
        original_installed=original,
    )
    assert seen == [
        (source, "original\n"),
        (ready, "adopted\n"),
        (source, "original\n"),
        (ready, "adopted\n"),
    ]
    assert (ready / "pkg/mod.py").read_text() == "adopted\n"
