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
from typing import Any, Dict, List

import pytest

from towel.unification import fixed_point
from towel.unification.models import RefactoringProposal
from towel.unification.refactor_engine import UnificationRefactorEngine

# Two block shapes, one per file, so each file holds exactly one same-file
# duplicate and nothing pairs across the files.
ARITHMETIC = """
def {name}(value):
    total = value + 1
    scaled = total * 2
    label = str(scaled)
    return label
"""

LOOP = """
def {name}(value):
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


def _run(
    root: Path, engine: UnificationRefactorEngine, **kwargs: Any
) -> tuple[Dict[str, Any], str]:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return engine.refactor_directory_to_fixed_point(str(root), str(root), **kwargs)


def _behaviour(path: Path) -> List[str]:
    namespace: Dict[str, Any] = {}
    exec(compile(path.read_text(), str(path), "exec"), namespace)
    functions = sorted(name for name in namespace if len(name) == 2 and name[1].isdigit())
    return [namespace[name](value) for name in functions for value in (0, 3, 7)]


def test_stale_proposal_is_dropped_and_the_run_still_reaches_the_fixed_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    files = _project(tmp_path)
    expected = {name: _behaviour(path) for name, path in files.items()}
    engine = UnificationRefactorEngine(min_lines=3)
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
    """A tqdm stand-in that records what the driver asks of it."""

    events: List[str] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.desc = kwargs.get("desc")
        FakeBar.events.append(f"create total={kwargs.get('total')} desc={self.desc}")

    def update(self, n: int = 1) -> None:
        FakeBar.events.append(f"update {n} {self.desc}")

    def set_postfix(self, postfix: Dict[str, Any], refresh: bool = True) -> None:
        FakeBar.events.append(f"postfix A={postfix['A']} Q={postfix['Q']}")

    def refresh(self) -> None:
        FakeBar.events.append("refresh")

    def close(self) -> None:
        FakeBar.events.append("close")


@pytest.mark.parametrize(
    "max_iterations, expected_reason", [(0, "fixed_point"), (1, "iteration_cap")]
)
def test_tqdm_bar_receives_one_update_per_applied_proposal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, max_iterations: int, expected_reason: str
) -> None:
    _project(tmp_path)
    FakeBar.events = []
    monkeypatch.setattr(fixed_point, "load_tqdm", lambda: FakeBar)
    results, reason = _run(
        tmp_path,
        UnificationRefactorEngine(min_lines=3),
        progress="tqdm",
        max_iterations=max_iterations,
    )
    assert reason == expected_reason
    applied = sum(count for count, _ in results.values())
    assert applied == (max_iterations or 2)
    apply_bars = [e for e in FakeBar.events if e.startswith("create") and "desc=apply" in e]
    assert apply_bars == [f"create total={max_iterations or None} desc=apply"]
    assert FakeBar.events.count("update 1 apply") == applied
    # The postfix after the last update: nothing queued at the fixed point,
    # one proposal still queued when the cap of one stops the run.
    last_update = max(i for i, e in enumerate(FakeBar.events) if e == "update 1 apply")
    assert FakeBar.events[last_update + 1] == f"postfix A={applied} Q={2 - applied}"
    assert FakeBar.events[-1] == "close"
    if expected_reason == "fixed_point":
        assert FakeBar.events[-2] == "refresh"


def test_auto_without_tqdm_draws_the_inline_bar_and_ends_its_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(tmp_path)
    monkeypatch.setattr(fixed_point, "load_tqdm", lambda: None)
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        results, reason = UnificationRefactorEngine(min_lines=3).refactor_directory_to_fixed_point(
            str(tmp_path), str(tmp_path), progress="auto"
        )
    assert reason == "fixed_point" and sum(count for count, _ in results.values()) == 2
    # The bar redraws with carriage returns on stderr, like tqdm's, so stdout
    # (a redirected report) stays clean.
    text = err.getvalue()
    assert "\r[towel] discovered " in text and "\r[towel] applied " in text
    assert "applied=2 queued=0" in text
    assert text.endswith("\n"), "the inline bar's line is terminated at the fixed point"
    assert "\r" not in out.getvalue()


def test_a_bar_that_cannot_be_constructed_costs_only_its_display(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(tmp_path)

    def broken_factory(*args: Any, **kwargs: Any) -> Any:
        # Only the driver's apply bar is guarded; the pair loop's bar must work.
        if kwargs.get("desc") == "apply":
            raise RuntimeError("no terminal")
        return FakeBar(*args, **kwargs)

    monkeypatch.setattr(fixed_point, "load_tqdm", lambda: broken_factory)
    results, reason = _run(tmp_path, UnificationRefactorEngine(min_lines=3), progress="tqdm")
    assert reason == "fixed_point" and sum(count for count, _ in results.values()) == 2
