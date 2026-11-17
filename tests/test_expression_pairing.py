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

"""Which expression candidates pair, and in which form.

Audit case 44 (engine map E): two expressions pair when they stand in
different functions, share a bucket, and unify with something left in
common. Unlike two runs of statements of the same kinds, most expressions
with one root are unrelated, and an unrelated pair is not similar code a
run should count. Strict mode pairs each site in the form that tests truth
exactly as the site did.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path
from typing import List, Sequence, Tuple

import pytest

from towel.api import UnificationRefactorEngine
from towel.unification.expression_sites import expression_site, is_expression_block
from towel.unification.models import CodeBlockPair
from tests.test_candidate_index import analyzed_functions

CONDITION = """(
        member.is_active
        and not member.is_banned
        and member.email_verified
        and (now - member.created_at).days >= 7
        and member.warnings < 3
    )"""

IN_STATEMENTS = f"""
def can_post(member, now):
    if {CONDITION}:
        return True
    return False


def visible_authors(members, now):
    return sorted(
        member.name
        for member in members
        if {CONDITION}
    )
"""
"""The lane's boundary example ``b``: the condition inside two different statements."""


def pairs_of(
    tmp_path: Path, *sources: str, **options: object
) -> Tuple[UnificationRefactorEngine, List[CodeBlockPair]]:
    paths = []
    for index, source in enumerate(sources):
        path = tmp_path / f"m{index}.py"
        path.write_text(textwrap.dedent(source))
        paths.append(str(path))
    options.setdefault("expression_candidates", True)
    engine = UnificationRefactorEngine(**options)  # type: ignore[arg-type]
    return engine, engine.find_block_pairs(analyzed_functions(paths), progress="none")


def expression_pairs(pairs: Sequence[CodeBlockPair]) -> List[CodeBlockPair]:
    return [pair for pair in pairs if is_expression_block(pair.block1_nodes)]


def test_the_condition_inside_two_different_statements_is_a_pair(tmp_path: Path) -> None:
    """Example ``b`` gave "No duplicates found!" in 1.949: no statements of the two functions pair."""
    _, pairs = pairs_of(tmp_path, IN_STATEMENTS)
    found = expression_pairs(pairs)
    assert [
        (pair.function1_name, pair.block1_range, pair.function2_name, pair.block2_range)
        for pair in found
    ] == [("can_post", (4, 8), "visible_authors", (19, 23))]
    assert is_expression_block(found[0].block2_nodes)
    assert [pair for pair in pairs if not is_expression_block(pair.block1_nodes)] == []
    _, without = pairs_of(tmp_path, IN_STATEMENTS, expression_candidates=False)
    assert without == []


def test_statement_pairs_are_what_they_were(tmp_path: Path) -> None:
    """Expression candidates add pairs; they change no pair of statement blocks, nor their order."""
    source = IN_STATEMENTS + textwrap.dedent("""
    def first(rows):
        total = 0
        for row in rows:
            total += row
        return total


    def second(rows):
        total = 0
        for row in rows:
            total += row
        return total
    """)
    _, pairs = pairs_of(tmp_path, source)
    _, without = pairs_of(tmp_path, source, expression_candidates=False)
    assert without and expression_pairs(pairs)

    def places(
        found: Sequence[CodeBlockPair],
    ) -> List[Tuple[str, Tuple[int, int], str, Tuple[int, int]]]:
        return [
            (pair.function1_name, pair.block1_range, pair.function2_name, pair.block2_range)
            for pair in found
        ]

    statements = [pair for pair in pairs if not is_expression_block(pair.block1_nodes)]
    assert places(statements) == places(without)


def test_expressions_with_one_root_and_nothing_else_in_common_are_no_pair(tmp_path: Path) -> None:
    """Both are an ``and`` of three operands; they share nothing a helper could hold."""
    _, pairs = pairs_of(
        tmp_path,
        """
        def first(order, stock):
            return [
                order.total > stock.limit
                and order.customer.is_active
                and stock.count(order.sku) > 0
            ]


        def second(path, mode):
            return [
                path.exists()
                and mode in ("r", "w")
                and not path.is_dir()
            ]
        """,
    )
    assert expression_pairs(pairs) == []


def test_expressions_that_differ_whole_are_no_pair(tmp_path: Path) -> None:
    """Only the subscript is shared; each whole value would have to be the argument."""
    _, pairs = pairs_of(
        tmp_path,
        """
        def first(table, a, b, c):
            return table[
                a.one + b.two
                + c.three
            ]


        def second(table, a, b, c):
            return table[
                [a.one, b.two,
                 c.three]
            ]
        """,
        min_lines=2,
    )
    assert [
        pair
        for pair in expression_pairs(pairs)
        if type(pair.block1_nodes[0].value).__name__ != "Subscript"  # type: ignore[attr-defined]
    ] == []


TWO_SITES_OF_ONE_FUNCTION = f"""
def only(member, now, other):
    first = {CONDITION}
    member = other
    second = {CONDITION}
    return first, second
"""


def test_two_sites_of_one_function_are_no_pair(tmp_path: Path) -> None:
    """As for statements: a pair is two different functions (a helper's other sites join it later).

    The source starts at column 0: indented and dedented, its condition's
    closing line set the common indentation, and the file did not parse.
    """
    assert textwrap.dedent(TWO_SITES_OF_ONE_FUNCTION) == TWO_SITES_OF_ONE_FUNCTION
    ast.parse(TWO_SITES_OF_ONE_FUNCTION)
    _, pairs = pairs_of(tmp_path, TWO_SITES_OF_ONE_FUNCTION)
    assert expression_pairs(pairs) == []


LONG_FUNCTION = f"""
def only(member, now, items):
    print("start")
    if {CONDITION}:
        print("first")
    total = 0
    for item in items:
        total += item
    print("middle", total)
    count = len(items)
    print("count", count)
    if {CONDITION}:
        label = "second"
        print(label, total)
    return total
"""
"""One function long enough to pair with itself, its condition written twice."""


@pytest.mark.parametrize("strict", [False, True])
def test_two_sites_of_a_function_long_enough_to_pair_with_itself_are_no_pair(
    tmp_path: Path, strict: bool
) -> None:
    """Blocks of statements in one long function pair (lane reach); expressions do not.

    "Expressions are candidates" pairs two expressions only in different
    functions. Integration 4 found that once a long function pairs with itself,
    its two conditions paired too, and strict mode extracted them: a
    combination neither lane designed nor had reviewed.
    """
    _, pairs = pairs_of(tmp_path, LONG_FUNCTION, strict=strict, min_lines=3)
    assert expression_pairs(pairs) == []
    assert any(pair.function1_name == pair.function2_name == "only" for pair in pairs)


def test_expressions_in_different_modules_pair_only_across_modules(tmp_path: Path) -> None:
    one = (
        f"def can_post(member, now):\n    if {CONDITION}:\n        return True\n    return False\n"
    )
    two = f"def may_vote(member, now):\n    return [{CONDITION}]\n"
    _, within = pairs_of(tmp_path, one, two)
    assert expression_pairs(within) == []
    _, across = pairs_of(tmp_path, one, two, cross_module_helpers=True)
    assert len(expression_pairs(across)) == 1 and across[0].is_cross_file


# -- Strict mode's forms ------------------------------------------------------

TESTED = f"def tested(member, now):\n    if {CONDITION}:\n        return 1\n    return 0\n"
ALSO_TESTED = (
    f"def also_tested(members, now):\n    return [m for member in members if {CONDITION}]\n"
)
USED = f"def used(member, now):\n    value = {CONDITION}\n    return value\n"
ALSO_USED = f"def also_used(member, now):\n    return [{CONDITION}]\n"


def _forms(pair: CodeBlockPair) -> Tuple[bool, bool]:
    first, second = expression_site(pair.block1_nodes), expression_site(pair.block2_nodes)
    assert first is not None and second is not None
    return first.exact_truth, second.exact_truth


def test_relaxed_mode_pairs_every_site_as_the_value(tmp_path: Path) -> None:
    """Relaxed mode returns the expression; a truth test may then run twice (the ruling of 2026-11-16)."""
    _, pairs = pairs_of(tmp_path, TESTED + "\n\n" + USED + "\n\n" + ALSO_TESTED, strict=False)
    assert len(expression_pairs(pairs)) == 3
    assert {_forms(pair) for pair in expression_pairs(pairs)} == {(False, False)}


def test_strict_mode_pairs_two_tested_sites_in_the_exact_truth_form(tmp_path: Path) -> None:
    """Both only test the value: ``return True if E else False`` tests each operand once, as they did."""
    _, pairs = pairs_of(tmp_path, TESTED + "\n\n" + ALSO_TESTED, strict=True)
    (pair,) = expression_pairs(pairs)
    assert _forms(pair) == (True, True)
    assert "True if" in __import__("ast").unparse(pair.block1_nodes[0])


def test_strict_mode_pairs_two_used_sites_as_the_value(tmp_path: Path) -> None:
    _, pairs = pairs_of(tmp_path, USED + "\n\n" + ALSO_USED, strict=True)
    (pair,) = expression_pairs(pairs)
    assert _forms(pair) == (False, False)


def test_strict_mode_keeps_a_tested_site_with_a_used_one_in_their_own_forms(
    tmp_path: Path,
) -> None:
    """One helper cannot serve both: the pair is kept in two forms, and declined when it is judged.

    The decline, ``truth_test_repeated``, is held end to end by
    ``tests/test_expression_guards.py``.
    """
    _, pairs = pairs_of(tmp_path, TESTED + "\n\n" + USED, strict=True)
    (pair,) = expression_pairs(pairs)
    assert _forms(pair) == (True, False)


def test_strict_mode_gives_an_expression_no_jump_tests_one_form(tmp_path: Path) -> None:
    """A dict display is evaluated to a value wherever it stands, and tested once by whoever tests it."""
    display = "{\n        'name': member.name,\n        'since': member.created_at,\n        'now': now,\n    }"
    source = (
        f"def tested(member, now):\n    if {display}:\n        return 1\n    return 0\n\n\n"
        f"def used(member, now):\n    value = {display}\n    return value\n"
    )
    _, pairs = pairs_of(tmp_path, source, strict=True)
    (pair,) = expression_pairs(pairs)
    assert _forms(pair) == (False, False)


@pytest.mark.parametrize("strict", [False, True])
def test_expression_pairs_count_against_the_pair_budget(tmp_path: Path, strict: bool) -> None:
    """They are bucketed with the statement blocks, so the budget that bounds those bounds these."""
    _, pairs = pairs_of(
        tmp_path, TESTED + "\n\n" + ALSO_TESTED, strict=strict, max_candidate_pairs=0
    )
    assert len(expression_pairs(pairs)) == 1
