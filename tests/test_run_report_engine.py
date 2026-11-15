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

import logging
from pathlib import Path

import pytest

from towel.unification.fixed_point import Publication
from towel.unification.refactor_engine import UnificationRefactorEngine

BODY = (
    "    a = x + 1\n    b = a * 2\n    c = b + 3\n"
    "    d = c + 4\n    e = d * 3\n    f = e - 12\n"
    "    g = f // 3\n    h = g + 9\n    i = h * 2\n"
    "    j = i - 18\n    k = j // 2\n    return k\n"
)
DUPLICATES = "def first(x):\n" + BODY + "\ndef second(x):\n" + BODY

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


def test_each_declined_pair_is_located(tmp_path: Path) -> None:
    """Item 4: ``left_alone`` names each declined pair's files and lines, under its reason."""
    target = _many_left_alone(tmp_path / "project", 1)
    engine = UnificationRefactorEngine()
    engine.refactor_directory_to_fixed_point(str(target), str(tmp_path / "out"), progress="none")
    located = {
        (site.reason, tuple((Path(b.path).name, b.first_line, b.last_line) for b in site.blocks))
        for site in engine.run_report.left_alone
    }
    assert ("unprofitable_helper", (("module_0.py", 2, 5), ("module_0.py", 9, 12))) in located
    assert len(located) == sum(engine.run_report.declined_pairs.values()) == 3


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
