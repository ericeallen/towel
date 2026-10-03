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

"""Literal workflow checker pins remain bounded, contextual and visible to environment reuse."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from scripts import ecosystem_check as ecosystem
from tests.ecosystem_fixtures import candidate_wheel, project_tree, uv_required


def _workflow(tree: Path, jobs: str) -> Path:
    path = tree / ".github/workflows/check.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("name: checks\non: push\njobs:\n" + jobs)
    return path


def _job(name: str, script: str, *, condition: str = "", step_fields: str = "") -> str:
    return (
        f"  {name}:\n    runs-on: ubuntu-latest\n"
        + (f"    if: {condition}\n" if condition else "")
        + "    steps:\n    - name: Install checker\n      run: |\n"
        + "".join("        " + line + "\n" for line in script.splitlines())
        + step_fields
    )


def test_wrapt_workflow_installs_preserve_markers_and_job_context(tmp_path: Path) -> None:
    _workflow(
        tmp_path,
        _job(
            "linux",
            """python -m pip install "mypy==1.19.1; python_version == '3.9'"
python -m pip install "mypy==1.20.1; python_version >= '3.10'"
""",
        ),
    )
    choices = ecosystem._typing_tool_choices(Path(sys.executable), tmp_path, ["mypy"])
    assert choices["mypy"].requirements == ("mypy==1.20.1; python_version >= '3.10'",)
    assert choices["mypy"].source == "typing context: .github/workflows/check.yml job linux"


@pytest.mark.parametrize(
    "script",
    [
        '# python -m pip install "mypy==1"',
        "echo 'python -m pip install mypy==1'",
        "cat <<EOF\npython -m pip install mypy==1\nEOF",
        "if false; then\npython -m pip install mypy==1\nfi",
        "false && python -m pip install mypy==1",
        "python -m pip install mypy==1 || true",
        "python -m pip install mypy==1 > output",
        'python -m pip install "mypy==$VERSION"',
        'python -m pip install "mypy==${{ matrix.mypy }}"',
        'python -m pip install "mypy==`echo 1`"',
        'python -m pip install "mypy==%VERSION%"',
        "python -m pip install --requirement mypy==1",
        "python -m pip install --constraint mypy==1",
        "python -m pip install mypy==1.*",
        'python -m pip install "mypy==1"\necho done',
        'python -m pip install "mypy==1',
    ],
)
def test_shell_text_that_is_not_a_literal_install_cannot_select_a_checker(
    tmp_path: Path, script: str
) -> None:
    _workflow(tmp_path, _job("check", script))
    assert ecosystem.typing_declarations(tmp_path, None) == []


@pytest.mark.parametrize("condition", ["false", "${{ false }}", "matrix.enabled", "success()"])
@pytest.mark.parametrize("where", ["job", "step"])
def test_conditional_installs_do_not_become_winning_contexts(
    tmp_path: Path, condition: str, where: str
) -> None:
    kwargs = (
        {"condition": condition} if where == "job" else {"step_fields": f"      if: {condition}\n"}
    )
    _workflow(
        tmp_path,
        _job("inactive", "python -m pip install mypy==1", **kwargs)
        + _job("active", "pip install mypy==2"),
    )
    declarations = ecosystem.typing_declarations(tmp_path, None)
    assert [(item.requirement, item.context) for item in declarations] == [
        ("mypy==2", ".github/workflows/check.yml job active")
    ]


@pytest.mark.parametrize("shell", ["python", "pwsh", "${{ matrix.shell }}"])
def test_uninterpreted_step_shells_are_not_read_as_shell_installs(
    tmp_path: Path, shell: str
) -> None:
    _workflow(tmp_path, _job("check", "pip install mypy==1", step_fields=f"      shell: {shell}\n"))
    assert ecosystem.typing_declarations(tmp_path, None) == []


@pytest.mark.parametrize("prefix", ["defaults:\n  run:\n    shell: python\n", ""])
def test_uninterpreted_default_shells_are_skipped(tmp_path: Path, prefix: str) -> None:
    path = _workflow(tmp_path, _job("check", "pip install mypy==1"))
    if prefix:
        path.write_text(prefix + path.read_text())
    else:
        path.write_text(
            path.read_text().replace(
                "    steps:", "    defaults:\n      run:\n        shell: python\n    steps:"
            )
        )
    assert ecosystem.typing_declarations(tmp_path, None) == []


@pytest.mark.parametrize(
    "run",
    [
        'pip install "mypy>=1,<2"',
        "'pip install mypy==1'",
        '"pip install mypy==1"',
    ],
)
def test_inline_literal_install_steps_are_read(tmp_path: Path, run: str) -> None:
    _workflow(tmp_path, f"  check:\n    steps:\n      - run: {run}\n")
    assert len(ecosystem.typing_declarations(tmp_path, None)) == 1


def test_workflow_jobs_are_alternatives_after_existing_contexts(tmp_path: Path) -> None:
    _workflow(
        tmp_path, _job("first", "pip install mypy==1") + _job("second", "pip install mypy==2")
    )
    assert ecosystem._typing_tool_choices(Path(sys.executable), tmp_path, ["mypy"])[
        "mypy"
    ].requirements == ("mypy==1",)
    (tmp_path / "tox.ini").write_text("[testenv:type]\ncommands = mypy\ndeps = mypy>=2\n")
    chosen = ecosystem._typing_tool_choices(Path(sys.executable), tmp_path, ["mypy"])["mypy"]
    assert chosen.source == "typing context: tox.ini [testenv:type]"
    assert chosen.requirements == ("mypy>=2",)


@uv_required
def test_workflow_selection_is_installed_recorded_and_invalidates_reuse(
    tmp_path: Path, offline_index: Path
) -> None:
    work = tmp_path / "work"
    tree = project_tree(work / "sample", "sample", "sample", "source")
    path = _workflow(tree, _job("type", "python -m pip install mypy==1.0"))
    candidate = ecosystem.load_candidate(candidate_wheel(tmp_path / "dist"))
    project = ecosystem.Project("sample", "unused", "pinned", "sample")
    result = ecosystem.environment(project, work, tree, candidate, tmp_path / "log")
    assert result.record.checkers[0] == ecosystem.Tool(
        "mypy",
        "1.0.0",
        ecosystem.TypingContext("typing context: .github/workflows/check.yml job type"),
    )
    recorded = json.loads((work / "sample-env/towel-tools.json").read_text())
    assert "mypy==1.0" in recorded["mypy"]["requirements"]
    fingerprint = ecosystem._selection_inputs(tree)
    path.write_text(path.read_text().replace("mypy==1.0", "mypy==2.0"))
    assert ecosystem._selection_inputs(tree) != fingerprint
    again = ecosystem.environment(project, work, tree, candidate, tmp_path / "log")
    assert again.record.checkers[0].version == "2.0.0"


@uv_required
def test_precommit_pin_precedes_a_workflow_alternative(tmp_path: Path, offline_index: Path) -> None:
    work = tmp_path / "work"
    tree = project_tree(work / "sample", "sample", "sample", "source")
    _workflow(tree, _job("type", "pip install mypy==1.0"))
    (tree / ".pre-commit-config.yaml").write_text(
        "repos:\n- repo: https://github.com/pre-commit/mirrors-mypy\n  rev: v2.0.0\n"
    )
    candidate = ecosystem.load_candidate(candidate_wheel(tmp_path / "dist"))
    result = ecosystem.environment(
        ecosystem.Project("sample", "unused", "pinned", "sample"),
        work,
        tree,
        candidate,
        tmp_path / "log",
    )
    assert result.record.checkers[0] == ecosystem.Tool("mypy", "2.0.0", ".pre-commit-config.yaml")
    assert any(
        item.declared == "mypy==1.0" and "selected by .pre-commit-config.yaml" in item.skipped
        for item in result.record.typing.requirements
    )


@pytest.mark.parametrize("where", ["job", "step"])
@pytest.mark.parametrize("key", ["if : false", "'if': false", '"if": false', "<<: *inactive"])
def test_alternate_yaml_keys_cannot_hide_a_condition(tmp_path: Path, where: str, key: str) -> None:
    jobs = _job("check", "pip install mypy==1")
    if where == "job":
        jobs = jobs.replace("    steps:", "    " + key + "\n    steps:")
    else:
        jobs += "      " + key + "\n"
    _workflow(tmp_path, jobs)
    assert ecosystem.typing_declarations(tmp_path, None) == []


@pytest.mark.parametrize(
    "field", ["      if: true\n      if: false\n", "      run: echo skipped\n"]
)
def test_duplicate_step_keys_are_not_interpreted(tmp_path: Path, field: str) -> None:
    _workflow(tmp_path, _job("check", "pip install mypy==1", step_fields=field))
    assert ecosystem.typing_declarations(tmp_path, None) == []


@pytest.mark.parametrize(
    "prefix",
    [
        '"defaults": {run: {shell: python}}\n',
        "'defaults': {run: {shell: python}}\n",
        "defaults : {run: {shell: python}}\n",
        "<<: *defaults\n",
        "jobs: {}\n",
        "---\n",
    ],
)
def test_unsupported_or_duplicate_root_fields_cannot_hide_a_shell(
    tmp_path: Path, prefix: str
) -> None:
    path = _workflow(tmp_path, _job("check", "pip install mypy==1"))
    path.write_text(prefix + path.read_text())
    assert ecosystem.typing_declarations(tmp_path, None) == []
