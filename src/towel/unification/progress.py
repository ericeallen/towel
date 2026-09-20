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

import sys
import importlib
import threading
from typing import Callable, Literal, Mapping, Optional, Protocol, cast

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


class Heartbeat:
    """Redraws a display on a timer, so a long silent step still shows it is alive.

    A tqdm bar redraws only when something advances it, its elapsed clock
    included, so a step that takes minutes looks exactly like a hung process.
    Verification makes such steps ordinary: a proposal the project rejects
    costs several whole-project checks and never advances the bar. tqdm runs a
    thread over its own bars for the same reason, so redrawing from one here is
    the sanctioned shape; the caller serializes it against its own drawing.

    ``stop`` is idempotent, and a redraw that fails is dropped rather than
    ending a run for the sake of its display.
    """

    def __init__(self, redraw: Callable[[], object], period: float = 1.0) -> None:
        self._redraw = redraw
        self._period = period
        self._stopped = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._beat, name="towel-progress", daemon=True)
        self._thread.start()

    def _beat(self) -> None:
        while not self._stopped.wait(self._period):
            quietly(self._redraw)

    def stop(self) -> None:
        self._stopped.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2)


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
