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

"""Re-audit problem 3: retain established formatting without a tool table."""

from pathlib import Path
import subprocess
from typing import Optional

import pytest

from tests.test_cli_integration import LITERAL_DUPLICATES, invoke
from tests.test_generated_code_formatting import LITERAL_STRINGS
from towel import formatting
from towel.project_tools import ToolChoice
from towel.source_files import source_policy


def _clean(path: Path) -> None:
    path.write_text('value = "already formatted"\n')


def test_black_clean_sources_choose_one_run_owned_formatter(tmp_path: Path) -> None:
    """Re-audit problem 3: default Black is an existing workflow without a tool table."""
    first, second = tmp_path / "a.py", tmp_path / "b.py"
    _clean(first)
    _clean(second)
    notes: list[ToolChoice[formatting.SnippetFormatter]] = []
    factory = formatting.formatter_factory_for_project(tmp_path, notes.append)
    selected = factory(str(first))
    assert selected is not None
    assert selected("value='generated'") == 'value = "generated"'
    assert factory(str(second)) is not None
    assert "all analyzed files already formatted" in notes[0].note


def test_clean_source_probe_runs_once_per_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-audit problem 3: repeated materializations do not repeat the project check."""
    first, second = tmp_path / "a.py", tmp_path / "b.py"
    _clean(first)
    _clean(second)
    calls: list[Path] = []

    def probe(path: Path) -> Optional[formatting.FormatterKind]:
        calls.append(path)
        return "black"

    monkeypatch.setattr(formatting, "_sources_already_formatted", probe)
    factory = formatting.formatter_factory_for_project(tmp_path)
    assert factory(str(first)) is not None
    assert factory(str(first)) is not None
    assert factory(str(second)) is not None
    assert calls == [tmp_path]


def test_one_dirty_selected_file_prevents_inference_but_excluded_file_does_not(
    tmp_path: Path,
) -> None:
    """Re-audit problem 3: infer only from the entire selected analysis scope."""
    clean, dirty = tmp_path / "clean.py", tmp_path / "dirty.py"
    _clean(clean)
    dirty.write_text("value='unformatted'\n")
    assert formatting.formatter_factory_for_project(tmp_path)(str(clean)) is None
    with source_policy(strict=False) as policy:
        policy.excluded_names = frozenset({"dirty.py"})
        assert formatting.formatter_factory_for_project(tmp_path)(str(clean)) is not None


@pytest.mark.parametrize("hook", ["black", "ruff-format", "'black'", '"ruff-format"'])
def test_pre_commit_hook_is_configuration_without_a_packaging_marker(
    tmp_path: Path, hook: str
) -> None:
    """Re-audit problem 3: standard pre-commit format hooks declare a formatter."""
    (tmp_path / ".pre-commit-config.yaml").write_text(
        f"repos:\n  - repo: example\n    hooks:\n      - id: {hook} # formatter\n"
    )
    child = tmp_path / "nested"
    child.mkdir()
    source = child / "a.py"
    source.write_text("value='dirty'\n")
    choice = formatting.formatter_for_project(source)
    assert choice.tool is not None
    assert "pre-commit hook" in choice.note
    assert choice.tool("value='generated'") == 'value = "generated"'


@pytest.mark.parametrize(
    "hook_lines",
    [
        "      # - id: black",
        "      - id: ruff",
        "      - id: ruff-check",
        "      - id: black\n        args: [--line-length, '100']",
    ],
)
def test_comments_lint_hooks_and_hook_overrides_do_not_claim_default_formatting(
    tmp_path: Path, hook_lines: str
) -> None:
    """Re-audit problem 3: a hook must establish the formatting we actually apply."""
    (tmp_path / ".pre-commit-config.yaml").write_text(
        f"repos:\n  - repo: example\n    hooks:\n{hook_lines}\n"
    )
    source = tmp_path / "a.py"
    source.write_text("value='dirty'\n")
    assert formatting.formatter_for_project(source).tool is None


def test_empty_scope_cannot_establish_formatter(tmp_path: Path) -> None:
    """Re-audit problem 3: vacuous success does not establish a formatting workflow."""
    assert formatting._sources_already_formatted(tmp_path) is None


def test_missing_tools_do_not_establish_formatter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-audit problem 3: failed checks are not evidence that input is formatted."""
    _clean(tmp_path / "a.py")
    monkeypatch.setattr(formatting, "_ruff_executable", lambda: None)

    def unavailable(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, "", "No module named black")

    monkeypatch.setattr(subprocess, "run", unavailable)
    with source_policy(strict=False, include_ignored=True):
        assert formatting._sources_already_formatted(tmp_path) is None


def test_inferred_black_uses_black_defaults_not_linter_width(tmp_path: Path) -> None:
    """Re-audit problem 3: the clean check and generated formatting use identical settings."""
    (tmp_path / "pyproject.toml").write_text("[tool.pycodestyle]\nmax-line-length=40\n")
    source = tmp_path / "a.py"
    _clean(source)
    callback = formatting.formatter_factory_for_project(tmp_path)(str(source))
    assert callback is not None
    statement = 'result = call("' + "x" * 50 + '")'
    assert callback(statement) == statement


def test_black_clean_cli_output_remains_black_clean_and_reports_reason(
    tmp_path: Path,
) -> None:
    """Re-audit problem 3: a default Black project remains clean after extraction."""
    import black

    source, output = tmp_path / "input.py", tmp_path / "output.py"
    source.write_text(black.format_str(LITERAL_STRINGS, mode=black.Mode()))
    result = invoke(["dry", str(source), str(output), "--no-interactive", "--no-types"])
    assert result.status == 0, result
    after = output.read_text()
    assert "__extracted_func" in after
    assert black.format_str(after, mode=black.Mode()) == after
    assert "Black (all analyzed files already formatted)" in result.stdout + result.stderr


def test_no_format_does_not_probe_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-audit problem 3: --no-format never starts formatter inference."""
    source, output = tmp_path / "input.py", tmp_path / "output.py"
    source.write_text(LITERAL_DUPLICATES)

    def forbidden(path: Path) -> Optional[formatting.FormatterKind]:
        raise AssertionError("--no-format probed a formatter")

    monkeypatch.setattr(formatting, "_sources_already_formatted", forbidden)
    result = invoke(
        [
            "dry",
            str(source),
            str(output),
            "--no-interactive",
            "--no-types",
            "--no-format",
        ]
    )
    assert result.status == 0, result


def test_ruff_clean_sources_select_ruff_without_creating_project_cache(tmp_path: Path) -> None:
    """Re-audit problem 3: Ruff inference is read-only when Black would change the input."""
    import black

    source = tmp_path / "input.py"
    original = 'result = function("' + "a" * 40 + '")  # pylint: disable=line-too-long\n'
    assert black.format_str(original, mode=black.Mode()) != original
    source.write_text(original)
    notes: list[ToolChoice[formatting.SnippetFormatter]] = []
    callback = formatting.formatter_factory_for_project(tmp_path, notes.append)(str(source))
    assert callback is not None
    assert notes[0].note == "ruff (all analyzed files already formatted)"
    assert callback(original) == original.rstrip("\n")
    assert source.read_text() == original
    assert set(tmp_path.iterdir()) == {source}
