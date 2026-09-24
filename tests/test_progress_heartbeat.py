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

"""A long verification step must still look alive.

A tqdm bar redraws only when something advances it, its elapsed clock included,
and verifying a proposal the project rejects advances nothing. On a capped
Sphinx run about 25 proposals were rejected for each one applied, so the bar
stood still for minutes at a time, which is indistinguishable from a hung run.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from typing import Any, List, Mapping, Optional

from towel.unification.fixed_point import _ApplyProgress
from towel.unification.progress import Heartbeat


def test_it_redraws_until_stopped() -> None:
    beats = threading.Semaphore(0)
    heartbeat = Heartbeat(beats.release, period=0.01)
    heartbeat.start()
    try:
        for _ in range(3):
            assert beats.acquire(timeout=5), "the display stopped being redrawn"
    finally:
        heartbeat.stop()


def test_a_redraw_that_fails_does_not_end_the_run_or_the_heartbeat() -> None:
    attempts = threading.Semaphore(0)

    def fail() -> None:
        attempts.release()
        raise RuntimeError("the terminal went away")

    heartbeat = Heartbeat(fail, period=0.01)
    heartbeat.start()
    try:
        for _ in range(2):
            assert attempts.acquire(timeout=5), "one failed redraw ended the heartbeat"
    finally:
        heartbeat.stop()


def test_stopping_is_idempotent_and_leaves_no_thread_behind() -> None:
    heartbeat = Heartbeat(lambda: None, period=0.01)
    heartbeat.start()
    heartbeat.start()  # Already running; must not start a second thread.
    heartbeat.stop()
    heartbeat.stop()
    assert not [thread for thread in threading.enumerate() if thread.name == "towel-progress"]


class _RecordingBar:
    """A bar that remembers what it was told, standing in for tqdm."""

    def __init__(self, **_: object) -> None:
        self.postfixes: List[Mapping[str, object]] = []
        self.updates = 0
        self.closed = False
        self.closes = 0

    def update(self, n: int = 1) -> None:
        self.updates += n

    def set_postfix(self, ordered_dict: Mapping[str, object], refresh: bool = True) -> None:
        self.postfixes.append(dict(ordered_dict))

    def refresh(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True
        self.closes += 1


def _reporter_showing(bar: _RecordingBar, period: float) -> _ApplyProgress:
    reporter = _ApplyProgress("tqdm", lambda **kwargs: bar)
    reporter._heartbeat = Heartbeat(reporter._redraw, period=period)
    return reporter


def _proposal(description: str = "Extract common code from first and second") -> Any:
    """Enough of a proposal for the reporter, which only ever reads its description."""
    return SimpleNamespace(description=description)


DESCRIPTION = "Extract common code from first and second"


def test_the_bar_keeps_being_redrawn_while_a_proposal_is_weighed() -> None:
    """Nothing is applied here: every redraw comes from the heartbeat."""
    bar = _RecordingBar()
    reporter = _reporter_showing(bar, period=0.01)
    try:
        reporter.discovered([_proposal()], applied=0, max_iterations=0)
        reporter.applying(0, 1, 1, DESCRIPTION)
        drawn = len(bar.postfixes)
        deadline = time.monotonic() + 5
        while len(bar.postfixes) < drawn + 3 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        reporter.close()
    assert bar.updates == 0, "the bar advanced, so this proved nothing about a stalled one"
    assert len(bar.postfixes) >= drawn + 3, "a proposal under verification stopped redrawing"


def test_the_bar_says_which_proposal_is_being_weighed() -> None:
    bar = _RecordingBar()
    reporter = _reporter_showing(bar, period=60.0)
    try:
        reporter.discovered([_proposal()], applied=0, max_iterations=0)
        reporter.applying(2, 7, 9, DESCRIPTION)
    finally:
        reporter.close()
    latest = bar.postfixes[-1]
    assert latest["A"] == 2 and latest["Q"] == 7
    assert "#9" in str(latest["on"]) and "first" in str(latest["on"])


def test_the_display_thread_does_not_outlive_the_run() -> None:
    bar = _RecordingBar()
    reporter = _reporter_showing(bar, period=0.01)
    reporter.discovered([_proposal()], applied=0, max_iterations=0)
    reporter.finish_at_fixed_point()
    assert bar.closed
    assert not [thread for thread in threading.enumerate() if thread.name == "towel-progress"]


def test_a_run_with_nothing_to_do_starts_no_display_thread() -> None:
    reporter = _reporter_showing(_RecordingBar(), period=0.01)
    try:
        reporter.discovered([], applied=0, max_iterations=0)
        assert not [t for t in threading.enumerate() if t.name == "towel-progress"]
    finally:
        reporter.close()


def test_the_inline_display_reports_elapsed_time(capsys: object) -> None:
    """Without tqdm the line is redrawn too, so it must carry something that moves."""
    reporter = _ApplyProgress("auto", None)
    started: Optional[float] = getattr(reporter, "_started", None)
    assert started is not None
    reporter._started = time.monotonic() - 125.0
    reporter.applying(1, 2, 3, "Extract common code from first and second")
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "2.1m" in captured.err, captured.err


def test_a_run_that_raises_still_closes_the_bar() -> None:
    """A bar left open writes over whatever the terminal prints next."""
    bar = _RecordingBar()
    reporter = _reporter_showing(bar, period=60.0)
    reporter.discovered([_proposal()], applied=0, max_iterations=0)
    reporter.close()
    assert bar.closed


def test_the_bar_is_closed_once_when_the_run_ends_normally() -> None:
    bar = _RecordingBar()
    reporter = _reporter_showing(bar, period=60.0)
    reporter.discovered([_proposal()], applied=0, max_iterations=0)
    reporter.finish_at_fixed_point()
    reporter.close()
    assert bar.closes == 1, "the finisher closed it, so close must not repeat that"


def test_a_heartbeat_can_be_started_again_after_it_was_stopped() -> None:
    beats = threading.Semaphore(0)
    heartbeat = Heartbeat(beats.release, period=0.01)
    heartbeat.start()
    assert beats.acquire(timeout=5)
    heartbeat.stop()
    heartbeat.start()
    try:
        assert beats.acquire(timeout=5), "a restarted heartbeat did not beat"
    finally:
        heartbeat.stop()
