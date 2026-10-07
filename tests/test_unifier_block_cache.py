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

"""Block facts may be reused only during one immutable unification."""

import ast
from typing import Sequence

import pytest

from towel.unification.bounded_cache import memoization_disabled
from towel.unification.scope_analyzer import ScopeAnalyzer
from towel.unification.unifier import Unifier


def test_repeated_block_queries_copy_the_cached_result(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    original = ScopeAnalyzer.free_variables

    def counted(analyzer: ScopeAnalyzer, nodes: Sequence[ast.AST]) -> set[str]:
        nonlocal calls
        calls += 1
        return original(analyzer, nodes)

    monkeypatch.setattr(ScopeAnalyzer, "free_variables", counted)
    block = ast.parse("consume(value)").body
    unifier = Unifier()
    unifier._reset_unification_state([block, block])
    first = unifier._block_free_variables(0)
    first.clear()
    assert unifier._block_free_variables(1) == {"consume", "value"}
    assert calls == 1
    empty = ast.parse("pass").body
    unifier._reset_unification_state([empty, empty])
    assert unifier._block_free_variables(0) == set()
    assert unifier._block_free_variables(1) == set()
    assert calls == 2


def test_new_unification_discards_facts_after_input_ast_changes() -> None:
    block = ast.parse("consume(before)").body
    unifier = Unifier()
    assert unifier.unify_blocks([block, block], [{}, {}]) is not None
    assert unifier._block_free_variables(0) == {"consume", "before"}
    for node in ast.walk(block[0]):
        if isinstance(node, ast.Name) and node.id == "before":
            node.id = "after"
    assert unifier.unify_blocks([block, block], [{}, {}]) is not None
    assert unifier._block_free_variables(0) == {"consume", "after"}


def test_disabled_memoization_recomputes_and_release_drops_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    original = ScopeAnalyzer.free_variables

    def counted(analyzer: ScopeAnalyzer, nodes: Sequence[ast.AST]) -> set[str]:
        nonlocal calls
        calls += 1
        return original(analyzer, nodes)

    monkeypatch.setattr(ScopeAnalyzer, "free_variables", counted)
    block = ast.parse("consume(value)").body
    unifier = Unifier()
    unifier._reset_unification_state([block, block])
    with memoization_disabled():
        assert unifier._block_free_variables(0) == {"consume", "value"}
        assert unifier._block_free_variables(0) == {"consume", "value"}
    assert calls == 2
    unifier.release_blocks()
    assert unifier.current_blocks is None
    assert unifier._block_free_variables(0) == set()
