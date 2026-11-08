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

"""Interrupt a CLI's preparation safely, before its final publication starts."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import os
import signal
import time
from types import FrameType
from typing import Iterator


class BudgetExpired(BaseException):
    """An incomplete run, never a candidate refusal or a failed type check.

    Like KeyboardInterrupt this must unwind all the way through workers and
    private stages, including code that catches ordinary analysis exceptions.
    """


@dataclass(frozen=True)
class _Budget:
    owner: int
    deadline: float


_active: ContextVar[_Budget | None] = ContextVar("towel_execution_budget", default=None)


def _expire(signum: int, frame: FrameType | None) -> None:
    budget = _active.get()
    if budget is not None and os.getpid() == budget.owner:
        raise BudgetExpired()


@contextmanager
def execution_budget(seconds: float | None) -> Iterator[None]:
    """Bound setup/analysis/checking; unwind cleanup without repeated interrupts.

    POSIX interval timers interrupt Python computation and blocking tool I/O.
    They are not inherited by fork workers. The owner check also prevents a
    worker's inherited context from changing its parent's publication budget.
    Final publication explicitly ends the budget after a last deadline check.
    """
    if seconds is None:
        yield
        return
    if _active.get() is not None or signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise ValueError("A time limit cannot replace an already active interval timer")
    previous_handler = signal.getsignal(signal.SIGALRM)
    token = _active.set(_Budget(os.getpid(), time.monotonic() + seconds))
    signal.signal(signal.SIGALRM, _expire)
    try:
        signal.setitimer(signal.ITIMER_REAL, seconds)
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        _active.reset(token)


def finish_before_publication() -> None:
    """Refuse an expired run, or let its final transaction finish without a timer.

    The last check happens before any user output changes. Once publication
    starts, an elapsed-time interrupt must not turn committed output into an
    apparent failed run. Explicit interrupts retain ordinary recovery behavior.
    """
    budget = _active.get()
    if budget is None or os.getpid() != budget.owner:
        return
    signal.setitimer(signal.ITIMER_REAL, 0)
    if time.monotonic() >= budget.deadline:
        raise BudgetExpired()
