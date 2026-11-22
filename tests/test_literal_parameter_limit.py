# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0.
"""The cheap literal-window bound must agree with the full unifier."""

import ast
import warnings
from unittest.mock import patch

from hypothesis import example, given, settings, strategies as st

from towel.source_files import source_policy_context
from towel.unification.constant_consistency import constant_identity
from towel.unification.typing_forms import ModuleText
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.unifier import Unifier

Literal = str | int | float | bool | None
Row = tuple[Literal, Literal, Literal]
LITERALS = st.sampled_from([0, False, 1, True, 0.0, -0.0, 2, None, "a", "b"])
ROWS = st.tuples(LITERALS, LITERALS, LITERALS)


def calls(rows: list[Row]) -> list[ast.stmt]:
    block = ast.parse("g.edge(0, 0, weight=0)\n" * len(rows)).body
    for statement, row in zip(block, rows, strict=True):
        assert isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)
        call = statement.value
        for argument, value in zip([*call.args, call.keywords[0].value], row, strict=True):
            assert isinstance(argument, ast.Constant)
            argument.value = value
    return block


@settings(deadline=None)
@given(st.lists(st.tuples(ROWS, ROWS), min_size=1, max_size=8), st.integers(0, 7))
@example([((0, 0, 0), (1, 1, 1))] * 4, 1)
@example([((False, 0, -0.0), (True, 1, 0.0))], 2)
def test_generated_windows_match_full_unification(pairs: list[tuple[Row, Row]], limit: int) -> None:
    blocks = [calls([a for a, _ in pairs]), calls([b for _, b in pairs])]
    oracle = Unifier(max_parameters=limit)
    expected = oracle.unify_blocks(blocks, [{}, {}])
    engine = UnificationRefactorEngine(max_parameters=limit)
    with patch.object(engine.unifier, "unify_blocks", wraps=engine.unifier.unify_blocks) as full:
        result = engine._unify_memoized(blocks, [{}, {}])
    assert (result is not None) == (expected is not None)
    differences = {
        (constant_identity(a), constant_identity(b))
        for left, right in pairs
        for a, b in zip(left, right)
        if constant_identity(a) != constant_identity(b)
    }
    if len(differences) > limit:
        assert expected is None
        assert full.call_count == 0


def test_overlapping_windows_do_not_invoke_full_unification() -> None:
    body = calls([(i, i + 1, i + 2) for i in range(12)])
    engine = UnificationRefactorEngine(max_parameters=3)
    with patch.object(engine.unifier, "unify_blocks", wraps=engine.unifier.unify_blocks) as full:
        for start in range(5):
            for length in (3, 4):
                blocks = [body[start : start + length], body[start + 4 : start + 4 + length]]
                assert engine._unify_memoized(blocks, [{}, {}]) is None
    assert full.call_count == 0


def test_only_warning_free_local_typing_lookups_can_be_skipped() -> None:
    for callee, text, lookups in [
        ("edge", "", 2),
        ("cast", "", 4),
        ("edge", r"message = '\q'", 4),
    ]:
        engine = UnificationRefactorEngine(max_parameters=1)
        blocks = [ast.parse(f"g.{callee}({i}, {i + 1})").body for i in (1, 4)]
        source = f"{text}\ng.{callee}(1, 2)\ng.{callee}(4, 5)"
        modules = [ModuleText("source.py", source)] * 2
        context = source_policy_context(strict=False)
        with (
            warnings.catch_warnings(record=True) as diagnostics,
            patch.object(engine, "_typing_forms", wraps=engine._typing_forms) as resolve,
        ):
            warnings.simplefilter("always")
            for _ in range(2):
                assert context.run(engine._unify_memoized, blocks, [{}, {}], modules) is None
        assert bool(diagnostics) == bool(text)
        assert resolve.call_count == lookups
