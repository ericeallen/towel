# Copyright 2025-2026 Eric Allen
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Ownership memoization changes cost, never ownership or caller-visible mutation."""

from __future__ import annotations

import ast
import gc
import weakref
from unittest.mock import patch

import pytest

from towel.unification import block_analysis as analysis
from towel.unification.bounded_cache import memoization_disabled


@pytest.mark.parametrize(
    "source,expected",
    [
        ("x = resource\ny = 1\nz = x.item", {"x", "z"}),
        ("for x in items:\n    y = x[0]", {"x", "y"}),
        ("with manager as x:\n    y = x + 1", {"x", "y"}),
        ("if (x := source):\n    y = x", {"x", "y"}),
        ("def nested():\n    x = resource\ny = 1", set()),
    ],
)
def test_statement_facts_match_uncached_ownership(source: str, expected: set[str]) -> None:
    block = ast.parse(source).body
    initial = {"x", "y", "z"}
    before = ast.dump(ast.Module(body=block, type_ignores=[]), include_attributes=True)
    with memoization_disabled():
        assert analysis.lifetime_bound_names(block, initial) == expected
    assert analysis.lifetime_bound_names(block, initial) == expected
    assert ast.dump(ast.Module(body=block, type_ignores=[]), include_attributes=True) == before


def test_repeated_statements_compute_once_and_return_fresh_results() -> None:
    block = ast.parse("x = value\ny = other").body
    with patch.object(
        analysis, "_statement_lifetime_bindings", wraps=analysis._statement_lifetime_bindings
    ) as compute:
        first = analysis.lifetime_bound_names(block, {"x", "y"})
        first.clear()
        assert analysis.lifetime_bound_names(block, {"x", "y"}) == {"x", "y"}
        assert analysis.lifetime_bound_names(block, {"y"}) == {"y"}
        assert compute.call_count == 2
        with memoization_disabled():
            assert analysis.lifetime_bound_names(block, {"x"}) == {"x"}
            assert analysis.lifetime_bound_names(block, {"x"}) == {"x"}
        assert compute.call_count == 6


def test_same_prefix_and_length_do_not_alias_different_blocks() -> None:
    first, second, third = ast.parse("x = value\ny = other\nz = another").body
    assert analysis.lifetime_bound_names([first, second], {"x", "y", "z"}) == {"x", "y"}
    assert analysis.lifetime_bound_names([first, third], {"x", "y", "z"}) == {"x", "z"}


def test_cache_does_not_retain_parsed_nodes() -> None:
    statement = ast.parse("x = resource").body[0]
    reference = weakref.ref(statement)
    assert analysis.lifetime_bound_names([statement], {"x"}) == {"x"}
    assert statement in analysis._LIFETIME_BINDINGS
    del statement
    gc.collect()
    assert reference() is None
