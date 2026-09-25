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

"""A checker the project configures but Towel cannot run refuses the typed run.

The project's own check is the promise a typed run keeps. Choosing another
checker in its place gives a verdict the project never asked for, and checking
with nothing at all is the silence the promise exists to prevent; both used to
print one line starting "Note:" and write the refactoring with exit status 0.
A project that configures no checker at all keeps the documented behaviour:
mypy when installed, else pyright, else copying only.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import List

import pytest

from towel import type_inference
from towel.type_inference import type_oracle_for_project
from tests.test_cli_integration import invoke

requires_mypy = pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")
requires_pyright = pytest.mark.skipif(
    importlib.util.find_spec("pyright") is None, reason="pyright absent"
)

BODY = "    total = value + 1\n    doubled = total * 2\n    answer = doubled - 3\n"
SOURCE = (
    'def first(value: int) -> int:\n    print("first")\n' + BODY + "    return answer\n\n\n"
    'def second(value: int) -> int:\n    print("second")\n' + BODY + "    return answer + 1\n"
)


def _without_pyright(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(type_inference, "_pyright_command", lambda: None)


class _AbsentMypy:
    def __init__(self) -> None:
        raise ImportError("mypy is not installed")


def _without_mypy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(type_inference, "MypyInferrer", _AbsentMypy)


def _project(root: Path, tools: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(tools, encoding="utf-8")
    path = root / "m.py"
    path.write_text(SOURCE, encoding="utf-8")
    return path


@requires_mypy
def test_both_configured_and_pyright_missing_refuses_naming_pyright(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _project(tmp_path, '[tool.mypy]\nstrict = true\n[tool.pyright]\nstrict = ["."]\n')
    _without_pyright(monkeypatch)
    closed: List[bool] = []
    original_close = type_inference.MypyInferrer.close

    def recording_close(self: type_inference.MypyInferrer) -> None:
        closed.append(True)
        original_close(self)

    monkeypatch.setattr(type_inference.MypyInferrer, "close", recording_close)
    with pytest.raises(type_inference.CheckerNotInstalled) as refused:
        type_oracle_for_project(path)
    message = str(refused.value)
    assert "pyright" in message and "mypy" not in message.split(" is configured")[0]
    assert str(tmp_path / "pyproject.toml") in message
    assert "--no-types" in message and "install" in message.lower()
    assert closed, "the mypy checker already started is released, not leaked"


def test_pyright_only_project_with_pyright_missing_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _project(tmp_path, '[tool.pyright]\ntypeCheckingMode = "basic"\n')
    _without_pyright(monkeypatch)
    with pytest.raises(type_inference.CheckerNotInstalled, match=r"(?s)pyright.*--no-types"):
        type_oracle_for_project(path)


def test_mypy_configured_and_missing_refuses_even_when_pyright_could_stand_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _project(tmp_path, "[tool.mypy]\nstrict = true\n")
    _without_mypy(monkeypatch)
    with pytest.raises(type_inference.CheckerNotInstalled, match=r"(?s)mypy.*--no-types"):
        type_oracle_for_project(path)


@requires_pyright
def test_an_unconfigured_project_still_takes_whichever_checker_is_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _project(tmp_path, '[project]\nname = "p"\nversion = "0"\n')
    _without_mypy(monkeypatch)
    choice = type_oracle_for_project(path)
    try:
        assert isinstance(choice.tool, type_inference.PyrightOracle)
        assert choice.note == "pyright"
    finally:
        if choice.tool is not None:
            choice.tool.close()
    _without_pyright(monkeypatch)
    nothing = type_oracle_for_project(path)
    assert nothing.tool is None
    assert nothing.note == "neither mypy nor pyright is installed"


@pytest.mark.parametrize("directory", [False, True])
def test_the_command_line_refuses_before_writing_and_no_types_proceeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, directory: bool
) -> None:
    project = tmp_path / "project"
    path = _project(project, '[tool.pyright]\ntypeCheckingMode = "strict"\n')
    _without_pyright(monkeypatch)
    target = str(project if directory else path)
    common = ["--no-interactive", "--no-format", "--progress", "none"]
    refused = invoke(["dry", target, target, *common])
    assert refused.status == 1, refused
    assert "Error: pyright is configured" in refused.stderr
    assert "--no-types" in refused.stderr
    assert "Note:" not in refused.stdout
    assert path.read_text(encoding="utf-8") == SOURCE, "nothing may be written"
    assert not (project / ".towel-helpers.json").exists()
    unverified = invoke(["dry", target, target, *common, "--no-types"])
    assert unverified.status == 0, unverified
    assert "__extracted_func_0" in path.read_text(encoding="utf-8")
