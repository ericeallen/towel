# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0.
"""Refreshes must repeat contextual judgments and reuse unchanged literal proofs."""

import ast
from pathlib import Path
from unittest.mock import patch

from hypothesis import given, settings, strategies as st

from towel.unification import block_analysis
from towel.unification.literal_windows import exceeds_literal_parameter_limit
from towel.unification.refactor_engine import UnificationRefactorEngine


def literal_block(values: list[int]) -> list[ast.stmt]:
    return ast.parse("\n".join(f"g.edge({value}, {value + 1})" for value in values)).body


def test_shifted_blocks_reuse_only_the_proof_in_the_same_run(tmp_path: Path) -> None:
    target = tmp_path / "edges.py"
    target.write_text("def edges(g):\n    pass\n")
    engine = UnificationRefactorEngine(max_parameters=1)
    with patch.object(
        block_analysis,
        "exceeds_literal_parameter_limit",
        wraps=exceeds_literal_parameter_limit,
    ) as judge:
        for run in (1, 2):
            with engine._staged_output(target, target):
                for shift in (0, 5):
                    pair = [literal_block([i, i + 2]) for i in (1, 8)]
                    for block in pair:
                        for node in block:
                            ast.increment_lineno(node, shift)
                    assert engine._unify_memoized(pair, [{}, {}]) is None
                assert judge.call_count == run


@settings(deadline=None)
@given(
    st.lists(st.tuples(st.integers(0, 10), st.integers(0, 10)), min_size=1, max_size=12),
    st.integers(0, 5),
    st.integers(0, 30),
)
def test_reused_literal_refusal_agrees_with_fresh_unification_after_edits(
    rows: list[tuple[int, int]], limit: int, edit: int
) -> None:
    from tempfile import TemporaryDirectory

    engine = UnificationRefactorEngine(max_parameters=limit)
    left = [a for a, _ in rows]
    right = [b for _, b in rows]
    with TemporaryDirectory() as directory:
        target = Path(directory) / "g.py"
        target.write_text("pass\n")
        with engine._staged_output(target, target):
            for values in (right, right, [edit, *right[1:]], left):
                blocks = [literal_block(left), literal_block(values)]
                expected = UnificationRefactorEngine(max_parameters=limit)._unify_memoized(
                    blocks, [{}, {}]
                )
                assert (engine._unify_memoized(blocks, [{}, {}]) is None) == (expected is None)


@settings(deadline=None, max_examples=20)
@given(
    st.lists(st.sampled_from(["", "    print(locals())\n", "    del g\n"]), min_size=1, max_size=5),
    st.booleans(),
)
def test_current_context_decides_before_a_reused_refusal(edits: list[str], strict: bool) -> None:
    from tempfile import TemporaryDirectory

    def source(before: str) -> str:
        return (
            "def f(g):\n" + before + "    g.edge(1, 2)\n    g.edge(2, 3)\n    g.edge(3, 4)\n"
            "def h(g):\n    g.edge(5, 6)\n    g.edge(6, 7)\n    g.edge(7, 8)\n"
        )

    with TemporaryDirectory() as directory:
        target = Path(directory) / "sample.py"
        target.write_text(source(""))
        warm = UnificationRefactorEngine(max_parameters=1, strict=strict)
        with warm._staged_output(target, target) as stage:
            path = str(stage.target)
            for change in ["", *edits, ""]:
                stage.target.write_text(source(change))
                fresh = UnificationRefactorEngine(max_parameters=1, strict=strict)
                assert warm.analyze_file(path) == fresh.analyze_file(path) == []
                assert warm.declined_pairs == fresh.declined_pairs


def test_a_shape_without_a_cache_key_still_receives_the_ordinary_judgment(tmp_path: Path) -> None:
    source = tmp_path / "program.py"
    source.write_text("pass\n")
    engine = UnificationRefactorEngine()
    statements = literal_block([1, 2])
    with (
        engine._staged_output(source, source),
        patch.object(engine, "_sid", side_effect=RecursionError),
    ):
        assert engine._are_structurally_similar(statements, literal_block([3, 4]))
        assert not engine._are_structurally_similar(statements, literal_block([3]))
