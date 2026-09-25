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

"""Command-line paths the suite did not reach: recovery, JSON listing, empty runs, odd suffixes.

Every test drives ``cli.main`` with only process I/O replaced, as
``test_cli_integration`` does, so the parser, the handlers and the engine run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_cli_integration import DUPLICATES, HELPER, invoke
from tests.test_recovery_journal import _interrupted_transaction


def test_recover_restores_the_journal_targets_and_exits_cleanly(tmp_path: Path) -> None:
    files, journal = _interrupted_transaction(tmp_path)
    result = invoke(["recover", str(journal)])
    assert result.status == 0
    assert all(path.read_bytes() == b"value = 1\n" for path in files)
    assert not journal.exists()


def test_recover_reports_an_invalid_journal_with_a_failure_status(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    result = invoke(["recover", str(missing)])
    assert result.status == 1
    assert result.stderr == (
        f"Error: Invalid transaction directory: {missing}: a journal's name begins with"
        " .towel-transaction-\n"
    )
    assert result.stdout == ""


def test_helper_listing_as_json_is_the_inventory_and_writes_nothing(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(HELPER)
    result = invoke(["rename-helpers", str(tmp_path), "--list", "--json"])
    assert result.status == 0
    payload = json.loads(result.stdout)
    assert [entry["name"] for entry in payload["helpers"]] == ["__extracted_func_0"]
    assert "parameter" in payload["mapping_format"]
    assert (tmp_path / "m.py").read_text() == HELPER


@pytest.mark.parametrize("layout", ["file", "directory"])
def test_dry_with_nothing_to_extract_says_so_and_copies_the_input(
    tmp_path: Path, layout: str
) -> None:
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    module = source_dir / "m.py"
    module.write_text("def only(x):\n    return x + 1\n")
    target, out = (
        (module, tmp_path / "out.py") if layout == "file" else (source_dir, tmp_path / "out")
    )
    result = invoke(["dry", str(target), str(out), "--no-interactive", "--progress", "none"])
    assert result.status == 0, result.stderr
    if layout == "file":
        assert "\nNo refactorings found!\n" in result.stdout
        assert out.read_text() == module.read_text()
    else:
        assert "\nNo refactorings found! Termination: fixed_point\n" in result.stdout
        assert (out / "m.py").read_text() == module.read_text()


@pytest.mark.parametrize(
    "flag, answer, analyzed",
    [
        ("--interactive", "n\n", False),
        ("--interactive", "y\ny\n", True),
        ("--interactive", "", False),
        ("--no-interactive", "", True),
    ],
    ids=["declined", "accepted", "closed-stdin", "no-interactive-never-asks"],
)
def test_input_without_a_py_suffix_is_analyzed_only_on_confirmation(
    tmp_path: Path, flag: str, answer: str, analyzed: bool
) -> None:
    """Asked only when interactive: round 4's D5 found ``--no-interactive`` still prompting."""
    source = tmp_path / "r9p2_script"
    source.write_text(DUPLICATES)
    out = tmp_path / "out.py"
    result = invoke(["dry", str(source), str(out), flag, "--progress", "none"], stdin=answer)
    assert result.status == 0, result.stderr
    assert f"Warning: '{source}' is not a Python file (.py)" in result.stdout
    assert ("Analyze anyway?" in result.stdout) is (flag == "--interactive")
    assert out.exists() is analyzed
    assert ("Applied 1 refactoring(s)" in result.stdout) is analyzed
    assert source.read_text() == DUPLICATES
