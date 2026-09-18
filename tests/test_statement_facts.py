"""The per-statement facts fold to exactly what a walk of the whole block computed."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterator, List, Sequence

import pytest

from towel.unification.block_signature import BlockSignature, extract_block_signature
from towel.unification.extractor import contains_return
from towel.unification.visitors import OwnScopeVisitor

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "src" / "towel"


def _reference_signature(block: Sequence[ast.AST]) -> BlockSignature:
    """The signature as one walk of the whole block computed it, before the memo."""
    stmt_seq = tuple(type(s).__name__ for s in block)
    has_with = False
    has_try = False
    name_load_count = 0
    name_store_count = 0
    call_count = 0
    skip_types = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
    for stmt in block:
        stack = [stmt]
        while stack:
            node = stack.pop()
            if isinstance(node, skip_types):
                continue
            if not has_with and isinstance(node, ast.With):
                has_with = True
            if not has_try and isinstance(node, ast.Try):
                has_try = True
            if isinstance(node, ast.Name):
                if isinstance(node.ctx, ast.Load):
                    name_load_count += 1
                elif isinstance(node.ctx, ast.Store):
                    name_store_count += 1
            elif isinstance(node, ast.Call):
                call_count += 1
            stack.extend(ast.iter_child_nodes(node))
    return BlockSignature(
        stmt_count=len(block),
        stmt_seq=stmt_seq,
        has_with=has_with,
        has_try=has_try,
        name_load_count=name_load_count,
        name_store_count=name_store_count,
        call_count=call_count,
    )


class _ReferenceReturnFinder(OwnScopeVisitor):
    """The return finder as it was: every scope but nested functions."""

    def __init__(self) -> None:
        self.found_return = False

    def visit_Return(self, node: ast.Return) -> None:
        self.found_return = True


def _reference_contains_return(block: Sequence[ast.stmt]) -> bool:
    finder = _ReferenceReturnFinder()
    for stmt in block:
        finder.visit(stmt)
        if finder.found_return:
            return True
    return False


def _statement_lists(tree: ast.AST) -> Iterator[List[ast.stmt]]:
    """Every statement list in ``tree``: bodies, else-suites, handlers, finally-suites."""
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            value = getattr(node, field, None)
            if isinstance(value, list) and value and isinstance(value[0], ast.stmt):
                yield value


def _contiguous_blocks(statements: List[ast.stmt], longest: int) -> Iterator[List[ast.stmt]]:
    for length in range(1, min(longest, len(statements)) + 1):
        for start in range(len(statements) - length + 1):
            yield statements[start : start + length]


@pytest.mark.parametrize("path", sorted(SOURCE_ROOT.rglob("*.py")), ids=lambda p: p.name)
def test_folded_facts_match_the_whole_block_walk(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    compared = 0
    for statements in _statement_lists(tree):
        for block in _contiguous_blocks(statements, longest=6):
            assert extract_block_signature(block) == _reference_signature(block)
            assert contains_return(block) == _reference_contains_return(block)
            compared += 1
    assert compared > 0


def test_return_inside_nested_function_is_not_the_blocks() -> None:
    block = ast.parse("def inner():\n    return 1\nx = inner()\n").body
    assert not contains_return(block)
    assert contains_return(ast.parse("if x:\n    return 1\n").body)
    assert contains_return(ast.parse("class C:\n    return 1\n").body)


def test_signature_leaves_nested_scopes_out_but_counts_around_them() -> None:
    block = ast.parse("f = lambda a: g(a)\nwith open(p) as h:\n    y = h.read()\n").body
    signature = extract_block_signature(block)
    assert signature == _reference_signature(block)
    assert signature.has_with and not signature.has_try
    assert signature.call_count == 2
    assert signature.name_store_count == 3
