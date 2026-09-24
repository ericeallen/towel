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

"""A finished typed run is confirmed once by a checker that shares no warm state.

Every candidate is judged by a language server, which answers when it has gone
quiet. A marker it must publish first keeps silence from being read as a
verdict before it has started, but not at every instant of its reply. The
command line reanalyses from nothing, so one run of it over the finished
project turns any residue of that kind from a silent wrong answer into a loud
one. mypy keeps state across a run too, its incremental cache and its scan of
what imports the change, and a mypy run is confirmed the same way, from an
empty cache and a fresh scan.
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
    holds_warm_state,
    served_by_a_language_server,
    start_cold,
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


def _with_the_change(sources: Mapping[str, str]) -> bool:
    return any("_extracted_func" in text for text in sources.values())


class _AgreeableWhileWarm(PyrightOracle):
    """A checker with a blind spot: clean while it has a warm session, not once cold.

    This is the shape of the residue the confirmation exists for. A settle ends
    on silence once the change has been acknowledged, and silence at the wrong
    instant would read as a clean project; nothing else in the run would notice.
    The error it misses is one the change brought: one the original has too,
    cold, is the checker's mode and not the run's doing, and is excused.
    """

    def check_project(
        self, sources: Mapping[str, str], *, excluded_paths: Sequence[str] = ()
    ) -> CheckResult:
        real = super().check_project(sources, excluded_paths=excluded_paths)
        if self._warmed:
            return CheckSuccess()
        if not _with_the_change(sources):
            return real
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


def _mypy_project(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n", encoding="utf-8")
    path = tmp_path / "m.py"
    path.write_text(SOURCE, encoding="utf-8")
    return path


class _CountingMypy(MypyInferrer):
    """Counts the checks it answers after it was asked to forget what it knew."""

    def __init__(self) -> None:
        super().__init__()
        self.cold_checks = 0
        self.forgotten = False

    def forget_warm_state(self) -> None:
        super().forget_warm_state()
        self.forgotten = True

    def check_project(
        self, sources: Mapping[str, str], *, excluded_paths: Sequence[str] = ()
    ) -> CheckResult:
        self.cold_checks += self.forgotten
        return super().check_project(sources, excluded_paths=excluded_paths)


def test_a_mypy_run_is_confirmed_once_from_nothing(tmp_path: Path) -> None:
    """mypy keeps state across a run as well: its cache, the consumer scan.

    An entry written from supplied text, or a scan that has not followed the
    tree, answers for a project that is no longer there, and every verdict of
    the run rests on them. One check that starts from an empty cache and a
    fresh scan shares none of that.
    """
    pytest.importorskip("mypy")
    path = _mypy_project(tmp_path)
    oracle = _CountingMypy()
    try:
        assert holds_warm_state(oracle) is False, "nothing checked yet"
        engine = UnificationRefactorEngine(
            min_lines=3, reuse_existing_functions=False, type_oracle=oracle
        )
        _, applied, _ = engine.refactor_to_fixed_point(str(path), progress="none")
    finally:
        oracle.close()
    assert applied >= 1
    assert oracle.cold_checks == 1


def test_forgetting_leaves_a_mypy_oracle_with_nothing_it_learned(tmp_path: Path) -> None:
    pytest.importorskip("mypy")
    path = _mypy_project(tmp_path)
    oracle = MypyInferrer()
    try:
        oracle.check_project({str(path): path.read_text(encoding="utf-8")})
        assert holds_warm_state(oracle)
        warm_cache = oracle._cache_dir
        assert any(warm_cache.iterdir()), "the check wrote its incremental cache"
        start_cold(oracle)
        assert not holds_warm_state(oracle)
        assert oracle._process is None, "the worker and its imports went"
        assert oracle._cache_dir != warm_cache and not any(oracle._cache_dir.iterdir())
        assert not warm_cache.exists(), "an owned cache is removed, not left behind"
        assert oracle._import_scans == {}
        result = oracle.check_project({str(path): path.read_text(encoding="utf-8")})
        assert isinstance(result, CheckSuccess) and result.errors == ()
    finally:
        oracle.close()


class _MypyAgreeableWhileWarm(MypyInferrer):
    """mypy with a blind spot in its warm state: clean until it forgets, about the change."""

    def __init__(self) -> None:
        super().__init__()
        self.forgotten = False

    def forget_warm_state(self) -> None:
        super().forget_warm_state()
        self.forgotten = True

    def check_project(
        self, sources: Mapping[str, str], *, excluded_paths: Sequence[str] = ()
    ) -> CheckResult:
        real = super().check_project(sources, excluded_paths=excluded_paths)
        if not self.forgotten or not _with_the_change(sources):
            return real
        invented = TypeDiagnostic(next(iter(sources)), "mypy: invented: missed while warm", 1)
        return CheckSuccess((*getattr(real, "errors", ()), invented))


def test_a_mypy_run_its_warm_state_waved_through_fails_loudly_at_the_end(tmp_path: Path) -> None:
    pytest.importorskip("mypy")
    path = _mypy_project(tmp_path)
    oracle = _MypyAgreeableWhileWarm()
    try:
        engine = UnificationRefactorEngine(
            min_lines=3, reuse_existing_functions=False, type_oracle=oracle
        )
        with pytest.raises(RefactoringError, match="did not report while the run was in progress"):
            engine.refactor_to_fixed_point(str(path), progress="none")
    finally:
        oracle.close()


def test_a_run_that_applied_nothing_is_not_checked_again(tmp_path: Path) -> None:
    """Nothing changed, so the baseline, itself a cold check, already said it all."""
    pytest.importorskip("mypy")
    path = tmp_path / "m.py"
    path.write_text("def only(value: int) -> int:\n    return value\n", encoding="utf-8")
    oracle = _CountingMypy()
    try:
        engine = UnificationRefactorEngine(
            min_lines=3, reuse_existing_functions=False, type_oracle=oracle
        )
        _, applied, _ = engine.refactor_to_fixed_point(str(path), progress="none")
    finally:
        oracle.close()
    assert applied == 0
    assert oracle.cold_checks == 0 and not oracle.forgotten


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
