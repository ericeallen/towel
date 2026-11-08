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

from towel.execution_budget import BudgetExpired, execution_budget, finish_before_publication


def test_timeout_unwinds_and_restores_signal_state() -> None:
    previous = signal.getsignal(signal.SIGALRM)
    cleaned = False
    with pytest.raises(BudgetExpired):
        with execution_budget(0.01):
            try:
                time.sleep(1)
            finally:
                cleaned = True
    assert cleaned
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    assert signal.getsignal(signal.SIGALRM) == previous


def test_publication_finishes_after_the_budget_without_false_timeout() -> None:
    with execution_budget(0.1):
        finish_before_publication()
        time.sleep(0.15)
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)


def test_an_existing_timer_is_never_overwritten() -> None:
    with execution_budget(10):
        with pytest.raises(ValueError, match="already active"):
            with execution_budget(1):
                pytest.fail("must not replace the outer budget")
        assert signal.getitimer(signal.ITIMER_REAL)[0] > 0


def test_an_unrelated_alarm_handler_survives_an_unexpected_exception() -> None:
    previous = signal.getsignal(signal.SIGALRM)
    received: list[int] = []

    def unrelated(signum: int, frame: FrameType | None) -> None:
        received.append(signum)

    signal.signal(signal.SIGALRM, unrelated)
    try:
        with pytest.raises(RuntimeError, match="injected failure"):
            with execution_budget(10):
                raise RuntimeError("injected failure")
        assert signal.getsignal(signal.SIGALRM) is unrelated
        assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
        signal.raise_signal(signal.SIGALRM)
        assert received == [signal.SIGALRM]
    finally:
        signal.signal(signal.SIGALRM, previous)
