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

"""Per-function precomputation gives the same answers as the per-query walks.

Definite assignment, locally bound names, and the closure guard used to walk
the whole function for every candidate block. They now read facts computed
once per function. These tests compare the cached forms with the reference
implementations on every function and statement of every example file, so
a divergence anywhere in the corpus fails.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.hostile_execution import fixture_sources, parsed_or_skipped
from towel.unification import definite_assignment as da
from towel.unification.definite_assignment import (
    definitely_bound_after,
    definitely_bound_before,
    definitely_bound_before_each,
)

EXAMPLES = sorted(
    path
    for directory in ("test_examples", "test_examples_crossfile", "tests/hostile_cases")
    for path in fixture_sources(Path(__file__).parent.parent / directory, recursive=True)
)


def _functions(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _statements_in_own_lists(function: ast.AST):
    """Statements reachable through statement lists, as a path from the root would be."""
    pending = [function]
    while pending:
        container = pending.pop()
        for field in ("body", "orelse", "finalbody", "handlers", "cases"):
            children = getattr(container, field, None)
            if isinstance(children, list):
                for child in children:
                    yield child
                    pending.append(child)


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_cached_definite_before_matches_the_path_walk(path: Path) -> None:
    tree = parsed_or_skipped(path)
    checked = 0
    for function in _functions(tree):
        for statement in _statements_in_own_lists(function):
            if not isinstance(statement, ast.stmt):
                continue
            assert definitely_bound_before(function, statement) == (
                da._definitely_bound_before_uncached(function, statement)
            ), f"{path.name}:{statement.lineno}"
            checked += 1
    assert checked > 0


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_before_each_matches_after_each_prefix(path: Path) -> None:
    tree = parsed_or_skipped(path)
    for function in _functions(tree):
        body = function.body
        expected = [definitely_bound_after(body[:index]) for index in range(len(body))]
        assert list(definitely_bound_before_each(body)) == expected


@pytest.mark.parametrize("path", EXAMPLES[:20], ids=lambda p: p.name)
def test_locally_bound_names_match_the_reference(path: Path) -> None:
    tree = parsed_or_skipped(path)
    for function in _functions(tree):
        assert da.locally_bound_names(function) == da._locally_bound_names(function)
