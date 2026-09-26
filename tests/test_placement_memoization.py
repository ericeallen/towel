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

"""Repeated placement questions share parsing and method walks, never graph-dependent verdicts."""

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
from towel.unification.import_graph import ImportGraphCache, ImportTimeCode
from towel.unification.models import ClassInfo
from towel.unification.module_bindings import global_bindings
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


def test_host_questions_parse_a_source_once_without_mutating_it(tmp_path: Path) -> None:
    path = tmp_path / "box.py"
    info = ClassInfo(name="Box", qualname="Box", file_path=str(path))
    engine = UnificationRefactorEngine()
    # ModuleBindings has its own independent parse cache; count only the
    # repeated parsing of ImportTimeCode under examination here.
    assert global_bindings(METHODS) is not None
    with patch.object(ast, "parse", wraps=ast.parse) as parses:
        for _ in range(10):
            assert engine._hosts_method_helpers(info, {str(path): METHODS})
    assert parses.call_count == 1
    tree = engine._parse_source(METHODS)
    before = ast.dump(tree, include_attributes=True)
    assert engine._hosts_method_helpers(info, {str(path): METHODS})
    assert ast.dump(tree, include_attributes=True) == before
    assert engine._parse_source(METHODS) is tree


def test_host_question_reads_revised_source_at_the_same_path(tmp_path: Path) -> None:
    path = tmp_path / "box.py"
    info = ClassInfo(name="Box", qualname="Box", file_path=str(path))
    engine = UnificationRefactorEngine()
    path.write_text(METHODS)
    assert engine._hosts_method_helpers(info, {})
    path.write_text(METHODS + "\n    def __getattribute__(self, name):\n        return 0\n")
    assert not engine._hosts_method_helpers(info, {})
    # The pair's source takes precedence over a different version on disk.
    assert engine._hosts_method_helpers(info, {str(path): METHODS})


def test_host_verdict_follows_a_changed_imported_base(tmp_path: Path) -> None:
    base = tmp_path / "base.py"
    path = tmp_path / "box.py"
    source = "from base import Base\n\n" + METHODS.replace("class Box:", "class Box(Base):")
    base.write_text("class Base:\n    pass\n")
    path.write_text(source)
    info = ClassInfo(name="Box", qualname="Box", file_path=str(path))
    engine = UnificationRefactorEngine()
    assert engine._hosts_method_helpers(info, {str(path): source})
    tree = engine._parse_source(source)
    base.write_text("class Base:\n    def __getattribute__(self, name):\n        return 0\n")
    engine.import_graph.begin_run()
    assert not engine._hosts_method_helpers(info, {str(path): source})
    # The host's source did not change; its parse can still be reused even
    # though the inherited class machinery requires a different answer.
    assert engine._parse_source(source) is tree


@pytest.mark.parametrize("intercepts_attributes", [False, True])
def test_supplied_tree_matches_fresh_parsing_through_guards_and_imported_bases(
    tmp_path: Path, intercepts_attributes: bool
) -> None:
    (tmp_path / "base.py").write_text(
        "class Base:\n"
        + (
            "    def __getattribute__(self, name):\n        return 0\n"
            if intercepts_attributes
            else "    pass\n"
        )
    )
    path = tmp_path / "box.py"
    source = (
        "from typing import TYPE_CHECKING\nfrom base import Base\n\n"
        "if TYPE_CHECKING:\n    raise RuntimeError('type-only')\n\n"
        + METHODS.replace("class Box:", "class Box(Base):")
    )
    path.write_text(source)
    tree = ast.parse(source)
    before = ast.dump(tree, include_attributes=True)
    fresh = ImportTimeCode(source, path=path, cache=ImportGraphCache())
    shared = ImportTimeCode(source, path=path, cache=ImportGraphCache(), tree=tree)
    for _ in range(2):
        assert shared.statements() == fresh.statements()
        assert shared.hosts_method_helpers("Box") == fresh.hosts_method_helpers("Box")
        assert shared.hosts_method_helpers("Box") is not intercepts_attributes
    assert shared.tree is tree
    assert ast.dump(tree, include_attributes=True) == before


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
