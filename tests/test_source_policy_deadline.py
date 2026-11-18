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

"""An interruption while a run's source policy is scoped cannot outlive the run.

A relaxed policy left active makes every later run in the process skip what it
should refuse. These tests need no timing: a trace function raises a real signal
at one chosen bytecode step of entering or leaving the policy's scope, and the
walking tests visit every step. The exception is held while the policy is
inspected, as a caller or pytest would hold it, so that a suspended generator's
cleanup cannot run late and hide the leak. The policy is inspected inside the
time limit, in the context that made the call: ``run_with_budget`` has a context
of its own, which would discard a leak before a check outside it could see one.
"""

from __future__ import annotations

from dataclasses import dataclass
from dis import opmap
import gc
from itertools import count
from pathlib import Path
import signal
import sys
from types import FrameType
from typing import TYPE_CHECKING, Callable, Iterator, Optional

import pytest

from towel.api import UnificationRefactorEngine
from towel.cli import _install_signal_handling
from towel.execution_budget import BudgetExpired, run_with_budget
from towel.program_files import refuse_unparsed_program
from towel.source_files import (
    _SOURCE_POLICY,
    SourcePolicy,
    current_source_policy,
    source_policy_context,
    with_source_policy,
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


@pytest.fixture(autouse=True)
def no_collection_inside_the_window() -> Iterator[None]:
    """Keep other tests' garbage out: Python discards a signal's exception in a weakref callback."""
    collecting = gc.isenabled()
    gc.collect()
    gc.disable()
    yield
    if collecting:
        gc.enable()


@pytest.fixture(autouse=True)
def command_line_signal_handlers() -> Iterator[None]:
    """Answer Ctrl-C and SIGTERM as the command line does, whatever the runner installed."""
    previous = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
    signal.signal(signal.SIGINT, signal.default_int_handler)
    _install_signal_handling()
    yield
    for number, handler in previous.items():
        signal.signal(number, handler)


@dataclass(frozen=True)
class Interruption:
    """A signal a run can receive, and the exception that unwinds the run."""

    signal_number: signal.Signals
    raises: type[BaseException]


TIME_LIMIT = Interruption(signal.SIGALRM, BudgetExpired)
INTERRUPTIONS = {
    "time-limit": TIME_LIMIT,
    "ctrl-c": Interruption(signal.SIGINT, KeyboardInterrupt),
    "sigterm": Interruption(signal.SIGTERM, SystemExit),
}


@dataclass(frozen=True)
class Attempt:
    """Where the signal was raised, what the caller received, and the policy it was left with."""

    fired_in: Optional[str]
    caught: Optional[BaseException]
    left_behind: Optional[SourcePolicy]


FiresHere = Callable[[FrameType], bool]


def _run_until_interrupted(
    body: Callable[[], object],
    fires_here: FiresHere,
    interruption: Interruption,
    afterward: Callable[[], object] = lambda: None,
) -> Attempt:
    """Run ``body`` under a budget, raising the signal at the first step ``fires_here`` picks.

    ``afterward`` runs where the caller continues, while the exception is still held.
    """
    fired_in: list[str] = []

    def trace(frame: FrameType, event: str, arg: object) -> Optional[TraceFunction]:
        if frame.f_code is body.__code__:
            return None  # The caller's own bytecode is not part of the policy's window.
        frame.f_trace_opcodes = True
        if event == "opcode" and not fired_in and fires_here(frame):
            fired_in.append(frame.f_code.co_qualname)
            signal.raise_signal(interruption.signal_number)
        return trace

    previous_trace = sys.gettrace()

    attempts: list[Attempt] = []

    def caller() -> None:
        caught: Optional[BaseException] = None
        sys.settrace(trace)
        try:
            body()
        except interruption.raises as error:
            caught = error
        finally:
            sys.settrace(previous_trace)  # Disarming the budget is not part of the window.
        afterward()
        where = fired_in[0] if fired_in else None
        attempts.append(Attempt(where, caught, current_source_policy()))

    run_with_budget(3600, caller)
    return attempts[0]


def _at_step(step: int) -> FiresHere:
    steps = count()
    return lambda frame: next(steps) == step


@dataclass(frozen=True)
class _Options:
    strict: bool
    include_ignored: bool = False


def _scoped_run(strict: bool, shared: Optional[SourcePolicy]) -> Callable[[], None]:
    """A run that checks the policy it sees: its own, or the outer one it must share."""

    @with_source_policy
    def run(options: _Options) -> None:
        policy = current_source_policy()
        assert policy is not None and policy.strict == options.strict
        assert shared is None or policy is shared

    def body() -> None:
        run(_Options(strict))

    return body


def _assert_every_step_restores(
    outer: Optional[SourcePolicy],
    strict: bool,
    interruption: Interruption,
    shared: Optional[SourcePolicy] = None,
) -> None:
    body = _scoped_run(strict, shared)
    for step in count():
        attempt = _run_until_interrupted(body, _at_step(step), interruption)
        if attempt.fired_in is None:
            assert step > 10, "the trace never reached the policy"
            return
        where = f"step {step}, in {attempt.fired_in}"
        assert attempt.caught is not None, f"the interruption was lost at {where}"
        assert attempt.left_behind is outer, f"the policy outlived its run at {where}"


@pytest.fixture(params=sorted(INTERRUPTIONS))
def interruption(request: pytest.FixtureRequest) -> Interruption:
    return INTERRUPTIONS[request.param]


def test_an_interruption_at_any_step_leaves_no_policy_behind(interruption: Interruption) -> None:
    _assert_every_step_restores(None, False, interruption)


def test_an_interruption_at_any_step_restores_a_different_outer_policy(
    interruption: Interruption,
) -> None:
    def walk() -> None:
        _assert_every_step_restores(current_source_policy(), False, interruption)

    source_policy_context(strict=True).run(walk)


def test_an_interruption_at_any_step_keeps_the_outer_policy_a_nested_run_shares(
    interruption: Interruption,
) -> None:
    def walk() -> None:
        outer = current_source_policy()
        _assert_every_step_restores(outer, False, interruption, shared=outer)

    source_policy_context(strict=False).run(walk)


def _returning_from(qualified_name: str) -> FiresHere:
    """Fire on the last instruction of a function, when its work is done."""

    def fires_here(frame: FrameType) -> bool:
        code = frame.f_code
        return (
            code.co_qualname == qualified_name
            and code.co_code[frame.f_lasti] == opmap["RETURN_VALUE"]
        )

    return fires_here


@pytest.mark.parametrize(
    "fires_here",
    [
        _returning_from("source_policy_context"),
        _returning_from("with_source_policy.<locals>.run"),
    ],
    ids=["entering", "leaving"],
)
def test_an_api_caller_keeps_its_refusals_after_a_deadline(
    tmp_path: Path, fires_here: FiresHere
) -> None:
    """Integration 8: a caller that survives the limit must not inherit the run's relaxed policy."""
    source = tmp_path / "clean.py"
    source.write_text(DUPLICATES)
    (tmp_path / "broken.py").write_text("def broken(:\n")
    engine = UnificationRefactorEngine(strict=False)

    def still_refuses() -> None:
        with pytest.raises(UnparsedProgramError, match="broken.py"):
            refuse_unparsed_program(tmp_path)

    attempt = _run_until_interrupted(
        lambda: engine.analyze_file(str(source)), fires_here, TIME_LIMIT, still_refuses
    )

    assert attempt.fired_in is not None
    assert isinstance(attempt.caught, BudgetExpired)
    assert attempt.left_behind is None
