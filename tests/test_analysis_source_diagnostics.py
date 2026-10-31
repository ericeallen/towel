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

"""Shared syntax preserves native diagnostics without changing global warning policy."""

from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import threading
from typing import Callable, Literal
import warnings

import pytest

from towel import analysis_sources
from towel.analysis_sources import AnalysisSources
from towel.unification.module_bindings import global_bindings
from towel.unification.pipeline import AnalysisSession, SourceFileError
from towel.unification.refactor_engine import UnificationRefactorEngine

WARNING_SOURCES = [
    "value = '\\d'",
    "value = b'\\u1234'",
    "value = f'\\{1}'",
    "value = '\\400'",
    "value = '\\777'",
    "value = 1if True else 0",
    "value = 1.and 2",
    "value = [1for item in ()]",
    "value = 1e1if True else 0",
    "value = 1jif True else 0",
    "value = 0x1if True else 0",
    "value = '\\d'\nbroken =",
]


@pytest.mark.parametrize("source", WARNING_SOURCES)
@pytest.mark.parametrize("policy", ["always", "error", "ignore"])
def test_warning_capable_syntax_always_uses_native_diagnostics(
    source: str, policy: Literal["always", "error", "ignore"]
) -> None:
    def observe(
        parse: Callable[[str], ast.Module],
    ) -> tuple[str, tuple[tuple[str, str, str, int], ...]]:
        with warnings.catch_warnings(record=True) as emitted:
            warnings.simplefilter(policy)
            try:
                answer = ast.dump(parse(source), include_attributes=True)
            except SyntaxError as error:
                answer = repr(error.args)
            return answer, tuple(
                (warning.category.__name__, str(warning.message), warning.filename, warning.lineno)
                for warning in emitted
            )

    expected = observe(lambda text: ast.parse(text, filename="warning.py"))
    sources = AnalysisSources()
    for _ in range(2):
        assert observe(lambda text: sources.parse(text, "warning.py")) == expected


def test_unknown_parser_versions_parse_fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(analysis_sources, "_KNOWN_WARNING_SITES", False)
    sources = AnalysisSources()
    first = sources.parse("value = 1", "module.py")
    assert sources.parse("value = 1", "module.py") is not first


def test_concurrent_parses_leave_warning_filters_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the nesting order that previously left one catch_warnings scope installed."""
    entered_first, entered_second, completed_first = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    native_parse = ast.parse

    def scheduled_parse(source: str | bytes, filename: str = "<unknown>") -> ast.Module:
        if filename == "first.py":
            entered_first.set()
            assert entered_second.wait(5)
        else:
            assert entered_first.wait(5)
            entered_second.set()
            assert completed_first.wait(5)
        return native_parse(source, filename=filename)

    def first() -> ast.Module:
        try:
            return AnalysisSources().parse("first = 1", "first.py")
        finally:
            completed_first.set()

    monkeypatch.setattr(ast, "parse", scheduled_parse)
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("error")
        filters = warnings.filters[:]
        with ThreadPoolExecutor(max_workers=2) as threads:
            left = threads.submit(first)
            right = threads.submit(AnalysisSources().parse, "second = 2", "second.py")
            assert len(left.result(timeout=10).body) == len(right.result(timeout=10).body) == 1
        assert warnings.filters == filters
        with pytest.raises(UserWarning, match="after parsing"):
            warnings.warn("after parsing", UserWarning)
        assert not emitted


def test_parsing_does_not_capture_an_unrelated_threads_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, released = threading.Event(), threading.Event()
    native_parse = ast.parse

    def scheduled_parse(source: str | bytes, filename: str = "<unknown>") -> ast.Module:
        entered.set()
        assert released.wait(5)
        return native_parse(source, filename=filename)

    monkeypatch.setattr(ast, "parse", scheduled_parse)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with ThreadPoolExecutor(max_workers=1) as threads:
            parsed = threads.submit(AnalysisSources().parse, "value = 1", "module.py")
            try:
                assert entered.wait(5)
                with pytest.raises(UserWarning, match="outside the parser"):
                    warnings.warn("outside the parser", UserWarning)
            finally:
                released.set()
            assert len(parsed.result(timeout=10).body) == 1


def test_integer_limit_changes_invalidate_each_source_parse_cache(tmp_path: Path) -> None:
    original = sys.get_int_max_str_digits()
    source = "number = " + "9" * 1000 + "\n"
    path = tmp_path / "large.py"
    path.write_text(source)
    sources, session, engine = AnalysisSources(), AnalysisSession(), UnificationRefactorEngine()
    try:
        sys.set_int_max_str_digits(0)
        sources.parse(source, str(path))
        session.analyze_module(str(path))
        engine._parse_source(source)
        assert global_bindings(source) is not None
        sys.set_int_max_str_digits(640)
        with pytest.raises(SyntaxError):
            ast.parse(source)
        with pytest.raises(SyntaxError):
            sources.parse(source, str(path))
        assert not session.reusable(str(path))
        with pytest.raises(SourceFileError):
            session.analyze_module(str(path))
        with pytest.raises(SyntaxError):
            engine._parse_source(source)
        assert global_bindings(source) is None
        sys.set_int_max_str_digits(0)
        assert global_bindings(source) is not None
        assert session.analyze_module(str(path)).module.source == source
    finally:
        sys.set_int_max_str_digits(original)


def test_changed_recursion_limit_rechecks_analysis_depth(tmp_path: Path) -> None:
    original = sys.getrecursionlimit()
    path = tmp_path / "deep.py"
    path.write_text("number = " + "+".join("1" for _ in range(500)))
    session = AnalysisSession()
    try:
        sys.setrecursionlimit(10000)
        session.analyze_module(str(path))
        sys.setrecursionlimit(300)
        with pytest.raises(SourceFileError, match="nests too deeply"):
            AnalysisSession().analyze_module(str(path))
        with pytest.raises(SourceFileError, match="nests too deeply"):
            session.analyze_module(str(path))
    finally:
        sys.setrecursionlimit(original)


@pytest.mark.parametrize("reader", ["bindings", "engine", "session"])
def test_front_caches_follow_native_warning_policy(reader: str, tmp_path: Path) -> None:
    # A distinct source prevents a previous binding-cache entry from deciding
    # whether the first warning-as-error failure poisons later successful parses.
    source = f"# {tmp_path}\nvalue = '\\q'\n"
    path = tmp_path / "warning.py"
    path.write_text(source)
    engine, session = UnificationRefactorEngine(), AnalysisSession()
    action: Callable[[], object] = {
        "bindings": lambda: global_bindings(source),
        "engine": lambda: engine._parse_source(source),
        "session": lambda: session.analyze_module(str(path)),
    }[reader]
    policies: tuple[Literal["error", "ignore", "always"], ...] = (
        "error",
        "ignore",
        "always",
        "always",
        "error",
        "ignore",
    )
    with warnings.catch_warnings(record=True) as emitted:
        for policy in policies:
            warnings.simplefilter(policy)
            emitted.clear()
            if policy == "error":
                if reader == "bindings":
                    assert action() is None
                else:
                    error = SyntaxError if reader == "engine" else SourceFileError
                    with pytest.raises(error, match="invalid escape"):
                        action()
            else:
                assert action() is not None
            assert len(emitted) == (1 if policy == "always" else 0)
            assert not session.reusable(str(path))
            assert session.entry_count == 0


def test_warning_free_front_caches_still_reuse_their_results(tmp_path: Path) -> None:
    source = "quiet_front_cache_value = 1\n"
    path = tmp_path / "quiet.py"
    path.write_text(source)
    engine, session = UnificationRefactorEngine(), AnalysisSession()
    assert global_bindings(source) is global_bindings(source)
    assert engine._parse_source(source) is engine._parse_source(source)
    assert session.analyze_module(str(path)) is session.analyze_module(str(path))
    assert session.reusable(str(path))
