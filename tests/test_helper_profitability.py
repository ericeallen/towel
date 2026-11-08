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

"""Every abstraction must repay its complete interface and call-site burden."""

import ast
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from towel.diagnostics import Settings
from towel.unification.models import Replacement
from towel.unification.profitability import worthwhile_helper
from towel.unification.refactor_engine import UnificationRefactorEngine


def _helper(parameters: str, body: str) -> ast.FunctionDef:
    node = ast.parse(f"def helper({parameters}):\n" + body).body[0]
    assert isinstance(node, ast.FunctionDef)
    return node


def _sites(expression: str, count: int = 2) -> list[Replacement]:
    return [Replacement((index, index), ast.parse(expression).body[0]) for index in range(count)]


def test_five_inputs_for_two_simple_statements_need_more_than_two_sites() -> None:
    helper = _helper("a,b,c,d,e", "    a.append(b+c+d+e)\n    a.append(b*c*d*e)\n")
    assert not worthwhile_helper(helper, _sites("helper(a,b,c,d,e)"))
    assert worthwhile_helper(helper, _sites("helper(a,b,c,d,e)", 3))


def test_final_receiver_holder_and_deferred_wrappers_count_as_interface_cost() -> None:
    body = "    a.append(b+c)\n    a.append(b*c)\n    a.append(c+d)\n"
    assert worthwhile_helper(_helper("a,b,c,d", body), _sites("helper(a,b,c,d)"))
    assert not worthwhile_helper(
        _helper("self,a,b,c,_towel_owner", body), _sites("self.helper(a,b,c,owner)")
    )
    assert not worthwhile_helper(_helper("a,b,c,d", body), _sites("helper(a,lambda:b,lambda:c,d)"))


@pytest.mark.parametrize(
    ("body", "worthwhile_at_eight_sites"),
    [
        ("    if a:\n        return b(c,d)\n    return e\n", False),
        ("    return [b(x,c,d) for x in a if e(x)]\n", True),
        ("    return b(c,d) if a else e\n", False),
        ("    a.append(b+c)\n    a.append(b*d)\n    a.append(e)\n", True),
    ],
)
def test_control_flow_and_larger_bodies_must_repay_their_interfaces(
    body: str, worthwhile_at_eight_sites: bool
) -> None:
    helper = _helper("a,b,c,d,e", body)
    assert not worthwhile_helper(helper, _sites("helper(a,b,c,d,e)"))
    assert worthwhile_helper(helper, _sites("helper(a,b,c,d,e)", 8)) == worthwhile_at_eight_sites


def test_syntax_cost_does_not_depend_on_identifier_length_or_line_wrapping() -> None:
    helper = _helper("a,b,c,d,e", "    a.append(b+c+d+e)\n    a.append(b*c*d*e)\n")
    original = ast.dump(helper)
    sites = _sites("helper(a,b,c,d,e)", 3)
    assert worthwhile_helper(helper, sites)
    assert ast.dump(helper) == original
    helper.name = "a_descriptive_name_that_does_not_make_the_abstraction_more_complex"
    long_sites = _sites(f"{helper.name}(\n a,\n b,\n c,\n d,\n e\n)", 3)
    assert worthwhile_helper(helper, long_sites)


def test_complete_ownership_plumbing_declines_the_old_readme_example(tmp_path: Path) -> None:
    body = (
        "    items = [i for i in order.items if i.in_stock]\n"
        "    subtotal = sum(i.price for i in items)\n"
        "    total = round(subtotal * 1.08, 2)\n"
        "    return f'{label} {order.id}: ${total}'\n"
    )
    source = (
        'def order_summary(order, label="Order"):\n'
        + body
        + '\ndef quote_summary(quote, label="Quote"):\n'
        + body.replace("order", "quote")
    )
    path = tmp_path / "summary.py"
    path.write_text(source)
    for filtering, expected in ((True, 0), (False, 1)):
        engine = UnificationRefactorEngine(skip_trivial_helpers=filtering)
        output, count, _ = engine.refactor_to_fixed_point(
            str(path), output_path=str(tmp_path / f"output-{filtering}.py"), progress="none"
        )
        assert count == expected
        assert (output == source) == filtering
    assert path.read_text() == source


def test_analysis_counts_the_minimum_completed_annotation_signature(tmp_path: Path) -> None:
    original = (Path(__file__).parents[1] / "test_examples" / "annotated_module.py").read_text()
    functions: list[ast.stmt] = [
        node
        for node in ast.parse(original).body
        if isinstance(node, ast.FunctionDef) and node.name in {"describe_counts", "describe_shares"}
    ]
    source = ast.unparse(ast.Module(body=functions, type_ignores=[])) + "\n"
    path = tmp_path / "counts.py"
    path.write_text(source)
    bare = UnificationRefactorEngine(annotate_helpers=False)
    proposals = bare.analyze_file(str(path))
    assert proposals
    for proposal in proposals:
        assert bare.apply_refactoring(str(path), proposal) != source
    # Completing the generated partly annotated signature adds three Any
    # parameters. Do not offer a proposal that application will then refuse.
    annotated = UnificationRefactorEngine()
    assert annotated.analyze_file(str(path)) == []
    assert path.read_text() == source


def _source(wrapped: bool) -> str:
    append = "a.append(\n        b+c+d+e\n    )" if wrapped else "a.append(b+c+d+e)"
    return (
        "def first(a,b,c,d,e):\n"
        "    a.reverse()\n"
        f"    {append}\n"
        "    a.append(b*c*d*e)\n"
        "    try:\n        return sum(a)\n    except ValueError:\n        return 0\n"
        "def second(a,b,c,d,e):\n"
        "    assert a\n"
        f"    {append}\n"
        "    a.append(b*c*d*e)\n"
        "    for value in a:\n        if value < 0:\n            return value\n"
        "    return a[-1]\n"
    )


@pytest.mark.parametrize("wrapped", [False, True])
def test_source_wrapping_cannot_make_the_five_input_two_statement_helper_worthwhile(
    tmp_path: Path, wrapped: bool
) -> None:
    source = _source(wrapped)
    path = tmp_path / "subject.py"
    path.write_text(source)
    engine = UnificationRefactorEngine(settings=Settings.from_environ({"TOWEL_WORKERS": "1"}))
    output, applied, _ = engine.refactor_to_fixed_point(
        str(path), output_path=str(tmp_path / "output.py"), progress="none"
    )
    assert applied == 0
    assert output == source
    assert path.read_text() == source


def test_three_clustered_sites_can_repay_the_same_small_interface(tmp_path: Path) -> None:
    source = _source(True) + (
        "def third(a,b,c,d,e):\n"
        "    with open(__file__) as stream:\n        stream.read(0)\n"
        "    a.append(\n        b+c+d+e\n    )\n"
        "    a.append(b*c*d*e)\n"
        "    while False:\n        a.clear()\n"
        "    return tuple(a)\n"
    )
    path = tmp_path / "subject.py"
    path.write_text(source)
    engine = UnificationRefactorEngine(settings=Settings.from_environ({"TOWEL_WORKERS": "1"}))
    output, applied, _ = engine.refactor_to_fixed_point(
        str(path), output_path=str(tmp_path / "output.py"), progress="none"
    )
    assert applied == 1
    helpers = [
        node
        for node in ast.walk(ast.parse(output))
        if isinstance(node, ast.FunctionDef) and "extracted_func_" in node.name
    ]
    assert len(helpers) == 1
    assert len(helpers[0].args.args) + len(helpers[0].args.posonlyargs) == 5
    assert len(helpers[0].body) == 2
    observations: list[list[tuple[object, list[int]]]] = []
    for program in (source, output):
        namespace: dict[str, object] = {"__file__": str(path)}
        exec(program, namespace)
        rows: list[tuple[object, list[int]]] = []
        for name in ("first", "second", "third"):
            function = cast(Callable[[list[int], int, int, int, int], object], namespace[name])
            values = [99, 100]
            rows.append((function(values, 1, 2, 3, 4), values))
        observations.append(rows)
    assert observations[0] == observations[1]
    assert path.read_text() == source
