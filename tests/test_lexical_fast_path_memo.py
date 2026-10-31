# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0.
"""Cached lexical eligibility preserves native diagnostics, bounds and fork safety."""

from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
import os
import re
import signal
import sys
import threading
import traceback
from typing import Literal, Optional
from unittest.mock import patch
import warnings

import pytest

from towel import parsing_warnings as lexical
from towel.analysis_sources import AnalysisSources
from towel.canonical_ast import exact_dump
from towel.unification.bounded_cache import memoization_disabled


class _ObservedPattern:
    def __init__(
        self, pattern: re.Pattern[str], barrier: Optional[threading.Barrier] = None
    ) -> None:
        self.pattern = pattern
        self.barrier = barrier
        self.calls = 0
        self.lock = threading.Lock()

    def search(self, source: str) -> Optional[re.Match[str]]:
        with self.lock:
            self.calls += 1
        if self.barrier is not None:
            self.barrier.wait(timeout=5)
        return self.pattern.search(source)


@pytest.mark.parametrize(
    "source,expected,tokens",
    [
        ("value=17", True, 0),
        ("value=@", True, 0),
        (r"value=r'\d'", True, 1),
        (r"value='\d'", False, 1),
    ],
)
def test_fast_regex_and_token_eligibility_are_cached_in_one_table(
    monkeypatch: pytest.MonkeyPatch, source: str, expected: bool, tokens: int
) -> None:
    cache = lexical._LexicalCache()
    pattern = _ObservedPattern(lexical._POSSIBLE_WARNING)
    monkeypatch.setattr(lexical, "_CACHE", cache)
    monkeypatch.setattr(lexical, "_POSSIBLE_WARNING", pattern)
    with patch.object(lexical, "_tokens_cannot_warn", wraps=lexical._tokens_cannot_warn) as scanned:
        assert [lexical.parsing_cannot_warn(source) for _ in range(3)] == [expected] * 3
        assert pattern.calls == 1 and scanned.call_count == tokens
    assert cache.entries[source].cannot_warn is expected
    assert len(cache.entries) == 1


@pytest.mark.parametrize("mode", ["disabled", "oversized", "byte-budget"])
def test_bypass_repeats_the_same_fast_computation_without_retention(
    monkeypatch: pytest.MonkeyPatch, mode: Literal["disabled", "oversized", "byte-budget"]
) -> None:
    source = "value=@"
    if mode == "oversized":
        source = "# " + "x" * lexical._MAX_SOURCE_CHARACTERS + "\n" + source
    cache = lexical._LexicalCache(
        max_source_bytes=0 if mode == "byte-budget" else lexical._CACHE_SOURCE_BYTES
    )
    pattern = _ObservedPattern(lexical._POSSIBLE_WARNING)
    monkeypatch.setattr(lexical, "_CACHE", cache)
    monkeypatch.setattr(lexical, "_POSSIBLE_WARNING", pattern)
    from contextlib import nullcontext

    with (
        memoization_disabled() if mode == "disabled" else nullcontext(),
        patch.object(
            lexical,
            "_tokens_cannot_warn",
            side_effect=AssertionError("Fast branch must remain fast"),
        ),
    ):
        assert lexical.parsing_cannot_warn(source) and lexical.parsing_cannot_warn(source)
    assert pattern.calls == 2 and not cache.entries


@pytest.mark.parametrize("policy", ["always", "ignore", "error"])
def test_cached_warning_refusal_keeps_native_warning_and_error_precedence(
    monkeypatch: pytest.MonkeyPatch, policy: Literal["always", "ignore", "error"]
) -> None:
    monkeypatch.setattr(lexical, "_CACHE", lexical._LexicalCache())
    source = "value='\\d'\nbroken=(\n"
    sources = AnalysisSources()

    def observed(shared: bool) -> tuple[tuple[object, ...], tuple[tuple[str, str, int], ...]]:
        with warnings.catch_warnings(record=True) as got:
            warnings.simplefilter(policy)
            try:
                tree = (
                    sources.parse(source, "lexical-warning.py")
                    if shared
                    else ast.parse(source, filename="lexical-warning.py")
                )
                result: tuple[object, ...] = ("tree", exact_dump(tree))
            except (SyntaxError, Warning) as error:
                result = (type(error).__name__, error.args)
        return result, tuple((w.category.__name__, str(w.message), w.lineno) for w in got)

    expected = observed(False)
    assert observed(True) == expected and observed(True) == expected
    assert lexical._CACHE.entries[source].cannot_warn is False


@pytest.mark.parametrize("source", ["value=17", "value='λ😀'", "# \ud800\nvalue=17"])
def test_fast_entries_keep_exact_storage_accounting_and_caps(
    monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    cache = lexical._LexicalCache(max_entries=1)
    monkeypatch.setattr(lexical, "_CACHE", cache)
    assert lexical.parsing_cannot_warn(source)
    expected = sys.getsizeof(source) + (0 if source.isascii() else 4 * len(source) + 1)
    assert cache.entries[source].source_bytes == expected
    assert lexical.parsing_cannot_warn("different=18")
    assert len(cache.entries) == 1 and source not in cache.entries
    assert all(type(value.cannot_warn) is bool for value in cache.entries.values())


def test_lexical_boolean_is_independent_of_native_integer_parser_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lexical, "_CACHE", lexical._LexicalCache())
    source = "value=" + "1" * 700
    original = sys.get_int_max_str_digits()
    sources = AnalysisSources()
    try:
        sys.set_int_max_str_digits(0)
        assert lexical.parsing_cannot_warn(source)
        sources.parse(source, "wide-lexical.py")
        sys.set_int_max_str_digits(640)
        assert lexical.parsing_cannot_warn(source)
        with pytest.raises(SyntaxError):
            sources.parse(source, "wide-lexical.py")
        with pytest.raises(SyntaxError):
            ast.parse(source, filename="wide-lexical.py")
    finally:
        sys.set_int_max_str_digits(original)


def test_simultaneous_misses_preserve_one_entry_and_weight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = lexical._LexicalCache()
    pattern = _ObservedPattern(lexical._POSSIBLE_WARNING, threading.Barrier(4))
    monkeypatch.setattr(lexical, "_CACHE", cache)
    monkeypatch.setattr(lexical, "_POSSIBLE_WARNING", pattern)
    source = "value=17"
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(lexical.parsing_cannot_warn, source) for _ in range(4)]
        assert all(future.result(timeout=10) for future in futures)
    assert len(cache.entries) == 1 and pattern.calls == 4
    assert cache.entries._total_weight == cache.entries[source].source_bytes


@pytest.mark.skipif(not hasattr(os, "fork"), reason="Requires native fork")
def test_fork_resets_inherited_locked_lexical_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = lexical._LexicalCache()
    monkeypatch.setattr(lexical, "_CACHE", cache)
    source = "value=17"
    assert lexical.parsing_cannot_warn(source)
    read_fd, write_fd = os.pipe()
    cache.lock.acquire()
    try:
        pid = os.fork()
        if pid == 0:
            status = 1
            os.close(read_fd)
            signal.alarm(5)
            try:
                assert lexical.parsing_cannot_warn(source)
                assert lexical._CACHE is not cache and lexical._CACHE.pid == os.getpid()
                assert os.write(write_fd, b"1") == 1
                status = 0
            except BaseException:
                traceback.print_exc()
                sys.stderr.flush()
            finally:
                os.close(write_fd)
                os._exit(status)
    finally:
        cache.lock.release()
    os.close(write_fd)
    try:
        response = os.read(read_fd, 1)
    finally:
        os.close(read_fd)
        waited, status = os.waitpid(pid, 0)
    assert waited == pid and os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
    assert response == b"1" and lexical._CACHE is cache
