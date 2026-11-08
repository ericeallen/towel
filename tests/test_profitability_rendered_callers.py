# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Usefulness counts the callers that publication actually writes."""

from __future__ import annotations

import ast
import copy
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from towel.canonical_ast import canonical_dump
from towel.unification import materialize, pair_evaluation
from towel.unification.models import (
    MethodKind,
    RefactoringProposal,
    Replacement,
    is_generated_helper_name,
)
from towel.unification.profitability import _syntax_cost, worthwhile_helper
from towel.unification.refactor_engine import UnificationRefactorEngine


def _normalized(node: ast.AST) -> str:
    result = copy.deepcopy(node)
    for child in ast.walk(result):
        if isinstance(child, ast.Name) and is_generated_helper_name(child.id):
            child.id = "helper"
        elif isinstance(child, ast.Attribute) and is_generated_helper_name(child.attr):
            child.attr = "helper"
    if not isinstance(result, ast.Module):
        assert isinstance(result, ast.stmt)
        result = ast.Module(body=[result], type_ignores=[])
    return canonical_dump(result)


def _verify_counted_callers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
    *,
    manual_method: MethodKind | None = None,
) -> tuple[UnificationRefactorEngine, RefactoringProposal, str]:
    path = tmp_path / "program.py"
    path.write_text(source)
    analysis: dict[tuple[tuple[int, int], ...], list[ast.AST]] = {}
    final: list[list[ast.AST]] = []

    def counted_analysis(
        helper: ast.FunctionDef,
        replacements: Sequence[Replacement],
        callers: Sequence[ast.AST] | None = None,
    ) -> bool:
        assert callers is not None
        analysis[tuple(r.line_range for r in replacements)] = copy.deepcopy(list(callers))
        return worthwhile_helper(helper, replacements, callers)

    def counted_final(
        helper: ast.FunctionDef,
        replacements: Sequence[Replacement],
        callers: Sequence[ast.AST] | None = None,
    ) -> bool:
        assert callers is not None
        final.append(copy.deepcopy(list(callers)))
        return worthwhile_helper(helper, replacements, callers)

    monkeypatch.setattr(pair_evaluation, "worthwhile_helper", counted_analysis)
    monkeypatch.setattr(materialize, "worthwhile_helper", counted_final)
    engine = UnificationRefactorEngine(annotate_helpers=False)
    proposals = engine.analyze_files([str(path)], progress="none")
    assert len(proposals) == 1
    proposal = proposals[0]
    if manual_method is not None:
        # Public proposals also support an explicitly selected method host.
        proposal = replace(
            proposal,
            insert_into_class="C",
            method_kind=manual_method,
            replacements=[replace(r, method_kind=manual_method) for r in proposal.replacements],
        )
    before = (
        canonical_dump(proposal.extracted_function),
        [canonical_dump(r.node) for r in proposal.replacements],
    )
    predicted = engine._rendered_call_sites(proposal, reserve_ownership_holder=True)
    output = engine.apply_refactoring(str(path), proposal)
    assert before == (
        canonical_dump(proposal.extracted_function),
        [canonical_dump(r.node) for r in proposal.replacements],
    )
    assert len(final) == 1
    if manual_method is None:
        observed = analysis[tuple(r.line_range for r in proposal.replacements)]
        assert [_normalized(node) for node in observed] == [_normalized(node) for node in predicted]
    assert [_normalized(node) for node in final[0]] == [_normalized(node) for node in predicted]
    written = [
        ast.Module(body=function.body, type_ignores=[])
        for function in ast.walk(ast.parse(output))
        if isinstance(function, ast.FunctionDef) and function.name in {"first", "second"}
    ]
    assert len(written) == 2
    assert sorted(_normalized(node) for node in final[0]) == sorted(
        _normalized(node) for node in written
    )
    assert _syntax_cost(final[0]) == _syntax_cost(written)
    assert path.read_text() == source
    return engine, proposal, output


@pytest.mark.parametrize(
    "kind,receiver_used,manual_host",
    [
        ("instance", True, False),
        ("classmethod", True, False),
        ("instance", False, True),
        ("classmethod", False, True),
        ("staticmethod", False, False),
        ("staticmethod", False, True),
    ],
)
def test_method_dispatch_cost_matches_the_written_callers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: MethodKind,
    receiver_used: bool,
    manual_host: bool,
) -> None:
    receiver = "cls" if kind == "classmethod" else "self"
    parameters = receiver if receiver_used else f"{receiver}, item"
    if kind == "staticmethod":
        parameters = "item"
    operand = receiver if receiver_used else "item"
    attribute = "values"
    decorator = "" if kind == "instance" else f"    @{kind}\n"
    source = "class C:\n    values = []\n" + "".join(
        decorator
        + f"    def {name}({parameters}):\n"
        + "".join(
            f"        {operand}.{attribute}.append({index if index else offset})\n"
            for index in range(12)
        )
        for name, offset in (("first", 0), ("second", 100))
    )
    engine, proposal, output = _verify_counted_callers(
        tmp_path, monkeypatch, source, manual_method=kind if manual_host else None
    )
    naming = engine._helper_naming(proposal)
    if kind != "staticmethod":
        assert proposal.insert_into_class == "C"
        assert naming.receiver_parameter_index == (1 if receiver_used else None)
    elif manual_host:
        assert proposal.insert_into_class == "C"
    else:
        assert proposal.insert_into_class is None
    if manual_host:
        counted = engine._rendered_call_sites(proposal, reserve_ownership_holder=True)
        assert _syntax_cost(counted) == _syntax_cost([r.node for r in proposal.replacements]) + 2
    observations: list[object] = []
    for program in (source, output):
        namespace: dict[str, object] = {}
        exec(program, namespace)
        cls = namespace["C"]
        assert callable(cls)
        instance = cls()
        for name in ("first", "second"):
            method = getattr(instance, name)
            method() if receiver_used else method(instance)
        observations.append(getattr(instance, attribute))
    assert observations[0] == observations[1] == [*range(12), 100, *range(1, 12)]


def test_mixed_ownership_cost_includes_the_unowned_callers_none_argument(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = (
        "    a = 1\n"
        + "".join(f"    {chr(98+i)} = {chr(97+i)} + {i+2}\n" for i in range(10))
        + "    return k\n"
    )
    source = "def first(unused):\n" + body + "\ndef second():\n" + body
    engine, proposal, output = _verify_counted_callers(tmp_path, monkeypatch, source)
    assert [r.argument_handoff is not None for r in proposal.replacements] == [True, False]
    callers = engine._rendered_call_sites(proposal, reserve_ownership_holder=True)
    second = next(
        node
        for node in ast.parse(output).body
        if isinstance(node, ast.FunctionDef) and node.name == "second"
    )
    assert ast.unparse(second.body[-1]).endswith("(None)")
    assert _syntax_cost([callers[1]]) == _syntax_cost([proposal.replacements[1].node]) + 1
    for program in (source, output):
        namespace: dict[str, object] = {}
        exec(program, namespace)
        first, second_function = namespace["first"], namespace["second"]
        assert callable(first) and callable(second_function)
        assert first(object()) == second_function() == 66


@pytest.mark.parametrize("kind", ["instance", "classmethod"])
def test_owned_method_dispatch_counts_the_reserved_and_appended_holder_identically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: MethodKind,
) -> None:
    receiver = "cls" if kind == "classmethod" else "self"
    decorator = "    @classmethod\n" if kind == "classmethod" else ""
    body = "".join(f"        {chr(98+i)} = {chr(97+i)} + {i+2}\n" for i in range(10))
    source = "class C:\n    base = 1\n" + "".join(
        decorator
        + f"    def {name}({receiver}):\n"
        + f"        a = {receiver}.base + {offset}\n"
        + body
        + "        return k\n"
        for name, offset in (("first", 1), ("second", 2))
    )
    engine, proposal, output = _verify_counted_callers(tmp_path, monkeypatch, source)
    assert proposal.insert_into_class == "C"
    assert all(replacement.argument_handoff is not None for replacement in proposal.replacements)
    assert engine._helper_naming(proposal, reserve_ownership_holder=True).parameter_count == (
        engine._helper_naming(proposal).parameter_count + 1
    )
    for program in (source, output):
        namespace: dict[str, object] = {}
        exec(program, namespace)
        cls = namespace["C"]
        assert callable(cls)
        instance = cls()
        assert instance.first() == 67
        assert instance.second() == 68
