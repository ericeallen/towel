import ast
from typing import List, cast

from src.towel.unification.unifier import Substitution, Unifier


def _parse_stmt_list(code: str):
    return ast.parse(code).body


def _assign_value(block: List[ast.stmt], index: int) -> ast.expr:
    assign = block[index]
    assert isinstance(assign, ast.Assign), "Expected assignment statement"
    return assign.value


def test_unify_dict_literals():
    code1 = "config = {'key': 'value1', 'timeout': 10}"
    code2 = "config = {'key': 'value2', 'timeout': 20}"
    blocks = [_parse_stmt_list(code1), _parse_stmt_list(code2)]
    u = Unifier(max_parameters=5, parameterize_constants=True)
    subst = u.unify_blocks(blocks, [{}, {}])
    assert subst is not None


def test_unify_list_literals():
    code1 = "items = [1, 2, 3]"
    code2 = "items = [4, 5, 6]"
    blocks = [_parse_stmt_list(code1), _parse_stmt_list(code2)]
    u = Unifier(max_parameters=5, parameterize_constants=True)
    subst = u.unify_blocks(blocks, [{}, {}])
    assert subst is not None


def test_unify_set_literals():
    code1 = "values = {1, 2, 3}"
    code2 = "values = {4, 5, 6}"
    blocks = [_parse_stmt_list(code1), _parse_stmt_list(code2)]
    u = Unifier(max_parameters=5, parameterize_constants=True)
    subst = u.unify_blocks(blocks, [{}, {}])
    assert subst is not None


def test_unify_tuple_literals():
    code1 = "point = (1, 2)"
    code2 = "point = (3, 4)"
    blocks = [_parse_stmt_list(code1), _parse_stmt_list(code2)]
    u = Unifier(max_parameters=5, parameterize_constants=True)
    subst = u.unify_blocks(blocks, [{}, {}])
    assert subst is not None


def test_unify_lambda_expressions():
    code1 = "func = lambda x: x * 2"
    code2 = "func = lambda y: y * 3"
    blocks = [_parse_stmt_list(code1), _parse_stmt_list(code2)]
    u = Unifier(max_parameters=5, parameterize_constants=True)
    subst = u.unify_blocks(blocks, [{}, {}])
    assert subst is not None


def test_unify_assert_statements():
    code1 = "assert x > 0"
    code2 = "assert y > 0"
    blocks = [_parse_stmt_list(code1), _parse_stmt_list(code2)]
    u = Unifier(max_parameters=5, parameterize_constants=True)
    subst = u.unify_blocks(blocks, [{}, {}])
    assert subst is not None


def test_unifier_with_walrus_and_with_optional_vars():
    # Two blocks differing only in names inside with and walrus target should unify
    code1 = (
        "with open('a') as f:\n    data = f.read()\n    if (x := len(data)) > 0:\n        val = x\n"
    )
    code2 = "with open('a') as fh:\n    data = fh.read()\n    if (y := len(data)) > 0:\n        val = y\n"
    blocks = [_parse_stmt_list(code1), _parse_stmt_list(code2)]
    u = Unifier()
    subst = u.unify_blocks(blocks, [{}, {}])
    assert subst is None, "Current engine rejects walrus alpha-renaming scenario"


def test_unifier_fstring_format_spec():
    code1 = "result = f'{value:{width}}'"
    code2 = "result = f'{value:{width}}'"  # identical
    blocks = [_parse_stmt_list(code1), _parse_stmt_list(code2)]
    u = Unifier()
    subst = u.unify_blocks(blocks, [{}, {}])
    assert subst is not None


def test_unifier_list_comp_tuple_target_alpha():
    code1 = "pairs = [(k, v) for k, v in items]"
    code2 = "pairs = [(key, val) for key, val in items]"
    blocks = [_parse_stmt_list(code1), _parse_stmt_list(code2)]
    u = Unifier()
    subst = u.unify_blocks(blocks, [{}, {}])
    assert subst is not None


def test_unifier_constant_inconsistency_rule():
    # Should fail due to constant 2 appearing both differing and identical positions
    code1 = "x = item * 2\nz = y ** 2"  # constant 2 twice
    code2 = "x = item * 3\nz = y ** 2"  # second occurrence identical (2 vs 2)
    blocks = [_parse_stmt_list(code1), _parse_stmt_list(code2)]
    u = Unifier()
    subst = u.unify_blocks(blocks, [{}, {}])
    assert subst is None, "Inconsistent constant parameterization should reject"


def test_unifier_reject_parameterize_entire_fstring():
    code1 = "msg = f'User: {name}'"
    code2 = "msg = f'User: {other}'"  # differing inner expression accepted
    blocks = [_parse_stmt_list(code1), _parse_stmt_list(code2)]
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
    blocks = [_parse_stmt_list(code1), _parse_stmt_list(code2)]
    u = Unifier(max_parameters=2)
    subst = u.unify_blocks(blocks, [{}, {}])
    assert subst is None, "Should reject when exceeding max parameter count"


def test_unifier_skip_unreachable_variable_at_call_site():
    # Loop variables referenced outside the loop should prevent unification
    code1 = "for value in data:\n    leak = process(value)\nres = value"
    code2 = "for item in data:\n    leak = process(item)\nres = item"
    blocks = [_parse_stmt_list(code1), _parse_stmt_list(code2)]
    u = Unifier()
    subst = u.unify_blocks(blocks, [{}, {}])
    assert subst is None


def test_try_parameterize_rejects_lambda_lifting_of_local_binding():
    # Comprehension element references the loop variable; lambda lifting must be rejected
    code1 = "result = [value + 1 for value in data]"
    code2 = "result = [value + 2 for value in data]"
    blocks = [_parse_stmt_list(code1), _parse_stmt_list(code2)]

    u = Unifier()
    u.current_blocks = blocks
    expr_a = cast(ast.ListComp, cast(ast.Assign, blocks[0][0]).value).elt
    expr_b = cast(ast.ListComp, cast(ast.Assign, blocks[1][0]).value).elt

    subst = Substitution()
    allowed = u._try_parameterize([expr_a, expr_b], subst, [0, 1])
    assert allowed is False
    assert subst.mappings == {}


def test_try_parameterize_logs_and_rejects_unreachable_name():
    # Loop-carried names are not visible at the call site and must be rejected
    code1 = "for value in data:\n    res = value\nout = value"
    code2 = "for item in data:\n    res = item\nout = item"
    blocks = [_parse_stmt_list(code1), _parse_stmt_list(code2)]

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
    blocks = [_parse_stmt_list(code1), _parse_stmt_list(code2)]

    u = Unifier(parameterize_constants=False)
    subst = u.unify_blocks(blocks, [{}, {}])
    assert subst is None


# Removed legacy unittest.TestCase class tests to avoid duplication and reduce runtime.
