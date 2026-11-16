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

"""What a run says of an expression it left alone, and what it counts.

Audit case 44 made expressions candidates; the audit's finding 3 ("why was
my code left alone?") asks that every place left alone has a sentence a
user can act on. A declined pair of expressions is counted apart from the
pairs of statement blocks, under the same reason code, with a sentence
that speaks of an expression; only expressions that share enough for a
helper are a place with similar code at all.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from towel.api import UnificationRefactorEngine
from towel.cli_reporting import reason_lines
from towel.reason_sentences import (
    EXPRESSION_REASONS,
    PAIR_REASONS,
    explain,
    explain_expression,
    reason_code,
    without_subject,
)
from towel.unification.fixed_point import RunReport
from towel.unification.models import RejectReason, expression_count
from tests.test_cli_integration import invoke

UNTYPED = ["--no-types", "--no-format", "--progress", "none"]

TESTED_AND_USED = """
def tested(member, now):
    if (
        member.is_active
        and not member.is_banned
        and member.email_verified
        and member.warnings < 3
    ):
        return 1
    return 0


def used(member, now):
    value = (
        member.is_active
        and not member.is_banned
        and member.email_verified
        and member.warnings < 3
    )
    return value
"""


@pytest.mark.parametrize("reason", sorted(RejectReason))
def test_every_reason_has_a_sentence_about_an_expression(reason: RejectReason) -> None:
    """Each reason reads of an expression what it reads of a block, in a plain complete sentence."""
    explanation = explain_expression(reason)
    assert explanation.sentence.endswith(".") and explanation.sentence[0].isupper()
    for text in (explanation.sentence, explanation.remedy or ""):
        assert "block" not in text.lower(), text
    assert explain(expression_count(str(reason))) == explanation
    # A sentence written for expressions replaces the reworded one; nothing else changes.
    if reason not in EXPRESSION_REASONS and "lock" not in PAIR_REASONS[reason].sentence:
        assert explanation == PAIR_REASONS[reason]


def test_the_new_reason_says_what_strict_mode_cannot_do() -> None:
    explanation = explain(str(RejectReason.TRUTH_TEST_REPEATED))
    assert "--strict" in explanation.sentence and "twice" in explanation.sentence
    assert explanation.remedy == "Without --strict the expression is extracted."


def test_an_expression_under_a_handler_is_not_said_to_assign_a_variable() -> None:
    """Review of the ``expressions`` lane, defect 3: strict mode's refusal, said of an expression."""
    explanation = explain(expression_count(str(RejectReason.OWNED_BINDING_EXCEPTION_BOUNDARY)))
    assert "--strict" in explanation.sentence and "handler" in explanation.sentence
    assert "assigns a variable" not in explanation.sentence
    assert explanation.remedy == "Without --strict the expression is extracted."


def test_an_expression_count_keeps_its_reason_code_and_what_it_names() -> None:
    counted = expression_count("builtin_argument[len]")
    assert reason_code(counted) == "builtin_argument"
    assert without_subject(counted) == expression_count("builtin_argument")
    assert reason_code("builtin_argument[len]") == without_subject("builtin_argument[len]")
    explanation = explain(counted)
    assert explanation.sentence.endswith("(Here: len.)")
    assert "one expression uses a variable named like a builtin" in explanation.sentence
    assert explain(expression_count("other")).sentence == explain("other").sentence


def test_expressions_and_statements_declined_for_one_reason_are_two_lines() -> None:
    """One code, two sentences: the count of each kind stands beside its own wording."""
    report = RunReport(
        {
            "not_structurally_similar": 2,
            expression_count("not_structurally_similar"): 3,
            expression_count("builtin_argument[len]"): 1,
        },
        {},
    )
    lines = reason_lines(report)
    assert lines[2:] == [
        "  3  The expressions have the same outline but are less than 60% alike in detail."
        " [not_structurally_similar]",
        "  2  The blocks have the same kinds of statements but are less than 60% alike in detail."
        " [not_structurally_similar]",
        "  1  The call would have to pass a builtin such as len to the helper, or one expression"
        " uses a variable named like a builtin that the other uses as the builtin."
        " (Here: len.) [builtin_argument]",
        "     What you can do: Rename the variable that shadows the builtin.",
    ]


# -- End to end -----------------------------------------------------------------


def test_the_engine_counts_a_declined_expression_pair_where_the_expressions_stand(
    tmp_path: Path,
) -> None:
    path = tmp_path / "m.py"
    path.write_text(TESTED_AND_USED)
    engine = UnificationRefactorEngine(strict=True)
    assert engine.analyze_file(str(path)) == []
    assert engine.declined_pairs == {expression_count("truth_test_repeated"): 1}
    (place,) = engine.last_left_alone
    assert place.reason == expression_count("truth_test_repeated")
    assert [(block.first_line, block.last_line) for block in place.blocks] == [(4, 7), (15, 18)]
    assert engine.last_pair_tally.candidate_pairs == 1


def test_a_run_explains_an_expression_it_left_alone(tmp_path: Path) -> None:
    """The sentence, the code, what to do, and where: the lines of the two expressions."""
    source = tmp_path / "src"
    source.mkdir()
    (source / "m.py").write_text(TESTED_AND_USED)
    result = invoke(["preview", str(source), "--strict", *UNTYPED])
    assert result.status == 0, result.stderr
    assert "Found 1 place with similar code and extracted none." in result.stdout
    assert (
        "  1  With --strict: the expression decides an if or while at one place and gives a"
        " value at another" in result.stdout
    )
    assert "[truth_test_repeated]" in result.stdout
    assert "What you can do: Without --strict the expression is extracted." in result.stdout
    assert "  m.py:4-7 and m.py:15-18: With --strict: the expression decides" in result.stdout


def test_expressions_that_share_only_their_outline_are_not_similar_code(tmp_path: Path) -> None:
    """Two unrelated conditions of three operands: nothing is counted, nothing is reported."""
    source = tmp_path / "src"
    source.mkdir()
    (source / "m.py").write_text(textwrap.dedent("""
            def ships(order, stock):
                if (
                    order.total > stock.limit
                    and order.customer.is_active
                    and stock.count(order.sku) > 0
                ):
                    stock.reserve(order)


            def readable(path, mode):
                found = (
                    path.exists()
                    and mode in ("r", "w")
                    and not path.is_dir()
                )
                return found
            """))
    result = invoke(["preview", str(source), *UNTYPED])
    assert result.status == 0, result.stderr
    assert "No repeated code found in 1 file." in result.stdout
    engine = UnificationRefactorEngine()
    assert engine.analyze_file(str(source / "m.py")) == []
    assert [reason for reason in engine.declined_pairs if reason.startswith("expression ")] == []
