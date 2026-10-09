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

"""Source ownership, immutable storage and bounded IF binding certificates."""

from __future__ import annotations

import ast
import copy
import gc
import pickle
import weakref
from dataclasses import FrozenInstanceError, replace
from textwrap import indent
from typing import Optional, Sequence, cast
from pathlib import Path
from unittest.mock import patch

import pytest

from towel.unification.if_binding_transfer import (
    IfBindingTransfer,
    IfTransferSite,
    certify_if_binding_transfer,
    verify_if_binding_transfer,
)
from towel.unification import if_binding_transfer
from towel.unification.models import source_digest_of
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.semantic_safety import nested_bindings_escape


def _parts(source: str) -> tuple[ast.Module, ast.FunctionDef, list[ast.stmt]]:
    module = ast.parse(source)
    function = next(node for node in module.body if isinstance(node, ast.FunctionDef))
    branch = next(node for node in function.body if isinstance(node, ast.If))
    return module, function, branch.body


def _certify(
    source: str,
    module: ast.Module,
    function: ast.FunctionDef,
    nodes: Sequence[ast.stmt],
    outputs: tuple[str, ...] = ("answer",),
) -> Optional[IfBindingTransfer]:
    site = IfTransferSite(
        source_digest_of(source),
        (function.lineno, function.col_offset),
        (nodes[0].lineno, nodes[0].col_offset, len(nodes)),
    )
    return certify_if_binding_transfer(source, module, function, nodes, site, outputs)


SIMPLE = "answer = 999\ndef f(flag):\n    if flag:\n        subtotal = 20\n        doubled = subtotal * 2\n        answer = doubled + 3\n    return answer\n"


@pytest.mark.parametrize(
    "body",
    [
        "subtotal = 20\ndoubled = subtotal * 2\nanswer = doubled + 3",
        "if active:\n    subtotal = 20\nelse:\n    subtotal = 21\ndoubled = subtotal * 2\nanswer = doubled + 3",
        "answer: int = 3",
        "answer = 3\nobserve(answer)",
    ],
)
def test_fresh_outputs_certify_without_mutating_source(body: str) -> None:
    source = "def f(flag):\n    if flag:\n" + indent(body, "        ") + "\n    return answer\n"
    module, function, nodes = _parts(source)
    before = ast.dump(module, include_attributes=True)
    certificate = _certify(source, module, function, nodes)
    assert certificate is not None
    assert "answer" in certificate.normal_exit_bound
    assert verify_if_binding_transfer(certificate, source, module)
    assert ast.dump(module, include_attributes=True) == before
    # The old guard remains conservative; only the source certificate admits this path.
    assert nested_bindings_escape(function, nodes)


@pytest.mark.parametrize("field", ["body", "orelse"])
def test_nested_if_and_alternative_suites_retain_exact_path(field: str) -> None:
    source = "def f(flag, active):\n    if flag:\n        if active:\n            answer = 3\n        else:\n            answer = 4\n    return answer\n"
    module, function, nodes = _parts(source)
    inner = nodes[0]
    assert isinstance(inner, ast.If)
    selected = inner.body if field == "body" else inner.orelse
    # The other arm initializes the same local, hence it is not fresh for this smaller slice.
    assert _certify(source, module, function, selected) is None
    certificate = _certify(source, module, function, nodes)
    assert certificate is not None
    assert certificate.suite_path == (("body", 0),)
    assert verify_if_binding_transfer(certificate, source, ast.parse(source))


def test_nested_if_suite_and_outer_alternative_have_distinct_paths() -> None:
    source = "def f(flag, active):\n    if flag:\n        if active:\n            answer = 3\n    return answer\n"
    module, function, nodes = _parts(source)
    inner = nodes[0]
    assert isinstance(inner, ast.If)
    certificate = _certify(source, module, function, inner.body)
    assert certificate is not None
    assert certificate.suite_path == (("body", 0), ("body", 0))
    assert verify_if_binding_transfer(certificate, source, ast.parse(source))
    source = "def f(flag):\n    if flag:\n        observe()\n    else:\n        answer = 3\n    return answer\n"
    module, function, _ = _parts(source)
    outer = function.body[0]
    assert isinstance(outer, ast.If)
    certificate = _certify(source, module, function, outer.orelse)
    assert certificate is not None
    assert certificate.suite_field == "orelse"
    assert verify_if_binding_transfer(certificate, source, ast.parse(source))


@pytest.mark.parametrize(
    "body",
    [
        "answer = answer + 1",
        "observe(answer)\nanswer = 3",
        "answer = 3\nanswer = 4",
        "answer = 3\nanswer += 1",
        "answer = 3\ndel answer",
        "if active:\n    answer = 3",
        "if active:\n    answer = 3\nanswer = 4",
        "answer: int",
        "answer = 3\nreturn answer",
        "answer = 3\nraise RuntimeError()",
        "answer = 3\nassert answer",
        "while active:\n    answer = 3",
        "for item in items:\n    answer = item",
        "with manager:\n    answer = 3",
        "try:\n    answer = 3\nfinally:\n    observe()",
        "answer = 3\nf = lambda: answer",
        "answer = 3\nvalues = [answer for item in items]",
        "global answer\nanswer = 3",
        "answer = (temporary := 3)",
        "answer = 3\ndef capture():\n    return answer",
        "obj.answer = 3",
        "answer, other = values",
    ],
)
def test_unsupported_or_nonfresh_flow_is_refused(body: str) -> None:
    source = "def f(flag):\n    if flag:\n" + indent(body, "        ") + "\n    return answer\n"
    module, function, nodes = _parts(source)
    assert _certify(source, module, function, nodes) is None


@pytest.mark.parametrize(
    "source",
    [
        "def f(flag, answer):\n    if flag:\n        answer = 3\n    return answer\n",
        "def f(flag):\n    answer = 2\n    if flag:\n        answer = 3\n    return answer\n",
        "def f(flag):\n    observe(answer)\n    if flag:\n        answer = 3\n    return answer\n",
        "def f(flag):\n    if flag:\n        answer = 3\n    answer = 4\n    return answer\n",
        "def f(flag):\n    if flag:\n        answer = 3\n    del answer\n",
        "def f(flag):\n    if flag:\n        answer = 3\n    def capture():\n        return answer\n    return capture\n",
        "def f(flag):\n    global answer\n    if flag:\n        answer = 3\n    return answer\n",
    ],
)
def test_function_context_must_establish_fresh_local_ownership(source: str) -> None:
    module, function, nodes = _parts(source)
    assert _certify(source, module, function, nodes) is None


@pytest.mark.parametrize(
    "wrapper",
    ["while flag:", "for flag in flags:", "with manager:", "try:", "match flag:\n    case True:"],
)
def test_if_below_unsupported_ancestry_does_not_certify(wrapper: str) -> None:
    inner = "if active:\n    answer = 3"
    if wrapper.startswith("match"):
        body = wrapper + "\n" + indent(inner, "        ")
    else:
        body = wrapper + "\n" + indent(inner, "    ")
    if wrapper == "try:":
        body += "\nfinally:\n    observe()"
    source = "def f(flag):\n" + indent(body, "    ") + "\n    return answer\n"
    module = ast.parse(source)
    function = cast(ast.FunctionDef, module.body[0])
    branch = next(node for node in ast.walk(function) if isinstance(node, ast.If))
    assert _certify(source, module, function, branch.body) is None


def test_ordered_outputs_and_source_owned_contiguous_slice_are_exact() -> None:
    module, function, nodes = _parts(SIMPLE)
    certificate = _certify(SIMPLE, module, function, nodes, ("doubled", "answer"))
    assert certificate is not None
    assert certificate.initialized_names == frozenset(("subtotal", "doubled", "answer"))
    assert certificate.returned_names == ("doubled", "answer")
    assert _certify(SIMPLE, module, function, nodes, ("answer", "answer")) is None
    assert _certify(SIMPLE, module, function, nodes, ("unknown",)) is None
    assert _certify(SIMPLE, module, function, [nodes[0], nodes[2]]) is None
    assert _certify(SIMPLE, module, function, copy.deepcopy(nodes)) is None
    assert _certify(SIMPLE, module, copy.deepcopy(function), nodes) is None
    assert _certify(SIMPLE, module, function, function.body) is None


def test_source_digest_tree_and_coordinates_are_independent_requirements() -> None:
    module, function, nodes = _parts(SIMPLE)
    certificate = _certify(SIMPLE, module, function, nodes)
    assert certificate is not None
    for site in (
        replace(certificate.site, module_digest="stale"),
        replace(certificate.site, function_position=(2, 1)),
        replace(certificate.site, block_position=(4, 8, 2)),
    ):
        assert (
            certify_if_binding_transfer(SIMPLE, module, function, nodes, site, ("answer",)) is None
        )
    changed = SIMPLE.replace("subtotal = 20", "subtotal = 21")
    assert not verify_if_binding_transfer(certificate, changed, ast.parse(changed))
    assert _certify(changed, module, function, nodes) is None
    nodes[0].lineno += 1
    assert _certify(SIMPLE, module, function, nodes) is None


def test_ambiguous_shared_and_cyclic_trees_are_refused() -> None:
    module, function, nodes = _parts(SIMPLE)
    function.body.append(nodes[0])
    assert _certify(SIMPLE, module, function, nodes) is None
    module, function, nodes = _parts(SIMPLE)
    branch = cast(ast.If, function.body[0])
    branch.body.append(branch)
    assert _certify(SIMPLE, module, function, nodes) is None


def test_certificate_survives_reparse_copy_store_and_owner_collection() -> None:
    module, function, nodes = _parts(SIMPLE)
    refs = (weakref.ref(module), weakref.ref(function), weakref.ref(nodes[0]))
    certificate = _certify(SIMPLE, module, function, nodes, ("doubled", "answer"))
    assert certificate is not None
    stored = cast(IfBindingTransfer, pickle.loads(pickle.dumps(certificate)))
    assert stored == certificate
    assert verify_if_binding_transfer(stored, SIMPLE, copy.deepcopy(module))
    del module, function, nodes
    gc.collect()
    assert all(ref() is None for ref in refs)
    assert verify_if_binding_transfer(stored, SIMPLE, ast.parse(SIMPLE))
    with pytest.raises(FrozenInstanceError):
        setattr(stored, "count", 0)
    for changed in (
        replace(stored, count=0),
        replace(stored, start=-1),
        replace(stored, suite_field="orelse"),
        replace(stored, suite_path=(("body", 100),)),
        replace(stored, initialized_names=frozenset(("answer",))),
        replace(stored, normal_exit_bound=frozenset()),
        replace(stored, returned_names=("answer", "answer")),
    ):
        assert not verify_if_binding_transfer(changed, SIMPLE, ast.parse(SIMPLE))


def test_verification_reproves_source_after_an_owner_mutation() -> None:
    module, function, nodes = _parts(SIMPLE)
    certificate = _certify(SIMPLE, module, function, nodes)
    assert certificate is not None
    assert verify_if_binding_transfer(certificate, SIMPLE, module)
    assignment = cast(ast.Assign, nodes[0])
    assignment.value = ast.copy_location(ast.Constant(value=99), assignment.value)
    assert not verify_if_binding_transfer(certificate, SIMPLE, module)


def test_ineligible_ancestry_is_rejected_before_whole_source_validation() -> None:
    source = "def f(flag):\n    while flag:\n        answer = 3\n    return answer\n"
    module = ast.parse(source)
    function = cast(ast.FunctionDef, module.body[0])
    loop = cast(ast.While, function.body[0])
    with patch.object(if_binding_transfer, "_source_corresponds", side_effect=AssertionError):
        assert _certify(source, module, function, loop.body) is None
        branch = ast.If(test=ast.Constant(value=True), body=[], orelse=[])
        branch.body.append(branch)
        function.body.insert(0, branch)
        assert _certify(source, module, function, loop.body) is None


def test_every_admitted_certificate_and_reproof_checks_whole_source_once() -> None:
    module, function, nodes = _parts(SIMPLE)
    with patch.object(
        if_binding_transfer, "_source_corresponds", wraps=if_binding_transfer._source_corresponds
    ) as checked:
        certificate = _certify(SIMPLE, module, function, nodes)
        assert certificate is not None
        assert checked.call_count == 1
        assert verify_if_binding_transfer(certificate, SIMPLE, module)
        assert checked.call_count == 2
        assignment = cast(ast.Assign, nodes[0])
        assignment.value = ast.copy_location(ast.Constant(value=99), assignment.value)
        assert not verify_if_binding_transfer(certificate, SIMPLE, module)
        assert checked.call_count == 3


@pytest.mark.parametrize("warm_cache", [False, True])
def test_shared_gate_keeps_lifetime_outputs_and_reproves_cached_sites(
    tmp_path: Path, warm_cache: bool
) -> None:
    path = tmp_path / "case.py"
    path.write_text(SIMPLE)
    engine = UnificationRefactorEngine(min_lines=3)
    analysis = engine.analysis_session.analyze_module(str(path))
    engine._record_function_paths(analysis.functions)
    function = analysis.functions[0].node
    branch = function.body[0]
    assert isinstance(branch, ast.If)
    nodes = branch.body
    analyzer = analysis.module.scope_analyzer
    site = engine._block_site(function, nodes)
    assert site is not None
    if warm_cache:
        assert engine._block_rejected(nested_bindings_escape, nodes, function, site=site)
        assert engine._if_binding_transfer(function, nodes, SIMPLE, analyzer, site=site) is not None
    assert not engine._nested_bindings_rejected(
        function, nodes, SIMPLE, analyzer, site=site, allow_if_transfer=True
    )
    assert engine._nested_bindings_rejected(
        function, nodes, SIMPLE, analyzer, site=site, allow_if_transfer=False
    )
    assert (
        engine._if_binding_transfer(
            function, nodes, SIMPLE, analyzer, site=site, returned=("answer",)
        )
        is None
    )
    certificate = engine._if_binding_transfer(
        function, nodes, SIMPLE, analyzer, site=site, returned=("answer", "doubled")
    )
    assert certificate is not None
    assert certificate.returned_names == ("answer", "doubled")
    assert engine._if_binding_transfer(function, nodes, SIMPLE, analyzer, site=None) is None
    # A warmed original guard cannot discharge independent aligned-output certification.
    with patch("towel.unification.block_analysis.certify_if_binding_transfer", return_value=None):
        assert engine._nested_bindings_rejected(
            function, nodes, SIMPLE, analyzer, site=site, allow_if_transfer=True
        )
        assert (
            engine._if_binding_transfer(
                function, nodes, SIMPLE, analyzer, site=site, returned=("answer", "doubled")
            )
            is None
        )
