# Copyright 2026 Eric Allen
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

"""Runtime item 11: one nonblocking suitability notice before a relaxed run."""

import json
from pathlib import Path

import pytest

from tests.test_cli_integration import invoke, LITERAL_DUPLICATES


@pytest.mark.parametrize("command", ["preview", "dry"])
def test_notice_once_and_additive_json_findings(tmp_path: Path, command: str) -> None:
    """Runtime 11: finalizer evidence survives JSON and prints exactly one notice."""
    source = tmp_path / "model.py"
    source.write_text("class Resource:\n    def __del__(self): pass\n\n" + LITERAL_DUPLICATES)
    argv = [command, str(source)]
    if command == "dry":
        argv.extend([str(tmp_path / "out.py"), "--no-interactive"])
    result = invoke([*argv, "--json", "--no-types", "--no-format", "--progress", "none"])
    assert result.status == 0, result.stderr
    findings = json.loads(result.stdout)["relaxed_findings"]
    assert len(findings) == 1
    assert findings[0]["kind"] == "finalizer"
    assert findings[0]["line"] == 2
    assert "model.py" in findings[0]["path"]
    assert result.stderr.count("Notice: relaxed mode") == 1
    assert "--strict" in result.stderr and "strict = true" in result.stderr


@pytest.mark.parametrize("flag", ["--quiet", "--strict"])
def test_quiet_and_strict_suppress_notice(tmp_path: Path, flag: str) -> None:
    """Runtime 11: quiet suppresses the notice; strict has no relaxed finding."""
    source = tmp_path / "model.py"
    source.write_text("class Resource:\n    def __del__(self): pass\n")
    result = invoke(["preview", str(source), "--json", "--no-types", "--no-format", flag])
    assert result.status == 0, result.stderr
    assert "Notice: relaxed mode" not in result.stderr
    findings = json.loads(result.stdout)["relaxed_findings"]
    assert bool(findings) == (flag == "--quiet")


def test_no_lifetime_observers_has_no_notice(tmp_path: Path) -> None:
    """Runtime 11: unremarkable analysed code gets no suitability warning."""
    source = tmp_path / "model.py"
    source.write_text("value = 1\n")
    result = invoke(["preview", str(source), "--json", "--no-types", "--no-format"])
    assert result.status == 0, result.stderr
    assert json.loads(result.stdout)["relaxed_findings"] == []
    assert "Notice: relaxed mode" not in result.stderr
