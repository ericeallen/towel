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

"""Captured inlined targets must retain the caller's enclosing-cell topology."""

from __future__ import annotations

import ast
import copy
import gc
import weakref
from pathlib import Path

import pytest

from towel.canonical_ast import exact_dump
from towel.unification.scope_analyzer import ScopeAnalyzer
from towel.unification.semantic_safety import captured_comprehension_changes_enclosing_cell
from towel.unification.refactor_engine import UnificationRefactorEngine


def _analyze(source: str) -> tuple[ast.Module, ast.FunctionDef, ScopeAnalyzer]:
    module = ast.parse(source)
    analyzer = ScopeAnalyzer()
    analyzer.analyze(module)
    function = next(
        node
        for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef) and node.name == "site"
    )
    return module, function, analyzer


def _source(expression: str, *, continuation: str = "return o.v") -> str:
    return (
        "def outer(o, rows):\n    def site():\n        values = "
        + expression
        + "\n        "
        + continuation
        + "\n    return site()\n"
    )


@pytest.mark.parametrize(
    "expression",
    [
        "[(lambda: o + 1)() for o in rows]",
        "{(lambda: o + 1)() for o in rows}",
        "{o: (lambda: o + 1)() for o in rows}",
        "{(lambda: o + 1)(): 1 for o in rows}",
        "[(lambda: o + 1)() for _, o in rows]",
        "[(lambda: o + 1)() for p in rows for o in p]",
        "[[(lambda: o + 1)() for o in p] for p in rows]",
        "[next(o + q for q in (1,)) for o in rows]",
        "{next(o + q for q in (1,)) for o in rows}",
        "{o: next(o + q for q in (1,)) for o in rows}",
        "[list(q for q in (1,) if o) for o in rows]",
        "[list(q for p in (1,) for q in o) for o in rows]",
    ],
)
def test_original_inlined_target_captured_by_a_closure_is_refused(expression: str) -> None:
    source = _source(expression)
    module, function, analyzer = _analyze(source)
    before = exact_dump(module)
    for _ in range(10):
        assert captured_comprehension_changes_enclosing_cell(analyzer, function, function.body[:1])
    assert exact_dump(module) == before
    rebuilt, copied_function, copied_analyzer = _analyze(source)
    assert exact_dump(rebuilt) == before
    assert captured_comprehension_changes_enclosing_cell(
        copied_analyzer, copied_function, copied_function.body[:1]
    )


@pytest.mark.parametrize(
    "expression,continuation",
    [
        ("[o + 1 for o in rows]", "return o.v"),
        ("[(lambda: p + 1)() for p in rows]", "return o.v"),
        ("[(lambda: o + 1)() for o in rows]", "return values"),
        ("[(lambda o: o + 1)(o) for o in rows]", "return o.v"),
        ("[(lambda o=o: o + 1)() for o in rows]", "return o.v"),
        ("[(lambda: 1)() for o in rows]", "return o.v"),
        ("[(lambda: p + 1)() for o in rows for p in (1,)]", "return o.v"),
        ("[(lambda: o + 1)() for o in (1,)]", "return values"),
        ("((lambda: o + 1)() for o in rows)", "return o.v"),
        ("([(lambda: o + 1)() for o in rows] for p in (1,))", "return o.v"),
        ("(lambda: [(lambda: o + 1)() for o in rows])", "return o.v"),
        ("[list(q for q in o) for o in rows]", "return o.v"),
    ],
)
def test_ordinary_unrelated_and_separate_frame_comprehensions_stay_eligible(
    expression: str, continuation: str
) -> None:
    _, function, analyzer = _analyze(_source(expression, continuation=continuation))
    assert not captured_comprehension_changes_enclosing_cell(analyzer, function, function.body[:1])


@pytest.mark.parametrize(
    "source",
    [
        "def site(o, rows):\n    values = [(lambda: o+1)() for o in rows]\n    return o.v\n",
        "o=object()\ndef site(rows):\n    values = [(lambda: o+1)() for o in rows]\n    return o.v\n",
        "def outer(o, rows):\n    def site():\n        o=object()\n        values = [(lambda: o+1)() for o in rows]\n        return o.v\n    return site()\n",
    ],
)
def test_own_local_or_module_binding_is_not_an_enclosing_cell(source: str) -> None:
    _, function, analyzer = _analyze(source)
    assert not captured_comprehension_changes_enclosing_cell(analyzer, function, function.body)


def test_outer_generator_first_iterable_is_still_in_the_function_frame() -> None:
    _, function, analyzer = _analyze(_source("(q for q in [(lambda: o+1)() for o in rows])"))
    assert captured_comprehension_changes_enclosing_cell(analyzer, function, function.body[:1])


@pytest.mark.parametrize(
    "statement",
    [
        "values = (lambda ignored=[(lambda: o+1)() for o in rows]: ignored)()",
        "values = (lambda *, ignored=[next(o+q for q in (1,)) for o in rows]: ignored)()",
        "def consume(ignored=[(lambda: o+1)() for o in rows]):\n    return ignored",
        "def consume(*, ignored=[next(o+q for q in (1,)) for o in rows]):\n    return ignored",
        "@decorate([(lambda: o+1)() for o in rows])\ndef consume():\n    return 0",
        "class Made(factory([(lambda: o+1)() for o in rows])):\n    pass",
        "class Made(metaclass=factory([next(o+q for q in (1,)) for o in rows])):\n    pass",
        "def consume(value: [(lambda: o+1)() for o in rows]):\n    return value",
        "def consume() -> [next(o+q for q in (1,)) for o in rows]:\n    return 0",
    ],
)
def test_evaluated_definition_heads_stay_in_the_original_cell_frame(statement: str) -> None:
    body = "\n".join("        " + line for line in statement.splitlines())
    source = (
        "def outer(o, rows):\n    def site():\n"
        + body
        + "\n        return o.v\n    return site()\n"
    )
    module, function, analyzer = _analyze(source)
    before = exact_dump(module)
    assert captured_comprehension_changes_enclosing_cell(analyzer, function, function.body[:-1])
    assert exact_dump(module) == before


@pytest.mark.parametrize(
    "statement,prefix",
    [
        ("def consume():\n    return [(lambda: o+1)() for o in rows]", ""),
        ("class Made:\n    values = [(lambda: o+1)() for o in rows]", ""),
        (
            "def consume(value: [(lambda: o+1)() for o in rows]):\n    return value",
            "from __future__ import annotations\n",
        ),
        (
            "def consume() -> [next(o+q for q in (1,)) for o in rows]:\n    return 0",
            "from __future__ import annotations\n",
        ),
        ("values = (lambda ignored=[(lambda: r+1)() for r in rows]: ignored)()", ""),
    ],
)
def test_other_definition_frames_and_postponed_annotations_are_not_caller_cells(
    statement: str, prefix: str
) -> None:
    body = "\n".join("        " + line for line in statement.splitlines())
    source = (
        prefix
        + "def outer(o, rows):\n    def site():\n"
        + body
        + "\n        return o.v\n    return site()\n"
    )
    _, function, analyzer = _analyze(source)
    assert not captured_comprehension_changes_enclosing_cell(analyzer, function, function.body[:-1])


def test_unrelated_block_and_same_named_nested_target_are_not_confused() -> None:
    source = _source("[(lambda: o + 1)() for o in rows]")
    _, function, analyzer = _analyze(source)
    assert not captured_comprehension_changes_enclosing_cell(analyzer, function, function.body[1:])
    source = _source("[[(lambda: o+1)() for o in (1,)] for p in rows]")
    _, function, analyzer = _analyze(source)
    outer = function.body[0]
    assert captured_comprehension_changes_enclosing_cell(analyzer, function, [outer])
    # A generator owns the inner comprehension's frame rather than the original function.
    _, function, analyzer = _analyze(
        _source("([[(lambda: o+1)() for o in (1,)] for p in rows] for q in (1,))")
    )
    assert not captured_comprehension_changes_enclosing_cell(analyzer, function, function.body[:1])


def test_guard_keeps_no_new_owner_cache_and_can_reanalyze_a_copy() -> None:
    source = _source("[(lambda: o+1)() for o in rows]")
    module, function, analyzer = _analyze(source)
    refs = (weakref.ref(module), weakref.ref(function))
    assert captured_comprehension_changes_enclosing_cell(analyzer, function, function.body[:1])
    copied = copy.deepcopy(module)
    copied_analyzer = ScopeAnalyzer()
    copied_analyzer.analyze(copied)
    copied_function = next(
        node
        for node in ast.walk(copied)
        if isinstance(node, ast.FunctionDef) and node.name == "site"
    )
    assert captured_comprehension_changes_enclosing_cell(
        copied_analyzer, copied_function, copied_function.body[:1]
    )
    del module, function, analyzer
    gc.collect()
    assert all(ref() is None for ref in refs)


@pytest.mark.parametrize("warm", [False, True])
def test_site_bound_guard_rechecks_each_original_scope(tmp_path: Path, warm: bool) -> None:
    bad = _source("[(lambda: o+1)() for o in rows]")
    good = bad.replace("return o.v", "return values")
    engine = UnificationRefactorEngine()
    cases = [("bad", bad, True), ("good", good, False)]
    for name, source, expected in cases if warm else cases[::-1]:
        path = tmp_path / (name + ".py")
        path.write_text(source)
        analysis = engine.analysis_session.analyze_module(str(path))
        engine._record_function_paths(analysis.functions)
        function = next(entry.node for entry in analysis.functions if entry.node.name == "site")
        block = function.body[:1]
        site = engine._block_site(function, block)
        assert site is not None
        for _ in range(2):
            assert (
                engine._block_rejected(
                    captured_comprehension_changes_enclosing_cell,
                    block,
                    function,
                    analysis.module.scope_analyzer,
                    site=site,
                )
                is expected
            )
