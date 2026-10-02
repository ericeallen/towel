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

"""Repeated lexical proofs stay bounded, concurrent, and independent of forked locks."""

import ast
from concurrent.futures import ThreadPoolExecutor
import multiprocessing
from multiprocessing.connection import Connection
import os
import sys
import threading

import pytest

from towel import parsing_warnings
from towel.unification.bounded_cache import memoization_disabled


@pytest.fixture
def tokenized(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    monkeypatch.setattr(parsing_warnings, "_CACHE", parsing_warnings._LexicalCache())
    original = parsing_warnings._tokens_cannot_warn
    seen: list[str] = []

    def observe(source: str) -> bool:
        seen.append(source)
        return original(source)

    monkeypatch.setattr(parsing_warnings, "_tokens_cannot_warn", observe)
    return seen


def test_repeated_source_corpus_does_not_thrash_a_tiny_entry_limit(tokenized: list[str]) -> None:
    # The observed production prefix used 219 distinct source texts. Keep a
    # similar breadth and several MiB here without depending on its sources.
    sources = [f"# {index} " + "x" * 15000 + "\nvalue = r'\\d'\n" for index in range(219)]
    assert sum(sys.getsizeof(source) for source in sources) < parsing_warnings._CACHE_SOURCE_BYTES
    assert all(map(parsing_warnings.parsing_cannot_warn, sources))
    equal_texts = [source.encode().decode() for source in sources]
    assert all(left == right and left is not right for left, right in zip(sources, equal_texts))
    assert all(map(parsing_warnings.parsing_cannot_warn, equal_texts))
    assert tokenized == sources


def test_source_byte_budget_evicts_least_recently_used_text(
    monkeypatch: pytest.MonkeyPatch, tokenized: list[str]
) -> None:
    sources = [f"value{index} = r'\\d'" for index in range(3)]
    budget = 2 * sys.getsizeof(sources[0])
    cache = parsing_warnings._LexicalCache(max_source_bytes=budget)
    monkeypatch.setattr(parsing_warnings, "_CACHE", cache)
    for source in (sources[0], sources[1], sources[0], sources[2], sources[0], sources[1]):
        assert parsing_warnings.parsing_cannot_warn(source)
    assert tokenized == [sources[0], sources[1], sources[2], sources[1]]
    assert sum(cache.entries[source].source_bytes for source in tuple(cache.entries)) <= budget


def test_unicode_storage_includes_a_later_native_parser_utf8_buffer(
    monkeypatch: pytest.MonkeyPatch, tokenized: list[str]
) -> None:
    source = "value = r'\\d" + "😀" * 100 + "'"
    cache = parsing_warnings._LexicalCache(max_source_bytes=2000)
    monkeypatch.setattr(parsing_warnings, "_CACHE", cache)
    assert parsing_warnings.parsing_cannot_warn(source)
    stored = cache.entries[source].source_bytes
    ast.parse(source)
    assert sys.getsizeof(source) <= stored <= 2000
    assert parsing_warnings.parsing_cannot_warn(source)
    assert tokenized == [source]


def test_one_overweight_source_is_not_retained(
    monkeypatch: pytest.MonkeyPatch, tokenized: list[str]
) -> None:
    source = "value = r'\\d'"
    cache = parsing_warnings._LexicalCache(max_source_bytes=sys.getsizeof(source) - 1)
    monkeypatch.setattr(parsing_warnings, "_CACHE", cache)
    assert parsing_warnings.parsing_cannot_warn(source)
    assert parsing_warnings.parsing_cannot_warn(source)
    assert tokenized == [source, source]
    assert not cache.entries


def test_false_answers_reuse_but_changed_text_and_no_memo_recompute(tokenized: list[str]) -> None:
    warning = "value = '\\d'"
    safe = "value = r'\\d'"
    assert not parsing_warnings.parsing_cannot_warn(warning)
    assert not parsing_warnings.parsing_cannot_warn(warning)
    with memoization_disabled():
        assert not parsing_warnings.parsing_cannot_warn(warning)
        assert parsing_warnings.parsing_cannot_warn(safe)
    assert parsing_warnings.parsing_cannot_warn(safe)
    assert not parsing_warnings.parsing_cannot_warn(warning)
    assert tokenized == [warning, warning, safe, safe]


def test_concurrent_misses_and_evictions_keep_answers_and_storage_consistent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = parsing_warnings._LexicalCache(max_entries=8, max_source_bytes=1024)
    monkeypatch.setattr(parsing_warnings, "_CACHE", cache)
    original = parsing_warnings._tokens_cannot_warn
    simultaneous = threading.Barrier(4)

    def simultaneous_miss(source: str) -> bool:
        simultaneous.wait(timeout=5)
        return original(source)

    monkeypatch.setattr(parsing_warnings, "_tokens_cannot_warn", simultaneous_miss)
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert all(pool.map(parsing_warnings.parsing_cannot_warn, ["value = r'\\d'"] * 4))
        monkeypatch.setattr(parsing_warnings, "_tokens_cannot_warn", original)
        cases = [
            (f"value{index} = {'r' if index % 2 else ''}'\\d'", bool(index % 2))
            for index in range(64)
        ]
        sources, expected = zip(*(cases * 8))
        assert tuple(pool.map(parsing_warnings.parsing_cannot_warn, sources)) == expected
    assert len(cache.entries) <= 8
    assert cache.entries._total_weight == sum(
        cache.entries[source].source_bytes for source in tuple(cache.entries)
    )
    assert cache.entries._total_weight <= 1024


def _read_in_fork(connection: Connection) -> None:
    try:
        answer = parsing_warnings.parsing_cannot_warn("child = r'\\d'")
        connection.send((answer, os.getpid(), parsing_warnings._CACHE.pid))
    finally:
        connection.close()


@pytest.mark.skipif("fork" not in multiprocessing.get_all_start_methods(), reason="requires fork")
def test_fork_discards_an_inherited_locked_parent_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = parsing_warnings._LexicalCache()
    monkeypatch.setattr(parsing_warnings, "_CACHE", cache)
    assert parsing_warnings.parsing_cannot_warn("parent = r'\\d'")
    context = multiprocessing.get_context("fork")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(target=_read_in_fork, args=(send,))
    with cache.lock:
        process.start()
        send.close()
        try:
            assert receive.poll(5), "child did not replace the inherited locked cache"
            assert receive.recv() == (True, process.pid, process.pid)
            process.join(timeout=5)
            assert process.exitcode == 0
        finally:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            receive.close()
    assert parsing_warnings._CACHE is cache
    assert tuple(cache.entries) == ("parent = r'\\d'",)
