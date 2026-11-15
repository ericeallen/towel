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

"""Audit D1: differing f-string text keeps syntax, values, and formatting effects."""

from __future__ import annotations

import ast
from typing import Callable, cast

import pytest

from towel.unification.extractor import HygienicExtractor
from towel.unification.instantiation import instantiation_mismatch
from towel.unification.unifier import Unifier


@pytest.mark.parametrize(
    "left,right",
    [
        ('f"{x} ERROR records"', 'f"{x} WARN records"'),
        ('f"{{ERROR}} {x}"', 'f"{{WARN}} {x}"'),
        ('f"{x!r} ERROR"', 'f"{x!r} WARN"'),
        ('f"{x!a} ERROR"', 'f"{x!a} WARN"'),
        ('f"{x:>10} ERROR"', 'f"{x:<10} WARN"'),
        ('f"{x:>{3}} ERROR"', 'f"{x:>{4}} WARN"'),
        ('f"{x=} ERROR"', 'f"{x=} WARN"'),
        ('rf"\\path\\ERROR {x}"', 'rf"\\path\\WARN {x}"'),
        ('f"{x} {{inside}}"', 'f"{x} {{other}}"'),
        ('f"{x} {{a}} value"', 'f"{x} b value"'),
        ('f"{x}" " ERROR"', 'f"{x}" " WARN"'),
        ("f\"{x} {b'ERROR'!r}\"", "f\"{x} {b'WARN'!r}\""),
        ("f\"{x} {f'ERROR {x}'}\"", "f\"{x} {f'WARN {x}'}\""),
    ],
)
def test_differing_text_round_trips(left: str, right: str) -> None:
    """Audit D1: adversarial Python 3.11 syntax must instantiate and execute identically."""
    blocks = [ast.parse(f"return {expression}").body for expression in (left, right)]
    originals = [ast.dump(ast.Module(body=block, type_ignores=[])) for block in blocks]
    substitution = Unifier().unify_blocks(blocks, [{}, {}])
    assert substitution is not None
    extractor = HygienicExtractor()
    helper, order = extractor.extract_function(
        template_block=blocks[0],
        substitution=substitution,
        free_variables={"x"},
        enclosing_names=set(),
        is_value_producing=True,
        function_name="helper",
    )
    for index, expression in enumerate((left, right)):
        call = extractor.generate_call(
            function_name=helper.name,
            block_idx=index,
            substitution=substitution,
            param_order=order,
            free_variables={"x"},
            is_value_producing=True,
        )
        assert (
            instantiation_mismatch(
                helper,
                call,
                blocks[index],
                {},
                {},
                site_function_names={"x"},
                preamble_length=0,
                returns_variables=False,
            )
            is None
        )
        wrapper = ast.parse("def run(x): pass").body[0]
        assert isinstance(wrapper, ast.FunctionDef)
        wrapper.body = [call]
        module = ast.fix_missing_locations(ast.Module(body=[helper, wrapper], type_ignores=[]))
        rendered = ast.unparse(module)
        ast.parse(rendered, feature_version=(3, 11))
        namespace: dict[str, object] = {}
        exec(compile(rendered, "<audit-d1>", "exec"), namespace)
        run = cast(Callable[[object], object], namespace["run"])
        for value in (7, -2.5, "é{quoted}"):
            assert run(value) == eval(expression, {"x": value})
    assert [ast.dump(ast.Module(body=block, type_ignores=[])) for block in blocks] == originals


def test_text_parameter_excludes_shared_affixes() -> None:
    """Audit D1: the argument describes the difference, not the surrounding sentence."""
    blocks = [ast.parse(f'return f"{{x}} {level} records"').body for level in ("ERROR", "WARN")]
    substitution = Unifier().unify_blocks(blocks, [{}, {}])
    assert substitution is not None
    extractor = HygienicExtractor()
    helper, order = extractor.extract_function(
        template_block=blocks[0],
        substitution=substitution,
        free_variables={"x"},
        enclosing_names=set(),
        is_value_producing=True,
    )
    passed = []
    for index in range(2):
        call = extractor.generate_call(
            function_name=helper.name,
            block_idx=index,
            substitution=substitution,
            param_order=order,
            free_variables={"x"},
            is_value_producing=True,
        )
        assert isinstance(call, ast.Return) and isinstance(call.value, ast.Call)
        passed.append([arg.value for arg in call.value.args if isinstance(arg, ast.Constant)])
    assert passed == [["ERROR"], ["WARN"]]


@pytest.mark.parametrize("marker", ["gettext", "custom_message"])
def test_translation_text_stays_pinned(marker: str) -> None:
    """Audit D1: parameterization never hides messages from translation extraction."""
    from towel.unification.static_positions import DEFAULT_TRANSLATION_KEYWORDS

    blocks = [ast.parse(f'return {marker}(f"{{x}} {level}")').body for level in ("ERROR", "WARN")]
    assert (
        Unifier().unify_blocks(
            blocks,
            [{}, {}],
            translation_keywords=DEFAULT_TRANSLATION_KEYWORDS.with_specs(["custom_message"]),
        )
        is None
    )


def test_text_fragments_survive_cached_substitution() -> None:
    """Audit D1: cached recipes retain affixes without holding source ASTs."""
    from towel.unification.structural_memo import load_substitution, store_substitution

    sources = ['return f"{x} ERROR records"', 'return f"{x} WARN records"']
    blocks = [ast.parse(source).body for source in sources]
    renames: list[dict[str, str]] = [{}, {}]
    substitution = Unifier().unify_blocks(blocks, renames)
    assert substitution is not None
    stored = store_substitution(substitution, blocks, renames)
    restored, _ = load_substitution(stored, [ast.parse(source).body for source in sources])
    assert restored.fstring_affixes == substitution.fstring_affixes
    assert restored.occurrence_mappings == substitution.occurrence_mappings


@pytest.mark.parametrize(
    "left,right",
    [
        ('f"{x}"', 'f"{x:>10}"'),
        ('f"{x!s} ERROR"', 'f"{x!r} WARN"'),
    ],
)
def test_different_formatting_operations_are_declined(left: str, right: str) -> None:
    """Audit D1: text parameters cannot erase a conversion or format operation."""
    assert (
        Unifier().unify_blocks(
            [ast.parse(f"return {expr}").body for expr in (left, right)], [{}, {}]
        )
        is None
    )


def test_single_chunk_spec_keeps_the_original_literal_object() -> None:
    """Audit D1: format callbacks may observe spec identity, not only equality."""
    blocks = [
        ast.parse(f'return f"{{x:shared {label} suffix}}"').body for label in ("ERROR", "WARN")
    ]
    substitution = Unifier().unify_blocks(blocks, [{}, {}])
    assert substitution is not None
    assert list(substitution.fstring_affixes.values()) == [("", "")]
    extractor = HygienicExtractor()
    helper, order = extractor.extract_function(
        template_block=blocks[0],
        substitution=substitution,
        free_variables={"x"},
        enclosing_names=set(),
        is_value_producing=True,
        function_name="helper",
    )
    calls = [
        extractor.generate_call(
            function_name=helper.name,
            block_idx=index,
            substitution=substitution,
            param_order=order,
            free_variables={"x"},
            is_value_producing=True,
        )
        for index in range(2)
    ]
    source = ast.unparse(helper) + "\n" + """
class Observer:
    def __format__(self, spec):
        assert spec is expected
        return 'formatted'
"""
    for label, call in zip(("ERROR", "WARN"), calls):
        assert isinstance(call, ast.Return) and call.value is not None
        source += f"expected = 'shared {label} suffix'\nx = Observer()\nassert {ast.unparse(call.value)} == 'formatted'\n"
    exec(compile(source, "<spec-identity>", "exec"), {})
