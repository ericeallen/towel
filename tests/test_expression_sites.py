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

"""Which expressions are candidates, and how they are bucketed.

The user-perspective audit of 1.949 (case 44, engine map E: "Repeated
expressions: candidates are statements, never an expression inside one")
showed one five-line condition, written in an ``if`` and in a generator's
``if`` clause, that Towel never saw. An expression candidate is a complete
expression of a function body, at a place where it is evaluated for its
value, spanning ``--min-lines`` lines. This module pins which expressions
those are, each exclusion of the design, what the engine records about
where one stands, and the key that buckets them.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

from towel.api import UnificationRefactorEngine
from towel.unification.block_signature import extract_block_signature, quick_filter
from towel.unification.expression_sites import (
    ExpressionSite,
    census,
    expression_key,
    expression_signature,
    expression_site,
    expression_sites,
    is_expression_block,
    standing_block,
    truth_decides,
)
from tests.test_candidate_index import analyzed_functions

CONDITION = """(
        member.is_active
        and not member.is_banned
        and member.warnings < 3
    )"""
"""A three-line ``and`` of seven operands' worth of syntax, written inside parentheses."""


def sites_of(source: str, min_lines: int = 3) -> List[ExpressionSite]:
    """The expression candidates of the first function of ``source``."""
    module = ast.parse(textwrap.dedent(source))
    function = next(
        node for node in module.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    return expression_sites(function, min_lines)


def kinds(source: str, min_lines: int = 3) -> List[str]:
    return [type(site.expression).__name__ for site in sites_of(source, min_lines)]


# -- Which expressions --------------------------------------------------------


def test_the_audits_condition_is_a_candidate_in_an_if_and_in_a_comprehension() -> None:
    """Audit case 44: the condition is one candidate in each function, wherever it is written."""
    in_if = sites_of(f"""
    def can_post(member, now):
        if {CONDITION}:
            return True
        return False
    """)
    in_clause = sites_of(f"""
    def visible(members, now):
        return sorted(
            member.name
            for member in members
            if {CONDITION}
        )
    """)
    assert [type(site.expression).__name__ for site in in_if] == ["BoolOp"]
    # The generator is a candidate too; its condition is the one that matters.
    assert [type(site.expression).__name__ for site in in_clause] == ["GeneratorExp", "BoolOp"]
    assert ast.dump(in_if[0].expression) == ast.dump(in_clause[1].expression)
    assert (in_if[0].first_line, in_if[0].last_line) == (4, 6)


@pytest.mark.parametrize(
    "statement",
    [
        f"if {CONDITION}:\n        pass",
        f"while {CONDITION}:\n        break",
        f"assert {CONDITION}",
        f"if now:\n        pass\n    elif {CONDITION}:\n        pass",
        f"return {CONDITION}",
        f"allowed = {CONDITION}",
        f"allowed: bool = {CONDITION}",
        f"total += {CONDITION}",
        f"record({CONDITION}, now)",
        f"record(now, flag={CONDITION})",
        f"table[{CONDITION}] = now",
        f"result = {{now: {CONDITION}}}",
        f"result = [{CONDITION} for member in now]",
        f"result = [member for member in now if {CONDITION}]",
        f"result = now if {CONDITION} else None",
        f"for item in {CONDITION}:\n        pass",
        f"with {CONDITION} as handle:\n        pass",
        f"try:\n        pass\n    except {CONDITION}:\n        pass",
        f"match now:\n        case [first] if {CONDITION}:\n            pass",
        f"raise {CONDITION}",
        f"yield {CONDITION}",
        f"del table[{CONDITION}]",
    ],
)
def test_an_expression_is_a_candidate_wherever_it_is_evaluated_for_its_value(
    statement: str,
) -> None:
    """A condition, an argument, a returned or assigned value, a comprehension's element or clause."""
    source = f"def f(member, now, table, total):\n    {statement}\n"
    found = sites_of(source)
    assert "BoolOp" in [type(site.expression).__name__ for site in found], statement
    condition = next(site for site in found if isinstance(site.expression, ast.BoolOp))
    assert ast.unparse(condition.expression).startswith("member.is_active and (not")


@pytest.mark.parametrize(
    "expression, reason",
    [
        ("(yield member)\n        and member.is_banned\n        and member.warnings < 3", "yield"),
        (
            "(yield from member)\n        and member.is_banned\n        and member.warnings < 3",
            "yield",
        ),
        (
            "await member.load()\n        and member.is_banned\n        and member.warnings < 3",
            "await",
        ),
        (
            "[row async for row in member]\n        and member.is_banned\n        and member.warnings",
            "await",
        ),
        (
            "(seen := member.is_active)\n        and member.is_banned\n        and member.warnings < 3",
            "assignment expression",
        ),
        (
            "super().is_active\n        and member.is_banned\n        and member.warnings < 3",
            "class cell",
        ),
        (
            "__class__.is_active\n        and member.is_banned\n        and member.warnings < 3",
            "class cell",
        ),
        ("1\n        + 2\n        + 3\n        + 4", "constants only"),
        ("member.one\n        or member\n        .two", "too small"),
    ],
)
def test_an_expression_that_cannot_move_is_not_a_candidate(expression: str, reason: str) -> None:
    """Each exclusion of the design, by what the expression holds."""
    source = f"async def f(member):\n    value = (\n        {expression}\n    )\n    return value\n"
    assert kinds(source) == []
    function = ast.parse(source).body[0]
    assert isinstance(function, ast.AsyncFunctionDef)
    assert reason in census(function, 3)


def test_a_single_call_is_not_a_candidate_but_its_arguments_are() -> None:
    """A helper of one call would only forward; what the call is given can still be shared."""
    found = sites_of(f"""
    def f(member, now):
        return record(
            {CONDITION},
            now,
            member,
        )
    """)
    assert [type(site.expression).__name__ for site in found] == ["BoolOp"]


@pytest.mark.parametrize(
    "statement",
    [
        # The callee of a call is looked up, not evaluated for a value.
        f"{CONDITION}(now)",
        # An assignment target and a deletion target are stored, not read.
        "(\n        member\n        .profile\n        .settings\n        .theme\n    ) = now",
        # An annotation is not evaluated in a function.
        f"value: {CONDITION} = now",
        # A field of an f-string is part of its text.
        f'value = f"{{{CONDITION}}}"',
        # A lambda's body runs when the lambda is called.
        f"value = lambda member: {CONDITION}",
        # A nested definition's decorators and defaults are left to it.
        f"def inner(flag={CONDITION}):\n        pass",
        f"@{CONDITION}\n    def inner():\n        pass",
        f"class Inner:\n        flag = {CONDITION}",
    ],
)
def test_an_expression_that_is_not_a_value_where_it_stands_is_not_a_candidate(
    statement: str,
) -> None:
    source = f"def f(member, now):\n    {statement}\n"
    assert "BoolOp" not in kinds(source) and "Attribute" not in kinds(source), statement


def test_a_slice_and_a_starred_argument_are_not_candidates_but_their_parts_are() -> None:
    found = sites_of(f"""
    def f(member, now, rows):
        return rows[
            1:{CONDITION}
        ] + list(*{CONDITION})
    """)
    names = [type(site.expression).__name__ for site in found]
    assert names.count("BoolOp") == 2 and "Slice" not in names and "Starred" not in names
    # ``rows[1:2, i]``: the tuple holds a slice, which no helper can return.
    assert kinds("""
    def f(member, rows):
        return rows[
            1:2,
            member.is_active and member.is_banned and member.warnings,
        ]
    """) == ["Subscript"]


def test_a_type_statement_is_not_entered() -> None:
    """Its value is evaluated when ``__value__`` is first read, not where it stands."""
    if not hasattr(ast, "TypeAlias"):
        pytest.skip("type statements need Python 3.12")
    assert kinds("""
    def f(member):
        type Alias = (
            member.one
            | member.two
            | member.three
        )
    """) == []


def test_the_expression_must_span_the_minimum_lines() -> None:
    source = f"def f(member, now):\n    return {CONDITION}\n"
    assert kinds(source, min_lines=3) == ["BoolOp"]
    assert kinds(source, min_lines=4) == []
    one_line = (
        "def f(member):\n    return member.is_active and not member.is_banned and member.ok\n"
    )
    assert kinds(one_line, min_lines=3) == []
    assert kinds(one_line, min_lines=1) == ["BoolOp"]


def test_the_census_counts_every_expression_that_spans_the_minimum() -> None:
    function = ast.parse(textwrap.dedent(f"""
    def f(member, now):
        first = {CONDITION}
        second = record(
            member,
            now,
        )
        return (
            1,
            2,
        )
    """)).body[0]
    assert isinstance(function, ast.FunctionDef)
    assert census(function, 3) == {"candidate": 1, "single call": 1, "constants only": 1}


# -- Where a candidate stands -------------------------------------------------


@pytest.mark.parametrize(
    "statement, tests_truth",
    [
        (f"if {CONDITION}:\n        pass", True),
        (f"while {CONDITION}:\n        break", True),
        (f"assert {CONDITION}", True),
        (f"value = 1 if {CONDITION} else 2", True),
        (f"value = [m for m in now if {CONDITION}]", True),
        (f"match now:\n        case [m] if {CONDITION}:\n            pass", True),
        (f"if not {CONDITION}:\n        pass", True),
        (f"if now and {CONDITION}:\n        pass", True),
        (f"if now or not {CONDITION}:\n        pass", True),
        (f"if ({CONDITION} if now else member):\n        pass", True),
        (f"value = {CONDITION}", False),
        (f"return {CONDITION}", False),
        (f"value = not {CONDITION}", False),
        (f"value = now and {CONDITION}", False),
        (f"value = {CONDITION} if now else member", False),
        (f"if record({CONDITION}):\n        pass", False),
        (f"if [{CONDITION}]:\n        pass", False),
        (f"if {CONDITION} == now:\n        pass", False),
    ],
)
def test_a_site_records_whether_only_the_truth_of_its_value_is_used(
    statement: str, tests_truth: bool
) -> None:
    """Where the compiler turns ``and``, ``or`` and ``not`` into jumps, and where it keeps the value."""
    source = f"def f(member, now):\n    {statement}\n"
    condition = next(
        site for site in sites_of(source) if ast.unparse(site.expression).startswith("member.is")
    )
    assert condition.tests_truth is tests_truth, statement


TRUTH_PLACES = [
    "if {E}:\n        note('taken')",
    "while {E}:\n        note('taken')\n        break",
    "assert {E}, note('failed')",
    "note(1 if {E} else 2)",
    "note([m for m in (1, 2) if {E}])",
    "match 1:\n        case 1 if {E}:\n            note('taken')",
    "if not {E}:\n        note('taken')",
    "if b and {E}:\n        note('taken')",
    "if b or not {E}:\n        note('taken')",
    "if ({E} if b else a):\n        note('taken')",
]


@pytest.mark.parametrize("place", TRUTH_PLACES)
@pytest.mark.parametrize("a_is_true", [True, False])
@pytest.mark.parametrize("b_is_true", [True, False])
def test_this_python_tests_each_operand_once_where_a_site_only_tests_truth(
    place: str, a_is_true: bool, b_is_true: bool
) -> None:
    """The fact strict mode's exact form rests on, held to the interpreter that runs the tests.

    Where a site ``tests_truth``, ``a and b`` written there and a call that
    returns ``True if a and b else False`` must test the same operands the
    same number of times, in the same order, and take the same way.
    """
    program = textwrap.dedent("""
    events = []

    def note(what):
        events.append(what)

    class Tested:
        def __init__(self, name, truth):
            self.name, self.truth = name, truth

        def __bool__(self):
            events.append("bool " + self.name)
            return self.truth

    def exact(a, b):
        return True if a and b else False

    def written(a, b):
        try:
            PLACE_WRITTEN
        except AssertionError:
            note("raised")

    def called(a, b):
        try:
            PLACE_CALLED
        except AssertionError:
            note("raised")
    """)
    program = program.replace(
        "PLACE_WRITTEN", place.replace("\n    ", "\n        ").format(E="(a and b)")
    )
    program = program.replace(
        "PLACE_CALLED", place.replace("\n    ", "\n        ").format(E="exact(a, b)")
    )
    namespace: Dict[str, object] = {}
    exec(compile(program, "<truth>", "exec"), namespace)  # nosec B102 - the test's own text
    traces: List[Tuple[str, ...]] = []
    for function in ("written", "called"):
        events = namespace["events"]
        assert isinstance(events, list)
        events.clear()
        tested = namespace["Tested"]
        assert callable(tested)
        run = namespace[function]
        assert callable(run)
        run(tested("a", a_is_true), tested("b", b_is_true))
        traces.append(tuple(events))
    assert traces[0] == traces[1] and traces[0]
    # And the engine does classify the place as one that only tests truth.
    source = f"def f(a, b, member, now):\n    {place.format(E=CONDITION)}\n"
    condition = next(
        site for site in sites_of(source) if ast.unparse(site.expression).startswith("member.is")
    )
    assert condition.tests_truth


@pytest.mark.parametrize(
    "expression, decides",
    [
        ("a and b", True),
        ("a or b", True),
        ("not a", True),
        ("a if b else c", True),
        ("a < b < c", True),
        ("a < b", False),
        ("a + b", False),
        ("f(a and b)", False),
        ("[a and b]", False),
        ("-a", False),
    ],
)
def test_which_expressions_a_truth_test_compiles_to_jumps(expression: str, decides: bool) -> None:
    assert truth_decides(ast.parse(expression, mode="eval").body) is decides


def test_a_site_inside_a_comprehension_records_the_targets_bound_and_not_yet_bound() -> None:
    """A clause's target is the comprehension's variable from the first clause on."""
    found = sites_of(f"""
    def f(groups, member, now):
        return [
            member
            for group in groups
            if {CONDITION}
            for member in group
            if {CONDITION}
        ]
    """)
    first, second = [site for site in found if isinstance(site.expression, ast.BoolOp)]
    assert first.comprehension_names == {"group"} and first.unbound_names == {"member"}
    assert second.comprehension_names == {"group", "member"} and not second.unbound_names
    assert second.comprehension_iterables == (("group", "groups"), ("member", "group"))
    # The first iterable is evaluated where the comprehension stands.
    outside = sites_of(f"""
    def f(member, now):
        return [member for member in {CONDITION}]
    """)
    assert not outside[0].comprehension_names and not outside[0].unbound_names


def test_a_site_records_the_statement_a_type_checker_can_be_asked_at() -> None:
    """The first line of its statement, and of the whole ``if`` for an ``elif``."""
    found = sites_of(f"""
    def f(member, now):
        if now:
            pass
        elif {CONDITION}:
            pass
        try:
            pass
        except {CONDITION}:
            value = {CONDITION}
    """)
    assert [site.statement_line for site in found] == [3, 11, 18]
    assert [type(site.anchor).__name__ for site in found] == ["If", "ExceptHandler", "Assign"]


# -- The block that stands for a candidate ------------------------------------


def test_a_candidate_stands_as_a_return_of_the_functions_own_expression() -> None:
    site = sites_of(f"def f(member, now):\n    value = {CONDITION}\n")[0]
    block = standing_block(site)
    assert isinstance(block, ast.Return) and block.value is site.expression
    assert (block.lineno, block.col_offset) == (site.expression.lineno, site.expression.col_offset)
    assert expression_site([block]) is site and is_expression_block([block])
    assert not is_expression_block([ast.parse("return 1").body[0]])
    exact = standing_block(ExpressionSite(**{**site.__dict__, "exact_truth": True}))
    assert ast.unparse(exact).startswith("return True if member.is_active and")
    assert ast.unparse(exact).endswith("else False")


# -- Buckets ------------------------------------------------------------------


def _key(expression: str) -> Tuple[object, ...]:
    return expression_key(ast.parse(expression, mode="eval").body)


def test_the_key_holds_the_root_and_nothing_below_it() -> None:
    """A differing child is what becomes a parameter, so it is not part of the key."""
    assert _key("a.x and b.y and c") == _key("f(a) and [b] and (c or d)")
    assert _key("a and b and c") != _key("a or b or c")
    assert _key("a and b and c") != _key("a and b")
    assert _key("a + b") != _key("a - b")
    assert _key("a < b <= c") != _key("a < b < c")
    assert _key("[a, b]") != _key("(a, b)") and _key("[a, b]") != _key("[a, b, c]")
    assert _key("{a: b, **c}") != _key("{a: b, c: d}")
    assert _key("[a for a in b if a]") != _key("[a for a in b]")
    assert _key("[a for a in b]") == _key("[f(a) for a in g(b)]")
    assert _key("lambda a: a") != _key("lambda a, b: a")
    assert _key("a.b") == _key("c.d")


def test_expression_candidates_share_a_bucket_only_with_their_own_kind() -> None:
    """An expression never pairs with a statement, nor with an expression of another key."""
    function = ast.parse(textwrap.dedent(f"""
    def f(member, now):
        first = {CONDITION}
        second = (
            member.is_active
            or member.is_banned
            or member.warnings < 3
        )
        return {CONDITION}
    """)).body[0]
    assert isinstance(function, ast.FunctionDef)
    first, second, third = [
        expression_signature(standing_block(site)) for site in expression_sites(function, 3)
    ]
    assert first.stmt_seq[0] == "expression"
    assert quick_filter(first, third)
    assert not quick_filter(first, second)
    returned = extract_block_signature([function.body[-1]])
    assert returned.stmt_seq == ("Return",) and not quick_filter(third, returned)


def test_the_engine_enumerates_a_functions_expressions_once(tmp_path: Path) -> None:
    """Enumerated per function, signed for pairing, and left out where the user says so."""
    path = tmp_path / "m.py"
    path.write_text(textwrap.dedent(f"""
    def first(member, now):
        if {CONDITION}:
            return 1
        return 0


    def second(member, now):  # towel: no-extract
        if {CONDITION}:
            return 1
        return 0
    """))
    functions = analyzed_functions([str(path)])
    engine = UnificationRefactorEngine(expression_candidates=True)
    engine._record_function_paths(functions)
    signed = engine._signed_expressions(functions[0].node)
    assert [(span, is_expression_block(block)) for span, block, _ in signed] == [((4, 6), True)]
    assert engine._signed_expressions(functions[0].node) is signed
    assert engine._signed_expressions(functions[1].node) == []
    statements_only = UnificationRefactorEngine(expression_candidates=False)
    statements_only._record_function_paths(functions)
    assert statements_only._signed_expressions(functions[0].node) == []


def test_an_expression_is_told_apart_from_whatever_begins_where_it_does(tmp_path: Path) -> None:
    """The guards remember their verdicts by where a block stands.

    A statement that is one expression begins where the expression does,
    and so does an expression's first operand; the exact truth form of a
    site is another block again.
    """
    path = tmp_path / "m.py"
    path.write_text(
        "def f(member, now):\n"
        "    (member.profile.settings.theme\n"
        "     .name.title.text) and (\n"
        "        member.is_active\n"
        "        and now)\n"
    )
    (function,) = analyzed_functions([str(path)])
    engine = UnificationRefactorEngine(expression_candidates=True, min_lines=2)
    engine._record_function_paths([function])
    whole, operand = [block for _, block, _ in engine._signed_expressions(function.node)][:2]
    statement = function.node.body
    assert (statement[0].lineno, statement[0].col_offset) == (whole[0].lineno, whole[0].col_offset)
    places = [
        engine._block_site(function.node, block)
        for block in (statement, whole, operand, engine._in_exact_truth_form(whole))
    ]
    assert None not in places and len(set(places)) == 4
    assert places[0] is not None and places[0].expression is None
    assert engine._in_exact_truth_form(whole) is engine._in_exact_truth_form(whole)
