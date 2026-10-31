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

from pathlib import Path

import pytest

from tests.test_cli_integration import DUPLICATES, evaluate_functions, invoke


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
    assert evaluate_functions(generated) == evaluate_functions(DUPLICATES)
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
