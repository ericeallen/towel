"""The unannotated helper is checked only when the all-``Any`` rejection leaves it a chance.

On a capped Sphinx run 72 of 287 project checks tried an unannotated helper
and none was accepted: 70 followed an all-``Any`` rejection whose errors lay at
the call site, which no spelling of the helper's signature can reach.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import textwrap
from typing import List, Mapping, Sequence

import pytest

from towel.type_inference import CheckResult, MypyInferrer, TypeDiagnostic
from towel.unification.exceptions import RefactoringError
from towel.unification.materialize import _Rejection
from towel.unification.refactor_engine import UnificationRefactorEngine

requires_mypy = pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")

MODULE = textwrap.dedent("""
    def caller() -> None:
        helper(1)


    @staticmethod
    def helper(value):
        return value
    """).lstrip()


def _rejection(*errors: TypeDiagnostic) -> _Rejection:
    return _Rejection(errors, "/project/m.py", "helper", MODULE)


def test_errors_on_the_helpers_own_lines_are_confined_to_it() -> None:
    decorator, last = 5, 7
    assert _rejection(
        TypeDiagnostic("/project/m.py", "explicit Any", decorator),
        TypeDiagnostic("/project/m.py", "missing annotation", last),
    ).confined_to_helper()


def test_an_error_at_the_call_site_or_in_another_file_is_not_confined() -> None:
    assert not _rejection(TypeDiagnostic("/project/m.py", "at the call", 2)).confined_to_helper()
    assert not _rejection(TypeDiagnostic("/project/other.py", "consumer", 6)).confined_to_helper()


def test_an_unlocated_error_costs_a_check_rather_than_a_refactoring() -> None:
    assert _rejection(TypeDiagnostic("/project/other.py", "somewhere")).confined_to_helper()


class _RecordingOracle(MypyInferrer):
    def __init__(self) -> None:
        super().__init__()
        self.checked: List[str] = []

    def check_project(
        self, sources: Mapping[str, str], *, excluded_paths: Sequence[str] = ()
    ) -> CheckResult:
        self.checked.extend(sources.values())
        return super().check_project(sources, excluded_paths=excluded_paths)


def _helper_signatures(sources: Sequence[str]) -> List[str]:
    return [
        ast.unparse(node.args)
        for source in sources
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name
    ]


@requires_mypy
def test_a_call_site_error_under_every_any_ends_the_ladder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Moving ``isinstance`` into the helper leaves ``other`` an ``object`` in the caller's thunk.

    Towel now declines this extraction where the proposal is built, so reaching
    the ladder at all means setting that refusal aside. The ladder rule is the
    second line and is worth keeping tested: an error no helper signature can
    reach ends the ladder rather than costing two more project checks.
    """
    monkeypatch.setattr(
        "towel.unification.pair_evaluation.narrowing_lost_at_call_site", lambda *_: None
    )
    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n")
    path = tmp_path / "shapes.py"
    path.write_text(textwrap.dedent("""
            class Base:
                pass


            class Klass(Base):
                def __init__(self, name: str, final: bool) -> None:
                    self.name = name
                    self.final = final

                def __eq__(self, other: object) -> bool:
                    if not isinstance(other, Klass):
                        return NotImplemented
                    return self.name == other.name and self.final == other.final

                def __hash__(self) -> int:
                    return hash(self.name)


            class Enum(Base):
                def __init__(self, name: str, scoped: str) -> None:
                    self.name = name
                    self.scoped = scoped

                def __eq__(self, other: object) -> bool:
                    if not isinstance(other, Enum):
                        return NotImplemented
                    return self.name == other.name and self.scoped == other.scoped

                def __hash__(self) -> int:
                    return hash(self.name)
            """).lstrip())
    oracle = _RecordingOracle()
    try:
        engine = UnificationRefactorEngine(
            min_lines=2, reuse_existing_functions=False, type_oracle=oracle
        )
        (proposal,) = engine.analyze_file(str(path))
        with pytest.raises(RefactoringError, match="Every helper annotation variant"):
            engine.apply_refactoring(str(path), proposal)
    finally:
        oracle.close()
    # The generic rung is tried first, since the ordinary signature holds Any;
    # the ladder that ends is the one after it.
    signatures = [
        signature for signature in _helper_signatures(oracle.checked) if "_TowelT" not in signature
    ]
    assert len(signatures) == 2, signatures
    assert signatures[-1].count(": Any") == signatures[-1].count(",") + 1, signatures
