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

import importlib
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


def render_inline_bar(pct: int, bar_len: int = 24) -> str:
    """A ``bar_len``-character ``#``/``-`` bar for a percentage, clamped to 0-100."""
    pct = max(0, min(100, pct))
    filled = (pct * bar_len) // 100
    return "#" * filled + "-" * (bar_len - filled)
