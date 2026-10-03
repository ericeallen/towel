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

"""Checker contexts choose real versions, and stale environments cannot bypass them."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
import subprocess
import sys
from typing import Mapping, Optional, Sequence

import pytest

from scripts import ecosystem_check as ecosystem
from tests.ecosystem_fixtures import candidate_wheel, project_tree, uv_required


def _tox(requirements: str) -> str:
    return "[testenv:type]\ncommands = mypy\ndeps =\n" + "".join(
        f"    {line}\n" for line in requirements.splitlines()
    )


def _configured(
    tmp_path: Path, config: str = ""
) -> tuple[Path, Path, ecosystem.Candidate, ecosystem.Project]:
    work = tmp_path / "work"
    tree = project_tree(work / "sample", "sample", "sample", "source")
    if config:
        (tree / "tox.ini").write_text(config)
    candidate = ecosystem.load_candidate(candidate_wheel(tmp_path / "dist"))
    return work, tree, candidate, ecosystem.Project("sample", "unused", "pinned", "sample")


@pytest.mark.parametrize(
    "filename,text,context",
    [
        ("tox.ini", _tox("mypy==1.0"), "tox.ini [testenv:type]"),
        ("tox.ini", _tox("mypy>=1,<2"), "tox.ini [testenv:type]"),
        (
            "pyproject.toml",
            '[dependency-groups]\nlinting = ["mypy>=1,<2"]\n',
            "pyproject.toml [dependency-groups] linting",
        ),
        (
            "noxfile.py",
            "import nox\n@nox.session\ndef typing(session):\n"
            '    session.install("mypy>=1,<2")\n    session.run("mypy")\n',
            "noxfile.py session typing",
        ),
    ],
)
@uv_required
def test_a_checker_uses_the_first_versioned_typing_context(
    tmp_path: Path, offline_index: Path, filename: str, text: str, context: str
) -> None:
    work, tree, candidate, project = _configured(tmp_path)
    with (tree / filename).open("a") as handle:
        handle.write(text)
    result = ecosystem.environment(project, work, tree, candidate, tmp_path / "log")
    checker = result.record.checkers[0]
    assert (checker.version, checker.source) == ("1.0.0", f"typing context: {context}")
    recorded = ecosystem._read_provenance(work / "sample-env/towel-tools.json")
    assert recorded is not None
    assert recorded["mypy"].version == "1.0.0"
    assert "mypy>=1.0" in recorded["mypy"].requirements
    marker = work / "sample-env/reused"
    marker.write_text("keep")
    again = ecosystem.environment(project, work, tree, candidate, tmp_path / "log")
    assert marker.read_text() == "keep"
    assert again.record == result.record


@uv_required
def test_precommit_and_tox_are_alternative_checker_contexts(
    tmp_path: Path, offline_index: Path
) -> None:
    work, tree, candidate, project = _configured(tmp_path, _tox("mypy==1.0"))
    (tree / ".pre-commit-config.yaml").write_text(
        "repos:\n  - repo: https://github.com/pre-commit/mirrors-mypy\n    rev: v2.0.0\n"
    )
    result = ecosystem.environment(project, work, tree, candidate, tmp_path / "log")
    assert result.record.checkers[0] == ecosystem.Tool("mypy", "2.0.0", ".pre-commit-config.yaml")
    assert result.record.typing.requirements == (
        ecosystem.TypingRequirement(
            "mypy==1.0",
            "tox.ini [testenv:type]",
            skipped="Towel's tool selection holds mypy 2.0.0, selected by .pre-commit-config.yaml",
        ),
    )


def test_a_false_marker_cannot_select_an_earlier_context(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[dependency-groups]\ntyping = [\"mypy==2; python_version < '3'\"]\n"
    )
    (tmp_path / "tox.ini").write_text(_tox("mypy>=1,<2\nmypy>=9; python_version < '3'"))
    choices = ecosystem._typing_tool_choices(Path(sys.executable), tmp_path, ["mypy"])
    assert choices["mypy"] == ecosystem.Choice(
        ecosystem.TypingContext("typing context: tox.ini [testenv:type]"),
        requirements=("mypy>=1,<2",),
    )


@pytest.mark.parametrize("excluded", [True, False])
@uv_required
def test_an_extra_inherits_the_marker_of_the_requirement_that_reached_it(
    tmp_path: Path, offline_index: Path, excluded: bool
) -> None:
    work, tree, candidate, project = _configured(tmp_path)
    marker = "python_version < '3'" if excluded else "python_version >= '3'"
    with (tree / "pyproject.toml").open("a") as handle:
        handle.write(
            "[project.optional-dependencies]\n"
            'outer = ["sample[inner]"]\ninner = ["mypy==1"]\n'
            f'[dependency-groups]\ntyping = ["sample[outer]; {marker}"]\n'
        )
    result = ecosystem.environment(project, work, tree, candidate, tmp_path / "log")
    assert result.record.checkers[0].version == ("2.0.0" if excluded else "1.0.0")
    record = next(item for item in result.record.typing.requirements if item.declared == "mypy==1")
    if excluded:
        assert record.skipped == "its marker excludes this environment"
    else:
        assert (
            "selected by typing context: pyproject.toml [dependency-groups] typing"
            in record.skipped
        )


@uv_required
def test_a_tox_context_combines_its_direct_and_group_requirements(
    tmp_path: Path, offline_index: Path
) -> None:
    config = _tox("mypy>=1") + "dependency_groups = bounds\n"
    work, tree, candidate, project = _configured(tmp_path, config)
    with (tree / "pyproject.toml").open("a") as handle:
        handle.write('[dependency-groups]\nbounds = ["mypy<2"]\n')
    result = ecosystem.environment(project, work, tree, candidate, tmp_path / "log")
    assert result.record.checkers[0].version == "1.0.0"
    assert ecosystem._typing_tool_choices(Path(sys.executable), tree, ["mypy"])[
        "mypy"
    ].requirements == (
        "mypy>=1",
        "mypy<2",
    )


@pytest.mark.parametrize("requirements", ["mypy<1", "mypy==1\nmypy>=2"])
@uv_required
def test_an_impossible_selected_context_is_refused_not_replaced_with_the_extra(
    tmp_path: Path, offline_index: Path, requirements: str
) -> None:
    work, tree, candidate, project = _configured(tmp_path, _tox(requirements))
    with pytest.raises(subprocess.CalledProcessError):
        ecosystem.environment(project, work, tree, candidate, tmp_path / "log")
    assert not (work / "sample-env/towel-deps.txt").exists()
    log = (tmp_path / "log").read_text()
    assert "mypy>=1.0" in log
    assert "No solution found" in log


@uv_required
def test_different_typing_contexts_do_not_form_an_artificial_conflict(
    tmp_path: Path, offline_index: Path
) -> None:
    config = _tox("mypy==1") + "\n[testenv:other]\ncommands = mypy\ndeps = mypy>=2\n"
    work, tree, candidate, project = _configured(tmp_path, config)
    result = ecosystem.environment(project, work, tree, candidate, tmp_path / "log")
    assert result.record.checkers[0].version == "1.0.0"
    assert [item.declared for item in result.record.typing.requirements] == ["mypy==1", "mypy>=2"]
    assert all(
        "selected by typing context: tox.ini [testenv:type]" in item.skipped
        for item in result.record.typing.requirements
    )


@pytest.mark.parametrize(
    "change",
    [
        "configuration",
        "layout",
        "installed-version",
        "recorded-constraint",
        "revision",
        "interpreter",
    ],
)
@uv_required
def test_reuse_requires_current_inputs_and_the_verified_selected_tools(
    tmp_path: Path, offline_index: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    work, tree, candidate, project = _configured(tmp_path, _tox("mypy==1"))
    log = tmp_path / "log"
    result = ecosystem.environment(project, work, tree, candidate, log)
    marker = work / "sample-env/reused"
    marker.write_text("stale")
    expected = "1.0.0"
    if change == "configuration":
        (tree / "tox.ini").write_text(_tox("mypy==2"))
        expected = "2.0.0"
    elif change in ("layout", "interpreter"):
        fingerprint = work / "sample-env/towel-deps.txt"
        text = fingerprint.read_text()
        text = (
            text.replace("layout=7", "layout=6")
            if change == "layout"
            else text.replace("interpreter=", "interpreter=older ")
        )
        fingerprint.write_text(text)
    elif change == "installed-version":
        ecosystem._install(result.python, log, "mypy==2")
    elif change == "recorded-constraint":
        provenance = work / "sample-env/towel-tools.json"
        data = json.loads(provenance.read_text())
        data["mypy"]["requirements"] = ["mypy>=2"]
        provenance.write_text(json.dumps(data))
    else:
        project = dataclasses.replace(project, rev="another-pin")
    again = ecosystem.environment(project, work, tree, candidate, log)
    assert not marker.exists()
    assert again.record.checkers[0].version == expected
    assert again.record.checkers[0].source == "typing context: tox.ini [testenv:type]"


def test_selection_fingerprint_includes_requirements_reached_outside_typing_files(
    tmp_path: Path,
) -> None:
    (tmp_path / "requirements.txt").write_text("-r included\n")
    (tmp_path / "included").write_text("mypy==1\n")
    before = ecosystem._selection_inputs(tmp_path)
    (tmp_path / "included").write_text("mypy==2\n")
    assert ecosystem._selection_inputs(tmp_path) != before


def test_install_success_is_not_evidence_that_the_selected_requirement_was_met(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, tree, candidate, _ = _configured(tmp_path, _tox("mypy==1"))
    versions: Mapping[str, Optional[str]] = {
        "mypy": "2.0.0",
        "pyright": "1.1.0",
        "black": "26.5.1",
        "isort": "9.0.1",
        "ruff": "0.16.0",
    }
    probes = iter(({}, versions))

    def probe(python: Path, names: Sequence[str]) -> ecosystem.EnvironmentProbe:
        return ecosystem.EnvironmentProbe("3.13", dict(next(probes)), ())

    monkeypatch.setattr(ecosystem, "probe_environment", probe)
    monkeypatch.setattr(ecosystem, "_install", lambda *_: "success")
    with pytest.raises(ecosystem.EnvironmentFailure, match="mypy 2.0.0 fails mypy==1"):
        ecosystem._install_tools(Path(sys.executable), candidate, tree, tmp_path / "log")
