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
code in one place and make tqdm a soft dependency.
"""

import importlib
from typing import Any, Optional


def load_tqdm() -> Optional[Any]:
    """Return ``tqdm.auto.tqdm`` if tqdm is installed, else ``None``.

    tqdm is an optional dependency, so any import failure is deliberately
    swallowed — the callers fall back to plain-text progress.
    """
    try:
        module = importlib.import_module("tqdm.auto")
        return getattr(module, "tqdm")
    except Exception:
        return None


def render_inline_bar(pct: int, bar_len: int = 24) -> str:
    """A ``bar_len``-character ``#``/``-`` bar for a percentage, clamped to 0-100."""
    pct = max(0, min(100, pct))
    filled = (pct * bar_len) // 100
    return "#" * filled + "-" * (bar_len - filled)
