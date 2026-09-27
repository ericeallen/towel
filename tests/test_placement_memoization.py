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

"""Repeated receiver questions share immutable method walks."""

from __future__ import annotations

import ast
import contextlib
import gc
import io
import weakref
from pathlib import Path
from unittest.mock import patch

import pytest

from towel.unification.bounded_cache import memoization_disabled
from towel.unification.placement import _dispatches_on
from towel.unification.refactor_engine import UnificationRefactorEngine

METHODS = """class Box:
    offset = 10

    def first(self, value):
        start = self.offset + value
        doubled = start * 2
        result = doubled + 11
        return result

    def second(self, value):
        start = self.offset + value
        doubled = start * 2
        result = doubled + 23
        return result
"""


def test_receiver_questions_walk_a_method_once_without_mutating_it() -> None:
    function = ast.parse("def method(self, other):\n    return self.a + other.b\n").body[0]
    assert isinstance(function, ast.FunctionDef)
    before = ast.dump(function, include_attributes=True)
    with patch.object(ast, "walk", wraps=ast.walk) as walks:
        for _ in range(10):
            assert _dispatches_on(function, "self")
            assert _dispatches_on(function, "other")
            assert not _dispatches_on(function, "unused")
    assert walks.call_count == 1
    assert ast.dump(function, include_attributes=True) == before
    # The differential-analysis switch must really recompute these answers.
    with memoization_disabled(), patch.object(ast, "walk", wraps=ast.walk) as walks:
        assert _dispatches_on(function, "self")
        assert not _dispatches_on(function, "unused")
    assert walks.call_count == 2


def test_receiver_memo_does_not_retain_the_method_tree() -> None:
    function = ast.parse("def method(self):\n    return self.value\n").body[0]
    assert isinstance(function, ast.FunctionDef)
    assert _dispatches_on(function, "self")
    reference = weakref.ref(function)
    del function
    gc.collect()
    assert reference() is None


def test_placement_memos_preserve_exact_fixed_point_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TOWEL_CHECK_AST_IMMUTABLE", "1")
    monkeypatch.setenv("TOWEL_WORKERS", "1")
    source = tmp_path / "box.py"
    outputs: list[str] = []
    for memoized in (True, False):
        source.write_text(METHODS)
        engine = UnificationRefactorEngine(annotate_helpers=False)
        with contextlib.ExitStack() as stack:
            if not memoized:
                stack.enter_context(memoization_disabled())
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            # A repeated analysis invokes the AST immutability check on reuse.
            assert engine.analyze_files([str(source)], progress="none")
            assert engine.analyze_files([str(source)], progress="none")
            result, applied, _ = engine.refactor_to_fixed_point(str(source), progress="none")
        assert applied > 0
        outputs.append(result)
    assert outputs[0] == outputs[1]
    assert "self.__extracted_func_" in outputs[0]
