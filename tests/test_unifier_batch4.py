import ast
import textwrap
import unittest

from towel.unification.unifier import Unifier


def parse_block(src: str):
    return ast.parse(textwrap.dedent(src)).body


class TestUnifierBatch4(unittest.TestCase):
    def test_async_comprehension_flag_mismatch(self) -> None:
        # ListComp async generator not expressible directly, use GeneratorExp with async
        b0 = parse_block("g = (x for x in xs)")
        b1 = parse_block("g = (x async for x in xs)")
        uni = Unifier()
        # is_async mismatch should cause failure
        self.assertIsNone(uni.unify_blocks([b0, b1], [{}, {}]))

    def test_generator_exp_multiple_generators_mismatch(self) -> None:
        b0 = parse_block("g = (x+y for x in xs for y in ys)")
        b1 = parse_block("g = (x+y for x in xs)")
        uni = Unifier()
        self.assertIsNone(uni.unify_blocks([b0, b1], [{}, {}]))

    def test_with_optional_vars_presence_mismatch(self) -> None:
        b0 = parse_block("with open('a') as f: pass")
        b1 = parse_block("with open('a'): pass")
        uni = Unifier()
        self.assertIsNone(uni.unify_blocks([b0, b1], [{}, {}]))

    def test_with_items_count_mismatch(self) -> None:
        b0 = parse_block("with open('a') as f, open('b') as g: pass")
        b1 = parse_block("with open('a') as f: pass")
        uni = Unifier()
        self.assertIsNone(uni.unify_blocks([b0, b1], [{}, {}]))

    def test_except_handler_body_param(self) -> None:
        # Bodies differ by a constant; expect parameterization within body
        b0 = parse_block(
            """
        try:
            pass
        except Exception as e:
            x = 2
        """
        )
        b1 = parse_block(
            """
        try:
            pass
        except Exception as e:
            x = 3
        """
        )
        uni = Unifier()
        subst = uni.unify_blocks([b0, b1], [{}, {}])
        self.assertIsNotNone(subst)
        assert subst is not None
        self.assertGreaterEqual(len(subst.param_expressions), 1)

    def test_lambda_vararg_kwarg_combo_rejected(self) -> None:
        b0 = parse_block("f = lambda *args, **kw: 1")
        b1 = parse_block("f = lambda *args, **kw: 2")
        uni = Unifier()
        self.assertIsNone(uni.unify_blocks([b0, b1], [{}, {}]))

    def test_nested_destructuring_assignment_alpha(self) -> None:
        b0 = parse_block(
            """
        (a, (b, c)) = data
        sum1 = a + b + c
        """
        )
        b1 = parse_block(
            """
        (x, (y, z)) = data
        sum1 = x + y + z
        """
        )
        uni = Unifier()
        subst = uni.unify_blocks([b0, b1], [{}, {}])
        self.assertIsNotNone(subst)
        assert subst is not None
        self.assertEqual(len(subst.param_expressions), 0)

    def test_parameterize_statement_refusal(self) -> None:
        # Force _try_parameterize to see statements by crafting a mismatch at stmt level
        b0 = parse_block("x = 1")
        b1 = parse_block("if True:\n    x = 1")
        uni = Unifier()
        self.assertIsNone(uni.unify_blocks([b0, b1], [{}, {}]))

    def test_function_param_lifting_for_bound_accessible(self) -> None:
        # Expressions referencing bound variables within accessible scope should yield function params only if common bound vars detected.
        b0 = parse_block(
            """
        base = 1
        res = base + a
        """
        )
        b1 = parse_block(
            """
        base = 1
        res = base + b
        """
        )
        uni = Unifier()
        subst = uni.unify_blocks([b0, b1], [{}, {}])
        self.assertIsNotNone(subst)
        assert subst is not None
        # The differing Name (a vs b) is not a function param (no bound vars intersection)
        for pname in subst.param_expressions:
            self.assertFalse(subst.is_function_param(pname))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
