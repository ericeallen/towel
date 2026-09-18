"""The per-block memo answers exactly what the per-query walk answers."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterator, List, Tuple

from towel.unification.binding_context import (
    bound_variables_in_block,
    get_bound_variables_in_context,
)

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "src" / "towel"


def _blocks_with_expressions(tree: ast.AST) -> Iterator[Tuple[List[ast.stmt], ast.expr]]:
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = node.body
        for length in (1, 2, 3):
            for start in range(0, max(0, len(body) - length + 1), 2):
                block = body[start : start + length]
                for statement in block:
                    for child in ast.walk(statement):
                        if isinstance(child, (ast.Name, ast.Call, ast.Attribute, ast.BinOp)):
                            yield block, child


def test_memoized_query_matches_the_walk_on_towels_source() -> None:
    compared = 0
    for path in sorted(SOURCE_ROOT.rglob("*.py"))[:20]:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for block, expression in _blocks_with_expressions(tree):
            wrapped = ast.Module(body=list(block), type_ignores=[])
            expected = get_bound_variables_in_context(wrapped, expression)
            assert bound_variables_in_block(block, expression) == expected
            # The second answer comes from the memo and must be the same object's worth.
            assert bound_variables_in_block(block, expression) == expected
            compared += 1
    assert compared > 1000


def test_memo_distinguishes_blocks_of_different_length() -> None:
    block = ast.parse("print(x)\nx = 1\n").body
    statement = block[0]
    assert isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)
    target = statement.value.args[0]
    assert isinstance(target, ast.Name)
    assert bound_variables_in_block(block[:1], target) == set()
    # The second statement assigns ``x``, so the longer block binds it.
    assert bound_variables_in_block(block, target) == {"x"}


def test_empty_block_binds_nothing() -> None:
    target = ast.parse("x", mode="eval").body
    assert bound_variables_in_block([], target) == set()
