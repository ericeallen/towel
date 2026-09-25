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

Directory mode skips a module it cannot decode or parse and says so; single-file
mode used to refactor the file's copy silently or stop with an error that named
nothing. A negative refactoring count was accepted and meant "run to a fixed
point", the same as zero.
"""

from __future__ import annotations

import argparse
import contextlib
import io
from pathlib import Path
from typing import Callable

import pytest

from towel.cli import _build_parser, _run_dry, _run_preview


def _dry_arguments(source: Path, destination: Path) -> argparse.Namespace:
    return _build_parser().parse_args(
        ["dry", str(source), str(destination), "--no-interactive", "--progress", "none"]
    )


def _run_quietly(
    handler: Callable[[argparse.Namespace], None], arguments: argparse.Namespace
) -> str:
    with contextlib.redirect_stdout(io.StringIO()) as out:
        handler(arguments)
    return out.getvalue()


def test_a_file_that_does_not_decode_is_reported_by_name(tmp_path: Path) -> None:
    source = tmp_path / "latin.py"
    original = b'def f():\n    return "caf\xe9"\n'
    source.write_bytes(original)
    destination = tmp_path / "out.py"
    with pytest.raises(ValueError, match=r"latin\.py: 'utf-8' codec"):
        _run_quietly(_run_dry, _dry_arguments(source, destination))
    assert source.read_bytes() == original
    assert not destination.exists(), "Invalid input must be rejected before copying"


def test_a_file_that_does_not_parse_is_reported_with_its_line(tmp_path: Path) -> None:
    source = tmp_path / "broken.py"
    original = "def f(:\n    pass\n"
    source.write_text(original)
    destination = tmp_path / "out.py"
    with pytest.raises(ValueError, match=r"broken\.py: line 1: invalid syntax"):
        _run_quietly(_run_dry, _dry_arguments(source, destination))
    assert source.read_text() == original
    assert not destination.exists(), "Invalid input must be rejected before copying"


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
    source.write_text(
        "def a(v):\n    x = v + 1\n    y = x * 2\n    z = y - 3\n    return z\n\n"
        "def b(v):\n    x = v + 1\n    y = x * 2\n    z = y - 3\n    return z + 1\n"
    )
    arguments = _build_parser().parse_args(["preview", str(source), "--progress", "none"])
    _run_preview(arguments)
    captured = capsys.readouterr()
    assert "\r" not in captured.out and "\r" not in captured.err
    assert "Found 1 refactoring opportunit" in captured.out
