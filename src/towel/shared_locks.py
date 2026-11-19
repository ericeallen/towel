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

"""Recover shared recursive locks when an asynchronous exception interrupts their owner."""

from contextlib import contextmanager
import threading
from typing import Callable, Iterator, cast


@contextmanager
def restoring_lock_depth(lock: threading.RLock) -> Iterator[None]:
    """Return only this thread's excess acquisitions, including an interrupted __enter__.

    Enter this scope *before* acquiring the lock. On CPython 3.14 the signal
    check at the return from __enter__ precedes the with statement's protected
    range. Libraries can also acquire recursively and miss their release.
    The native count is local to the calling thread, zero if another owns it.
    """
    recursion_count = cast(Callable[[], int], getattr(lock, "_recursion_count"))
    depth = recursion_count()
    try:
        yield
    finally:
        while recursion_count() > depth:
            lock.release()
