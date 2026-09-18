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


def _reference_similarity(
    block1: Sequence[ast.AST], block2: Sequence[ast.AST], threshold: float = 0.6
) -> bool:
    """The structural-similarity check as it walked both blocks per call."""
    from collections import Counter

    if len(block1) != len(block2):
        return False
    total_nodes = 0
    matching_nodes = 0
    for stmt1, stmt2 in zip(block1, block2):
        nodes1 = list(ast.walk(stmt1))
        nodes2 = list(ast.walk(stmt2))
        if abs(len(nodes1) - len(nodes2)) / max(len(nodes1), len(nodes2)) > 0.3:
            return False
        counter1 = Counter(type(n).__name__ for n in nodes1)
        counter2 = Counter(type(n).__name__ for n in nodes2)
        total_nodes += max(len(nodes1), len(nodes2))
        matching_nodes += sum((counter1 & counter2).values())
    if total_nodes == 0:
        return False
    return matching_nodes / total_nodes >= threshold


def test_memoized_similarity_matches_the_per_call_walk() -> None:
    from towel.unification.refactor_engine import UnificationRefactorEngine

    engine = UnificationRefactorEngine()
    blocks: List[List[ast.stmt]] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py"))[:12]:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for statements in _statement_lists(tree):
            blocks.extend(_contiguous_blocks(statements, longest=3))
    assert len(blocks) > 100
    verdicts = {True: 0, False: 0}
    for index, block1 in enumerate(blocks[:400]):
        for block2 in blocks[index + 1 : index + 40]:
            expected = _reference_similarity(block1, block2)
            assert engine._are_structurally_similar(block1, block2) == expected
            verdicts[expected] += 1
    assert verdicts[True] and verdicts[False]


def _reference_requires_original_frame(block: Sequence[ast.AST]) -> bool:
    """The frame-sensitivity guard as one walk of the whole block computed it."""
    from towel.unification.semantic_safety import (
        _has_comprehension_assignment,
        _is_frame_relative_call,
        has_external_loop_control,
        is_namespace_access_call,
    )

    if has_external_loop_control(block) or _has_comprehension_assignment(block):
        return True
    for statement in block:
        for node in ast.walk(statement):
            if isinstance(node, (ast.Yield, ast.YieldFrom, ast.Await, ast.AsyncFor, ast.AsyncWith)):
                return True
            if isinstance(node, ast.Call):
                if is_namespace_access_call(node):
                    return True
                if (
                    isinstance(node.func, ast.Name)
                    and node.func.id == "super"
                    and not node.args
                    and not node.keywords
                ):
                    return True
                if _is_frame_relative_call(node):
                    return True
    return False


def _reference_bound_names(block: Sequence[ast.AST]) -> set[str]:
    from towel.unification.scope_analyzer import pattern_capture_names

    names: set[str] = set()
    for statement in block:
        for node in ast.walk(statement):
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                names.add(node.id)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                names.add(node.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.match_case):
                names.update(pattern_capture_names(node.pattern))
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    if alias.name != "*":
                        names.add(alias.asname or alias.name.split(".")[0])
    return names


def _reference_deleted_names(block: Sequence[ast.AST]) -> set[str]:
    names: set[str] = set()
    for statement in block:
        for node in ast.walk(statement):
            if isinstance(node, ast.Delete):
                for target in node.targets:
                    names.update(
                        child.id
                        for child in ast.walk(target)
                        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Del)
                    )
            elif isinstance(node, ast.ExceptHandler) and node.name:
                names.add(node.name)
    return names


HOSTILE = Path(__file__).resolve().parent / "hostile_cases"


@pytest.mark.parametrize(
    "path",
    sorted(SOURCE_ROOT.rglob("*.py")) + sorted(HOSTILE.glob("*.py")),
    ids=lambda p: p.name,
)
def test_memoized_guards_match_the_whole_block_walk(path: Path) -> None:
    from towel.unification.semantic_safety import (
        _deleted_names,
        bound_names,
        requires_original_frame,
    )

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for statements in _statement_lists(tree):
        for block in _contiguous_blocks(statements, longest=4):
            assert requires_original_frame(block) == _reference_requires_original_frame(block)
            assert bound_names(block) == _reference_bound_names(block)
            assert _deleted_names(block) == _reference_deleted_names(block)


def test_structural_id_stays_injective_on_structure() -> None:
    from towel.unification.structural_memo import structural_id

    by_id: dict[str, tuple[str, ...]] = {}
    for path in sorted(SOURCE_ROOT.rglob("*.py"))[:15]:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for statements in _statement_lists(tree):
            for block in _contiguous_blocks(statements, longest=3):
                structure = tuple(ast.dump(s, include_attributes=False) for s in block)
                assert by_id.setdefault(structural_id(block), structure) == structure
    assert len(by_id) > 1000
