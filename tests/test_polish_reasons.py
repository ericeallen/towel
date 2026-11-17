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

"""Polish item 2: explain current unification limits rather than obsolete ones."""

import ast
from collections import Counter

import pytest

from towel.reason_sentences import explain, explain_expression
from towel.unification.models import RejectReason
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.exceptions import UncheckedCodeError
from towel.unification.unifier import Unifier
from tests.test_created_object_escapes import _escapes
from tests.test_declined_run_reports import _RefusesEveryCandidate


@pytest.mark.parametrize(
    "left,right",
    [
        ('return f"left {x}"', 'return f"right {x}"'),
        ("return x[1:2]", "return x[2:3]"),
        ("return client.one(x)", "return client.two(x)"),
        ("if ok:\n    a = x; use(a)", "if ok:\n    b = x; use(b)"),
    ],
)
def test_unification_explanation_does_not_list_supported_differences(left: str, right: str) -> None:
    """Polish 2: text, slices, methods and consistent local renames now unify."""
    assert (
        Unifier().unify_blocks([ast.parse(left).body, ast.parse(right).body], [{}, {}]) is not None
    )
    for explanation in (
        explain("unification_failed"),
        explain_expression(RejectReason.UNIFICATION_FAILED),
    ):
        wording = explanation.sentence + (explanation.remedy or "")
        for obsolete in (
            "f-string",
            "named differently",
            "method name",
            "slice",
            "same local names",
            "format.",
        ):
            assert obsolete not in wording


@pytest.mark.parametrize(
    "left,right",
    [
        ("return x + 1", "return x - 1"),
        ("if ok:\n    a=1; a=2; use(a)", "if ok:\n    b=1; c=2; use(c)"),
    ],
)
def test_unification_explanation_names_remaining_limits(left: str, right: str) -> None:
    """Polish 2: differing operators and inconsistent bindings really still fail."""
    assert Unifier().unify_blocks([ast.parse(left).body, ast.parse(right).body], [{}, {}]) is None
    sentence = explain("unification_failed").sentence
    assert "operator" in sentence and "bindings" in sentence


def test_forwarded_callee_explains_lookup_timing() -> None:
    """Polish 2: different method names alone no longer cause this refusal."""
    explanation = explain("forwarded_callee")
    assert "lookup" in explanation.sentence
    assert "client.get_profile" not in explanation.sentence


def test_similarity_explanation_covers_statement_size() -> None:
    """Polish 2: the size guard can refuse a pair more than 60% alike."""
    left, right = (ast.parse(source).body for source in ("return a + b", "return a + b + c"))
    first, second = (Counter(type(node) for node in ast.walk(block[0])) for block in (left, right))
    similarity = sum((first & second).values()) / max(sum(first.values()), sum(second.values()))
    assert similarity > 0.6
    assert not UnificationRefactorEngine()._are_structurally_similar(left, right)
    for explanation in (
        explain("not_structurally_similar"),
        explain_expression(RejectReason.NOT_STRUCTURALLY_SIMILAR),
    ):
        assert "size" in explanation.sentence
        assert "less than 60%" not in explanation.sentence


def test_forwarding_explanation_covers_multiple_generated_calls() -> None:
    """Polish 2: several calls of earlier helpers still share no new logic."""
    helper = ast.parse(
        "def helper(x):\n    a = __extracted_func_0(x)\n"
        "    b = __extracted_func_1(a)\n    return b\n"
    ).body[0]
    assert isinstance(helper, ast.FunctionDef)
    assert (
        UnificationRefactorEngine()._trivial_helper_reason(helper)
        == RejectReason.TRIVIAL_FORWARDING_HELPER
    )
    for explanation in (
        explain("trivial_forwarding_helper"),
        explain_expression(RejectReason.TRIVIAL_FORWARDING_HELPER),
    ):
        assert "single call" not in explanation.sentence
        assert "earlier helpers" in explanation.sentence


def test_escape_explanation_does_not_assert_an_object_really_escapes() -> None:
    """Polish 2: an unused local class is refused conservatively, without escaping."""
    assert _escapes("class Local:\n    pass")
    for explanation in (
        explain("created_object_escapes"),
        explain_expression(RejectReason.CREATED_OBJECT_ESCAPES),
    ):
        assert "cannot prove" in explanation.sentence
        assert "that is used after" not in explanation.sentence


@pytest.mark.looks_nowhere
def test_unchecked_explanation_does_not_assert_platform_unreachability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Polish 2: a missing probe leaves checker coverage unknown, not platform-unreachable."""
    monkeypatch.setattr("towel.unification.annotation_wiring.probe_plan", lambda source: None)
    oracle = _RefusesEveryCandidate()
    with pytest.raises(UncheckedCodeError, match="no probe can be placed"):
        UnificationRefactorEngine()._refuse_what_the_checker_does_not_look_at(
            oracle, {"example.py": "pass\n"}
        )
    assert oracle.checks == 0
    sentence = explain(
        "not verifiable: the type checker does not look at the code it changes"
    ).sentence
    assert "could not establish" in sentence
    assert "takes this code to be unreachable" not in sentence
