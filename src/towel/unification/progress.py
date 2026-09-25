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


"""Optional tqdm loading and a plain-text progress bar.

Both the engine and the pipeline show progress; these helpers keep that display
code in one place and make tqdm a soft dependency. A progress display must
never change an analysis outcome, so every call into a bar goes through
:func:`quietly`, the one place a display failure is allowed to vanish.
"""

import atexit
import sys
import importlib
import threading
import time
from contextlib import contextmanager
from typing import Callable, Iterator, List, Literal, Mapping, Optional, Protocol, Set, Tuple, cast

ProgressMode = Literal["auto", "tqdm", "none", "detail"]
"""How a run reports progress: tqdm bars, tqdm with an inline fallback, nothing, or per-phase detail."""

DEFAULT_PROGRESS: ProgressMode = "tqdm"
"""The one default every entry point shares; the command line's ``--progress`` uses it too."""


_MODES: Mapping[str, ProgressMode] = {
    "auto": "auto",
    "tqdm": "tqdm",
    "none": "none",
    "detail": "detail",
}


def normalize_progress(value: str) -> ProgressMode:
    """The progress mode ``value`` names, or the default for anything else."""
    return _MODES.get(value, DEFAULT_PROGRESS)


def wants_bar(mode: ProgressMode) -> bool:
    """Whether a mode shows a progress bar (tqdm, or the inline fallback under ``auto``)."""
    return mode in ("auto", "tqdm")


class ProgressBar(Protocol):  # pragma: no cover - protocol bodies are never run
    """The part of a tqdm bar the engine and the pipeline use."""

    def update(self, n: int = 1) -> object:
        """Advance the displayed progress."""
        ...

    def close(self) -> object:
        """Finish the progress display."""
        ...

    def refresh(self) -> object:
        """Redraw the bar."""
        ...

    def set_postfix(self, ordered_dict: Mapping[str, object], refresh: bool = True) -> object:
        """Display compact progress details."""
        ...


class ProgressBarFactory(Protocol):  # pragma: no cover - protocol bodies are never run
    """``tqdm.auto.tqdm`` as the engine calls it: a bar counting toward a total, or open-ended."""

    def __call__(
        self,
        *,
        total: Optional[int],
        desc: str,
        unit: str,
        dynamic_ncols: bool,
        leave: bool,
    ) -> ProgressBar:
        """Construct a bar."""
        ...


def load_tqdm() -> Optional[ProgressBarFactory]:
    """Return ``tqdm.auto.tqdm`` if tqdm is installed, else ``None``.

    tqdm is an optional dependency, so any import failure is deliberately
    swallowed: the callers fall back to plain-text progress.
    """
    try:
        module = importlib.import_module("tqdm.auto")
        return cast(ProgressBarFactory, getattr(module, "tqdm"))
    except Exception:
        return None


def quietly(action: Callable[[], object]) -> None:
    """Run a display action and drop any failure: a broken bar must not stop a run."""
    try:
        action()
    except Exception:
        pass


_BEATING: Set["Heartbeat"] = set()
"""Every heartbeat started and not yet stopped, for :func:`display_threads_stopped`."""


class Heartbeat:
    """Redraws a display on a timer, so a long silent step still shows it is alive.

    A tqdm bar redraws only when something advances it, its elapsed clock
    included, so a step that takes minutes looks exactly like a hung process.
    Verification makes such steps ordinary: a proposal the project rejects
    costs several whole-project checks and never advances the bar. tqdm runs a
    thread over its own bars for the same reason, so redrawing from one here is
    the sanctioned shape; the caller serializes it against its own drawing.

    ``stop`` is idempotent, and a redraw that fails is dropped rather than
    ending a run for the sake of its display. ``start``, ``stop`` and
    :func:`display_threads_stopped` are called from the run's own thread.
    """

    def __init__(self, redraw: Callable[[], object], period: float = 1.0) -> None:
        self._redraw = redraw
        self._period = period
        # The thread beating now and the event that ends it. Each thread has
        # an event of its own, so one told to end can never be revived by a
        # later start, which gets a new thread instead.
        self._beating: Optional[Tuple[threading.Thread, threading.Event]] = None

    def start(self) -> None:
        _BEATING.add(self)
        if self._beating is not None:
            return
        ended = threading.Event()
        thread = threading.Thread(
            target=self._beat, args=(ended,), name="towel-progress", daemon=True
        )
        self._beating = (thread, ended)
        thread.start()

    def _beat(self, ended: threading.Event) -> None:
        while not ended.wait(self._period):
            quietly(self._redraw)

    def _end_thread(self) -> Optional[threading.Thread]:
        """Tell the beating thread to end and forget it; the thread, for the caller to join."""
        beating, self._beating = self._beating, None
        if beating is None:
            return None
        thread, ended = beating
        ended.set()
        return thread

    def stop(self) -> None:
        _BEATING.discard(self)
        thread = self._end_thread()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)


def _running_tqdm_monitors() -> List[threading.Thread]:
    """tqdm's monitor threads alive in this process, whoever's bars started them.

    tqdm starts one with the first bar of a class and, in the versions Towel
    supports, never ends it, so once any bar has been drawn it runs for the
    rest of the process.
    """
    monitor_type = getattr(sys.modules.get("tqdm._monitor"), "TMonitor", None)
    if not isinstance(monitor_type, type):
        return []
    return [thread for thread in threading.enumerate() if isinstance(thread, monitor_type)]


def _end_tqdm_monitor(monitor: threading.Thread) -> Optional[Callable[[], None]]:
    """Tell one of tqdm's monitors to end; an action that starts its replacement.

    None when the monitor is not the shape this expects, a tqdm Towel does not
    know: it is left running, and a fork that needs it gone sees it there.
    """
    ended = getattr(monitor, "was_killed", None)
    owner = getattr(monitor, "tqdm_cls", None)
    interval = getattr(monitor, "sleep_interval", None)
    if not isinstance(ended, threading.Event) or owner is None or interval is None:
        return None
    ended.set()

    def restart() -> None:
        # What tqdm itself does when a bar finds its monitor gone, under its lock.
        with owner.get_lock():
            if getattr(owner, "monitor", None) is monitor:
                owner.monitor = type(monitor)(owner, interval)
        # Each monitor registers an exit signal of its own; the ended one needs none.
        atexit.unregister(getattr(monitor, "_atexit_signal", lambda: None))

    return restart


@contextmanager
def display_threads_stopped(timeout: float) -> Iterator[None]:
    """End every thread that redraws a display for the block, and start each again after.

    A forked child keeps only the thread that forked it, but every lock in the
    state it had at that instant; a lock held by a redraw -- tqdm's, or the
    one inside ``sys.stderr`` that a half-finished write holds -- is held for
    ever in the child, which waits on it as soon as it touches the stream, as
    every worker does when it flushes on exit. The heartbeats and tqdm's
    monitors are this process's display threads, so a fork made inside this
    block has none of them. Each gets up to ``timeout`` seconds to end; one
    still running after that (a redraw blocked on a full pipe) finishes in its
    own time and is still to be seen among the process's threads, where the
    caller looks before it forks. The ended threads are replaced when the
    block ends, however it ends.
    """
    heartbeats = list(_BEATING)
    ending = [thread for thread in (beat._end_thread() for beat in heartbeats) if thread]
    restarts: List[Callable[[], None]] = []
    for monitor in _running_tqdm_monitors():
        restart = _end_tqdm_monitor(monitor)
        if restart is not None:
            restarts.append(restart)
            ending.append(monitor)
    deadline = time.monotonic() + timeout
    for thread in ending:
        if thread is not threading.current_thread():
            thread.join(max(0.0, deadline - time.monotonic()))
    try:
        yield
    finally:
        for restart in restarts:
            quietly(restart)
        for beat in heartbeats:
            if beat in _BEATING:
                beat.start()


def render_inline_bar(pct: int, bar_len: int = 24) -> str:
    """A ``bar_len``-character ``#``/``-`` bar for a percentage, clamped to 0-100."""
    pct = max(0, min(100, pct))
    filled = (pct * bar_len) // 100
    return "#" * filled + "-" * (bar_len - filled)


def start_inline_status(label: str, enabled: bool) -> None:
    """Emit the leading inline progress label when progress is enabled."""
    if enabled:
        print(label, end=" ", flush=True, file=sys.stderr)


def update_inline_status(label: str, pct: int, *, bar_len: int = 24, suffix: str = "") -> None:
    """Print an inline progress update with consistent formatting."""
    bar = render_inline_bar(pct, bar_len=bar_len)
    suffix_text = f" {suffix}" if suffix else ""
    print(f"\r{label} [{bar}] {pct:3d}%{suffix_text}", end="", flush=True, file=sys.stderr)


def finish_inline_status(enabled: bool) -> None:
    """Terminate the inline status line so subsequent logs stay readable."""
    if enabled:
        print(file=sys.stderr)
