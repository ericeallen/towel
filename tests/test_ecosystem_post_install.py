# Copyright 2025-2026 Eric Allen
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Prepared test data must exist in both roles, with setup failures stopping before baseline."""

import dataclasses
import hashlib
import json
import subprocess
from pathlib import Path
import sys
from typing import Dict, List, Sequence, Tuple

import pytest

from scripts import ecosystem_check as ecosystem
from tests.test_ecosystem_environment import CANDIDATE

MANIFEST = Path(__file__).resolve().parents[1] / "scripts/ecosystem/manifest.toml"


def dateutil() -> ecosystem.Project:
    return ecosystem.load_manifest(MANIFEST, ["python-dateutil"])[0]


def test_new_optional_fields_preserve_existing_positional_constructor_meaning() -> None:
    project = ecosystem.Project(
        "fixture", "unused", "pinned", "pkg", ".", (), (), "git command", False
    )
    assert (
        project.prepare == "git command" and project.install is False and project.post_install == ()
    )
    phase = ecosystem.Phase(0, 1.0, "passed", "log")
    result = ecosystem.Result("fixture", "PASS", "pinned", phase)
    assert result.baseline is phase and result.post_install is None


def test_dateutil_retains_its_declared_profile_and_compatible_pytest() -> None:
    project = dateutil()
    assert project.test == ("{python}", "-m", "pytest", "-q", "--ignore=tests/property")
    assert 'pytest==8.4.2; python_version != "3.3"' in project.deps
    assert "six" in project.deps
    assert project.post_install[:2] == ("{python}", "-c")
    assert "2024a" not in project.post_install[2]  # The pinned metadata names the payload.
    tomlkit = ecosystem.load_manifest(MANIFEST, ["tomlkit"])[0]
    assert tomlkit.prepare == "git submodule update --init --quiet"
    assert tomlkit.post_install == ()


def test_clone_time_git_prepare_stays_separate_from_post_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    commands: List[Tuple[Sequence[str], Path | None]] = []

    def git(
        command: Sequence[str],
        *,
        check: bool,
        capture_output: bool,
        cwd: Path | None = None,
        text: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        assert check and capture_output
        commands.append((command, cwd))
        return subprocess.CompletedProcess(command, 0, "pinned\n", "")

    monkeypatch.setattr(subprocess, "run", git)
    project = ecosystem.Project(
        "fixture",
        "unused",
        "pinned",
        "pkg",
        prepare="git submodule update --init --quiet",
        post_install=("{python}", "builder.py"),
    )
    assert ecosystem.clone(project, source, tmp_path) == "pinned"
    assert commands == [
        (["git", "-C", str(source), "checkout", "-q", "pinned"], None),
        (["git", "submodule", "update", "--init", "--quiet"], source),
        (["git", "-C", str(source), "rev-parse", "HEAD"], None),
    ]


@pytest.mark.parametrize("value", ['"python prepare.py"', '["python", ""]', '["python", 1]'])
def test_post_install_rejects_non_argv_manifest_values(tmp_path: Path, value: str) -> None:
    manifest = tmp_path / "manifest.toml"
    manifest.write_text(
        '[[project]]\nname="fixture"\nurl="unused"\npackage="pkg"\npost_install=' + value
    )
    with pytest.raises(ValueError, match="post_install must be an argv array"):
        ecosystem.load_manifest(manifest, [])


def prepared_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: Tuple[str, ...], *, timeout: int = 5
) -> Tuple[ecosystem.Result, List[str]]:
    work = tmp_path / "work"
    source = work / "fixture"
    source.mkdir(parents=True, exist_ok=True)
    (source / "package.py").write_text("value = 1\n")
    steps: List[str] = []
    python = Path(sys.executable)
    record = ecosystem.Environment("selected", "candidate", (), ())

    def clone(*_: object) -> str:
        steps.append("clone")
        return "pinned"

    def environment(*_: object) -> ecosystem.ProjectEnvironment:
        steps.append("environment")
        (source / "installed").write_text("selected dependencies ready")
        return ecosystem.ProjectEnvironment(python, None, record)

    actual_run = ecosystem.run

    def run(
        argv: Sequence[str],
        cwd: Path,
        env: Dict[str, str],
        budget: int,
        log: Path,
        *,
        header: str = "",
    ) -> ecosystem.Phase:
        if log.name.endswith("-post-install.log"):
            steps.append("prepare")
            assert env["PATH"].split(":")[0] == str(python.parent)
            assert env["PYTHONPATH"] == "."
            return actual_run(argv, cwd, env, budget, log, header=header)
        if Path(argv[0]).name == "towel":
            steps.append("refactor")
            output = Path(argv[argv.index("dry") + 2])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("value = 2\n")
            log.write_text("Applied 1 refactoring\n")
            return ecosystem.Phase(0, 0.0, "Applied 1 refactoring", str(log))
        steps.append("baseline" if cwd == source else "after")
        assert (cwd / "archive").read_bytes() == b"prepared bytes"
        assert (cwd / "selected-python").read_text() == str(python)
        log.write_text("3 passed in 0.01s\n")
        return ecosystem.Phase(0, 0.0, "3 passed", str(log))

    monkeypatch.setattr(ecosystem, "clone", clone)
    monkeypatch.setattr(ecosystem, "environment", environment)
    monkeypatch.setattr(ecosystem, "accepts_cross_module", lambda *_: True)
    monkeypatch.setattr(ecosystem, "changed", lambda *_: (1, "fixture"))
    monkeypatch.setattr(ecosystem, "run", run)
    project = ecosystem.Project("fixture", "unused", "pinned", "package.py", post_install=command)
    return ecosystem.check_project(project, work, CANDIDATE, timeout), steps


def test_preparation_uses_selected_python_after_install_and_copies_data_to_both_roles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = (
        "{python}",
        "-c",
        "import sys; from pathlib import Path; assert Path('installed').exists(); "
        "Path('selected-python').write_text(sys.executable); "
        "Path('archive').write_bytes(b'prepared bytes'); print({'prepared': True})",
    )
    result, steps = prepared_run(tmp_path, monkeypatch, command)
    assert result.verdict == "PASS"
    assert steps == ["clone", "environment", "prepare", "baseline", "refactor", "after"]
    assert result.post_install is not None
    assert result.post_install.command == (sys.executable, *command[1:])
    assert result.post_install.phase.returncode == 0
    log = Path(result.post_install.phase.log).read_text()
    assert json.dumps(result.post_install.command) in log and "{'prepared': True}" in log
    receipt = dataclasses.asdict(result)["post_install"]
    assert receipt["command"][0] == sys.executable and receipt["phase"]["returncode"] == 0
    original = tmp_path / "work/fixture/archive"
    ready = tmp_path / "work/fixture-ready/archive"
    assert (
        hashlib.sha256(original.read_bytes()).digest()
        == hashlib.sha256(ready.read_bytes()).digest()
    )


@pytest.mark.parametrize(
    "command,timeout,code,detail",
    [
        (
            ("{python}", "-c", "print('preparation failed', flush=True); raise SystemExit(7)"),
            5,
            7,
            "preparation failed",
        ),
        (
            ("{python}", "-c", "import time; print('started', flush=True); time.sleep(20)"),
            1,
            -9,
            "TIMEOUT",
        ),
        (("/nonexistent/towel-prepare-command",), 5, 127, "could not start"),
    ],
)
def test_preparation_failure_has_receipt_and_stops_before_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: Tuple[str, ...],
    timeout: int,
    code: int,
    detail: str,
) -> None:
    result, steps = prepared_run(tmp_path, monkeypatch, command, timeout=timeout)
    assert result.verdict == "SETUP_ERROR"
    assert steps == ["clone", "environment", "prepare"]
    assert result.baseline is result.refactor is result.after is None
    assert result.post_install is not None and result.post_install.phase.returncode == code
    assert detail in Path(result.post_install.phase.log).read_text()
    assert "post-install preparation failed" in result.detail
    assert not (tmp_path / "work/fixture-ready").exists()


@pytest.mark.parametrize("payload", [None, b"corrupt"])
def test_dateutil_missing_or_bad_payload_cannot_enter_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: bytes | None
) -> None:
    work = tmp_path / "work/fixture"
    work.mkdir(parents=True)
    (work / "zonefile_metadata.json").write_text(
        json.dumps(
            {
                "tzdata_file": "metadata-selected.tar.gz",
                "tzdata_file_sha512": hashlib.sha512(b"valid").hexdigest(),
            }
        )
    )
    # A minimal upstream-shaped builder pins the boundary: the hook must not run
    # its downloader branch, and must propagate its hash failure without baseline.
    (work / "updatezinfo.py").write_text(
        "import hashlib, json\nfrom pathlib import Path\n"
        "m = json.loads(Path('zonefile_metadata.json').read_text())\n"
        "p = Path(m['tzdata_file'])\n"
        "if not p.exists():\n    Path('download-attempted').touch()\n"
        "assert hashlib.sha512(p.read_bytes()).hexdigest() == m['tzdata_file_sha512'], 'SHA failed'\n"
    )
    if payload is not None:
        (work / "metadata-selected.tar.gz").write_bytes(payload)
    # prepared_run creates this root itself; keep it reusable for pre-staged data.
    result, _ = prepared_run(tmp_path, monkeypatch, dateutil().post_install)
    assert result.verdict == "SETUP_ERROR" and result.baseline is None
    assert not (work / "download-attempted").exists()
    assert result.post_install is not None
    output = Path(result.post_install.phase.log).read_text()
    assert ("FileNotFoundError" if payload is None else "SHA failed") in output
