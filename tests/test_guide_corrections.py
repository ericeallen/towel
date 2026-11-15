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

"""Statements in the user guides that the 1.949 user audit found false stay corrected.

Each test names its audit item. Where the program can be asked, the test asks
it, so the guide follows the behaviour and not an earlier sentence; where the
behaviour is already pinned elsewhere, the test names that regression.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _guide(name: str) -> str:
    return (ROOT / "docs" / name).read_text(encoding="utf-8")


def _compact(text: str) -> str:
    return " ".join(text.split())


def _section(text: str, heading: str) -> str:
    """The text under one heading, up to the next heading of the same or a higher level."""
    assert text.count(f"\n{heading}\n") == 1, f"Expected one {heading!r}"
    level = len(heading) - len(heading.lstrip("#"))
    lines: list[str] = []
    for line in text.split(f"\n{heading}\n", 1)[1].splitlines():
        marks = len(line) - len(line.lstrip("#"))
        if 0 < marks <= level and line[marks : marks + 1] == " ":
            break
        lines.append(line)
    return "\n".join(lines)


def test_usage_guide_does_not_promise_that_an_interrupted_write_changes_nothing() -> None:
    """Audit finding 12 (Kimi's review, finding 1): the one wrong statement that matters.

    The guide said "A failed or interrupted run leaves the project as it was". An
    in-place batch killed part-way leaves some files changed and a recovery journal:
    tests/test_change_transactions.py::test_killed_process_can_be_recovered_by_cli.
    A reader who believed the guide would not look for the journal.
    """
    section = _compact(_section(_guide("USAGE_GUIDE.md"), "## Command-Line Usage"))
    assert "interrupted run leaves the project as it was" not in section
    assert "can leave some files changed and others not" in section
    assert "`.towel-transaction-<id>` journal" in section
    assert "`towel recover` restores the original files" in section
    assert "(CLI_GUIDE.md#recovery-and-interrupted-writes)" in section
    reference = _compact(_section(_guide("CLI_GUIDE.md"), "## Output and in-place changes"))
    assert "they can observe a partially applied batch" in reference
    assert "interrupted batches retain a recovery journal" in reference
