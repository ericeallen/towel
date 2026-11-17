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

"""A single file the command line cannot refactor is reported, naming the file.

Relaxed mode skips a module it cannot decode or parse and says so; strict mode
refuses it before copying, naming the file and giving a remedy.
A negative refactoring count was accepted and meant "run to a fixed
point", the same as zero.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import shlex
from pathlib import Path
from typing import Callable

import pytest

from tests.test_cli_integration import LITERAL_DUPLICATES as DUPLICATES
from tests.test_cli_integration import invoke
from towel.cli import _build_parser, _run_dry, _run_preview


def _dry_arguments(source: Path, destination: Path) -> argparse.Namespace:
    return _build_parser().parse_args(
        ["dry", str(source), str(destination), "--strict", "--no-interactive", "--progress", "none"]
    )


def _run_quietly(
    handler: Callable[[argparse.Namespace], None], arguments: argparse.Namespace
) -> str:
    with contextlib.redirect_stdout(io.StringIO()) as out:
        handler(arguments)
    return out.getvalue()


def test_a_file_that_does_not_decode_is_reported_by_name(tmp_path: Path) -> None:
    """Runtime item 6: strict mode retains the single-file decoding refusal."""
    source = tmp_path / "latin.py"
    original = b'def f():\n    return "caf\xe9"\n'
    source.write_bytes(original)
    destination = tmp_path / "out.py"
    with pytest.raises(ValueError, match=r"latin\.py: it cannot be decoded: 'utf-8' codec"):
        _run_quietly(_run_dry, _dry_arguments(source, destination))
    assert source.read_bytes() == original
    assert not destination.exists(), "Invalid input must be rejected before copying"


def test_a_file_that_does_not_parse_is_reported_with_its_line(tmp_path: Path) -> None:
    """Runtime item 6: strict mode retains the single-file parse refusal."""
    source = tmp_path / "broken.py"
    original = "def f(:\n    pass\n"
    source.write_text(original)
    destination = tmp_path / "out.py"
    with pytest.raises(ValueError, match=r"broken\.py: line 1: invalid syntax"):
        _run_quietly(_run_dry, _dry_arguments(source, destination))
    assert source.read_text() == original
    assert not destination.exists(), "Invalid input must be rejected before copying"


@pytest.mark.parametrize("original", [b'def f():\n    return "caf\xe9"\n', b"def f(:\n    pass\n"])
@pytest.mark.parametrize("command", ["dry", "preview"])
def test_relaxed_single_file_warns_once_and_preserves_invalid_bytes(
    tmp_path: Path, original: bytes, command: str
) -> None:
    """Runtime item 6: the new default skips bad input without losing the user's bytes."""
    source = tmp_path / "bad.py"
    source.write_bytes(original)
    destination = tmp_path / "out.py"
    arguments = [
        command,
        str(source),
        "--no-strict",
        "--no-types",
        "--no-format",
        "--progress",
        "none",
    ]
    if command == "dry":
        arguments.extend([str(destination), "--no-interactive"])
    result = invoke(arguments)
    assert result.status == 0, result
    assert result.stderr.count("Skipping ") == 1, result.stderr
    assert str(source) in result.stderr
    assert "Analysed 0 Python files (0 functions). Skipped 1 file" in result.stdout
    assert source.read_bytes() == original
    if command == "dry":
        assert destination.read_bytes() == original
    else:
        assert not destination.exists()


def test_a_negative_refactoring_count_is_refused_by_the_parser(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["dry", "in.py", "out.py", "--max-refactorings", "-1"])
    assert "-1 is negative" in capsys.readouterr().err


def test_a_non_numeric_refactoring_count_is_refused_by_the_parser(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["dry", "in.py", "out.py", "--max-refactorings", "many"])
    assert "expected a whole number" in capsys.readouterr().err


def test_preview_of_one_file_is_silent_under_progress_none(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "m.py"
    source.write_text(DUPLICATES)
    arguments = _build_parser().parse_args(["preview", str(source), "--progress", "none"])
    _run_preview(arguments)
    captured = capsys.readouterr()
    assert "\r" not in captured.out and "\r" not in captured.err
    assert "Would apply 1 refactoring" in captured.out


@pytest.mark.parametrize(
    "arguments",
    [
        ["preview", "in", "--summ"],
        ["dry", "in", "out", "--min-line", "4"],
        ["rename-helpers", "src", "--comp"],
        ["recover", "journal", "--he"],
        ["--vers"],
    ],
)
def test_an_abbreviated_option_is_refused(
    arguments: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """User audit CLI-10: --summ and --min-line were accepted as abbreviations, so a script
    could come to mean another option when a new one shares the prefix."""
    with pytest.raises(SystemExit) as exit_info:
        _build_parser().parse_args(arguments)
    assert exit_info.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


@pytest.mark.parametrize(
    "option, value, meaning",
    [
        ("--min-lines", "-1", "give the fewest lines a duplicate may span"),
        ("--max-parameters", "-1", "give the most parameters a helper may take"),
        (
            "--max-pairs",
            "-5",
            "give the most pairs of similar blocks to compare, or 0 for no limit",
        ),
        ("--max-refactorings", "-1", "give the most refactorings to apply, or 0 for no limit"),
    ],
)
def test_a_negative_count_is_refused_in_that_options_own_words(
    option: str, value: str, meaning: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """User audit CLI-09: --min-lines -1, --max-parameters -1 and --max-pairs -5 were refused with
    --max-refactorings' sentence, "0 runs to a fixed point"."""
    with pytest.raises(SystemExit) as exit_info:
        _build_parser().parse_args(["preview", "in", option, value])
    error = capsys.readouterr().err
    assert exit_info.value.code == 2
    assert f"argument {option}: {value} is negative; {meaning}" in error, error
    assert "fixed point" not in error


@pytest.mark.parametrize("seconds", ["99999999999999999999", "1e20", "100000001"])
def test_a_time_limit_too_long_for_the_timer_is_refused(
    seconds: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """User audit CLI-09: --time-limit 99999999999999999999 passed the parser and ended in a
    traceback from signal.setitimer; macOS refuses an interval over 100,000,000 seconds."""
    source = tmp_path / "m.py"
    source.write_text(DUPLICATES)
    result = invoke(["preview", str(source), "--progress", "none", "--time-limit", seconds])
    assert result.status == 2, result
    assert "time limit must be at most 100000000 seconds" in result.stderr
    assert "leave out --time-limit for no limit" in result.stderr
    assert _build_parser().parse_args(["preview", "in", "--time-limit", "1e8"]).time_limit == 1e8


@pytest.mark.parametrize(
    "given, suggested",
    [("myproject", "cleaned"), ("src/orders.py", "src/orders_cleaned.py"), ("my code", "cleaned")],
)
def test_dry_with_one_path_says_how_to_give_the_output(given: str, suggested: str) -> None:
    """User audit CLI-09: ``towel dry myproject`` said only "the following arguments are
    required: output", not that dry writes a new copy or how to refactor in place."""
    result = invoke(["dry", given])
    assert result.status == 2
    kind = "file" if given.endswith(".py") else "directory"
    quoted = shlex.quote(given)
    assert "dry needs an output path as well as the input." in result.stderr, result.stderr
    assert f"It writes the refactored code to a new {kind}" in result.stderr
    assert f"towel dry {quoted} {shlex.quote(suggested)}\n" in result.stderr
    assert "give the same path twice" in result.stderr
    assert f"towel dry {quoted} {quoted}\n" in result.stderr


def test_dry_without_paths_and_with_options_between_them_parses_as_before(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["dry"])
    assert "the following arguments are required: input, output" in capsys.readouterr().err
    arguments = _build_parser().parse_args(["dry", "in", "--no-interactive", "out"])
    assert (arguments.input, arguments.output, arguments.interactive) == ("in", "out", False)
