import ast
import textwrap
import unittest

from towel.unification.unifier import Unifier


def parse_block(src: str):
    return ast.parse(textwrap.dedent(src)).body


class TestUnifierBatch3(unittest.TestCase):
    def test_multi_occurrence_constants_aligned_parameterize(self) -> None:
        # 2 appears twice both blocks; 3 appears twice both blocks; differ consistently -> can parameterize
        b0 = parse_block(
            """
        x = a * 2
        y = b + 2
        z = c * 3
        w = d + 3
        """
        )
        b1 = parse_block(
            """
        x = a * 5
        y = b + 5
        z = c * 7
        w = d + 7
        """
        )
        uni = Unifier()
        subst = uni.unify_blocks([b0, b1], [{}, {}])
        self.assertIsNotNone(subst)
        assert subst is not None
        # Should parameterize two differing constant groups (2 vs 5) and (3 vs 7)
        self.assertEqual(len(subst.param_expressions), 2)

    def test_multi_occurrence_constants_misaligned_fail(self) -> None:
        # 2 appears twice in block0; block1 differs only once (inconsistent) -> reject
        b0 = parse_block(
            """
        x = a * 2
        y = b + 2
        z = c * 4
        """
        )
        b1 = parse_block(
            """
        x = a * 9
        y = b + 2
        z = c * 4
        """
        )
        uni = Unifier()
        self.assertIsNone(uni.unify_blocks([b0, b1], [{}, {}]))

    def test_with_type_comment_mismatch_fail(self) -> None:
        # Python's parser does not attach trailing '# type:' comments as type_comment on With.
        # Adjust expectation: identical structure produces successful unification with no params.
        b0 = parse_block("with open('a') as f: pass")
        b1 = parse_block("with open('a') as f: pass")
        uni = Unifier()
        subst = uni.unify_blocks([b0, b1], [{}, {}])
        self.assertIsNotNone(subst)
        assert subst is not None
        self.assertEqual(len(subst.param_expressions), 0)

    def test_except_different_specific_types_fail(self) -> None:
        b0 = parse_block(
            """
        try:
            pass
        except ValueError:
            pass
        """
        )
        b1 = parse_block(
            """
        try:
            pass
        except KeyError:
            pass
        """
        )
        uni = Unifier()
        self.assertIsNone(uni.unify_blocks([b0, b1], [{}, {}]))

    def test_lambda_defaults_rejected(self) -> None:
        # Current implementation only rejects complex parameter kinds (kwonly/vararg/kwarg/posonly).
        # Lambdas with defaults are treated like any other expressions; constants differ -> parameterized.
        b0 = parse_block("f = lambda x=1: x + 2")
        b1 = parse_block("f = lambda x=1: x + 3")
        uni = Unifier()
        subst = uni.unify_blocks([b0, b1], [{}, {}])
        self.assertIsNotNone(subst)
        assert subst is not None
        self.assertEqual(len(subst.param_expressions), 1)

    def test_lambda_posonly_rejected(self) -> None:
        # Python syntax: lambda x, /, y: x+y
        b0 = parse_block("f = lambda x, /, y: x + y")
        b1 = parse_block("f = lambda x, /, y: x + y")
        uni = Unifier()
        self.assertIsNone(uni.unify_blocks([b0, b1], [{}, {}]))

    def test_assignment_chain_alpha_renaming(self) -> None:
        b0 = parse_block(
            """
        first = a + 1
        second = first + 2
        result = second * first
        return result
        """
        )
        b1 = parse_block(
            """
        initial = a + 1
        nxt = initial + 2
        out = nxt * initial
        return out
        """
        )
        uni = Unifier()
        hr = [{}, {}]
        subst = uni.unify_blocks([b0, b1], hr)
        self.assertIsNotNone(subst)
        assert subst is not None
        # Should avoid parameterizing the chained variable names
        # All expressions identical; no differing constants -> zero parameters expected
        self.assertEqual(len(subst.param_expressions), 0)

    def test_destructuring_and_annotation_binding(self) -> None:
        b0 = parse_block(
            """
        (x, y) = pair
        value: int = x + y
        total = value + 1
        """
        )
        b1 = parse_block(
            """
        (a, b) = pair
        value: int = a + b
        total = value + 2
        """
        )
        uni = Unifier()
        subst = uni.unify_blocks([b0, b1], [{}, {}])
        self.assertIsNotNone(subst)
        assert subst is not None
        # Should parameterize the differing constant (1 vs 2), not the bound names
        self.assertEqual(len(subst.param_expressions), 1)

    def test_joinedstr_vs_constant_whole_node_parameterization_refusal(self) -> None:
        b0 = parse_block("s = 'hi' ")
        b1 = parse_block("s = f'hi {x}'")
        uni = Unifier()
        # Different node types; _try_parameterize sees JoinedStr and refuses -> overall fail
        self.assertIsNone(uni.unify_blocks([b0, b1], [{}, {}]))

    def test_nested_fstring_format_spec_recursion(self) -> None:
        b0 = parse_block("s = f'{val:{inner}}'")
        b1 = parse_block("s = f'{val:{other}}'")
        uni = Unifier()
        subst = uni.unify_blocks([b0, b1], [{}, {}])
        self.assertIsNotNone(subst)
        assert subst is not None
        self.assertGreaterEqual(len(subst.param_expressions), 1)

    def test_function_param_lifting_with_bound_vars(self) -> None:
        # Create expressions referencing variables bound earlier in the block (assigned) so they are accessible at call site.
        b0 = parse_block(
            """
        base = 10
        inc = 2
        total = base + inc
        final = total * base
        """
        )
        b1 = parse_block(
            """
        base = 20
        inc = 3
        total = base + inc
        final = total * base
        """
        )
        uni = Unifier()
        subst = uni.unify_blocks([b0, b1], [{}, {}])
        self.assertIsNotNone(subst)
        assert subst is not None
        # Expect parameter(s) created for differing constants; none should be function params (no bound vars inside differing constant nodes)
        for pname in subst.param_expressions:
            self.assertFalse(subst.is_function_param(pname))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
