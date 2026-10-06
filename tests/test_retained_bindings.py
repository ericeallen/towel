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

"""Hygienic lifetime-only targets preserve values, order and caller binding semantics."""

from __future__ import annotations

import ast
from typing import Callable, cast

import pytest

from towel.unification.retained_bindings import spell_retained_bindings


def _function(source: str) -> ast.FunctionDef:
    function = ast.parse(source).body[0]
    assert isinstance(function, ast.FunctionDef)
    return function


def _call(source: str = "x = helper()") -> ast.stmt:
    return ast.parse(source).body[0]


@pytest.mark.parametrize(
    "continuation",
    [
        "x = 2",
        "del x",
        "print(x)",
        "def read():\n        return x",
        "read = lambda: x",
        "read = [x for _ in ()]",
        "def change():\n        nonlocal x\n        x = 2",
    ],
)
def test_outside_binding_or_reference_preserves_returned_name(continuation: str) -> None:
    function = _function("def f():\n    x = 1\n    " + continuation + "\n")
    call = _call()
    result = spell_retained_bindings(function, function.body[:1], call)
    assert ast.unparse(result) == "x = helper()"


@pytest.mark.parametrize("header", ["def f(x):", "def f():\n    global x"])
def test_parameter_and_global_bindings_keep_their_names(header: str) -> None:
    function = _function(header + "\n    x = 1\n")
    result = spell_retained_bindings(function, function.body[-1:], _call())
    assert ast.unparse(result) == "x = helper()"


def test_prior_binding_keeps_name_and_rebind_timing() -> None:
    function = _function("def f():\n    x = old()\n    x = new()\n    after()\n")
    result = spell_retained_bindings(function, function.body[1:2], _call())
    assert ast.unparse(result) == "x = helper()"


def test_tuple_order_hygiene_and_input_immutability() -> None:
    function = _function("""def f():
    x = 1
    y = 2
    _towel_keep_x = 3
    def nested(_towel_keep_x_1):
        return y
""")
    call = _call("x, y = _towel_keep_x_2()")
    originals = ast.dump(function), ast.dump(call)
    result = spell_retained_bindings(function, function.body[:2], call)
    assert ast.unparse(result) == "_towel_keep_x_3, y = _towel_keep_x_2()"
    assert (ast.dump(function), ast.dump(call)) == originals
    assert result is not call
    assert isinstance(result, ast.Assign) and isinstance(call, ast.Assign)
    assert result.value is not call.value


def test_retained_values_finalize_in_the_same_sequence() -> None:
    source = """def f():
    x = Resource('x')
    y = Resource('y')
    z = 1
    events.append('after')
"""
    function = _function(source)
    call = spell_retained_bindings(function, function.body[:3], _call("x, y, z = helper()"))
    helper = """def helper():
    x = Resource('x')
    y = Resource('y')
    z = 1
    return x, y, z
"""
    replacement = _function(source)
    replacement.body = [call, replacement.body[-1]]
    events: list[str] = []

    class Resource:
        def __init__(self, name: str) -> None:
            self.name = name

        def __del__(self) -> None:
            events.append(self.name)

    namespace: dict[str, object] = {"Resource": Resource, "events": events}
    exec(source, namespace)
    cast(Callable[[], None], namespace["f"])()
    before = list(events)
    events.clear()
    exec(helper + ast.unparse(ast.fix_missing_locations(replacement)), namespace)
    cast(Callable[[], None], namespace["f"])()
    assert events == before == ["after", "x", "y"]


def test_a_value_return_without_assignment_is_unchanged() -> None:
    function = _function("def f():\n    return 1\n")
    call = _call("return helper()")
    assert spell_retained_bindings(function, function.body, call) is call
