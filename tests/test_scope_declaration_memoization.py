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

"""Scope declarations share a walk only while their unchanged function tree lives."""

from __future__ import annotations

import ast
import contextlib
import gc
import io
import symtable
import weakref
from pathlib import Path
from unittest.mock import patch

import pytest

from towel.unification.assignment_analyzer import scope_declarations
from towel.unification.bounded_cache import memoization_disabled
from towel.unification.models import FunctionNode
from towel.unification.refactor_engine import UnificationRefactorEngine


def _function(source: str) -> FunctionNode:
    function = ast.parse(source).body[0]
    assert isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
    return function


@pytest.mark.parametrize("prefix", ["def", "async def"])
@pytest.mark.parametrize("declared", [False, True])
def test_repeated_questions_walk_once_and_disabled_mode_recomputes(
    prefix: str, declared: bool
) -> None:
    declaration = "        global counter\n" if declared else "        pass\n"
    function = _function(f"{prefix} target(flag):\n    if flag:\n{declaration}    return flag\n")
    expected = frozenset({"counter"}) if declared else frozenset()
    before = ast.dump(function, include_attributes=True)
    with patch.object(ast, "iter_child_nodes", wraps=ast.iter_child_nodes) as children:
        result = scope_declarations(function)
        assert result == expected and isinstance(result, frozenset)
        traversals = children.call_count
        assert traversals > 0
        for _ in range(10):
            assert scope_declarations(function) == expected
        assert children.call_count == traversals
    with (
        memoization_disabled(),
        patch.object(ast, "iter_child_nodes", wraps=ast.iter_child_nodes) as children,
    ):
        assert scope_declarations(function) == expected
        assert scope_declarations(function) == expected
        assert children.call_count == 2 * traversals
    assert ast.dump(function, include_attributes=True) == before


@pytest.mark.parametrize("prefix", ["def", "async def"])
def test_declarations_match_cpython_without_entering_nested_scopes(prefix: str) -> None:
    source = f"""def outer():
    outer_name = 0
    {prefix} target(flag):
        global top
        if flag:
            global conditional
        while flag:
            nonlocal outer_name
            global loop_name
            break
        try:
            pass
        except ValueError:
            global handled
        finally:
            global finished
        def nested():
            global nested_name
            nonlocal outer_name
        async def nested_async():
            global async_name
        class Nested:
            global class_name
        return lambda: outer_name
"""
    tree = ast.parse(source)
    outer = tree.body[0]
    assert isinstance(outer, ast.FunctionDef)
    function = outer.body[1]
    assert isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
    table = symtable.symtable(source, "declarations.py", "exec").get_children()[0].get_children()[0]
    expected = frozenset(
        symbol.get_name()
        for symbol in table.get_symbols()
        if symbol.is_declared_global() or symbol.is_nonlocal()
    )
    assert expected == {"top", "conditional", "outer_name", "loop_name", "handled", "finished"}
    assert scope_declarations(function) == expected
    with memoization_disabled():
        assert scope_declarations(function) == expected


def test_distinct_function_trees_keep_their_own_declarations() -> None:
    original = _function("def target():\n    global before\n")
    revised = _function("def target():\n    global after\n")
    assert scope_declarations(original) == {"before"}
    assert scope_declarations(revised) == {"after"}
    assert scope_declarations(original) == {"before"}


def test_memo_does_not_retain_the_function_tree() -> None:
    function = _function("def target():\n    global counter\n")
    assert scope_declarations(function) == {"counter"}
    reference = weakref.ref(function)
    del function
    gc.collect()
    assert reference() is None


DECLARED_GLOBAL = """counter = 0


def first(value):
    global counter
    counter = value
    start = value + 1
    doubled = start * 2
    result = doubled + 11
    return result


def second(value):
    global counter
    counter = value
    start = value + 1
    doubled = start * 2
    result = doubled + 23
    return result
"""


def test_declaration_memo_preserves_fixed_point_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TOWEL_CHECK_AST_IMMUTABLE", "1")
    monkeypatch.setenv("TOWEL_WORKERS", "1")
    path = tmp_path / "declared.py"
    outputs: list[str] = []
    for memoized in (True, False):
        path.write_text(DECLARED_GLOBAL)
        engine = UnificationRefactorEngine(annotate_helpers=False)
        with contextlib.ExitStack() as stack:
            if not memoized:
                stack.enter_context(memoization_disabled())
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            assert engine.analyze_files([str(path)], progress="none")
            assert engine.analyze_files([str(path)], progress="none")
            result, applied, _ = engine.refactor_to_fixed_point(str(path), progress="none")
        assert applied > 0
        outputs.append(result)
    assert outputs[0] == outputs[1]
    assert "__extracted_func_" in outputs[0]
