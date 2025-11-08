import ast
import textwrap
import unittest

from towel.unification.unifier import Unifier


def parse_block(src: str):
    return ast.parse(textwrap.dedent(src)).body


class TestUnifierCore(unittest.TestCase):
    def test_constant_inconsistency_rejection(self) -> None:
        # Inconsistent constants: 2 appears twice in block0, but only once differs in block1
        b0 = parse_block(
            """
            x = a * 2
            z = y ** 2
            """
        )
        b1 = parse_block(
            """
            x = a * 3
            z = y ** 2
            """
        )
        uni = Unifier()
        hr = [{}, {}]
        self.assertIsNone(uni.unify_blocks([b0, b1], hr))

    def test_constant_parameterization_consistent(self) -> None:
        # Consistent differing constants at matching positions -> parameterize as one param
        b0 = parse_block(
            """
            x = a * 2
            z = y ** 2
            """
        )
        b1 = parse_block(
            """
            x = a * 3
            z = y ** 3
            """
        )
        uni = Unifier()
        hr = [{}, {}]
        subst = uni.unify_blocks([b0, b1], hr)
        self.assertIsNotNone(subst)
        assert subst is not None
        # Expect one parameter for the constant differing both places
        self.assertEqual(len(subst.param_expressions), 1)

    def test_name_alpha_and_parameterize(self) -> None:
        # Free-variable name differs; unifier should map names, then parameterize
        b0 = parse_block(
            """
            out = admin + 1
            return out
            """
        )
        b1 = parse_block(
            """
            out = user + 1
            return out
            """
        )
        uni = Unifier()
        hr = [{}, {}]
        subst = uni.unify_blocks([b0, b1], hr)
        self.assertIsNotNone(subst)
        assert subst is not None
        self.assertGreaterEqual(len(subst.param_expressions), 1)
        # Hygienic renames should record correspondence user -> admin for block1
        self.assertIn((1, "user"), uni.alpha_renamings)

    def test_for_loop_var_alpha(self) -> None:
        b0 = parse_block(
            """
            for i in items:
                y = i + 1
            else:
                y = y
            """
        )
        b1 = parse_block(
            """
            for j in items:
                y = j + 1
            else:
                y = y
            """
        )
        uni = Unifier()
        hr = [{}, {}]
        subst = uni.unify_blocks([b0, b1], hr)
        self.assertIsNotNone(subst)
        assert subst is not None
        # Different loop var names shouldn't force params
        self.assertEqual(len(subst.param_expressions), 0)

    def test_for_loop_tuple_targets_alpha(self) -> None:
        b0 = parse_block(
            """
            for (k, v) in pairs:
                a = k
                b = v
            """
        )
        b1 = parse_block(
            """
            for (key, value) in pairs:
                a = key
                b = value
            """
        )
        uni = Unifier()
        hr = [{}, {}]
        subst = uni.unify_blocks([b0, b1], hr)
        self.assertIsNotNone(subst)
        assert subst is not None
        self.assertEqual(len(subst.param_expressions), 0)

    def test_lambda_vararg_rejected(self) -> None:
        b0 = parse_block("f = lambda *args: args")
        b1 = parse_block("f = lambda *args: args")
        uni = Unifier()
        hr = [{}, {}]
        self.assertIsNone(uni.unify_blocks([b0, b1], hr))

    def test_except_handler_name_mismatch_none_vs_present(self) -> None:
        b0 = parse_block(
            """
            try:
                pass
            except Exception as e:
                pass
            """
        )
        b1 = parse_block(
            """
            try:
                pass
            except Exception:
                pass
            """
        )
        uni = Unifier()
        hr = [{}, {}]
        self.assertIsNone(uni.unify_blocks([b0, b1], hr))

    def test_except_handler_name_both_present(self) -> None:
        b0 = parse_block(
            """
            try:
                pass
            except Exception as ex:
                x = ex
            """
        )
        b1 = parse_block(
            """
            try:
                pass
            except Exception as err:
                x = err
            """
        )
        uni = Unifier()
        hr = [{}, {}]
        subst = uni.unify_blocks([b0, b1], hr)
        self.assertIsNotNone(subst)

    def test_with_optional_vars_alpha(self) -> None:
        b0 = parse_block(
            """
            with open('a') as f:
                x = f
            """
        )
        b1 = parse_block(
            """
            with open('a') as g:
                x = g
            """
        )
        uni = Unifier()
        hr = [{}, {}]
        subst = uni.unify_blocks([b0, b1], hr)
        self.assertIsNotNone(subst)
        assert subst is not None
        self.assertEqual(len(subst.param_expressions), 0)

    def test_joinedstr_unify_inner_formatted(self) -> None:
        b0 = parse_block("s = f'hi {x}'")
        b1 = parse_block("s = f'hi {y}'")
        uni = Unifier()
        hr = [{}, {}]
        subst = uni.unify_blocks([b0, b1], hr)
        self.assertIsNotNone(subst)
        assert subst is not None
        # Should parameterize inner Name, not entire f-string
        self.assertGreaterEqual(len(subst.param_expressions), 1)

    def test_max_parameters_limit(self) -> None:
        # Requires two parameters (a vs c) and (b vs d), but max_parameters=1
        b0 = parse_block("x = a + b")
        b1 = parse_block("x = c + d")
        uni = Unifier(max_parameters=1)
        hr = [{}, {}]
        self.assertIsNone(uni.unify_blocks([b0, b1], hr))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
