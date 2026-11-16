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

"""A block that awaits becomes an ``async def`` helper that each site awaits.

Audit finding 2 (engine map E, scenarios 27 and 28): every block holding
``await``, ``async for`` or ``async with`` was declined as
``frame_sensitive_block``, although the design record called an awaited
async helper sound (docs/design/proposals/decline-capabilities.md). The
argument is in docs/design/DECISIONS.md, "A block that awaits becomes an
async helper". Generators stay refused, now with a reason of their own.

The hostile half runs each program before and after refactoring, in both
modes: a cancellation delivered while the helper is suspended, an exception
crossing the helper's boundary, a timeout and a task group entered outside
the block, the running task's identity, and duplicates that straddle an
``async with``, which no candidate may split.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

from towel.reason_sentences import explain
from towel.unification.models import RejectReason
from towel.unification.refactor_engine import UnificationRefactorEngine
from tests.test_callback_parameters import FIXTURES, refactored, refactored_scenario

MODES = pytest.mark.parametrize("strict", [False, True], ids=["relaxed", "strict"])


def _async_helpers(source: str) -> List[ast.AsyncFunctionDef]:
    return [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.AsyncFunctionDef) and "extracted_func" in node.name
    ]


def _helpers_of(source: str) -> List[ast.AST]:
    return [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and "extracted_func" in node.name
    ]


def _awaited_helper_calls(source: str) -> List[str]:
    return [
        ast.unparse(node)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Await)
        and isinstance(node.value, ast.Call)
        and "extracted_func" in ast.unparse(node.value.func)
    ]


def _bare_helper_calls(source: str) -> List[str]:
    """Calls of a generated helper that nothing awaits."""
    tree = ast.parse(source)
    awaited = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Await)}
    return [
        ast.unparse(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and "extracted_func" in ast.unparse(node.func)
        and id(node) not in awaited
    ]


# -- The audit's cases ---------------------------------------------------------


class _Synchronous(ast.NodeTransformer):
    """The same program with every ``async`` and ``await`` taken out, for analysis only."""

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        self.generic_visit(node)
        return ast.copy_location(
            ast.FunctionDef(
                name=node.name,
                args=node.args,
                body=node.body,
                decorator_list=node.decorator_list,
                returns=node.returns,
                type_comment=node.type_comment,
            ),
            node,
        )

    def visit_Await(self, node: ast.Await) -> ast.AST:
        self.generic_visit(node)
        return node.value

    def visit_AsyncWith(self, node: ast.AsyncWith) -> ast.AST:
        self.generic_visit(node)
        return ast.copy_location(ast.With(items=node.items, body=node.body), node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> ast.AST:
        self.generic_visit(node)
        return ast.copy_location(
            ast.For(target=node.target, iter=node.iter, body=node.body, orelse=node.orelse), node
        )


def _verdict(path: Path, source: str, strict: bool) -> Tuple[int, Dict[str, int]]:
    path.write_text(source)
    engine = UnificationRefactorEngine(strict=strict)
    proposals = engine.analyze_file(str(path))
    return len(proposals), dict(engine.declined_pairs)


def _as_synchronous(source: str) -> str:
    return ast.unparse(ast.fix_missing_locations(_Synchronous().visit(ast.parse(source))))


@MODES
@pytest.mark.parametrize("case", ["27_async_literal", "28_async_retry", "hostile"])
def test_await_no_longer_decides_a_verdict(tmp_path: Path, case: str, strict: bool) -> None:
    """Each pair is judged as the same code without ``async`` and ``await`` would be.

    Scenario 28's retry loops differ in a call of another arity
    (``bus.publish(payload)`` against ``bucket.put(key, data, checksum)``),
    which no parameter stands for, and scenario 27 is declined in strict mode
    for the order its function releases objects: so are their synchronous
    twins. The audit's expectation for 28 accepts a decline.
    """
    if case == "hostile":
        source = EXCEPTION_ACROSS
    else:
        source = (FIXTURES / "reach" / case / "src" / f"{_MODULES[case]}.py.txt").read_text()
    asynchronous = _verdict(tmp_path / "asynchronous.py", source, strict)
    synchronous = _verdict(tmp_path / "synchronous.py", _as_synchronous(source), strict)
    if not strict:
        assert asynchronous == synchronous
        return
    # Strict mode certifies the hand-over of a whole body's arguments for a
    # plain ``def`` only, so a whole coroutine body it would certify as a
    # function is declined as ``owned_binding_frame_boundary`` instead.
    assert asynchronous[0] <= synchronous[0]
    assert sum(asynchronous[1].values()) == sum(synchronous[1].values()) + (
        synchronous[0] - asynchronous[0]
    )
    assert not {"frame_sensitive_block", "generator_block"} & (
        set(asynchronous[1]) - set(synchronous[1])
    )
    extra = {
        reason: count - synchronous[1].get(reason, 0)
        for reason, count in asynchronous[1].items()
        if count > synchronous[1].get(reason, 0)
    }
    assert set(extra) <= {"owned_binding_frame_boundary"}


_MODULES = {"27_async_literal": "aclient", "28_async_retry": "ajobs"}


def test_two_awaiting_fetches_share_an_async_helper(tmp_path: Path) -> None:
    """Audit scenario 27: ``fetch_user`` and ``fetch_team`` differ in a path and a key."""
    generated, same = refactored_scenario(tmp_path, "reach/27_async_literal")
    assert same
    (helper,) = _async_helpers(generated)
    assert len(_awaited_helper_calls(generated)) == 2
    assert _bare_helper_calls(generated) == []
    assert any(isinstance(node, ast.Await) for node in ast.walk(helper))


@MODES
def test_scenarios_27_and_28_behave_as_before_in_both_modes(tmp_path: Path, strict: bool) -> None:
    for case in ("27_async_literal", "28_async_retry"):
        _, same = refactored_scenario(tmp_path / case, f"reach/{case}", strict=strict)
        assert same, case


# -- Hostile programs ----------------------------------------------------------

CANCELLED_INSIDE = """
import asyncio

LOG = []


async def first(gate, name):
    LOG.append(name + ":start")
    try:
        await gate.wait()
        LOG.append(name + ":woke")
    finally:
        LOG.append(name + ":cleanup")
    LOG.append(name + ":after")
    return name


async def second(gate, name):
    LOG.append(name + ":begin")
    try:
        await gate.wait()
        LOG.append(name + ":woke")
    finally:
        LOG.append(name + ":cleanup")
    LOG.append(name + ":after")
    return name


async def main():
    for function in (first, second):
        task = asyncio.ensure_future(function(asyncio.Event(), function.__name__))
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            LOG.append("cancelled")
    print(LOG)


asyncio.run(main())
"""


@MODES
def test_a_cancellation_reaches_the_await_inside_the_helper(tmp_path: Path, strict: bool) -> None:
    """Cancelled while suspended in the helper: its ``finally`` runs and the caller sees it."""
    generated, same = refactored(tmp_path, CANCELLED_INSIDE, strict=strict)
    assert _async_helpers(generated)
    assert same


EXCEPTION_ACROSS = """
import asyncio


class Refused(Exception):
    pass


async def step(value):
    await asyncio.sleep(0)
    if value < 0:
        raise Refused(value)
    return value * 2


async def first(value):
    try:
        doubled = await step(value)
        checked = doubled + 1
        await asyncio.sleep(0)
        total = checked * 3
    except Refused as error:
        return ("first refused", error.args)
    return ("first", total)


async def second(value):
    try:
        doubled = await step(value)
        checked = doubled + 1
        await asyncio.sleep(0)
        total = checked * 5
    except Refused as error:
        return ("second refused", error.args)
    return ("second", total)


async def main():
    for value in (3, -1):
        print(await first(value), await second(value))
        try:
            await step(value)
        except Refused as error:
            print("raised", error.args)


asyncio.run(main())
"""


@MODES
def test_an_exception_crosses_the_helper_into_the_caller_s_handler(
    tmp_path: Path, strict: bool
) -> None:
    generated, same = refactored(tmp_path, EXCEPTION_ACROSS, strict=strict)
    assert same
    # Strict mode declines it for the release order of its frame, as it
    # declines the synchronous twin (``test_await_no_longer_decides_a_verdict``).
    assert bool(_async_helpers(generated)) is not strict


TIMEOUT_AND_TASKS = """
import asyncio


async def slow(log, label):
    log.append(label + ":waiting")
    await asyncio.sleep(10)
    log.append(label + ":done")


async def first(log, group):
    task = asyncio.current_task()
    log.append(("first", task is asyncio.current_task()))
    group.create_task(slow(log, "first-child"))
    await asyncio.sleep(0)
    log.append("first:slept")
    await slow(log, "first")
    return log


async def second(log, group):
    task = asyncio.current_task()
    log.append(("second", task is asyncio.current_task()))
    group.create_task(slow(log, "second-child"))
    await asyncio.sleep(0)
    log.append("second:slept")
    await slow(log, "second")
    return log


async def run(function):
    log = []
    outer = asyncio.current_task()
    try:
        async with asyncio.timeout(0.05):
            async with asyncio.TaskGroup() as group:
                await function(log, group)
    except TimeoutError:
        log.append("timed out")
    log.append(asyncio.current_task() is outer)
    return log


async def main():
    print(await run(first))
    print(await run(second))


asyncio.run(main())
"""


@MODES
def test_a_timeout_and_a_task_group_entered_outside_the_block_are_unaffected(
    tmp_path: Path, strict: bool
) -> None:
    """``asyncio.timeout`` cancels the task at the helper's ``await``; the task is the same task."""
    generated, same = refactored(tmp_path, TIMEOUT_AND_TASKS, strict=strict)
    assert _async_helpers(generated)
    assert same


STRADDLING = """
import asyncio


class Lock:
    def __init__(self, log):
        self.log = log

    async def __aenter__(self):
        self.log.append("enter")
        await asyncio.sleep(0)

    async def __aexit__(self, *exc):
        self.log.append("exit")
        await asyncio.sleep(0)


async def first(log):
    async with Lock(log):
        log.append("one")
        await asyncio.sleep(0)
    await asyncio.sleep(0)
    return log


async def second(log):
    async with Lock(log) as held:
        log.append("one")
        await asyncio.sleep(0)
    await asyncio.sleep(0)
    return log


async def main():
    print(await first([]), await second([]))


asyncio.run(main())
"""


@MODES
def test_duplicates_that_straddle_an_async_with_are_never_split(
    tmp_path: Path, strict: bool
) -> None:
    """The repeated lines run from inside an ``async with`` to after it, under unlike headers.

    A candidate is a run of whole statements of one suite, so no block can
    hold the end of the statement's body without its ``__aenter__``; the
    pieces on either side are too short, and nothing is extracted.
    """
    path = tmp_path / "straddle.py"
    path.write_text(STRADDLING)
    engine = UnificationRefactorEngine(min_lines=3, skip_trivial_helpers=False, strict=strict)
    assert engine.analyze_file(str(path)) == []
    generated, same = refactored(tmp_path, STRADDLING, strict=strict)
    assert generated == STRADDLING and same


AWAITED_DIFFERENCE = """
import asyncio


async def fetch():
    await asyncio.sleep(0)
    return 5


async def first(cached):
    value = await fetch()
    doubled = value * 2
    print("first", doubled)
    return doubled + 1


async def second(cached):
    value = cached
    doubled = value * 2
    print("second", doubled)
    return doubled + 1


asyncio.run(first(1))
asyncio.run(second(1))
"""


def test_an_awaiting_difference_is_never_made_a_parameter(tmp_path: Path) -> None:
    """``await fetch()`` against ``cached``: neither a ``lambda:`` (a syntax error) nor moved to the call."""
    generated, same = refactored(tmp_path, AWAITED_DIFFERENCE)
    assert same
    assert "lambda: await" not in generated
    # The first statements differ in an ``await``, so only what follows is shared.
    for helper in _helpers_of(generated):
        assert not any(isinstance(node, ast.Await) for node in ast.walk(helper))


ASYNC_GENERATOR_RETURN = """
import asyncio


async def first(items):
    for item in items:
        await asyncio.sleep(0)
        if item is None:
            return
        yield item * 2


async def second(items):
    for item in items:
        await asyncio.sleep(0)
        if item is None:
            return
        yield item * 3


async def main():
    print([item async for item in first([1, 2, None, 4])])
    print([item async for item in second([1, 2, None, 4])])


asyncio.run(main())
"""


@MODES
def test_an_async_generator_s_early_return_is_never_given_a_value(
    tmp_path: Path, strict: bool
) -> None:
    """``return await helper()`` is a syntax error in an async generator; no such call is written."""
    generated, same = refactored(tmp_path, ASYNC_GENERATOR_RETURN, strict=strict)
    compile(generated, "generated.py", "exec")
    assert same


GENERATORS = """
def first(rows):
    for row in rows:
        cleaned = row.strip()
        if cleaned:
            yield cleaned.upper()


def second(rows):
    for row in rows:
        cleaned = row.strip()
        if cleaned:
            yield cleaned.lower()


print(list(first([" a ", ""])), list(second([" B ", ""])))
"""


def test_a_generator_block_is_refused_with_its_own_sentence(tmp_path: Path) -> None:
    path = tmp_path / "generators.py"
    path.write_text(GENERATORS)
    engine = UnificationRefactorEngine(min_lines=3, skip_trivial_helpers=False)
    assert engine.analyze_file(str(path)) == []
    assert "generator_block" in engine.declined_pairs
    assert "frame_sensitive_block" not in engine.declined_pairs
    sentence = explain(str(RejectReason.GENERATOR_BLOCK)).sentence
    assert "yield" in sentence and "await" not in sentence


def test_the_frame_sentence_no_longer_names_await() -> None:
    sentence = explain(str(RejectReason.FRAME_SENSITIVE_BLOCK)).sentence
    assert "await" not in sentence and "yield" not in sentence


FINALIZED = """
import asyncio

EVENTS = []


class Noisy:
    def __init__(self, name):
        self.name = name

    def __del__(self):
        EVENTS.append("del " + self.name)


async def first(label):
    left = Noisy(label + "-left")
    await asyncio.sleep(0)
    right = Noisy(label + "-right")
    EVENTS.append("made " + left.name + " " + right.name)
    return len(EVENTS)


async def second(label):
    left = Noisy(label + "-left")
    await asyncio.sleep(0)
    right = Noisy(label + "-right")
    EVENTS.append("made " + left.name + " " + right.name)
    return len(EVENTS)


async def main():
    EVENTS.append(await first("a"))
    EVENTS.append(await second("b"))
    print(EVENTS)


asyncio.run(main())
"""


def test_strict_mode_leaves_an_awaiting_body_whose_release_order_it_cannot_certify(
    tmp_path: Path,
) -> None:
    """Strict mode keeps exact lifetimes: a whole coroutine body binding objects with finalizers
    is left alone, and the program's ``__del__`` record is unchanged. Relaxed mode extracts it."""
    generated, same = refactored(tmp_path, FINALIZED, strict=True)
    assert same and generated == FINALIZED
    relaxed, _ = refactored(tmp_path, FINALIZED, strict=False)
    assert _async_helpers(relaxed)
