# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0.
"""Interval facts retain exact signatures while avoiding repeated statement-level scans."""

from __future__ import annotations

import ast
from collections import Counter
from unittest.mock import patch

import pytest

from towel.unification import block_analysis, block_signature
from towel.unification.block_signature import BlockSignatureIndex, extract_block_signature
from towel.unification.models import FunctionNode
from towel.unification.narrowing import (
    caller_narrowing_leaves_with_block,
    may_establish_caller_narrowing,
)
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.statement_facts import statement_facts

SOURCES = [
    "def f(data):\n    a = data + 1\n    b = a * 2\n    return b\n",
    "def f(data):\n    with data as item:\n        a = item.read()\n        return a\n",
    "def f(data):\n    try:\n        a = data()\n    except ValueError:\n        a = 1\n    finally:\n        print(data)\n    return a\n",
    "def f(obj):\n    if obj.value is None:\n        obj.value = obj.compute()\n    assert obj.value is not None\n    return obj.value + 1\n",
    "def f(data):\n    callback = lambda value: value + data\n    def nested():\n        return data()\n    return callback(3)\n",
    "def f(data):\n    match data:\n        case [item]:\n            result = item * 2\n            return result\n        case _:\n            return 0\n",
]


def _function(source: str) -> FunctionNode:
    node = ast.parse(source).body[0]
    assert isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    return node


@pytest.mark.parametrize("source", SOURCES)
def test_indexed_interval_signatures_equal_the_independent_statement_fold(source: str) -> None:
    function = _function(source)
    index = BlockSignatureIndex(function)
    for node in ast.walk(function):
        for _, value in ast.iter_fields(node):
            if not isinstance(value, list) or not value or not isinstance(value[0], ast.stmt):
                continue
            body = [stmt for stmt in value if isinstance(stmt, ast.stmt)]
            for start in range(len(body)):
                for stop in range(start + 1, len(body) + 1):
                    block = body[start:stop]
                    assert extract_block_signature(block, index=index) == extract_block_signature(
                        block
                    )
    assert extract_block_signature([], index=index) == extract_block_signature([])
    external = ast.parse("external = source()").body
    assert extract_block_signature(external, index=index) == extract_block_signature(external)


@pytest.mark.parametrize("size", [16, 32])
def test_overlapping_intervals_read_each_statement_fact_once(size: int) -> None:
    function = _function(
        "def f(data):\n" + "".join(f"    value{i} = data + {i}\n" for i in range(size))
    )
    engine = UnificationRefactorEngine(min_lines=1)
    with (
        patch.object(
            block_analysis, "holds_unspellable_int", wraps=block_analysis.holds_unspellable_int
        ) as spellable,
        patch.object(block_signature, "statement_facts", wraps=statement_facts) as facts,
    ):
        blocks = engine._signed_blocks(function)
    assert len(blocks) == size * (size + 1) // 2
    expected = Counter(id(statement) for statement in function.body)
    assert Counter(id(call.args[0]) for call in spellable.call_args_list) == expected
    assert Counter(id(call.args[0]) for call in facts.call_args_list) == expected
    assert all(signature == extract_block_signature(nodes) for _, nodes, signature in blocks)


@pytest.mark.parametrize("source", SOURCES)
def test_narrowing_prefix_only_bypasses_an_already_false_guard(source: str) -> None:
    function = _function(source)
    for start in range(len(function.body)):
        for stop in range(start + 1, len(function.body) + 1):
            block = function.body[start:stop]
            if not any(may_establish_caller_narrowing(statement) for statement in block):
                assert not caller_narrowing_leaves_with_block(function, block)
