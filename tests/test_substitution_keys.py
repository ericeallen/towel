"""Substitution keys are the node's structure, memoized per node but never per identity."""

from __future__ import annotations

import ast
import copy

from towel.unification.substitution import Substitution, structural_text


def test_structural_text_is_the_dump_without_positions() -> None:
    node = ast.parse("f(x, y=1)", mode="eval").body
    assert structural_text(node) == ast.dump(node, include_attributes=False)
    assert structural_text(node) is structural_text(node)


def test_an_equal_copy_finds_the_mapping_of_the_original() -> None:
    original = ast.parse("a.b[0]", mode="eval").body
    substitution = Substitution()
    substitution.add_mapping(0, original, "__param_0")
    shifted = ast.parse("\n\na.b[0]", mode="eval").body
    assert substitution.get_param_for_expr(0, copy.deepcopy(original)) == "__param_0"
    assert substitution.get_param_for_expr(0, shifted) == "__param_0"
    assert substitution.get_param_for_expr(1, original) is None
    assert substitution.get_param_for_expr(0, ast.parse("a.b[1]", mode="eval").body) is None
