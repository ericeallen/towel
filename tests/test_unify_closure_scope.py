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

"""Audit D5: a nested scope's own bindings do not capture the caller's names."""

from __future__ import annotations

import ast
import textwrap

import pytest

from towel.unification.semantic_safety import (
    _lazily_read_names,
    nested_scopes_cross_block_boundary,
)


def _function(source: str) -> ast.FunctionDef:
    function = ast.parse(textwrap.dedent(source)).body[0]
    assert isinstance(function, ast.FunctionDef)
    return function


@pytest.mark.parametrize(
    "definition",
    [
        "callback = lambda value: value.name",
        "callback = lambda: (value := 1) + value",
        "def callback(value):\n    return value.name",
        "def callback():\n    value = 1\n    return value",
        "def callback():\n    return [value.name for value in items]",
        "callback = (value.name for value in items)",
    ],
)
@pytest.mark.parametrize("scope_first", [False, True])
def test_own_bindings_do_not_cross_the_block_boundary(definition: str, scope_first: bool) -> None:
    """Audit D5: rebinding a caller's names cannot change the nested scope's own names."""
    assignment = "value = 10"
    statements = [definition, assignment] if scope_first else [assignment, definition]
    function = _function("def caller(items):\n" + textwrap.indent("\n".join(statements), "    "))
    block = function.body[:1]
    assert not nested_scopes_cross_block_boundary(function, block)


@pytest.mark.parametrize(
    "definition",
    [
        "callback = lambda argument: argument.name + value",
        "callback = lambda: (lambda argument: argument + value)",
        "def callback():\n    def inner():\n        return value\n    return inner",
        "def callback():\n    return [argument + value for argument in items]",
        "callback = (argument + value for argument in items)",
    ],
)
@pytest.mark.parametrize("scope_first", [False, True])
def test_genuine_captures_still_cross_the_block_boundary(
    definition: str, scope_first: bool
) -> None:
    """Audit D5: nested and transitive captures still share the caller's mutable binding."""
    assignment = "value = 10"
    statements = [definition, assignment] if scope_first else [assignment, definition]
    function = _function("def caller(items):\n" + textwrap.indent("\n".join(statements), "    "))
    block = function.body[:1]
    assert nested_scopes_cross_block_boundary(function, block)


@pytest.mark.parametrize(
    "source, expected",
    [
        ("lambda value=value: value", frozenset({"value"})),
        ("lambda value: (lambda inner=value: inner)", frozenset()),
        ("(value for value in value)", frozenset({"value"})),
        ("(value for value in items if value)", frozenset({"items"})),
    ],
)
def test_binding_positions_keep_their_enclosing_scope(
    source: str, expected: frozenset[str]
) -> None:
    """Audit D5: own binders do not hide names read before that scope exists."""
    node = ast.parse(source, mode="eval").body
    assert _lazily_read_names(node) == expected


def test_function_defaults_keep_their_enclosing_scope() -> None:
    """Audit D5: a default named like its parameter still reads the enclosing name."""
    function = _function("def callback(value=value):\n    return value\n")
    assert _lazily_read_names(function) == frozenset({"value"})


def test_nonlocal_declarations_do_not_make_captures_local() -> None:
    """Audit D5: an explicit nonlocal remains a capture despite a store in the nested body."""
    function = _function("""
        def caller():
            def callback():
                nonlocal value
                value += 1
                return value
            value = 10
        """)
    assert nested_scopes_cross_block_boundary(function, function.body[1:])
