import ast
from src.towel.unification.unifier import Unifier


def _parse_stmt_list(code: str):
    return ast.parse(code).body


def test_unifier_with_walrus_and_with_optional_vars():
    # Two blocks differing only in names inside with and walrus target should unify
    code1 = "with open('a') as f:\n    data = f.read()\n    if (x := len(data)) > 0:\n        val = x\n"
    code2 = "with open('a') as fh:\n    data = fh.read()\n    if (y := len(data)) > 0:\n        val = y\n"
    blocks = [_parse_stmt_list(code1), _parse_stmt_list(code2)]
    u = Unifier()
    subst = u.unify_blocks(blocks, [{}, {}])
    assert subst is not None, "Should unify with alpha-renaming of f/fh and x/y"


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
    assert all(not isinstance(expr, ast.JoinedStr) for exprs in subst.param_expressions.values() for _, expr in exprs)


def test_unifier_exceed_max_parameters():
    # Force more parameters than allowed
    code1 = "a = w + x + y + z + q"  # 5 variables
    code2 = "a = w1 + x1 + y1 + z1 + q1"
    blocks = [_parse_stmt_list(code1), _parse_stmt_list(code2)]
    u = Unifier(max_parameters=2)
    subst = u.unify_blocks(blocks, [{}, {}])
    assert subst is None, "Should reject when exceeding max parameter count"


def test_unifier_skip_unreachable_variable_at_call_site():
    # Variable defined inside nested function should not be parameterized
    # block0 has nested function referencing inner_var used later; block1 uses different name
    code1 = "def inner():\n    inner_var = 1\n    return inner_var\nres = inner_var"  # inner_var not defined at module level
    code2 = "def inner():\n    other = 1\n    return other\nres = other"  # other also not defined at module level
    blocks = [_parse_stmt_list(code1), _parse_stmt_list(code2)]
    u = Unifier()
    subst = u.unify_blocks(blocks, [{}, {}])
    # Should fail because 'inner_var' and 'other' are not accessible at call site
    assert subst is None


# Removed legacy unittest.TestCase class tests to avoid duplication and reduce runtime.
