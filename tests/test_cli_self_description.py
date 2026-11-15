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
from typing import Iterator, List, Optional, Tuple

import pytest

from towel import cli, type_inference
from tests.test_cli_integration import DUPLICATES, HELPER, invoke
from tests.test_recovery_journal import _interrupted_transaction

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


def _help(arguments: List[str], monkeypatch: pytest.MonkeyPatch, columns: int = 80) -> str:
    """What ``towel <arguments> --help`` prints in a terminal ``columns`` wide."""
    monkeypatch.setenv("COLUMNS", str(columns))
    result = invoke([*arguments, "--help"])
    assert result.status == 0 and not result.stderr, result
    return result.stdout


COMMANDS = [[], ["dry"], ["preview"], ["rename-helpers"], ["recover"]]


@pytest.mark.parametrize("command", COMMANDS, ids=lambda command: " ".join(command) or "towel")
def test_help_fits_the_terminal(command: List[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """User audit CLI-03: ``towel dry --help`` printed each option as one line of up to 622
    characters, which an 80-column terminal broke mid-word; ``preview --help`` was wrapped."""
    long_lines = [line for line in _help(command, monkeypatch).splitlines() if len(line) > 80]
    assert not long_lines, long_lines


def _command(name: str) -> argparse.ArgumentParser:
    """The parser of ``towel <name>``."""
    for action in cli._build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            child = action.choices[name]
            assert isinstance(child, argparse.ArgumentParser)
            return child
    raise AssertionError(f"towel has no command {name}")


def _long_name(action: argparse.Action) -> str:
    return next(option for option in action.option_strings if option.startswith("--"))


SECTIONS = ("what you get", "what is examined", "limits", "advanced")
"""dry's and preview's help sections, in the order a newcomer needs them."""

SHARED_SECTIONS = {
    "what you get": {"--json", "--explain", "--stats", "--progress"},
    "what is examined": {"--exclude", "--cross-module"},
    "limits": {
        "--min-lines",
        "--max-parameters",
        "--max-refactorings",
        "--time-limit",
        "--max-pairs",
    },
    "advanced": {"--strict", "--types", "--format", "--parameterize-builtins"},
}
OWN_OUTPUT_OPTIONS = {
    "dry": {"--interactive"},
    "preview": {"--summary", "--diff", "--quick", "--inventory"},
}


@pytest.mark.parametrize("command", ["dry", "preview"])
def test_options_are_listed_in_the_order_a_newcomer_needs_them(
    command: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """User audit CLI-04: the help listed --interactive, --exclude, --cross-module and nine
    lines on --parameterize-builtins first, and what a newcomer wants (--summary, --diff,
    --json, --quick) last. Options now come in sections: what you get, what is examined,
    limits, advanced."""
    listed = {
        group.title: {
            _long_name(action)
            for action in group._group_actions
            if action.option_strings and action.help != argparse.SUPPRESS
        }
        for group in _command(command)._action_groups
    }
    expected = {**SHARED_SECTIONS}
    expected["what you get"] = {"--help", *SHARED_SECTIONS["what you get"]}
    expected["what you get"] |= OWN_OUTPUT_OPTIONS[command]
    for title, options in expected.items():
        assert options <= listed[title], (title, options - listed[title])
    assert set(listed) == {"positional arguments", *SECTIONS}
    text = _help([command], monkeypatch)
    headings = [text.index(f"\n{title}:\n") for title in ("positional arguments", *SECTIONS)]
    assert headings == sorted(headings)


JARGON = re.compile(
    r"\bthunks?\b|anti-unification|ownership transfer|lifetime holder|fixed point"
    r"|\bborrowers?\b|\bhosts?\b",
    re.IGNORECASE,
)
"""Towel's internal vocabulary, which no help may use without a plain gloss."""


def _option_help() -> List[Tuple[str, str]]:
    """``(command and argument, help text)`` for every listed argument of every command."""
    return [
        (
            f"{parser.prog} {action.option_strings[-1] if action.option_strings else action.dest}",
            action.help,
        )
        for parser in _parsers(cli._build_parser())
        for action in parser._actions
        if action.help and action.help != argparse.SUPPRESS
    ]


def test_option_help_is_in_a_python_developers_words() -> None:
    """User audit CLI-04: option help spoke of "whole-body ownership transfer", "the final
    lifetime holder", "a lookup thunk" and running "to a fixed point"."""
    jargon = [
        (argument, found[0]) for argument, text in _option_help() if (found := JARGON.search(text))
    ]
    assert not jargon, jargon


def test_every_option_with_a_setting_states_its_default() -> None:
    """User audit CLI-04: an option that takes a value, or has an on and an off form, says what
    happens without it."""
    silent = [
        f"{parser.prog} {action.option_strings[0]}"
        for parser in _parsers(cli._build_parser())
        for action in parser._actions
        if action.option_strings
        and action.help != argparse.SUPPRESS
        and (isinstance(action, argparse.BooleanOptionalAction) or action.nargs != 0)
        and "default" not in (action.help or "").lower()
    ]
    assert not silent, silent


@pytest.mark.parametrize("command", COMMANDS, ids=lambda command: " ".join(command) or "towel")
def test_help_never_splits_an_option_name(
    command: List[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """User audit CLI-03: wrapped help broke "--no-interactive" after "--no-"."""
    for columns in (60, 80, 100):
        text = _help(command, monkeypatch, columns)
        assert not re.search(r"--[\w-]*-\n", text), text


@pytest.mark.parametrize("example", _examples())
def test_every_help_example_is_a_valid_command_line(example: str) -> None:
    """``towel --help`` once showed ``towel rename-helpers`` without the target it requires."""
    try:
        cli._build_parser().parse_args(shlex.split(example))
    except SystemExit as error:
        pytest.fail(f"towel {example} is refused by the parser (exit {error.code})")


def test_the_top_level_help_says_what_towel_does_in_one_sentence() -> None:
    """Kimi docs review 7: "Explain 'DRYs'"; the description was "A Python tool that DRYs your
    code - finds and refactors repeated code"."""
    description = cli._build_parser().description or ""
    assert "DRY" not in description
    assert len(re.findall(r"[.!?](?:\s|$)", description)) == 1, description
    assert "helper function" in description


@pytest.mark.parametrize("command", COMMANDS, ids=lambda command: " ".join(command) or "towel")
def test_no_help_text_uses_towels_internal_vocabulary(
    command: List[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """User audit CLI-04: the first line of ``dry --help`` said "anti-unification"."""
    text = " ".join(_help(command, monkeypatch).split())
    assert not JARGON.search(text) and "DRYs" not in text, JARGON.search(text)


@pytest.mark.parametrize("command", ["preview", "dry", "rename-helpers"])
def test_every_command_has_a_description_and_examples(command: str) -> None:
    """User audit CLI-04 and Kimi docs review 7: preview had no examples."""
    parser = _command(command)
    assert parser.description
    examples = [line for line in (parser.epilog or "").splitlines() if _EXAMPLE.match(line)]
    assert 2 <= len(examples) <= 3, examples


SOURCES = {"orders.py": DUPLICATES, "helpers.py": HELPER}
"""``src/`` for the examples: a module with a duplicate, and one with a helper to name."""

ANSWERS = {"dry": "y\n", "rename-helpers": '{"__extracted_func_0": "doubled"}\n'}
"""What a person types at each command's question: yes, and an assistant's suggested name."""


@pytest.mark.parametrize("example", _examples())
def test_every_help_example_works_in_a_directory_holding_only_src(
    example: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """User audit CLI-06: ``towel rename-helpers src/``, the help's own example, was refused in
    a directory holding only ``src/`` for want of project metadata."""
    (tmp_path / "src").mkdir()
    for name, text in SOURCES.items():
        (tmp_path / "src" / name).write_text(text)
    monkeypatch.chdir(tmp_path)
    arguments = shlex.split(example)
    result = invoke(arguments, stdin=ANSWERS.get(arguments[0], ""))
    assert result.status == 0, result
    assert "Error" not in result.stderr, result.stderr


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
    assert result.status == 1, result.stderr
    assert "Proceed? (y/N)" in result.stdout and "Aborted" in result.stderr
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


def test_recover_says_what_it_does_and_keeps_an_edit_as_it_says(tmp_path: Path) -> None:
    """User audit CLI-04 and Kimi docs review 7: ``recover --help`` had no description, said
    nothing of what it does with a file edited since the interruption, and gave no example."""
    parser = _command("recover")
    description = " ".join((parser.description or "").split())
    assert "puts every original back and removes the journal" in description
    assert "it keeps your edit and the journal, and names the file" in description
    example = re.search(
        r"^\s*towel recover src/(\.towel-transaction-)<id>$", parser.epilog or "", re.M
    )
    assert example, parser.epilog
    # The journal sits where the example says, and an edit is kept as the description says.
    source = tmp_path / "src"
    source.mkdir()
    files, journal = _interrupted_transaction(source)
    assert journal.parent == source and journal.name.startswith(example[1])
    files[0].write_bytes(b"value = 3\n")
    result = invoke(["recover", str(journal)])
    assert result.status == 1 and str(files[0]) in result.stderr, result
    assert files[0].read_bytes() == b"value = 3\n" and journal.is_dir()
