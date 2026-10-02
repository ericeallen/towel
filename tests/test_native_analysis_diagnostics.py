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

"""Analysis observes native diagnostics without borrowing global warning state."""

from __future__ import annotations

import ast
import builtins
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import os
from pathlib import Path
import re
import sys
import threading
from typing import Callable, Iterator, ParamSpec, TypeVar
import warnings

import pytest

from towel import import_model, program_files, reachability, type_baseline
from towel.analysis_sources import memoized_source_analysis
from towel.unification.bounded_cache import memoization_disabled
from towel.unification.import_graph import ImportTimeCode

_P = ParamSpec("_P")
_R = TypeVar("_R")


@contextmanager
def _integer_limit(limit: int) -> Iterator[None]:
    previous = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(limit)
    try:
        yield
    finally:
        sys.set_int_max_str_digits(previous)


def _reader(name: str, path: Path) -> Callable[[], object]:
    source = path.read_text()
    return {
        "imports": lambda: import_model._read_module(path),
        "complaint": lambda: program_files.parse_failure(path),
        "probes": lambda: reachability.probe_plan(source),
        "parsed": lambda: type_baseline._parsed(source),
        "block": lambda: type_baseline._block_statements(source, 1, 3),
        "helper": lambda: type_baseline._written_helper(source, "helper"),
    }[name]


def _delayed(
    function: Callable[_P, _R],
    entered: threading.Event,
    release: threading.Event,
    *,
    filename: str | None = None,
) -> Callable[_P, _R]:
    def run(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        if filename is None or (len(args) > 1 and args[1] == filename):
            entered.set()
            assert release.wait(5), "analysis did not release the native operation"
        return function(*args, **kwargs)

    return run


@pytest.mark.parametrize("reader", ["imports", "complaint", "probes", "parsed", "block", "helper"])
def test_parsing_cannot_swallow_an_unrelated_threads_warning(
    reader: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "module.py"
    # The distinct comment keeps process-wide caches from bypassing the paused parse.
    path.write_text(f"# {reader}: {tmp_path}\nvalue = 1\n")
    action = _reader(reader, path)
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(ast, "parse", _delayed(ast.parse, entered, release))
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always")
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(action)
            assert entered.wait(5), "analysis did not reach native parsing"
            try:
                warnings.warn("unrelated external diagnostic", UserWarning)
            finally:
                release.set()
            pending.result(timeout=5)
    assert [str(item.message) for item in emitted] == ["unrelated external diagnostic"]


def test_probe_compilation_cannot_swallow_an_unrelated_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(
        builtins, "compile", _delayed(builtins.compile, entered, release, filename="<probes>")
    )
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always")
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(reachability.probe_plan, "value = 1\n")
            assert entered.wait(5)
            try:
                warnings.warn("during compilation", UserWarning)
            finally:
                release.set()
            assert pending.result(timeout=5) is not None
    assert [str(item.message) for item in emitted] == ["during compilation"]


@pytest.mark.parametrize("reader", ["imports", "complaint", "probes", "parsed", "block", "helper"])
def test_warm_and_uncached_readers_preserve_native_warning_policy(
    reader: str, tmp_path: Path
) -> None:
    path = tmp_path / "module.py"
    path.write_text("def helper():\n    return '\\q'\n")
    action = _reader(reader, path)
    counts = []
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always")
        for _ in range(2):
            before = len(emitted)
            action()
            counts.append(len(emitted) - before)
        with memoization_disabled():
            before = len(emitted)
            action()
            counts.append(len(emitted) - before)
        assert counts[0] > 0
        assert len(set(counts)) == 1
        warnings.simplefilter("error")
        rejected = action()
        if reader == "imports":
            assert isinstance(rejected, import_model._Module) and rejected.sites is None
        elif reader == "complaint":
            assert isinstance(rejected, program_files.UnparsedFile)
            assert "invalid escape" in rejected.complaint
        elif reader == "block":
            assert rejected == ()
        else:
            assert rejected is None


@pytest.mark.parametrize(
    "source",
    [
        "value = 1 is 2\n",
        "assert (1, 2)\n",
        "value = 1()\n",
        "value = 1[0]\n",
        "value = [1]['x']\n",
    ],
)
def test_probe_compiler_warnings_survive_a_warm_prepared_plan(source: str) -> None:
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always")
        assert reachability.probe_plan(source) is not None
        first = [str(item.message) for item in emitted]
        emitted.clear()
        assert reachability.probe_plan(source) is not None
        assert [str(item.message) for item in emitted] == first
        assert first
        warnings.simplefilter("error")
        assert reachability.probe_plan(source) is None


@pytest.mark.parametrize("reader", ["complaint", "probes", "parsed", "block", "helper"])
def test_derived_source_caches_follow_the_current_integer_limit(
    reader: str, tmp_path: Path
) -> None:
    path = tmp_path / "module.py"
    path.write_text("def helper():\n    return " + "9" * 1000 + "\n")
    action = _reader(reader, path)
    with _integer_limit(0):
        before = action()
        assert before is None if reader == "complaint" else bool(before)
    with _integer_limit(640):
        after = action()
        with memoization_disabled():
            fresh = action()
        assert after == fresh
        assert after is not None if reader == "complaint" else not after


def test_complaints_follow_current_content_even_when_stat_is_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "module.py"
    path.write_bytes(b"value = 1\n")
    status = path.stat()
    assert program_files.parse_failure(path) is None
    path.write_bytes(b"value = ]\n")
    os.utime(path, ns=(status.st_atime_ns, status.st_mtime_ns))
    assert path.stat().st_size == status.st_size
    assert path.stat().st_ino == status.st_ino
    failure = program_files.parse_failure(path)
    assert failure is not None and "unmatched" in failure.complaint


def test_source_memo_is_bounded_and_obeys_disabled_mode() -> None:
    calls = []

    @memoized_source_analysis(maxsize=2)
    def measured(source: str, count: int, *, suffix: str = "") -> str:
        calls.append((source, count, suffix))
        return source * count + suffix

    assert measured("a", 2, suffix="!") == "aa!"
    assert measured("a", 2, suffix="!") == "aa!"
    assert len(calls) == 1
    with memoization_disabled():
        assert measured("a", 2, suffix="!") == "aa!"
    assert len(calls) == 2
    measured("b", 1)
    measured("c", 1)
    measured("a", 2, suffix="!")
    assert len(calls) == 5


def _pattern(pattern: str, flags: str = "0") -> tuple[ImportTimeCode, ast.Call]:
    source = f"import re\npattern = re.compile({pattern!r}, {flags})\n"
    analyzer = ImportTimeCode(source)
    statement = ast.parse(source).body[1]
    assert isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Call)
    return analyzer, statement.value


@pytest.mark.parametrize("pattern", ["[[]", "[a--b]", "[a&&b]", "[a~~b]", "[a||b]"])
def test_regex_warning_classification_does_not_depend_on_resident_re_cache(pattern: str) -> None:
    analyzer, call = _pattern(pattern)
    re.purge()
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always")
        assert not analyzer._quiet_pattern(call, 1, None)
        assert not emitted
        try:
            re.compile(pattern)
        except re.error:
            pass
        assert emitted
        emitted.clear()
        assert not analyzer._quiet_pattern(call, 1, None)
        assert not emitted


def test_regex_debug_is_never_executed_or_classified_as_quiet(
    capsys: pytest.CaptureFixture[str],
) -> None:
    analyzer, call = _pattern("a+", "re.DEBUG")
    assert not analyzer._quiet_pattern(call, 1, None)
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("flag", ["re.TEMPLATE", "re.T", "1"])
def test_obsolete_regex_template_flag_is_rejected_cold_and_warm(flag: str) -> None:
    analyzer, call = _pattern("template_flag_probe", flag)
    re.purge()
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("error")
        assert not analyzer._quiet_pattern(call, 1, None)
        # Warm the stdlib cache independently. CPython 3.11 warns on this flag.
        warnings.simplefilter("ignore", DeprecationWarning)
        re.compile("template_flag_probe", 1)
        warnings.simplefilter("error")
        assert not analyzer._quiet_pattern(call, 1, None)
        assert not emitted


def test_quiet_regex_cannot_capture_an_unrelated_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    analyzer, call = _pattern("a+cache_warning_probe")
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(re, "compile", _delayed(re.compile, entered, release))
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always")
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(analyzer._quiet_pattern, call, 1, None)
            assert entered.wait(5)
            try:
                warnings.warn("during regex compilation", UserWarning)
            finally:
                release.set()
            assert pending.result(timeout=5)
    assert [str(item.message) for item in emitted] == ["during regex compilation"]
