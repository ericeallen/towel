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

"""Sharing syntax must preserve file identity, current source and cache lifetime."""

from __future__ import annotations

import ast
import gc
from pathlib import Path
from types import MappingProxyType
from typing import Literal
from unittest.mock import patch
import weakref
import warnings

import pytest

from towel.analysis_sources import AnalysisSources, parse_analysis_source, sharing_analysis_sources
from towel import import_model
from towel.source_text import decode_source
from towel.unification.bounded_cache import memoization_disabled
from towel.unification.decorator_reach import _Resolver
from towel.unification.import_graph import ImportGraphCache, _module_level_import_bindings
from towel.unification.module_bindings import bindings_from_tree
from towel.unification.namespace_writes import _file_writes


def test_readers_share_one_parse_and_leave_its_tree_unchanged(tmp_path: Path) -> None:
    """Different safety analyses previously parsed the same file independently."""
    path = tmp_path / "subject.py"
    source = "import math\nclass Subject: pass\nalias = Subject\n"
    path.write_text(source)
    with sharing_analysis_sources(check_ast_immutable=True):
        with patch.object(ast, "parse", wraps=ast.parse) as parses:
            resolver = _Resolver(ImportGraphCache())
            loaded = resolver._load(str(path))
            assert loaded is not None
            before = ast.dump(loaded.tree, include_attributes=True)
            imports = import_model._read_module(path)
            writes = _file_writes(path, tmp_path, None)
            bindings = _module_level_import_bindings(path, ImportGraphCache())
            assert imports.sites is not None and imports.sites[0].module == "math"
            assert writes is not None and not writes.by_name and not writes.by_path
            assert bindings == {"math": ("math",)}
            assert parse_analysis_source(source, str(path)) is loaded.tree
            assert ast.dump(loaded.tree, include_attributes=True) == before
            assert parses.call_count == 1


def test_file_identity_source_and_working_directory_are_part_of_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    with sharing_analysis_sources():
        monkeypatch.chdir(first)
        tree = parse_analysis_source("value = 1", "subject.py")
        assert tree is parse_analysis_source("value = 1", "subject.py")
        assert tree is not parse_analysis_source("value = 2", "subject.py")
        assert tree is not parse_analysis_source("value = 1", "another.py")
        monkeypatch.chdir(second)
        assert tree is not parse_analysis_source("value = 1", "subject.py")


@pytest.mark.parametrize(
    "data",
    [b"value = 1\r\n", b"\xef\xbb\xbfvalue = 1\n", b"# coding: latin-1\nvalue = '\xe9'\n"],
)
def test_encoded_and_decoded_source_share_equivalent_syntax(data: bytes) -> None:
    text = decode_source(data)
    expected = ast.dump(ast.parse(data), include_attributes=True)
    with sharing_analysis_sources():
        from_bytes = parse_analysis_source(data, "encoded.py")
        from_text = parse_analysis_source(text, "encoded.py")
        assert from_bytes is from_text
        assert ast.dump(from_text, include_attributes=True) == expected


@pytest.mark.parametrize(
    "source",
    [
        "def broken(",
        b"# coding: unknown-encoding\nx = 1",
        b"x='\xff'",
        b"# coding: base64_codec\nvalue = 1\n",
        b"# coding: hex_codec\nvalue = 1\n",
        b"# coding: rot_13\nvalue = 1\n",
    ],
)
def test_invalid_source_keeps_python_diagnostics(source: str | bytes) -> None:
    with pytest.raises(SyntaxError) as expected:
        ast.parse(source, filename="broken.py")
    with sharing_analysis_sources():
        for _ in range(2):
            with pytest.raises(SyntaxError) as actual:
                parse_analysis_source(source, "broken.py")
            assert actual.value.args == expected.value.args


def test_nested_contexts_and_disabled_memos_do_not_leak_results() -> None:
    with sharing_analysis_sources():
        outer = parse_analysis_source("value = 1", "subject.py")
        with sharing_analysis_sources():
            inner = parse_analysis_source("value = 1", "subject.py")
            assert inner is not outer
        assert parse_analysis_source("value = 1", "subject.py") is outer
        with memoization_disabled():
            assert parse_analysis_source("value = 1", "subject.py") is not outer
        assert parse_analysis_source("value = 1", "subject.py") is outer
    assert parse_analysis_source("value = 1", "subject.py") is not outer


def test_eviction_and_context_exit_release_trees_and_import_facts() -> None:
    sources = AnalysisSources(max_entries=1)
    first = sources.parse("import math", "first.py")
    import_model._scan(first, Path("first.py"))
    reference = weakref.ref(first)
    del first
    sources.parse("import sys", "second.py")
    gc.collect()
    assert reference() is None

    with sharing_analysis_sources():
        tree = parse_analysis_source("import pathlib", "third.py")
        import_model._scan(tree, Path("third.py"))
        reference = weakref.ref(tree)
        del tree
        assert reference() is not None
    gc.collect()
    assert reference() is None


def test_oversized_sources_are_not_retained() -> None:
    sources = AnalysisSources(max_source_bytes=2)
    first = sources.parse("value = 1", "subject.py")
    assert sources.parse("value = 1", "subject.py") is not first


def test_mutation_diagnostic_catches_a_changed_shared_tree() -> None:
    sources = AnalysisSources(check_ast_immutable=True)
    tree = sources.parse("value = 1", "subject.py")
    tree.body.clear()
    with pytest.raises(RuntimeError, match="mutated the cached AST"):
        sources.parse("value = 1", "subject.py")


def test_mutation_diagnostic_also_checks_source_positions() -> None:
    sources = AnalysisSources(check_ast_immutable=True)
    tree = sources.parse("value = 1", "subject.py")
    tree.body[0].lineno = 999
    with pytest.raises(RuntimeError, match="mutated the cached AST"):
        sources.parse("value = 1", "subject.py")


def test_mutation_diagnostic_distinguishes_boolean_and_integer_constants() -> None:
    sources = AnalysisSources(check_ast_immutable=True)
    tree = sources.parse("value = 1", "subject.py")
    assignment = tree.body[0]
    assert isinstance(assignment, ast.Assign)
    assert isinstance(assignment.value, ast.Constant)
    assignment.value.value = True
    with pytest.raises(RuntimeError, match="mutated the cached AST"):
        sources.parse("value = 1", "subject.py")


def test_immutability_checks_accept_deep_parseable_expressions() -> None:
    source = "value = " + " + ".join("1" for _ in range(1500))
    sources = AnalysisSources(check_ast_immutable=True)
    tree = sources.parse(source, "deep.py")
    assert sources.parse(source, "deep.py") is tree


def test_warning_policy_and_repeated_warnings_survive_a_warm_cache() -> None:
    source = "value = '\\d'"
    with sharing_analysis_sources():
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            parse_analysis_source(source, "warning.py")
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            with pytest.raises(SyntaxError) as expected:
                ast.parse(source, filename="warning.py")
            with pytest.raises(SyntaxError) as actual:
                parse_analysis_source(source, "warning.py")
            assert actual.value.args == expected.value.args
        with warnings.catch_warnings(record=True) as emitted:
            warnings.simplefilter("always")
            parse_analysis_source(source, "warning.py")
            parse_analysis_source(source, "warning.py")
        assert len(emitted) == 2


@pytest.mark.parametrize("policy", ["always", "error"])
def test_syntax_errors_keep_preceding_warnings_and_their_error_precedence(
    policy: Literal["always", "error"],
) -> None:
    source = "value = '\\d'\nbroken ="
    with warnings.catch_warnings(record=True) as expected_warnings:
        warnings.simplefilter(policy)
        with pytest.raises(SyntaxError) as expected:
            ast.parse(source, filename="broken.py")
    with sharing_analysis_sources(), warnings.catch_warnings(record=True) as actual_warnings:
        warnings.simplefilter(policy)
        with pytest.raises(SyntaxError) as actual:
            parse_analysis_source(source, "broken.py")
    assert actual.value.args == expected.value.args
    assert [(w.category, str(w.message)) for w in actual_warnings] == [
        (w.category, str(w.message)) for w in expected_warnings
    ]


@pytest.mark.parametrize("policy", ["always", "error", "ignore"])
def test_encoded_source_preserves_decode_warning_policy(
    policy: Literal["always", "error", "ignore"],
) -> None:
    source = b"# coding: unicode_escape\nvalue = '\\d'\n"
    sources = AnalysisSources()

    def read(
        shared: bool,
    ) -> tuple[ast.Module | SyntaxError | Warning, tuple[warnings.WarningMessage, ...]]:
        with warnings.catch_warnings(record=True) as emitted:
            warnings.simplefilter(policy)
            try:
                tree = (
                    sources.parse(source, "encoded.py")
                    if shared
                    else ast.parse(source, filename="encoded.py")
                )
                return tree, tuple(emitted)
            except (SyntaxError, Warning) as error:
                return error, tuple(emitted)

    expected, expected_warnings = read(False)
    for _ in range(2):
        actual, actual_warnings = read(True)
        if isinstance(expected, (SyntaxError, Warning)):
            assert isinstance(actual, (SyntaxError, Warning))
            assert type(actual) is type(expected)
            assert actual.args == expected.args
        else:
            assert isinstance(actual, ast.Module)
            assert ast.dump(actual, include_attributes=True) == ast.dump(
                expected, include_attributes=True
            )
        assert [(w.category, str(w.message), w.filename, w.lineno) for w in actual_warnings] == [
            (w.category, str(w.message), w.filename, w.lineno) for w in expected_warnings
        ]


def test_import_summaries_keep_paths_and_reuse_pure_work() -> None:
    tree = ast.parse("import math")
    with patch.object(import_model, "_scan_tree", wraps=import_model._scan_tree) as scans:
        first = import_model._scan(tree, Path("first.py"))
        assert import_model._scan(tree, Path("first.py")) is first
        second = import_model._scan(tree, Path("second.py"))
        assert first.sites is not None and second.sites is not None
        assert first.sites[0].file == Path("first.py")
        assert second.sites[0].file == Path("second.py")
        assert scans.call_count == 2
        with memoization_disabled():
            assert import_model._scan(tree, Path("first.py")) == first
        assert scans.call_count == 3


def test_binding_results_are_immutable_and_need_no_second_parse() -> None:
    source = "class UniqueBindingSubject: pass\n"
    tree = ast.parse(source)
    with patch.object(ast, "parse", side_effect=AssertionError("redundant parse")):
        bindings = bindings_from_tree(source, tree)
    assert bindings.may_bind("UniqueBindingSubject")
    assert isinstance(bindings.bindings, MappingProxyType)
    assert isinstance(bindings.class_orders, MappingProxyType)
    assert isinstance(bindings.class_hosts, MappingProxyType)
