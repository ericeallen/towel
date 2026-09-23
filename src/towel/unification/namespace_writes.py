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

"""Where a program may bind a builtin's name in one of its own modules.

A bare name in a function is looked up in the function's module, then in the
builtins. A helper one module borrows from another reads a builtin in the
host's namespace where the block it replaced read the borrower's, so the two
are the same lookup only while neither module holds the name. No helper takes
a builtin as a parameter, since a call such as ``helper(rows, len)`` would
surprise every reader; a pair whose moved code reads a builtin one of its
modules may hold is declined instead. This module gathers the evidence that a
module may hold one:

- its own scope binds the name, however conditionally, by a ``def``,
  ``class``, import or assignment; a function declares it ``global``; one of
  its star imports reaches it; or it rebinds ``__builtins__``, where every
  builtin lookup of its functions then goes;
- code writes into its namespace at run time: ``globals()[...] = ...``, or
  ``vars()`` and ``locals()`` so used at its top level; a method that writes
  that dictionary (``globals().update(...)``); ``globals()`` handed to other
  code; ``setattr(sys.modules[__name__], ...)``; ``exec`` or ``eval`` of code
  whose names land there;
- the project's own code, its tests included, patches the name into it:
  ``mock.patch("pkg.mod.open", ...)`` however ``patch`` is reached, with or
  without ``create=True``; ``patch.object(mod, "open", ...)``,
  ``patch.multiple`` and ``patch.dict`` of ``mod.__dict__``; pytest's
  ``monkeypatch.setattr`` in either form and ``monkeypatch.setitem`` of
  ``mod.__dict__``; ``setattr(mod, "open", ...)``; and ``mod.open = ...``.

Code refers to a module by a dotted name (a patch target, an absolute import,
``importlib.import_module``, ``sys.modules[...]``) or by its file (a relative
import, ``sys.modules[__name__]``). A dotted name reaches a module when it is
one of the names the module's path gives it below the project root
(``src.pkg.mod``, ``pkg.mod``, ``mod``), and these include every name the
program's own imports can give it. A name is read through literals,
f-strings, ``+`` and names bound once to such a string (``MODULE =
"pkg.mod"``); a patch target whose module part is computed at run time,
``"pkg." + name + ".open"``, counts for every module, since it may name any.
A target computed whole, and a module object reached other than by an
import, ``importlib.import_module``, ``getattr`` with a spelled name or
``sys.modules`` (a fixture's return value), are not followed, and what code
outside the project does is not seen at all.
"""

from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import AbstractSet, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Set
from typing import Tuple, Union

from ..consumers import MAXIMUM_FILES, SKIPPED_DIRECTORIES
from .bounded_cache import BoundedCache
from .builtins import BUILTIN_NAMES
from .module_bindings import global_bindings

ANY_NAME = "*"
"""The name of a write that may bind any name: ``setattr(mod, name, value)``, ``exec(code)``."""

ANY_MODULE = "*"
"""The dotted name of a write into a module computed at run time: ``patch(prefix + ".open")``."""


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
    """Every write into a module's namespace that the project's own files make."""

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
        for name in (*module_names(module, self.root), ANY_MODULE):
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

# A file that names none of these cannot write into a module's namespace by
# any form the scan recognizes, so it is not parsed. An attribute store counts
# only where the attribute is a builtin's name, followed by an assignment, an
# augmented one, an annotation, or the comma of an unpacking target.
_MAY_WRITE = re.compile(
    r"patch|setattr|delattr|setitem|delitem|__dict__|\bvars\b|\bglobals\b|\blocals\b"
    r"|\bexec\b|\beval\b|\bmodules\b|import_module"
    r"|\.\s*(?:"
    + "|".join(sorted((re.escape(name) for name in BUILTIN_NAMES if name.isidentifier())))
    + r")\s*(?:(?:[-+*/%@&|^]|//|\*\*|<<|>>)?=(?!=)|[:,])"
    + r"|\bdel\b"
)


def scan_project_writes(root: Path) -> ProjectWrites:
    """The writes into module namespaces that the Python files under ``root`` make.

    The directories the consumer scan skips are skipped here too; stubs never
    run and are not read. Past the consumer scan's limit the project cannot
    be read whole, and the answer says so rather than claim no write exists.
    """
    project = root.resolve()
    by_path: Dict[Path, List[NamespaceWrite]] = {}
    by_name: Dict[str, List[NamespaceWrite]] = {}
    count = 0
    for parent, directories, files in os.walk(project, onerror=lambda _: None):
        directories[:] = sorted(name for name in directories if name not in SKIPPED_DIRECTORIES)
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            count += 1
            if count > MAXIMUM_FILES:
                return ProjectWrites(project, {}, {}, complete=False)
            path = Path(parent, name)
            scanned = _file_writes(path, project)
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


def _file_writes(path: Path, root: Path) -> Optional[_FileWrites]:
    """What ``path`` writes into module namespaces; None when it cannot run or writes nothing."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if not _MAY_WRITE.search(data.decode("utf-8", errors="replace")):
        return None
    try:
        tree = ast.parse(data, filename=str(path))
    except (SyntaxError, ValueError):
        return None  # It cannot run, so it writes nothing.
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

# Methods of a namespace dictionary that only read it.
_READING_METHODS = frozenset(
    {"get", "keys", "values", "items", "copy", "__contains__", "__getitem__", "__len__", "__iter__"}
)
# Methods of a namespace dictionary that write it.
_WRITING_METHODS = frozenset(
    {"update", "setdefault", "__setitem__", "__ior__", "pop", "popitem", "clear", "__delitem__"}
)
# Builtins that only read a dictionary handed to them.
_READING_CALLEES = frozenset(
    {"len", "list", "tuple", "set", "frozenset", "sorted", "iter", "reversed", "dict", "repr"}
    | {"str", "print", "id", "type", "isinstance", "bool", "any", "all", "next", "sum"}
)
# Callees that take a dotted target to patch: ``mock.patch``, ``monkeypatch.setattr``.
_PATCHING_CALLEES = frozenset({"patch", "setattr", "delattr"})


class _References:
    """Which expressions of one file denote a module, and which spell a string, found statically.

    Every import binds, whatever scope it is in, and so does a plain
    assignment of a module reference (``mod = importlib.import_module(...)``,
    ``me = sys.modules[__name__]``): a name bound to a module anywhere may be
    one wherever it is read, which can only add evidence. A string is read
    through literals, f-strings and ``+``, and through a name the file binds
    once, to such a string (``MODULE = "pkg.mod"``).
    """

    def __init__(self, path: Path, tree: ast.Module) -> None:
        self._path = path
        self._strings = _string_names(tree)
        self._bound: Dict[str, Set[_ModuleRef]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        self._bind(alias.asname, {alias.name})
                    else:
                        head = alias.name.split(".")[0]
                        self._bind(head, {head})
            elif isinstance(node, ast.ImportFrom):
                self._bind_import_from(node)
        for node in ast.walk(tree):
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

    def tail(self, node: ast.expr) -> str:
        """The known end of the string ``node`` spells, after its last part computed at run time."""
        parts = _string_parts(node, self._strings)
        known: List[str] = []
        for part in reversed(parts):
            if part is None:
                break
            known.append(part)
        return "".join(reversed(known))

    def of(self, node: ast.expr) -> FrozenSet[_ModuleRef]:
        """The modules ``node`` may denote; empty when it denotes none that is known."""
        if isinstance(node, ast.Name):
            return frozenset(self._bound.get(node.id, ()))
        if isinstance(node, ast.Attribute):
            return frozenset(
                reached for ref in self.of(node.value) for reached in _attribute(ref, node.attr)
            )
        if isinstance(node, ast.Subscript) and "sys.modules" in self.of(node.value):
            return self._named(node.slice)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
        ):
            attribute = self.text(node.args[1])
            if attribute is None or not attribute.isidentifier():
                return frozenset()
            return frozenset(
                reached for ref in self.of(node.args[0]) for reached in _attribute(ref, attribute)
            )
        if isinstance(node, ast.Call) and node.args:
            callee = self.of(node.func)
            if "importlib.import_module" in callee:
                return self._named(node.args[0])
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and "sys.modules" in self.of(node.func.value)
            ):
                return self._named(node.args[0])
        return frozenset()

    def _named(self, node: ast.expr) -> FrozenSet[_ModuleRef]:
        """The module a ``sys.modules`` key or an ``import_module`` argument names."""
        text = self.text(node)
        if text is not None:
            return frozenset({text})
        if isinstance(node, ast.Name) and node.id == "__name__":
            return frozenset({self._path})
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "name"
            and isinstance(node.value, ast.Name)
            and node.value.id == "__spec__"
        ):
            return frozenset({self._path})
        return frozenset()


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


def _string_names(tree: ast.Module) -> Dict[str, str]:
    """The names ``tree`` binds exactly once, by an assignment of a string it spells statically."""
    bindings: Dict[str, int] = {}
    for node in ast.walk(tree):
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
        for node in ast.walk(tree)
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
        self._path = path
        self._shown = shown
        self._references = _References(path, tree)
        self._parents: Dict[ast.AST, ast.AST] = {
            child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
        }
        self._nesting = 0
        self.by_path: Dict[Path, List[NamespaceWrite]] = {}
        self.by_name: Dict[str, List[NamespaceWrite]] = {}

    # -- scopes: ``vars()`` and ``locals()`` are the module's only at its top level

    def _nested(self, node: ast.AST) -> None:
        self._nesting += 1
        self.generic_visit(node)
        self._nesting -= 1

    visit_FunctionDef = visit_AsyncFunctionDef = visit_Lambda = visit_ClassDef = _nested

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
        """``mod.open = ...`` and ``mod.__dict__["open"] = ...``, inside unpacking too."""
        if isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                self._store(element)
        elif isinstance(target, ast.Starred):
            self._store(target.value)
        elif isinstance(target, ast.Attribute):
            self._record(self._references.of(target.value), [target.attr], target)
        elif isinstance(target, ast.Subscript):
            self._record(self._namespaces(target.value), [_name_of(target.slice)], target)

    # -- calls --------------------------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        callee = _callee(node.func)
        first = node.args[0] if node.args else None
        text = self._references.text(first) if first is not None else None
        if text is not None:
            self._dotted_target(node, callee, text)
        elif first is not None:
            if callee in _PATCHING_CALLEES:
                # A target whose module part is computed (``"pkg." + name + ".open"``)
                # may name any module; the attribute it spells is written there.
                tail = self._references.tail(first)
                attribute = tail.rpartition(".")[2]
                if "." in tail and attribute.isidentifier():
                    self._record({ANY_MODULE}, [attribute], node)
            modules = self._references.of(first)
            if callee in {"object", "setattr", "delattr"}:
                second = node.args[1] if len(node.args) > 1 else None
                self._record(modules, [_name_of(second)], node)
            elif callee == "multiple":
                self._record(modules, _keyword_names(node.keywords), node)
            if callee in {"setitem", "delitem", "dict"}:
                self._record(
                    self._namespaces(first), _dictionary_names(node.args[1:], node.keywords), node
                )
        if isinstance(node.func, ast.Attribute) and node.func.attr in _WRITING_METHODS:
            self._record(self._namespaces(node.func.value), _method_names(node), node)
        if callee in {"exec", "eval"} and isinstance(node.func, ast.Name):
            # The code runs in the namespace given, else in the caller's
            # globals, which a ``global`` statement in it writes.
            namespaces = (
                self._namespaces(node.args[1]) if len(node.args) > 1 else frozenset({self._path})
            )
            self._record(namespaces, [ANY_NAME], node)
        if self._own_namespace(node) and self._handed_on(node):
            self._record({self._path}, [ANY_NAME], node)
        self.generic_visit(node)

    def _dotted_target(self, node: ast.Call, callee: Optional[str], target: str) -> None:
        """A string naming a module attribute: ``patch("pkg.mod.open")``, ``setattr("pkg.mod.open", v)``."""
        if callee == "multiple":
            self._record({target}, _keyword_names(node.keywords), node)
        module, _, attribute = target.rpartition(".")
        if not module:
            return
        if attribute == "__dict__":
            self._record({module}, _dictionary_names(node.args[1:], node.keywords), node)
        elif attribute.isidentifier():
            self._record({module}, [attribute], node)

    def _own_namespace(self, node: ast.expr) -> bool:
        """Whether ``node`` is this module's namespace: ``globals()``, or ``vars()``/``locals()`` at its top."""
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            return False
        if node.args or node.keywords:
            return False
        return node.func.id == "globals" or (
            node.func.id in {"vars", "locals"} and self._nesting == 0
        )

    def _namespaces(self, node: ast.expr) -> FrozenSet[_ModuleRef]:
        """The modules whose namespace dictionary ``node`` is."""
        if self._own_namespace(node):
            return frozenset({self._path})
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "vars"
            and len(node.args) == 1
        ):
            return self._references.of(node.args[0])
        if isinstance(node, ast.Attribute) and node.attr == "__dict__":
            return self._references.of(node.value)
        return frozenset()

    def _handed_on(self, namespace: ast.Call) -> bool:
        """Whether this module's namespace dictionary reaches code that may write it.

        Subscripting it, testing membership, calling a method of it, looping
        over it or giving it to a builtin that only reads are not; a store or
        a writing method is recorded where it happens. Anything else, an
        assignment, an argument, a return, passes the dictionary on.
        """
        parent = self._parents.get(namespace)
        if isinstance(parent, (ast.Subscript, ast.Compare)):
            return False
        if isinstance(parent, ast.Attribute):
            return parent.attr not in _READING_METHODS | _WRITING_METHODS
        if isinstance(parent, (ast.For, ast.AsyncFor, ast.comprehension)):
            return parent.iter is not namespace
        if isinstance(parent, ast.Call) and namespace in parent.args:
            return _callee(parent.func) not in _READING_CALLEES
        return True

    def _record(self, modules: Iterable[_ModuleRef], names: Sequence[str], node: ast.AST) -> None:
        writes = [
            NamespaceWrite(name, f"{self._shown}:{getattr(node, 'lineno', 0)}") for name in names
        ]
        for module in modules:
            if isinstance(module, Path):
                self.by_path.setdefault(module, []).extend(writes)
            else:
                self.by_name.setdefault(module, []).extend(writes)


def _callee(func: ast.expr) -> Optional[str]:
    """The last identifier of a callee: ``patch`` of ``mock.patch``, ``object`` of ``patch.object``."""
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _name_of(node: Optional[ast.expr]) -> str:
    """The name a key or attribute argument spells, or :data:`ANY_NAME`."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.isidentifier():
        return node.value
    return ANY_NAME


def _keyword_names(keywords: Sequence[ast.keyword]) -> List[str]:
    return [keyword.arg or ANY_NAME for keyword in keywords]


def _dictionary_names(arguments: Sequence[ast.expr], keywords: Sequence[ast.keyword]) -> List[str]:
    """The keys a ``setitem``, ``patch.dict`` or ``update`` puts: a key, a literal's keys, keywords."""
    names = _keyword_names(keywords)
    if not arguments:
        return names
    first = arguments[0]
    if isinstance(first, ast.Dict):
        names.extend(_name_of(key) if key is not None else ANY_NAME for key in first.keys)
    else:
        names.append(_name_of(first))
    return names


def _method_names(call: ast.Call) -> List[str]:
    """The keys a namespace dictionary's writing method may put or remove."""
    assert isinstance(call.func, ast.Attribute)
    if call.func.attr in {"update", "__ior__"}:
        return _dictionary_names(call.args, call.keywords)
    if call.func.attr in {"popitem", "clear"}:
        return [ANY_NAME]
    return [_name_of(call.args[0] if call.args else None)]


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


def _star_imports(tree: ast.Module) -> List[ast.ImportFrom]:
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


def _exports(module: Path, root: Path, reading: FrozenSet[Path]) -> Optional[FrozenSet[str]]:
    """The names ``from module import *`` binds; None when any name may be among them.

    A literal ``__all__`` says; without one, every name of the module's own
    scope not starting with an underscore, every name a function declares
    ``global``, and what its own star imports bind. A module that does not
    parse, builds ``__all__``, writes its namespace at run time, or takes
    part in a star-import cycle may bind anything.
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
    if ANY_NAME in written:
        return None
    names = {name for name in table.bindings if not name.startswith("_")}
    names |= table.rebound_by_global | written
    for statement in _star_imports(tree):
        reached = _star_bindings(module, statement, root, reading | {module})
        if reached is None:
            return None
        names |= reached
    return frozenset(names)


def _star_bindings(
    importer: Path, statement: ast.ImportFrom, root: Path, reading: FrozenSet[Path]
) -> Optional[FrozenSet[str]]:
    """The names a star import binds in ``importer``; None when it may bind any name.

    A source outside the project, or one not found, may bind anything.
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
) -> Optional[str]:
    """Why ``module`` may hold one of the builtins ``names`` in its namespace; None when it cannot.

    ``module`` is the module's resolved path below ``project.root`` and
    ``source`` its text as the pair under evaluation read it.
    """
    shown = _shown(module, project.root)
    table = global_bindings(source)
    tree = _parsed(source)
    if table is None or tree is None:
        return f"{shown} does not parse"
    if "__builtins__" in table.bindings or "__builtins__" in table.rebound_by_global:
        return f"{shown} rebinds __builtins__"
    for name in sorted(names):
        if name in table.bindings:
            return f"{name}: {shown} binds it"
        if name in table.rebound_by_global:
            return f"{name}: a function of {shown} declares it global"
    for statement in _star_imports(tree):
        reached = _star_bindings(module, statement, project.root, frozenset())
        hit = sorted(names) if reached is None else sorted(names & reached)
        if hit:
            return f"{hit[0]}: {shown} imports * from {_spelled(statement)}, which may bind it"
    for write in project.into(module):
        if write.name == ANY_NAME:
            return f"{write.site} may write any name into {shown}"
        if write.name in names:
            return f"{write.name}: {write.site} writes it into {shown}"
    return None
