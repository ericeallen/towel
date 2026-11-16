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

"""A proposal that goes stale mid-run is dropped, its files re-analyzed, and the run continues.

The directory driver computes a queue of proposals from one global pass and
applies them one at a time. A file edited after that pass (here, by a
wrapper around the apply step, standing in for an editor or another tool)
no longer matches the digests the proposal was computed from, so the apply
raises ``ChangeConflict``. The driver must not stop: it drops the stale
proposal, re-analyzes the affected files, queues the fresh proposals ahead
of the rest, and still reaches the fixed point with every duplicate removed.
The remaining tests cover the driver's three progress displays: a tqdm bar
(faked, so its calls are visible), the inline bar it falls back to under
``auto`` without tqdm, and a bar whose construction fails.
"""

from __future__ import annotations

import contextlib
import io
import logging
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import pytest

from towel.unification import progress
from towel.unification.models import RefactoringProposal
from towel.unification.refactor_engine import UnificationRefactorEngine

# Small fixtures exercise semantic guards independently of the product usefulness policy.

# Two block shapes, one per file, so each file holds exactly one same-file
# duplicate and nothing pairs across the files. Module-owned inputs keep
# these recovery/progress tests independent of argument transfer proofs.
ARITHMETIC = """
def {name}():
    total = value + 1
    scaled = total * 2
    label = str(scaled)
    return label
"""

LOOP = """
def {name}():
    parts = []
    for index in range(value):
        parts.append(str(index * 2))
    return ",".join(parts)
"""


def _project(root: Path) -> Dict[str, Path]:
    files = {
        "a.py": root / "a.py",
        "b.py": root / "b.py",
    }
    files["a.py"].write_text(
        textwrap.dedent(ARITHMETIC.format(name="a1") + ARITHMETIC.format(name="a2"))
    )
    files["b.py"].write_text(textwrap.dedent(LOOP.format(name="b1") + LOOP.format(name="b2")))
    return files


class _TerminalStream(io.StringIO):
    def isatty(self) -> bool:
        return True


def _run(
    root: Path, engine: UnificationRefactorEngine, **kwargs: Any
) -> tuple[Dict[str, Any], str]:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(_TerminalStream()):
        return engine.refactor_directory_to_fixed_point(str(root), str(root), **kwargs)


def _behaviour(path: Path) -> List[str]:
    namespace: Dict[str, Any] = {}
    exec(compile(path.read_text(), str(path), "exec"), namespace)
    functions = sorted(name for name in namespace if len(name) == 2 and name[1].isdigit())
    observed = []
    for name in functions:
        for value in (0, 3, 7):
            namespace["value"] = value
            observed.append(namespace[name]())
    return observed


def test_stale_proposal_is_dropped_and_the_run_still_reaches_the_fixed_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    files = _project(tmp_path)
    expected = {name: _behaviour(path) for name, path in files.items()}
    engine = UnificationRefactorEngine(skip_trivial_helpers=False, min_lines=3)
    original = UnificationRefactorEngine.apply_refactoring_multi_file
    edited: List[str] = []

    def edit_the_first_target_then_apply(
        self: UnificationRefactorEngine, proposal: RefactoringProposal
    ) -> Dict[str, str]:
        if not edited:
            target = Path(proposal.file_path)
            target.write_text(target.read_text() + "\n# edited between analysis and apply\n")
            edited.append(proposal.file_path)
        return original(self, proposal)

    monkeypatch.setattr(
        UnificationRefactorEngine, "apply_refactoring_multi_file", edit_the_first_target_then_apply
    )
    with (
        caplog.at_level(logging.INFO, logger="towel"),
        caplog.at_level(logging.DEBUG, logger="towel.rejections"),
    ):
        results, reason = _run(tmp_path, engine, progress="detail")

    assert reason == "fixed_point"
    assert len(edited) == 1
    messages = [record.getMessage() for record in caplog.records]
    assert any("Dropped stale proposal" in m and "Stale proposal" in m for m in messages), messages
    assert any(m.startswith("STALE: ") for m in messages), messages
    # Both files were still refactored, including the one edited under the run.
    applied = {Path(path).name: count for path, (count, _) in results.items()}
    assert applied == {"a.py": 1, "b.py": 1}, results
    edited_file = files[Path(edited[0]).name]
    assert "# edited between analysis and apply" in edited_file.read_text()
    # Each file's duplicated body survives once, whether it was extracted or reused.
    for name, path, marker in (
        ("a.py", files["a.py"], "scaled = total * 2"),
        ("b.py", files["b.py"], "parts.append("),
    ):
        text = path.read_text()
        assert text.count(marker) == 1, text
        assert _behaviour(path) == expected[name]


class FakeBar:
    """A tqdm stand-in that records counts across transient analysis phases."""

    instances: List["FakeBar"] = []

    def __init__(
        self,
        *,
        total: Optional[int],
        desc: str,
        unit: str,
        dynamic_ncols: bool,
        leave: bool,
    ) -> None:
        self.total = total
        self.desc = desc
        self.count = 0
        self.postfixes: List[Mapping[str, object]] = []
        self.closed = False
        FakeBar.instances.append(self)

    def update(self, n: int = 1) -> None:
        self.count += n

    def set_postfix(self, postfix: Mapping[str, object], refresh: bool = True) -> None:
        self.postfixes.append(dict(postfix))

    def refresh(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    "max_iterations, expected_reason", [(0, "fixed_point"), (1, "iteration_cap")]
)
def test_tqdm_bar_tracks_applied_proposals_across_analysis_phases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, max_iterations: int, expected_reason: str
) -> None:
    """Audit CLI-02: sharing a terminal line must preserve the applied-proposal count."""
    _project(tmp_path)
    FakeBar.instances = []
    monkeypatch.setattr(progress, "load_tqdm", lambda: FakeBar)
    results, reason = _run(
        tmp_path,
        UnificationRefactorEngine(skip_trivial_helpers=False, min_lines=3),
        progress="tqdm",
        max_iterations=max_iterations,
    )
    assert reason == expected_reason
    applied = sum(count for count, _ in results.values())
    assert applied == (max_iterations or 2)
    apply_bars = [bar for bar in FakeBar.instances if bar.desc == "apply"]
    assert apply_bars
    assert all(bar.total == (max_iterations or None) for bar in apply_bars)
    assert apply_bars[-1].count == applied
    assert all(0 <= bar.count <= applied for bar in apply_bars)
    # Nested analysis temporarily owns the line. Restoring the apply display
    # must preserve its count and the remaining queue, including at a cap.
    assert apply_bars[-1].postfixes[-1]["applied"] == applied
    assert apply_bars[-1].postfixes[-1]["queued"] == 2 - applied
    assert all(bar.closed for bar in FakeBar.instances)


def test_auto_without_tqdm_draws_and_clears_the_terminal_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audit CLI-02: the fallback clears its terminal line instead of leaving a log."""
    _project(tmp_path)
    monkeypatch.setattr(progress, "load_tqdm", lambda: None)
    out = io.StringIO()
    err = _TerminalStream()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        results, reason = UnificationRefactorEngine(
            skip_trivial_helpers=False, min_lines=3
        ).refactor_directory_to_fixed_point(str(tmp_path), str(tmp_path), progress="auto")
    assert reason == "fixed_point" and sum(count for count, _ in results.values()) == 2
    # The bar redraws with carriage returns on stderr, like tqdm's, so stdout
    # (a redirected report) stays clean.
    text = err.getvalue()
    assert "on=discovered:" in text and "on=applied:" in text
    assert "applied=2 queued=0" in text
    assert text.endswith("\r\x1b[2K"), "the transient line is cleared at the fixed point"
    assert "\n" not in text
    assert "\r" not in out.getvalue()


def test_a_bar_that_cannot_be_constructed_costs_only_its_display(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audit CLI-02: a failed display backend never changes the refactoring result."""
    _project(tmp_path)

    def broken_factory(
        *, total: Optional[int], desc: str, unit: str, dynamic_ncols: bool, leave: bool
    ) -> FakeBar:
        if desc == "apply":
            raise RuntimeError("no terminal")
        return FakeBar(total=total, desc=desc, unit=unit, dynamic_ncols=dynamic_ncols, leave=leave)

    monkeypatch.setattr(progress, "load_tqdm", lambda: broken_factory)
    results, reason = _run(
        tmp_path,
        UnificationRefactorEngine(skip_trivial_helpers=False, min_lines=3),
        progress="tqdm",
    )
    assert reason == "fixed_point" and sum(count for count, _ in results.values()) == 2
