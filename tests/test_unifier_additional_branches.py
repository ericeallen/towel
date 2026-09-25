#!/usr/bin/env python3
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

"""
Targeted tests to exercise less-traveled Unifier branches (with/namedexpr/lambda/comprehensions).
"""

import unittest
import ast

from towel.unification.unifier import Unifier


def _parse_block(src: str):
    tree = ast.parse(src)
    return tree.body


class TestUnifierWithAndNamedExpr(unittest.TestCase):
    def setUp(self) -> None:
        self.unifier = Unifier(max_parameters=5, parameterize_constants=True)

    def test_unify_with_optional_vars_alpha(self) -> None:
        b1 = _parse_block("""
result = None
with open(path) as a:
    result = a.read()
return result
""")
        b2 = _parse_block("""
result = None
with open(path) as alias:
    result = alias.read()
return result
""")
        res = self.unifier.unify_blocks([b1, b2], [{}, {}])
        self.assertIsNotNone(res)

    def test_unify_named_expr_differing_targets_is_alpha_renamed(self) -> None:
        """A walrus target is a block-level binding: ``t`` and ``temp`` are alpha-equivalent.

        The renaming persists past the assignment expression, so the later
        read ``x = t`` matches ``x = temp`` as it would for an assignment.
        """
        b1 = _parse_block("""
if (t := get()):
    x = t
""")
        b2 = _parse_block("""
if (temp := get()):
    x = temp
""")
        res = self.unifier.unify_blocks([b1, b2], [{}, {}])
        self.assertIsNotNone(res)
        assert res is not None
        self.assertEqual(res.param_expressions, {})

    def test_unify_named_expr_identical_targets(self) -> None:
        b1 = _parse_block("""
if (t := get()):
    x = t
""")
        b2 = _parse_block("""
if (t := get()):
    x = t
""")
        res = self.unifier.unify_blocks([b1, b2], [{}, {}])
        self.assertIsNotNone(res)
        assert res is not None
        self.assertEqual(res.param_expressions, {})


class TestUnifierLambdaAndComprehensions(unittest.TestCase):
    def setUp(self) -> None:
        self.unifier = Unifier(max_parameters=5, parameterize_constants=True)

    def test_unify_lambda_param_alpha(self) -> None:
        b1 = _parse_block("fn = lambda x: x + 1")
        b2 = _parse_block("fn = lambda y: y + 1")
        res = self.unifier.unify_blocks([b1, b2], [{}, {}])
        self.assertIsNotNone(res)

    def test_unify_dict_comp_alpha(self) -> None:
        b1 = _parse_block("result = {k: v for k, v in items}")
        b2 = _parse_block("result = {key: val for key, val in items}")
        res = self.unifier.unify_blocks([b1, b2], [{}, {}])
        self.assertIsNotNone(res)


if __name__ == "__main__":
    unittest.main(verbosity=2)
