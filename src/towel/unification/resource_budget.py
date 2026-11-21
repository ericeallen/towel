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

"""One re-export work allowance across queries, analysis passes and forked workers."""

from __future__ import annotations

import multiprocessing
from multiprocessing.sharedctypes import SynchronizedArray
from threading import Lock
from typing import Optional

# Keep the graph allocation bound independent of rereading directory entries.
# NetworkX needs hundreds of thousands of checks of the original layout
# across whole-project and localized passes, even when its chart is small.
_WORK_LIMIT = 1_000_000
_LAYOUT_LIMIT = 1_000_000


class WorkLimitReached(Exception):
    """No remaining work can be spent proving that a call is safe."""


class ResourceBudget:
    def __init__(self) -> None:
        self._lock = Lock()
        self._remaining = [_WORK_LIMIT, _LAYOUT_LIMIT]
        self._exhausted = False
        self._shared: Optional[SynchronizedArray[int]] = None

    def share_before_fork(self) -> None:
        if self._shared is None:
            self._shared = multiprocessing.get_context("fork").Array(
                "q", [*self._remaining, int(self._exhausted)]
            )

    @property
    def remaining(self) -> int:
        return self._shared[0] if self._shared is not None else self._remaining[0]

    @property
    def layout_remaining(self) -> int:
        return self._shared[1] if self._shared is not None else self._remaining[1]

    @property
    def exhausted(self) -> bool:
        return bool(self._shared[2]) if self._shared is not None else self._exhausted

    def take(self, amount: int = 1, *, layout: bool = False) -> None:
        index = int(layout)
        shared = self._shared
        if shared is None:
            with self._lock:
                if self._exhausted:
                    raise WorkLimitReached
                if self._remaining[index] >= amount:
                    self._remaining[index] -= amount
                    return
                self._remaining[index] = 0
                self._exhausted = True
        else:
            with shared.get_lock():
                if shared[2]:
                    raise WorkLimitReached
                if shared[index] >= amount:
                    shared[index] -= amount
                    return
                shared[index] = 0
                shared[2] = 1
        raise WorkLimitReached
