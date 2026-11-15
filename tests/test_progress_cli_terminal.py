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

"""Audit CLI-02: real previews leave transient progress out of terminals and pipes."""

from __future__ import annotations

import os
from pathlib import Path
import select
import subprocess
import sys
import time

import pytest

from tests.test_progress_renderer import read_terminal, terminal


def _three_extractions(root: Path) -> None:
    root.mkdir()
    for index in range(3):
        body = f"    x = {index + 1}\n    y = x + 2\n    return y\n"
        (root / f"module_{index}.py").write_text(
            "def first():\n" + body + "\ndef second():\n" + body,
            encoding="utf-8",
        )


def _preview_command(root: Path) -> list[str]:
    return [
        sys.executable,
        "-c",
        "import sys; sys.modules['tqdm.auto'] = None; from towel.cli import main; main()",
        "preview",
        str(root),
        "--no-types",
        "--no-format",
        "--min-lines",
        "3",
        "--summary",
    ]


def _screen(transcript: str, columns: int) -> str:
    """Model carriage return, full-line erase and wrapping for this ASCII fixture."""
    rows: list[list[str]] = [[]]
    column = 0
    offset = 0
    while offset < len(transcript):
        if transcript.startswith("\x1b[2K", offset):
            rows[-1] = []
            offset += len("\x1b[2K")
            continue
        char = transcript[offset]
        offset += 1
        if char == "\r":
            column = 0
        elif char == "\n":
            rows.append([])
            column = 0
        else:
            assert char.isprintable(), repr(char)
            if column >= columns:
                rows.append([])
                column = 0
            while len(rows[-1]) <= column:
                rows[-1].append(" ")
            rows[-1][column] = char
            column += 1
    return "\n".join("".join(row).rstrip() for row in rows).strip()


def test_preview_three_extractions_clears_a_narrow_terminal(tmp_path: Path) -> None:
    """Audit CLI-02: three real refactorings do not leave completed bars scrolling."""
    root = tmp_path / "project"
    _three_extractions(root)
    columns = 32
    chunks: list[str] = []
    with terminal(columns) as (stream, master):
        with subprocess.Popen(
            _preview_command(root),
            stdout=subprocess.PIPE,
            stderr=stream,
            env={**os.environ, "TOWEL_WORKERS": "2"},
        ) as process:
            deadline = time.monotonic() + 60
            while process.poll() is None:
                if time.monotonic() > deadline:
                    process.kill()
                    process.communicate()
                    pytest.fail("preview did not finish within 60 seconds")
                if select.select([master], [], [], 0.05)[0]:
                    chunks.append(read_terminal(master))
            chunks.append(read_terminal(master))
            stdout, _ = process.communicate(timeout=5)
            assert process.returncode == 0, "".join(chunks)
    result = stdout.decode()
    progress = "".join(chunks)
    assert "Would apply 3 refactoring(s)." in result, result
    assert "Analyzing files:" in progress and "apply:" in progress, progress
    assert "\n" not in progress, repr(progress)
    assert _screen(progress, columns) == "", repr(progress)
    assert _screen(progress + result, columns) == _screen(result, columns)


def test_preview_three_extractions_has_no_progress_on_a_pipe(tmp_path: Path) -> None:
    """Audit CLI-02: default auto produces results but no bars in redirected output."""
    root = tmp_path / "project"
    _three_extractions(root)
    completed = subprocess.run(
        _preview_command(root),
        capture_output=True,
        env={**os.environ, "TOWEL_WORKERS": "2"},
        timeout=60,
        check=False,
    )
    stdout, stderr = completed.stdout.decode(), completed.stderr.decode()
    assert completed.returncode == 0, stderr
    assert "Would apply 3 refactoring(s)." in stdout, stdout
    assert "\r" not in stderr and "\x1b" not in stderr, repr(stderr)
    assert "Analyzing files:" not in stderr and "apply:" not in stderr, stderr
    assert "tqdm" not in stderr, stderr
