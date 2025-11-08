import ast
import textwrap
import unittest

from towel.unification.unifier import Unifier


def parse_block(src: str):
    return ast.parse(textwrap.dedent(src)).body


class TestUnifierBatch5(unittest.TestCase):
    def test_operator_node_difference_refusal(self) -> None:
        b0 = parse_block("x = a + b")
        b1 = parse_block("x = a - b")
        uni = Unifier()
        self.assertIsNone(uni.unify_blocks([b0, b1], [{}, {}]))

    def test_three_block_constant_parameterization(self) -> None:
        # 3 blocks; _check_constant_consistency len(values)!=2 early True path
        b0 = parse_block("x = a * 2")
        b1 = parse_block("x = a * 3")
        b2 = parse_block("x = a * 4")
        uni = Unifier()
        subst = uni.unify_blocks([b0, b1, b2], [{}, {}, {}])
        self.assertIsNotNone(subst)
        assert subst is not None
        self.assertEqual(len(subst.param_expressions), 1)

    def test_attribute_name_parameterization(self) -> None:
        b0 = parse_block("x = obj.a")
        b1 = parse_block("x = obj.b")
        uni = Unifier()
        subst = uni.unify_blocks([b0, b1], [{}, {}])
        self.assertIsNotNone(subst)
        assert subst is not None
        self.assertEqual(len(subst.param_expressions), 1)

    def test_lambda_zero_params_parameterize_body_constant(self) -> None:
        b0 = parse_block("f = lambda: 2")
        b1 = parse_block("f = lambda: 3")
        uni = Unifier()
        subst = uni.unify_blocks([b0, b1], [{}, {}])
        self.assertIsNotNone(subst)
        assert subst is not None
        self.assertEqual(len(subst.param_expressions), 1)

    def test_bare_except_both_present_success(self) -> None:
        b0 = parse_block(
            """
        try:
            pass
        except:
            x = 1
        """
        )
        b1 = parse_block(
            """
        try:
            pass
        except:
            x = 2
        """
        )
        uni = Unifier()
        subst = uni.unify_blocks([b0, b1], [{}, {}])
        self.assertIsNotNone(subst)
        assert subst is not None
        self.assertEqual(len(subst.param_expressions), 1)

    def test_dictcomp_if_count_mismatch_failure(self) -> None:
        b0 = parse_block("d = {k: v for k, v in items if k > 0 if v < 10}")
        b1 = parse_block("d = {k: v for k, v in items if k > 0}")
        uni = Unifier()
        self.assertIsNone(uni.unify_blocks([b0, b1], [{}, {}]))

    def test_genexp_if_count_mismatch_failure(self) -> None:
        b0 = parse_block("g = (x for x in xs if x > 0 if x < 10)")
        b1 = parse_block("g = (x for x in xs if x > 0)")
        uni = Unifier()
        self.assertIsNone(uni.unify_blocks([b0, b1], [{}, {}]))

    def test_with_tuple_optional_vars_arity_mismatch_failure(self) -> None:
        b0 = parse_block("with mgr() as (x, y): pass")
        b1 = parse_block("with mgr() as (x, y, z): pass")
        uni = Unifier()
        self.assertIsNone(uni.unify_blocks([b0, b1], [{}, {}]))

    def test_unifier_reset_param_counter(self) -> None:
        b0 = parse_block("x = a + 2")
        b1 = parse_block("x = a + 3")
        uni = Unifier()
        subst1 = uni.unify_blocks([b0, b1], [{}, {}])
        self.assertIsNotNone(subst1)
        assert subst1 is not None
        names1 = list(subst1.param_expressions.keys())
        self.assertIn("__param_0", names1)
        # Reset and ensure next param starts again at __param_0
        uni.reset()
        subst2 = uni.unify_blocks([b0, b1], [{}, {}])
        self.assertIsNotNone(subst2)
        assert subst2 is not None
        names2 = list(subst2.param_expressions.keys())
        self.assertIn("__param_0", names2)

    def test_joinedstr_conversion_mismatch_failure(self) -> None:
        b0 = parse_block("s = f'{x!r}'")
        b1 = parse_block("s = f'{x!s}'")
        uni = Unifier()
        self.assertIsNone(uni.unify_blocks([b0, b1], [{}, {}]))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
