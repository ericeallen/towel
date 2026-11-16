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

import ast
from pathlib import Path
from typing import Callable, List, cast

import pytest

from tests.test_helpers import parse_block, refactor_to_fixed_point_silently, write_module
from towel.unification.unifier import Unifier
from towel.unification.substitution import Substitution


def _assign_value(block: List[ast.stmt], index: int) -> ast.expr:
    assign = block[index]
    assert isinstance(assign, ast.Assign), "Expected assignment statement"
    return assign.value


def test_unify_dict_literals():
    code1 = "config = {'key': 'value1', 'timeout': 10}"
    code2 = "config = {'key': 'value2', 'timeout': 20}"
    blocks = [parse_block(code1), parse_block(code2)]
    u = Unifier(max_parameters=5, parameterize_constants=True)
    subst = u.unify_blocks(blocks, [{}, {}])
    assert subst is not None


def test_unify_list_literals():
    code1 = "items = [1, 2, 3]"
    code2 = "items = [4, 5, 6]"
    blocks = [parse_block(code1), parse_block(code2)]
    u = Unifier(max_parameters=5, parameterize_constants=True)
    subst = u.unify_blocks(blocks, [{}, {}])
    assert subst is not None


def test_unify_set_literals():
    code1 = "values = {1, 2, 3}"
    code2 = "values = {4, 5, 6}"
    blocks = [parse_block(code1), parse_block(code2)]
    u = Unifier(max_parameters=5, parameterize_constants=True)
    subst = u.unify_blocks(blocks, [{}, {}])
    assert subst is not None


def test_unify_tuple_literals():
    code1 = "point = (1, 2)"
    code2 = "point = (3, 4)"
    blocks = [parse_block(code1), parse_block(code2)]
    u = Unifier(max_parameters=5, parameterize_constants=True)
    subst = u.unify_blocks(blocks, [{}, {}])
    assert subst is not None


def assert_lambda_keyword_behavior_preserved(tmp_path: Path, blocks: List[List[ast.stmt]]) -> None:
    source = "\n".join(
        f"def {name}():\n    {ast.unparse(block[0])}\n    return func\n"
        for name, block in zip(("first", "second"), blocks)
    )
    final, applied = refactor_to_fixed_point_silently(write_module(tmp_path, source))
    assert applied == 0, "different escaping lambda keyword signatures must remain separate"
    for text in (source, final):
        namespace: dict[str, object] = {}
        exec(compile(text, "<lambda-keywords>", "exec"), namespace)
        for name, keyword, other, expected in (
            ("first", "x", "y", 6),
            ("second", "y", "x", 9),
        ):
            factory = cast(Callable[[], Callable[..., int]], namespace[name])
            callback = factory()
            assert callback(**{keyword: 3}) == expected
            with pytest.raises(TypeError, match="unexpected keyword argument"):
                callback(**{other: 3})


def test_unify_lambda_expressions(tmp_path: Path):
    """Review ruling 4: retain the original distinct keyword signatures and decline."""
    code1 = "func = lambda x: x * 2"
    code2 = "func = lambda y: y * 3"
    blocks = [parse_block(code1), parse_block(code2)]
    u = Unifier(max_parameters=5, parameterize_constants=True)
    subst = u.unify_blocks(blocks, [{}, {}])
    assert subst is None
    assert_lambda_keyword_behavior_preserved(tmp_path, blocks)


def test_unify_assert_statements():
    code1 = "assert x > 0"
    code2 = "assert y > 0"
    blocks = [parse_block(code1), parse_block(code2)]
    u = Unifier(max_parameters=5, parameterize_constants=True)
    subst = u.unify_blocks(blocks, [{}, {}])
    assert subst is not None


def test_unifier_with_walrus_and_with_optional_vars():
    # Two blocks differing only in names inside with and walrus target should unify
    code1 = (
        "with open('a') as f:\n    data = f.read()\n    if (x := len(data)) > 0:\n        val = x\n"
    )
    code2 = "with open('a') as fh:\n    data = fh.read()\n    if (y := len(data)) > 0:\n        val = y\n"
    blocks = [parse_block(code1), parse_block(code2)]
    u = Unifier()
    subst = u.unify_blocks(blocks, [{}, {}])
    assert subst is not None, "with-target and walrus-target spellings are alpha-equivalent"
    assert subst.param_expressions == {}


def test_unifier_fstring_format_spec():
    code1 = "result = f'{value:{width}}'"
    code2 = "result = f'{value:{width}}'"  # identical
    blocks = [parse_block(code1), parse_block(code2)]
    u = Unifier()
    subst = u.unify_blocks(blocks, [{}, {}])
    assert subst is not None


def test_unifier_list_comp_tuple_target_alpha():
    code1 = "pairs = [(k, v) for k, v in items]"
    code2 = "pairs = [(key, val) for key, val in items]"
    blocks = [parse_block(code1), parse_block(code2)]
    u = Unifier()
    subst = u.unify_blocks(blocks, [{}, {}])
    assert subst is not None


def test_unifier_distinguishes_differing_and_fixed_constant_occurrences():
    # The differing multiplier and fixed exponent are separate occurrences.
    code1 = "x = item * 2\nz = y ** 2"  # constant 2 twice
    code2 = "x = item * 3\nz = y ** 2"  # second occurrence identical (2 vs 2)
    blocks = [parse_block(code1), parse_block(code2)]
    u = Unifier()
    subst = u.unify_blocks(blocks, [{}, {}])
    assert subst is not None
    assert len(subst.param_expressions) == 1
    for index, block in enumerate(blocks):
        product = _assign_value(block, 0)
        power = _assign_value(block, 1)
        assert isinstance(product, ast.BinOp)
        assert isinstance(power, ast.BinOp)
        assert subst.get_param_for_expr(index, product.right) is not None
        assert subst.get_param_for_expr(index, power.right) is None
        assert isinstance(power.right, ast.Constant) and power.right.value == 2


def test_unifier_reject_parameterize_entire_fstring():
    code1 = "msg = f'User: {name}'"
    code2 = "msg = f'User: {other}'"  # differing inner expression accepted
    blocks = [parse_block(code1), parse_block(code2)]
    u = Unifier()
    subst = u.unify_blocks(blocks, [{}, {}])
    assert subst is not None
    # Ensure only inner expression is parameterized, not entire f-string
    # There should be at least one param expression that is ast.Name, not JoinedStr
    assert all(
        not isinstance(expr, ast.JoinedStr)
        for exprs in subst.param_expressions.values()
        for _, expr in exprs
    )


def test_unifier_exceed_max_parameters():
    # Force more parameters than allowed
    code1 = "a = w + x + y + z + q"  # 5 variables
    code2 = "a = w1 + x1 + y1 + z1 + q1"
    blocks = [parse_block(code1), parse_block(code2)]
    u = Unifier(max_parameters=2)
    subst = u.unify_blocks(blocks, [{}, {}])
    assert subst is None, "Should reject when exceeding max parameter count"


def test_unifier_skip_unreachable_variable_at_call_site():
    # Loop variables referenced outside the loop should prevent unification
    code1 = "for value in data:\n    leak = process(value)\nres = value"
    code2 = "for item in data:\n    leak = process(item)\nres = item"
    blocks = [parse_block(code1), parse_block(code2)]
    u = Unifier()
    subst = u.unify_blocks(blocks, [{}, {}])
    assert subst is None


def test_try_parameterize_lifts_an_expression_over_a_comprehension_target():
    # The element reads the comprehension's own variable, which no argument
    # evaluated at the call can supply; since 2.0 the parameter is a function
    # of it (docs/DECISIONS.md, "Relaxed mode passes arguments by value").
    code1 = "result = [value + 1 for value in data]"
    code2 = "result = [value + 2 for value in data]"
    blocks = [parse_block(code1), parse_block(code2)]

    u = Unifier()
    u.current_blocks = blocks
    expr_a = cast(ast.ListComp, cast(ast.Assign, blocks[0][0]).value).elt
    expr_b = cast(ast.ListComp, cast(ast.Assign, blocks[1][0]).value).elt

    subst = Substitution()
    allowed = u._try_parameterize([expr_a, expr_b], subst, [0, 1])
    assert allowed is True
    assert list(subst.function_params.values()) == [["value"]]


def test_try_parameterize_rejects_lifting_past_a_name_the_block_binds_later():
    # ``step`` is rebound after the element reads it; a function of ``row``
    # alone would read the caller's ``step``, so nothing is parameterized.
    code1 = "for row in rows:\n    out.append(row.alpha[step])\n    step = step + 1"
    code2 = "for row in rows:\n    out.append(row.beta)\n    step = step + 1"
    blocks = [parse_block(code1), parse_block(code2)]

    u = Unifier()
    u.current_blocks = blocks

    def argument(block: list[ast.stmt]) -> ast.expr:
        loop = cast(ast.For, block[0])
        return cast(ast.Call, cast(ast.Expr, loop.body[0]).value).args[0]

    subst = Substitution()
    allowed = u._try_parameterize([argument(blocks[0]), argument(blocks[1])], subst, [0, 1])
    assert allowed is False
    assert subst.mappings == {}


def test_try_parameterize_logs_and_rejects_unreachable_name():
    # Loop-carried names are not visible at the call site and must be rejected
    code1 = "for value in data:\n    res = value\nout = value"
    code2 = "for item in data:\n    res = item\nout = item"
    blocks = [parse_block(code1), parse_block(code2)]

    u = Unifier()
    u.current_blocks = blocks
    expr_a = _assign_value(blocks[0], 1)
    expr_b = _assign_value(blocks[1], 1)

    subst = Substitution()
    allowed = u._try_parameterize([expr_a, expr_b], subst, [0, 1])
    assert allowed is False
    assert subst.mappings == {}


def test_unifier_rejects_lambda_lift_in_comprehension_when_constants_locked():
    # Disabling constant parameterization forces the engine down the lambda-lift guard path
    code1 = "result = [value + 1 for value in data]\nreturn result"
    code2 = "result = [value + 2 for value in data]\nreturn result"
    blocks = [parse_block(code1), parse_block(code2)]

    u = Unifier(parameterize_constants=False)
    subst = u.unify_blocks(blocks, [{}, {}])
    assert subst is None


# Removed legacy unittest.TestCase class tests to avoid duplication and reduce runtime.
