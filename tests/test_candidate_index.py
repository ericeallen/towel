"""The candidate index must preserve the ordered exhaustive search result."""

import ast
from dataclasses import replace
from itertools import combinations, product
from pathlib import Path
from unittest.mock import patch

import pytest

from towel.unification.block_signature import (
    extract_block_signature,
    quick_filter,
    signature_bucket_key,
)
from towel.unification.models import CodeBlockPair
from towel.unification.pipeline import analyze_scopes, collect_functions, parse_modules
from towel.unification.refactor_engine import UnificationRefactorEngine


def analyzed_functions(paths):
    modules = parse_modules(paths)
    analyze_scopes(modules)
    return [
        (
            function.file_path,
            function.node,
            function.source,
            function.scope_analyzer,
            function.root_scope,
            function.class_name,
            function.enclosing_function,
            function.ancestry,
        )
        for function in collect_functions(modules)
    ]


def exhaustive_pairs(engine, functions):
    """The pre-index algorithm, deliberately comparing the full Cartesian product."""
    result = []
    for first, second in combinations(functions, 2):
        for (range1, nodes1), (range2, nodes2) in product(
            engine._extract_code_blocks(first[1]), engine._extract_code_blocks(second[1])
        ):
            if len(nodes1) != len(nodes2):
                continue
            if min(range1[1] - range1[0] + 1, range2[1] - range2[0] + 1) < engine.min_lines:
                continue
            if not quick_filter(extract_block_signature(nodes1), extract_block_signature(nodes2)):
                continue
            result.append(
                CodeBlockPair(
                    file_path=first[0],
                    function1_name=first[1].name,
                    function2_name=second[1].name,
                    block1_range=range1,
                    block2_range=range2,
                    block1_nodes=nodes1,
                    block2_nodes=nodes2,
                    file_path2=second[0],
                    class1_name=first[5],
                    class2_name=second[5],
                    enclosing_function1_name=first[6],
                    enclosing_function2_name=second[6],
                    function1_ancestry=first[7],
                    function2_ancestry=second[7],
                    scope_analyzer1=first[3],
                    scope_analyzer2=second[3],
                    root_scope1=first[4],
                    root_scope2=second[4],
                    source1=first[2],
                    source2=second[2],
                    function1_node=first[1],
                    function2_node=second[1],
                )
            )
    return result


@pytest.mark.parametrize("min_lines", [1, 2, 5])
def test_index_preserves_every_ordered_pair_across_scopes(tmp_path, min_lines):
    source = """
def plain(x):
    a = x + 1
    b = a * 2
    return b

def control(x):
    for item in x:
        if item:
            value = item + 1
            print(value)
    else:
        print(x)
    return x

def exceptional(x):
    try:
        value = x + 1
        print(value)
    except ValueError:
        value = x - 1
        print(value)
    finally:
        print(x)
    return value

class Container:
    def method(self, x):
        with x as value:
            result = value + 1
            print(result)
        return value

    def factory(self, x):
        def nested(y):
            value = y + 1
            result = value * 2
            return result
        return nested(x)
"""
    paths = []
    for index in range(2):
        path = tmp_path / f"module{index}.py"
        path.write_text(source)
        paths.append(str(path))
    functions = analyzed_functions(paths)
    engine = UnificationRefactorEngine(min_lines=min_lines)
    expected = exhaustive_pairs(engine, functions)
    actual = engine._find_block_pairs_multi_file(functions, progress="none")
    assert expected
    assert actual == expected


def test_index_matches_all_original_example_files():
    examples = Path(__file__).parent.parent / "test_examples"
    paths = sorted(examples.glob("*.py"))
    assert len(paths) >= 26
    for path in paths:
        functions = analyzed_functions([str(path)])
        engine = UnificationRefactorEngine()
        assert engine._find_block_pairs_multi_file(functions) == exhaustive_pairs(engine, functions)


def test_index_extracts_blocks_and_signatures_once_per_function(tmp_path):
    path = tmp_path / "duplicates.py"
    path.write_text(
        "\n".join(
            f"def func{index}(x):\n    a = x + 1\n    b = a * 2\n    return b\n"
            for index in range(8)
        )
    )
    functions = analyzed_functions([str(path)])
    engine = UnificationRefactorEngine(min_lines=1)
    expected_blocks = sum(len(engine._extract_code_blocks(function[1])) for function in functions)
    with (
        patch.object(engine, "_extract_code_blocks", wraps=engine._extract_code_blocks) as extract,
        patch(
            "towel.unification.refactor_engine.extract_block_signature",
            wraps=extract_block_signature,
        ) as signature,
    ):
        pairs = engine._find_block_pairs_multi_file(functions)
    assert pairs
    assert extract.call_count == len(functions)
    assert signature.call_count == expected_blocks


def test_bucket_gates_never_exclude_quick_filter_matches():
    template = extract_block_signature(ast.parse("result = value").body)
    signatures = [
        replace(
            template,
            stmt_count=count,
            stmt_seq=sequence,
            has_with=has_with,
            has_try=has_try,
            name_load_count=loads,
        )
        for count, sequence, has_with, has_try, loads in product(
            (0, 1, 2), ((), ("Assign",), ("Return",)), (False, True), (False, True), (0, 2, 3)
        )
    ]
    for first, second in product(signatures, repeat=2):
        if quick_filter(first, second):
            assert signature_bucket_key(first) == signature_bucket_key(second)
