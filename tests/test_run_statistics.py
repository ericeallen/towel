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

"""Statistics that explain losses: every candidate pair is accounted for.

The run report counted declined pairs and unapplied proposals, but not the
pairs that only repeated another pair's proposal, the proposals the overlap
filter dropped, or how many helper signatures the checker judged. A reader
could not tell where a project's duplicates went. ``--stats`` now accounts
for each candidate pair of the last whole analysis exactly once, and counts
the signatures over the whole run, without changing what the run writes.
"""

from __future__ import annotations

import contextlib
import filecmp
import io
import multiprocessing
from pathlib import Path
from typing import List, Mapping, Sequence, Tuple

import pytest

from towel.type_inference import CheckResult, CheckSuccess
from towel.unification.annotation_ladder import Verified
from towel.unification.materialize import Materialization
from towel.unification.models import PairTally
from towel.unification.parallel import ParallelEvaluation
from towel.unification.refactor_engine import UnificationRefactorEngine
from tests.test_cli_integration import invoke
from tests.test_declined_run_reports import BLOCK, _RefusesEveryCandidate, _similar_functions


def _analyzed(engine: UnificationRefactorEngine, path: Path) -> Tuple[PairTally, int, int]:
    """One analysis of ``path``: its pair tally, its declined pairs, and its emitted proposals."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        emitted = engine.analyze_files([str(path)], progress="none")
    return engine._pair_tally, sum(engine.declined_pairs.values()), len(emitted)


def test_every_candidate_pair_is_counted_exactly_once(tmp_path: Path) -> None:
    """Twenty similar functions: most pairs repeat a proposal, and some proposals overlap."""
    path = _similar_functions(tmp_path / "project", 20)
    tally, declined, emitted = _analyzed(UnificationRefactorEngine(min_lines=2), path)
    assert tally.candidate_pairs > 0
    assert tally.duplicate_pairs > 0, "the cluster's pairs repeat each other's proposal"
    assert tally.candidate_pairs == declined + tally.duplicate_pairs + tally.distinct_proposals
    assert 0 < emitted <= tally.distinct_proposals


def test_an_analysis_without_pairs_counts_nothing(tmp_path: Path) -> None:
    path = tmp_path / "m.py"
    path.write_text("def only(x):\n    return x + 1\n", encoding="utf-8")
    tally, declined, emitted = _analyzed(UnificationRefactorEngine(min_lines=2), path)
    assert (tally, declined, emitted) == (PairTally(), 0, 0)


@pytest.mark.skipif(
    "fork" not in multiprocessing.get_all_start_methods(), reason="needs the fork start method"
)
def test_forked_evaluation_tallies_what_serial_evaluation_tallies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Duplicates are decided when verdicts settle in pair order, so forking moves no count."""
    path = _similar_functions(tmp_path / "project", 24)
    serial = _analyzed(UnificationRefactorEngine(min_lines=2), path)
    monkeypatch.setattr(ParallelEvaluation, "PARALLEL_PAIR_THRESHOLD", 1)
    monkeypatch.setattr(ParallelEvaluation, "PARALLEL_PROBE_PAIRS", 8)
    monkeypatch.setattr(ParallelEvaluation, "PARALLEL_MIN_PROJECTED_SECONDS", 0.0)
    monkeypatch.setattr(ParallelEvaluation, "_parallel_workers", lambda self: 2)
    forked: List[bool] = []
    original = ParallelEvaluation._evaluate_pairs_parallel

    def recording(self: ParallelEvaluation, *args: object, **kwargs: object) -> object:
        forked.append(True)
        return original(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(ParallelEvaluation, "_evaluate_pairs_parallel", recording)
    parallel = _analyzed(UnificationRefactorEngine(min_lines=2), path)
    assert forked, "the second analysis forked"
    assert parallel == serial


def test_the_run_report_accounts_for_the_last_whole_analysis(tmp_path: Path) -> None:
    """At the fixed point, the report's pair counts add up and match a fresh analysis."""
    root = tmp_path / "project"
    _similar_functions(root, 6)
    engine = UnificationRefactorEngine(min_lines=2)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        engine.refactor_directory_to_fixed_point(str(root), str(tmp_path / "out"), progress="none")
    report = engine.run_report
    assert report.candidate_pairs > 0
    assert report.candidate_pairs == (
        sum(report.declined_pairs.values())
        + report.duplicate_pairs
        + report.overlapping_proposals
        + report.emitted_proposals
    )
    fresh = UnificationRefactorEngine(min_lines=2)
    tally, declined, emitted = _analyzed(fresh, tmp_path / "out" / "m.py")
    assert report.candidate_pairs == tally.candidate_pairs
    assert report.emitted_proposals == emitted
    assert report.signatures_verified == report.signatures_refused == 0, "no checker ran"


class _AcceptsEveryCandidate(_RefusesEveryCandidate):
    """Passes the original project and every candidate."""

    def check_project(
        self, sources: Mapping[str, str], *, excluded_paths: Sequence[str] = ()
    ) -> CheckResult:
        self.checks += 1
        return CheckSuccess()


def _typed_run(
    root: Path, oracle: _RefusesEveryCandidate, monkeypatch: pytest.MonkeyPatch
) -> Tuple[str, List[bool]]:
    """``towel dry --stats`` on two duplicated functions; its stdout, and each attempt's verdict."""
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "m.py").write_text(
        BLOCK.format(name="fa", tag="a", number=1) + BLOCK.format(name="fb", tag="a", number=2),
        encoding="utf-8",
    )
    monkeypatch.setattr("towel.cli._type_oracle", lambda path: oracle)
    verdicts: List[bool] = []
    original = Materialization._attempt

    def recording(self: Materialization, *args: object, **kwargs: object) -> object:
        outcome = original(self, *args, **kwargs)  # type: ignore[arg-type]
        verdicts.append(isinstance(outcome, Verified))
        return outcome

    monkeypatch.setattr(Materialization, "_attempt", recording)
    target = str(root / "pkg")
    result = invoke(
        ["dry", target, target, "--no-interactive", "--no-format", "--progress", "none", "--stats"]
    )
    assert result.status == 0, result
    return result.stdout, verdicts


def test_refused_signatures_are_counted_attempt_by_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stdout, verdicts = _typed_run(tmp_path / "project", _RefusesEveryCandidate(), monkeypatch)
    assert verdicts and not any(verdicts)
    count = len(verdicts)
    assert f"{count} signature attempt(s): 0 verified, {count} refused" in stdout
    assert "1 proposal(s) not applied" in stdout


def test_a_verified_signature_is_counted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stdout, verdicts = _typed_run(tmp_path / "project", _AcceptsEveryCandidate(), monkeypatch)
    assert verdicts == [True]
    assert "1 signature attempt(s): 1 verified, 0 refused" in stdout


def _project(root: Path) -> Path:
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "m.py").write_text(
        "".join(BLOCK.format(name=f"f{i}", tag="a", number=i) for i in range(4)),
        encoding="utf-8",
    )
    return root / "pkg"


def test_stats_change_nothing_the_run_writes(tmp_path: Path) -> None:
    source = _project(tmp_path / "project")
    outputs = {}
    for flag in ("--no-stats", "--stats"):
        output = tmp_path / flag.strip("-")
        result = invoke(
            ["dry", str(source), str(output), "--no-interactive", "--no-types", "--progress"]
            + ["none", flag]
        )
        assert result.status == 0, result
        outputs[flag] = (output, result.stdout)
    (plain, plain_stdout), (counted, counted_stdout) = outputs["--no-stats"], outputs["--stats"]
    comparison = filecmp.dircmp(plain, counted)
    assert sorted(comparison.left_list) == sorted(comparison.right_list)
    assert not comparison.diff_files and not comparison.funny_files
    for name in comparison.common_files:
        if name.endswith(".py"):
            assert (plain / name).read_bytes() == (counted / name).read_bytes()
    assert "Statistics" not in plain_stdout
    assert "candidate pair(s):" in counted_stdout
    assert "signature attempt(s)" not in counted_stdout, "no checker judged a signature"


def test_a_quick_preview_says_it_has_no_statistics(tmp_path: Path) -> None:
    source = _project(tmp_path / "project")
    result = invoke(["preview", str(source), "--quick", "--stats", "--progress", "none"])
    assert result.status == 0, result
    assert "a --quick list has no statistics" in result.stdout
    assert "candidate pair(s):" not in result.stdout


def test_overlapping_proposals_are_the_ones_the_filter_drops(tmp_path: Path) -> None:
    """The report's overlap count is exactly what the overlap filter removes from the distinct list."""
    path = _similar_functions(tmp_path / "project", 20)
    engine = UnificationRefactorEngine(min_lines=2)
    distinct: List[int] = []
    original = UnificationRefactorEngine.process_block_pairs

    def recording(self: UnificationRefactorEngine, *args: object, **kwargs: object) -> object:
        proposals = original(self, *args, **kwargs)  # type: ignore[arg-type]
        distinct.append(len(proposals))
        return proposals

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(UnificationRefactorEngine, "process_block_pairs", recording)
        tally, _declined, emitted = _analyzed(engine, path)
    assert distinct == [tally.distinct_proposals]
    assert distinct[0] > emitted, "the cluster's proposals overlap"
