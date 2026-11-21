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

# One million metered operations, including queued returns and namespace scans.
# The native NetworkX workload spends tens of thousands on its distinct roots.
_WORK_LIMIT = 1_000_000


class WorkLimitReached(Exception):
    """No remaining work can be spent proving that a call is safe."""


class ResourceBudget:
    def __init__(self) -> None:
        self._lock = Lock()
        self.remaining = _WORK_LIMIT
        self._exhausted = False
        self._shared: Optional[SynchronizedArray[int]] = None

    def share_before_fork(self) -> None:
        if self._shared is None:
            self._shared = multiprocessing.get_context("fork").Array(
                "q", [self.remaining, int(self._exhausted)]
            )

    @property
    def exhausted(self) -> bool:
        return bool(self._shared[1]) if self._shared is not None else self._exhausted

    def take(self, amount: int = 1) -> None:
        shared = self._shared
        if shared is None:
            with self._lock:
                if self.remaining >= amount:
                    self.remaining -= amount
                    return
                self.remaining = 0
                self._exhausted = True
        else:
            with shared.get_lock():
                if shared[0] >= amount:
                    shared[0] -= amount
                    return
                shared[0] = 0
                shared[1] = 1
        raise WorkLimitReached
