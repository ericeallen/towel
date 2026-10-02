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

"""Ordinary bindings that can change a builtin lookup in a borrowed helper.

A helper reads bare builtins in its host module. Cross-module extraction must
therefore retain evidence that a participating module binds that name: lexical
bindings, ``global`` declarations, star imports, ``__builtins__`` rebinding,
and direct attribute assignment or deletion on an imported module.

Module references follow absolute and relative imports, simple aliases and
``importlib.import_module`` with a statically spelled module name. Reflection
through namespace dictionaries, ``sys.modules``, ``getattr``, patch APIs or
``setattr`` is outside the preservation contract and is not scanned.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import AbstractSet, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Set
from typing import Tuple, Union

from ..analysis_sources import parse_analysis_source
from ..consumers import MAXIMUM_FILES
from ..program_files import program_directories, refuse_unparsed_file
from .bounded_cache import BoundedCache
from .builtins import BUILTIN_NAMES
from .module_bindings import global_bindings

ANY_NAME = "*"
"""The unknown name reported when the project is too large to scan whole."""


@dataclass(frozen=True)
class NamespaceWrite:
    """A place in the project that may bind ``name`` in a module's namespace."""

    name: str
    """The name written, or :data:`ANY_NAME`."""
    site: str
    """Where, as ``path:line`` below the project root."""


_ModuleRef = Union[Path, str]
"""A module named by its file, or by a dotted name."""


@dataclass(frozen=True)
class ProjectWrites:
    """Direct module-attribute writes found in the project's own files."""

    root: Path
    by_path: Mapping[Path, Tuple[NamespaceWrite, ...]]
    by_name: Mapping[str, Tuple[NamespaceWrite, ...]]
    complete: bool
    """False when the project holds more files than a scan reads: any write may be missed."""

    def into(self, module: Path) -> Tuple[NamespaceWrite, ...]:
        """The writes that may land in the namespace of ``module``, a resolved path below the root."""
        if not self.complete:
            return (NamespaceWrite(ANY_NAME, f"{self.root} is too large to read whole"),)
        found = list(self.by_path.get(module, ()))
        for name in module_names(module, self.root):
            found.extend(self.by_name.get(name, ()))
        return tuple(found)


def module_names(module: Path, root: Path) -> FrozenSet[str]:
    """Every dotted name the path of ``module`` gives it below ``root``.

    ``root/src/pkg/mod.py`` is ``src.pkg.mod``, ``pkg.mod`` and ``mod``, and a
    package's ``__init__.py`` is named for its directory. Whichever of them
    the program's imports use, it is among these.
    """
    try:
        parts = list(module.relative_to(root).with_suffix("").parts)
    except ValueError:
        return frozenset()
    if parts and parts[-1] == "__init__":
        parts.pop()
    return frozenset(
        ".".join(parts[start:])
        for start in range(len(parts))
        if all(part.isidentifier() for part in parts[start:])
    )


# -- the project scan ---------------------------------------------------------

# A direct store or deletion must mention a builtin attribute. Read-only
# mentions also pass this conservative gate: punctuation cannot distinguish
# every valid multiline, parenthesized or unpacking assignment target.
_MAY_WRITE = re.compile(
    r"\.[\s\\]*(?:"
    + "|".join(sorted((re.escape(name) for name in BUILTIN_NAMES if name.isidentifier())))
    + r")\b"
)


def scan_project_writes(
    root: Path, excluded_names: AbstractSet[str] = frozenset()
) -> ProjectWrites:
    """The writes into module namespaces that the Python files under ``root`` make.

    It reads the program's files (``program_directories``), those the run
    excludes included, since unchanged files can still assign module
    attributes; stubs never run and are not read. A file that does not parse
    here refuses the run unless ``excluded_names`` names it
    (:func:`_file_writes`). Past the consumer scan's limit the project cannot
    be read whole, and the answer says so rather than claim no write exists.
    A file is parsed only when its text mentions a builtin attribute; the
    AST scanner then distinguishes stores and deletions from read-only uses.
    """
    project = root.resolve()
    by_path: Dict[Path, List[NamespaceWrite]] = {}
    by_name: Dict[str, List[NamespaceWrite]] = {}
    count = 0
    for parent, files in program_directories(project):
        for name in files:
            if not name.endswith(".py"):
                continue
            count += 1
            if count > MAXIMUM_FILES:
                return ProjectWrites(project, {}, {}, complete=False)
            path = Path(parent, name)
            scanned = _file_writes(path, project, excluded_names)
            if scanned is None:
                continue
            for target, writes in scanned.by_path.items():
                by_path.setdefault(target, []).extend(writes)
            for dotted, writes in scanned.by_name.items():
                by_name.setdefault(dotted, []).extend(writes)
    return ProjectWrites(
        project,
        {target: tuple(writes) for target, writes in by_path.items()},
        {dotted: tuple(writes) for dotted, writes in by_name.items()},
        complete=True,
    )


@dataclass(frozen=True)
class _FileWrites:
    by_path: Mapping[Path, Tuple[NamespaceWrite, ...]]
    by_name: Mapping[str, Tuple[NamespaceWrite, ...]]


def _file_writes(
    path: Path,
    root: Path,
    excluded_names: AbstractSet[str] = frozenset(),
) -> Optional[_FileWrites]:
    """What ``path`` writes into module namespaces; None when it cannot be read or writes nothing.

    A file whose text has no possible ordinary write is not parsed. One
    that is parsed and does not parse here may run on a newer Python
    and write there, so it refuses the run, unless the run excludes it; one
    that does not decode runs nowhere, and writes nothing.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if not _MAY_WRITE.search(data.decode("utf-8", errors="replace")):
        return None
    try:
        tree = parse_analysis_source(data, filename=str(path))
    except (SyntaxError, ValueError) as error:
        refuse_unparsed_file(path, root, excluded_names, error)
        return None  # Excluded, or not text in its declared encoding: it writes nothing.
    resolved = path.resolve()
    scanner = _WriteScanner(resolved, tree, _shown(resolved, root))
    scanner.visit(tree)
    return _FileWrites(
        {target: tuple(writes) for target, writes in scanner.by_path.items()},
        {dotted: tuple(writes) for dotted, writes in scanner.by_name.items()},
    )


def _shown(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


# -- what one file writes -----------------------------------------------------


class _References:
    """Which expressions of one file denote a module, and which spell a string, found statically.

    Every import binds, whatever scope it is in, and so does a plain
    assignment of a module reference (``mod = importlib.import_module(...)``):
    a name bound to a module anywhere may be one wherever it is read, which
    can only add evidence. A string is read
    through literals, f-strings and ``+``, and through a name the file binds
    once, to such a string (``MODULE = "pkg.mod"``).
    """

    def __init__(self, path: Path, nodes: Sequence[ast.AST]) -> None:
        self._path = path
        self._strings = _string_names(nodes)
        self._bound: Dict[str, Set[_ModuleRef]] = {}
        for node in nodes:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        self._bind(alias.asname, {alias.name})
                    else:
                        head = alias.name.split(".")[0]
                        self._bind(head, {head})
            elif isinstance(node, ast.ImportFrom):
                self._bind_import_from(node)
        for node in nodes:
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                found = self.of(node.value)
                if found:
                    self._bind(node.targets[0].id, found)

    def _bind(self, name: str, refs: Iterable[_ModuleRef]) -> None:
        self._bound.setdefault(name, set()).update(refs)

    def _bind_import_from(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name == "*":
                continue
            local = alias.asname or alias.name
            if not node.level:
                if node.module:
                    self._bind(local, {f"{node.module}.{alias.name}"})
                continue
            base = _climbed(self._path.parent, node.level - 1)
            if node.module:
                base = base.joinpath(*node.module.split("."))
            self._bind(local, _module_files(base / alias.name))

    def text(self, node: ast.expr) -> Optional[str]:
        """The string ``node`` spells, when every part of it is known."""
        parts = _string_parts(node, self._strings)
        return None if None in parts else "".join(part or "" for part in parts)

    def of(self, node: ast.expr) -> FrozenSet[_ModuleRef]:
        """The modules ``node`` may denote; empty when it denotes none that is known."""
        if isinstance(node, ast.Name):
            return frozenset(self._bound.get(node.id, ()))
        if isinstance(node, ast.Attribute):
            return frozenset(
                reached for ref in self.of(node.value) for reached in _attribute(ref, node.attr)
            )
        if isinstance(node, ast.Call) and node.args:
            callee = self.of(node.func)
            if "importlib.import_module" in callee:
                return self._named(node.args[0])
        return frozenset()

    def _named(self, node: ast.expr) -> FrozenSet[_ModuleRef]:
        """The module a statically spelled ``import_module`` argument names."""
        text = self.text(node)
        return frozenset({text}) if text is not None else frozenset()


def _string_parts(node: ast.expr, strings: Mapping[str, str]) -> List[Optional[str]]:
    """The pieces of the string ``node`` spells, None for each piece known only at run time."""
    if isinstance(node, ast.Constant):
        return [node.value if isinstance(node.value, str) else None]
    if isinstance(node, ast.Name):
        return [strings.get(node.id)]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _string_parts(node.left, strings) + _string_parts(node.right, strings)
    if isinstance(node, ast.JoinedStr):
        parts: List[Optional[str]] = []
        for value in node.values:
            if isinstance(value, ast.FormattedValue):
                plain = value.conversion == -1 and value.format_spec is None
                parts.extend(_string_parts(value.value, strings) if plain else [None])
            else:
                parts.extend(_string_parts(value, strings))
        return parts
    return [None]


def _string_names(nodes: Sequence[ast.AST]) -> Dict[str, str]:
    """Names in a module walk bound exactly once to a statically spelled string."""
    bindings: Dict[str, int] = {}
    for node in nodes:
        if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            names = [node.id]
        elif isinstance(node, ast.arg):
            names = [node.arg]
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [(alias.asname or alias.name).split(".")[0] for alias in node.names]
        else:
            continue
        for name in names:
            bindings[name] = bindings.get(name, 0) + 1
    values = {
        target.id: node.value
        for node in nodes
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None
        for target in _targets(node)
        if isinstance(target, ast.Name) and bindings.get(target.id) == 1
    }
    strings: Dict[str, str] = {}
    for _ in range(3):  # a name spelled from a name spelled from literals
        for name, value in values.items():
            parts = _string_parts(value, strings)
            if name not in strings and None not in parts:
                strings[name] = "".join(part or "" for part in parts)
    return strings


def _climbed(directory: Path, steps: int) -> Path:
    for _ in range(steps):
        directory = directory.parent
    return directory


def _module_files(base: Path) -> FrozenSet[Path]:
    """The files a module at ``base`` (a path without suffix) may be, resolved."""
    return frozenset(
        {base.with_name(base.name + ".py").resolve(), (base / "__init__.py").resolve()}
    )


def _attribute(ref: _ModuleRef, attribute: str) -> FrozenSet[_ModuleRef]:
    """What ``module.attribute`` may denote as a module: a submodule of a package."""
    if isinstance(ref, str):
        return frozenset({f"{ref}.{attribute}"})
    if ref.name == "__init__.py":
        return _module_files(ref.parent / attribute)
    return frozenset()


class _WriteScanner(ast.NodeVisitor):
    """Collects the writes one file makes into module namespaces, its own included."""

    def __init__(self, path: Path, tree: ast.Module, shown: str) -> None:
        self._shown = shown
        self._references = _References(path, tuple(ast.walk(tree)))
        self.by_path: Dict[Path, List[NamespaceWrite]] = {}
        self.by_name: Dict[str, List[NamespaceWrite]] = {}

    # -- stores -------------------------------------------------------------

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._store(target)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._store(node.target)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self._store(node.target)
        self.generic_visit(node)

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            self._store(target)
        self.generic_visit(node)

    def _store(self, target: ast.expr) -> None:
        """Direct module attribute stores and deletes, including unpacking."""
        if isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                self._store(element)
        elif isinstance(target, ast.Starred):
            self._store(target.value)
        elif isinstance(target, ast.Attribute):
            self._record(self._references.of(target.value), [target.attr], target)

    def _record(
        self,
        modules: Iterable[_ModuleRef],
        names: Sequence[str],
        node: ast.AST,
    ) -> None:
        site = f"{self._shown}:{getattr(node, 'lineno', 0)}"
        writes = [NamespaceWrite(name, site) for name in names]
        for module in modules:
            if isinstance(module, Path):
                self.by_path.setdefault(module, []).extend(writes)
            else:
                self.by_name.setdefault(module, []).extend(writes)


# -- what a module's own statements bind --------------------------------------

_TREES: BoundedCache[str, Optional[ast.Module]] = BoundedCache(128)


def _parsed(source: str) -> Optional[ast.Module]:
    if source in _TREES:
        return _TREES[source]
    try:
        tree: Optional[ast.Module] = ast.parse(source)
    except (SyntaxError, ValueError):
        tree = None
    return _TREES.put(source, tree)


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return None


def _module_scope_statements(body: Sequence[ast.stmt]) -> Iterable[ast.stmt]:
    """The statements that run in the module's own scope: not those of a function or class body."""
    for statement in body:
        yield statement
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for _field, value in ast.iter_fields(statement):
            children = value if isinstance(value, list) else [value]
            for child in children:
                if isinstance(child, ast.stmt):
                    yield from _module_scope_statements([child])
                elif isinstance(child, (ast.ExceptHandler, ast.match_case)):
                    yield from _module_scope_statements(child.body)


def star_imports(tree: ast.Module) -> List[ast.ImportFrom]:
    """Every ``from ... import *`` of the module's own scope, however conditional."""
    return [
        statement
        for statement in _module_scope_statements(tree.body)
        if isinstance(statement, ast.ImportFrom)
        and any(alias.name == "*" for alias in statement.names)
    ]


def _star_sources(importer: Path, statement: ast.ImportFrom, root: Path) -> Tuple[Path, ...]:
    """The project files ``from ... import *`` in ``importer`` may read.

    A relative import names one; an absolute one may be found below any
    directory from the importer's own up to the project root.
    """
    parts = statement.module.split(".") if statement.module else []
    if statement.level:
        bases: List[Path] = [_climbed(importer.parent, statement.level - 1)]
    else:
        bases = [
            directory
            for directory in (importer.parent, *importer.parent.parents)
            if directory.is_relative_to(root)
        ]
    found: Dict[Path, None] = {}
    for base in bases:
        target = base.joinpath(*parts)
        candidates = _module_files(target) if parts else {(target / "__init__.py").resolve()}
        for candidate in sorted(candidates):
            if candidate.is_file():
                found[candidate] = None
    return tuple(found)


_NO_ALL = frozenset({"*no __all__*"})
"""What :func:`_declared_all` answers for a module that binds no ``__all__``."""


def _declared_all(tree: ast.Module) -> Optional[FrozenSet[str]]:
    """``__all__`` when the module binds it once, to a literal list or tuple of strings.

    :data:`_NO_ALL` when the module never mentions it as a target; None when
    it builds, extends or rebinds it any other way, or declares it ``global``.
    """
    stores = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and node.id == "__all__"
        and not isinstance(node.ctx, ast.Load)
    ]
    if any(
        (isinstance(node, ast.Attribute) and _is_all(node.value))
        or (isinstance(node, ast.Global) and "__all__" in node.names)
        for node in ast.walk(tree)
    ):
        return None
    if not stores:
        return _NO_ALL
    assignments = [
        statement
        for statement in _module_scope_statements(tree.body)
        if isinstance(statement, (ast.Assign, ast.AnnAssign))
        and any(target in stores for target in _targets(statement))
    ]
    if len(stores) != 1 or len(assignments) != 1 or assignments[0].value is None:
        return None
    return _string_literals(assignments[0].value)


def _is_all(node: ast.expr) -> bool:
    return isinstance(node, ast.Name) and node.id == "__all__"


def _targets(statement: Union[ast.Assign, ast.AnnAssign]) -> List[ast.expr]:
    return list(statement.targets) if isinstance(statement, ast.Assign) else [statement.target]


def _string_literals(node: ast.expr) -> Optional[FrozenSet[str]]:
    """The strings of a literal list or tuple of strings; None for anything else."""
    if not isinstance(node, (ast.List, ast.Tuple)):
        return None
    values = [element.value for element in node.elts if isinstance(element, ast.Constant)]
    if len(values) != len(node.elts) or not all(isinstance(value, str) for value in values):
        return None
    return frozenset(str(value) for value in values)


def _exports(
    module: Path,
    root: Path,
    reading: FrozenSet[Path],
) -> Optional[FrozenSet[str]]:
    """The names ``from module import *`` binds; None when any name may be among them.

    A literal ``__all__`` says; without one, every name of the module's own
    scope not starting with an underscore, every name a function declares
    ``global``, and what its own star imports bind. A module that does not
    parse, builds ``__all__`` dynamically or takes part in a star-import
    cycle may bind anything. Reflective writes are not included.
    """
    source = _read(module)
    tree = _parsed(source) if source is not None else None
    table = global_bindings(source) if source is not None else None
    if tree is None or table is None or module in reading:
        return None
    declared = _declared_all(tree)
    if declared is not _NO_ALL:
        return declared
    scanner = _WriteScanner(module, tree, str(module))
    scanner.visit(tree)
    written = {write.name for write in scanner.by_path.get(module, ())}
    written.update(
        write.name for name in module_names(module, root) for write in scanner.by_name.get(name, ())
    )
    names = {name for name in table.bindings if not name.startswith("_")}
    names |= table.rebound_by_global | written
    for statement in star_imports(tree):
        reached = star_bindings(module, statement, root, reading | {module})
        if reached is None:
            return None
        names |= reached
    return frozenset(names)


def star_bindings(
    importer: Path,
    statement: ast.ImportFrom,
    root: Path,
    reading: FrozenSet[Path] = frozenset(),
) -> Optional[FrozenSet[str]]:
    """The names a star import binds in ``importer``; None when it may bind any name.

    A source outside the project, or one not found, may bind anything.
    ``reading`` holds the modules whose exports are being read, so a star
    import reaching one of them closes a cycle.
    """
    sources = _star_sources(importer, statement, root)
    if not sources:
        return None
    names: Set[str] = set()
    for source in sources:
        exported = _exports(source, root, reading)
        if exported is None:
            return None
        names |= exported
    return frozenset(names)


def _spelled(statement: ast.ImportFrom) -> str:
    return "." * statement.level + (statement.module or "")


# -- the question -------------------------------------------------------------


def builtin_rebinding(
    module: Path, source: str, names: AbstractSet[str], project: ProjectWrites
) -> Dict[str, str]:
    """Each builtin of ``names`` that ``module`` may hold in its namespace, with why.

    ``module`` is the module's resolved path below ``project.root`` and
    ``source`` its text as the pair under evaluation read it. Empty when the
    module can hold none of them.
    """
    shown = _shown(module, project.root)
    found: Dict[str, str] = {}

    def note(hit: Iterable[str], why: str) -> None:
        for name in sorted(hit):
            found.setdefault(name, why)

    table = global_bindings(source)
    tree = _parsed(source)
    if table is None or tree is None:
        note(names, f"{shown} does not parse")
        return found
    if "__builtins__" in table.bindings or "__builtins__" in table.rebound_by_global:
        note(names, f"{shown} rebinds __builtins__")
    for name in sorted(names):
        if name in table.bindings:
            note([name], f"{name}: {shown} binds it")
        if name in table.rebound_by_global:
            note([name], f"{name}: a function of {shown} declares it global")
    for statement in star_imports(tree):
        reached = star_bindings(module, statement, project.root)
        hit = sorted(names) if reached is None else sorted(names & reached)
        for name in hit:
            note([name], f"{name}: {shown} imports * from {_spelled(statement)}, which may bind it")
    for write in project.into(module):
        if write.name == ANY_NAME:
            note(names, f"{write.site} may write any name into {shown}")
        elif write.name in names:
            note([write.name], f"{write.name}: {write.site} writes it into {shown}")
    return found
