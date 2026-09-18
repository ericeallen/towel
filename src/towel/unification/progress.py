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
from typing import Any, Callable, Iterator, Mapping, Optional, Protocol, cast


class ProgressBar(Protocol):
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

    def __iter__(self) -> Iterator[Any]:
        """Iterate the wrapped iterable, advancing the bar."""
        ...


class ProgressBarFactory(Protocol):
    """``tqdm.auto.tqdm`` as the engine calls it: a bar over an iterable or a total."""

    def __call__(self, *args: object, **kwargs: object) -> ProgressBar:
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
