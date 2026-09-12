"""Compatibility contracts for deprecated, non-production AST transforms."""

import ast

import pytest

from towel.unification.ast_normalizer import (
    ArithmeticCanonicalizer,
    AssignToAugAssignNormalizer,
    canonicalize_arithmetic,
    normalize_assigns_to_augassigns,
    normalize_code,
)


@pytest.mark.parametrize(
    "transform, source, expected",
    [
        (
            normalize_assigns_to_augassigns,
            "def add(x, y):\n    x = x + y\n    return x\n",
            "def add(x, y):\n    x += y\n    return x\n",
        ),
        (canonicalize_arithmetic, "result = value - 2", "result = value + -2"),
    ],
)
def test_deprecated_wrappers_preserve_output_without_mutating_input(transform, source, expected):
    tree = ast.parse(source)
    original = ast.dump(tree, include_attributes=True)
    with pytest.warns(DeprecationWarning, match="deprecated"):
        result = transform(tree)
    assert result is not tree
    assert ast.dump(tree, include_attributes=True) == original
    assert ast.unparse(result) == ast.unparse(ast.parse(expected))
    # Child nodes are independent too, even where the transform did not rewrite them.
    result.body.clear()
    assert ast.dump(tree, include_attributes=True) == original


@pytest.mark.parametrize("visitor_type", [AssignToAugAssignNormalizer, ArithmeticCanonicalizer])
def test_direct_visitors_warn_and_keep_node_transformer_contract(visitor_type):
    tree = ast.parse("value = 1")
    with pytest.warns(DeprecationWarning, match="deprecated"):
        visitor = visitor_type()
    assert visitor.visit(tree) is tree


def test_normalize_code_keeps_legacy_result_and_warns():
    with pytest.warns(DeprecationWarning, match="deprecated") as warnings:
        result = normalize_code("def add(x, y):\n    x = x + y\n    return x - 2\n")
    assert len(warnings) == 2
    assert ast.dump(ast.parse(result)) == ast.dump(
        ast.parse("def add(x, y):\n    x += y\n    return x + -2\n")
    )


def test_warning_documents_real_list_aliasing_hazard():
    source = "def add(values):\n    values = values + [2]\n    return values\n"
    original = {}
    transformed = {}
    exec(source, original)
    with pytest.warns(DeprecationWarning):
        rewritten = normalize_code(source)
    exec(rewritten, transformed)
    before_argument = [1]
    after_argument = [1]
    assert original["add"](before_argument) == transformed["add"](after_argument) == [1, 2]
    assert before_argument == [1]
    assert after_argument == [1, 2]
