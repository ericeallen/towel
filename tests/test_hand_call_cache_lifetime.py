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

"""Hand-call syntax may be shared, but module identity and lifetime may not.

The old weak-key memo kept applications that held their module, which held the
key tree alive. Besides defeating eviction, a shared tree reused the first
module's path and bindings for a later caller. These tests preserve application
forms and every site while checking identity and collection independently.
"""

from __future__ import annotations

import ast
import gc
from pathlib import Path
import weakref

from towel.unification.decorator_reach import _Module, _hand_calls, _layout
from towel.unification.module_bindings import global_bindings

_SOURCE = (
    "def f():\n"
    "    return 1\n"
    "f = checked(f)\n"
    "g = factory(option=True)(f)\n"
    "class C:\n"
    "    method = wrap(f, marker=True)\n"
)


def _module(path: Path, tree: ast.Module) -> _Module:
    bindings = global_bindings(_SOURCE)
    assert bindings is not None
    return _Module(str(path), _SOURCE, tree, bindings, _layout(tree))


def test_shared_hand_call_syntax_uses_each_requesting_module(tmp_path: Path) -> None:
    tree = ast.parse(_SOURCE)
    before = ast.dump(tree, include_attributes=True)
    first = _module(tmp_path / "first.py", tree)
    second = _module(tmp_path / "second.py", tree)
    for module in (first, second, first):
        found = _hand_calls(module)
        assert all(application.module is module for application, _ in found)
        assert [
            (application.form, application.site, spelled) for application, spelled in found
        ] == [
            ("bare", f"{Path(module.path).name}:3", "f"),
            ("called", f"{Path(module.path).name}:4", "f"),
            ("applied", f"{Path(module.path).name}:6", "f"),
        ]
        assert isinstance(found[-1][0].slot.owner, ast.ClassDef)
    assert ast.dump(tree, include_attributes=True) == before


def test_hand_call_memo_does_not_keep_its_module_or_tree_alive(tmp_path: Path) -> None:
    def consult() -> tuple[weakref.ReferenceType[ast.Module], weakref.ReferenceType[_Module]]:
        tree = ast.parse(_SOURCE)
        module = _module(tmp_path / "subject.py", tree)
        assert len(_hand_calls(module)) == 3
        return weakref.ref(tree), weakref.ref(module)

    tree_reference, module_reference = consult()
    gc.collect()
    assert module_reference() is None
    assert tree_reference() is None
