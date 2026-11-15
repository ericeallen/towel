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

"""Audit W2 / finding 11: preview can gate a build without writing anything."""

import json
from pathlib import Path

import pytest

from tests.test_cli_integration import DUPLICATES, invoke
from tests.test_project_config import FLAGS


@pytest.mark.parametrize("presentation", [[], ["--summary"], ["--diff"], ["--json"]])
@pytest.mark.parametrize("duplicated", [False, True])
def test_w2_check_changes_only_status_and_never_the_tree(
    tmp_path: Path, presentation: list[str], duplicated: bool
) -> None:
    source = tmp_path / "example.py"
    source.write_text(DUPLICATES if duplicated else "def only(x):\n    return x + 1\n")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    arguments = ["preview", str(source), *FLAGS, *presentation]
    ordinary = invoke(arguments)
    checked = invoke([*arguments, "--check"])
    assert ordinary.status == 0, ordinary.stderr
    assert checked.status == (3 if duplicated else 0), checked.stderr
    assert checked.stdout == ordinary.stdout
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_w2_check_keeps_failure_status_and_json(tmp_path: Path) -> None:
    # This base is strict; the runtime lane will add relaxed parse-file skipping.
    source = tmp_path / "broken.py"
    source.write_text("def broken(:\n")
    result = invoke(["preview", str(source), "--check", "--json", *FLAGS])
    assert result.status == 1, result.stderr
    assert json.loads(result.stdout)["status"] == "error"
    assert not (tmp_path / ".towel-helpers.json").exists()


def test_w2_quick_check_is_an_argument_error(tmp_path: Path) -> None:
    source = tmp_path / "example.py"
    source.write_text(DUPLICATES)
    result = invoke(["preview", str(source), "--quick", "--check", *FLAGS])
    assert result.status == 2
    assert "--quick cannot be combined" in result.stderr and "--check" in result.stderr


def test_w2_check_uses_project_policy(tmp_path: Path) -> None:
    source = tmp_path / "example.py"
    source.write_text(DUPLICATES)
    (tmp_path / "pyproject.toml").write_text("[tool.towel]\nmin-lines = 99\n")
    assert invoke(["preview", str(source), "--check", *FLAGS]).status == 0
    assert invoke(["preview", str(source), "--check", "--no-config", *FLAGS]).status == 3
