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

"""Ownership boundaries distinguish harmless literals from opaque caller/local values."""

from __future__ import annotations

import ast

import pytest

from towel.unification.frame_ownership import frame_ownership


def _function(source: str) -> ast.FunctionDef:
    function = ast.parse(source).body[0]
    assert isinstance(function, ast.FunctionDef)
    return function


@pytest.mark.parametrize(
    "prefix",
    [
        "outer = Resource()",
        "outer: object = Resource()",
        "import outer",
        "def outer(): pass",
        "class outer: pass",
        "match subject:\n        case {'key': outer}: pass",
        "try: pass\n    except ValueError as outer: pass",
    ],
)
def test_opaque_outside_bindings_are_potential_owners(prefix: str) -> None:
    function = _function("def f():\n    " + prefix + "\n    inner = Resource()\n    fail()\n")
    facts = frame_ownership(function, function.body[-2:])
    assert facts.inside == {"inner"} and "outer" in facts.outside and facts.split


@pytest.mark.parametrize("prefix", ["outer = 1", "outer: int = 1", "outer: int", "(outer := 1)"])
def test_literal_and_uninitialized_annotation_do_not_own_outside_values(prefix: str) -> None:
    function = _function("def f():\n    " + prefix + "\n    inner = Resource()\n    fail()\n")
    facts = frame_ownership(function, function.body[-2:])
    assert facts.inside == {"inner"} and not facts.outside and not facts.split


def test_every_parameter_is_a_potential_owner_even_when_unused_or_annotated_scalar() -> None:
    function = _function(
        "def f(a: int, /, b=1, *args, c=None, **kwargs):\n    inner = Resource()\n    return 1\n"
    )
    facts = frame_ownership(function, function.body)
    assert facts.outside == {"a", "b", "args", "c", "kwargs"} and facts.split and facts.whole_body


def test_exact_binding_identity_keeps_outside_reassignment() -> None:
    function = _function("def f():\n    x = Resource()\n    x = Resource()\n    x = 1\n")
    facts = frame_ownership(function, function.body[1:2])
    assert facts.inside == facts.outside == {"x"}


def test_nested_own_bindings_do_not_become_callers_owned_values() -> None:
    function = _function(
        "def f():\n    def nested():\n        hidden = Resource()\n    inner = Resource()\n    return 1\n"
    )
    facts = frame_ownership(function, function.body[1:])
    assert facts.outside == {"nested"} and "hidden" not in facts.inside | facts.outside


def test_global_and_nonlocal_are_not_frame_owned() -> None:
    function = _function(
        "def f():\n    global outer\n    nonlocal inner\n    outer = Resource()\n    inner = Resource()\n"
    )
    facts = frame_ownership(function, function.body[2:])
    assert not facts.inside and not facts.outside and not facts.split


def test_literal_only_block_remains_eligible_with_outside_resource() -> None:
    function = _function("def f(unused):\n    outer = Resource()\n    x = 1\n    y = 2\n")
    facts = frame_ownership(function, function.body[2:])
    assert not facts.inside and facts.outside == {"unused", "outer"} and not facts.split


def test_whole_body_match_ignores_only_original_docstring_and_does_not_mutate_ast() -> None:
    function = _function('def f():\n    "doc"\n    inner = Resource()\n    return 1\n')
    before = ast.dump(function, include_attributes=True)
    facts = frame_ownership(function, function.body[1:])
    assert facts.whole_body and facts.inside == {"inner"} and not facts.split
    assert ast.dump(function, include_attributes=True) == before
    cloned = ast.parse(ast.unparse(function)).body[0]
    assert isinstance(cloned, ast.FunctionDef)
    assert not frame_ownership(function, cloned.body[1:]).whole_body
