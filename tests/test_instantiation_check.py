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


def test_returned_variables_must_match_assignment_targets_in_order() -> None:
    helper = _function("def h(items):\n    a = items[0]\n    b = items[-1]\n    return (a, b)\n")
    block = _block("lo = items[0]\nhi = items[-1]\n")
    renames = ({"a": "__temp_0", "b": "__temp_1"}, {"lo": "__temp_0", "hi": "__temp_1"})
    good = _statement("lo, hi = h(items)")
    assert (
        instantiation_mismatch(
            helper, good, block, *renames, preamble_length=0, returns_variables=True
        )
        is None
    )
    swapped = _statement("hi, lo = h(items)")
    reason = instantiation_mismatch(
        helper, swapped, block, *renames, preamble_length=0, returns_variables=True
    )
    assert reason is not None and "assignment targets" in reason


def test_early_return_with_returned_variables_is_rejected() -> None:
    helper = _function(
        "def h(obj, self):\n    if obj is None:\n        return self\n    cls = obj.__class__\n    return cls\n"
    )
    call = _statement("cls = h(obj, self)")
    block = _block("if obj is None:\n    return self\ncls = obj.__class__\n")
    reason = instantiation_mismatch(
        helper, call, block, {}, {}, preamble_length=0, returns_variables=True
    )
    assert reason is not None and "early return" in reason


def test_early_return_requires_return_call() -> None:
    helper = _function("def h(x):\n    if x:\n        return 1\n    return 2\n")
    block = _block("if x:\n    return 1\nreturn 2\n")
    assert (
        instantiation_mismatch(
            helper,
            _statement("return h(x)"),
            block,
            {},
            {},
            preamble_length=0,
            returns_variables=False,
        )
        is None
    )
    assert (
        instantiation_mismatch(
            helper, _statement("h(x)"), block, {}, {}, preamble_length=0, returns_variables=False
        )
        is not None
    )


def test_lambda_parameters_are_alpha_equivalent_only_inside_their_lambda() -> None:
    """``lambda v: v`` equals ``lambda w: w``; a free ``v`` outside the lambda is a different name."""
    from towel.unification.instantiation import _alpha_normalize

    def normalized(source: str) -> str:
        return ast.dump(_alpha_normalize(ast.parse(source)), include_attributes=False)

    assert normalized("f = lambda v: v * 2") == normalized("f = lambda w: w * 2")
    assert normalized("f = lambda v: v * 2\ntotal = v + 1") == normalized(
        "f = lambda w: w * 2\ntotal = v + 1"
    )
    assert normalized("f = lambda v: v * 2\ntotal = v + 1") != normalized(
        "f = lambda w: w * 2\ntotal = w + 1"
    )
    assert normalized("f = lambda v: (lambda v: v)(v)") == normalized(
        "f = lambda a: (lambda b: b)(a)"
    )
    assert normalized("f = lambda v=x: v") == normalized("f = lambda w=x: w")
    assert normalized("f = lambda v=x: v") != normalized("f = lambda w=y: w")
