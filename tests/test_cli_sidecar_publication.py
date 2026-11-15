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

"""Optional naming metadata never misreports successful source publication."""

import json
from pathlib import Path

import pytest

from tests.test_cli_integration import LITERAL_DUPLICATES as DUPLICATES
from tests.test_cli_integration import evaluate_functions, invoke


@pytest.mark.parametrize("in_place", [False, True])
@pytest.mark.parametrize("collision", ["directory", "symlink", "io_error"])
def test_optional_sidecar_failure_reports_successful_source_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    in_place: bool,
    collision: str,
) -> None:
    """A real extraction succeeds even when auxiliary naming metadata cannot be written."""
    from towel import cli

    source = tmp_path / "input.py"
    source.write_text(DUPLICATES)
    output = source if in_place else tmp_path / "output.py"
    sidecar = output.with_name(output.name + ".towel-helpers.json")
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("keep\n")
    if collision == "directory":
        sidecar.mkdir()
        (sidecar / "keep").write_text("keep\n")
    elif collision == "symlink":
        sidecar.symlink_to(sentinel)
    else:

        def fail_write(target: Path, text: str) -> None:
            raise OSError("simulated sidecar I/O failure")

        monkeypatch.setattr(cli, "_write_atomically", fail_write)
    result = invoke(
        ["dry", str(source), str(output), "--no-interactive", "--no-types", "--progress", "none"]
    )
    assert result.status == 0, result.stderr
    assert "Source publication succeeded" in caplog.text
    assert "optional naming sidecar" in caplog.text
    generated = output.read_text()
    assert "__extracted_func_" in generated
    assert evaluate_functions(generated, calls=((),)) == evaluate_functions(DUPLICATES, calls=((),))
    assert sentinel.read_text() == "keep\n"
    if collision == "directory":
        assert (sidecar / "keep").read_text() == "keep\n"
    elif collision == "symlink":
        assert sidecar.is_symlink()
    else:
        assert not sidecar.exists()
    assert not list(tmp_path.glob(sidecar.name + ".*"))
    if not in_place:
        assert source.read_text() == DUPLICATES


@pytest.mark.parametrize("directory", [False, True])
@pytest.mark.parametrize("in_place", [False, True])
def test_w10_metadata_can_be_omitted_without_losing_refactoring_or_helper_inventory(
    tmp_path: Path, directory: bool, in_place: bool
) -> None:
    """Audit W10: projects can omit the naming sidecar; call-site changes are then absent."""
    from towel.cli import _change_sidecar_path

    source = tmp_path / ("source" if directory else "source.py")
    if directory:
        source.mkdir()
    source_file = source / "example.py" if directory else source
    source_file.write_text(DUPLICATES)
    output = source if in_place else tmp_path / ("output" if directory else "output.py")
    result = invoke(
        [
            "dry",
            str(source),
            str(output),
            "--no-interactive",
            "--no-types",
            "--no-format",
            "--progress",
            "none",
            "--no-helper-metadata",
        ]
    )
    assert result.status == 0, result.stderr
    generated = (output / "example.py" if directory else output).read_text()
    assert "__extracted_func_" in generated
    assert evaluate_functions(generated, calls=((),)) == evaluate_functions(DUPLICATES, calls=((),))
    assert not _change_sidecar_path(output).exists()
    inventory_root = output if directory else output.parent
    inventory = invoke(["rename-helpers", str(inventory_root), "--list", "--json"])
    assert inventory.status == 0, inventory.stderr
    helpers = json.loads(inventory.stdout)["helpers"]
    assert helpers and all(helper["changes"] == [] for helper in helpers)
    if not in_place:
        assert source_file.read_text() == DUPLICATES


def test_w10_project_can_disable_metadata_and_cli_can_restore_it(tmp_path: Path) -> None:
    """Audit W10: a team's policy can omit metadata without removing per-run control."""
    from towel.cli import _change_sidecar_path

    source = tmp_path / "source.py"
    source.write_text(DUPLICATES)
    (tmp_path / "pyproject.toml").write_text("[tool.towel]\nhelper-metadata = false\n")
    for enabled in (False, True):
        output = tmp_path / f"output-{enabled}.py"
        result = invoke(
            [
                "dry",
                str(source),
                str(output),
                "--no-interactive",
                "--no-types",
                "--no-format",
                "--progress",
                "none",
                *(["--helper-metadata"] if enabled else []),
            ]
        )
        assert result.status == 0, result.stderr
        assert _change_sidecar_path(output).exists() is enabled
