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

"""CLI output operations must preserve existing files and honor cancellation."""

import argparse
import io
from pathlib import Path
from unittest.mock import patch

import pytest

from towel.cli import RunCancelled, _build_parser, _run_dry


def arguments(source: Path, destination: Path) -> argparse.Namespace:
    """The parsed arguments of ``towel dry``, so the test cannot drift from the parser."""
    return _build_parser().parse_args(
        ["dry", str(source), str(destination), "--max-refactorings", "1", "--progress", "none"]
    )


def test_existing_output_preserved(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    destination = tmp_path / "output"
    destination.mkdir()
    sentinel = destination / "keep.txt"
    sentinel.write_text("valuable")
    with pytest.raises(ValueError, match="already exists") as refusal:
        _run_dry(arguments(source, destination))
    assert sentinel.read_text() == "valuable"
    # User audit, error case 30: the refusal said to "explicitly refactor in place", not how.
    assert "(to refactor in place, give the same path twice)" in str(refusal.value)


@pytest.mark.parametrize("ancestor", [True, False])
def test_overlapping_paths_rejected(tmp_path: Path, ancestor: bool) -> None:
    source = tmp_path / "source"
    source.mkdir()
    sentinel = source / "keep.py"
    sentinel.write_text("x = 1\n")
    destination = tmp_path if ancestor else source / "output"
    with pytest.raises(ValueError, match="contain"):
        _run_dry(arguments(source, destination))
    assert sentinel.read_text() == "x = 1\n"
    assert not (source / "output").exists()


# A complete integer pipeline repays the generated helper's call interface.
_BODY = (
    "    a = x + 1\n    b = a * 2\n    c = b + 3\n"
    "    d = c + 4\n    e = d * 3\n    f = e - 12\n"
    "    g = f // 3\n    h = g + 9\n    i = h * 2\n"
    "    j = i - 18\n    k = j // 2\n    return k\n"
)
# Literal inputs, as the python lane made the CLI tests: no argument is handed over.
_DUPLICATES = "def first():\n    x = 1\n" + _BODY + "\ndef second():\n    x = 2\n" + _BODY


def test_cancel_does_not_create_output(tmp_path: Path) -> None:
    """Declining the question asked after the analysis writes nothing (audit finding 7)."""
    source = tmp_path / "source.py"
    source.write_text(_DUPLICATES)
    destination = tmp_path / "output.py"
    with (
        patch("sys.stdin", io.StringIO("n\n")),
        pytest.raises(RunCancelled, match="Cancelled; nothing was written."),
    ):
        _run_dry(arguments(source, destination))
    assert not destination.exists()
    assert source.read_text() == _DUPLICATES
