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

"""What the command line says about itself is what it does: help examples and the cost note."""

import argparse
import importlib.util
import os
import re
import shlex
import shutil
from pathlib import Path
from typing import Iterator, List, Optional

import pytest

from towel import cli, type_inference
from tests.test_cli_integration import invoke

_EXAMPLE = re.compile(r"^\s*towel (?P<arguments>[^#]*?)\s*(#.*)?$")


def _parsers(parser: argparse.ArgumentParser) -> Iterator[argparse.ArgumentParser]:
    yield parser
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                yield from _parsers(child)


def _examples() -> List[str]:
    """Every ``towel ...`` line any help epilog shows, without placeholders like <command>."""
    return [
        match["arguments"]
        for parser in _parsers(cli._build_parser())
        for line in (parser.epilog or "").splitlines()
        if (match := _EXAMPLE.match(line)) and "<" not in match["arguments"]
    ]


def test_help_shows_examples_from_every_command() -> None:
    commands = {shlex.split(example)[0] for example in _examples()}
    assert {"dry", "preview", "rename-helpers"} <= commands


@pytest.mark.parametrize("example", _examples())
def test_every_help_example_is_a_valid_command_line(example: str) -> None:
    """``towel --help`` once showed ``towel rename-helpers`` without the target it requires."""
    try:
        cli._build_parser().parse_args(shlex.split(example))
    except SystemExit as error:
        pytest.fail(f"towel {example} is refused by the parser (exit {error.code})")


def _project(root: Path, files: int, *, under: str = "") -> Path:
    directory = root / under if under else root
    directory.mkdir(parents=True, exist_ok=True)
    for index in range(files):
        (directory / f"module_{index}.py").write_text(f"value_{index} = {index}\n")
    return root


def _without_checkers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make mypy and pyright look uninstalled, as in a plain ``pip install code-towel``."""
    find_spec, which = importlib.util.find_spec, shutil.which

    def hidden_spec(name: str, package: Optional[str] = None) -> object:
        return None if name in {"mypy", "pyright"} else find_spec(name, package)

    def hidden_which(
        name: str, mode: int = os.F_OK | os.X_OK, path: Optional[str] = None
    ) -> object:
        return None if name == "pyright" else which(name, mode, path)

    monkeypatch.setattr(importlib.util, "find_spec", hidden_spec)
    monkeypatch.setattr(shutil, "which", hidden_which)


def test_cost_note_is_silent_when_no_checker_will_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It said "Type checking is on (the installed checker)" with no checker installed."""
    project = _project(tmp_path / "project", cli._COST_NOTE_FILES)
    _without_checkers(monkeypatch)
    assert cli._verification_cost(project, True) == []


def test_interactive_run_without_a_checker_does_not_announce_type_checking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path / "project", cli._COST_NOTE_FILES)
    _without_checkers(monkeypatch)
    result = invoke(["dry", str(project), str(tmp_path / "out"), "--interactive"], stdin="n\n")
    assert result.status == 0, result.stderr
    assert "Proceed? (y/N)" in result.stdout and "Aborted." in result.stdout
    assert "Type checking is on" not in result.stdout


def test_cost_note_is_silent_when_a_configured_checker_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run is refused for the missing checker after the prompt; no cost is described."""
    project = _project(tmp_path / "project", cli._COST_NOTE_FILES)
    (project / "mypy.ini").write_text("[mypy]\n")
    _without_checkers(monkeypatch)
    with pytest.raises(type_inference.CheckerNotInstalled):
        type_inference.type_checkers_for_project(project)
    assert cli._verification_cost(project, True) == []


@pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")
def test_cost_note_names_the_checker_that_will_run_without_starting_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Named as the run selects it, and selected without constructing a checker before consent."""
    project = _project(tmp_path / "project", cli._COST_NOTE_FILES)
    (project / "mypy.ini").write_text("[mypy]\n")

    def never_started(*args: object, **kwargs: object) -> None:
        raise AssertionError("the cost note must not start a checker")

    monkeypatch.setattr(type_inference, "MypyInferrer", never_started)
    monkeypatch.setattr(type_inference, "PyrightOracle", never_started)
    note = cli._verification_cost(project, True)
    assert note and note[0].startswith("Type checking is on (mypy), "), note
    assert f"{cli._COST_NOTE_FILES} Python files" in note[1]


def test_cost_note_counts_only_the_files_the_run_analyzes(tmp_path: Path) -> None:
    """Excluded directories and environments are not part of the run, so not of its cost."""
    project = _project(tmp_path / "project", 2)
    _project(project, cli._COST_NOTE_FILES, under="vendored")
    _project(project, cli._COST_NOTE_FILES, under="env/lib")
    (project / "env" / "pyvenv.cfg").write_text("home = /usr/bin\n")
    assert cli._verification_cost(project, True, ("vendored",)) == []


@pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")
def test_checker_selection_matches_the_oracle_the_run_starts(tmp_path: Path) -> None:
    (tmp_path / "mypy.ini").write_text("[mypy]\n")
    assert type_inference.type_checkers_for_project(tmp_path) == ("mypy",)
    choice = type_inference.type_oracle_for_project(tmp_path)
    try:
        assert isinstance(choice.tool, type_inference.MypyInferrer) and choice.note == "mypy"
    finally:
        assert choice.tool is not None
        choice.tool.close()
