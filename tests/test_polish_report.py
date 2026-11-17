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

"""Whole-project reporting after incremental refactoring (polish audit item 1)."""

from pathlib import Path

from tests.test_run_report_engine import DUPLICATES
from towel.unification.refactor_engine import UnificationRefactorEngine

DECLINED = (
    "def first(x):\n    a = x + 1\n    b = a * 2\n    return b\n\n"
    "def second(x):\n    a = x + 2\n    b = a * 2\n    return b\n"
)


def test_incremental_run_reports_unchanged_files(tmp_path: Path) -> None:
    """Polish item 1: rewriting one module must not erase another module's declines."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "changed.py").write_text(DUPLICATES, encoding="utf-8")
    (project / "unchanged.py").write_text(DECLINED, encoding="utf-8")
    output = tmp_path / "out"
    engine = UnificationRefactorEngine()
    results, termination = engine.refactor_directory_to_fixed_point(
        str(project), str(output), progress="none"
    )
    assert termination == "fixed_point"
    assert sum(count for count, _ in results.values()) > 0
    assert (output / "unchanged.py").read_text(encoding="utf-8") == DECLINED

    fresh = UnificationRefactorEngine()
    proposals = fresh.analyze_directory(str(output), progress="none")
    unchanged = tuple(
        site
        for site in fresh.last_left_alone
        if all(Path(block.path).name == "unchanged.py" for block in site.blocks)
    )
    assert unchanged, "The untouched module must contain declined candidate pairs"
    report = engine.run_report
    assert all(site in report.left_alone for site in unchanged)
    # A fresh engine does not know which helpers this run generated, so it
    # may classify their pairs differently. It must still see the same pairs.
    assert sum(report.declined_pairs.values()) == sum(fresh.declined_pairs.values())
    assert report.candidate_pairs == fresh.last_pair_tally.candidate_pairs
    assert report.duplicate_pairs == fresh.last_pair_tally.duplicate_pairs
    assert report.emitted_proposals == len(proposals)
    assert report.candidate_pairs == (
        sum(report.declined_pairs.values())
        + report.duplicate_pairs
        + report.overlapping_proposals
        + report.emitted_proposals
    )
