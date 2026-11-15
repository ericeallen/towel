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

"""Strict selection refuses unavailable configured checkers; relaxed CLI runs stay useful.

The project's own check is the promise a typed run keeps. Choosing another
checker in its place gives a verdict the project never asked for, and checking
with nothing at all is the silence the promise exists to prevent; both used to
print one line starting "Note:" and write the refactoring with exit status 0.
A project that configures no checker at all keeps the documented behaviour:
mypy when installed, else pyright, else copying only.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
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

# Keep the public CLI's readability policy enabled: this shared arithmetic is
# substantial enough that disabling an unavailable checker still extracts it.
BODY = """    total = value + 1
    doubled = total * 2
    answer = doubled - 3
    answer = answer + 17
    answer = answer * 19
    answer = answer - 23
    answer = answer // 29
    answer = answer + 31
    answer = answer * 37
    answer = answer - 41
    answer = answer // 43
"""
SOURCE = (
    'def first(value: int) -> int:\n    print("first")\n' + BODY + "    return answer\n\n\n"
    'def second(value: int) -> int:\n    print("second")\n' + BODY + "    return answer\n"
)


def _without_pyright(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(type_inference, "_pyright_command", lambda: None)


def _without_mypy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(type_inference, "_mypy_installed", lambda: False)


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
    started: List[bool] = []
    original_init = type_inference.MypyInferrer.__init__

    def recording_init(self: type_inference.MypyInferrer) -> None:
        started.append(True)
        original_init(self)

    monkeypatch.setattr(type_inference.MypyInferrer, "__init__", recording_init)
    with pytest.raises(type_inference.CheckerNotInstalled) as refused:
        type_oracle_for_project(path)
    message = str(refused.value)
    assert "pyright" in message and "mypy" not in message.split(" is configured")[0]
    assert str(tmp_path / "pyproject.toml") in message
    assert "--no-types" in message and "install" in message.lower()
    assert not started, "the refusal comes before any checker starts, so none can leak"


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
    refused = invoke(["dry", target, target, *common, "--strict"])
    assert refused.status == 1, refused
    assert "Error: pyright is configured" in refused.stderr
    assert "--no-types" in refused.stderr
    assert "Note:" not in refused.stdout
    assert path.read_text(encoding="utf-8") == SOURCE, "nothing may be written"
    assert not (project / ".towel-helpers.json").exists()
    unverified = invoke(["dry", target, target, *common, "--no-types"])
    assert unverified.status == 0, unverified
    assert "__extracted_func_0" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("checker", ["mypy", "pyright"])
@pytest.mark.parametrize("directory", [False, True])
def test_relaxed_missing_checker_previews_bare_helpers_with_explicit_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, checker: str, directory: bool
) -> None:
    """Typed audit item 2: unavailable configured checkers must not hide results."""
    path = _project(tmp_path, f"[tool.{checker}]\n")
    _without_mypy(monkeypatch)
    _without_pyright(monkeypatch)
    target = str(tmp_path if directory else path)
    flags = ["--no-format", "--progress", "none", "--json"]
    relaxed = invoke(["preview", target, *flags])
    assert relaxed.status == 0, relaxed.stderr
    document = json.loads(relaxed.stdout)
    assert document["types"] == "unverified"
    assert document["applied"] > 0
    helpers = document["helpers"]["helpers"]
    assert helpers
    for helper in helpers:
        function = ast.parse(helper["source"]).body[0]
        assert isinstance(function, ast.FunctionDef)
        assert function.returns is None
        assert all(
            argument.annotation is None
            for argument in ast.walk(function.args)
            if isinstance(argument, ast.arg)
        )
    assert f"Warning: {checker} is configured" in relaxed.stderr
    assert str(tmp_path / "pyproject.toml") in relaxed.stderr
    assert sys.executable in relaxed.stderr
    assert "Install" in relaxed.stderr and "into that environment" in relaxed.stderr
    assert "--no-types" in relaxed.stderr and "--strict to refuse instead" in relaxed.stderr
    assert path.read_text() == SOURCE
    strict = invoke(["preview", target, *flags, "--strict"])
    assert strict.status == 1
    assert json.loads(strict.stdout)["status"] == "error"
    assert "types" not in json.loads(strict.stdout)
    assert path.read_text() == SOURCE
    off = invoke(["preview", target, *flags, "--no-types"])
    assert off.status == 0
    assert json.loads(off.stdout)["types"] == "off"
    assert "Warning:" not in off.stderr


@requires_mypy
def test_available_configured_checker_is_named_as_verified_in_json(tmp_path: Path) -> None:
    """Typed audit item 2: the JSON distinguishes a checker run from fallback."""
    path = _project(tmp_path, "[tool.mypy]\n")
    path.write_text("value: int = 3\n")
    result = invoke(["preview", str(path), "--json", "--no-format", "--progress", "none"])
    assert result.status == 0, result.stderr
    assert json.loads(result.stdout)["types"] == "verified"


@pytest.mark.parametrize("in_place", [False, True])
@pytest.mark.parametrize("consent", ["yes", "no", "unattended"])
def test_dry_discloses_unverified_fallback_before_consent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, in_place: bool, consent: str
) -> None:
    """Typed round 2 finding 3: dry discloses fallback before consent or publication."""
    import io
    from towel import cli

    project = tmp_path / "project"
    path = _project(project, "[tool.mypy]\n")
    destination = project if in_place else tmp_path / "output"
    _without_mypy(monkeypatch)
    _without_pyright(monkeypatch)
    confirm = cli._confirm
    prompts: list[str] = []

    def informed_confirmation(prompt: str) -> bool:
        assert isinstance(sys.stderr, io.StringIO)
        assert "Continuing without type verification" in sys.stderr.getvalue()
        assert path.read_text() == SOURCE
        assert in_place or not destination.exists()
        prompts.append(prompt)
        return confirm(prompt)

    monkeypatch.setattr(cli, "_confirm", informed_confirmation)
    flags = ["--no-interactive", "--json"] if consent == "unattended" else []
    result = invoke(
        ["dry", str(project), str(destination), "--no-format", "--progress", "none", *flags],
        stdin="y\n" if consent == "yes" else "n\n",
    )
    assert result.status == (1 if consent == "no" else 0), result.stderr
    assert len(prompts) == (0 if consent == "unattended" else 1)
    assert result.stderr.count("Continuing without type verification") == 1
    if consent == "no":
        assert path.read_text() == SOURCE
        assert in_place or not destination.exists()
    else:
        tree = ast.parse((destination / "m.py").read_text())
        helpers = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name
        ]
        assert helpers
        assert all(
            node.returns is None and all(arg.annotation is None for arg in node.args.args)
            for node in helpers
        )
        if consent == "unattended":
            document = json.loads(result.stdout)
            assert document["published"] is True
            assert document["types"] == "unverified"


@pytest.mark.parametrize("configured", [False, True])
def test_unverified_json_means_no_checker_was_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured: bool
) -> None:
    """Typed round 2 finding 5: both missing-checker paths share one verification outcome."""
    path = _project(tmp_path, "[tool.mypy]\n" if configured else "[project]\nname = 'example'\n")
    _without_mypy(monkeypatch)
    _without_pyright(monkeypatch)
    result = invoke(["preview", str(path), "--json", "--no-format", "--progress", "none"])
    document = json.loads(result.stdout)
    assert result.status == 0, result.stderr
    assert document["status"] == "complete"
    assert document["types"] == "unverified"


def test_failed_checker_is_an_error_not_a_missing_checker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Typed round 2 finding 5: failure must not masquerade as the unverified fallback."""
    from towel import cli
    from towel.type_inference import CheckFailure
    from tests.test_type_run_policy import _Oracle

    path = _project(tmp_path, "[tool.mypy]\n")
    monkeypatch.setattr(cli, "_type_oracle", lambda _: _Oracle(lambda _: CheckFailure("timed out")))
    result = invoke(["preview", str(path), "--json", "--no-format", "--progress", "none"])
    document = json.loads(result.stdout)
    assert result.status == 1
    assert document["status"] == "error"
    assert "timed out" in document["error"]
    assert "types" not in document


def test_types_field_is_in_the_public_schema() -> None:
    """Typed round 2 finding 5: scripts have a documented meaning for each verification value."""
    schema = (Path(__file__).parents[1] / "docs" / "COMPATIBILITY.md").read_text()
    row = next(line for line in schema.splitlines() if line.startswith("| `types` |"))
    assert all(f'"{value}"' in row for value in ("verified", "unverified", "off"))
    assert "absent" in row and "status" in row
