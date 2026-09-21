"""A finished typed run is confirmed once by a checker that shares no warm state.

Every candidate is judged by a language server, which answers when it has gone
quiet. A marker it must publish first keeps silence from being read as a
verdict before it has started, but not at every instant of its reply. The
command line reanalyses from nothing, so one run of it over the finished
project turns any residue of that kind from a silent wrong answer into a loud
one.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import textwrap
from typing import Mapping, Sequence

import pytest

from towel.type_inference import (
    CheckFailure,
    CheckResult,
    CheckSuccess,
    MypyInferrer,
    PyrightOracle,
    TypeDiagnostic,
    served_by_a_language_server,
)
from towel.unification.exceptions import RefactoringError
from towel.unification.refactor_engine import UnificationRefactorEngine

requires_pyright = pytest.mark.skipif(
    importlib.util.find_spec("pyright") is None, reason="pyright absent"
)

SOURCE = textwrap.dedent("""
    def first(value: int) -> int:
        total = value + 1
        doubled = total * 2
        answer = doubled - 3
        return answer


    def second(value: int) -> int:
        total = value + 1
        doubled = total * 2
        answer = doubled - 4
        return answer
    """).lstrip()


def _project(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pyright]\ntypeCheckingMode = "basic"\n', encoding="utf-8"
    )
    path = tmp_path / "m.py"
    path.write_text(SOURCE, encoding="utf-8")
    return path


@requires_pyright
def test_a_warm_session_is_recognised_and_the_command_line_is_not(tmp_path: Path) -> None:
    path = _project(tmp_path)
    warm = PyrightOracle()
    cold = PyrightOracle(language_server=False)
    try:
        assert not served_by_a_language_server(warm), "nothing checked yet"
        warm.check_project({str(path): path.read_text(encoding="utf-8")})
        cold.check_project({str(path): path.read_text(encoding="utf-8")})
        assert served_by_a_language_server(warm)
        assert not served_by_a_language_server(cold)
    finally:
        warm.close()
        cold.close()


@requires_pyright
def test_a_clean_run_is_confirmed_and_says_nothing(tmp_path: Path) -> None:
    path = _project(tmp_path)
    oracle = PyrightOracle()
    try:
        engine = UnificationRefactorEngine(
            min_lines=3, reuse_existing_functions=False, type_oracle=oracle
        )
        _, applied, _ = engine.refactor_to_fixed_point(str(path), progress="none")
    finally:
        oracle.close()
    assert applied >= 1
    assert "_extracted_func" in path.read_text(encoding="utf-8")


class _AgreeableWhileWarm(PyrightOracle):
    """A checker with a blind spot: clean while it has a warm session, not once cold.

    This is the shape of the residue the confirmation exists for. A settle ends
    on silence once the change has been acknowledged, and silence at the wrong
    instant would read as a clean project; nothing else in the run would notice.
    """

    def check_project(
        self, sources: Mapping[str, str], *, excluded_paths: Sequence[str] = ()
    ) -> CheckResult:
        real = super().check_project(sources, excluded_paths=excluded_paths)
        if self._warmed:
            return CheckSuccess()
        invented = TypeDiagnostic(next(iter(sources)), "pyright: invented: missed while warm", 1)
        return CheckSuccess((*getattr(real, "errors", ()), invented))


@requires_pyright
def test_a_run_the_warm_checker_waved_through_fails_loudly_at_the_end(tmp_path: Path) -> None:
    """The cold check is the only thing that can catch a warm checker's blind spot."""
    path = _project(tmp_path)
    oracle = _AgreeableWhileWarm()
    try:
        engine = UnificationRefactorEngine(
            min_lines=3, reuse_existing_functions=False, type_oracle=oracle
        )
        with pytest.raises(RefactoringError, match="did not report while the run was in progress"):
            engine.refactor_to_fixed_point(str(path), progress="none")
    finally:
        oracle.close()


def test_a_run_without_a_warm_session_is_not_checked_again(tmp_path: Path) -> None:
    """Nothing to confirm when no session was used; mypy alone must not pay for it."""
    pytest.importorskip("mypy")
    path = _project(tmp_path)
    oracle = MypyInferrer()
    try:
        assert not served_by_a_language_server(oracle)
        engine = UnificationRefactorEngine(
            min_lines=3, reuse_existing_functions=False, type_oracle=oracle
        )
        _, applied, _ = engine.refactor_to_fixed_point(str(path), progress="none")
    finally:
        oracle.close()
    assert applied >= 1


class _WarmThenUncheckable(PyrightOracle):
    """A session that answers, then a command line that cannot run at all."""

    def check_project(
        self, sources: Mapping[str, str], *, excluded_paths: Sequence[str] = ()
    ) -> CheckResult:
        # Warm checks answer; the cold confirmation at the end cannot run.
        if self._warmed or not self.answered_from_a_session:
            return super().check_project(sources, excluded_paths=excluded_paths)
        return CheckFailure("pyright timed out")


@requires_pyright
def test_a_confirmation_that_could_not_run_is_not_silence(tmp_path: Path) -> None:
    """A check that did not happen has confirmed nothing, and must say so."""
    path = _project(tmp_path)
    oracle = _WarmThenUncheckable()
    try:
        engine = UnificationRefactorEngine(
            min_lines=3, reuse_existing_functions=False, type_oracle=oracle
        )
        with pytest.raises(RefactoringError, match="could not be confirmed"):
            engine.refactor_to_fixed_point(str(path), progress="none")
    finally:
        oracle.close()


class _SessionThatDiesMidRun(PyrightOracle):
    """A session answers once, then is abandoned; the run finishes cold."""

    def check_project(
        self, sources: Mapping[str, str], *, excluded_paths: Sequence[str] = ()
    ) -> CheckResult:
        result = super().check_project(sources, excluded_paths=excluded_paths)
        self.stop_language_servers()
        return result


@requires_pyright
def test_a_run_whose_session_died_is_still_confirmed(tmp_path: Path) -> None:
    """Abandoning a session is exactly when its verdicts most want confirming."""
    path = _project(tmp_path)
    oracle = _SessionThatDiesMidRun()
    try:
        oracle.check_project({str(path): path.read_text(encoding="utf-8")})
        assert not oracle._warmed, "the session was abandoned"
        assert served_by_a_language_server(oracle), "but it did answer, so confirm the run"
    finally:
        oracle.close()
