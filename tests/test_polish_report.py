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

import json
from pathlib import Path
from unittest.mock import patch

from tests.test_cli_integration import invoke

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


def test_last_analysis_covering_every_file_supplies_the_report(tmp_path: Path) -> None:
    """The convergence pass already judged every pair when every file changed."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "changed.py").write_text(DUPLICATES, encoding="utf-8")
    engine = UnificationRefactorEngine()
    with patch.object(engine, "analyze_directory", wraps=engine.analyze_directory) as analyze:
        results, termination = engine.refactor_directory_to_fixed_point(
            str(project), str(tmp_path / "out"), progress="none"
        )
    assert termination == "fixed_point" and len(results) == 1
    assert analyze.call_count == 2, analyze.call_args_list
    report = engine.run_report
    assert report.candidate_pairs == engine.last_pair_tally.candidate_pairs
    assert report.declined_pairs == engine.declined_pairs
    assert report.left_alone == engine.last_left_alone


def test_json_after_an_application_does_not_judge_hidden_pairs(tmp_path: Path) -> None:
    """JSON counts declined proposals after an application; it needs no final pair locations."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.py").write_text(DUPLICATES, encoding="utf-8")
    (project / "b.py").write_text(DECLINED, encoding="utf-8")
    with patch.object(
        UnificationRefactorEngine,
        "analyze_directory",
        autospec=True,
        side_effect=UnificationRefactorEngine.analyze_directory,
    ) as analyze:
        result = invoke(
            [
                "dry",
                str(project),
                str(project),
                "--strict",
                "--json",
                "--no-interactive",
                "--no-format",
                "--no-types",
                "--progress",
                "none",
            ]
        )
    assert result.status == 0, result
    data = json.loads(result.stdout)
    assert (data["applied"], data["left_alone"], data["analysed"]["files"]) == (1, 0, 2)
    assert analyze.call_count == 2, analyze.call_args_list
