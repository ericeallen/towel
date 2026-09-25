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

Extracting the duplicate body of ``Base.first`` once added ``_extracted_func_0``
to ``Base``, and ``Child`` then overrode it incompatibly. The project no longer
checked, and ``Child().first(2)`` returned ``'surprise'`` where it returned
``3``. A check that walked only ``lib`` never looked at ``consumer.py`` and
called it clean. That particular conflict is gone, since a method helper is
now class-private and stored as ``_Base__extracted_func_0``, out of every
subclass's reach; the check still has to see ``consumer.py``, because anything
else a candidate changes that a consumer's own code relies on, such as a type
a checker infers for an unannotated function, is judged only there.

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
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Callable, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Set
from typing import Tuple

from .source_files import is_environment

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
"""Directories that hold no project source, or hold a second copy of it.

Every name here is one no import can spell (it starts with a dot) or one a
tool chooses for what it keeps (``__pycache__``, ``node_modules``) or writes
(``build``, ``dist``). ``env`` and ``venv`` are not among them: a project may
call its own package that, and an environment is known by what it holds
(:func:`scanned_directories`).
"""


def scanned_directories(parent: str | Path, names: Iterable[str]) -> List[str]:
    """Which of ``parent``'s subdirectories ``names`` a scan of the project enters, sorted.

    Not those in ``SKIPPED_DIRECTORIES``, and not an environment packages are
    installed into, whatever it is called (``source_files.is_environment``).
    A package of the project's own named ``venv`` used to be skipped by its
    name, so no scan saw what it imports or defines.
    """
    return sorted(
        name
        for name in names
        if name not in SKIPPED_DIRECTORIES and not is_environment(Path(parent) / name)
    )


MAXIMUM_FILES = 20_000
"""Beyond this the tree is not a project, and the scan stops rather than crawl."""


class ScanLimitExceeded(RuntimeError):
    """The tree holds more files than the scan reads, so its consumers are unknown.

    A partial list would make the complete check silently incomplete: a
    consumer beyond the limit could be broken by the change and never checked.
    """


@dataclass(frozen=True)
class _ImportStatement:
    """One import as written: ``level`` dots, then ``module``, naming ``names`` from it.

    ``import a.b`` is ``(0, "a.b", ())``. Kept unresolved, because what a
    relative import names depends on the file's module name, and that depends
    on ``__init__`` files elsewhere in the tree, which can change while the
    file itself does not.
    """

    level: int
    module: Optional[str]
    names: Tuple[str, ...]


_Stamp = Tuple[int, int, int]
"""``(st_mtime_ns, st_size, st_ino)``: Towel replaces a file atomically, so the inode moves."""


@dataclass(frozen=True)
class _ScannedFile:
    stamp: _Stamp
    statements: Optional[Tuple[_ImportStatement, ...]]
    """``None`` when the file does not parse: it can import nothing, and no checker can build it."""


def _statements(tree: ast.Module) -> Tuple[_ImportStatement, ...]:
    found: List[_ImportStatement] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(_ImportStatement(0, alias.name, ()) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.append(
                _ImportStatement(node.level, node.module, tuple(a.name for a in node.names))
            )
    return tuple(found)


def _imported_modules(
    statements: Iterable[_ImportStatement], module: str, *, is_package: bool
) -> Set[str]:
    """Every module name this file imports, relative imports resolved.

    A submodule import implies its parents: ``import a.b.c`` reads ``a`` and
    ``a.b`` too, and either of them may be the package under refactoring.

    A relative import is resolved against the file's package, which for a
    package's ``__init__`` is the module itself: ``from .sub import y`` in
    ``app/__init__.py`` names ``app.sub``, not ``sub``. Taking the parent there
    missed every consumer that reaches a change through a re-export.

    ``from app import helpers`` may import the module ``app.helpers``, and
    whether it does is not visible here, so both are recorded. Recording only
    ``app`` missed every file that reaches the change through ``helpers``, since
    ``app/__init__`` itself need not consume it. A name that is not a module
    only ever matches a module that does not exist.
    """
    package = module if is_package else module.rpartition(".")[0]
    names: Set[str] = set()
    for statement in statements:
        if statement.level == 0:
            base = statement.module or ""
        else:
            # ``from . import x`` inside ``a.b.c`` names ``a.b``; each further
            # dot drops one more component.
            parts = package.split(".") if package else []
            climbed = parts[: len(parts) - (statement.level - 1)] if statement.level > 1 else parts
            base = ".".join([*climbed, statement.module] if statement.module else climbed)
        if base:
            names.add(base)
        names.update(f"{base}.{name}" if base else name for name in statement.names if name != "*")
    implied: Set[str] = set()
    for name in names:
        parts = name.split(".")
        implied.update(".".join(parts[: index + 1]) for index in range(len(parts)))
    return implied


def _python_files(root: Path) -> List[Path]:
    found: List[Path] = []
    for parent, directories, files in os.walk(root, onerror=lambda _: None):
        directories[:] = scanned_directories(parent, directories)
        for name in files:
            if name.endswith((".py", ".pyi")):
                found.append(Path(parent) / name)
                if len(found) > MAXIMUM_FILES:
                    raise ScanLimitExceeded(
                        f"{root} holds more than {MAXIMUM_FILES} Python files, so what imports"
                        " the change cannot be established"
                    )
    return found


@dataclass(frozen=True)
class _ImportGraph:
    """Which names each file defines, and which files import each name."""

    defines: Mapping[Path, FrozenSet[str]]
    importers: Mapping[str, FrozenSet[Path]]


def module_names(path: Path, root: Path, module_name: ModuleNamer) -> FrozenSet[str]:
    """Every dotted name ``path`` can be imported by, as far as the tree shows.

    ``module_name`` gives the name the ``__init__`` files imply, and where the
    outermost of those directories sits inside ``root`` without one of its own,
    the directories above it may be PEP 420 namespace packages: ``nsp/lib.py``
    is ``lib`` by its markers and ``nsp.lib`` to every file importing it. A scan
    that knew only the first name found no consumers for such a module at all.

    Whether a directory is a namespace package depends on the search path the
    checker is given, which is not known here, so every such name is taken.
    Over-including a consumer costs a little checking; missing one calls a
    broken project clean. No name shorter than the markers imply is taken:
    ``pkg/types.py`` in a regular package is never ``types``.
    """
    canonical = module_name(path)
    try:
        parts = list(path.relative_to(root).with_suffix("").parts)
    except ValueError:
        return frozenset({canonical})
    if parts and parts[-1] == "__init__":
        parts.pop()
    shortest = len(canonical.split("."))
    longer = {
        ".".join(parts[start:])
        for start in range(len(parts) - shortest + 1)
        if all(part.isidentifier() for part in parts[start:])
    }
    return frozenset({canonical, *longer})


def _graph(
    files: Mapping[Path, _ScannedFile], root: Path, module_name: ModuleNamer
) -> _ImportGraph:
    defines: Dict[Path, FrozenSet[str]] = {}
    importers: Dict[str, Set[Path]] = {}
    for path, scanned in files.items():
        if scanned.statements is None:
            continue
        names = module_names(path, root, module_name)
        defines[path] = names
        is_package = path.stem == "__init__"
        # A relative import means one thing, but which of the file's names the
        # checker uses is not known here; resolving against each over-includes.
        for name in names:
            for imported in _imported_modules(scanned.statements, name, is_package=is_package):
                importers.setdefault(imported, set()).add(path)
    return _ImportGraph(defines, {name: frozenset(paths) for name, paths in importers.items()})


def _reached(graph: _ImportGraph, provided: Iterable[str]) -> Set[Path]:
    """Every file importing a name in ``provided``, or a name such a file defines."""
    # A consumer of a consumer is reached through it: a test helper importing
    # the package, and the tests importing that.
    pending = [name for name in provided if name]
    seen = set(pending)
    reached: Set[Path] = set()
    while pending:
        for path in graph.importers.get(pending.pop(), ()):
            if path in reached:
                continue
            reached.add(path)
            for name in graph.defines[path] - seen:
                seen.add(name)
                pending.append(name)
    return reached


class ImportScan:
    """The import graph of the files under ``root``, following the tree as it changes.

    A prospective check happens hundreds of times in a run, so the graph is
    kept, and it has to be the graph of the project as it now stands: an
    in-place run writes each refactoring it applies before the next candidate is
    judged, and an applied refactoring can add an import. A graph kept from the
    start of the run never learned that a file had become a consumer, and the
    candidates that broke it were called clean.

    So each question walks the tree again and compares stamps, which is cheap,
    and parses only the files whose stamp moved, which is what costs. The graph
    itself is rebuilt whenever any file moved, because a new ``__init__`` renames
    the modules beside it without touching them.

    The graph holds every file. Which of the consumers are already being checked
    differs from one request to the next, so it is applied to the answer, never
    to what is kept: a scan that left out the packages of the first request
    answered every later request, whose packages were fewer, without them.
    """

    def __init__(self, root: Path, module_name: ModuleNamer) -> None:
        self._root = root
        self._module_name = module_name
        self._files: Dict[Path, _ScannedFile] = {}
        self._graph: Optional[_ImportGraph] = None

    def _follow_tree(self) -> _ImportGraph:
        current: Dict[Path, _ScannedFile] = {}
        for path in _python_files(self._root):
            resolved = path.resolve()
            try:
                status = path.stat()
            except OSError:
                continue  # Gone since the walk listed it; it imports nothing now.
            stamp = (status.st_mtime_ns, status.st_size, status.st_ino)
            known = self._files.get(resolved)
            current[resolved] = (
                known if known is not None and known.stamp == stamp else _scanned(path, stamp)
            )
        if self._graph is None or current != self._files:
            self._graph = _graph(current, self._root, self._module_name)
        self._files = current
        return self._graph

    def consumers(self, provided: Iterable[str], *, exclude: Sequence[Path] = ()) -> List[str]:
        """Files that import a module of ``provided``, transitively, outside ``exclude``.

        ``provided`` names the modules the packages under refactoring define, as
        dotted prefixes: a file importing ``pkg.thing`` consumes ``pkg``.
        ``exclude`` names directories already being checked, whose files are not
        returned again; files inside them still carry the change onwards.
        """
        wanted = [name for name in provided if name]
        if not wanted:
            return []
        excluded = [directory.resolve() for directory in exclude]
        return sorted(
            str(path)
            for path in _reached(self._follow_tree(), wanted)
            if not any(path.is_relative_to(directory) for directory in excluded)
        )


def _scanned(path: Path, stamp: _Stamp) -> _ScannedFile:
    try:
        tree = ast.parse(path.read_bytes(), filename=str(path))
    except (OSError, SyntaxError, ValueError):
        # It cannot import anything, and the checker could not build it.
        return _ScannedFile(stamp, None)
    return _ScannedFile(stamp, _statements(tree))


def consumers_of(
    root: Path,
    provided: Iterable[str],
    *,
    module_name: ModuleNamer,
    exclude: Sequence[Path] = (),
) -> List[str]:
    """Files under ``root`` that import a module of ``provided``, transitively; one scan."""
    return ImportScan(root, module_name).consumers(provided, exclude=exclude)


def module_prefixes(paths: Iterable[str], root: Path, module_name: ModuleNamer) -> Set[str]:
    """The dotted names the analyzed files define, with the packages holding them.

    ``pkg/inner/m.py`` contributes ``pkg.inner.m``, ``pkg.inner`` and ``pkg``,
    so a file importing any of them is a consumer of the change. Each of the
    file's names contributes (see :func:`module_names`).
    """
    names: Set[str] = set()
    for path in paths:
        for module in module_names(Path(path), root, module_name):
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
