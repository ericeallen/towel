"""Generic hypotheses need complete checker agreement and transactional rollback."""

from __future__ import annotations

import ast
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.test_generic_extraction import ADDITION, _project
from tests.test_type_run_policy import _Oracle
from towel.type_inference import (
    CheckFailure,
    CheckResult,
    CheckSuccess,
    CombinedOracle,
    MypyInferrer,
    PyrightOracle,
    RevealKey,
    RevealRequest,
    Subtyping,
    TypeDiagnostic,
    TypeOracle,
)
from towel.unification.exceptions import RefactoringError
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.type_bindings import TypeKind, TypeTerm
from towel.unification.type_generalization import GenericSignature, generalize_signatures


@pytest.fixture(params=["mypy", "pyright"])
def checker(request: pytest.FixtureRequest) -> Iterator[TypeOracle]:
    pytest.importorskip(request.param)
    oracle: TypeOracle = MypyInferrer() if request.param == "mypy" else PyrightOracle()
    try:
        yield oracle
    finally:
        oracle.close()


class _RecordingOracle:
    """Keep real diagnostics alongside each complete prospective source graph."""

    def __init__(self, oracle: TypeOracle) -> None:
        self.oracle = oracle
        self.checks: list[tuple[dict[str, str], CheckResult]] = []

    def check_project(
        self, sources: Mapping[str, str], *, excluded_paths: Sequence[str] = ()
    ) -> CheckResult:
        result = self.oracle.check_project(sources, excluded_paths=excluded_paths)
        self.checks.append((dict(sources), result))
        return result

    def check(self, file_path: str, source: str) -> CheckResult:
        return self.check_project({file_path: source})

    def reveal(self, requests: Sequence[RevealRequest]) -> Mapping[RevealKey, str]:
        return self.oracle.reveal(requests)

    def is_subtype(
        self, file_path: str, source: str, pairs: Sequence[tuple[str, str]]
    ) -> Sequence[Subtyping]:
        return self.oracle.is_subtype(file_path, source, pairs)

    def close(self) -> None:
        self.oracle.close()


def _engine(oracle: TypeOracle) -> UnificationRefactorEngine:
    return UnificationRefactorEngine(
        min_lines=2, reuse_existing_functions=False, type_oracle=oracle
    )


def _error(path: Path) -> CheckSuccess:
    return CheckSuccess((TypeDiagnostic(str(path), "Rejected prospective helper"),))


def _helper(source: str) -> ast.FunctionDef:
    return next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name
    )


def _declarations(source: str) -> tuple[str, ...]:
    tree = ast.parse(source)
    constructors = {
        alias.asname or alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "typing"
        for alias in node.names
        if alias.name == "TypeVar"
    }
    return tuple(
        ast.dump(node)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id in constructors
    )


def test_checker_disagreement_cannot_accept_a_generic_candidate(tmp_path: Path) -> None:
    path = _project(tmp_path, ADDITION)
    original = path.read_text()
    primary = _Oracle(lambda _: CheckSuccess())
    dissenting = _Oracle(
        lambda sources: CheckSuccess() if sources[str(path)] == original else _error(path)
    )
    oracle = CombinedOracle(primary, [dissenting])
    engine = _engine(oracle)
    proposal = engine.analyze_file(str(path))[0]
    with pytest.raises(RefactoringError, match="Every helper annotation variant"):
        engine.apply_refactoring(str(path), proposal)
    assert sum(bool(_declarations(sources[str(path)])) for sources in dissenting.checks) >= 2
    assert primary.checks == dissenting.checks
    assert path.read_text() == original and engine.change_log == ()
    assert proposal.helper_type_declarations == ()


def test_generic_checker_timeout_aborts_without_trying_an_unchecked_fallback(
    tmp_path: Path,
) -> None:
    path = _project(tmp_path, ADDITION)
    original = path.read_text()

    def verdict(sources: Mapping[str, str]) -> CheckResult:
        source = sources[str(path)]
        if source == original:
            return CheckSuccess()
        return CheckFailure("generic checker timed out") if _declarations(source) else _error(path)

    oracle = _Oracle(verdict)
    engine = _engine(oracle)
    proposal = engine.analyze_file(str(path))[0]
    with pytest.raises(RefactoringError, match="Prospective project type check failed.*timed out"):
        engine.apply_refactoring(str(path), proposal)
    assert _declarations(oracle.checks[-1][str(path)])
    assert sum(bool(_declarations(sources[str(path)])) for sources in oracle.checks) == 1
    assert path.read_text() == original and engine.change_log == ()
    assert proposal.helper_type_declarations == ()


@pytest.mark.parametrize("fallback", ["any", "bare"])
def test_rejected_generic_declarations_do_not_leak_into_fallbacks(
    tmp_path: Path, fallback: str
) -> None:
    path = _project(tmp_path, ADDITION)
    original = path.read_text()

    def verdict(sources: Mapping[str, str]) -> CheckResult:
        source = sources[str(path)]
        if source == original:
            return CheckSuccess()
        if _declarations(source):
            return _error(path)
        helper = _helper(source)
        annotations = [argument.annotation for argument in helper.args.args] + [helper.returns]
        expected = all(annotation is None for annotation in annotations)
        if fallback == "any":
            expected = all(
                isinstance(annotation, ast.Name) and annotation.id == "Any"
                for annotation in annotations
            )
        return CheckSuccess() if expected else _error(path)

    oracle = _Oracle(verdict)
    engine = _engine(oracle)
    proposal = engine.analyze_file(str(path))[0]
    result = engine.apply_refactoring(str(path), proposal)
    assert sum(bool(_declarations(sources[str(path)])) for sources in oracle.checks) >= 2
    assert not _declarations(result) and "TypeVar" not in result and "_TowelT" not in result
    assert path.read_text() == original
    assert proposal.helper_type_declarations == ()
    # Reusing the same proposal also starts without abandoned declaration state.
    assert engine.apply_refactoring(str(path), proposal) == result


def test_real_checkers_validate_even_an_unused_constraint_body(
    tmp_path: Path, checker: TypeOracle
) -> None:
    path = _project(tmp_path, ADDITION)
    original = path.read_text()
    assert checker.check(str(path), original) == CheckSuccess()
    oracle = _RecordingOracle(checker)
    engine = _engine(oracle)
    proposal = engine.analyze_file(str(path))[0]
    dictionary = TypeTerm(
        TypeKind.APPLY,
        children=tuple(
            TypeTerm(TypeKind.ATOM, f"builtins.{name}", name) for name in ("dict", "str", "int")
        ),
    )

    def extra_constraint(
        rows: Sequence[Sequence[TypeTerm]], reserved: set[str]
    ) -> tuple[GenericSignature, ...]:
        # Fault-inject an unsupported alternative that no original caller uses.
        return tuple(
            replace(
                candidate,
                parameters=tuple(
                    (
                        replace(parameter, constraints=(*parameter.constraints, dictionary))
                        if parameter.constraints
                        else parameter
                    )
                    for parameter in candidate.parameters
                ),
            )
            for candidate in generalize_signatures(rows, reserved)
        )

    with patch(
        "towel.unification.generic_annotations.generalize_signatures", side_effect=extra_constraint
    ):
        try:
            fallback = engine.apply_refactoring(str(path), proposal)
        except RefactoringError as error:
            assert "Every helper annotation variant" in str(error)
            assert engine.change_log == ()
        else:
            # Pyright can accept an ordinary Any fallback that strict mypy
            # rejects. Neither may certify the invalid generic alternative.
            assert not _declarations(fallback) and "_TowelT" not in fallback
            assert checker.check(str(path), fallback) == CheckSuccess()
    tested = [
        (sources[str(path)], result)
        for sources, result in oracle.checks
        if _declarations(sources[str(path)]) and "dict[str, int]" in sources[str(path)]
    ]
    assert tested
    assert all(isinstance(result, CheckSuccess) and result.errors for _, result in tested)
    assert any(
        "dict" in diagnostic.message.lower()
        and ("+" in diagnostic.message or "operator" in diagnostic.message)
        for _, result in tested
        if isinstance(result, CheckSuccess)
        for diagnostic in result.errors
    ), tested
    assert path.read_text() == original


def test_a_lambda_that_escapes_the_original_narrowing_is_never_proposed(
    tmp_path: Path, checker: TypeOracle
) -> None:
    """The extraction is declined where proposals are built, before any checker is asked."""
    path = _project(
        tmp_path,
        """
        class Named:
            names: list[str]

        class Flagged:
            flags: list[int]

        def first(other: object) -> bool:
            if not isinstance(other, Named):
                return False
            return other.names == ["a"]

        def second(other: object) -> bool:
            if not isinstance(other, Flagged):
                return False
            return other.flags == [1]
        """,
    )
    original = path.read_text()
    oracle = _RecordingOracle(checker)
    assert _engine(oracle).analyze_file(str(path)) == []
    assert path.read_text() == original


def test_real_checkers_reject_lambdas_that_escape_the_original_narrowing(
    tmp_path: Path, checker: TypeOracle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The checker is the last line, and must still catch this if the refusal is ever weakened."""
    monkeypatch.setattr(
        "towel.unification.pair_evaluation.narrowing_lost_at_call_site", lambda *_: None
    )
    path = _project(
        tmp_path,
        """
        class Named:
            names: list[str]

        class Flagged:
            flags: list[int]

        def first(other: object) -> bool:
            if not isinstance(other, Named):
                return False
            return other.names == ["a"]

        def second(other: object) -> bool:
            if not isinstance(other, Flagged):
                return False
            return other.flags == [1]
        """,
    )
    original = path.read_text()
    assert checker.check(str(path), original) == CheckSuccess()
    oracle = _RecordingOracle(checker)
    engine = _engine(oracle)
    proposal = engine.analyze_file(str(path))[0]
    assert any(
        isinstance(node, ast.Lambda) for rep in proposal.replacements for node in ast.walk(rep.node)
    )
    with pytest.raises(RefactoringError, match="Every helper annotation variant"):
        engine.apply_refactoring(str(path), proposal)
    errors = [
        diagnostic.message
        for _, result in oracle.checks
        if isinstance(result, CheckSuccess)
        for diagnostic in result.errors
    ]
    assert any(("names" in error or "flags" in error) and "object" in error for error in errors)
    assert path.read_text() == original and engine.change_log == ()


def test_fixed_point_rerun_does_not_accumulate_type_variables(
    tmp_path: Path, checker: TypeOracle
) -> None:
    path = _project(tmp_path, ADDITION)
    engine = _engine(checker)
    first, count, _ = engine.refactor_to_fixed_point(str(path), progress="none")
    assert count == 1 and len(_declarations(first)) == 1
    second, count, _ = engine.refactor_to_fixed_point(str(path), progress="none")
    assert count == 0 and second == first and path.read_text() == first
    assert _declarations(second) == _declarations(first)
    assert checker.check(str(path), second) == CheckSuccess()
