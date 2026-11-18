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
import os
import unicodedata
import importlib
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Iterator, List, Literal, Mapping, Optional, Protocol, Set, Tuple, cast

ProgressMode = Literal["auto", "tqdm", "none", "detail"]
"""How a run reports progress: tqdm bars, tqdm with an inline fallback, nothing, or per-phase detail."""

DEFAULT_PROGRESS: ProgressMode = "auto"
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
        beating = self._beating
        if beating is None:
            return None
        thread, ended = beating
        ended.set()
        # If interrupted, cleanup may repeat. Signal the thread before forgetting it.
        self._beating = None
        return thread if thread.ident is not None else None

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


class _RunDisplay:
    """Presentation choices and diagnostics owned by one command invocation."""

    def __init__(self, quiet: bool) -> None:
        self.quiet = quiet
        self.terminal = sys.stderr.isatty()
        self._loaded = False
        self._factory: Optional[ProgressBarFactory] = None
        self._warnings: Set[str] = set()
        self.tqdm_noted = False

    def factory(self) -> Optional[ProgressBarFactory]:
        if not self._loaded:
            self._factory = load_tqdm()
            self._loaded = True
        return self._factory

    def repeated_warning(self, message: str) -> bool:
        if message in self._warnings:
            return True
        self._warnings.add(message)
        return False


_RUN: ContextVar[Optional[_RunDisplay]] = ContextVar("towel_progress_run", default=None)


@contextmanager
def progress_run(*, quiet: bool = False, inherit: bool = False) -> Iterator[None]:
    """Bound backend decisions and repeated warnings to a single invocation."""
    if inherit and _RUN.get() is not None:
        yield
        return
    run = _RunDisplay(quiet)
    token = _RUN.set(run)
    try:
        yield
    finally:
        for renderer in tuple(_ACTIVE):
            if renderer._owner is run:
                renderer.close()
        _RUN.reset(token)


def is_quiet() -> bool:
    run = _RUN.get()
    return run.quiet if run is not None else False


def repeated_warning(message: str) -> bool:
    run = _RUN.get()
    return run.repeated_warning(message) if run is not None else False


_TQDM_NOTED = False


_DRAWING = threading.RLock()
_ACTIVE: List["ProgressRenderer"] = []


def _terminal_width() -> int:
    try:
        return max(1, os.get_terminal_size(sys.stderr.fileno()).columns - 1)
    except (OSError, ValueError, AttributeError):
        return 79


def _fit_line(text: str, width: int) -> str:
    """Fit printable text in display cells, leaving the terminal's wrap column free."""
    result: List[str] = []
    used = 0
    for char in text:
        if unicodedata.category(char).startswith("C"):
            char = " "
        cells = (
            0
            if unicodedata.combining(char)
            else (2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1)
        )
        if used + cells > width:
            break
        result.append(char)
        used += cells
    return "".join(result)


class ProgressRenderer:
    """One transient, terminal-sized line shared by all analysis and apply phases.

    Nested phases temporarily take over the line. Display failures cost only
    the display; every drawing operation and every note uses the same lock.
    """

    def __init__(
        self,
        mode: ProgressMode,
        *,
        total: Optional[int],
        desc: str,
        unit: str,
        factory: Optional[ProgressBarFactory] = None,
    ) -> None:
        run = _RUN.get()
        if run is not None and run.quiet:
            mode = "none"
        terminal = run.terminal if run is not None else sys.stderr.isatty()
        self._enabled = wants_bar(mode) and terminal and total != 0
        self._factory = factory
        if wants_bar(mode) and (self._enabled or mode == "tqdm"):
            self._factory = factory or (run.factory() if run is not None else load_tqdm())
            if mode == "tqdm" and self._factory is None:
                _note_missing_tqdm()
        self._total = total
        self._desc = desc
        self._unit = unit
        self._done = 0
        self._details: Mapping[str, object] = {}
        self._bar: Optional[ProgressBar] = None
        self._closed = False
        self._owner: Optional[_RunDisplay] = None
        self._visible = False
        self._started = time.monotonic()
        self._heartbeat = Heartbeat(self.refresh)

    def _activate(self) -> None:
        if self not in _ACTIVE:
            # The terminal is shared. Only the run that drew this bar may close it.
            self._owner = _RUN.get()
            if _ACTIVE:
                _ACTIVE[-1]._clear()
            _ACTIVE.append(self)

    def _clear(self) -> None:
        if self._bar is not None:
            quietly(self._bar.close)
            self._bar = None
        if self._visible:
            print("\r\x1b[2K", end="", file=sys.stderr, flush=True)
            self._visible = False

    def _draw(self) -> None:
        if not self._enabled or self._closed:
            return
        self._activate()
        if _ACTIVE[-1] is not self:
            return
        if self._factory is not None and self._bar is None:
            try:
                self._bar = self._factory(
                    total=self._total,
                    desc=self._desc,
                    unit=self._unit,
                    dynamic_ncols=True,
                    leave=False,
                )
                self._bar.update(self._done)
            except Exception:
                self._factory = None
        if self._bar is not None:
            self._bar.set_postfix(self._details, refresh=True)
        else:
            count = str(self._done) if self._total is None else f"{self._done}/{self._total}"
            detail = " ".join(f"{key}={value}" for key, value in self._details.items())
            elapsed = (time.monotonic() - self._started) / 60
            line = _fit_line(
                f"{self._desc}: {count} {self._unit} | {elapsed:.1f}m | {detail}", _terminal_width()
            )
            print(f"\r\x1b[2K{line}", end="", file=sys.stderr, flush=True)
        self._visible = True

    def note(self, message: str) -> None:
        """Write a note without colliding with this or a nested phase's display."""
        write_note(message)

    def start_heartbeat(self) -> None:
        """Keep an active terminal display alive while verification is busy."""
        if self._enabled and not self._closed:
            self.refresh()
            if self in _ACTIVE:
                self._heartbeat.start()

    def heartbeat(
        self,
        completed: int,
        total: Optional[int] = None,
        *,
        label: Optional[str] = None,
        details: Optional[Mapping[str, object]] = None,
    ) -> None:
        """Report absolute worker/apply counts and keep elapsed time moving.

        Call after each completed chunk or application, and close in ``finally``.
        A growing apply queue can use ``total=None`` and put its rate in
        ``details``. All heartbeat threads obey ``display_threads_stopped``.
        """
        if not self._enabled or self._closed:
            return
        with _DRAWING:
            if total != self._total or (label is not None and label != self._desc):
                quietly(self._clear)
            delta = completed - self._done
            self._done, self._total = completed, total
            if label is not None:
                self._desc = label
            if details is not None:
                self._details = dict(details)
            bar = self._bar
            if bar is not None:
                quietly(lambda: bar.update(delta))
            quietly(self._draw)
        self.start_heartbeat()

    def refresh(self) -> None:
        with _DRAWING:
            quietly(self._draw)

    def update(self, n: int = 1) -> None:
        if not self._enabled:
            return
        with _DRAWING:
            self._done += n
            bar = self._bar
            if bar is not None:
                quietly(lambda: bar.update(n))
            quietly(self._draw)

    def set_postfix(self, ordered_dict: Mapping[str, object], refresh: bool = True) -> None:
        if not self._enabled:
            return
        with _DRAWING:
            self._details = dict(ordered_dict)
            if refresh:
                quietly(self._draw)

    def close(self) -> None:
        self._heartbeat.stop()
        with _DRAWING:
            self._closed = True
            quietly(self._clear)
            if self in _ACTIVE:
                _ACTIVE.remove(self)
                if _ACTIVE:
                    quietly(_ACTIVE[-1]._draw)


def _note_missing_tqdm() -> None:
    """An explicit backend request deserves an explanation when it falls back."""
    from ..diagnostics import LOG

    global _TQDM_NOTED
    run = _RUN.get()
    noted = run.tqdm_noted if run is not None else _TQDM_NOTED
    if not noted:
        if run is not None:
            run.tqdm_noted = True
        else:
            _TQDM_NOTED = True
        LOG.warning("tqdm is not installed; progress is shown as inline bars on stderr instead")


def write_message(message: str, *, prefix: str = "") -> None:
    """Print a diagnostic on its own line, then restore the active progress phase."""
    if prefix and not message.startswith(prefix + ":"):
        message = f"{prefix}: {message}"
    with _DRAWING:
        active = _ACTIVE[-1] if _ACTIVE else None
        if active is not None:
            quietly(active._clear)
        try:
            print(message, file=sys.stderr, flush=True)
        finally:
            if active is not None:
                quietly(active._draw)


def write_note(message: str) -> None:
    """Route a tool note through the display; accepts existing ``Note:`` prefixes."""
    if not is_quiet():
        write_message(message, prefix="Note")
