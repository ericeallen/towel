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
from contextvars import ContextVar, copy_context
from dataclasses import dataclass
import os
import signal
import time
import threading
from types import FrameType
from typing import Callable, Iterator


class TimerConflict(ValueError):
    """The caller already owns the real-time interval timer."""


class BudgetExpired(BaseException):
    """An incomplete run, never a candidate refusal or a failed type check.

    Like KeyboardInterrupt this must unwind all the way through workers and
    private stages, including code that catches ordinary analysis exceptions.
    """


@dataclass
class _Budget:
    owner: int
    deadline: float
    abandoned: bool = False


_active: ContextVar[_Budget | None] = ContextVar("towel_execution_budget", default=None)


def _current_budget() -> _Budget | None:
    budget = _active.get()
    return budget if budget is not None and not budget.abandoned else None


def _expire(signum: int, frame: FrameType | None) -> None:
    budget = _current_budget()
    if budget is not None and os.getpid() == budget.owner:
        raise BudgetExpired()


def run_with_budget(seconds: float | None, action: Callable[[], None]) -> None:
    """Bound setup/analysis/checking; unwind cleanup without repeated interrupts.

    POSIX interval timers interrupt Python computation and blocking tool I/O.
    They are not inherited by fork workers. The owner check also prevents a
    worker's inherited context from changing its parent's publication budget.
    Final publication explicitly ends the budget after a last deadline check.
    """
    if seconds is None:
        action()
        return
    if _current_budget() is not None or signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise TimerConflict("--time-limit conflicts with an already active interval timer")
    previous_handler = signal.getsignal(signal.SIGALRM)

    def timed() -> None:
        _active.set(_Budget(os.getpid(), time.monotonic() + seconds))
        signal.setitimer(signal.ITIMER_REAL, seconds)
        action()

    # Only timed() owns the alarm. Context.run restores the caller before
    # cleanup, so even an alarm at disarm cannot interrupt handler restoration.
    signal.signal(signal.SIGALRM, _expire)
    try:
        copy_context().run(timed)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def finish_before_publication() -> None:
    """Refuse an expired run, or let its final transaction finish without a timer.

    The last check happens before any user output changes. Once publication
    starts, an elapsed-time interrupt must not turn committed output into an
    apparent failed run. Explicit interrupts retain ordinary recovery behavior.
    """
    budget = _current_budget()
    if budget is None or os.getpid() != budget.owner:
        return
    signal.setitimer(signal.ITIMER_REAL, 0)
    if time.monotonic() >= budget.deadline:
        raise BudgetExpired()


def check_budget() -> None:
    """Poll the owner's deadline between units of work, even with its alarm deferred."""
    budget = _current_budget()
    if budget is not None and os.getpid() == budget.owner and time.monotonic() >= budget.deadline:
        raise BudgetExpired()


@contextmanager
def polling_interrupts() -> Iterator[Callable[[], None]]:
    """Record signals during pool ownership; the result loop raises at its next poll.

    Cleanup stays inside this scope so a second Ctrl-C cannot abandon live
    workers halfway through their bounded shutdown. Library calls outside the
    main thread cannot install signal handlers, but still poll their budget.
    """
    pending: list[int] = []

    def record(signum: int, frame: FrameType | None) -> None:
        pending.append(signum)

    def check() -> None:
        if pending:
            if pending[0] == signal.SIGINT:
                raise KeyboardInterrupt()
            if pending[0] == signal.SIGALRM:
                raise BudgetExpired()
            raise SystemExit(128 + pending[0])
        check_budget()

    if threading.current_thread() is not threading.main_thread():
        yield check
        return
    signals = [signal.SIGINT, signal.SIGTERM]
    budget = _current_budget()
    if budget is not None and os.getpid() == budget.owner:
        signals.append(signal.SIGALRM)
    previous = {signum: signal.getsignal(signum) for signum in signals}
    try:
        for signum in previous:
            signal.signal(signum, record)
        try:
            yield check
        finally:
            # Pool cleanup has finished even when a failed future unwinds the scope.
            # Deliver its recorded cancellation before a caller can fall back.
            check()
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def abandon_budget() -> None:
    """End an incomplete preview's deadline so it can report its private snapshot."""
    budget = _current_budget()
    if budget is not None and os.getpid() == budget.owner:
        signal.setitimer(signal.ITIMER_REAL, 0)
        budget.abandoned = True


@contextmanager
def recording_application() -> Iterator[None]:
    """Keep a committed stage edit and its applied count together at the deadline.

    Only the short write-and-record section defers the alarm; rendering and
    external verification remain interruptible. A failed write still rolls
    back through the ordinary transaction machinery.
    """
    budget = _current_budget()
    owner = budget is not None and os.getpid() == budget.owner
    if owner:
        signal.setitimer(signal.ITIMER_REAL, 0)
    try:
        yield
    finally:
        if owner and budget is not None:
            signal.setitimer(signal.ITIMER_REAL, max(0.000001, budget.deadline - time.monotonic()))
