import ast
import textwrap
import unittest

from towel.unification.unifier import Unifier


def parse_block(src: str):
    return ast.parse(textwrap.dedent(src)).body


class TestUnifierBatch2(unittest.TestCase):
    def test_listcomp_generator_count_mismatch_failure(self) -> None:
        b0 = parse_block("result = [x for x in xs if x > 0 if x < 10]")
        b1 = parse_block("result = [x for x in xs if x > 0]")
        uni = Unifier()
        self.assertIsNone(uni.unify_blocks([b0, b1], [{}, {}]))

    def test_listcomp_tuple_target_alpha(self) -> None:
        b0 = parse_block("pairs = [(k, v) for (k, v) in items]")
        b1 = parse_block("pairs = [(key, value) for (key, value) in items]")
        uni = Unifier()
        subst = uni.unify_blocks([b0, b1], [{}, {}])
        self.assertIsNotNone(subst)
        assert subst is not None
        self.assertEqual(len(subst.param_expressions), 0)

    def test_listcomp_tuple_vs_name_target_failure(self) -> None:
        b0 = parse_block("vals = [a for a in seq]")
        b1 = parse_block("vals = [x for (x, y) in seq]")  # invalid structure difference
        # Note: second block invalid Python unless seq yields tuples; still parses
        uni = Unifier()
        self.assertIsNone(uni.unify_blocks([b0, b1], [{}, {}]))

    def test_setcomp_parametrizes_inner(self) -> None:
        b0 = parse_block("s = {x + 1 for x in xs}")
        b1 = parse_block("s = {x + 2 for x in xs}")
        uni = Unifier()
        subst = uni.unify_blocks([b0, b1], [{}, {}])
        self.assertIsNotNone(subst)
        assert subst is not None
        self.assertGreaterEqual(len(subst.param_expressions), 1)

    def test_dictcomp_key_and_value_param(self) -> None:
        b0 = parse_block("d = {k: v + 1 for k, v in items}")
        b1 = parse_block("d = {k: v + 2 for k, v in items}")
        uni = Unifier()
        subst = uni.unify_blocks([b0, b1], [{}, {}])
        self.assertIsNotNone(subst)
        assert subst is not None
        self.assertGreaterEqual(len(subst.param_expressions), 1)

    def test_generator_exp_param(self) -> None:
        b0 = parse_block("g = (x * 2 for x in xs)")
        b1 = parse_block("g = (x * 3 for x in xs)")
        uni = Unifier()
        subst = uni.unify_blocks([b0, b1], [{}, {}])
        self.assertIsNotNone(subst)

    def test_with_multiple_items_tuple_optional_vars(self) -> None:
        b0 = parse_block("with open('a') as f, open('b') as g: r = (f, g)")
        b1 = parse_block("with open('a') as x, open('b') as y: r = (x, y)")
        uni = Unifier()
        subst = uni.unify_blocks([b0, b1], [{}, {}])
        self.assertIsNotNone(subst)
        assert subst is not None
        self.assertEqual(len(subst.param_expressions), 0)

    def test_except_type_mismatch_failure(self) -> None:
        b0 = parse_block(
            """
        try:
            pass
        except Exception:
            pass
        """
        )
        b1 = parse_block(
            """
        try:
            pass
        except:
            pass
        """
        )
        uni = Unifier()
        self.assertIsNone(uni.unify_blocks([b0, b1], [{}, {}]))

    def test_namedexpr_value_parameterization(self) -> None:
        b0 = parse_block("res = (a := foo())")
        b1 = parse_block("res = (b := bar())")
        uni = Unifier()
        subst = uni.unify_blocks([b0, b1], [{}, {}])
        self.assertIsNotNone(subst)
        assert subst is not None
        self.assertGreaterEqual(len(subst.param_expressions), 1)

    def test_lambda_kwonly_rejected(self) -> None:
        b0 = parse_block("f = lambda x, *, y: x + y")
        b1 = parse_block("f = lambda x, *, y: x + y")
        uni = Unifier()
        self.assertIsNone(uni.unify_blocks([b0, b1], [{}, {}]))

    def test_lambda_kwarg_rejected(self) -> None:
        b0 = parse_block("f = lambda x, **kw: x")
        b1 = parse_block("f = lambda x, **kw: x")
        uni = Unifier()
        self.assertIsNone(uni.unify_blocks([b0, b1], [{}, {}]))

    def test_fstring_nested_format_spec_parameterization(self) -> None:
        b0 = parse_block("s = f'{val:{w}}'")
        b1 = parse_block("s = f'{val:{u}}'")
        uni = Unifier()
        subst = uni.unify_blocks([b0, b1], [{}, {}])
        self.assertIsNotNone(subst)
        assert subst is not None
        self.assertGreaterEqual(len(subst.param_expressions), 1)

    def test_fstring_literal_mismatch_failure(self) -> None:
        b0 = parse_block("s = f'hi {x}'")
        b1 = parse_block("s = f'bye {x}'")
        uni = Unifier()
        self.assertIsNone(uni.unify_blocks([b0, b1], [{}, {}]))

    def test_bound_var_param_refusal_in_loop_body(self) -> None:
        b0 = parse_block("for i in xs:\n    y = i + a")
        b1 = parse_block("for j in xs:\n    y = j + b")
        uni = Unifier()
        # Difference i+a vs j+b uses bound vars + differing names; should unify loop alpha
        # but parameterization of expression should fail due to bound variable accessibility
        subst = uni.unify_blocks([b0, b1], [{}, {}])
        # Unification may still succeed with alpha-renaming; ensure no function param created
        if subst is not None:
            for pname, exprs in subst.param_expressions.items():
                self.assertFalse(subst.is_function_param(pname))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
