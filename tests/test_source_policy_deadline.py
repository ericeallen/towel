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

"""A deadline that lands while a run's source policy changes cannot outlive the run.

A relaxed policy left active makes every later run in the process skip what it
should refuse. These tests need no timing: a trace function raises the real
time-limit signal at one chosen bytecode step of entering or leaving the policy,
and the first test walks every step. The exception is held while the policy is
inspected, as a caller or pytest would hold it, so that a suspended generator's
cleanup cannot run late and hide the leak.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import count
from pathlib import Path
import signal
import sys
import time
from types import FrameType
from typing import TYPE_CHECKING, Callable, Iterator, Optional

import pytest

from towel.api import UnificationRefactorEngine
from towel.execution_budget import BudgetExpired, execution_budget
from towel.program_files import refuse_unparsed_program
from towel.source_files import (
    _SOURCE_POLICY,
    SourcePolicy,
    current_source_policy,
    source_policy,
)
from towel.unification.exceptions import UnparsedProgramError

if TYPE_CHECKING:
    from _typeshed import TraceFunction

DUPLICATES = """\
def first(values):
    total = 0
    for value in values:
        total += value * 2
    return total


def second(items):
    total = 0
    for item in items:
        total += item * 2
    return total
"""


@pytest.fixture(autouse=True)
def no_policy_survives_a_failed_test() -> Iterator[None]:
    """A failure here must not spread the leak it reports to the tests that follow."""
    yield
    _SOURCE_POLICY.set(None)


@dataclass(frozen=True)
class Attempt:
    """Where the signal was raised, and the expiry the caller received."""

    fired_in: Optional[str]
    expired: Optional[BudgetExpired]


FiresHere = Callable[[FrameType], bool]


def _wait_for_delivery() -> None:
    """Wait in a function of its own: CPython 3.13 leaves a ``while`` directly under ``with``
    without an exception handler on its backward jump, where a signal escapes the ``with``."""
    give_up = time.monotonic() + 5
    while time.monotonic() < give_up:
        time.sleep(0.001)


def _run_until_deadline(body: Callable[[], object], fires_here: FiresHere) -> Attempt:
    """Run ``body`` under a budget, raising its signal at the first step ``fires_here`` picks."""
    fired_in: list[str] = []

    def trace(frame: FrameType, event: str, arg: object) -> Optional[TraceFunction]:
        if frame.f_code is body.__code__:
            return None  # The caller's own bytecode is not part of the policy's window.
        frame.f_trace_opcodes = True
        if event == "opcode" and not fired_in and fires_here(frame):
            fired_in.append(frame.f_code.co_qualname)
            signal.raise_signal(signal.SIGALRM)
        return trace

    previous_trace = sys.gettrace()
    expired: Optional[BudgetExpired] = None
    try:
        with execution_budget(3600):
            sys.settrace(trace)
            body()
            sys.settrace(previous_trace)
            if fired_in:
                _wait_for_delivery()
    except BudgetExpired as caught:
        expired = caught
    finally:
        sys.settrace(previous_trace)
    return Attempt(fired_in[0] if fired_in else None, expired)


def _at_step(step: int) -> FiresHere:
    steps = count()
    return lambda frame: next(steps) == step


def _enter_and_leave(strict: bool) -> Callable[[], None]:
    def body() -> None:
        with source_policy(strict=strict):
            pass

    return body


def _assert_every_step_restores(outer: Optional[SourcePolicy], strict: bool) -> None:
    body = _enter_and_leave(strict)
    for step in count():
        attempt = _run_until_deadline(body, _at_step(step))
        if attempt.fired_in is None:
            assert step > 10, "the trace never reached the policy"
            return
        where = f"step {step}, in {attempt.fired_in}"
        assert attempt.expired is not None, f"the deadline was lost at {where}"
        assert current_source_policy() is outer, f"the policy outlived its run at {where}"


def test_a_deadline_at_any_step_leaves_no_policy_behind() -> None:
    _assert_every_step_restores(None, strict=False)


def test_a_deadline_at_any_step_restores_a_different_outer_policy() -> None:
    with source_policy(strict=True) as outer:
        _assert_every_step_restores(outer, strict=False)


def test_a_deadline_at_any_step_keeps_the_outer_policy_a_nested_run_shares() -> None:
    with source_policy(strict=False) as outer:
        _assert_every_step_restores(outer, strict=False)


def _in_policy_scope(frame: FrameType) -> bool:
    """Whether this frame enters or leaves a source policy, however that is implemented."""
    if frame.f_code.co_name == "source_policy":
        return True
    if frame.f_code.co_name not in ("__enter__", "__exit__"):
        return False
    owner = frame.f_locals.get("self")
    generator = getattr(owner, "gen", None)
    name = generator.gi_code.co_name if generator is not None else type(owner).__name__
    return "sourcepolicy" in name.replace("_", "").lower()


def _just_installed(frame: FrameType) -> bool:
    return _in_policy_scope(frame) and current_source_policy() is not None


def _leaving(frame: FrameType) -> bool:
    return frame.f_code.co_name == "__exit__" and _in_policy_scope(frame)


@pytest.mark.parametrize("fires_here", [_just_installed, _leaving], ids=["entering", "leaving"])
def test_an_api_caller_keeps_its_refusals_after_a_deadline(
    tmp_path: Path, fires_here: FiresHere
) -> None:
    """Integration 8: a caller that survives the limit must not inherit the run's relaxed policy."""
    source = tmp_path / "clean.py"
    source.write_text(DUPLICATES)
    (tmp_path / "broken.py").write_text("def broken(:\n")
    engine = UnificationRefactorEngine(strict=False)

    attempt = _run_until_deadline(lambda: engine.analyze_file(str(source)), fires_here)

    assert attempt.fired_in is not None
    assert current_source_policy() is None
    with pytest.raises(UnparsedProgramError, match="broken.py"):
        refuse_unparsed_program(tmp_path)
