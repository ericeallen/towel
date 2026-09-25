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

"""Substitution keys are the node's structure, memoized per node but never per identity."""

from __future__ import annotations

import ast
import copy
import sys

import pytest

from towel.canonical_ast import canonical_dump
from towel.unification.substitution import Substitution, structural_text


def test_structural_text_is_the_canonical_dump() -> None:
    node = ast.parse("f(x, y=1)", mode="eval").body
    assert structural_text(node) == canonical_dump(node)
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


def _wider_than_the_decimal_limit(extra_bits: int = 0) -> ast.stmt:
    """``x = 0xfff...``: more decimal digits than the interpreter converts, when it limits them."""
    digits = max(sys.get_int_max_str_digits(), sys.int_info.default_max_str_digits)
    bits = int(digits * 3.33) + 8 + extra_bits
    return ast.parse(f"x = {(1 << bits) - 1:#x}").body[0]


def test_a_node_holding_an_int_wider_than_the_decimal_limit_has_a_key() -> None:
    wide, wider = _wider_than_the_decimal_limit(), _wider_than_the_decimal_limit(4)
    if sys.get_int_max_str_digits():
        with pytest.raises(ValueError):
            ast.dump(wide)  # the premise: a plain dump refuses the literal
    assert structural_text(wide) == structural_text(copy.deepcopy(wide))
    assert structural_text(wide) != structural_text(wider)
    assert structural_text(wide) != structural_text(ast.parse("x = 1").body[0])
