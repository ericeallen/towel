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

"""A deadline cannot interrupt committed publication or strand the caller's timer."""

import signal
import time
from types import FrameType

import pytest

from towel.execution_budget import BudgetExpired, finish_before_publication, run_with_budget


def test_timeout_unwinds_and_restores_signal_state() -> None:
    previous = signal.getsignal(signal.SIGALRM)
    cleaned = False

    def work() -> None:
        nonlocal cleaned
        try:
            time.sleep(1)
        finally:
            cleaned = True

    with pytest.raises(BudgetExpired):
        run_with_budget(0.01, work)
    assert cleaned
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    assert signal.getsignal(signal.SIGALRM) == previous


def test_publication_finishes_after_the_budget_without_false_timeout() -> None:
    def publish() -> None:
        finish_before_publication()
        time.sleep(0.15)

    run_with_budget(0.1, publish)
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)


def test_an_existing_timer_is_never_overwritten() -> None:
    def nested() -> None:
        with pytest.raises(ValueError, match="already active"):
            run_with_budget(1, lambda: pytest.fail("must not replace the outer budget"))
        assert signal.getitimer(signal.ITIMER_REAL)[0] > 0

    run_with_budget(10, nested)


def test_an_unrelated_alarm_handler_survives_an_unexpected_exception() -> None:
    previous = signal.getsignal(signal.SIGALRM)
    received: list[int] = []

    def unrelated(signum: int, frame: FrameType | None) -> None:
        received.append(signum)

    def fail() -> None:
        raise RuntimeError("injected failure")

    signal.signal(signal.SIGALRM, unrelated)
    try:
        with pytest.raises(RuntimeError, match="injected failure"):
            run_with_budget(10, fail)
        assert signal.getsignal(signal.SIGALRM) is unrelated
        assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
        signal.raise_signal(signal.SIGALRM)
        assert received == [signal.SIGALRM]
    finally:
        signal.signal(signal.SIGALRM, previous)


def test_alarm_during_pool_cleanup_does_not_abandon_workers() -> None:
    """Runtime 1: a deadline during interrupt cleanup cannot interrupt its joins."""
    from towel.execution_budget import polling_interrupts

    cleaned = False

    def work() -> None:
        nonlocal cleaned
        with polling_interrupts() as check:
            try:
                signal.raise_signal(signal.SIGINT)
                check()
            finally:
                signal.raise_signal(signal.SIGALRM)
                cleaned = True

    with pytest.raises(KeyboardInterrupt):
        run_with_budget(10, work)
    assert cleaned


def test_interrupt_during_normal_pool_cleanup_is_not_lost() -> None:
    """Runtime 1: an interrupt after the final result poll still ends the run."""
    from towel.execution_budget import polling_interrupts

    cleaned = False
    with pytest.raises(KeyboardInterrupt):
        with polling_interrupts() as check:
            check()
            signal.raise_signal(signal.SIGINT)
            cleaned = True
    assert cleaned


def test_deadline_during_normal_pool_cleanup_is_delivered_after_cleanup() -> None:
    """Runtime 1: the recorded deadline is raised only after worker cleanup ends."""
    from towel.execution_budget import polling_interrupts

    cleaned = False

    def work() -> None:
        nonlocal cleaned
        with polling_interrupts() as check:
            check()
            signal.raise_signal(signal.SIGALRM)
            cleaned = True

    with pytest.raises(BudgetExpired):
        run_with_budget(10, work)
    assert cleaned


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_recorded_signal_survives_a_broken_pool(signum: int) -> None:
    """D1: a pool error cannot discard the cancellation recorded before it."""
    from concurrent.futures.process import BrokenProcessPool
    from towel.execution_budget import polling_interrupts

    previous = signal.getsignal(signum)
    expected = KeyboardInterrupt if signum == signal.SIGINT else SystemExit
    with pytest.raises(expected) as raised:
        with polling_interrupts():
            signal.raise_signal(signum)
            raise BrokenProcessPool("worker received the group signal")
    if isinstance(raised.value, SystemExit):
        assert raised.value.code == 143
    assert signal.getsignal(signum) == previous


def test_deadline_at_disarm_cannot_strand_the_signal_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Integration 8: a deadline at teardown must leave the caller able to set another."""
    from contextvars import copy_context

    previous = signal.getsignal(signal.SIGALRM)
    real_setitimer = signal.setitimer

    def expire_at_disarm(timer: int, seconds: float) -> tuple[float, float]:
        if not seconds:
            signal.raise_signal(signal.SIGALRM)
        return real_setitimer(timer, seconds)

    def run() -> None:
        with monkeypatch.context() as patch:
            patch.setattr(signal, "setitimer", expire_at_disarm)
            try:
                run_with_budget(30, lambda: None)
            except BudgetExpired:
                pass
        assert signal.getsignal(signal.SIGALRM) == previous
        assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
        run_with_budget(30, lambda: None)

    try:
        copy_context().run(run)
    finally:
        real_setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def test_kernel_alarm_after_a_deadline_poll_cannot_interrupt_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace
    from towel import execution_budget

    cleaned = False

    def work() -> None:
        nonlocal cleaned
        # The monotonic deadline is visible before the kernel delivers SIGALRM.
        monkeypatch.setattr(
            execution_budget, "time", SimpleNamespace(monotonic=lambda: float("inf"))
        )
        try:
            execution_budget.check_budget()
        finally:
            signal.raise_signal(signal.SIGALRM)
            cleaned = True

    with pytest.raises(BudgetExpired):
        run_with_budget(60, work)
    assert cleaned
