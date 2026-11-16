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

"""Runtime items 5/6: omissions say why files were left out of analysis."""

import pytest

from towel.cli_reporting import census_line, outcome_line
from towel.unification.fixed_point import RunReport
from towel.unification.models import SkippedFile

IGNORED = "Git ignores it (use --include-ignored to analyse it)"
EXCLUDED = "its directory is excluded from analysis"
UNREADABLE = "it cannot be read: Permission denied"


def test_census_groups_omissions_by_reason() -> None:
    """Runtime item 5: excluded and ignored files are not described as unreadable."""
    report = RunReport(
        files_analysed=1,
        functions_analysed=2,
        skipped_files=(
            SkippedFile("generated.py", IGNORED),
            SkippedFile("generated2.py", IGNORED),
            SkippedFile("build/m.py", EXCLUDED),
            SkippedFile("locked.py", UNREADABLE),
        ),
    )
    assert census_line(report) == (
        "Analysed 1 Python file (2 functions). Skipped 1 file it could not read or parse."
        " Skipped 2 files Git ignores (--include-ignored includes them)."
        " Skipped 1 file in excluded directories."
    )


@pytest.mark.parametrize("reason", [IGNORED, EXCLUDED])
def test_omitted_only_project_does_not_claim_to_be_unreadable(reason: str) -> None:
    """Runtime item 5: no selected input is distinct from an input read failure."""
    report = RunReport(skipped_files=(SkippedFile("m.py", reason),))
    assert outcome_line(report, 0, "src") == "No Python files selected for analysis under src."


def test_mixed_omissions_name_only_selected_files_as_unreadable() -> None:
    """Runtime items 5/6: an ignored readable file does not contradict the outcome."""
    report = RunReport(
        skipped_files=(
            SkippedFile("ignored.py", IGNORED),
            SkippedFile("locked.py", UNREADABLE),
        )
    )
    assert outcome_line(report, 0, "src") == "No selected Python file under src could be read."


def test_unreadable_only_wording_is_preserved() -> None:
    """Runtime item 6: retain the established read-failure wording."""
    report = RunReport(skipped_files=(SkippedFile("locked.py", UNREADABLE),))
    assert census_line(report).endswith("Skipped 1 file it could not read or parse.")
    assert outcome_line(report, 0, "src") == "No Python file under src could be read."


def test_parse_failure_census_names_parsing() -> None:
    """Round 2 minor: invalid syntax must not be described solely as a read failure."""
    report = RunReport(skipped_files=(SkippedFile("bad.py", "line 1: invalid syntax"),))
    assert "could not read or parse" in census_line(report)
