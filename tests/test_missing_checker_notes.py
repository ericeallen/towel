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

"""Typed audit item 4: optional checker notes concern only annotated target code."""

from pathlib import Path

import pytest

from towel import type_inference
from tests.test_cli_integration import invoke


@pytest.mark.parametrize("directory", [False, True])
@pytest.mark.parametrize(
    "source, relevant",
    [
        ("def f(value):\n    return value + 1\n", False),
        ("def f(value: int):\n    return value + 1\n", True),
        ("def f(value) -> int:\n    return value + 1\n", True),
        ("answer: int = 42\n", True),
    ],
)
def test_missing_optional_checkers_note_only_annotated_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    directory: bool,
    source: str,
    relevant: bool,
) -> None:
    """Typed audit item 4: unannotated projects need no optional-checker nag."""
    monkeypatch.setattr(type_inference, "_mypy_installed", lambda: False)
    monkeypatch.setattr(type_inference, "_pyright_command", lambda: None)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "example"\nversion = "0"\n')
    path = tmp_path / "example.py"
    path.write_text(source)
    result = invoke(
        ["preview", str(tmp_path if directory else path), "--no-format", "--progress", "none"]
    )
    assert result.status == 0, result.stderr
    note = "neither mypy nor pyright is installed"
    assert (note in result.stderr) is relevant
    assert result.stderr.count(note) <= 1
    if relevant:
        assert 'pip install "code-towel[types]"' in result.stderr
    assert path.read_text() == source


def test_annotations_in_excluded_files_do_not_trigger_optional_checker_note(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Typed audit item 4: the note must describe the selected target only."""
    monkeypatch.setattr(type_inference, "_mypy_installed", lambda: False)
    monkeypatch.setattr(type_inference, "_pyright_command", lambda: None)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "example"\nversion = "0"\n')
    (tmp_path / "main.py").write_text("answer = 42\n")
    ignored = tmp_path / "ignored"
    ignored.mkdir()
    (ignored / "typed.py").write_text("answer: int = 42\n")
    result = invoke(
        ["preview", str(tmp_path), "--exclude", "ignored", "--no-format", "--progress", "none"]
    )
    assert result.status == 0, result.stderr
    assert "neither mypy nor pyright is installed" not in result.stderr


@pytest.mark.parametrize("unreadable", [b"\xff", b"def broken(:\n"])
def test_optional_note_census_does_not_refuse_unreadable_sources(
    tmp_path: Path,
    unreadable: bytes,
) -> None:
    """Typed audit item 4: an informational scan cannot add a source-read refusal."""
    from towel.cli import _target_has_annotations

    (tmp_path / "a_bad.py").write_bytes(unreadable)
    assert not _target_has_annotations(tmp_path, ())
    (tmp_path / "b_typed.py").write_text("answer: int = 42\n")
    assert _target_has_annotations(tmp_path, ())
