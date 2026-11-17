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

"""A redirect must resolve its callee where the new call actually stands."""

from __future__ import annotations

import ast
from pathlib import Path
import sys
from textwrap import indent

import pytest

from tests.test_callback_parameters import run_script
from tests.test_redirect_soundness import EXPRESSION
from tests.test_shape_review import dry
from towel.unification.function_index import FunctionIndex
from towel.unification.models import ExpressionPlace, FunctionArtifact, Replacement
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.scope_analyzer import ScopeAnalyzer
from towel.unification.splicing import BlockColumns
from towel.unification.visitors import FunctionCollector


def _reachable(
    source: str, text: str, *, expression: bool = False, cross_module: bool = False
) -> bool:
    provider = "def total(value):\n    return value\n\n"
    tree = ast.parse(source if cross_module else provider + source)
    source = ast.unparse(tree)
    tree = ast.parse(source)
    analyzer = ScopeAnalyzer()
    root = analyzer.analyze(tree)
    artifacts: list[FunctionArtifact] = []
    FunctionCollector(
        lambda node, class_name, enclosing, ancestry: artifacts.append(
            FunctionArtifact(
                "module.py", node, source, analyzer, root, class_name, enclosing, ancestry
            )
        )
    ).visit(tree)
    candidates = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.expr if expression else ast.stmt) and ast.unparse(node) == text
    ]
    assert len(candidates) == 1, candidates
    original = candidates[0]
    assert original.end_lineno is not None and original.end_col_offset is not None
    replacement = Replacement(
        line_range=(original.lineno, original.end_lineno),
        node=ast.Expr(
            value=ast.Call(func=ast.Name(id="helper", ctx=ast.Load()), args=[], keywords=[])
        ),
        columns=BlockColumns(original.col_offset, original.end_col_offset),
        expression=(
            ExpressionPlace(
                (original.lineno, original.col_offset),
                (original.end_lineno, original.end_col_offset),
            )
            if expression
            else None
        ),
    )
    target = artifacts[0]
    if cross_module:
        provider_tree = ast.parse(provider)
        provider_function = provider_tree.body[0]
        assert isinstance(provider_function, ast.FunctionDef)
        provider_analyzer = ScopeAnalyzer()
        provider_root = provider_analyzer.analyze(provider_tree)
        target = FunctionArtifact(
            "provider.py",
            provider_function,
            provider,
            provider_analyzer,
            provider_root,
            None,
            None,
            [],
        )
    return UnificationRefactorEngine()._existing_function_reachable(
        target, replacement, "module.py", FunctionIndex.build(artifacts)
    )


@pytest.mark.parametrize(
    "binding",
    [
        "total = 0",
        "*total, other = (0, 1)",
        "if False:\n        del total",
        "if False:\n        total += 1",
        "[total := 0 for unused in (0,)]",
        "with manager() as total:\n        pass",
        "try:\n        pass\n    except Exception as total:\n        pass",
        "match value:\n        case {'x': total}:\n            pass",
        "match value:\n        case [*total]:\n            pass",
        "match value:\n        case {'x': _, **total}:\n            pass",
    ],
)
def test_statement_redirect_rejects_every_local_binding(binding: str) -> None:
    """Redirect review 1: lexical locals count even before or after their binding executes."""
    assert not _reachable(
        f"def report(value):\n    {binding}\n    print(value + 42)\n", "print(value + 42)"
    )


@pytest.mark.parametrize(
    "body",
    [
        "return [value + 42 for total, value in values]",
        "return [value for total, value in values if value + 42]",
        "return (value + 42 for total, value in values)",
        "return [value for value in values if value + 42 for total in (0,)]",
        "return lambda total: value + 42",
        "return lambda *, total: value + 42",
        "return lambda: (value + 42, (total := 0))",
    ],
)
def test_expression_location_includes_comprehension_and_lambda_scopes(body: str) -> None:
    """Redirect review 1: resolution uses the actual site, including future loop targets."""
    assert not _reachable(
        f"def report(values, value):\n    {body}\n", "value + 42", expression=True
    )


@pytest.mark.parametrize(
    "body",
    [
        "return [value for total in range(value + 42)]",
        "return lambda total=value + 42: total",
    ],
)
def test_first_iterable_and_lambda_default_use_enclosing_scope(body: str) -> None:
    """Redirect review 1 control: defaults and first iterables run in the outer scope."""
    assert _reachable(f"def report(value):\n    {body}\n", "value + 42", expression=True)


def test_whole_comprehension_statement_uses_enclosing_scope() -> None:
    """Redirect review 1 control: the call replacing a whole comprehension stands outside it."""
    assert _reachable(
        "def report(values):\n    print([value for total, value in values])\n",
        "print([value for total, value in values])",
    )


def test_enclosing_function_local_blocks_redirect() -> None:
    """Redirect review 1: a closure's local wins over the module definition."""
    assert not _reachable(
        "def outer(value):\n    total = 0\n    def report():\n        print(value + 42)\n",
        "print(value + 42)",
    )


def test_global_declaration_bypasses_enclosing_function_local() -> None:
    """Redirect review 1 control: explicit global resolution ignores enclosing locals."""
    assert _reachable(
        "def outer(value):\n    total = 0\n    def report():\n        global total\n        print(value + 42)\n",
        "print(value + 42)",
    )


def test_nonlocal_declaration_reaches_enclosing_function_local() -> None:
    """Redirect review 1: nonlocal is a closure binding rather than a global declaration."""
    assert not _reachable(
        "def outer(value):\n    total = 0\n    def report():\n        nonlocal total\n        print(value + 42)\n",
        "print(value + 42)",
    )


def test_method_skips_class_attribute_bindings() -> None:
    """Redirect review 1 control: class attributes do not bind a method's free names."""
    assert _reachable(
        "class Box:\n    total = 0\n    def report(self, value):\n        print(value + 42)\n",
        "print(value + 42)",
    )


def test_function_parameter_blocks_redirect() -> None:
    """Redirect review 1: an ordinary function parameter shadows the module callee."""
    assert not _reachable("def report(value, total):\n    print(value + 42)\n", "print(value + 42)")


@pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 695 syntax requires Python 3.12")
@pytest.mark.parametrize(
    "source",
    [
        "def report[total](value):\n    print(value + 42)\n",
        "class Box[total]:\n    def report(self, value):\n        print(value + 42)\n",
    ],
)
def test_type_parameter_blocks_redirect(source: str) -> None:
    """Redirect review 1: annotation-scope bindings remain visible through class bodies."""
    assert not _reachable(source, "print(value + 42)")


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("binding", ["[total := 0 for unused in (0,)]", "*total, other = (0, 1)"])
def test_statement_redirect_preserves_shadowed_callee_behavior(
    tmp_path: Path, strict: bool, binding: str
) -> None:
    """Redirect review 1: statement redirects must not call a walrus or starred binding."""
    block = f'    print({EXPRESSION})\n    print("done")\n'
    source = tmp_path / "input.py"
    source.write_text(
        "def total(value):\n"
        + block
        + "\ndef report(value):\n    "
        + binding
        + "\n"
        + block
        + '\n    print("after", value)\n\nreport(2)\n'
    )
    output = tmp_path / "output.py"
    before = run_script(source)
    assert before[0] == 0, before
    dry(source, output, strict, "--complete-scope", "--no-format")
    assert run_script(output) == before


@pytest.mark.parametrize("star", [False, True])
def test_borrower_star_import_makes_free_callee_uncertain(star: bool) -> None:
    """Redirect review 1: a star import may already bind a borrowed function's name."""
    source = (
        "from other import *\n" if star else ""
    ) + "def report(value):\n    print(value + 42)\n"
    assert _reachable(source, "print(value + 42)", cross_module=True) is not star


COMPREHENSION_CASES = [
    "comprehension_shadow",
    "comprehension_wrong_result",
    "comprehension_filter_shadow",
    "generator_shadow",
    "comprehension_later_binder",
]


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("case", COMPREHENSION_CASES)
def test_statement_redirect_preserves_whole_comprehension(
    tmp_path: Path, strict: bool, case: str
) -> None:
    """Redirect review 1: all five comprehensions remain safe inside whole statements."""
    comprehensions = {
        "comprehension_shadow": f"[{EXPRESSION} for total, value in values]",
        "comprehension_wrong_result": f"[{EXPRESSION} for total, value in values]",
        "comprehension_filter_shadow": f"[value for total, value in values if {EXPRESSION}]",
        "generator_shadow": f"list({EXPRESSION} for total, value in values)",
        "comprehension_later_binder": f"[value for value in values if {EXPRESSION} for total in (0,)]",
    }
    values = {
        "comprehension_wrong_result": "[(lambda value: -999, 2)]",
        "comprehension_later_binder": "[2, 3]",
    }.get(case, "[(0, 2), (1, 3)]")
    block = f'    print({comprehensions[case]})\n    print("done")\n'
    source = tmp_path / "input.py"
    source.write_text(
        "def total(values):\n"
        + block
        + '\ndef report(values):\n    print("before")\n'
        + block
        + '\n    print("after", len(values))\n\n'
        + f"report({values})\n"
    )
    output = tmp_path / "output.py"
    before = run_script(source)
    assert before[0] == 0, before
    dry(source, output, strict, "--complete-scope", "--no-format")
    assert run_script(output) == before
    # A comprehension-local spelling must not disable the safe statement redirect.
    report = next(
        node
        for node in ast.parse(output.read_text()).body
        if isinstance(node, ast.FunctionDef) and node.name == "report"
    )
    assert any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "total"
        for node in ast.walk(report)
    ), output.read_text()


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("case", COMPREHENSION_CASES)
def test_statement_redirect_declines_shadowing_in_loop_equivalents(
    tmp_path: Path, strict: bool, case: str
) -> None:
    """Redirect review 1: all five statement-loop equivalents retain the loop's shadow."""
    computation = (
        f'if {EXPRESSION}:\n    print(value)\nprint("done")'
        if case in {"comprehension_filter_shadow", "comprehension_later_binder"}
        else f'print({EXPRESSION})\nprint("done")'
    )
    # A later loop binding makes total a local throughout report, before it
    # has a value. The remaining forms bind it before the duplicate statements.
    if case == "comprehension_later_binder":
        loop = "for value in values:\n" + indent(computation, "    ")
        loop += "\n    for total in (0,):\n        pass"
        values = "[2, 3]"
    else:
        loop = "for total, value in values:\n" + indent(computation, "    ")
        values = (
            "[(lambda value: -999, 2)]"
            if case == "comprehension_wrong_result"
            else "[(0, 2), (1, 3)]"
        )
    source = tmp_path / "input.py"
    source.write_text(
        "def total(value):\n"
        + indent(computation, "    ")
        + "\n\ndef report(values):\n"
        + indent(loop, "    ")
        + '\n    print("after", len(values))\n\n'
        + f"report({values})\n"
    )
    output = tmp_path / "output.py"
    before = run_script(source)
    assert before[0] == 0, before
    dry(source, output, strict, "--complete-scope", "--no-format")
    assert run_script(output) == before
    report = next(
        node
        for node in ast.parse(output.read_text()).body
        if isinstance(node, ast.FunctionDef) and node.name == "report"
    )
    assert not any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "total"
        for node in ast.walk(report)
    )
