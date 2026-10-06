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

"""Finite synthetic driver controls: no checker subprocess or throughput claim."""

import ast
import copy
from pathlib import Path
from unittest.mock import patch

import pytest

from towel.formatting import FormattingChangedCode
from towel.type_inference import PyrightOracle
from towel.unification.exceptions import (
    CheckerUnavailableError,
    RefactoringError,
    TypeRejectedExtraction,
)
from towel.unification.fixed_point import (
    _ApplyProgress,
    _DirectoryRun,
    _RejectedProposals,
)
from towel.unification.models import RefactoringProposal, Replacement, source_digest_of
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.rehearing import (
    checker_context,
    exact_candidate_key,
    execution_context,
)


def proposal(path: Path) -> RefactoringProposal:
    node = ast.parse("def helper():\n    pass\n").body[0]
    assert isinstance(node, ast.FunctionDef)
    return RefactoringProposal(
        str(path),
        node,
        [Replacement((1, 1), ast.parse("VALUE = 1").body[0])],
        path.name,
        0,
        source_digests=((str(path), source_digest_of(path.read_text())),),
    )


@pytest.mark.parametrize("old_becomes_valid", [False, True])
def test_whole_revision_not_local_dependency_and_current_exact_skip(
    tmp_path: Path, old_becomes_valid: bool
) -> None:
    a, b, c = (tmp_path / name for name in ("a.py", "b.py", "c.py"))
    for path in (a, b, c):
        path.write_text("VALUE = 0\n")
    attempted: list[str] = []
    engine = UnificationRefactorEngine()
    engine._analysis_paths = tuple(str(p) for p in (a, b, c))
    engine._type_run_oracle = object.__new__(PyrightOracle)
    run = _DirectoryRun()
    reporter = _ApplyProgress("none", None)

    def analyze(*args: object, **kwargs: object) -> list[RefactoringProposal]:
        assert len(attempted) < 9, "unchanged refusals looped"
        return [proposal(p) for p in (a, b, c) if p.read_text() == "VALUE = 0\n"]

    def apply(candidate: RefactoringProposal) -> dict[str, str]:
        path = Path(candidate.file_path)
        attempted.append(path.name)
        if path == c or path == a and (not old_becomes_valid or b.read_text() == "VALUE = 0\n"):
            raise TypeRejectedExtraction("completed candidate checking")
        return {str(path): "VALUE = 1\n"}

    with (
        patch.object(engine, "analyze_directory", analyze),
        patch.object(engine, "analyze_files", return_value=[]),
        patch.object(engine, "apply_refactoring_multi_file", apply),
    ):
        results, reason = engine._apply_until_fixed_point(tmp_path, run, reporter, 0, "fixed_point")
    assert reason == "fixed_point"
    assert attempted == (
        ["a.py", "b.py", "c.py", "a.py", "c.py"]
        if old_becomes_valid
        else ["a.py", "b.py", "c.py", "a.py"]
    )
    assert sum(count for count, _ in results.values()) == (2 if old_becomes_valid else 1)
    assert c.read_text() == "VALUE = 0\n"


@pytest.mark.parametrize(
    "failure", [CheckerUnavailableError, FormattingChangedCode, RefactoringError]
)
def test_current_revision_transient_or_unknown_failure_retried(
    tmp_path: Path, failure: type[Exception]
) -> None:
    b, c = tmp_path / "b.py", tmp_path / "c.py"
    for path in (b, c):
        path.write_text("VALUE = 0\n")
    attempted: list[str] = []
    engine = UnificationRefactorEngine()
    engine._analysis_paths = (str(b), str(c))
    engine._type_run_oracle = object.__new__(PyrightOracle)

    def analyze(*args: object, **kwargs: object) -> list[RefactoringProposal]:
        assert len(attempted) < 6
        return [proposal(p) for p in (b, c) if p.read_text() == "VALUE = 0\n"]

    def apply(candidate: RefactoringProposal) -> dict[str, str]:
        attempted.append(Path(candidate.file_path).name)
        if candidate.file_path == str(c) and attempted.count("c.py") == 1:
            # A rejected first rung does not turn a later rendering/tool failure
            # into a completed verdict for the whole candidate.
            engine._checker_refusals = 1
            raise failure("transient or unknown")
        return {candidate.file_path: "VALUE = 1\n"}

    with (
        patch.object(engine, "analyze_directory", analyze),
        patch.object(engine, "analyze_files", return_value=[]),
        patch.object(engine, "apply_refactoring_multi_file", apply),
    ):
        results, reason = engine._apply_until_fixed_point(
            tmp_path, _DirectoryRun(), _ApplyProgress("none", None), 0, "fixed_point"
        )
    assert reason == "fixed_point" and attempted == ["b.py", "c.py", "c.py"]
    assert len(results) == 2


def test_exact_candidate_not_coarse_identity(tmp_path: Path) -> None:
    path = tmp_path / "m.py"
    path.write_text("VALUE = 0\n")
    original = proposal(path)
    shifted = copy.deepcopy(original)
    shifted.replacements[0].line_range = (2, 2)
    assert _RejectedProposals.identity(original) == _RejectedProposals.identity(shifted)
    assert exact_candidate_key(original) != exact_candidate_key(shifted)
    rejected = _RejectedProposals()
    rejected.add(original, revision=1, context="same")
    assert shifted in rejected, "existing ordinary-pass coarse policy stays"
    rejected.retain_current(1, "same")
    assert copy.deepcopy(original) in rejected and shifted not in rejected


@pytest.mark.parametrize("change", ["config", "oracle", "plugin"])
def test_checker_context_change_invalidates_or_disables_retention(
    tmp_path: Path, change: str
) -> None:
    path = tmp_path / "m.py"
    path.write_text("VALUE = 0\n")
    config = tmp_path / "pyrightconfig.json"
    config.write_text('{"include":["m.py"]}')
    oracle = object.__new__(PyrightOracle)
    before = checker_context(oracle, [str(path)])
    assert before is not None
    if change == "config":
        config.write_text('{"include":["m.py"],"typeCheckingMode":"strict"}')
    if change == "oracle":
        oracle = object.__new__(PyrightOracle)
    if change == "plugin":
        from towel.type_inference import MypyInferrer

        oracle = MypyInferrer()  # type: ignore[assignment]
        (tmp_path / "mypy.ini").write_text("[mypy]\nplugins = stateful_plugin\n")
    after = checker_context(oracle, [str(path)])
    assert after != before
    rejected = _RejectedProposals()
    candidate = proposal(path)
    rejected.add(candidate, revision=1, context=before)
    rejected.retain_current(1, after)
    assert candidate not in rejected


def test_custom_oracle_and_unsupported_metadata_are_not_cached(tmp_path: Path) -> None:
    path = tmp_path / "m.py"
    path.write_text("VALUE = 0\n")
    assert checker_context(None, [str(path)]) is None

    class Custom(PyrightOracle):
        pass

    assert checker_context(object.__new__(Custom), [str(path)]) is None
    candidate = proposal(path)
    candidate.extracted_function.unknown_extension = object()  # type: ignore[attr-defined]
    assert exact_candidate_key(candidate) is None
    rejected = _RejectedProposals()
    rejected.add(candidate, revision=1, context="same")
    rejected.retain_current(1, "same")
    assert not rejected


@pytest.mark.parametrize("fails_later", [False, True])
def test_only_normal_ladder_exhaustion_is_a_completed_refusal(
    tmp_path: Path, fails_later: bool
) -> None:
    from towel.type_inference import TypeDiagnostic
    from towel.unification.annotation_ladder import Rejection

    path = tmp_path / "m.py"
    path.write_text("VALUE = 0\n")
    candidate = proposal(path)
    engine = UnificationRefactorEngine()
    engine._type_run_oracle = object.__new__(PyrightOracle)
    declined = Rejection(
        (TypeDiagnostic(str(path), "rejected", 1),),
        str(path),
        "helper",
        "def helper():\n    pass\n",
    )
    responses = [
        declined,
        RefactoringError("later rung rendering failed") if fails_later else declined,
    ]
    with (
        patch.object(engine, "_active_type_oracle", return_value=engine._type_run_oracle),
        patch.object(engine, "_infer_helper_annotations"),
        patch.object(engine, "_annotation_ladder", return_value=iter([candidate, candidate])),
        patch.object(engine, "_judge_for", return_value=lambda helper, rejection: None),
        patch.object(engine, "_attempt", side_effect=responses) as attempt,
    ):
        with pytest.raises(RefactoringError) as caught:
            engine._materialize_refactoring(candidate)
    assert attempt.call_count == 2
    assert isinstance(caught.value, TypeRejectedExtraction) is not fails_later
    assert str(caught.value) == (
        "later rung rendering failed"
        if fails_later
        else "Every helper annotation variant introduces project type errors"
    )


@pytest.mark.parametrize("callback", ["snippet_formatter", "file_finisher", "inference"])
def test_custom_callbacks_disable_execution_retention(tmp_path: Path, callback: str) -> None:
    path = tmp_path / "m.py"
    path.write_text("VALUE = 0\n")
    engine = UnificationRefactorEngine()
    engine._analysis_paths = (str(path),)
    engine._type_run_oracle = object.__new__(PyrightOracle)
    assert execution_context(engine) is not None
    if callback == "inference":
        with patch.object(engine, "_infer_helper_annotations"):
            assert execution_context(engine) is None
    else:
        setattr(engine, callback, lambda *args: args[-1])
        assert execution_context(engine) is None


def test_exact_engine_options_and_known_reference_invalidate(tmp_path: Path) -> None:
    from towel.type_baseline import KnownErrors
    from towel.type_inference import TypeDiagnostic

    path = tmp_path / "m.py"
    path.write_text("VALUE = 0\n")
    engine = UnificationRefactorEngine()
    engine._analysis_paths = (str(path),)
    engine._type_run_oracle = object.__new__(PyrightOracle)
    before = execution_context(engine)
    engine.annotate_helpers = False
    after = execution_context(engine)
    assert before is not None and after is not None and before != after
    engine._type_known = KnownErrors((TypeDiagnostic(str(path), "baseline", 1),))
    assert execution_context(engine) != after


def test_actual_builtin_ruff_pipeline_is_registered_and_reconfiguration_invalidates(
    tmp_path: Path,
) -> None:
    from towel import formatting

    path = tmp_path / "m.py"
    path.write_text("VALUE = 0\n")
    (tmp_path / "pyproject.toml").write_text('[project]\nname="fixture"\nversion="0"\n')
    config = tmp_path / "ruff.toml"
    base = tmp_path / "base.toml"
    config.write_text('extend="base.toml"\n')
    base.write_text('line-length=88\n[lint]\nselect=["F","I"]\n')
    formatter = formatting.ruff_formatter(path)
    finishing = formatting.file_finisher_for_project(path).tool
    assert formatter("x=1") == "x = 1"
    assert finishing is not None and finishing(str(path), "VALUE = 0\n") == "VALUE = 0\n"
    assert formatting.formatting_repeatability(formatter) is not None
    assert formatting.formatting_repeatability(finishing) is not None
    engine = UnificationRefactorEngine(snippet_formatter=formatter, file_finisher=finishing)
    engine._analysis_paths = (str(path),)
    engine._type_run_oracle = object.__new__(PyrightOracle)
    before = execution_context(engine)
    assert before is not None
    base.write_text('line-length=90\n[lint]\nselect=["F","I"]\n')
    assert execution_context(engine) != before
    before = execution_context(engine)
    engine.snippet_formatter = formatting.ruff_formatter(path)
    assert execution_context(engine) != before, "callback identity must stay bound"


def test_builtin_black_worker_closed_invalidates_and_registry_is_weak(
    tmp_path: Path,
) -> None:
    import gc
    import weakref
    from towel import formatting

    formatter = formatting.black_formatter(formatting.BlackSettings())
    capability = formatting.formatting_repeatability(formatter)
    assert capability is not None and capability.workers
    assert formatter("x=1") == "x = 1"
    tool = capability.workers[0]()
    assert tool is not None
    tool.close()
    assert formatting.formatting_repeatability(formatter) is None
    reference = weakref.ref(formatter)
    del formatter
    gc.collect()
    assert reference() is None, "registry must not keep a disposed callback alive"


def test_wrapping_custom_callbacks_never_confers_repeatability() -> None:
    from towel import formatting

    custom = formatting.checked(lambda source: source)
    sorting = formatting.sorted_where_already_sorted(lambda path, source: source, "custom")
    assert formatting.formatting_repeatability(custom) is None
    assert formatting.formatting_repeatability(sorting) is None


def test_malformed_extended_formatter_config_is_uncached(tmp_path: Path) -> None:
    from towel import formatting

    path = tmp_path / "m.py"
    path.write_text("VALUE = 0\n")
    (tmp_path / "pyproject.toml").write_text('[project]\nname="fixture"\nversion="0"\n')
    (tmp_path / "ruff.toml").write_text('extend="missing.toml"\n')
    formatter = formatting.ruff_formatter(path)
    engine = UnificationRefactorEngine(snippet_formatter=formatter)
    engine._analysis_paths = (str(path),)
    engine._type_run_oracle = object.__new__(PyrightOracle)
    assert execution_context(engine) is None


def test_mutable_parent_environment_invalidates_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "m.py"
    path.write_text("VALUE = 0\n")
    engine = UnificationRefactorEngine()
    engine._analysis_paths = (str(path),)
    engine._type_run_oracle = object.__new__(PyrightOracle)
    before = execution_context(engine)
    assert before is not None
    monkeypatch.setenv("TOWEL_REHEARING_CONTEXT_CONTROL", "changed")
    assert execution_context(engine) != before


def test_real_discovery_and_render_restore_the_exact_candidate_and_name_seed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "m.py"
    source = "".join(
        f"def {name}(value: int) -> int:\n    total = value + 1\n    doubled = total * 2\n    answer = doubled - {offset}\n    return answer\n\n"
        for name, offset in (("first", 3), ("second", 4))
    )
    path.write_text(source)
    engine = UnificationRefactorEngine(min_lines=2)
    discovered = engine.analyze_file(str(path))
    assert discovered
    candidate = discovered[0]
    key = exact_candidate_key(candidate)
    assert key is not None
    counters = dict(engine._helper_name_counters)
    rendered = engine.apply_refactoring_multi_file(candidate)
    assert rendered[str(path)] != source and path.read_text() == source
    assert engine._helper_name_counters == counters
    assert exact_candidate_key(candidate) == key
    rediscovered = engine.analyze_file(str(path))
    assert any(exact_candidate_key(other) == key for other in rediscovered)


def test_deep_ast_key_is_linear_sized_and_canonical_sensitive(tmp_path: Path) -> None:
    """Nested structure must not recursively re-escape serialized child strings."""
    import json

    path = tmp_path / "m.py"
    path.write_text("VALUE = 0\n")
    sizes: list[int] = []
    for depth in (16, 32, 64):
        candidate = proposal(path)
        expression: ast.expr = ast.Name(id="value", ctx=ast.Load())
        for _ in range(depth):
            expression = ast.UnaryOp(op=ast.Not(), operand=expression)
        candidate.extracted_function.body = [ast.Return(value=expression)]
        ast.fix_missing_locations(candidate.extracted_function)
        key = exact_candidate_key(candidate)
        assert key is not None
        assert json.loads(key)[0] == "RefactoringProposal"
        size = len(key)
        nodes = sum(1 for _ in ast.walk(candidate.extracted_function))
        assert size <= nodes * 300 + 4000
        assert key == exact_candidate_key(copy.deepcopy(candidate))
        shifted = copy.deepcopy(candidate)
        shifted.extracted_function.body[0].lineno += 1
        assert exact_candidate_key(shifted) != key
        different = copy.deepcopy(candidate)
        for node in ast.walk(different.extracted_function):
            if isinstance(node, ast.Name):
                node.id = "different"
                break
        assert exact_candidate_key(different) != key
        sizes.append(size)
    assert sizes[1] < sizes[0] * 2.1
    assert sizes[2] < sizes[1] * 2.1


def test_every_argument_handoff_field_is_part_of_exact_key(tmp_path: Path) -> None:
    from dataclasses import fields, replace
    from towel.unification.models import ArgumentHandoff

    path = tmp_path / "m.py"
    path.write_text("VALUE = 0\n")
    candidate = proposal(path)
    handoff = ArgumentHandoff(
        parameters=("a", "b"),
        box_name="box",
        captures=(("a", "captured_a"),),
        function_name="first",
        function_line=1,
        function_dump="function AST",
        call_dump="call AST",
        helper_name="helper",
        module_dump="module AST",
    )
    candidate.replacements[0].argument_handoff = handoff
    key = exact_candidate_key(candidate)
    assert key is not None and key == exact_candidate_key(copy.deepcopy(candidate))
    changes = {
        "parameters": replace(handoff, parameters=("b", "a")),
        "box_name": replace(handoff, box_name="other_box"),
        "captures": replace(handoff, captures=(("b", "captured_b"),)),
        "function_name": replace(handoff, function_name="second"),
        "function_line": replace(handoff, function_line=2),
        "function_dump": replace(handoff, function_dump="different function AST"),
        "call_dump": replace(handoff, call_dump="different call AST"),
        "helper_name": replace(handoff, helper_name="other_helper"),
        "module_dump": replace(handoff, module_dump="different module AST"),
    }
    assert {field.name for field in fields(handoff)} == set(changes)
    for name, handoff_variant in changes.items():
        changed = copy.deepcopy(candidate)
        changed.replacements[0].argument_handoff = handoff_variant
        assert exact_candidate_key(changed) != key, name
    # Closed AST/candidate metadata boundary: unsupported extension records fail closed.
    from towel.unification.frame_ownership import FrameOwnership

    candidate.extracted_function.ownership_extension = FrameOwnership(  # type: ignore[attr-defined]
        frozenset({"a"}), frozenset({"b"}), False
    )
    assert exact_candidate_key(candidate) is None


def test_projected_collection_tags_and_order_do_not_collide(tmp_path: Path) -> None:
    path = tmp_path / "m.py"
    path.write_text("VALUE = 0\n")
    candidate = proposal(path)
    candidate.extracted_function.extension = ("x", "y")  # type: ignore[attr-defined]
    tuple_key = exact_candidate_key(candidate)
    candidate.extracted_function.extension = ["x", "y"]  # type: ignore[attr-defined]
    assert exact_candidate_key(candidate) != tuple_key
    candidate.extracted_function.extension = frozenset({"y", "x"})  # type: ignore[attr-defined]
    frozen_key = exact_candidate_key(candidate)
    candidate.extracted_function.extension = frozenset({"x", "y"})  # type: ignore[attr-defined]
    assert frozen_key is not None and exact_candidate_key(candidate) == frozen_key
    candidate.extracted_function.extension = float("nan")  # type: ignore[attr-defined]
    assert exact_candidate_key(candidate) is None


def test_undeclared_record_metadata_is_never_omitted(tmp_path: Path) -> None:
    path = tmp_path / "m.py"
    path.write_text("VALUE = 0\n")
    candidate = proposal(path)
    candidate.new_metadata = "not declared"  # type: ignore[attr-defined]
    assert exact_candidate_key(candidate) is None


def test_empty_ladder_is_unknown_not_completed_checker_refusal(tmp_path: Path) -> None:
    path = tmp_path / "m.py"
    path.write_text("VALUE = 0\n")
    engine = UnificationRefactorEngine()
    with (
        patch.object(engine, "_active_type_oracle", return_value=object.__new__(PyrightOracle)),
        patch.object(engine, "_infer_helper_annotations"),
        patch.object(engine, "_annotation_ladder", return_value=iter(())),
        patch.object(engine, "_judge_for", return_value=lambda helper, rejection: None),
    ):
        with pytest.raises(RefactoringError) as caught:
            engine._materialize_refactoring(proposal(path))
    assert not isinstance(caught.value, TypeRejectedExtraction)


def test_explicit_engine_settings_are_bound_not_only_parent_environment(tmp_path: Path) -> None:
    from dataclasses import replace

    path = tmp_path / "m.py"
    path.write_text("VALUE = 0\n")
    engine = UnificationRefactorEngine()
    engine._analysis_paths = (str(path),)
    engine._type_run_oracle = object.__new__(PyrightOracle)
    before = execution_context(engine)
    assert before is not None
    engine._settings = replace(
        engine._settings, check_ast_immutable=not engine._settings.check_ast_immutable
    )
    assert execution_context(engine) != before
