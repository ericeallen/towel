# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0.
"""Exact literal guard sets, compiler-site carrier paths, and weak pure memo lifetimes."""

from __future__ import annotations

import ast
from collections import Counter
import copy
import gc
import sys
import weakref

import pytest

from towel.canonical_ast import canonical_dump, exact_dump
from towel.unification import compiler_literals as literals
from towel.unification.bounded_cache import memoization_disabled
from towel.unification.structural_memo import load_substitution, path_map, store_substitution
from towel.unification.unifier import Unifier


def _expression(source: str) -> ast.expr:
    return ast.parse(source, mode="eval").body


def test_protected_descendant_sets_cover_nested_roots_and_membership_contexts() -> None:
    block = ast.parse(
        "literal = (1+2,(3,(4,)))\ndynamic=(value,5)\nmatched = value in {6+7,8}\n"
        "local: Literal[9,10] = value"
    ).body
    expected = frozenset(
        canonical_dump(_expression(source))
        for source in (
            "1",
            "2",
            "1+2",
            "3",
            "4",
            "(4,)",
            "(3,(4,))",
            "{6+7,8}",
            "6+7",
            "6",
            "7",
            "8",
        )
    )
    assert literals.pooled_literal_parts(block) == expected
    with memoization_disabled():
        assert literals.pooled_literal_parts(copy.deepcopy(block)) == expected


def test_pure_classification_reuses_exact_nodes_without_mutating_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    block = ast.parse("literal = " + "1000000" + "+1" * 64).body
    before = exact_dump(ast.Module(body=block, type_ignores=[]))
    calls: Counter[ast.AST] = Counter()
    original = literals._compute_literal_input

    def counted(node: ast.AST) -> bool:
        calls[node] += 1
        return original(node)

    monkeypatch.setattr(literals, "_compute_literal_input", counted)
    expected = frozenset(
        {canonical_dump(_expression("1000000")), canonical_dump(_expression("1"))}
        | {canonical_dump(_expression("1000000" + "+1" * depth)) for depth in range(1, 64)}
    )
    assert literals.pooled_literal_parts(block) == expected
    first_calls = sum(calls.values())
    assert first_calls > 0 and max(calls.values()) == 1
    assert literals.pooled_literal_parts(block) == expected
    assert sum(calls.values()) == first_calls
    with memoization_disabled():
        assert literals.pooled_literal_parts(block) == expected
    assert sum(calls.values()) > first_calls
    assert exact_dump(ast.Module(body=block, type_ignores=[])) == before


def test_classification_memo_does_not_retain_source_nodes() -> None:
    gc.collect()
    baseline = len(literals._LITERAL_INPUT)

    def classify() -> weakref.ReferenceType[ast.AST]:
        root = _expression("(1000000+1,(1000002,))")
        reference: weakref.ReferenceType[ast.AST] = weakref.ref(root)
        assert literals._literal_input(root)
        assert len(literals._LITERAL_INPUT) > baseline
        return reference

    reference = classify()
    gc.collect()
    assert reference() is None
    assert len(literals._LITERAL_INPUT) <= baseline


def test_whole_root_requires_global_occurrence_correspondence_and_constant_opt_in() -> None:
    blocks = [
        ast.parse(source).body
        for source in (
            "literal=(1000000,1000001)\nother=(1000000,1000001)",
            "literal=(1000002,1000003)\nother=(1000000,1000001)",
        )
    ]
    assert Unifier().unify_blocks(blocks, [{}, {}]) is None
    simple = [ast.parse("literal=" + source).body for source in ("(1,2)", "(3,4)")]
    assert Unifier(parameterize_constants=False).unify_blocks(simple, [{}, {}]) is None
    result = Unifier().unify_blocks(simple, [{}, {}])
    assert result is not None and len(result.param_expressions) == 1
    assert all(
        isinstance(node, ast.Tuple) for _, node in next(iter(result.param_expressions.values()))
    )


def test_carrier_store_load_rebinds_every_original_element_path() -> None:
    blocks = [
        ast.parse(source).body
        for source in (
            "number=1000000\nfraction=1000000.25\ndata=b'byte label'\nreturn number,fraction,data,value+5",
            "number=1000000\nfraction=1000000.25\ndata=b'byte label'\nreturn number,fraction,data,value+7",
        )
    ]
    renames: list[dict[str, str]] = [{}, {}]
    result = Unifier().unify_blocks(blocks, renames, thread_literal_pools=True)
    assert result is not None and result.literal_pool is not None
    stored = store_substitution(result, blocks, renames)
    assert stored.literal_pool is not None
    shifted = [
        ast.parse("\n\n" + ast.unparse(ast.Module(body=block, type_ignores=[]))).body
        for block in blocks
    ]
    loaded, _ = load_substitution(stored, shifted)
    assert loaded.literal_pool is not None and loaded.literal_pool.keys == result.literal_pool.keys
    for index, row in enumerate(loaded.literal_pool.rows):
        paths = path_map(shifted[index])
        assert all(node in paths for node in row)
        assert all(node is not old for node, old in zip(row, result.literal_pool.rows[index]))
    assert stored == store_substitution(loaded, shifted, renames)
    loaded.remove_parameter(loaded.literal_pool.parameter)
    assert loaded.literal_pool is None


def test_carrier_pins_all_same_key_occurrences_and_avoids_closed_computations() -> None:
    blocks = [
        ast.parse(source).body
        for source in (
            "label='noninterned label'\nmatch value:\n case 'noninterned label': return label\nreturn label+str(5)",
            "label='noninterned label'\nmatch value:\n case 'noninterned label': return label\nreturn label+str(7)",
        )
    ]
    result = Unifier().unify_blocks(blocks, [{}, {}], thread_literal_pools=True)
    assert result is not None and result.literal_pool is None
    computed = [
        ast.parse(source).body
        for source in (
            "literal=1//0\nreturn value+5,literal",
            "literal=1//0\nreturn value+7,literal",
        )
    ]
    result = Unifier().unify_blocks(computed, [{}, {}], thread_literal_pools=True)
    assert result is not None and result.literal_pool is None


def test_unknown_compiler_global_fallback_keeps_only_singletons_and_trusted_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "version_info", (3, 99))
    assert literals.is_global_literal(None) and literals.is_global_literal(False)
    assert not literals.is_global_literal(1) and not literals.is_global_literal("identifier")
    body = ast.parse("return carrier[0]").body
    assert literals.retained_literal_pool(body)
    assert not literals.retained_literal_pool(body, trusted_projection=("carrier", 1))
    assert literals.retained_literal_pool(body, trusted_projection=("different", 1))


@pytest.mark.parametrize(
    "expression",
    [
        "1e309-1e309",
        "1e308*1e308-1e308*1e308",
        "1e309j-1e309j",
        "1/2",
        "2**-1",
        "2**(1-2)",
        "(1,2)[0]+1",
    ],
)
def test_unproved_float_transitions_and_closed_subscript_arithmetic_remain_pinned(
    expression: str,
) -> None:
    assert literals.unstable_literal_pool(ast.parse("return " + expression).body)


@pytest.mark.parametrize(
    "expression",
    ["1e309", "1e309j", "-0.0", "(1e309,-0.0)", "2**100", "2//1", "'a'+'b'", "float('nan')"],
)
def test_simple_literals_integer_results_and_dynamic_nan_calls_remain_available(
    expression: str,
) -> None:
    assert not literals.unstable_literal_pool(ast.parse("return " + expression).body)
