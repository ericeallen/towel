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

"""The CLI checks before copying and owns the checker's entire lifetime."""

from pathlib import Path
from typing import Mapping, Sequence
from unittest.mock import Mock

import pytest

from towel import cli
from towel.type_inference import CheckFailure, CheckResult, CheckSuccess, TypeDiagnostic, TypeOracle
from tests.test_cli_integration import invoke


@pytest.mark.parametrize("fail", [False, True])
def test_oracle_closes_after_success_or_output_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail: bool
) -> None:
    source = tmp_path / "input"
    source.mkdir()
    (source / "main.py").write_text("value = 3\n")
    output = tmp_path / "output"
    oracle = Mock(spec=TypeOracle)

    def select(path: Path) -> TypeOracle:
        assert path == source
        assert not output.exists(), "Select the original checker before creating output"
        return oracle

    def check_project(
        sources: Mapping[str, str], *, excluded_paths: Sequence[str] = ()
    ) -> CheckResult:
        assert sources == {str(source / "main.py"): "value = 3\n"}
        assert not output.exists(), "Check the complete original before creating output"
        return CheckSuccess()

    oracle.check_project.side_effect = check_project

    def sidecar(*args: object) -> None:
        assert (output / "main.py").read_text() == "value = 3\n"
        if fail:
            raise OSError("simulated output failure")

    monkeypatch.setattr(cli, "_type_oracle", select)
    monkeypatch.setattr(cli, "_write_change_sidecar", sidecar)
    result = invoke(
        ["dry", str(source), str(output), "--no-interactive", "--no-format", "--progress", "none"]
    )
    assert result.status == int(fail), result.stderr
    oracle.check_project.assert_called_once_with({str(source / "main.py"): "value = 3\n"})
    oracle.close.assert_called_once_with()
    assert (source / "main.py").read_text() == "value = 3\n"


def test_cli_closes_checker_when_the_original_cannot_be_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, output = tmp_path / "input.py", tmp_path / "output.py"
    source.write_text("value = 3\n")
    oracle = Mock(spec=TypeOracle)
    oracle.check_project.return_value = CheckFailure("checker timed out")
    monkeypatch.setattr(cli, "_type_oracle", lambda path: oracle)
    result = invoke(
        ["dry", str(source), str(output), "--no-interactive", "--no-format", "--progress", "none"]
    )
    assert result.status == 1
    assert "Original project type check failed" in result.stderr
    assert "--no-types" in result.stderr
    oracle.close.assert_called_once_with()
    assert source.read_text() == "value = 3\n" and not output.exists()


def test_cli_reports_the_originals_errors_and_goes_on_to_close_the_checker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Errors the original check reports are compared with, not refused."""
    source, output = tmp_path / "input.py", tmp_path / "output.py"
    source.write_text("value = 3\n")
    oracle = Mock(spec=TypeOracle)
    oracle.check_project.return_value = CheckSuccess(
        (TypeDiagnostic(str(source), "Existing type error"),)
    )
    monkeypatch.setattr(cli, "_type_oracle", lambda path: oracle)
    result = invoke(
        ["dry", str(source), str(output), "--no-interactive", "--no-format", "--progress", "none"]
    )
    assert result.status == 0, result.stderr
    assert "The original project's type check reports 1 error(s) in 1 file(s)." in result.stderr
    assert "--no-types" not in result.stderr
    oracle.close.assert_called_once_with()
    assert source.read_text() == "value = 3\n" == output.read_text()


def test_cancelled_run_never_constructs_checker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "input.py"
    source.write_text("value = 3\n")
    selector = Mock()
    monkeypatch.setattr(cli, "_type_oracle", selector)
    result = invoke(["dry", str(source), str(tmp_path / "output.py"), "--interactive"], stdin="n\n")
    assert result.status == 0
    selector.assert_not_called()
    assert not (tmp_path / "output.py").exists()
