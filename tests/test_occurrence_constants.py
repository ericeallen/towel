# Copyright 2025-2026 Eric Allen
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Occurrence substitution preserves exact columns through copying and cache hydration."""

from __future__ import annotations

import ast
import copy
import gc
import weakref
from types import FunctionType

import pytest

from towel.unification.ast_paths import path_map
from towel.unification.extractor import HygienicExtractor
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.structural_memo import load_substitution, store_substitution
from towel.unification.substitution import Substitution
from towel.unification.unifier import Unifier


def _unify(*sources: str, pools: bool = False) -> tuple[list[list[ast.stmt]], Substitution]:
    blocks = [ast.parse(source).body for source in sources]
    substitution = Unifier().unify_blocks(blocks, [{} for _ in blocks], thread_literal_pools=pools)
    assert substitution is not None
    return blocks, substitution


def _extract(block: list[ast.stmt], substitution: Substitution) -> ast.FunctionDef:
    helper, _ = HygienicExtractor().extract_function(
        template_block=block,
        substitution=substitution,
        free_variables={"x", "y"},
        enclosing_names=set(),
        is_value_producing=True,
    )
    return helper


def test_differing_offset_does_not_replace_equal_multiplier() -> None:
    blocks, substitution = _unify("return x + 1 + x * 2", "return x + 2 + x * 2")
    before = [ast.dump(ast.Module(body=block, type_ignores=[])) for block in blocks]
    helper = _extract(blocks[0], substitution)
    result = helper.body[0]
    assert isinstance(result, ast.Return) and isinstance(result.value, ast.BinOp)
    multiplier = result.value.right
    assert isinstance(multiplier, ast.BinOp)
    assert isinstance(multiplier.right, ast.Constant)
    assert type(multiplier.right.value) is int and multiplier.right.value == 2
    assert len(substitution.param_expressions) == 1
    assert len(substitution.occurrence_mappings) == 2
    assert before == [ast.dump(ast.Module(body=block, type_ignores=[])) for block in blocks]
    assert len(helper.args.posonlyargs + helper.args.args) == 2


@pytest.mark.parametrize(
    "sources, expected",
    [
        (("return x+2+x*2", "return x+3+x*3"), 1),
        (("return x+2+x*2", "return x+3+x*4"), 2),
        (("return x+1+x*2", "return x+2+x*1"), 2),
        (("return x+2+x*2", "return x+3+x*3", "return x+4+x*4"), 1),
        (("return x+2+x*2", "return x+3+x*3", "return x+4+x*5"), 2),
        (("return x+False+x*0", "return x+0+x*False"), 2),
        (("return x+0+x*0", "return x+False+x*0.0"), 2),
    ],
)
def test_only_complete_exact_typed_columns_share_parameters(sources, expected) -> None:
    blocks, substitution = _unify(*sources)
    assert len(substitution.param_expressions) == expected
    assert len(substitution.occurrence_mappings) == len(blocks) * 2
    assert all(
        len(expressions) == len(blocks) for expressions in substitution.param_expressions.values()
    )


@pytest.mark.parametrize("pools", [False, True])
def test_paths_survive_deepcopy_and_reparse_without_rebinding_source(pools: bool) -> None:
    sources = ("return x+1+x*1000000", "return x+1000000+x*1000000")
    blocks, original = _unify(*sources, pools=pools)
    source_constants = [node for node in ast.walk(blocks[0][0]) if isinstance(node, ast.Constant)]
    original_lookups = [original.get_param_for_expr(0, node) for node in source_constants]
    stored = store_substitution(original, blocks, [{}, {}])
    expected_helper = ast.dump(_extract(blocks[0], original))
    assert [original.get_param_for_expr(0, node) for node in source_constants] == original_lookups
    for fresh in (copy.deepcopy(blocks), [ast.parse("\n\n" + source).body for source in sources]):
        loaded, renames = load_substitution(stored, fresh)
        assert loaded.occurrence_mappings == original.occurrence_mappings
        assert ast.dump(_extract(fresh[0], loaded)) == expected_helper
        assert store_substitution(loaded, fresh, renames) == stored
        assert all(
            node in path_map(fresh[index])
            for expressions in loaded.param_expressions.values()
            for index, node in expressions
        )
        loaded.remove_parameter(next(iter(loaded.param_expressions)))
        assert original.occurrence_mappings
        assert store_substitution(original, blocks, [{}, {}]) == stored


def test_common_carrier_selects_only_equal_occurrence_of_differing_value() -> None:
    blocks, substitution = _unify("return x+1+x*1000000", "return x+1000000+x*1000000", pools=True)
    pool = substitution.literal_pool
    assert pool is not None and len(pool.keys) == 1
    assert len(substitution.param_expressions) == 2
    assert all(len(row) == 1 for row in pool.rows)
    assert all(len(selectors) == 1 for selectors in pool.selectors)
    for index, block in enumerate(blocks):
        constants = [node for node in ast.walk(block[0]) if isinstance(node, ast.Constant)]
        mapped = [substitution.get_param_for_expr(index, node) for node in constants]
        assert mapped.count(pool.parameter) == 1
        assert len(set(mapped)) == 2


def test_constant_outside_pooled_root_is_not_pinned_by_same_value_inside_it() -> None:
    blocks, substitution = _unify("return x+1+(2,)", "return x+2+(2,)")
    assert len(substitution.param_expressions) == 1
    helper = _extract(blocks[0], substitution)
    tuples = [node for node in ast.walk(helper) if isinstance(node, ast.Tuple)]
    assert len(tuples) == 1
    assert all(isinstance(node, ast.Constant) and node.value == 2 for node in tuples[0].elts)


@pytest.mark.parametrize("source", ["return x+1+x*y", "return x+y+x*1"])
def test_mixed_constant_name_families_have_no_global_constant_mapping(source: str) -> None:
    _, substitution = _unify(source, "return x+2+x*2")
    assert not substitution.mappings
    assert len(substitution.occurrence_mappings) == 4


def test_parameter_budget_counts_columns_and_carrier() -> None:
    five = "return x" + "".join(f"+{value}" for value in range(1, 6))
    other_five = "return x" + "".join(f"+{value}" for value in range(6, 11))
    blocks, substitution = _unify(five, other_five)
    assert len(substitution.param_expressions) == 5
    six = [ast.parse(five + "+11").body, ast.parse(other_five + "+12").body]
    assert Unifier().unify_blocks(six, [{}, {}]) is None
    repeated = [ast.parse("return x" + "+1" * 8).body, ast.parse("return x" + "+2" * 8).body]
    shared = Unifier().unify_blocks(repeated, [{}, {}])
    assert shared is not None and len(shared.param_expressions) == 1
    pools = [ast.parse(source + "+1000000").body for source in (five, other_five)]
    saturated = Unifier().unify_blocks(pools, [{}, {}], thread_literal_pools=True)
    assert saturated is not None and len(saturated.param_expressions) == 5
    assert saturated.literal_pool is None
    assert Unifier(max_parameters=4).unify_blocks(blocks, [{}, {}]) is None


def test_shared_expression_nodes_decline_on_cold_and_warm_cache() -> None:
    blocks, _ = _unify("return x+1+x*1", "return x+2+x*2")
    engine = UnificationRefactorEngine()
    assert engine._unify_memoized(blocks, [{}, {}]) is not None
    expression = blocks[0][0]
    assert isinstance(expression, ast.Return) and isinstance(expression.value, ast.BinOp)
    assert isinstance(expression.value.left, ast.BinOp) and isinstance(
        expression.value.right, ast.BinOp
    )
    expression.value.right.right = expression.value.left.right
    assert Unifier().unify_blocks(blocks, [{}, {}]) is None
    assert engine._unify_memoized(blocks, [{}, {}]) is None
    assert UnificationRefactorEngine()._unify_memoized(blocks, [{}, {}]) is None


def test_higher_order_promotion_keeps_other_equal_literal_occurrences_fixed() -> None:
    sources = ["fn = factory(1000000)\nother = x + 1000000\nreturn fn(other)"] * 2
    blocks = [ast.parse(source).body for source in sources]
    substitution = Unifier(promote_equal_hof_literals=True).unify_blocks(blocks, [{}, {}])
    assert substitution is not None and len(substitution.param_expressions) == 1
    assert not substitution.mappings
    assert len(substitution.occurrence_mappings) == 2
    helper = _extract(blocks[0], substitution)
    literals = [node for node in ast.walk(helper) if isinstance(node, ast.Constant)]
    assert len(literals) == 1 and literals[0].value == 1000000


def test_carrier_does_not_collect_literals_inside_structurally_parameterized_ancestor() -> None:
    blocks, substitution = _unify(
        "return factory('a label with spaces').first",
        "return factory('a label with spaces').second",
        pools=True,
    )
    assert len(substitution.param_expressions) == 1
    assert substitution.mappings and not substitution.occurrence_mappings
    assert substitution.literal_pool is None
    helper = _extract(blocks[0], substitution)
    assert not any(isinstance(node, ast.Constant) for node in ast.walk(helper))


@pytest.mark.parametrize("conversion", ["", "!r", "!s", "!a"])
def test_evaluated_fstring_constants_preserve_text_conversion_and_format_effects(
    conversion: str,
) -> None:
    sources = tuple(
        "return f'head: {x+" + str(offset) + "+x*2" + conversion + ":>20} tail'"
        for offset in (1, 2)
    )
    blocks, substitution = _unify(*sources)
    helper = _extract(blocks[0], substitution)
    assert len(substitution.param_expressions) == 1
    assert len(helper.args.posonlyargs + helper.args.args) == 2
    formatted = [node for node in ast.walk(helper) if isinstance(node, ast.FormattedValue)]
    assert len(formatted) == 1 and isinstance(formatted[0].value, ast.BinOp)
    assert isinstance(formatted[0].value.left, ast.BinOp)
    assert isinstance(formatted[0].value.left.right, ast.Name)
    literals = [node.value for node in ast.walk(helper) if isinstance(node, ast.Constant)]
    assert sorted(value for value in literals if type(value) is str) == [" tail", ">20", "head: "]
    assert [value for value in literals if type(value) is int] == [2]
    events: list[tuple[str, object]] = []

    class Probe:
        def __init__(self, value: int) -> None:
            self.value = value

        def __add__(self, other: object) -> Probe:
            assert type(other) is int or isinstance(other, Probe)
            amount = other.value if isinstance(other, Probe) else other
            assert isinstance(amount, int)
            events.append(("add", amount))
            return Probe(self.value + amount)

        def __mul__(self, other: int) -> Probe:
            events.append(("mul", other))
            return Probe(self.value * other)

        def __repr__(self) -> str:
            events.append(("repr", self.value))
            return f"Probe({self.value})"

        def __str__(self) -> str:
            events.append(("str", self.value))
            return str(self.value)

        def __format__(self, spec: str) -> str:
            events.append(("format", spec))
            return format(self.value, spec)

    namespace: dict[str, object] = {}
    exec(ast.unparse(helper), namespace)
    generated = namespace[helper.name]
    assert isinstance(generated, FunctionType)
    for offset, source in zip((1, 2), sources):
        original_namespace: dict[str, object] = {}
        exec("def observed(x):\n    " + source, original_namespace)
        original = original_namespace["observed"]
        assert isinstance(original, FunctionType)
        events.clear()
        expected = original(Probe(3))
        expected_events = tuple(events)
        events.clear()
        assert generated(offset, Probe(3)) == expected
        assert tuple(events) == expected_events


def test_nested_format_spec_evaluates_occurrence_parameter() -> None:
    sources = ("return f'[{x:>{1+x*2}}]'", "return f'[{x:>{2+x*2}}]'")
    blocks, substitution = _unify(*sources)
    helper = _extract(blocks[0], substitution)
    assert len(helper.args.posonlyargs + helper.args.args) == 2
    namespace: dict[str, object] = {}
    exec(ast.unparse(helper), namespace)
    generated = namespace[helper.name]
    assert isinstance(generated, FunctionType)
    for offset, source in zip((1, 2), sources):
        original_namespace: dict[str, object] = {}
        exec("def observed(x):\n    " + source, original_namespace)
        original = original_namespace["observed"]
        assert isinstance(original, FunctionType)
        assert generated(offset, 3) == original(3)


def test_release_blocks_releases_pooled_occurrences_and_metadata_state() -> None:
    sources = [
        "value: Literal[1000000] = (1000000, 1000001)\nreturn x+" + str(offset) + "+x*2"
        for offset in (1, 2)
    ]
    blocks = [ast.parse(source).body for source in sources]
    snapshots = [
        ast.dump(ast.Module(body=block, type_ignores=[]), include_attributes=True)
        for block in blocks
    ]
    refs = [
        weakref.ref(node)
        for block in blocks
        for statement in block
        for node in ast.walk(statement)
        if isinstance(node, ast.expr)
    ]
    unifier = Unifier()
    substitution = unifier.unify_blocks(blocks, [{}, {}])
    assert substitution is not None
    assert any(unifier._pooled_literal_nodes)
    assert any(unifier._read_in_place)
    assert snapshots == [
        ast.dump(ast.Module(body=block, type_ignores=[]), include_attributes=True)
        for block in blocks
    ]
    unifier.release_blocks()
    assert unifier.current_blocks is None
    del blocks, substitution
    gc.collect()
    assert all(reference() is None for reference in refs)
    assert unifier._pooled_literal_nodes == ()
    assert unifier._read_in_place == ()
