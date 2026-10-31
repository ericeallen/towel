# Copyright 2025-2026 Eric Allen
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Construct only helper inputs that survive expression substitution.

These are quality and discovery contracts: a replaced parent must not leave
orphan child parameters or spend their parameter budget. A free variable
captured only by a thunk stays in that caller-side thunk. Runtime effects
must still occur where the original expression ran; this is not permission
to delete arbitrary arguments from existing calls.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from towel.unification.extractor import HygienicExtractor
from towel.unification.substitution import Substitution
from towel.unification.unifier import Unifier
from tests.test_scope_guard_boundaries import _extract
from tests.test_helpers import original_argument_name


@pytest.mark.parametrize(
    "body,needed",
    [
        ("def inner():\n    return value\nreturn inner", True),
        ("return [value + item for item in items]", True),
        ("del value", True),
        ("class Inner:\n    member: value\nreturn Inner", True),
        ("local: value = 1\nreturn local", False),
    ],
)
def test_parameter_pruning_respects_runtime_scope_and_binding(body: str, needed: bool) -> None:
    """Nested references, evaluated annotations and deletes still require the original input."""
    helper, order = HygienicExtractor().extract_function(
        template_block=ast.parse(body).body,
        substitution=Substitution(),
        free_variables={"value"},
        enclosing_names=set(),
        is_value_producing=True,
    )
    assert ("value" in order) is needed
    assert [arg.arg for arg in helper.args.posonlyargs + helper.args.args] == list(order)


def test_parent_attribute_difference_does_not_spend_a_child_parameter() -> None:
    blocks = [ast.parse(s).body for s in ("print(config.first)", "print(other.second)")]
    substitution = Unifier(max_parameters=1).unify_blocks(blocks, [{}, {}])
    assert substitution is not None, "One attribute value difference requires one parameter"
    assert len(substitution.param_expressions) == 1
    assert [
        ast.unparse(expr) for _, expr in next(iter(substitution.param_expressions.values()))
    ] == ["config.first", "other.second"]


@pytest.mark.parametrize("body", ["return env.render(value)", "return env.other(value)"])
def test_free_name_captured_only_in_substituted_expression_is_not_passed(body: str) -> None:
    blocks = [ast.parse(source).body for source in (body, "return env.third(value)")]
    substitution = Unifier().unify_blocks(blocks, [{}, {}])
    assert substitution is not None
    helper, order = HygienicExtractor().extract_function(
        template_block=blocks[0],
        substitution=substitution,
        free_variables={"env", "value"},
        enclosing_names=set(),
        is_value_producing=True,
    )
    assert "env" not in order
    assert "value" in order, "The thunk is only the callee; its argument remains in the helper"
    assert [arg.arg for arg in helper.args.posonlyargs + helper.args.args] == list(order)
    assert [arg.arg for arg in helper.args.posonlyargs] == ["__param_0"]
    assert [arg.arg for arg in helper.args.args] == ["value"]
    for index, callee in enumerate(
        (body.removeprefix("return ").removesuffix("(value)"), "env.third")
    ):
        statement = HygienicExtractor().generate_call(
            function_name=helper.name,
            block_idx=index,
            substitution=substitution,
            param_order=order,
            free_variables={"env", "value"},
            is_value_producing=True,
        )
        assert isinstance(statement, ast.Return) and isinstance(statement.value, ast.Call)
        call = statement.value
        assert not call.keywords
        assert [ast.unparse(argument) for argument in call.args] == [
            f"lambda *args, **kwargs: {callee}(*args, **kwargs)",
            "value",
        ]
        assert len(call.args) == len(helper.args.posonlyargs + helper.args.args)


def test_unused_thunk_inputs_disappear_without_changing_effects(tmp_path: Path) -> None:
    source = """
class Config:
    def __getattribute__(self, name):
        if name in ('first', 'second'):
            print('read', name)
            return 5
        return object.__getattribute__(self, name)
def first(config, value):
    print('before', value)
    result = config.first + value
    print('after', result)
    return result
def second(other, value):
    print('before', value)
    result = other.second + value
    print('after', result)
    return result
print(first(Config(), 2), second(Config(), 4))
"""
    result, count = _extract(tmp_path, source)
    assert count, result
    helpers = [
        node
        for node in ast.walk(ast.parse(result))
        if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name
    ]
    assert helpers
    for helper in helpers:
        referenced = {
            node.id
            for statement in helper.body
            for node in ast.walk(statement)
            if isinstance(node, ast.Name)
        }
        parameters = helper.args.posonlyargs + helper.args.args
        # The last input deliberately retains original arguments until helper
        # cleanup; it has an ownership effect even without a body read.
        assert parameters[-1].arg == "_towel_owner"
        assert parameters[-1].arg not in referenced
        assert [parameter.arg for parameter in parameters[:-1]] == ["__param_0", "value"]
        assert all(parameter.arg in referenced for parameter in parameters[:-1]), ast.unparse(
            helper
        )
        for function in ast.parse(result).body:
            if not isinstance(function, ast.FunctionDef) or function.name not in {
                "first",
                "second",
            }:
                continue
            (call,) = [
                node
                for node in ast.walk(function)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == helper.name
            ]
            assert original_argument_name(function, call.args[-2]) == "value"
            holder = call.args[-1]
            assert isinstance(holder, ast.Call) and isinstance(holder.func, ast.Attribute)
            assert holder.func.attr == "pop" and not holder.args and not holder.keywords
