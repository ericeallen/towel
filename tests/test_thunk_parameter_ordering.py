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

"""Certified-prefix ordering keeps fixed parameter slots and the existing inlining guards."""

from __future__ import annotations

import ast

import pytest

from tests.test_helpers import function_def
from towel.unification.substitution import Substitution
from towel.unification.thunk_inlining import (
    _eligible_leading_thunks,
    inline_leading_thunks,
    reorder_eligible_thunk_parameters,
)


def _inputs(source: str, *candidates: str) -> tuple[ast.FunctionDef, Substitution, dict[str, int]]:
    helper = function_def(source)
    substitution = Substitution()
    for name in candidates:
        substitution.function_params[name] = []
        substitution.param_expressions[name] = [(0, ast.Constant(1)), (1, ast.Constant(2))]
        substitution.params_used_as_callee.add(name)
    return (
        helper,
        substitution,
        {
            argument.arg: index
            for index, argument in enumerate((*helper.args.posonlyargs, *helper.args.args))
        },
    )


def _expression_snapshot(substitution: Substitution) -> dict[str, tuple[tuple[int, str], ...]]:
    return {
        name: tuple(
            (index, ast.dump(expression, include_attributes=True)) for index, expression in rows
        )
        for name, rows in substitution.param_expressions.items()
    }


def _assert_ordering_preserves_nodes(
    helper: ast.FunctionDef, substitution: Substitution, order: dict[str, int]
) -> dict[str, int]:
    parameters = (*helper.args.posonlyargs, *helper.args.args)
    arguments = {argument.arg: argument for argument in parameters}
    argument_dumps = {
        name: ast.dump(argument, include_attributes=True) for name, argument in arguments.items()
    }
    body = tuple(ast.dump(statement, include_attributes=True) for statement in helper.body)
    expressions = _expression_snapshot(substitution)
    function_params = {name: list(bound) for name, bound in substitution.function_params.items()}
    callee_parameters = set(substitution.params_used_as_callee)
    inlined = set(substitution.inlined_parameters)
    original_order = dict(order)
    result = reorder_eligible_thunk_parameters(helper, substitution, order)
    assert order == original_order
    assert tuple(ast.dump(statement, include_attributes=True) for statement in helper.body) == body
    assert _expression_snapshot(substitution) == expressions
    assert substitution.function_params == function_params
    assert substitution.params_used_as_callee == callee_parameters
    assert substitution.inlined_parameters == inlined
    for argument in (*helper.args.posonlyargs, *helper.args.args):
        assert argument is arguments[argument.arg]
        assert ast.dump(argument, include_attributes=True) == argument_dumps[argument.arg]
    return result


@pytest.mark.parametrize(
    "source,candidates,expected",
    [
        ("def h(a, b, shared):\n    return b(), a()\n", ("a", "b"), ("b", "a", "shared")),
        (
            "def h(a, b, c, shared):\n    return c(), a(), b()\n",
            ("a", "b", "c"),
            ("c", "a", "b", "shared"),
        ),
        (
            "def h(a, b, c, shared):\n    first = b()\n    second = a()\n    effect()\n    return c()\n",
            ("a", "b", "c"),
            ("b", "a", "c", "shared"),
        ),
        (
            "def h(a: object, b: 'Alias', shared):\n    return b(), a()\n",
            ("a", "b"),
            ("b", "a", "shared"),
        ),
        ("def h(a, b, f):\n    return f(b(), a())\n", ("a", "b"), ("b", "a", "f")),
    ],
)
def test_complete_certified_prefix_orders_only_eligible_parameter_slots(
    source: str, candidates: tuple[str, ...], expected: tuple[str, ...]
) -> None:
    helper, substitution, order = _inputs(source, *candidates)
    reordered = _assert_ordering_preserves_nodes(helper, substitution, order)
    assert tuple(argument.arg for argument in helper.args.args) == expected
    assert reordered == {name: index for index, name in enumerate(expected)}
    expected_inlined = set(candidates) - {"c"} if "effect()" in source else set(candidates)
    assert inline_leading_thunks(helper, substitution, reordered) == expected_inlined


def test_complete_prefix_discovery_is_pure_and_independent_of_parameter_order() -> None:
    helper, substitution, order = _inputs(
        "def h(a,b,c):\n    return c(), b(), a()\n", "a", "b", "c"
    )
    before = (
        ast.dump(helper, include_attributes=True),
        _expression_snapshot(substitution),
        dict(order),
    )
    assert _eligible_leading_thunks(helper, substitution) == ("c", "b", "a")
    assert _eligible_leading_thunks(helper, substitution) == ("c", "b", "a")
    assert (
        ast.dump(helper, include_attributes=True),
        _expression_snapshot(substitution),
        order,
    ) == before
    assert set(substitution.function_params) == {"a", "b", "c"}
    assert not substitution.inlined_parameters


def test_fixed_free_variable_gap_bounds_reordering_and_later_inlining() -> None:
    helper, substitution, order = _inputs(
        "def h(a, shared, b, c):\n    return c(), b(), a()\n", "a", "b", "c"
    )
    reordered = _assert_ordering_preserves_nodes(helper, substitution, order)
    assert reordered == {"a": 0, "shared": 1, "c": 2, "b": 3}
    assert inline_leading_thunks(helper, substitution, reordered) == {"b", "c"}
    assert "a()" in ast.unparse(helper)


def test_fixed_eager_parameter_gap_is_not_an_ordering_candidate() -> None:
    helper, substitution, order = _inputs(
        "def h(a, eager, b, c):\n    return c(), b(), eager, a()\n", "a", "b", "c"
    )
    substitution.param_expressions["eager"] = [(0, ast.Constant(3)), (1, ast.Constant(4))]
    reordered = _assert_ordering_preserves_nodes(helper, substitution, order)
    assert reordered == {"a": 0, "eager": 1, "c": 2, "b": 3}
    assert inline_leading_thunks(helper, substitution, reordered) == {"b", "c"}


def test_signature_segments_reorder_independently_without_moving_the_slash() -> None:
    helper, substitution, order = _inputs(
        "def h(a,b,/,c,d,shared):\n    return b(), a(), d(), c()\n", "a", "b", "c", "d"
    )
    reordered = _assert_ordering_preserves_nodes(helper, substitution, order)
    assert tuple(argument.arg for argument in helper.args.posonlyargs) == ("b", "a")
    assert tuple(argument.arg for argument in helper.args.args) == ("d", "c", "shared")
    assert reordered == {"b": 0, "a": 1, "d": 2, "c": 3, "shared": 4}
    assert inline_leading_thunks(helper, substitution, reordered) == {"a", "b", "c", "d"}


def test_an_eligible_thunk_never_crosses_the_positional_only_boundary() -> None:
    helper, substitution, order = _inputs("def h(a,/,b,shared):\n    return b(),a()\n", "a", "b")
    before = ast.dump(helper, include_attributes=True)
    assert _assert_ordering_preserves_nodes(helper, substitution, order) == order
    assert ast.dump(helper, include_attributes=True) == before
    assert inline_leading_thunks(helper, substitution, order) == {"b"}


@pytest.mark.parametrize(
    "signature",
    ["a,b=1", "a=1,b=2", "a,b=1,/", "a,b,*rest", "a,b,**rest", "a,b,*,flag", "a,b,*,flag=1"],
)
def test_unsupported_signatures_are_unchanged(signature: str) -> None:
    helper, substitution, order = _inputs(f"def h({signature}):\n    return b(),a()\n", "a", "b")
    before = ast.dump(helper, include_attributes=True)
    assert _assert_ordering_preserves_nodes(helper, substitution, order) == order
    assert ast.dump(helper, include_attributes=True) == before


@pytest.mark.parametrize(
    "body",
    [
        "return b(),a(),b()",
        "return a(),b(),a()",
        "if flag:\n    return b(),a()\nreturn 0",
        "return b() if flag else a()",
        "return flag and (b(),a())",
        "while b():\n    a()",
        "return [b() for _ in a()]",
        "return lambda: (b(),a())",
        "try:\n    return b(),a()\nexcept Error:\n    return 0",
        "effect()\nreturn b(),a()",
        "return unknown(b(),a())",
        "return obj.method(b(),a())",
        "first = unbound\nreturn b(),a()",
        "b = 1\nreturn b(),a()",
        "del b\nreturn b(),a()",
        "return 0\nreturn b(),a()",
        "return b()\nreturn a()",
        "return {b(),a()}",
    ],
)
def test_ineligible_or_incomplete_regions_do_not_trigger_parameter_permutation(body: str) -> None:
    source = "def h(a,b,flag,obj):\n" + "\n".join("    " + line for line in body.splitlines())
    helper, substitution, order = _inputs(source, "a", "b")
    before = ast.dump(helper, include_attributes=True)
    assert _assert_ordering_preserves_nodes(helper, substitution, order) == order
    assert ast.dump(helper, include_attributes=True) == before


@pytest.mark.parametrize(
    "expression", ["module_value", "make_value()", "object.value", "items[0]", "(1,make_value())"]
)
def test_every_caller_must_prove_an_immutable_result_before_any_parameter_moves(
    expression: str,
) -> None:
    helper, substitution, order = _inputs("def h(a,b):\n    return b(),a()\n", "a", "b")
    substitution.param_expressions["b"].append((2, ast.parse(expression, mode="eval").body))
    before = ast.dump(helper, include_attributes=True)
    assert _assert_ordering_preserves_nodes(helper, substitution, order) == order
    assert ast.dump(helper, include_attributes=True) == before


def test_missing_result_and_bound_function_arguments_are_not_ordering_candidates() -> None:
    for missing, bound in ((True, False), (False, True)):
        helper, substitution, order = _inputs("def h(a,b):\n    return b(),a()\n", "a", "b")
        if missing:
            substitution.param_expressions["b"] = []
        if bound:
            substitution.function_params["b"] = ["local"]
        before = ast.dump(helper, include_attributes=True)
        assert _assert_ordering_preserves_nodes(helper, substitution, order) == order
        assert ast.dump(helper, include_attributes=True) == before
