# Copyright 2026 Eric Allen
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

"""Which builtin names a project's own code may store as an attribute of anything.

Relaxed mode reads a builtin bare in a helper shared across modules unless
something could give one participating module its own binding of the name
(docs/design/DECISIONS.md, "Relaxed mode reads a builtin bare across
modules"). A module object can reach code through any receiver, so a store
to ``x.len`` anywhere in the project, whatever ``x`` is, counts: as an
assignment, augmented or annotated assignment, deletion, loop, ``with`` or
comprehension target, ``setattr(x, "len", …)`` or ``delattr``, through any
receiver (``monkeypatch.setattr``), or a ``patch("pkg.module.len")`` target.
The scan is syntactic and deliberately coarse; it cannot see code outside the
project.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import AbstractSet, FrozenSet, Optional, Set

from ..consumers import MAXIMUM_FILES
from ..program_files import program_directories
from ..source_text import read_source


def _callee_name(call: ast.Call) -> str:
    callee = call.func
    if isinstance(callee, ast.Name):
        return callee.id
    return callee.attr if isinstance(callee, ast.Attribute) else ""


def _stored_attributes(tree: ast.AST, names: AbstractSet[str]) -> Set[str]:
    found: Set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and not isinstance(node.ctx, ast.Load)
            and node.attr in names
        ):
            found.add(node.attr)
        elif not isinstance(node, ast.Call):
            continue
        elif _callee_name(node) in {"setattr", "delattr"} and len(node.args) >= 2:
            # ``setattr(module, "len", …)``, ``monkeypatch.setattr(module, "len", …)``,
            # and ``monkeypatch.setattr("pkg.module.len", …)``.
            for argument in node.args[:2]:
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    last = argument.value.rsplit(".", 1)[-1]
                    if last in names:
                        found.add(last)
        elif (
            _callee_name(node) in {"patch", "setitem"}
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and node.args[0].value.rsplit(".", 1)[-1] in names
        ):
            # ``mock.patch("pkg.module.len", …)``.
            found.add(node.args[0].value.rsplit(".", 1)[-1])
    return found


def attribute_stores(root: Path, names: AbstractSet[str]) -> Optional[FrozenSet[str]]:
    """Those of ``names`` some file under ``root`` stores as an attribute; None past the scan's limit.

    A file is parsed only when its text mentions one of the names or is not
    ASCII; one that does not read or parse is skipped, since it cannot run
    either.
    """
    found: Set[str] = set()
    count = 0
    for parent, files in program_directories(root):
        for name in files:
            if not name.endswith(".py"):
                continue
            count += 1
            if count > MAXIMUM_FILES:
                return None
            try:
                text = read_source(str(Path(parent, name)))
            except (OSError, UnicodeError, SyntaxError):
                continue
            # Identifiers are NFKC-normalized by the parser (``ℓen`` is ``len``),
            # so only an ASCII file can be passed over by its text.
            if text.isascii() and not any(wanted in text for wanted in names):
                continue
            try:
                tree = ast.parse(text)
            except (SyntaxError, ValueError):
                continue
            found |= _stored_attributes(tree, names)
    return frozenset(found)
