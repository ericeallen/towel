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

"""The modules a change can reach: what imports the packages under refactoring.

A complete project check exists to see the code around the change, not only the
changed file. Most of it is inside the package and mypy reaches it by following
imports out of the files it is given. The rest imports *into* the package, and
no amount of following imports forward will find it::

    # lib/base.py, under refactoring
    class Base:
        def first(self, value: int) -> int: ...

    # consumer.py, unchanged, never imported by lib
    class Child(lib.Base):
        def _extracted_func_0(self, value: int) -> str: ...

Extracting the duplicate body of ``Base.first`` adds ``_extracted_func_0`` to
``Base``, and ``Child`` now overrides it incompatibly. The project no longer
checks, and ``Child().first(2)`` returns ``'surprise'`` where it returned ``3``.
A check that walked only ``lib`` never looked at ``consumer.py`` and called it
clean.

Walking the whole repository instead is how this was once found, and it fails
for a different reason: repositories hold files mypy cannot build at all, test
data written to be invalid and demo scripts sharing a module name, and one of
them fails the build and refuses the project. Neither can import the package,
so neither is a consumer, and this module never selects one.

What it does select is every file that imports, directly or through another
such file, a module of a package under refactoring. Import edges are read with
``ast``, so a file that does not parse is simply not a consumer -- true, since
it cannot import anything, and true of the checker too, which could not build
it either. A dynamic import is invisible here, as it is to the checker.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set

ModuleNamer = Callable[[Path], str]
"""What names a file's module; supplied by the caller that owns the convention."""

SKIPPED_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".tox",
        ".nox",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "node_modules",
        "build",
        "dist",
        ".eggs",
    }
)
"""Directories that hold no project source, or hold a second copy of it."""

MAXIMUM_FILES = 20_000
"""Beyond this the tree is not a project, and the scan stops rather than crawl."""


def _imported_modules(tree: ast.Module, module: str) -> Set[str]:
    """Every module name this file imports, relative imports resolved.

    A submodule import implies its parents: ``import a.b.c`` reads ``a`` and
    ``a.b`` too, and either of them may be the package under refactoring.
    """
    package = module.rsplit(".", 1)[0] if "." in module else ""
    names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module:
                    names.add(node.module)
                continue
            # ``from . import x`` inside ``a.b.c`` names ``a.b``; each further
            # dot drops one more component.
            parts = package.split(".") if package else []
            climbed = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
            base = ".".join([*climbed, node.module] if node.module else climbed)
            if base:
                names.add(base)
    implied: Set[str] = set()
    for name in names:
        parts = name.split(".")
        implied.update(".".join(parts[: index + 1]) for index in range(len(parts)))
    return implied


def _python_files(root: Path) -> List[Path]:
    found: List[Path] = []
    for parent, directories, files in os.walk(root, onerror=lambda _: None):
        directories[:] = [name for name in directories if name not in SKIPPED_DIRECTORIES]
        for name in files:
            if name.endswith((".py", ".pyi")):
                found.append(Path(parent) / name)
                if len(found) >= MAXIMUM_FILES:
                    return found
    return found


def consumers_of(
    root: Path,
    provided: Iterable[str],
    *,
    module_name: ModuleNamer,
    exclude: Sequence[Path] = (),
) -> List[str]:
    """Files under ``root`` that import a module of ``provided``, transitively.

    ``provided`` names the modules the packages under refactoring define, as
    dotted prefixes: a file importing ``pkg.thing`` consumes ``pkg``. ``exclude``
    names directories already being checked, whose files are not returned again.
    """
    wanted = {name for name in provided if name}
    if not wanted:
        return []
    excluded = [directory.resolve() for directory in exclude]
    candidates: Dict[Path, Set[str]] = {}
    defines: Dict[Path, str] = {}
    for path in _python_files(root):
        resolved = path.resolve()
        if any(resolved.is_relative_to(directory) for directory in excluded):
            continue
        try:
            tree = ast.parse(path.read_bytes(), filename=str(path))
        except (OSError, SyntaxError, ValueError):
            # It cannot import anything, and the checker could not build it.
            continue
        name = module_name(path)
        defines[resolved] = name
        candidates[resolved] = _imported_modules(tree, name)
    reached: Set[Path] = set()
    # A consumer of a consumer is reached through it, so the set grows until it
    # stops: a test helper importing the package, and the tests importing that.
    growing = True
    while growing:
        growing = False
        for path, imports in candidates.items():
            if path in reached or imports.isdisjoint(wanted):
                continue
            reached.add(path)
            wanted.add(defines[path])
            growing = True
    return sorted(str(path) for path in reached)


def module_prefixes(paths: Iterable[str], module_name: ModuleNamer) -> Set[str]:
    """The dotted names the analyzed files define, with the packages holding them.

    ``pkg/inner/m.py`` contributes ``pkg.inner.m``, ``pkg.inner`` and ``pkg``,
    so a file importing any of them is a consumer of the change.
    """
    names: Set[str] = set()
    for path in paths:
        module = module_name(Path(path))
        parts = module.split(".")
        names.update(".".join(parts[: index + 1]) for index in range(len(parts)))
    return names


def walked_package(path: Path) -> Optional[Path]:
    """The top package ``path`` belongs to, or ``None`` when it belongs to none."""
    directory = path.parent
    if not _is_package(directory):
        return None
    while directory.parent != directory and _is_package(directory.parent):
        directory = directory.parent
    return directory


def _is_package(directory: Path) -> bool:
    return (directory / "__init__.py").exists() or (directory / "__init__.pyi").exists()
