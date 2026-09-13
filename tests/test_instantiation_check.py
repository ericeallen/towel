"""Unit tests for the helper instantiation invariant."""

from __future__ import annotations

import ast
from typing import cast

import pytest

from towel.unification.instantiation import instantiation_mismatch


def _function(source: str) -> ast.FunctionDef:
    return cast(ast.FunctionDef, ast.parse(source).body[0])


def _statement(source: str) -> ast.stmt:
    return ast.parse(source).body[0]


def _block(source: str) -> list[ast.AST]:
    return list(ast.parse(source).body)


def test_consistent_instantiation_matches() -> None:
    helper = _function("def h(__param_0, d):\n    out = []\n    out.append(d[__param_0])\n")
    call = _statement("h(k + 1, d)")
    block = _block("out = []\nout.append(d[k + 1])\n")
    assert (
        instantiation_mismatch(
            helper, call, block, {}, {}, preamble_length=0, returns_variables=False
        )
        is None
    )


def test_parameter_substituted_at_unrelated_position_is_rejected() -> None:
    helper = _function(
        "def h(__param_0, d):\n    if __param_0 in d:\n        return d[__param_0]\n"
    )
    call = _statement("return h(k + 1, d)")
    block = _block("if k in d:\n    return d[k + 1]\n")
    reason = instantiation_mismatch(
        helper, call, block, {}, {}, preamble_length=0, returns_variables=False
    )
    assert reason is not None and reason.startswith("body")


def test_thunk_is_beta_reduced_at_use_sites() -> None:
    helper = _function(
        "def h(__param_0, self):\n    if not __param_0():\n        raise ValueError()\n"
    )
    call = _statement("h(lambda: self.email, self)")
    block = _block("if not self.email:\n    raise ValueError()\n")
    assert (
        instantiation_mismatch(
            helper, call, block, {}, {}, preamble_length=0, returns_variables=False
        )
        is None
    )


def test_lambda_lifted_parameter_binds_block_variables() -> None:
    helper = _function(
        "def h(__param_0, items):\n    for item in items:\n        use(__param_0(item))\n"
    )
    call = _statement("h(lambda item: item * 2, items)")
    block = _block("for item in items:\n    use(item * 2)\n")
    assert (
        instantiation_mismatch(
            helper, call, block, {}, {}, preamble_length=0, returns_variables=False
        )
        is None
    )


def test_forwarded_callee_reduces_to_original_call() -> None:
    helper = _function("def h(__param_0, x):\n    value = __param_0(x, key=1)\n    return value\n")
    call = _statement("return h(lambda *args, **kwargs: obj.method(*args, **kwargs), x)")
    block = _block("value = obj.method(x, key=1)\nreturn value\n")
    assert (
        instantiation_mismatch(
            helper, call, block, {}, {}, preamble_length=0, returns_variables=False
        )
        is None
    )


def test_thunk_used_as_value_is_rejected() -> None:
    helper = _function("def h(__param_0):\n    f = __param_0\n    return f\n")
    call = _statement("return h(lambda: obj.attr)")
    block = _block("f = obj.attr\nreturn f\n")
    assert (
        instantiation_mismatch(
            helper, call, block, {}, {}, preamble_length=0, returns_variables=False
        )
        is not None
    )


def test_renamed_binders_compare_equal_but_captures_do_not() -> None:
    helper = _function("def h(pairs):\n    for key, value in pairs:\n        use(key, value)\n")
    call = _statement("h(pairs)")
    renamed = _block("for k, v in pairs:\n    use(k, v)\n")
    assert (
        instantiation_mismatch(
            helper, call, renamed, {}, {}, preamble_length=0, returns_variables=False
        )
        is None
    )
    captured = _block("for k, v in pairs:\n    use(key, v)\n")
    assert (
        instantiation_mismatch(
            helper, call, captured, {}, {}, preamble_length=0, returns_variables=False
        )
        is not None
    )


def test_preamble_and_return_suffix_are_ignored() -> None:
    helper = _function(
        "def h(x):\n    global counter\n    counter = x\n    total = x + 1\n    return total\n"
    )
    call = _statement("total = h(x)")
    block = _block("counter = x\ntotal = x + 1\n")
    assert (
        instantiation_mismatch(
            helper, call, block, {}, {}, preamble_length=1, returns_variables=True
        )
        is None
    )


def test_hygienic_renames_translate_template_spelling() -> None:
    helper = _function("def h(xs):\n    vals = [x for x in xs]\n    return vals\n")
    call = _statement("res = h(xs)")
    block = _block("res = [y for y in xs]\n")
    assert (
        instantiation_mismatch(
            helper,
            call,
            block,
            {"vals": "__temp_0"},
            {"res": "__temp_0"},
            preamble_length=0,
            returns_variables=True,
        )
        is None
    )


@pytest.mark.parametrize(
    "call_source", ["h(a)", "h(a, b, c)", "h(a, key=b)", "other(a, b)", "x = 1"]
)
def test_call_shape_and_arity_are_checked(call_source: str) -> None:
    helper = _function("def h(a, b):\n    use(a, b)\n")
    block = _block("use(a, b)\n")
    assert (
        instantiation_mismatch(
            helper,
            _statement(call_source),
            block,
            {},
            {},
            preamble_length=0,
            returns_variables=False,
        )
        is not None
    )
