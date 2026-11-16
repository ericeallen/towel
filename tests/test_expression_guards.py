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

"""Hostile programs for expression candidates: what must hold when one is extracted, or it is left alone.

Audit case 44 made an expression inside a statement a candidate. Its
soundness argument is that the call stands where the expression stood, so
nothing outside it moves; what remains is what the helper's frame changes.
Each program here prints what it does, in order, and is run before and
after refactoring in both modes. It is either extracted with the same
output, or declined for the reason the test names. What the guards are told
about where an expression stands is pinned in
``tests/test_expression_site_analyses.py``.

Strict mode must reproduce the output exactly, truth tests and releases
included. Relaxed mode may test a value's truth a second time (the ruling
of 2026-11-16, docs/design/DECISIONS.md, "Relaxed mode is the default"):
the programs that count truth tests say so.
"""

from __future__ import annotations

import contextlib
import io
import textwrap
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Tuple

import pytest

from towel.api import UnificationRefactorEngine
from towel.unification.models import RejectReason, expression_count
from tests.test_callback_parameters import refactored, run_script

NOTE = """
def note(tag, value=True):
    print(tag)
    return value
"""
"""``note(tag, value)`` prints ``tag`` and gives ``value``: an operand whose evaluation is visible."""

TRACED = '''
class T:
    """A value that reports its truth tests, attribute reads and release."""

    def __init__(self, name, truth=True):
        self.name, self.truth = name, truth

    def __bool__(self):
        print("bool", self.name)
        return self.truth

    def __getattr__(self, attribute):
        if attribute.startswith("__"):
            raise AttributeError(attribute)
        print("get", self.name, attribute)
        return T(self.name + "." + attribute, self.truth)

    def __del__(self):
        print("del", self.name)
'''

EXTRACTED = "extracted"


def declined_for(tmp_path: Path, source: str, *, strict: bool) -> Dict[str, int]:
    """The reasons the expression pairs of ``source`` were declined for, with their counts."""
    path = tmp_path / ("reasons_strict.py" if strict else "reasons_relaxed.py")
    path.write_text(source)
    engine = UnificationRefactorEngine(min_lines=3, skip_trivial_helpers=False, strict=strict)
    engine.analyze_file(str(path))
    prefix = expression_count("")
    return {
        reason[len(prefix) :]: count
        for reason, count in engine.declined_pairs.items()
        if reason.startswith(prefix)
    }


def outcome(tmp_path: Path, source: str, *, strict: bool) -> Tuple[str, bool, Dict[str, int]]:
    """The refactored text, whether the program still prints what it printed, and the declines."""
    source = textwrap.dedent(source)
    generated, same = refactored(tmp_path, source, strict=strict)
    return generated, same, declined_for(tmp_path, source, strict=strict)


PROGRAMS: Dict[str, Tuple[str, str, str]] = {}

_Written = Callable[[], None]


def program(
    name: str, relaxed: str, strict: Optional[str] = None
) -> Callable[[_Written], _Written]:
    """Register the docstring of the decorated function as a program with its expected outcomes."""

    def register(function: _Written) -> _Written:
        text = textwrap.dedent(function.__doc__ or "")
        PROGRAMS[name] = (text, relaxed, strict if strict is not None else relaxed)
        return function

    return register


UNDER_A_HANDLER = str(RejectReason.OWNED_BINDING_EXCEPTION_BOUNDARY)
"""Strict mode's earlier refusal of a program whose expression reads, under a handler, a name its
function rebinds or a comprehension's variable (``expression_arguments_outlive_a_handler``)."""


# -- Evaluation order ---------------------------------------------------------


@program("effects_in_order", EXTRACTED)
def _() -> None:
    """
    def first(a, b):
        if (
            note("one", a)
            and note("two", b)
            and note("three", a + b > 2)
        ):
            print("taken")
        print("after")

    def second(pairs):
        return [
            (a, b)
            for a, b in pairs
            if (
                note("one", a)
                and note("two", b)
                and note("three", a + b > 2)
            )
        ]

    for a, b in ((0, 0), (1, 0), (1, 1), (2, 1)):
        first(a, b)
    print(second([(0, 0), (1, 0), (1, 1), (2, 1)]))
    """


@program("value_used_in_order", EXTRACTED)
def _() -> None:
    """
    def first(a, b):
        print("before")
        value = [
            note("one", a)
            or note("two", b)
            or note("three", a + b)
        ]
        print("after", value)
        return value

    def second(a, b):
        return note("first argument"), (
            note("one", a)
            or note("two", b)
            or note("three", a + b)
        ), note("last argument")

    for a, b in ((0, 0), (1, 0), (0, 2)):
        print(first(a, b), second(a, b))
    """


@program("a_differing_call_runs_where_it_stood", EXTRACTED)
def _() -> None:
    """
    def low(x):
        return note("low", x < 2)

    def high(x):
        return note("high", x > 2)

    def first(a, b):
        return [
            note("one", a)
            and note("two", b)
            and low(a + b)
        ]

    def second(a, b):
        return [
            note("one", a)
            and note("two", b)
            and high(a + b)
        ]

    for a, b in ((0, 1), (1, 0), (1, 1), (2, 1)):
        print(first(a, b), second(a, b))
    """


@program("a_differing_read_after_a_call_stays_after_it", EXTRACTED)
def _() -> None:
    """
    def first(a, b):
        return (
            note("start", a),
            b.left,
            note("stop", a),
            b.end,
        )

    def second(a, b):
        return (
            note("start", a),
            b.right,
            note("stop", a),
            b.end,
        )

    for name in ("p", "q"):
        print([item.name for item in first(T(name), T("b"))])
        print([item.name for item in second(T(name), T("b"))])
    """


@program("the_test_of_a_loop_runs_each_time", EXTRACTED)
def _() -> None:
    """
    def first(limit):
        count = 0
        while (
            note("one", count < limit)
            and note("two", count != 3)
            and note("three", limit)
        ):
            count += 1
        return count

    def second(count, limit):
        if (
            note("one", count < limit)
            and note("two", count != 3)
            and note("three", limit)
        ):
            return "more"
        return "done"

    print(first(2), first(5), second(1, 2), second(3, 9))
    """


@program("an_exception_inside_is_caught_where_it_was", EXTRACTED)
def _() -> None:
    """
    def first(table, key):
        try:
            if (
                note("one", table)
                and note("two", table[key])
                and note("three", table[key] > 1)
            ):
                return "big"
        except KeyError as error:
            return "missing " + str(error)
        return "small"

    def second(table, key):
        try:
            while (
                note("one", table)
                and note("two", table[key])
                and note("three", table[key] > 1)
            ):
                return "loop"
        except KeyError as error:
            return "missing " + str(error)
        return "none"

    for table, key in (({}, "a"), ({"a": 0}, "a"), ({"a": 2}, "a"), ({"a": 2}, "b")):
        print(first(table, key), second(table, key))
    """


@program("a_handler_releases_a_parameter_the_expression_read", EXTRACTED, UNDER_A_HANDLER)
def _() -> None:
    """
    def boom():
        raise KeyError("stop")

    def first(item, a):
        try:
            value = (
                note("one", a)
                and boom()
                and note("three", item)
            )
            return value
        except KeyError:
            item = None
            print("handler one")
        return item

    def second(item, a):
        try:
            return [
                note("one", a)
                and boom()
                and note("three", item)
            ]
        except KeyError:
            item = None
            print("handler two")
        return item

    print(first(T("i1"), 1), second(T("i2"), 1))
    """


@program("a_generator_and_a_coroutine_keep_their_expression_in_step", EXTRACTED)
def _() -> None:
    """
    import asyncio

    def first(rows, floor):
        for row in rows:
            yield (
                note("one", row)
                and note("two", row > floor)
                and note("three", row * 2)
            )

    async def second(row, floor):
        await asyncio.sleep(0)
        return [
            note("one", row)
            and note("two", row > floor)
            and note("three", row * 2)
        ]

    print(list(first([0, 1, 5], 2)), asyncio.run(second(5, 2)))
    """


# -- Truth tests --------------------------------------------------------------


@program("two_places_that_only_test", EXTRACTED)
def _() -> None:
    """
    def first(a, b):
        if (
            a.x.y
            and b.x.y
            and a.z.w
        ):
            return 1
        return 0

    def second(a, b):
        while (
            a.x.y
            and b.x.y
            and a.z.w
        ):
            return 2
        return 3

    for a_is_true in (True, False):
        for b_is_true in (True, False):
            print(first(T("a", a_is_true), T("b", b_is_true)))
            print(second(T("a", a_is_true), T("b", b_is_true)))
    """


@program("a_negated_and_a_conditional_test", EXTRACTED)
def _() -> None:
    """
    def first(a, b):
        if not (
            a.x.y
            and b.x.y
            and a.z.w
        ):
            return 1
        return 0

    def second(a, b):
        return "yes" if (
            a.x.y
            and b.x.y
            and a.z.w
        ) else "no"

    for a_is_true in (True, False):
        for b_is_true in (True, False):
            print(first(T("a", a_is_true), T("b", b_is_true)))
            print(second(T("a", a_is_true), T("b", b_is_true)))
    """


@program("a_place_that_tests_and_a_place_that_uses", EXTRACTED, RejectReason.TRUTH_TEST_REPEATED)
def _() -> None:
    """
    def first(a, b):
        if (
            a.x.y
            and b.x.y
            and a.z.w
        ):
            return 1
        return 0

    def second(a, b):
        value = (
            a.x.y
            and b.x.y
            and a.z.w
        )
        print("got", type(value).__name__, value.name)
        return 4

    for a_is_true in (True, False):
        for b_is_true in (True, False):
            print(first(T("a", a_is_true), T("b", b_is_true)))
            print(second(T("a", a_is_true), T("b", b_is_true)))
    """


@program("two_places_that_use_the_value", EXTRACTED)
def _() -> None:
    """
    def first(a, b):
        value = (
            a.x.y
            or b.x.y
            or a.z.w
        )
        print("got", value.name)
        return 1

    def second(a, b):
        return [
            a.x.y
            or b.x.y
            or a.z.w
        ][0].name

    for a_is_true in (True, False):
        for b_is_true in (True, False):
            print(first(T("a", a_is_true), T("b", b_is_true)))
            print(second(T("a", a_is_true), T("b", b_is_true)))
    """


# -- Names read at the call ---------------------------------------------------


@program("a_call_inside_rebinds_a_variable_it_reads_later", RejectReason.REBOUND_EXTERNAL_BINDING)
def _() -> None:
    """
    def outer():
        x = 1

        def bump():
            nonlocal x
            x = 2
            return True

        def first():
            return [
                note("one", x)
                and bump()
                and note("x is %d" % x, x)
            ]

        def second():
            return {
                note("one", x)
                and bump()
                and note("x is %d" % x, x)
            }

        print(first())
        x = 1
        print(second())

    outer()
    """


@program("a_call_inside_rebinds_a_module_variable", EXTRACTED)
def _() -> None:
    """
    LEVEL = 1

    def bump():
        global LEVEL
        LEVEL = 2
        return True

    def first():
        return [
            note("one", LEVEL)
            and bump()
            and note("level is %d" % LEVEL, LEVEL)
        ]

    def second():
        value = (
            note("one", LEVEL)
            and bump()
            and note("level is %d" % LEVEL, LEVEL)
        )
        return value

    print(first())
    LEVEL = 1
    print(second())
    """


@program(
    "a_local_that_may_be_unassigned",
    RejectReason.THUNK_OF_POSSIBLY_UNBOUND_LOCAL,
    UNDER_A_HANDLER,
)
def _() -> None:
    """
    def first(flag, a):
        if flag:
            late = 5
        try:
            return [
                note("one", a)
                and note("two", a)
                and note("three", late)
            ]
        except UnboundLocalError as error:
            return type(error).__name__

    def second(flag, a):
        if flag:
            late = 5
        try:
            value = (
                note("one", a)
                and note("two", a)
                and note("three", late)
            )
            return value
        except UnboundLocalError as error:
            return type(error).__name__

    for flag in (True, False):
        for a in (0, 1):
            print(first(flag, a), second(flag, a))
    """


@program(
    "a_comprehension_variable_not_yet_assigned",
    RejectReason.THUNK_OF_POSSIBLY_UNBOUND_LOCAL,
    UNDER_A_HANDLER,
)
def _() -> None:
    """
    def first(rows, y):
        try:
            return [
                1
                for x in rows
                if (
                    note("one", x)
                    and note("two", x)
                    and note("three", y)
                )
                for y in x
            ]
        except NameError as error:
            return type(error).__name__

    def second(x, y):
        if (
            note("one", x)
            and note("two", x)
            and note("three", y)
        ):
            return 1
        return 0

    print(first([[1], []], 7), first([0], 7), second(1, 2))
    """


@program(
    "a_loop_test_after_its_body_deleted_a_name",
    RejectReason.THUNK_OF_POSSIBLY_UNBOUND_LOCAL,
    UNDER_A_HANDLER,
)
def _() -> None:
    """
    def first(a, b):
        n = 0
        try:
            while (
                note("one", a)
                and note("two", b)
                and note("three", n < 2)
            ):
                n += 1
                if n == 2:
                    del b
        except UnboundLocalError as error:
            return type(error).__name__
        return n

    def second(a, b, n):
        if (
            note("one", a)
            and note("two", b)
            and note("three", n < 2)
        ):
            return "yes"
        return "no"

    print(first(1, 1), first(0, 1), second(1, 1, 5))
    """


@program(
    "a_thunk_would_close_over_a_comprehension_variable",
    RejectReason.THUNK_OF_POSSIBLY_UNBOUND_LOCAL,
)
def _() -> None:
    """
    class Row:
        def __init__(self, n):
            self.left, self.right = n, -n

    def first(rows, k):
        return [
            row.left
            for row in rows
            if (
                note("one", k)
                and note("two", row)
                and note("three", row.left)
            )
        ]

    def second(rows, k):
        return [
            row.left
            for row in rows
            if (
                note("one", k)
                and note("two", row)
                and note("three", row.right)
            )
        ]

    print(first([Row(0), Row(1)], 1), second([Row(0), Row(2)], 1))
    """


# -- Closures and scopes ------------------------------------------------------


@program("a_lambda_inside_reads_a_parameter", EXTRACTED)
def _() -> None:
    """
    def first(items, low):
        return [
            note("one", items)
            and sorted(items, key=lambda item: abs(item - low))
            and note("three", low)
        ]

    def second(items, low):
        value = (
            note("one", items)
            and sorted(items, key=lambda item: abs(item - low))
            and note("three", low)
        )
        return value

    print(first([3, 1, 2], 1), second([], 0), second([5, 4], 5))
    """


@program("a_generator_would_outlive_the_helper", RejectReason.CREATED_OBJECT_ESCAPES)
def _() -> None:
    """
    def first(rows, floor):
        found = (
            note("row %d" % row, row)
            for row in rows
            if row > floor
        )
        floor = 100
        return list(found)

    def second(rows, floor):
        return list(
            note("row %d" % row, row)
            for row in rows
            if row > floor
        )

    print(first([1, 5, 9], 4), second([1, 5, 9], 4))
    """


@program("a_private_name_of_two_classes", RejectReason.PRIVATE_NAME_LEXICAL_CLASS)
def _() -> None:
    """
    class Shop:
        def __init__(self):
            self.__limit = 3

        def big(self, order):
            value = (
                note("one", order)
                and note("two", order > self.__limit)
                and note("three", order % 2 == 0)
            )
            return "big" if value else "small"

    class Depot:
        def __init__(self):
            self.__limit = 5

        def big(self, order):
            return [
                note("one", order)
                and note("two", order > self.__limit)
                and note("three", order % 2 == 0)
            ]

    print(Shop().big(4), Depot().big(4), Depot().big(6))
    """


@program("a_site_marked_no_extract", "untouched")
def _() -> None:
    """
    def first(a, b):  # towel: no-extract
        if (
            note("one", a)
            and note("two", b)
            and note("three", a + b > 2)
        ):
            return 1
        return 0

    def second(a, b):
        return [
            note("one", a)
            and note("two", b)
            and note("three", a + b > 2)
        ]

    print(first(1, 2), second(1, 0))
    """


@program("a_tool_comment_on_a_line_the_expression_shares", RejectReason.DIRECTIVE_ON_SHARED_LINE)
def _() -> None:
    """
    def first(a, b):
        value = [note("one", a)  # noqa: E501
                 and note("two", b)
                 and note("three", a + b > 2)]
        return value

    def second(a, b):
        return [
            note("one", a)
            and note("two", b)
            and note("three", a + b > 2)
        ]

    print(first(1, 2), second(1, 0))
    """


ALSO_DECLINED: Mapping[str, set[str]] = {
    # The two comprehensions pair as well, whole. Their differing attribute
    # of the comprehension's own variable becomes a callback, which the
    # instantiation check refuses for the statements too (not this lane's).
    "a_thunk_would_close_over_a_comprehension_variable": {str(RejectReason.INSTANTIATION_MISMATCH)},
}
"""What else a program's other expression pairs are declined for."""


def _source(name: str) -> str:
    text = PROGRAMS[name][0]
    prelude = TRACED if "T(" in text else ""
    return prelude + (NOTE if "note(" in text else "") + text


@pytest.mark.parametrize("strict", [False, True], ids=["relaxed", "strict"])
@pytest.mark.parametrize("name", sorted(PROGRAMS))
def test_a_hostile_program_is_extracted_unchanged_or_left_alone(
    tmp_path: Path, name: str, strict: bool
) -> None:
    expected = PROGRAMS[name][2 if strict else 1]
    source = _source(name)
    generated, same, declined = outcome(tmp_path, source, strict=strict)
    if expected == EXTRACTED:
        assert "__extracted_func_0(" in generated, declined
        assert not declined
        if "T(" in source and not strict:
            # Relaxed mode: the same events, but for truth tests and releases.
            assert _without_truth_and_release(tmp_path, strict)
        else:
            assert same
    elif expected == "untouched":
        assert generated == source and not declined
    else:
        assert generated == source, generated
        assert str(expected) in declined
        assert set(declined) <= {str(expected)} | ALSO_DECLINED.get(name, set())
        assert same


def _without_truth_and_release(tmp_path: Path, strict: bool) -> bool:
    """Whether the original and the changed program print the same, truth tests and releases aside."""
    directory = tmp_path / ("strict" if strict else "relaxed")
    kept: List[List[str]] = []
    for script in ("original.py", "changed.py"):
        status, output, _ = run_script(directory / script)
        assert status == 0
        kept.append(
            [line for line in output.splitlines() if not line.startswith(("bool ", "del "))]
        )
    return kept[0] == kept[1]


def test_relaxed_mode_tests_a_deciding_operand_a_second_time_and_nothing_else(
    tmp_path: Path,
) -> None:
    """The one assumption relaxed mode adds for expressions, held to exactly that.

    ``if a and b:`` tests ``a`` once; ``if helper(a, b):`` with ``return a
    and b`` tests it in the helper and again in the ``if`` when ``a``
    decided. Every other line of the program's output is the same, in the
    same order.
    """
    source = _source("two_places_that_only_test")
    generated, same, _ = outcome(tmp_path, source, strict=False)
    assert "True if" not in generated and not same
    outputs = [
        run_script(tmp_path / "relaxed" / script)[1].splitlines()
        for script in ("original.py", "changed.py")
    ]
    extra = list(outputs[1])
    for line in outputs[0]:
        extra.remove(line)
    assert extra and all(line.startswith("bool ") for line in extra)
    without_repeats = [
        line
        for index, line in enumerate(outputs[1])
        if not (line.startswith("bool ") and index and outputs[1][index - 1] == line)
    ]
    assert without_repeats == outputs[0]


def test_strict_mode_reproduces_truth_tests_and_releases(tmp_path: Path) -> None:
    """Strict mode's guarantee, on the programs that print every truth test and every release."""
    for name in ("two_places_that_only_test", "a_negated_and_a_conditional_test"):
        generated, same, _ = outcome(tmp_path / name, _source(name), strict=True)
        assert "True if" in generated and same
    generated, same, _ = outcome(tmp_path, _source("two_places_that_use_the_value"), strict=True)
    assert "True if" not in generated and "__extracted_func_0(" in generated and same


RELEASED_IN_A_HANDLER = """
events = []


class Noisy:
    def __init__(self, name):
        self.name = name

    def __del__(self):
        events.append("del:" + self.name)


def boom():
    raise RuntimeError("stop")


def f1(a):
    x = Noisy("x1")
    try:
        res = (
            a > 0
            and boom()
            and x is not None
            and a < 100
            and a != 42
        )
        return res
    except RuntimeError:
        x = None
        events.append("handler:f1")
    return x


def f2(a):
    x = Noisy("x2")
    try:
        out = (
            a > 0
            and boom()
            and x is not None
            and a < 100
            and a != 42
        )
        return out
    except RuntimeError:
        x = None
        events.append("handler:f2")
    return x


print(f1(5), f2(5), events)
"""


@pytest.mark.parametrize("strict", [False, True], ids=["relaxed", "strict"])
def test_strict_mode_leaves_an_expression_inside_a_try_alone(tmp_path: Path, strict: bool) -> None:
    """Review of the ``expressions`` lane, defect 3: strict mode released ``x`` after the handler.

    A helper that raises stays on the traceback, and its frame holds its
    arguments until the handler ends, so ``x = None`` in the handler no
    longer released ``x`` there. Through 1.949 strict mode declined both
    assignments (``owned_binding_exception_boundary``); it still does, and
    leaves their values alone too. Relaxed mode promises no release order.
    """
    generated, same, declined = outcome(tmp_path, RELEASED_IN_A_HANDLER, strict=strict)
    if strict:
        # What remains is the extraction of both whole bodies, as 1.949 made it.
        assert generated == _statements_only(tmp_path, RELEASED_IN_A_HANDLER), generated
        assert same
        assert set(declined) == {str(RejectReason.OWNED_BINDING_EXCEPTION_BOUNDARY)}
    else:
        assert "__extracted_func_0(" in generated


def _statements_only(tmp_path: Path, source: str) -> str:
    """``source`` refactored in strict mode without expression candidates."""
    path = tmp_path / "statements_only.py"
    path.write_text(source)
    engine = UnificationRefactorEngine(
        min_lines=3, skip_trivial_helpers=False, strict=True, expression_candidates=False
    )
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        engine.refactor_to_fixed_point(str(path))
    return path.read_text()


# -- Review of 2026-11-16 -------------------------------------------------------

UNASSIGNED_AFTER_A_SUBSCRIPT: Dict[str, str] = {
    "a_later_clauses_variable": """
        def first(rows, y):
            try:
                return [
                    1
                    for x in rows
                    if (
                        x[0]
                        + y
                        + x[1]
                        + x[2]
                        + x[3]
                    ) > 0
                    for y in x
                ]
            except IndexError:
                return "empty"

        def second(x, y):
            try:
                if (
                    x[0]
                    + y
                    + x[1]
                    + x[2]
                    + x[3]
                ) > 0:
                    return 1
            except IndexError:
                return "empty"
            return 0

        print(first([[]], 7), second([], 7), second([1, 2, 3, 4], 7))
        """,
    "a_local_bound_on_one_path": """
        def first(has_bonus, items):
            if has_bonus:
                bonus = 10
            try:
                return [
                    items[0]
                    + bonus
                    + items[1]
                    + items[2]
                ]
            except IndexError:
                return "empty"

        def second(items, bonus):
            try:
                value = (
                    items[0]
                    + bonus
                    + items[1]
                    + items[2]
                )
                return value
            except IndexError:
                return "empty"

        print(first(False, []), first(True, [1, 2, 3]), second([], 3), second([1, 2, 3], 3))
        """,
    "a_block_of_statements": """
        def first(flag, items):
            if flag:
                bonus = 10
            try:
                total = items[0] + bonus + items[1]
                print("total", total)
                print("done", total * 2)
                return total
            except IndexError:
                return "empty"

        def second(flag, items, bonus):
            try:
                total = items[0] + bonus + items[1]
                print("total", total)
                print("done", total * 2)
                return total
            except IndexError:
                return "empty"

        print(first(False, []), first(True, [1, 2]), second(False, [], 3))
        """,
}
"""A name that may be unassigned, read after a subscript that fails first."""


@pytest.mark.parametrize("strict", [False, True], ids=["relaxed", "strict"])
@pytest.mark.parametrize("name", sorted(UNASSIGNED_AFTER_A_SUBSCRIPT))
def test_a_name_that_may_be_unassigned_is_not_read_ahead_of_a_subscript(
    tmp_path: Path, name: str, strict: bool
) -> None:
    """Review of the ``expressions`` lane, defect 2 (audit case 44; fixed by ``ux/byvalue``).

    Relaxed mode passed ``y`` (or ``bonus``) by value because every step
    before its read is quiet. But ``x[0]`` fails first when the list is
    empty, and the handler caught that; read at the call, the unassigned
    name raised ``UnboundLocalError`` instead. The name stays a lambda,
    which is declined, or the code is extracted another way that reads it
    where it was read.
    """
    generated, same = refactored(
        tmp_path, textwrap.dedent(UNASSIGNED_AFTER_A_SUBSCRIPT[name]), strict=strict
    )
    assert same, generated


SHADOWED_LATER = """
def first(items, low):
    found = [
        x * 2 + low
        for x in items
        if x > low
        and x % 2 == 0
        and x != 42
    ]
    x = 0
    return found, x


def second(items, low):
    return [
        x * 2 + low
        for x in items
        if x > low
        and x % 2 == 0
        and x != 42
    ]


print(first([1, 2, 4, 42], 1), second([0, 2, 6], 1))
"""


@pytest.mark.parametrize("strict", [False, True], ids=["relaxed", "strict"])
def test_a_comprehensions_variable_is_not_the_local_its_function_binds_later(
    tmp_path: Path, strict: bool
) -> None:
    """Review of the ``expressions`` lane, second suspicion: a needless refusal.

    ``x`` in the comprehension is the comprehension's own variable, bound
    where the condition reads it; ``x = 0`` after the comprehension binds
    the function's. The pair was declined as reading a variable assigned
    only later in its function (``incomplete_lifetime_block1``).
    """
    generated, same, declined = outcome(tmp_path, SHADOWED_LATER, strict=strict)
    assert str(RejectReason.INCOMPLETE_LIFETIME_BLOCK1) not in declined, declined
    assert "__extracted_func_0(" in generated and same
