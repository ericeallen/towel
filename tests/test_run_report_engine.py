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

"""The engine's side of a run's report: what it read, where it left code alone, and the seam
before publication (audit findings 3 and 7).

``RunReport`` gained the census the analysis used to discard, and the place
of every pair it declined; the two fixed-point drivers gained a callback
that hears what a run is about to write before it writes it, which ``dry``
uses to ask once it knows.
"""

from __future__ import annotations

import gc
import logging
from dataclasses import replace
from pathlib import Path

import pytest

from tests.test_cli_integration import invoke
from tests.test_helpers import lifetime_order_certified
from towel.unification.fixed_point import Publication, RunReport
from towel.unification.models import (
    BlockLocation,
    ClassInfo,
    CodeBlockPair,
    FunctionArtifact,
    LeftAlone,
    PairVerdict,
    RefactoringProposal,
)
from towel.unification.progress import ProgressMode
from towel.unification.refactor_engine import UnificationRefactorEngine

BODY = (
    "    a = x + 1\n    b = a * 2\n    c = b + 3\n"
    "    d = c + 4\n    e = d * 3\n    f = e - 12\n"
    "    g = f // 3\n    h = g + 9\n    i = h * 2\n"
    "    j = i - 18\n    k = j // 2\n    return k\n"
)
# Literal inputs, as the python lane made the CLI tests: no argument is handed over.
DUPLICATES = "def first():\n    x = 1\n" + BODY + "\ndef second():\n    x = 2\n" + BODY

PRICING = (
    "def format_price_usd(amount):\n"
    "    rounded = round(amount, 2)\n"
    '    whole, _, frac = f"{rounded:.2f}".partition(".")\n'
    '    whole = f"{int(whole):,}"\n'
    '    return "$" + whole + "." + frac\n'
    "\n\n"
    "def format_price_chf(amount):\n"
    "    rounded = round(amount, 2)\n"
    '    whole, _, frac = f"{rounded:.2f}".partition(".")\n'
    '    whole = f"{int(whole):,}"\n'
    '    return "CHF " + whole + "." + frac\n'
)
"""The audit's scenario 01: similar code, all of it left alone."""

UTF16_MODULE = "def wide(x):\n    return x\n".encode("utf-16")


def _many_left_alone(root: Path, count: int) -> Path:
    root.mkdir()
    for index in range(count):
        body = PRICING.replace("format_price_", f"format_{index}_price_")
        (root / f"module_{index}.py").write_text(body)
    return root


def test_the_report_counts_what_the_run_read_and_skipped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Item 1: files, functions and the skipped files reach ``RunReport``; each is warned of once."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "example.py").write_text(DUPLICATES)
    (project / "wide.py").write_bytes(UTF16_MODULE)
    engine = UnificationRefactorEngine()
    with caplog.at_level(logging.WARNING, logger="towel"):
        results, _ = engine.refactor_directory_to_fixed_point(
            str(project), str(tmp_path / "out"), progress="none"
        )
    report = engine.run_report
    assert sum(count for count, _ in results.values()) == 1
    assert (report.files_analysed, report.functions_analysed) == (1, 2)
    [skipped] = report.skipped_files
    assert skipped.path == str(tmp_path / "out" / "wide.py")
    assert skipped.reason.startswith("it is not UTF-8 text")
    # The run analysed the project more than once; the file is named once.
    assert caplog.text.count("Skipping ") == 1


def test_a_directory_without_python_files_reads_nothing(tmp_path: Path) -> None:
    """Item 1: the census tells "no Python files" from "nothing alike" (audit finding 3)."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "README.txt").write_text("Nothing to see.\n")
    engine = UnificationRefactorEngine()
    engine.refactor_directory_to_fixed_point(
        str(tmp_path / "docs"), str(tmp_path / "out"), progress="none"
    )
    report = engine.run_report
    assert (report.files_analysed, report.functions_analysed, report.left_alone) == (0, 0, ())


@pytest.mark.strict_mode
def test_each_declined_pair_is_located(tmp_path: Path) -> None:
    """Item 4: ``left_alone`` names each declined pair's files and lines, under its reason."""
    target = _many_left_alone(tmp_path / "project", 1)
    engine = UnificationRefactorEngine()
    engine.refactor_directory_to_fixed_point(str(target), str(tmp_path / "out"), progress="none")
    located = {
        (site.reason, tuple((Path(b.path).name, b.first_line, b.last_line) for b in site.blocks))
        for site in engine.run_report.left_alone
    }
    reason = "unprofitable_helper" if lifetime_order_certified() else "lifetime_order_uncertified"
    assert (reason, (("module_0.py", 2, 5), ("module_0.py", 9, 12))) in located
    assert len(located) == sum(engine.run_report.declined_pairs.values()) == 3


@pytest.mark.strict_mode
def test_left_alone_locations_are_the_same_from_forked_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 4: locations travel with the verdicts settled from workers, in pair order."""
    target = _many_left_alone(tmp_path / "project", 2)

    def left_alone(parallel: bool) -> object:
        engine = UnificationRefactorEngine()
        if parallel:
            monkeypatch.setattr(engine, "_should_use_parallel", lambda pairs: pairs > 0)
            monkeypatch.setattr(engine, "_parallel_workers", lambda: 2)
        engine.analyze_directory(str(target), progress="none")
        return engine.last_left_alone

    serial = left_alone(False)
    assert serial and serial == left_alone(True)


def test_the_engine_hears_what_it_would_publish_before_it_does(tmp_path: Path) -> None:
    """Item 5: the drivers' callback hears the count and the files, then may stop the run."""
    target = tmp_path / "applied"
    target.mkdir()
    (target / "example.py").write_text(DUPLICATES)
    heard: list[Publication] = []

    def refuse(publication: Publication) -> None:
        heard.append(publication)
        assert not (tmp_path / "out").exists()
        raise RuntimeError("stop")

    with pytest.raises(RuntimeError, match="stop"):
        UnificationRefactorEngine().refactor_directory_to_fixed_point(
            str(target), str(tmp_path / "out"), progress="none", before_publication=refuse
        )
    assert not (tmp_path / "out").exists()
    [publication] = heard
    assert publication.applied == 1 and not publication.in_place
    assert [Path(path).name for path in publication.files] == ["example.py"]
    heard.clear()
    source = target / "example.py"
    _, applied, _ = UnificationRefactorEngine().refactor_to_fixed_point(
        str(source),
        progress="none",
        output_path=str(tmp_path / "one.py"),
        before_publication=heard.append,
    )
    assert applied == 1 and heard[0].files == (str(tmp_path / "one.py"),)


def _live_location_records() -> tuple[int, int]:
    objects = gc.get_objects()
    return (
        sum(type(o) is LeftAlone for o in objects),
        sum(type(o) is BlockLocation for o in objects),
    )


@pytest.mark.strict_mode
def test_repeated_declines_share_their_report_locations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Settlement keeps multiplicity without retaining a fresh record for every verdict."""
    target = _many_left_alone(tmp_path / "project", 1)
    engine = UnificationRefactorEngine()
    settle = engine._settle
    repeats = 256

    def repeated(pair: CodeBlockPair, verdict: PairVerdict) -> RefactoringProposal | None:
        if verdict.counted is not None:
            verdict = replace(verdict, identity=None)
            for _ in range(repeats - 1):
                settle(pair, verdict)
        return settle(pair, verdict)

    monkeypatch.setattr(engine, "_settle", repeated)
    before_sites, before_blocks = _live_location_records()
    engine.analyze_directory(str(target), progress="none")
    after_sites, after_blocks = _live_location_records()
    count = sum(engine.declined_pairs.values())
    assert count == 3 * repeats
    retained = after_sites - before_sites, after_blocks - before_blocks
    assert retained[0] <= 3 and retained[1] <= 6, retained
    assert len(engine.last_left_alone) == count


@pytest.mark.parametrize(("flags", "limit"), [([], 5), (["--json"], 0)])
def test_summary_holds_only_the_locations_it_can_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flags: list[str], limit: int
) -> None:
    """Every synthetic pair has a different right block; even deduplicating whole pairs is unbounded."""
    target = _many_left_alone(tmp_path / "project", 1)
    original = UnificationRefactorEngine._settle
    recorded = []

    def many_places(
        engine: UnificationRefactorEngine, pair: CodeBlockPair, verdict: PairVerdict
    ) -> RefactoringProposal | None:
        decline = replace(verdict, counted="unprofitable_helper", identity=None, proposal=None)
        for line in range(100, 356):
            original(engine, replace(pair, block2_range=(line, line)), decline)
        recorded.append(_live_location_records())
        return None

    monkeypatch.setattr(UnificationRefactorEngine, "_settle", many_places)
    before_sites, before_blocks = _live_location_records()
    result = invoke(
        ["preview", str(target), "--no-types", "--no-format", "--progress", "none", *flags]
    )
    assert result.status == 0, result
    retained = (
        max(s for s, _ in recorded) - before_sites,
        max(b for _, b in recorded) - before_blocks,
    )
    assert retained[0] <= limit and retained[1] <= 2 * limit, retained
    assert str(256 * len(recorded)) in result.stdout


def test_timed_preview_releases_the_previous_analysis_and_can_still_explain_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deadline during the next whole pass reports the last completed one, including ties."""
    from towel.cli_reporting import left_alone_lines
    from towel.execution_budget import BudgetExpired

    target = tmp_path / "sample.py"
    target.write_text(DUPLICATES + "\n" + PRICING)
    original = UnificationRefactorEngine.process_block_pairs
    previous_sites: set[int] = set()
    expected: list[str] = []
    retained: list[int] = []

    def interrupt_second(
        engine: UnificationRefactorEngine,
        pairs: list[CodeBlockPair],
        functions: list[FunctionArtifact],
        classes: list[ClassInfo],
        *,
        progress: ProgressMode,
    ) -> list[RefactoringProposal]:
        if previous_sites:
            retained.extend(
                id(o) for o in gc.get_objects() if type(o) is LeftAlone and id(o) in previous_sites
            )
            raise BudgetExpired()
        proposals = original(engine, pairs, functions, classes, progress=progress)
        sites = engine.last_left_alone
        assert proposals and sites
        previous_sites.update(map(id, sites))
        expected.extend(
            left_alone_lines(RunReport(left_alone=sites), lambda _: target.name, every=True)
        )
        return proposals

    monkeypatch.setattr(UnificationRefactorEngine, "process_block_pairs", interrupt_second)
    result = invoke(
        [
            "preview",
            str(target),
            "--explain",
            "--time-limit",
            "300",
            "--no-types",
            "--no-format",
            "--progress",
            "none",
        ]
    )
    assert result.status == 124, result
    assert "\n".join(expected) in result.stdout, result.stdout
    assert not retained, f"{len(retained)} records from the previous analysis remain live"


@pytest.mark.strict_mode
def test_a_standalone_analysis_can_follow_a_run_without_keeping_two_sets_of_sites(
    tmp_path: Path,
) -> None:
    first = _many_left_alone(tmp_path / "first", 1)
    second = _many_left_alone(tmp_path / "second", 1)
    engine = UnificationRefactorEngine()
    engine.refactor_directory_to_fixed_point(str(first), str(tmp_path / "out"), progress="none")
    previous = len(engine.run_report.left_alone)
    assert previous
    engine.analyze_directory(str(second), progress="none")
    assert engine.last_left_alone
    assert not any(
        type(obj) is BlockLocation and Path(obj.path).parent == tmp_path / "out"
        for obj in gc.get_objects()
    )
    assert all(
        Path(block.path).parent == tmp_path / "out"
        for site in engine.run_report.left_alone
        for block in site.blocks
    )
    assert len(engine.run_report.left_alone) == previous


def test_explain_counts_repetitions_separated_by_a_different_rank(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An intervening location of a different rank cannot separate repetitions in sorted output."""
    source = tmp_path / "sample.py"
    source.write_text(PRICING)
    original = UnificationRefactorEngine._settle
    retained = []
    pairs = []

    def alternate(
        engine: UnificationRefactorEngine, pair: CodeBlockPair, verdict: PairVerdict
    ) -> None:
        decline = replace(verdict, counted="unprofitable_helper", identity=None, proposal=None)
        for index in range(100):
            right = (10, 12) if index % 2 else (10, 11)
            original(engine, replace(pair, block1_range=(1, 2), block2_range=right), decline)
        retained.append(len(engine.last_left_alone))
        pairs.append(100)

    monkeypatch.setattr(UnificationRefactorEngine, "_settle", alternate)
    result = invoke(
        [
            "preview",
            str(source),
            "--quick",
            "--explain",
            "--no-types",
            "--no-format",
            "--progress",
            "none",
        ]
    )
    assert result.status == 0, result
    lines = [
        line.split(": ", 1)[0]
        for line in result.stdout.splitlines()
        if line.startswith("  sample.py:")
    ]
    expected = sum(pairs) // 2
    assert expected > 0
    assert (
        lines
        == ["  sample.py:1-2 and sample.py:10-12"] * expected
        + ["  sample.py:1-2 and sample.py:10-11"] * expected
    )
    assert max(retained) == 2
