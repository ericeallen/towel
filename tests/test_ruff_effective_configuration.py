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

"""Ruff, rather than a second TOML interpreter, resolves effective import policy."""

from pathlib import Path

import pytest

from towel.formatting import (
    formatter_for_project,
    import_sorter_for_project,
    project_configures_ruff_import_sorting,
)

ORIGINAL = "import sys\n\nprint(sys.argv)\n"
ASSEMBLED = "import sys\nimport os\n\nprint(sys.argv, os.sep)\n"
SORTED = "import os\nimport sys\n\nprint(sys.argv, os.sep)\n"


def _finish(module: Path, project: Path | None = None) -> str:
    choice = import_sorter_for_project(project or module)
    return choice.tool(str(module), ASSEMBLED) if choice.tool else ASSEMBLED


@pytest.mark.parametrize("filename", ["pyproject.toml", "ruff.toml", ".ruff.toml"])
def test_each_supported_config_form_sorts_real_added_imports(tmp_path: Path, filename: str) -> None:
    pytest.importorskip("ruff")
    prefix = "tool.ruff." if filename == "pyproject.toml" else ""
    (tmp_path / filename).write_text(f'[{prefix}lint]\nselect=["I"]\n')
    module = tmp_path / "m.py"
    module.write_text(ORIGINAL)
    assert project_configures_ruff_import_sorting(module)
    assert _finish(module) == SORTED
    assert _finish(module, tmp_path) == SORTED


@pytest.mark.parametrize("disabled", ['ignore=["I"]', 'select=["F"]'])
def test_selected_and_ignored_rules_are_resolved_by_ruff(tmp_path: Path, disabled: str) -> None:
    pytest.importorskip("ruff")
    (tmp_path / "base.toml").write_text('[lint]\nselect=["I"]\n')
    (tmp_path / "ruff.toml").write_text(f'extend="base.toml"\n[lint]\n{disabled}\n')
    module = tmp_path / "m.py"
    module.write_text(ORIGINAL)
    assert not project_configures_ruff_import_sorting(module)
    assert _finish(module) == ASSEMBLED
    assert _finish(module, tmp_path) == ASSEMBLED


def test_ignoring_i001_does_not_force_import_reordering(tmp_path: Path) -> None:
    pytest.importorskip("ruff")
    (tmp_path / "ruff.toml").write_text('[lint]\nselect=["I"]\nignore=["I001"]\n')
    module = tmp_path / "m.py"
    module.write_text(ORIGINAL)
    assert _finish(module) == ASSEMBLED
    assert _finish(module, tmp_path) == ASSEMBLED


@pytest.mark.parametrize(
    "excluded",
    [
        'exclude=["m.py"]\n[lint]\nselect=["I"]',
        '[lint]\nselect=["I"]\nexclude=["m.py"]',
        '[lint]\nselect=["I"]\n[lint.per-file-ignores]\n"m.py"=["I"]',
    ],
)
def test_native_file_selection_remains_in_force(tmp_path: Path, excluded: str) -> None:
    pytest.importorskip("ruff")
    (tmp_path / "ruff.toml").write_text(excluded + "\n")
    module = tmp_path / "m.py"
    module.write_text(ORIGINAL)
    assert _finish(module) == ASSEMBLED
    assert _finish(module, tmp_path) == ASSEMBLED


def test_dotfile_precedence_and_extended_nested_rules_apply_per_file(
    tmp_path: Path,
) -> None:
    pytest.importorskip("ruff")
    (tmp_path / "pyproject.toml").write_text('[tool.ruff.lint]\nselect=["I"]\n')
    (tmp_path / "ruff.toml").write_text('[lint]\nselect=["I"]\n')
    (tmp_path / ".ruff.toml").write_text('[lint]\nselect=["F"]\n')
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "ruff.toml").write_text('extend="../ruff.toml"\n')
    module = tmp_path / "m.py"
    child = nested / "m.py"
    for file in (module, child):
        file.write_text(ORIGINAL)
    assert not project_configures_ruff_import_sorting(module)
    assert project_configures_ruff_import_sorting(child)
    assert _finish(module, tmp_path) == ASSEMBLED
    assert _finish(child, tmp_path) == SORTED


def test_standalone_only_project_and_nested_non_ruff_pyproject_preserve_parent_policy(
    tmp_path: Path,
) -> None:
    pytest.importorskip("ruff")
    (tmp_path / "ruff.toml").write_text('[lint]\nselect=["I"]\n[format]\nquote-style="single"\n')
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "pyproject.toml").write_text('[project]\nname="nested"\nversion="0"\n')
    module = nested / "m.py"
    module.write_text(ORIGINAL)
    assert project_configures_ruff_import_sorting(module)
    assert _finish(module) == SORTED
    formatter = formatter_for_project(module).tool
    assert formatter is not None and formatter('value = "string"') == "value = 'string'"
