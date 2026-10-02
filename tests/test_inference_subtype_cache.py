# Copyright 2025-2026 Eric Allen
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

"""Subtype evidence belongs to one inference, including its unsuccessful probes."""

from __future__ import annotations

import ast
import gc
from typing import Mapping, Sequence
import weakref

import pytest

from towel.canonical_ast import canonical_dump
from towel.type_inference import CheckResult, RevealKey, RevealRequest, Subtyping
from towel.unification.annotations import (
    ApplySite,
    _inference_subtypes,
    infer_missing_annotations,
    oracle_subtypes,
)
from towel.unification.bounded_cache import memoization_disabled

YES, NO, UNKNOWN = Subtyping.YES, Subtyping.NO, Subtyping.UNKNOWN


class _RecordingOracle:
    """A deterministic relation with controllable unavailable or untyped evidence."""

    def __init__(self, revealed: str = "int | bool") -> None:
        self.revealed = revealed
        self.verdicts = {("bool", "int"): YES, ("int", "float"): YES}
        self.untyped: set[str] = set()
        self.failure: str | None = None
        self.batches: list[tuple[tuple[str, str], ...]] = []

    def reveal(self, requests: Sequence[RevealRequest]) -> Mapping[RevealKey, str]:
        return {
            (request.file_path, request.line, index): self.revealed
            for request in requests
            for index, _expression in enumerate(request.expressions)
        }

    def is_subtype(
        self, file_path: str, source: str, pairs: Sequence[tuple[str, str]]
    ) -> Sequence[Subtyping]:
        assert "class _TowelUnrelatedType" in source
        self.batches.append(tuple(pairs))
        if self.failure == "unanswered":
            return []
        if self.failure == "unresolved":
            return [UNKNOWN] * len(pairs)
        if self.failure == "unchecked":
            return [YES] * len(pairs)
        return [
            (
                YES
                if narrow == wide or narrow in self.untyped or wide in self.untyped
                else self.verdicts.get((narrow, wide), NO)
            )
            for narrow, wide in pairs
        ]

    def check(self, file_path: str, source: str) -> CheckResult:
        raise AssertionError("annotation inference does not check projects")

    def check_project(
        self, sources: Mapping[str, str], *, excluded_paths: Sequence[str] = ()
    ) -> CheckResult:
        raise AssertionError("annotation inference does not check projects")

    def close(self) -> None:
        pass


def _pairs(*pairs: tuple[str, str]) -> list[tuple[ast.expr, ast.expr]]:
    return [
        (ast.parse(narrow, mode="eval").body, ast.parse(wide, mode="eval").body)
        for narrow, wide in pairs
    ]


def _infer(oracle: _RecordingOracle) -> list[str]:
    union = oracle.revealed
    source = (
        "from other import Narrow, Wide\n"
        f"def caller(left: {union}, right: {union}) -> None:\n"
        "    generated(left, right)\n"
    )
    caller = ast.parse(source).body[-1]
    assert isinstance(caller, ast.FunctionDef)
    statement = caller.body[0]
    assert isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)
    helper = ast.parse(f"def generated(left: {union}, right: {union}) -> None:\n    pass\n").body[0]
    assert isinstance(helper, ast.FunctionDef)
    before = canonical_dump(helper)
    inferred = infer_missing_annotations(
        helper,
        [ApplySite("/project/m.py", source, 3, 3, "    ", statement, statement.value)],
        "/project/m.py",
        [],
        oracle,
    )
    assert canonical_dump(helper) == before
    annotations = [parameter.annotation for parameter in inferred.helper.args.args]
    assert all(annotation is not None for annotation in annotations)
    return [
        (
            annotation.value
            if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str)
            else ast.unparse(annotation)
        )
        for annotation in annotations
        if annotation is not None
    ]


def test_repeated_parameter_unions_share_one_checked_relation() -> None:
    oracle = _RecordingOracle()
    assert _infer(oracle) == ["int", "int"]
    assert len(oracle.batches) == 1
    assert oracle.batches[0][:2] == (("int", "bool"), ("bool", "int"))


def test_later_inference_reads_changed_dependencies_with_identical_host_text() -> None:
    oracle = _RecordingOracle("Narrow | Wide")
    oracle.verdicts[("Narrow", "Wide")] = YES
    assert _infer(oracle) == ["Wide", "Wide"]
    # The imported class hierarchy changes; the host and its requests do not.
    oracle.verdicts[("Narrow", "Wide")] = NO
    assert _infer(oracle) == ["Narrow | Wide", "Narrow | Wide"]
    assert len(oracle.batches) == 2


def test_public_relation_still_observes_changes_between_uses() -> None:
    oracle = _RecordingOracle()
    relation = oracle_subtypes(oracle, "/project/m.py", "")
    assert relation(_pairs(("bool", "int"))) == [YES]
    oracle.verdicts[("bool", "int")] = NO
    assert relation(_pairs(("bool", "int"))) == [NO]
    assert len(oracle.batches) == 2


def test_batch_deduplicates_questions_and_restores_requested_order() -> None:
    oracle = _RecordingOracle()
    relation = _inference_subtypes(oracle, "/project/m.py", "")
    assert relation(_pairs(("bool", "int"), ("int", "bool"), ("bool", "int"))) == [
        YES,
        NO,
        YES,
    ]
    assert oracle.batches[0][:2] == (("bool", "int"), ("int", "bool"))
    assert oracle.batches[0].count(("bool", "int")) == 1
    assert relation(_pairs(("int", "bool"), ("bool", "int"))) == [NO, YES]
    assert len(oracle.batches) == 1


def test_disabled_memoization_bypasses_existing_values_and_retains_no_answers() -> None:
    oracle = _RecordingOracle()
    relation = _inference_subtypes(oracle, "/project/m.py", "")
    assert relation(_pairs(("bool", "int"))) == [YES]
    oracle.verdicts[("bool", "int")] = NO
    with memoization_disabled():
        assert relation(_pairs(("bool", "int"), ("int", "float"))) == [NO, YES]
        assert relation(_pairs(("bool", "int"), ("int", "float"))) == [NO, YES]
    assert len(oracle.batches) == 3
    # Disabled calls neither replace existing evidence nor populate new keys.
    assert relation(_pairs(("bool", "int"))) == [YES]
    assert len(oracle.batches) == 3
    oracle.verdicts[("int", "float")] = NO
    assert relation(_pairs(("int", "float"))) == [NO]
    assert len(oracle.batches) == 4


@pytest.mark.parametrize("failure", ["unanswered", "unresolved", "unchecked"])
def test_unknown_answers_are_retried(failure: str) -> None:
    oracle = _RecordingOracle()
    relation = _inference_subtypes(oracle, "/project/m.py", "")
    oracle.failure = failure
    assert relation(_pairs(("bool", "int"))) == [UNKNOWN]
    oracle.failure = None
    assert relation(_pairs(("bool", "int"))) == [YES]
    assert len(oracle.batches) == 2
    assert relation(_pairs(("bool", "int"))) == [YES]
    assert len(oracle.batches) == 2


def test_a_later_batch_requires_its_own_checked_sentinel() -> None:
    oracle = _RecordingOracle()
    relation = _inference_subtypes(oracle, "/project/m.py", "")
    assert relation(_pairs(("bool", "int"))) == [YES]
    oracle.failure = "unchecked"
    assert relation(_pairs(("int", "float"))) == [UNKNOWN]
    assert oracle.batches[-1][-1] == ("int", "_TowelUnrelatedType")
    oracle.failure = None
    assert relation(_pairs(("int", "float"))) == [YES]
    assert len(oracle.batches) == 3


def test_any_and_untyped_bases_never_supply_definite_cached_evidence() -> None:
    oracle = _RecordingOracle()
    relation = _inference_subtypes(oracle, "/project/m.py", "")
    assert relation(_pairs(("list[Any]", "list[int]"))) == [UNKNOWN]
    assert not oracle.batches
    oracle.untyped.add("Base")
    assert relation(_pairs(("Base", "int"))) == [UNKNOWN]
    oracle.untyped.clear()
    assert relation(_pairs(("Base", "int"))) == [NO]
    assert len(oracle.batches) == 2


def test_cached_verdicts_do_not_retain_or_depend_on_input_trees() -> None:
    oracle = _RecordingOracle()
    relation = _inference_subtypes(oracle, "/project/m.py", "")
    pairs = _pairs(("bool", "int"))
    references = [weakref.ref(node) for node in pairs[0]]
    assert relation(pairs) == [YES]
    del pairs
    gc.collect()
    assert all(reference() is None for reference in references)
    assert relation(_pairs(("bool", "int"))) == [YES]
    assert len(oracle.batches) == 1
