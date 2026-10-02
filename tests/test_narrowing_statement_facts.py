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

"""Reuse immutable source facts without retaining trees or freezing generated helpers."""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import FrozenInstanceError
import gc
import textwrap
from typing import Iterator
import weakref

import pytest

from towel.unification import narrowing
from towel.unification.bounded_cache import memoization_disabled
from towel.unification.function_index import FunctionIndex
from towel.unification.import_graph import ImportGraphCache
from towel.unification.models import FunctionArtifact, Replacement
from towel.unification.scope_analyzer import ScopeAnalyzer


def _function(source: str) -> ast.FunctionDef:
    node = ast.parse(textwrap.dedent(source)).body[0]
    assert isinstance(node, ast.FunctionDef)
    return node


@pytest.fixture
def facts_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> weakref.WeakKeyDictionary[ast.AST, narrowing._StatementNarrowingFacts]:
    cache: weakref.WeakKeyDictionary[ast.AST, narrowing._StatementNarrowingFacts] = (
        weakref.WeakKeyDictionary()
    )
    monkeypatch.setattr(narrowing, "_STATEMENT_NARROWING_FACTS", cache)
    return cache


def test_statement_facts_keep_scope_and_comprehension_boundaries() -> None:
    statement = ast.parse(textwrap.dedent("""
            if obj.value is None:
                obj.value = compute()
                discarded = [(saved := item) for item in items if item is not None]
                def nested():
                    assert unrelated is not None
                    obj.nested = 1
                class Local:
                    assert other is not None
                    obj.member = 2
                callback = lambda: (inside := None)
                while not (peer.value is not None):
                    peer.value = compute()
                assert obj.value is not None and saved is not None
            """)).body[0]
    facts = narrowing._statement_narrowing_facts(statement)
    assert facts.tested == frozenset({"obj.value", "peer.value"})
    assert facts.asserted == frozenset({"obj.value", "saved"})
    assert facts.stored == frozenset({"obj.value", "discarded", "saved", "callback", "peer.value"})
    assert facts.stored == narrowing._stored_references([statement])


def test_overlapping_blocks_walk_each_source_statement_once(
    monkeypatch: pytest.MonkeyPatch,
    facts_cache: weakref.WeakKeyDictionary[ast.AST, narrowing._StatementNarrowingFacts],
) -> None:
    function = _function("def f():\n    a = 1\n    b = 2\n    c = 3\n    d = 4\n")
    blocks = [
        function.body[start:stop]
        for start in range(len(function.body))
        for stop in range(start + 1, len(function.body) + 1)
    ]
    roots = set(function.body)
    walks: Counter[ast.AST] = Counter()
    original = narrowing._own_scope

    def counted(node: ast.AST) -> Iterator[ast.AST]:
        if node in roots:
            walks[node] += 1
        yield from original(node)

    monkeypatch.setattr(narrowing, "_own_scope", counted)
    assert not any(
        narrowing.caller_narrowing_leaves_with_block(function, block) for block in blocks
    )
    assert walks == Counter({statement: 1 for statement in function.body})
    assert len(facts_cache) == len(function.body)
    walks.clear()
    with memoization_disabled():
        assert not any(
            narrowing.caller_narrowing_leaves_with_block(function, block) for block in blocks
        )
    assert sum(walks.values()) == sum(map(len, blocks))


CONTEXTS = """
def f(obj, value, choose, items):
    assert obj.value is not None
    obj = replacement()
    if obj.value is None:
        obj.value = compute()
    try:
        if choose:
            assert value is not None
            discarded = [value for value in items]
            value.upper()
        else:
            assert obj.value is not None
            obj.value.upper()
    except ValueError:
        assert value is not None
        value.upper()
    finally:
        obj.value.upper()
    while obj.value is None:
        obj.value = compute()
    return obj.value.upper()
"""


def _blocks(function: ast.FunctionDef) -> Iterator[list[ast.stmt]]:
    for node in ast.walk(function):
        for field in ("body", "orelse", "finalbody"):
            suite = getattr(node, field, None)
            if isinstance(suite, list) and suite and isinstance(suite[0], ast.stmt):
                for start in range(len(suite)):
                    for stop in range(start + 1, len(suite) + 1):
                        yield suite[start:stop]


def test_cached_facts_preserve_each_blocks_enclosing_and_following_context() -> None:
    function = _function(CONTEXTS)
    blocks = list(_blocks(function))
    with memoization_disabled():
        expected = [
            narrowing.caller_narrowing_leaves_with_block(function, block) for block in blocks
        ]
    assert any(expected) and not all(expected)
    assert [
        narrowing.caller_narrowing_leaves_with_block(function, block) for block in blocks
    ] == expected
    # Revisit the same facts with different neighboring windows after the cache fills.
    assert [
        narrowing.caller_narrowing_leaves_with_block(function, block) for block in reversed(blocks)
    ] == list(reversed(expected))


def test_reparsing_changed_source_does_not_reuse_old_facts() -> None:
    source = "def f(value):\n    assert value is not None\n    return value.upper()\n"
    original = _function(source)
    revised = _function(source.replace("assert value is not None", "assert other is not None"))
    assert narrowing.caller_narrowing_leaves_with_block(original, original.body[:1])
    assert not narrowing.caller_narrowing_leaves_with_block(revised, revised.body[:1])
    assert narrowing.caller_narrowing_leaves_with_block(original, original.body[:1])


def test_facts_are_immutable_and_do_not_keep_source_nodes_alive(
    facts_cache: weakref.WeakKeyDictionary[ast.AST, narrowing._StatementNarrowingFacts],
) -> None:
    function = _function("def f(value):\n    assert value is not None\n    return value.upper()\n")
    statement = function.body[0]
    facts = narrowing._statement_narrowing_facts(statement)
    function_ref = weakref.ref(function)
    statement_ref = weakref.ref(statement)
    assert facts is narrowing._statement_narrowing_facts(statement)
    assert isinstance(facts.asserted, frozenset)
    with pytest.raises(FrozenInstanceError):
        setattr(facts, "asserted", frozenset())
    assert len(facts_cache) == 1
    del statement, function
    gc.collect()
    assert function_ref() is None and statement_ref() is None
    assert not facts_cache
    assert facts.asserted == frozenset({"value"})


def test_generated_helper_rebinding_is_recomputed_after_in_place_edit() -> None:
    """Generated statements can be edited without reparsing during construction."""
    source = "def f(value: str | None):\n    if value is None:\n        return ''\n    return value.upper()\n"
    tree = ast.parse(source)
    function = tree.body[0]
    assert isinstance(function, ast.FunctionDef)
    guard = function.body[0]
    assert isinstance(guard, ast.If)
    helper = _function(
        "def extracted(condition, value):\n"
        "    if condition:\n        return ''\n"
        "    value = 'replacement'\n    return value.upper()\n"
    )
    assignment = helper.body[1]
    assert isinstance(assignment, ast.Assign)
    target = assignment.targets[0]
    assert isinstance(target, ast.Name)
    call = ast.parse("extracted(condition, value)").body[0]
    assert isinstance(call, ast.Expr) and isinstance(call.value, ast.Call)
    call.value.args[0] = guard.test
    analyzer = ScopeAnalyzer()
    scope = analyzer.analyze(tree)
    index = FunctionIndex.build(
        [FunctionArtifact("module.py", function, source, analyzer, scope, None, None, [])]
    )
    site = Replacement(line_range=(guard.lineno, function.end_lineno or guard.lineno), node=call)
    imports = ImportGraphCache()

    def reason() -> str | None:
        return narrowing.parameterized_narrowing_lost(
            helper, [site], index, "module.py", ast.parse, imports
        )

    assert reason() is None
    target.id = "other"
    assert reason() is not None
    target.id = "value"
    assert reason() is None
