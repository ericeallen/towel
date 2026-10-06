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

"""Receiver annotations share module binding scans without changing their conservative answers."""

from __future__ import annotations

import ast
import contextlib
import gc
import io
import weakref
from pathlib import Path
from unittest.mock import patch

import pytest

from towel.unification import placement
from towel.unification.bounded_cache import memoization_disabled
from towel.unification.refactor_engine import UnificationRefactorEngine
from tests.test_helpers import method_helper_calls


@pytest.mark.parametrize("name", ["Self", "missing"])
def test_repeated_binding_questions_walk_once_and_disabled_mode_recomputes(name: str) -> None:
    tree = ast.parse("from typing import Self\nif enabled:\n    other = value\n")
    before = ast.dump(tree, include_attributes=True)
    with patch.object(ast, "iter_child_nodes", wraps=ast.iter_child_nodes) as children:
        found = placement._module_scope_bindings(tree, name)
        traversals = children.call_count
        assert traversals > 0
        assert isinstance(found, tuple)
        assert bool(found) is (name == "Self")
        for _ in range(10):
            assert placement._module_scope_bindings(tree, name) == found
        assert children.call_count == traversals
    with (
        memoization_disabled(),
        patch.object(ast, "iter_child_nodes", wraps=ast.iter_child_nodes) as children,
    ):
        assert placement._module_scope_bindings(tree, name) == found
        assert placement._module_scope_bindings(tree, name) == found
        assert children.call_count == 2 * traversals
    assert ast.dump(tree, include_attributes=True) == before


@pytest.mark.parametrize(
    ("source", "name", "lines"),
    [
        ("from typing import Self\nfrom elsewhere import *\nSelf = object\n", "Self", (1, 2, 3)),
        ("from typing import Self as Receiver\nfrom elsewhere import *\n", "Self", (2,)),
        ("from typing import Self as Receiver\n", "Receiver", (1,)),
        ("if enabled:\n    Receiver = object\nelse:\n    Receiver = str\n", "Receiver", (1, 2, 4)),
        ("def f():\n    Receiver = object\nclass C:\n    Receiver = object\n", "Receiver", ()),
        # The existing scan deliberately overcounts compound statements whose
        # nested scope stores the name, while not entering that scope itself.
        ("if enabled:\n    def f():\n        Receiver = object\n", "Receiver", (1,)),
        ("def Receiver():\n    pass\nclass Receiver:\n    pass\n", "Receiver", (1, 3)),
        ("for Receiver in items:\n    Receiver = object\n", "Receiver", (1, 2)),
        ("try:\n    pass\nexcept Exception:\n    Receiver = object\n", "Receiver", (1, 4)),
    ],
)
def test_scan_preserves_order_aliases_stars_and_scope_boundaries(
    source: str, name: str, lines: tuple[int, ...]
) -> None:
    tree = ast.parse(source)
    for _ in range(2):
        assert tuple(node.lineno for node in placement._module_scope_bindings(tree, name)) == lines
    with memoization_disabled():
        assert tuple(node.lineno for node in placement._module_scope_bindings(tree, name)) == lines


def test_cache_separates_names_and_reparsed_modules() -> None:
    source = "from typing import Self\nReceiver = object\n"
    tree = ast.parse(source)
    assert placement._module_scope_bindings(tree, "Self") == (tree.body[0],)
    assert placement._module_scope_bindings(tree, "Receiver") == (tree.body[1],)
    revised = ast.parse("Self = object\nfrom typing import Self as Receiver\n")
    assert placement._module_scope_bindings(revised, "Self") == (revised.body[0],)
    assert placement._module_scope_bindings(revised, "Receiver") == (revised.body[1],)
    assert placement._module_scope_bindings(tree, "Self") == (tree.body[0],)


def test_memo_retains_neither_the_module_nor_its_statements() -> None:
    tree = ast.parse("from typing import Self\n")
    found = placement._module_scope_bindings(tree, "Self")
    assert found == (tree.body[0],)
    module_reference = weakref.ref(tree)
    statement_reference = weakref.ref(tree.body[0])
    del tree
    gc.collect()
    assert module_reference() is None
    del found
    gc.collect()
    assert statement_reference() is None


@pytest.mark.parametrize("receiver", ["Self", "Bound"])
def test_receiver_binding_memo_preserves_method_helper_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, receiver: str
) -> None:
    source = f"""from typing import Self, TypeVar

Bound = TypeVar("Bound", bound="Box")


class Box:
    offset = 10

    def first(self: {receiver}, value):
        start = self.offset + value
        doubled = start * 2
        result = doubled + 11
        return result

    def second(self: {receiver}, value):
        start = self.offset + value
        doubled = start * 2
        result = doubled + 23
        return result
"""
    monkeypatch.setenv("TOWEL_CHECK_AST_IMMUTABLE", "1")
    monkeypatch.setenv("TOWEL_WORKERS", "1")
    path = tmp_path / "receivers.py"
    outputs: list[str] = []
    for memoized in (True, False):
        path.write_text(source)
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
    assert method_helper_calls(outputs[0])
