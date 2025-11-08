#!/usr/bin/env python3
"""
Targeted tests to exercise less-traveled Unifier branches (with/namedexpr/lambda/comprehensions).
"""

import unittest
import ast

from src.towel.unification.unifier import Unifier


def _parse_block(src: str):
    tree = ast.parse(src)
    return tree.body


class TestUnifierWithAndNamedExpr(unittest.TestCase):
    def setUp(self) -> None:
        self.unifier = Unifier(max_parameters=5, parameterize_constants=True)

    def test_unify_with_optional_vars_alpha(self) -> None:
        b1 = _parse_block(
            """
result = None
with open(path) as a:
    result = a.read()
return result
"""
        )
        b2 = _parse_block(
            """
result = None
with open(path) as alias:
    result = alias.read()
return result
"""
        )
        res = self.unifier.unify_blocks([b1, b2], [{}, {}])
        self.assertIsNotNone(res)

    def test_unify_named_expr_alpha(self) -> None:
        b1 = _parse_block(
            """
if (t := get()):
    x = t
"""
        )
        b2 = _parse_block(
            """
if (temp := get()):
    x = temp
"""
        )
        # NamedExpr handling is conservative in current unifier; ensure it doesn't crash
        _ = self.unifier.unify_blocks([b1, b2], [{}, {}])


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
