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


import pytest

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
