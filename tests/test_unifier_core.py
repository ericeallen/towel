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
import unittest

import pytest

from tests.test_helpers import parse_block
from towel.unification.unifier import Unifier


class TestUnifierCore(unittest.TestCase):
    def test_constant_inconsistency_rejection(self) -> None:
        # Inconsistent constants: 2 appears twice in block0, but only once differs in block1
        b0 = parse_block("""
            x = a * 2
            z = y ** 2
            """)
        b1 = parse_block("""
            x = a * 3
            z = y ** 2
            """)
        uni = Unifier()
        hr: list[dict[str, str]] = [{}, {}]
        self.assertIsNone(uni.unify_blocks([b0, b1], hr))

    def test_constant_parameterization_consistent(self) -> None:
        # Consistent differing constants at matching positions -> parameterize as one param
        b0 = parse_block("""
            x = a * 2
            z = y ** 2
            """)
        b1 = parse_block("""
            x = a * 3
            z = y ** 3
            """)
        uni = Unifier()
        hr: list[dict[str, str]] = [{}, {}]
        subst = uni.unify_blocks([b0, b1], hr)
        self.assertIsNotNone(subst)
        assert subst is not None
        # Expect one parameter for the constant differing both places
        self.assertEqual(len(subst.param_expressions), 1)

    def test_name_alpha_and_parameterize(self) -> None:
        # Free-variable name differs; unifier should map names, then parameterize
        b0 = parse_block("""
            out = admin + 1
            return out
            """)
        b1 = parse_block("""
            out = user + 1
            return out
            """)
        uni = Unifier()
        hr: list[dict[str, str]] = [{}, {}]
        subst = uni.unify_blocks([b0, b1], hr)
        self.assertIsNotNone(subst)
        assert subst is not None
        self.assertGreaterEqual(len(subst.param_expressions), 1)
        # Hygienic renames should record correspondence user -> admin for block1
        self.assertIn((1, "user"), uni.alpha_renamings)

    def test_for_loop_var_alpha(self) -> None:
        b0 = parse_block("""
            for i in items:
                y = i + 1
            else:
                y = y
            """)
        b1 = parse_block("""
            for j in items:
                y = j + 1
            else:
                y = y
            """)
        uni = Unifier()
        hr: list[dict[str, str]] = [{}, {}]
        subst = uni.unify_blocks([b0, b1], hr)
        self.assertIsNotNone(subst)
        assert subst is not None
        # Different loop var names shouldn't force params
        self.assertEqual(len(subst.param_expressions), 0)

    def test_for_loop_tuple_targets_alpha(self) -> None:
        b0 = parse_block("""
            for (k, v) in pairs:
                a = k
                b = v
            """)
        b1 = parse_block("""
            for (key, value) in pairs:
                a = key
                b = value
            """)
        uni = Unifier()
        hr: list[dict[str, str]] = [{}, {}]
        subst = uni.unify_blocks([b0, b1], hr)
        self.assertIsNotNone(subst)
        assert subst is not None
        self.assertEqual(len(subst.param_expressions), 0)

    def test_lambda_vararg_rejected(self) -> None:
        b0 = parse_block("f = lambda *args: args")
        b1 = parse_block("f = lambda *args: args")
        uni = Unifier()
        hr: list[dict[str, str]] = [{}, {}]
        self.assertIsNone(uni.unify_blocks([b0, b1], hr))

    def test_except_handler_name_mismatch_none_vs_present(self) -> None:
        b0 = parse_block("""
            try:
                pass
            except Exception as e:
                pass
            """)
        b1 = parse_block("""
            try:
                pass
            except Exception:
                pass
            """)
        uni = Unifier()
        hr: list[dict[str, str]] = [{}, {}]
        self.assertIsNone(uni.unify_blocks([b0, b1], hr))

    def test_except_handler_name_both_present(self) -> None:
        b0 = parse_block("""
            try:
                pass
            except Exception as ex:
                x = ex
            """)
        b1 = parse_block("""
            try:
                pass
            except Exception as err:
                x = err
            """)
        uni = Unifier()
        hr: list[dict[str, str]] = [{}, {}]
        subst = uni.unify_blocks([b0, b1], hr)
        self.assertIsNotNone(subst)

    def test_with_optional_vars_alpha(self) -> None:
        b0 = parse_block("""
            with open('a') as f:
                x = f
            """)
        b1 = parse_block("""
            with open('a') as g:
                x = g
            """)
        uni = Unifier()
        hr: list[dict[str, str]] = [{}, {}]
        subst = uni.unify_blocks([b0, b1], hr)
        self.assertIsNotNone(subst)
        assert subst is not None
        self.assertEqual(len(subst.param_expressions), 0)

    def test_joinedstr_unify_inner_formatted(self) -> None:
        b0 = parse_block("s = f'hi {x}'")
        b1 = parse_block("s = f'hi {y}'")
        uni = Unifier()
        hr: list[dict[str, str]] = [{}, {}]
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
        hr: list[dict[str, str]] = [{}, {}]
        self.assertIsNone(uni.unify_blocks([b0, b1], hr))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


def test_assignment_expression_is_never_parameterized() -> None:
    import ast as _ast
    from towel.unification.unifier import Unifier

    blocks = [
        _ast.parse("v = (m := f(s))\nuse(v)\n").body,
        _ast.parse("v = g(s)\nuse(v)\n").body,
    ]
    assert Unifier().unify_blocks(blocks, [{}, {}]) is None


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("0", "0.0"),
        ("0", "False"),
        ("1", "True"),
        ("1.0", "True"),
        ("0", "0j"),
        ("0.0", "0j"),
    ],
)
def test_equal_constants_of_different_types_are_different_constants(
    first: str, second: str
) -> None:
    # They compare equal and hash alike, but a helper hard-coding one would
    # hand the other block a value of the wrong type.
    blocks = [ast.parse(f"x = {first}").body, ast.parse(f"x = {second}").body]
    substitution = Unifier().unify_blocks(blocks, [{}, {}])
    assert substitution is not None
    assert [
        [ast.unparse(node) for _, node in expressions]
        for expressions in substitution.param_expressions.values()
    ] == [[first, second]]


def test_a_false_elsewhere_is_no_occurrence_of_a_differing_zero() -> None:
    blocks = [
        ast.parse("x = 0\ny = False").body,
        ast.parse("x = 1\ny = False").body,
    ]
    substitution = Unifier().unify_blocks(blocks, [{}, {}])
    assert substitution is not None
    assert len(substitution.param_expressions) == 1


def test_the_same_constant_of_the_same_type_needs_no_parameter() -> None:
    for literal in ("0", "0.0", "False", "'a'", "b'a'", "None", "...", "1e400"):
        blocks = [ast.parse(f"x = {literal}").body, ast.parse(f"x = {literal}").body]
        substitution = Unifier().unify_blocks(blocks, [{}, {}])
        assert substitution is not None and not substitution.param_expressions, literal


def _match_blocks(first: str, second: str) -> list[list[ast.stmt]]:
    source = (
        "match value:\n    case {}:\n        found = 'hit'\n    case _:\n        found = 'miss'\n"
    )
    return [ast.parse(source.format(pattern)).body for pattern in (first, second)]


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("[1, rest]", "[2, rest]"),
        ("Color.RED", "Color.BLUE"),
        ('{"a": found}', '{"b": found}'),
        ("Point(x=1)", "Point(x=2)"),
        ("-1", "-2"),
        ("None", "True"),
        ("[kind(), x]", "[kind(), y]"),
    ],
)
def test_a_pattern_part_that_differs_is_no_parameter(first: str, second: str) -> None:
    # A bare parameter name where a pattern expects a value is a capture that
    # matches anything; a mapping key cannot be a bare name at all.
    assert Unifier().unify_blocks(_match_blocks(first, second), [{}, {}]) is None


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("First()", "Second()"),
        ("[mod_a.First()]", "[mod_b.First()]"),
        ("lights.RED", "paints.RED"),
        ("{keys_a.RED: found}", "{keys_b.RED: found}"),
    ],
)
def test_a_class_or_dotted_root_that_differs_is_a_parameter(first: str, second: str) -> None:
    substitution = Unifier().unify_blocks(_match_blocks(first, second), [{}, {}])
    assert substitution is not None
    assert len(substitution.param_expressions) == 1


def test_a_lambda_default_that_differs_is_a_parameter() -> None:
    # A default is evaluated where the lambda stands; each block keeps its own.
    blocks = [
        ast.parse("g = lambda v, s=k: v * s\nh = g(3)").body,
        ast.parse("g = lambda v, s=j: v * s\nh = g(3)").body,
    ]
    substitution = Unifier().unify_blocks(blocks, [{}, {}])
    assert substitution is not None
    assert [
        [ast.unparse(node) for _, node in expressions]
        for expressions in substitution.param_expressions.values()
    ] == [["k", "j"]]


def test_lambdas_with_different_defaults_do_not_unify() -> None:
    blocks = [
        ast.parse("g = lambda v, s=k: v * s\nh = g(3)").body,
        ast.parse("g = lambda v, s: v * s\nh = g(3, k)").body,
    ]
    assert Unifier().unify_blocks(blocks, [{}, {}]) is None
