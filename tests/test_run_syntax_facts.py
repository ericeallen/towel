# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0.
"""Repeated statements reuse syntax checks, even when a refresh moves their lines."""

from __future__ import annotations

import ast
from collections.abc import Iterator

import pytest

from towel.source_files import source_policy_context
from towel.unification import compiler_literals, unsupported_syntax
from towel.unification.unifier import Unifier


def test_unsupported_walk_once_per_structure_per_run(monkeypatch: pytest.MonkeyPatch) -> None:
    walk = ast.walk
    calls = 0

    def counted(node: ast.AST) -> Iterator[ast.AST]:
        nonlocal calls
        calls += 1
        return walk(node)

    monkeypatch.setattr(ast, "walk", counted)
    for run in (1, 2):
        context = source_policy_context(strict=False)
        for prefix in ("", "\n"):
            block = ast.parse(prefix + "use(x)\nuse(x)").body
            assert context.run(unsupported_syntax.unsupported_syntax, block) is None
        assert calls == run


def test_literal_walk_once_per_structure_and_current_occurrences(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    walk = compiler_literals._runtime_walk
    calls = 0

    def counted(
        block: list[ast.AST], protected_roots: frozenset[ast.AST] = frozenset()
    ) -> Iterator[tuple[ast.AST, bool]]:
        nonlocal calls
        calls += 1
        return walk(block, protected_roots)

    monkeypatch.setattr(compiler_literals, "_runtime_walk", counted)
    first_cost = 0
    for run in (1, 2):
        context = source_policy_context(strict=False)
        for prefix in ("", "\n"):
            block = ast.parse(prefix + "values = (1000, 2000)").body
            unifier = Unifier()
            context.run(unifier._reset_unification_state, [block, block])
            expected = frozenset(
                node for node in ast.walk(block[0]) if isinstance(node, ast.Constant)
            )
            assert unifier._pooled_literal_nodes == (expected, expected)
            if not first_cost:
                first_cost = calls
                assert first_cost > 0
            assert calls == run * first_cost
