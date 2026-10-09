# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0.
"""Proof scheduling changes no source authority or conservative fallback contract."""

from __future__ import annotations

import ast
from contextlib import nullcontext
from dataclasses import dataclass, replace
import os
import logging
import sys
from pathlib import Path
import traceback
from typing import Callable, Dict, List, Literal, Optional, Sequence, Tuple, cast
import warnings

import pytest

from towel.canonical_ast import exact_dump
from towel.analysis_sources import parsing_is_pure
from towel.diagnostics import VALIDATION
from towel.unification.bounded_cache import memoization_disabled
from towel.unification.engine_state import BlockSite
from towel.unification.function_index import FunctionIndex
from towel.unification.if_binding_transfer import IfBindingTransfer
from towel.unification.models import (
    CodeBlockPair,
    FunctionArtifact,
    FunctionNode,
    RefactoringProposal,
    source_digest_of,
)
from towel.unification import pair_evaluation as evaluation
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.scope_analyzer import ScopeAnalyzer
from towel.unification.substitution import Substitution
from towel.unification.typing_forms import ModuleText

SOURCE = "FLAG = True\nanswer = 999\ndef f():\n    if FLAG:\n        subtotal = 20\n        doubled = subtotal * 2\n        answer = doubled + 3\n    return answer\ndef g():\n    if FLAG:\n        subtotal = 20\n        doubled = subtotal * 2\n        answer = doubled + 7\n    print('second', answer)\n    return answer * 2\n"


class _Observed(UnificationRefactorEngine):
    def __init__(self) -> None:
        super().__init__(annotate_helpers=False)
        self.events: List[str] = []
        self.proofs: List[Tuple[str, Optional[Tuple[str, ...]], bool]] = []
        self.schedules: List[bool] = []

    def _can_defer_nested_binding_proof(
        self, pair: CodeBlockPair, ctx: evaluation._PairContext, functions: FunctionIndex
    ) -> bool:
        answer = super()._can_defer_nested_binding_proof(pair, ctx, functions)
        self.schedules.append(answer)
        return answer

    def _nested_pair_binding_rejected(
        self, pair: CodeBlockPair, ctx: evaluation._PairContext
    ) -> bool:
        self.events.append("nested")
        return super()._nested_pair_binding_rejected(pair, ctx)

    def _analyze_bindings(
        self, pair: CodeBlockPair, ctx: evaluation._PairContext
    ) -> Optional[evaluation._BindingAnalysis]:
        self.events.append("bindings")
        return super()._analyze_bindings(pair, ctx)

    def _check_shape(
        self, pair: CodeBlockPair, analysis: evaluation._BindingAnalysis
    ) -> Optional[bool]:
        self.events.append("shape")
        return super()._check_shape(pair, analysis)

    def _unify_memoized(
        self,
        blocks: Sequence[Sequence[ast.stmt]],
        hygienic_renames: List[Dict[str, str]],
        modules: Optional[Sequence[ModuleText]] = None,
        *,
        thread_literal_pools: bool = False,
    ) -> Optional[Substitution]:
        self.events.append("unify")
        return super()._unify_memoized(
            blocks, hygienic_renames, modules, thread_literal_pools=thread_literal_pools
        )

    def _if_binding_transfer(
        self,
        function: FunctionNode,
        nodes: Sequence[ast.stmt],
        source: str,
        analyzer: ScopeAnalyzer,
        *,
        site: Optional[BlockSite],
        returned: Optional[Sequence[str]] = None,
    ) -> Optional[IfBindingTransfer]:
        answer = super()._if_binding_transfer(
            function, nodes, source, analyzer, site=site, returned=returned
        )
        self.proofs.append(
            (function.name, None if returned is None else tuple(returned), answer is not None)
        )
        return answer


@dataclass(frozen=True)
class _Inputs:
    engine: _Observed
    module: ast.Module
    pair: CodeBlockPair
    artifacts: Tuple[FunctionArtifact, ...]
    index: FunctionIndex


def _inputs(tmp_path: Path, source: str = SOURCE) -> _Inputs:
    path = str(tmp_path / "case.py")
    Path(path).write_text(source)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        module = ast.parse(source)
    functions = [node for node in module.body if isinstance(node, ast.FunctionDef)]
    assert len(functions) == 2
    first, second = functions
    branches = [
        next(node for node in function.body if isinstance(node, ast.If)) for function in functions
    ]
    analyzer = ScopeAnalyzer()
    root_scope = analyzer.analyze(module)
    artifacts = tuple(
        FunctionArtifact(
            path, function, source, analyzer, root_scope, None, None, [], source_digest_of(source)
        )
        for function in functions
    )
    engine = _Observed()
    engine._record_function_paths(artifacts)
    pair = CodeBlockPair(
        path,
        first.name,
        second.name,
        (
            branches[0].body[0].lineno,
            branches[0].body[-1].end_lineno or branches[0].body[-1].lineno,
        ),
        (
            branches[1].body[0].lineno,
            branches[1].body[-1].end_lineno or branches[1].body[-1].lineno,
        ),
        branches[0].body,
        branches[1].body,
        path,
        None,
        None,
        None,
        None,
        [],
        [],
        analyzer,
        analyzer,
        root_scope,
        root_scope,
        source,
        source,
        first,
        second,
    )
    return _Inputs(engine, module, pair, artifacts, FunctionIndex.build(artifacts))


def _proposal(inputs: _Inputs) -> Optional[RefactoringProposal]:
    return inputs.engine._try_refactor_pair_multi_file(inputs.pair, inputs.artifacts, [])


@pytest.mark.parametrize(
    "mode",
    ["outside", "mismatch", "source_copy", "digest", "empty_digest", "missing_site", "warning"],
)
def test_ineligible_context_and_source_metadata_keep_early_proofs(
    tmp_path: Path, mode: str
) -> None:
    inputs = _inputs(tmp_path, "WARNING = '\\q'\n" + SOURCE if mode == "warning" else SOURCE)
    if mode == "source_copy":
        inputs.pair.source1 = inputs.pair.source1.encode().decode()
        assert (
            inputs.pair.source1 == inputs.artifacts[0].source
            and inputs.pair.source1 is not inputs.artifacts[0].source
        )
    elif mode in ("digest", "empty_digest"):
        artifacts = tuple(
            artifact._replace(source_digest="bad" if mode == "digest" else "")
            for artifact in inputs.artifacts
        )
        inputs = replace(inputs, artifacts=artifacts, index=FunctionIndex.build(artifacts))
    elif mode == "missing_site":
        inputs.engine._function_sources.clear()
    owner = object() if mode == "mismatch" else inputs.engine
    with nullcontext() if mode == "outside" else evaluation.deferring_pair_if_proofs(owner):
        ctx = inputs.engine._resolve_pair_context(inputs.pair, inputs.index)
        assert not inputs.engine._can_defer_nested_binding_proof(inputs.pair, ctx, inputs.index)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            inputs.engine._guard_pair(inputs.pair, inputs.index)
    assert inputs.engine.events == ["nested"]


def test_owner_scope_restores_parent_on_nested_exception() -> None:
    outer, inner = object(), object()
    assert evaluation._IF_PROOF_OWNER.get() is None
    with evaluation.deferring_pair_if_proofs(outer):
        with pytest.raises(RuntimeError), evaluation.deferring_pair_if_proofs(inner):
            assert evaluation._IF_PROOF_OWNER.get() is inner
            raise RuntimeError("exit")
        assert evaluation._IF_PROOF_OWNER.get() is outer
    assert evaluation._IF_PROOF_OWNER.get() is None


@pytest.mark.skipif(not hasattr(os, "fork"), reason="native fork unavailable")
def test_fork_inherits_owner_and_child_scope_exit_cannot_change_parent() -> None:
    owner = object()
    with evaluation.deferring_pair_if_proofs(owner):
        parent = evaluation._IF_SCHEDULING_SOURCES.get()
        assert parent is not None and evaluation._scheduling_parse_is_pure(owner, SOURCE)
        pid = os.fork()
        if pid == 0:
            try:
                assert evaluation._IF_PROOF_OWNER.get() is owner
                assert evaluation._IF_SCHEDULING_SOURCES.get() is parent
                assert SOURCE in parent.answers
                with evaluation.deferring_pair_if_proofs(object()):
                    assert evaluation._IF_PROOF_OWNER.get() is not owner
                assert evaluation._IF_PROOF_OWNER.get() is owner
            except BaseException:
                traceback.print_exc()
                os._exit(1)
            os._exit(0)
        assert os.waitpid(pid, 0)[1] == 0
        assert evaluation._IF_PROOF_OWNER.get() is owner
        assert SOURCE in parent.answers
    assert evaluation._IF_PROOF_OWNER.get() is None
    assert not parent.answers


@pytest.mark.parametrize("disabled", [False, True])
def test_accepted_pair_retains_both_fresh_proofs_for_each_caller(
    tmp_path: Path, disabled: bool
) -> None:
    inputs = _inputs(tmp_path)
    before = exact_dump(inputs.module)
    with (
        evaluation.deferring_pair_if_proofs(inputs.engine),
        memoization_disabled() if disabled else nullcontext(),
    ):
        result = _proposal(inputs)
    assert result is not None
    assert inputs.engine.events[:4] == ["bindings", "shape", "unify", "nested"]
    assert inputs.engine.proofs == [
        ("f", None, True),
        ("g", None, True),
        ("f", ("doubled", "answer"), True),
        ("g", ("doubled", "answer"), True),
    ]
    assert exact_dump(inputs.module) == before
    rendered = inputs.engine.apply_refactoring(inputs.pair.file_path, result)
    for text in (inputs.pair.source1, rendered):
        namespace: Dict[str, object] = {}
        exec(compile(text, "<proof-order>", "exec"), namespace)
        for name, expected in (("f", 43), ("g", 94)):
            function = cast("Callable[[], object]", namespace[name])
            namespace["FLAG"] = True
            assert function() == expected
            namespace["FLAG"] = False
            with pytest.raises(UnboundLocalError, match="answer"):
                function()


def test_unification_failure_avoids_initial_certificates(tmp_path: Path) -> None:
    source = SOURCE.replace("answer = doubled + 3", "answer = f'{doubled}:first'").replace(
        "answer = doubled + 7", "answer = f'{doubled}:second'"
    )
    early = _inputs(tmp_path, source)
    assert _proposal(early) is None
    assert len(early.engine.proofs) == 2 and all(item[2] for item in early.engine.proofs)
    later = _inputs(tmp_path, source)
    with evaluation.deferring_pair_if_proofs(later.engine):
        assert _proposal(later) is None
    assert later.engine.events == ["bindings", "shape", "unify"]
    assert not later.engine.proofs


def test_local_invalid_flow_adds_unification_work_but_cannot_render(tmp_path: Path) -> None:
    source = SOURCE.replace(
        "        answer = doubled +", "        assert doubled\n        answer = doubled +"
    )
    early = _inputs(tmp_path, source)
    assert _proposal(early) is None and early.engine.events == ["nested"]
    assert len(early.engine._unify_cache) == 0
    later = _inputs(tmp_path, source)
    with evaluation.deferring_pair_if_proofs(later.engine):
        assert _proposal(later) is None
    assert later.engine.events == ["bindings", "shape", "unify", "nested"]
    assert len(later.engine._unify_cache) == 1
    assert later.engine._pair_rejection == early.engine._pair_rejection


def test_current_module_mutation_is_rejected_after_unification(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    assignment = inputs.module.body[0]
    assert isinstance(assignment, ast.Assign) and isinstance(assignment.value, ast.Constant)
    assignment.value.value = 1000
    with evaluation.deferring_pair_if_proofs(inputs.engine):
        assert _proposal(inputs) is None
    assert inputs.engine.events == ["bindings", "shape", "unify", "nested"]
    assert inputs.engine.proofs == [("f", None, False)]


@pytest.mark.parametrize("policy", ["always", "error"])
def test_warning_capable_sources_keep_native_warning_and_refusal_order(
    tmp_path: Path, policy: Literal["always", "error"]
) -> None:
    source = "WARNING = '\\q'\n" + SOURCE
    observed: List[Tuple[List[Tuple[str, str]], List[str], bool]] = []
    for deferred in (False, True):
        inputs = _inputs(tmp_path, source)
        with warnings.catch_warnings(record=True) as emitted:
            warnings.simplefilter(policy)
            with evaluation.deferring_pair_if_proofs(inputs.engine) if deferred else nullcontext():
                proposal = _proposal(inputs)
        observed.append(
            (
                [(item.category.__name__, str(item.message)) for item in emitted],
                inputs.engine.events,
                proposal is not None,
            )
        )
    assert observed[0] == observed[1]
    assert observed[0][1][0] == "nested"
    if policy == "always":
        assert observed[0][0] and observed[0][2]
    else:
        assert not observed[0][2]


def test_outside_fallback_keeps_legacy_three_argument_unify_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = _inputs(tmp_path)
    original = inputs.engine._unify_pair
    calls = 0

    def legacy(
        pair: CodeBlockPair, analysis: evaluation._BindingAnalysis, ctx: evaluation._PairContext
    ) -> Optional[evaluation._Unified]:
        nonlocal calls
        calls += 1
        return original(pair, analysis, ctx)

    monkeypatch.setattr(inputs.engine, "_unify_pair", legacy)
    assert _proposal(inputs) is not None and calls == 1


def test_malformed_direct_function_keeps_original_guard_exception(tmp_path: Path) -> None:
    for mismatched_owner in (False, True):
        inputs = _inputs(tmp_path)
        delattr(inputs.pair.function1_node, "args")
        with evaluation.deferring_pair_if_proofs(object()) if mismatched_owner else nullcontext():
            with pytest.raises(AttributeError, match="args"):
                _proposal(inputs)
        assert inputs.engine.events == []


def test_earlier_binding_failure_has_documented_debug_and_reason_priority(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    source = SOURCE.replace("    if FLAG:", "    answer = 2\n    if FLAG:")
    with caplog.at_level(logging.DEBUG, logger=VALIDATION.name):
        early = _inputs(tmp_path, source)
        assert _proposal(early) is None
        assert str(early.engine._pair_rejection) == "nested_binding_escapes"
        assert "=== Finding Functions ===" not in caplog.text
        caplog.clear()
        later = _inputs(tmp_path, source)
        with evaluation.deferring_pair_if_proofs(later.engine):
            assert _proposal(later) is None
        assert str(later.engine._pair_rejection) == "unsafe_reassignment_block1"
        assert later.engine.events == ["bindings"]
        assert "=== Finding Functions ===" in caplog.text
        assert not later.engine.proofs


def test_actual_analyze_file_owns_the_scheduling_scope_and_releases_it(tmp_path: Path) -> None:
    path = tmp_path / "actual.py"
    path.write_text(SOURCE)
    engine = _Observed()
    before = path.read_bytes()
    assert evaluation._IF_PROOF_OWNER.get() is None
    proposals = engine.analyze_file(str(path))
    assert proposals and any(engine.schedules)
    assert evaluation._IF_PROOF_OWNER.get() is None
    assert path.read_bytes() == before


def test_large_source_reuses_only_lexical_answer_and_keeps_live_proofs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = "# 1candidate " + "x" * (128 * 1024) + "\n" + SOURCE
    inputs = _inputs(tmp_path, source)
    calls: List[str] = []
    original = parsing_is_pure

    def observed(text: str) -> bool:
        calls.append(text)
        return original(text)

    monkeypatch.setattr(evaluation, "parsing_is_pure", observed)
    with evaluation.deferring_pair_if_proofs(inputs.engine):
        ctx = inputs.engine._resolve_pair_context(inputs.pair, inputs.index)
        for _ in range(3):
            assert inputs.engine._can_defer_nested_binding_proof(inputs.pair, ctx, inputs.index)
        assert calls == [source]
        # Equal text does not replace the artifact's source-object ownership.
        inputs.pair.source1 = source.encode().decode()
        assert not inputs.engine._can_defer_nested_binding_proof(inputs.pair, ctx, inputs.index)
        inputs.pair.source1 = source
        assignment = inputs.module.body[0]
        assert isinstance(assignment, ast.Assign) and isinstance(assignment.value, ast.Constant)
        assignment.value.value = 1000
        assert _proposal(inputs) is None
        assert inputs.engine.proofs == [("f", None, False)]
        assert calls == [source]


@pytest.mark.parametrize("limit", ["outside", "owner", "entries", "bytes", "oversize", "disabled"])
def test_scheduling_purity_fallbacks_recompute_without_retention(
    monkeypatch: pytest.MonkeyPatch, limit: str
) -> None:
    owner = object()
    calls = 0

    def observed(source: str) -> bool:
        nonlocal calls
        calls += 1
        return False

    monkeypatch.setattr(evaluation, "parsing_is_pure", observed)
    with (
        (
            nullcontext()
            if limit == "outside"
            else evaluation.deferring_pair_if_proofs(
                object() if limit == "owner" else owner,
                entries=0 if limit == "entries" else 64,
                source_bytes=0 if limit == "bytes" else 1 if limit == "oversize" else 1024,
            )
        ),
        memoization_disabled() if limit == "disabled" else nullcontext(),
    ):
        assert not evaluation._scheduling_parse_is_pure(owner, SOURCE)
        assert not evaluation._scheduling_parse_is_pure(owner, SOURCE)
        assert calls == 2
        scope = evaluation._IF_SCHEDULING_SOURCES.get()
        assert scope is None or not scope.answers


def test_false_answers_and_disabled_warm_hits_keep_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = object()
    calls = 0

    def observed(source: str) -> bool:
        nonlocal calls
        calls += 1
        return False

    monkeypatch.setattr(evaluation, "parsing_is_pure", observed)
    with evaluation.deferring_pair_if_proofs(owner):
        assert not evaluation._scheduling_parse_is_pure(owner, SOURCE)
        assert not evaluation._scheduling_parse_is_pure(owner, SOURCE)
        assert calls == 1
        with memoization_disabled():
            assert not evaluation._scheduling_parse_is_pure(owner, SOURCE)
            assert not evaluation._scheduling_parse_is_pure(owner, SOURCE)
        assert calls == 3
        assert not evaluation._scheduling_parse_is_pure(owner, SOURCE)
        assert calls == 3


def test_scheduling_source_limits_evict_and_scope_exit_clears_under_disabled() -> None:
    owner = object()
    first, second = "a = 1\n", "b = 2\n"
    budget = sys.getsizeof(first)
    with evaluation.deferring_pair_if_proofs(owner, entries=2, source_bytes=budget):
        scope = evaluation._IF_SCHEDULING_SOURCES.get()
        assert scope is not None
        assert evaluation._scheduling_parse_is_pure(owner, first)
        assert evaluation._scheduling_parse_is_pure(owner, second)
        assert list(scope.answers) == [second]
        assert scope.answers._total_weight <= budget
    assert not scope.answers and scope.answers._total_weight == 0
    # Explicit cleanup works while disabled reads cannot retrieve retained entries.
    context = evaluation.deferring_pair_if_proofs(owner)
    context.__enter__()
    retained = evaluation._IF_SCHEDULING_SOURCES.get()
    assert retained is not None and evaluation._scheduling_parse_is_pure(owner, first)
    with memoization_disabled():
        context.__exit__(None, None, None)
    assert not retained.answers and retained.answers._total_weight == 0
    assert evaluation._IF_SCHEDULING_SOURCES.get() is None


def test_nested_source_scope_restores_parent_cache_on_exception() -> None:
    outer, inner = object(), object()
    with evaluation.deferring_pair_if_proofs(outer):
        parent = evaluation._IF_SCHEDULING_SOURCES.get()
        assert parent is not None and evaluation._scheduling_parse_is_pure(outer, SOURCE)
        with pytest.raises(RuntimeError), evaluation.deferring_pair_if_proofs(inner):
            child = evaluation._IF_SCHEDULING_SOURCES.get()
            assert child is not None and child is not parent
            assert evaluation._scheduling_parse_is_pure(inner, SOURCE)
            raise RuntimeError("exit")
        assert child is not None and not child.answers
        assert evaluation._IF_SCHEDULING_SOURCES.get() is parent and SOURCE in parent.answers
    assert not parent.answers and evaluation._IF_SCHEDULING_SOURCES.get() is None


@pytest.mark.parametrize("policy", ["always", "error"])
def test_warm_false_classification_keeps_each_native_warning_or_error(
    tmp_path: Path, policy: Literal["always", "error"]
) -> None:
    source = "WARNING = '\\q'\n" + SOURCE
    inputs = _inputs(tmp_path, source)
    observations: List[Tuple[List[Tuple[str, str]], bool]] = []
    with evaluation.deferring_pair_if_proofs(inputs.engine):
        for _ in range(2):
            with warnings.catch_warnings(record=True) as emitted:
                warnings.simplefilter(policy)
                result = inputs.engine._guard_pair(inputs.pair, inputs.index)
            observations.append(
                (
                    [(item.category.__name__, str(item.message)) for item in emitted],
                    result is not None,
                )
            )
        scope = evaluation._IF_SCHEDULING_SOURCES.get()
        assert scope is not None and not scope.answers[source].pure
    assert observations[0] == observations[1]
    if policy == "always":
        assert observations[0][0] and observations[0][1]
    else:
        assert not observations[0][1]


@pytest.mark.parametrize("entries,source_bytes", [(-1, 1), (1, -1)])
def test_invalid_scheduling_limits_leave_context_unchanged(entries: int, source_bytes: int) -> None:
    with (
        pytest.raises(ValueError),
        evaluation.deferring_pair_if_proofs(object(), entries=entries, source_bytes=source_bytes),
    ):
        pytest.fail("Invalid limits were admitted")
    assert evaluation._IF_PROOF_OWNER.get() is None
    assert evaluation._IF_SCHEDULING_SOURCES.get() is None


@pytest.mark.parametrize("reserve", [False, True])
def test_unicode_source_storage_reserves_native_utf8_before_retention(
    monkeypatch: pytest.MonkeyPatch, reserve: bool
) -> None:
    owner = object()
    source = "# λ 1candidate\n"
    storage = sys.getsizeof(source) + 4 * len(source) + 1
    calls = 0
    original = parsing_is_pure

    def observed(text: str) -> bool:
        nonlocal calls
        calls += 1
        return original(text)

    monkeypatch.setattr(evaluation, "parsing_is_pure", observed)
    with evaluation.deferring_pair_if_proofs(
        owner, source_bytes=storage if reserve else storage - 1
    ):
        assert evaluation._scheduling_parse_is_pure(owner, source)
        assert evaluation._scheduling_parse_is_pure(owner, source)
        scope = evaluation._IF_SCHEDULING_SOURCES.get()
        assert scope is not None
        assert calls == (1 if reserve else 2)
        assert scope.answers._total_weight == (storage if reserve else 0)


def test_source_entry_limit_and_subclass_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    class Source(str):
        pass

    calls = 0

    def observed(source: str) -> bool:
        nonlocal calls
        calls += 1
        return True

    owner = object()
    monkeypatch.setattr(evaluation, "parsing_is_pure", observed)
    with evaluation.deferring_pair_if_proofs(owner, entries=1):
        for source in ("a = 1", "b = 2", "a = 1"):
            assert evaluation._scheduling_parse_is_pure(owner, source)
        assert calls == 3
        scope = evaluation._IF_SCHEDULING_SOURCES.get()
        assert scope is not None and list(scope.answers) == ["a = 1"]
        subclass_source = Source("a = 1")
        assert evaluation._scheduling_parse_is_pure(owner, subclass_source)
        assert evaluation._scheduling_parse_is_pure(owner, subclass_source)
        assert calls == 5 and list(scope.answers) == ["a = 1"]
