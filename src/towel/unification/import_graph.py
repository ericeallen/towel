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

"""Resolving a project's import graph, for the cycle guard and helper placement.

Which files an import statement reaches, which module defines an imported name,
whether a new import would close a static cycle, and whether running a
module's top level can run code of its own (``ImportTimeCode``). What an
import names is the program's own answer, the import model's
(``ProgramImports``; docs/DECISIONS.md, "Import names come from the
program"), never one derived from packaging metadata or from where a copy
of the tree happens to sit. Results are cached per run in an
``ImportGraphCache`` the engine owns.
"""

from __future__ import annotations

import ast
import builtins
import re
import sys
import warnings
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import (
    Dict,
    FrozenSet,
    Iterable,
    Iterator,
    List,
    Literal,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
)

from .bounded_cache import BoundedCache, memoizing
from .exceptions import ProjectScanLimitError
from ..import_model import NameStatus
from ..declared_requirements import installed_with_the_project, normalized_name
from ..project_layout import find_project_root
from .module_bindings import (
    NAMESPACE_PRESERVING_DECORATORS,
    ModuleBindings,
    dotted_name,
    global_bindings,
    import_origin,
)
from .program_imports import ProgramImports, program_imports
from .statement_facts import (
    bindings_of,
    imported_binding_name,
)
from ..source_text import read_source


@dataclass(frozen=True)
class _Edges:
    """The project files one module's imports may execute, and whether some may run unseen."""

    files: FrozenSet[Path]
    unseen: bool
    """Some import enters a directory the model did not read (``ProgramImports.reached``)."""


class ImportGraphCache:
    """What one run has learned about the project's import graph.

    A cross-file pair asks whether hosting a helper would close an import
    cycle, and answering re-reads every reachable module unless the edges are
    remembered (Sphinx: 243 modules per pair). Edges and import bindings are
    keyed by path, modification time, and size, so a rewritten file is
    re-read. Every table is bounded, and the engine owns one instance per
    run; a caller with no engine makes its own.

    What an import names comes from the program's imports
    (:meth:`program_for`): the model of the project a path belongs to, read
    the first time a question needs it, less the directories
    ``excluded_names`` names. During a run that project is the one the stage
    copies (:meth:`begin_run`).
    """

    def __init__(self, limit: int = 8192, *, excluded_names: Iterable[str] = ()) -> None:
        self.excluded_names = frozenset(excluded_names)
        self._stage: Optional[Tuple[Path, Path]] = None
        self._programs: Dict[Path, ProgramImports] = {}
        self._unreadable: Dict[Path, ProjectScanLimitError] = {}
        self._declared: Dict[Path, FrozenSet[str]] = {}
        # The project root of each resolved path asked about (``project_root``).
        self._roots: Dict[Path, Path] = {}
        self.edges: BoundedCache[Tuple[Path, int, int, str], Optional[_Edges]] = BoundedCache(limit)
        self.bindings: BoundedCache[Tuple[Path, int, int], Optional[Dict[str, Tuple[str, ...]]]] = (
            BoundedCache(limit)
        )
        # Whether a module runs code at import, keyed like the edges.
        self.effects: BoundedCache[Tuple[Path, int, int], bool] = BoundedCache(limit)
        # Whether subclassing a module's class runs only Python's class
        # machinery, keyed like the edges plus the class's name.
        self.quiet_classes: BoundedCache[Tuple[Path, int, int, str], bool] = BoundedCache(limit)
        # The top-level names a module imports unconditionally, keyed like the edges.
        self.required_imports: BoundedCache[Tuple[Path, int, int], FrozenSet[str]] = BoundedCache(
            limit
        )
        # Resolving a path walks the filesystem; the class-hierarchy lookup
        # resolves every class's file per base-class reference.
        self.resolved_paths: BoundedCache[str, Path] = BoundedCache(limit)

    def resolve(self, path: str) -> Path:
        """``Path(path).resolve()``, once per spelling for the life of the cache."""
        resolved = self.resolved_paths.get(path)
        if resolved is None:
            resolved = self.resolved_paths.put(path, Path(path).resolve())
        return resolved

    def begin_run(
        self, origin_root: Optional[Path] = None, stage_root: Optional[Path] = None
    ) -> None:
        """Forget every model and what was learned under it; a run starts.

        With ``stage_root`` the run refactors a copy of the project at
        ``origin_root``, and a path in either is answered by that project's
        model, read from the project itself.
        """
        self._stage = (
            None
            if origin_root is None or stage_root is None
            else (origin_root.resolve(), stage_root.resolve())
        )
        self._programs = {}
        self._unreadable = {}
        self._declared = {}
        self._roots = {}
        for table in (self.edges, self.effects, self.quiet_classes):
            table.clear()

    def project_root(self, resolved: Path) -> Path:
        """``find_project_root(resolved)``, resolved, found once per path until the next run.

        Every cross-module pair asks for each of its files, and so does every
        call site's coverage question. The root depends only on the project's
        layout, and a run adds and removes no ``pyproject.toml``, ``setup.cfg``,
        ``setup.py`` or ``__init__.py``. Under ``memoization_disabled`` it keeps
        nothing.
        """
        if not memoizing():
            return find_project_root(resolved).resolve()
        known = self._roots.get(resolved)
        if known is None:
            known = self._roots[resolved] = find_project_root(resolved).resolve()
        return known

    def installed_with_the_project(self, path: Path) -> FrozenSet[str]:
        """``installed_with_the_project`` for the project holding ``path``, read once per run.

        Every cross-module pair asks, and the answer is what the project's
        configuration files say, which a run does not change. Under
        ``memoization_disabled`` it keeps nothing.
        """
        root = self.project_root(path.resolve())
        if not memoizing():
            return installed_with_the_project(root)
        known = self._declared.get(root)
        if known is None:
            known = self._declared[root] = installed_with_the_project(root)
        return known

    def program_for(self, path: Path) -> ProgramImports:
        """The program imports of the project ``path`` belongs to, read on first use.

        Raises ``ProjectScanLimitError`` for a project too large to read whole,
        each time it is asked, having tried once.
        """
        resolved = path.resolve()
        stage = self._stage
        if stage is not None and any(
            resolved == root or resolved.is_relative_to(root) for root in stage
        ):
            root, stage_root = stage
        else:
            root, stage_root = self.project_root(resolved), None
        known = self._programs.get(root)
        if known is not None:
            return known
        failure = self._unreadable.get(root)
        if failure is not None:
            raise failure
        try:
            known = program_imports(root, self.excluded_names, stage_root=stage_root)
        except ProjectScanLimitError as error:
            self._unreadable[root] = error
            raise
        self._programs[root] = known
        return known


def relative_import_levels(nodes: Iterable[ast.AST]) -> FrozenSet[int]:
    """The level of every relative import anywhere in ``nodes``, nested functions included."""
    return frozenset(
        node.level
        for statement in nodes
        for node in ast.walk(statement)
        if isinstance(node, ast.ImportFrom) and node.level
    )


def relative_imports_resolve_alike(files: Iterable[str], levels: Iterable[int]) -> bool:
    """Whether a relative import of each of ``levels`` names one module from every one of ``files``.

    A relative import resolves in the package of the module whose code runs
    it: ``from .sub import VAL`` in ``pkg/x/a.py`` reads ``pkg.x.sub``, and
    the same statement moved into a helper that ``pkg/y/b.py`` calls still
    reads ``pkg.x.sub`` for it, where the block read ``pkg.y.sub``. That
    package is the module's directory, climbed once per dot after the
    first, so the files agree exactly when those directories coincide;
    where the climb leaves the top-level package, the import raises there
    and nowhere else.
    """
    directories = {Path(path).resolve().parent for path in files}
    for level in levels:
        bases = set()
        for directory in directories:
            for _ in range(level - 1):
                directory = directory.parent
            bases.add(directory)
        if len(bases) > 1:
            return False
    return True


# Which import statements of a module count: every one (the cycle guard,
# which must not miss an edge), the ones its top level can run as it is
# imported (in any branch, but not in a function body), or only those its top
# level runs on every path (statements of the module body itself).
ImportExtent = Literal["everywhere", "at_import", "unconditionally"]


_TYPE_CHECKING = frozenset({"typing.TYPE_CHECKING", "typing_extensions.TYPE_CHECKING"})


class TypeCheckingGuards:
    """Which ``if`` tests of one module are ``TYPE_CHECKING``, false wherever the module runs.

    A body under ``if TYPE_CHECKING:`` is for a checker, which reads it, and
    never runs: Towel writes imports there on exactly that premise, and every
    question about what a module runs, imports or requires answers it the same
    way, through this one test. A test is such a guard when its name, resolved
    by the module's own bindings, is ``typing.TYPE_CHECKING`` or
    ``typing_extensions.TYPE_CHECKING`` however it is imported or aliased, or
    the module's own ``TYPE_CHECKING = False``. It is resolved where the
    statement holding the test runs when that is known, and otherwise only
    when every binding the module ever gives the name is one of those, which
    also covers ``try: from typing import TYPE_CHECKING`` with an ``except``
    that binds ``False``. Anything else runs: a name a function or class scope
    binds for itself (a local ``TYPE_CHECKING = True``), a name a function
    declares ``global``, a module with a star import, an expression other
    than the name (``not TYPE_CHECKING``), and an unresolvable one. Only the
    guarded body is skipped; its ``else`` runs.
    """

    def __init__(self, tree: ast.Module, bindings: Optional[ModuleBindings]) -> None:
        self._tree = tree
        self._bindings = bindings

    @classmethod
    def of(cls, source: str, tree: ast.Module) -> "TypeCheckingGuards":
        """The guards of the module ``tree`` parsed from ``source``."""
        return cls(tree, global_bindings(source))

    def never_true(
        self, test: ast.expr, shadowed: FrozenSet[str] = frozenset(), order: Optional[int] = None
    ) -> bool:
        """Whether ``test`` is a guard, where the names ``shadowed`` are the enclosing scopes' own.

        ``order`` is the index of the top-level statement the test runs in,
        when it runs as the module is imported; None for a test in a
        function body, which runs when the module's bindings are all made.
        """
        dotted = dotted_name(test)
        if dotted is None or self._bindings is None or dotted.split(".")[0] in shadowed:
            return False
        if order is not None and self._in_effect(dotted, order):
            return True
        return self._always_guard(dotted, 0)

    def _in_effect(self, dotted: str, order: int) -> bool:
        """Whether ``dotted`` is a guard by the binding certainly in effect at ``order``."""
        assert self._bindings is not None
        if self._bindings.resolve(dotted, order) in _TYPE_CHECKING:
            return True
        if "." in dotted:
            return False
        binding = self._bindings.in_effect(dotted, order)
        if binding is None or len(self._bindings.bindings.get(dotted, ())) != 1:
            return False
        return _binds_false(self._tree.body[binding.order], dotted)

    def _always_guard(self, dotted: str, depth: int) -> bool:
        """Whether every binding the module's own scope ever gives ``dotted``'s head makes it one."""
        assert self._bindings is not None
        head, _, rest = dotted.partition(".")
        bindings = self._bindings
        if (
            depth > 8
            or bindings.star_imports
            or head in bindings.rebound_by_global
            or not bindings.bindings.get(head)
        ):
            return False
        sources = list(_module_scope_bindings(self._tree, head))
        if not sources:
            return False
        for statement, alias in sources:
            if alias is not None and isinstance(statement, (ast.Import, ast.ImportFrom)):
                origin = import_origin(statement, alias)
                if origin is None or (f"{origin}.{rest}" if rest else origin) not in _TYPE_CHECKING:
                    return False
            elif not rest and _binds_false(statement, head):
                continue
            elif (
                isinstance(statement, ast.Assign)
                and all(isinstance(target, ast.Name) for target in statement.targets)
                and (aliased := dotted_name(statement.value)) is not None
            ):
                if not self._always_guard(f"{aliased}.{rest}" if rest else aliased, depth + 1):
                    return False
            else:
                return False
        return True


def _binds_false(statement: ast.AST, name: str) -> bool:
    """``name = False`` or ``name: bool = False``, and nothing else."""
    if isinstance(statement, ast.Assign):
        targets, value = statement.targets, statement.value
    elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
        targets, value = [statement.target], statement.value
    else:
        return False
    return (
        len(targets) == 1
        and isinstance(targets[0], ast.Name)
        and targets[0].id == name
        and isinstance(value, ast.Constant)
        and value.value is False
    )


def _module_scope_bindings(
    tree: ast.Module, name: str
) -> Iterator[Tuple[ast.AST, Optional[ast.alias]]]:
    """Each construct of the module's own scope that binds ``name``: the statement, and the alias.

    An import yields its alias; anything else binding the name yields the
    statement holding it (an assignment) or the binding node itself (a loop
    target, a definition), which callers treat as unknown.
    """
    pending: List[Tuple[ast.AST, Optional[ast.stmt]]] = [(node, None) for node in tree.body]
    while pending:
        node, statement = pending.pop()
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if imported_binding_name(alias) == name:
                    yield node, alias
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == name:
                yield node, None
            # Decorators, defaults and bases run in this scope; the body does not.
            pending.extend((child, None) for child in _header_nodes(node))
            continue
        if isinstance(node, ast.Lambda):
            pending.extend((default, statement) for default in node.args.defaults)
            continue
        if isinstance(node, ast.Name):
            if node.id == name and isinstance(node.ctx, (ast.Store, ast.Del)):
                yield (statement if statement is not None else node), None
            continue
        if isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name == name:
            yield node, None
        if isinstance(node, ast.MatchMapping) and node.rest == name:
            yield node, None
        if isinstance(node, ast.comprehension):
            pending.extend((child, statement) for child in (node.iter, *node.ifs))
            continue
        owner = node if isinstance(node, (ast.Assign, ast.AnnAssign)) else statement
        pending.extend((child, owner) for child in ast.iter_child_nodes(node))


def _header_nodes(
    node: Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef],
) -> List[ast.AST]:
    """What a definition evaluates in the scope that runs it."""
    if isinstance(node, ast.ClassDef):
        return [*node.decorator_list, *node.bases, *(keyword.value for keyword in node.keywords)]
    arguments = node.args
    return [
        *node.decorator_list,
        *arguments.defaults,
        *(default for default in arguments.kw_defaults if default is not None),
    ]


def _function_scope_names(function: Union[ast.FunctionDef, ast.AsyncFunctionDef]) -> FrozenSet[str]:
    """The names a function's body resolves in its own scope, not the module's."""
    arguments = function.args
    names = {
        argument.arg
        for argument in (
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
            *((arguments.vararg,) if arguments.vararg else ()),
            *((arguments.kwarg,) if arguments.kwarg else ()),
        )
    }
    for statement in function.body:
        names |= bindings_of(statement, into_nested_scopes=False)
        # A declaration hands the name to another scope, which this cannot resolve.
        names |= {
            name
            for node in ast.walk(statement)
            if isinstance(node, (ast.Global, ast.Nonlocal))
            for name in node.names
        }
    return frozenset(names)


def _class_scope_names(klass: ast.ClassDef) -> FrozenSet[str]:
    """The names a class body binds, which its own statements resolve before the module's."""
    return frozenset(
        name
        for statement in klass.body
        for name in bindings_of(statement, into_nested_scopes=False)
    )


def _import_statements(
    tree: ast.Module, extent: ImportExtent, guards: TypeCheckingGuards
) -> List[Union[ast.Import, ast.ImportFrom]]:
    """The import statements of ``tree`` that ``extent`` counts.

    None under a ``TYPE_CHECKING`` guard (``TypeCheckingGuards``) counts:
    such an import never runs, in any body.
    """
    if extent == "unconditionally":
        return [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    found: List[Union[ast.Import, ast.ImportFrom]] = []

    def visit(
        statements: Sequence[ast.stmt],
        outer: FrozenSet[str],
        own: FrozenSet[str],
        order: Optional[int],
    ) -> None:
        """``outer`` names the enclosing functions bind, ``own`` those of a class body here.

        ``order`` is the top-level statement these run in as the module is
        imported, or None inside a function body.
        """
        for position, statement in enumerate(statements):
            where = position if statements is tree.body else order
            if isinstance(statement, (ast.Import, ast.ImportFrom)):
                found.append(statement)
            elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # A function body runs only when it is called.
                if extent == "everywhere":
                    visit(
                        statement.body, outer | _function_scope_names(statement), frozenset(), None
                    )
            elif isinstance(statement, ast.ClassDef):
                visit(statement.body, outer, _class_scope_names(statement), where)
            elif isinstance(statement, ast.If) and guards.never_true(
                statement.test, outer | own, where
            ):
                visit(statement.orelse, outer, own, where)
            else:
                for field in ("body", "orelse", "finalbody"):
                    visit(getattr(statement, field, []), outer, own, where)
                for clause in (
                    *getattr(statement, "handlers", []),
                    *getattr(statement, "cases", []),
                ):
                    visit(clause.body, outer, own, where)

    visit(tree.body, frozenset(), frozenset(), None)
    return found


def _import_edges(
    current: Path,
    program: ProgramImports,
    cache: ImportGraphCache,
    extent: ImportExtent = "everywhere",
) -> Optional[_Edges]:
    """What the imports of ``current``, a module in the model's paths, may execute.

    The module is read where the run keeps it (``ProgramImports.in_run``),
    so an import an earlier extraction added is an edge like any other, and
    each import is resolved as the program's import model resolves it
    (``ImportModel.files_reached``): package initializers on the way
    included, and every candidate location of an ambiguous name. None when
    the module cannot be read.
    """
    path = program.in_run(current)
    try:
        stat = path.stat()
    except OSError:
        return None
    key = (path, stat.st_mtime_ns, stat.st_size, extent)
    if key in cache.edges:
        return cache.edges.get(key)
    try:
        source = read_source(path)
        tree = ast.parse(source)
    except (OSError, UnicodeError, SyntaxError):
        return cache.edges.put(key, None)
    files: Set[Path] = set()
    unseen = False
    for node in _import_statements(tree, extent, TypeCheckingGuards.of(source, tree)):
        for level, module, names in _import_requests(node):
            reached = program.reached(current, level, module, names)
            if reached is None:
                unseen = True
            else:
                files.update(reached)
    return cache.edges.put(key, _Edges(frozenset(files), unseen))


def _import_requests(
    node: Union[ast.Import, ast.ImportFrom],
) -> Iterator[Tuple[int, Optional[str], Tuple[str, ...]]]:
    """``(level, module, names)`` for each module one import statement imports.

    ``from . import name`` runs the package's ``__init__`` whether ``name``
    is a submodule or an attribute defined there, which the model counts:
    a helper hosted in a submodule and imported by that ``__init__``, which
    the submodule imports back, was missed that way (beautifulsoup4's tests
    package).
    """
    if isinstance(node, ast.Import):
        for alias in node.names:
            yield 0, alias.name, ()
        return
    yield node.level, node.module, tuple(alias.name for alias in node.names if alias.name != "*")


def _module_level_import_bindings(
    current: Path, cache: ImportGraphCache
) -> Optional[Dict[str, Tuple[str, ...]]]:
    """Names bound by unconditional module-level imports of ``current``.

    Each name maps to the dotted path it denotes, with relative modules
    written as ``.``-prefixed paths (``from .base import X`` binds ``X`` to
    ``.base.X``). Imports inside ``try``, ``if``, or functions are left out,
    so a name they bind resolves to nothing rather than to a guess. None
    when the module cannot be parsed.
    """
    try:
        stat = current.stat()
    except OSError:
        return None
    key = (current, stat.st_mtime_ns, stat.st_size)
    if key in cache.bindings:
        return cache.bindings.get(key)
    # A bare parse: this runs on the import-graph walk, which reaches modules
    # outside the analysis through the graph cache alone and has no engine,
    # so it cannot share the engine's parse memo. The bindings are remembered
    # per (path, mtime, size) above, so each file version is parsed once.
    try:
        tree = ast.parse(read_source(current))
    except (OSError, UnicodeError, SyntaxError):
        return cache.bindings.put(key, None)
    bindings: Dict[str, Tuple[str, ...]] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = tuple(alias.name.split("."))
                if alias.asname is not None:
                    bindings[alias.asname] = parts
                else:
                    # ``import a.b.c`` binds ``a``; the full path stays
                    # reachable as ``a.b.c`` and is matched by prefix.
                    bindings.setdefault(parts[0], parts[:1])
                    bindings[alias.name] = parts
        elif isinstance(node, ast.ImportFrom):
            prefix: Tuple[str, ...] = ("." * node.level,) if node.level else ()
            module = tuple(node.module.split(".")) if node.module else ()
            for alias in node.names:
                bound = imported_binding_name(alias)
                if bound is not None:
                    bindings[bound] = (*prefix, *module, alias.name)
    return cache.bindings.put(key, bindings)


def _module_definition_file(base: Path, parts: Tuple[str, ...]) -> Optional[Path]:
    """The file whose top level is the module ``parts`` under ``base``, if it exists."""
    cursor = base.joinpath(*parts) if parts else base
    initializer = cursor / "__init__.py"
    if initializer.is_file():
        return initializer.resolve()
    module = cursor.with_suffix(".py")
    if parts and module.is_file():
        return module.resolve()
    return None


def _definition_file(location: Path, inner: Sequence[str]) -> Optional[Path]:
    """The file whose top level is module ``inner`` below a top-level name's ``location``."""
    if location.suffix == ".py":
        return None if inner else location  # A module file has no submodules.
    return _module_definition_file(location, tuple(inner))


def imported_definition_sites(
    current_file: str, dotted_name: str, cache: ImportGraphCache
) -> Optional[FrozenSet[Tuple[Path, str]]]:
    """Where a name used in ``current_file`` may be defined, as (file, qualname) pairs.

    ``dotted_name`` is written as it appears in the module (``Base``,
    ``mod.Base``, ``pkg.mod.Outer.Inner``). Its longest dotted prefix bound
    by a module-level import names the module; the remainder is the
    qualified name inside it. Because ``from m import x`` may denote a
    submodule or an attribute, each split of the path into module and
    qualname whose module file exists is a candidate. A relative import is
    resolved from the file's place; an absolute one through the location
    the program's imports give its top-level name, and one of no module of
    the project names no site. None when the name is not bound by an
    unconditional module-level import, the module cannot be parsed, or the
    absolute name cannot be followed: the program's imports leave it
    ambiguous, or the project is too large to read them.
    """
    current = Path(current_file).resolve()
    bindings = _module_level_import_bindings(current, cache)
    if bindings is None:
        return None
    parts = dotted_name.split(".")
    bound: Optional[Tuple[Tuple[str, ...], Tuple[str, ...]]] = None
    for length in range(len(parts), 0, -1):
        target = bindings.get(".".join(parts[:length]))
        if target is not None:
            bound = (target, tuple(parts[length:]))
            break
    if bound is None:
        return None
    target, remainder = bound
    full = (*target, *remainder)
    # ``target`` may end in an attribute rather than a module, so every
    # split at or after the bound module's own length is tried.
    splits = range(max(1, len(target) - 1), len(full))
    if set(full[0]) == {"."}:
        base = current.parent
        for _ in range(len(full[0]) - 1):
            base = base.parent
        return frozenset(
            (file, ".".join(full[split:]))
            for split in splits
            if (file := _module_definition_file(base, full[1:split])) is not None
        )
    try:
        program = cache.program_for(current)
    except ProjectScanLimitError:
        return None
    info = program.model.names.get(full[0])
    if info is None:
        return None
    if info.status is NameStatus.EXTERNAL:
        return frozenset()  # An import of no module of the project names none of its files.
    location = info.location
    if not info.trusted or location is None:
        return None
    return frozenset(
        (program.in_run(file), ".".join(full[split:]))
        for split in splits
        if (file := _definition_file(location, full[1:split])) is not None
    )


def _reachable_modules(
    start: Iterable[Path],
    program: ProgramImports,
    cache: ImportGraphCache,
    extent: ImportExtent,
    *,
    sees_all: bool,
) -> Optional[Set[Path]]:
    """Every project module importing ``start`` may run, ``start`` included, in the model's paths.

    ``extent`` says which of each module's imports count (``ImportExtent``):
    ``"unconditionally"`` gives the modules importing ``start`` certainly
    loads, ``"at_import"`` those it may. None when some module cannot be
    read, or, with ``sees_all``, when some import may run what the model did
    not read. Without it such an import is taken to run nothing, which only
    ever leaves modules out.
    """
    pending = list(start)
    visited: Set[Path] = set()
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        edges = _import_edges(current, program, cache, extent)
        if edges is None or (sees_all and edges.unseen):
            return None
        pending.extend(edges.files - visited)
    return visited


# -- What running a module's top level can do ------------------------------
#
# One question serves helper hosting and helper insertion alike: can this
# top-level statement, as its module is imported, run code other than
# Python's own? A decorator, a default value, an evaluated annotation, a base
# class's metaclass or ``__init_subclass__``, an attribute access, an
# operator, and any call can run project code, and all count, unless what
# runs is one of the few callables below, resolved through the module's own
# imports, whose effect is Python's alone and reaches nothing outside what it
# builds. Each is checked in tests/test_import_time_code.py.

# Function decorators that wrap or mark the function and register it nowhere.
# ``typing.overload`` is absent: it records each overload in a registry that
# ``typing.clear_overloads()`` empties, so an earlier import is observable.
_QUIET_FUNCTION_DECORATORS = frozenset(
    {
        "abc.abstractmethod",
        "builtins.classmethod",
        "builtins.property",
        "builtins.staticmethod",
        "contextlib.asynccontextmanager",
        "contextlib.contextmanager",
        "functools.cache",
        "functools.cached_property",
        "functools.lru_cache",
        "typing.final",
        "typing.no_type_check",
        "typing.override",
        "typing_extensions.final",
        "typing_extensions.override",
    }
)

# Class decorators that return the class built from its own namespace.
_QUIET_CLASS_DECORATORS = NAMESPACE_PRESERVING_DECORATORS | {
    "typing.runtime_checkable",
    "typing_extensions.runtime_checkable",
}

# Constructors that only store their arguments in the object they return, so
# names may be passed as well as constants. ``logging.getLogger`` is absent:
# it registers the logger with the manager, and a ``dictConfig`` or
# ``fileConfig`` that runs later disables every logger existing by then, so
# creating one earlier silences it.
_QUIET_CONSTRUCTORS = frozenset(
    {
        "builtins.classmethod",
        "builtins.property",
        "builtins.staticmethod",
        "dataclasses.field",
        "typing.NewType",
        "typing.ParamSpec",
        "typing.TypeVar",
        "typing.TypeVarTuple",
        "typing_extensions.NewType",
        "typing_extensions.ParamSpec",
        "typing_extensions.TypeVar",
        "typing_extensions.TypeVarTuple",
    }
)

# Builtin constructors, quiet over constants only: handed a name they would
# iterate, hash or convert an object whose methods may be the project's.
_QUIET_BUILTIN_CONSTRUCTORS = frozenset(
    {"bool", "bytes", "dict", "float", "frozenset", "int", "list", "object", "set", "str", "tuple"}
)

# Classes whose subclass creation runs only their own metaclass and
# ``__init_subclass__``, which build the new class and register it nowhere.
_QUIET_BASES = frozenset(
    {
        "abc.ABC",
        "typing.NamedTuple",
        "typing.Protocol",
        "typing_extensions.Protocol",
    }
)
_QUIET_SUBSCRIPTED_BASES = frozenset(
    {"typing.Generic", "typing.Protocol", "typing_extensions.Protocol"}
)
_QUIET_METACLASSES = frozenset({"abc.ABCMeta", "builtins.type"})

# Class machinery known to leave a plain function of the class body in place,
# unwrapped and unregistered, while it builds the class; each is checked in
# tests/test_method_host_machinery.py. ``type`` and ``ABCMeta`` keep the
# namespace as given, ``Generic`` and ``object`` define the only
# ``__init_subclass__`` their subclasses run, and the enum metaclass makes
# members of the body's other values but never of a function. ``Protocol``
# is absent: a function in a protocol's body becomes one of its members.
_ENUM_BASES = frozenset(
    {"enum.Enum", "enum.Flag", "enum.IntEnum", "enum.IntFlag", "enum.ReprEnum", "enum.StrEnum"}
)
_ENUM_METACLASSES = frozenset({"enum.EnumMeta", "enum.EnumType"})

_BUILTIN_CLASSES = frozenset(
    f"builtins.{name}" for name, value in vars(builtins).items() if isinstance(value, type)
)

# The builtin classes whose instances are not looked up by ``object``'s own
# ``__getattribute__``: an instance of ``type`` is a class, looked up through
# its metaclass first, and ``super`` answers for the next class on an order.
# Every other builtin class runs that generic lookup; before CPython 3.14 many
# carry a ``__getattribute__`` slot of their own that wraps it
# (tests/test_method_host_machinery.py checks both against the interpreter).
_OWN_ATTRIBUTE_LOOKUP = frozenset({"builtins.type", "builtins.super"})


@dataclass(frozen=True)
class _ClassMachinery:
    """The bases, metaclasses and class members one judgment of a class takes as Python's own.

    ``builtin_bases`` are the builtin classes it accepts as bases, and
    ``forbidden_members`` the names no class on the order it accepts may bind.
    """

    label: str
    bases: FrozenSet[str]
    subscripted_bases: FrozenSet[str]
    metaclasses: FrozenSet[str]
    builtin_bases: FrozenSet[str] = _BUILTIN_CLASSES
    forbidden_members: FrozenSet[str] = frozenset({"__init_subclass__"})


# Subclassing runs only Python's class machinery: nothing of the project runs.
_RUNS_NO_CODE = _ClassMachinery(
    "runs-no-code", _QUIET_BASES, _QUIET_SUBSCRIPTED_BASES, _QUIET_METACLASSES
)
# A method helper stays what its class's methods reach: building the class
# leaves every plain function of its body a plain member, and looking up
# ``self.__extracted_func_0`` is Python's own, so that no class on the order
# defines ``__getattribute__``, which intercepts every lookup and, in a proxy,
# answers it from another object. ``__getattr__`` runs only when normal lookup
# fails, and nothing spells the helper's stored name, so it is allowed.
_HOSTS_METHOD_HELPERS = _ClassMachinery(
    "hosts-method-helpers",
    frozenset({"abc.ABC"}) | _ENUM_BASES,
    frozenset({"typing.Generic"}),
    _QUIET_METACLASSES | _ENUM_METACLASSES,
    builtin_bases=_BUILTIN_CLASSES - _OWN_ATTRIBUTE_LOOKUP,
    forbidden_members=frozenset({"__init_subclass__", "__getattribute__"}),
)

# Where the names an evaluated annotation subscripts may come from.
_ANNOTATION_MODULES = frozenset({"collections.abc", "typing", "typing_extensions"})
_BUILTIN_GENERICS = frozenset({"dict", "frozenset", "list", "set", "tuple", "type"})

# Facts of the running interpreter a module may branch on at import.
_PLATFORM_FACTS = frozenset({"os.name", "sys.byteorder", "sys.platform", "sys.version_info"})


@dataclass(frozen=True)
class _ClassBody:
    """What a class body has bound so far, which shadows the module's names after it.

    ``functions`` are the plain functions among them, and ``properties`` the
    ones a ``@property`` (or a setter of one) made properties.
    """

    names: FrozenSet[str] = frozenset()
    functions: FrozenSet[str] = frozenset()
    properties: FrozenSet[str] = frozenset()

    def after(self, statement: ast.stmt, *, is_property: bool = False) -> "_ClassBody":
        """The body's names once ``statement`` has run."""
        bound = _statement_binds(statement)
        plain = (
            isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
            and not statement.decorator_list
        )
        return _ClassBody(
            names=self.names | bound,
            functions=(self.functions - bound) | (bound if plain else frozenset()),
            properties=(self.properties - bound) | (bound if is_property else frozenset()),
        )


class ImportTimeCode:
    """Which top-level statements of one module can run code other than Python's own on import.

    Names resolve the way the module resolves them where each statement runs
    (``ModuleBindings``): a builtin only when the module can never bind its
    name, anything else only through a binding certainly in effect there.
    A base class is quiet when subclassing it runs only Python's class
    machinery: a builtin, one of ``_QUIET_BASES``, or a class of the project
    whose decorators keep it, whose metaclass is ``type`` or ``ABCMeta``,
    which defines no ``__init_subclass__``, and whose own bases are quiet; one
    imported from another module is followed there when ``path`` and
    ``cache`` locate it. A body guarded by a resolved ``TYPE_CHECKING`` never
    runs, nor one guarded by ``__name__ == "__main__"`` when the module is
    imported rather than run.
    """

    def __init__(
        self,
        source: str,
        *,
        path: Optional[Path] = None,
        cache: Optional[ImportGraphCache] = None,
        depth: int = 0,
    ) -> None:
        self._tree = ast.parse(source)
        self._bindings = global_bindings(source)
        self._guards = TypeCheckingGuards(self._tree, self._bindings)
        self._path = path
        self._cache = cache
        self._depth = depth
        self._postponed = any(
            isinstance(statement, ast.ImportFrom)
            and statement.module == "__future__"
            and any(alias.name == "annotations" for alias in statement.names)
            for statement in self._tree.body
        )

    @property
    def tree(self) -> ast.Module:
        return self._tree

    def statements(self) -> Tuple[bool, ...]:
        """For each top-level statement, in order, whether running it can run code."""
        return tuple(
            self._statement(statement, order, None)
            for order, statement in enumerate(self._tree.body)
        )

    # -- statements -----------------------------------------------------------

    def _statement(self, statement: ast.stmt, order: int, body: Optional[_ClassBody]) -> bool:
        """Whether ``statement``, running where top-level statement ``order`` does, can run code.

        ``body`` is the enclosing class body's state, or None at module level.
        """
        if isinstance(statement, (ast.Import, ast.ImportFrom, ast.Pass)):
            return False
        if isinstance(statement, ast.Expr):
            return self._expression(statement.value, order, body)
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return self._function(statement, order, body)
        if isinstance(statement, ast.ClassDef):
            return self._class(statement, order, body)
        if isinstance(statement, ast.Assign):
            return not all(
                isinstance(target, ast.Name) for target in statement.targets
            ) or self._assigned(statement.value, order, body)
        if isinstance(statement, ast.AnnAssign):
            return (
                not isinstance(statement.target, ast.Name)
                or self._annotation(statement.annotation, order, body)
                or (statement.value is not None and self._assigned(statement.value, order, body))
            )
        if isinstance(statement, ast.If):
            return self._conditional(statement, order, body)
        if isinstance(statement, ast.Try):
            handlers = [handler.type for handler in statement.handlers if handler.type]
            nested = statement.body + statement.orelse + statement.finalbody
            nested += [inner for handler in statement.handlers for inner in handler.body]
            return any(self._expression(handler, order, body) for handler in handlers) or any(
                self._statement(inner, order, body) for inner in nested
            )
        return True

    def _function(
        self,
        function: Union[ast.FunctionDef, ast.AsyncFunctionDef],
        order: int,
        body: Optional[_ClassBody],
    ) -> bool:
        arguments = function.args
        defaults = [*arguments.defaults, *filter(None, arguments.kw_defaults)]
        annotations = [
            argument.annotation
            for argument in (
                *arguments.posonlyargs,
                *arguments.args,
                *arguments.kwonlyargs,
                *filter(None, (arguments.vararg, arguments.kwarg)),
            )
            if argument.annotation is not None
        ]
        if function.returns is not None:
            annotations.append(function.returns)
        return (
            any(
                not self._quiet_decorator(decorator, order, body, _QUIET_FUNCTION_DECORATORS)
                for decorator in function.decorator_list
            )
            or any(self._expression(default, order, body) for default in defaults)
            or any(self._annotation(annotation, order, body) for annotation in annotations)
        )

    def _class(self, node: ast.ClassDef, order: int, body: Optional[_ClassBody]) -> bool:
        if body is not None and (node.bases or node.keywords or node.decorator_list):
            return True  # A nested class's bases would resolve in the enclosing body.
        if any(
            not self._quiet_decorator(decorator, order, body, _QUIET_CLASS_DECORATORS)
            for decorator in node.decorator_list
        ):
            return True
        if not self._quiet_keywords(node.keywords, order) or not all(
            self._quiet_base(base, order) for base in node.bases
        ):
            return True
        inner_body = _ClassBody()
        for inner in node.body:
            if self._statement(inner, order, inner_body):
                return True
            inner_body = inner_body.after(
                inner, is_property=self._makes_property(inner, order, inner_body)
            )
        return False

    def _makes_property(self, statement: ast.stmt, order: int, body: _ClassBody) -> bool:
        if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return False
        return any(
            self._origin(decorator, order, body) == "builtins.property"
            or self._is_property_accessor(decorator, body)
            for decorator in statement.decorator_list
        )

    def _conditional(self, statement: ast.If, order: int, body: Optional[_ClassBody]) -> bool:
        test = statement.test
        if self._is_type_checking(test, order, body) or (body is None and _is_main_guard(test)):
            # Never true where the module is imported: only the ``else`` runs.
            return any(self._statement(inner, order, body) for inner in statement.orelse)
        if not self._quiet_test(test, order, body):
            return True
        return any(
            self._statement(inner, order, body) for inner in statement.body + statement.orelse
        )

    # -- expressions ----------------------------------------------------------

    def _assigned(self, value: ast.expr, order: int, body: Optional[_ClassBody]) -> bool:
        """Whether binding ``value`` can run code; in a class body, ``__set_name__`` included.

        The class statement calls ``__set_name__`` on every object its
        namespace holds that has one, so a class attribute that merely names
        an object needs that object to be one without it.
        """
        if body is not None and isinstance(value, ast.Name):
            return not self._lacks_set_name(value.id, order, body)
        return self._expression(value, order, body)

    def _expression(self, node: ast.expr, order: int, body: Optional[_ClassBody]) -> bool:
        """Whether evaluating ``node`` can run code."""
        if isinstance(node, (ast.Constant, ast.Name)):
            return False
        if isinstance(node, (ast.Tuple, ast.List)):
            return any(self._expression(item, order, body) for item in node.elts)
        if isinstance(node, (ast.Set, ast.UnaryOp, ast.BinOp)):
            return not _is_constant(node)
        if isinstance(node, ast.Dict):
            return not all(key is not None and _is_constant(key) for key in node.keys) or any(
                self._expression(value, order, body) for value in node.values
            )
        if isinstance(node, (ast.Compare, ast.BoolOp)):
            return not self._quiet_test(node, order, body)
        if isinstance(node, ast.Lambda):
            defaults = [*node.args.defaults, *filter(None, node.args.kw_defaults)]
            return any(self._expression(default, order, body) for default in defaults)
        if isinstance(node, ast.Call):
            return not self._quiet_call(node, order, body)
        return True

    def _annotation(self, node: ast.expr, order: int, body: Optional[_ClassBody]) -> bool:
        """Whether an annotation, evaluated where it is written, can run code."""
        if self._postponed or isinstance(node, (ast.Constant, ast.Name)):
            return False
        if isinstance(node, ast.Subscript):
            origin = self._origin(node.value, order, body)
            generic = origin is not None and (
                origin.rpartition(".")[0] in _ANNOTATION_MODULES
                or origin in {f"builtins.{name}" for name in _BUILTIN_GENERICS}
            )
            parts = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
            return not generic or any(self._annotation(part, order, body) for part in parts)
        if isinstance(node, ast.List):
            # ``Callable[[int, str], bool]``'s parameters.
            return any(self._annotation(item, order, body) for item in node.elts)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            # ``X | None`` runs ``type.__or__``, which a metaclass may override.
            return not all(
                isinstance(operand, ast.Constant)
                or self._annotation_name(operand, order, body)
                or not self._annotation(operand, order, body)
                and isinstance(operand, (ast.Subscript, ast.BinOp))
                for operand in (node.left, node.right)
            )
        return not self._annotation_name(node, order, body)

    def _annotation_name(self, node: ast.expr, order: int, body: Optional[_ClassBody]) -> bool:
        """A typing name, a builtin class, or a class of the project with a quiet metaclass."""
        origin = self._origin(node, order, body)
        if origin is not None and (
            origin.rpartition(".")[0] in _ANNOTATION_MODULES or origin in _BUILTIN_CLASSES
        ):
            return True
        return isinstance(node, ast.Name) and body is None and self._quiet_base(node, order)

    def _quiet_test(self, test: ast.expr, order: int, body: Optional[_ClassBody]) -> bool:
        """A condition that compares facts of the interpreter with constants."""
        if isinstance(test, ast.BoolOp):
            return all(self._quiet_test(value, order, body) for value in test.values)
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
            return self._quiet_test(test.operand, order, body)
        if not isinstance(test, ast.Compare):
            return False
        return all(
            _is_constant(operand) or self._is_platform_fact(operand, order, body)
            for operand in (test.left, *test.comparators)
        )

    def _is_platform_fact(self, node: ast.expr, order: int, body: Optional[_ClassBody]) -> bool:
        if isinstance(node, ast.Subscript) and _is_constant(node.slice):
            node = node.value
        if isinstance(node, ast.Attribute) and node.attr in {"major", "minor", "micro"}:
            node = node.value
        return self._origin(node, order, body) in _PLATFORM_FACTS

    def _is_type_checking(self, test: ast.expr, order: int, body: Optional[_ClassBody]) -> bool:
        return self._guards.never_true(test, body.names if body is not None else frozenset(), order)

    # -- callables ------------------------------------------------------------

    def _quiet_decorator(
        self,
        decorator: ast.expr,
        order: int,
        body: Optional[_ClassBody],
        allowed: FrozenSet[str],
    ) -> bool:
        if body is not None and self._is_property_accessor(decorator, body):
            return True
        if isinstance(decorator, ast.Call):
            return (
                self._origin(decorator.func, order, body) in allowed
                and not decorator.args
                and all(_is_constant(keyword.value) for keyword in decorator.keywords)
            )
        return self._origin(decorator, order, body) in allowed

    @staticmethod
    def _is_property_accessor(decorator: ast.expr, body: _ClassBody) -> bool:
        """``@value.setter`` on a property this class body defined above."""
        return (
            isinstance(decorator, ast.Attribute)
            and decorator.attr in {"setter", "getter", "deleter"}
            and isinstance(decorator.value, ast.Name)
            and decorator.value.id in body.properties
        )

    def _quiet_keywords(
        self,
        keywords: Sequence[ast.keyword],
        order: int,
        machinery: _ClassMachinery = _RUNS_NO_CODE,
    ) -> bool:
        return all(
            keyword.arg == "metaclass"
            and self._origin(keyword.value, order, None) in machinery.metaclasses
            for keyword in keywords
        )

    def hosts_method_helpers(self, name: str) -> bool:
        """Whether a method helper placed in the top-level class ``name`` is what its methods reach.

        A helper placed in the body is seen by the class's metaclass, which
        builds the class from the namespace, and by the ``__init_subclass__``
        of every class on its method resolution order, its own included for
        each subclass: any of them may wrap it, register it, or drop it. And
        every call of it is an attribute lookup, which a ``__getattribute__``
        anywhere on that order intercepts: a forwarding proxy answers it from
        another object. So the metaclass must be ``type``, ``abc.ABCMeta`` or
        the enum metaclass, the class must bind neither ``__init_subclass__``
        nor ``__getattribute__``, and every base must be a builtin class other
        than ``type`` and ``super``, ``abc.ABC``, ``typing.Generic[...]``, an
        enum, or a class of the project that qualifies in turn, each resolved
        through the module's imports (``_HOSTS_METHOD_HELPERS``). The class's
        decorators are :meth:`ModuleBindings.keeps_namespace`'s question.
        """
        if self._bindings is None:
            return False
        order = self._bindings.class_orders.get(name)
        if order is None:
            return False
        node = self._tree.body[order]
        return (
            isinstance(node, ast.ClassDef)
            and node.name == name
            and self._quiet_keywords(node.keywords, order, _HOSTS_METHOD_HELPERS)
            and not _binds_any(node, _HOSTS_METHOD_HELPERS.forbidden_members)
            and all(self._quiet_base(base, order, _HOSTS_METHOD_HELPERS) for base in node.bases)
        )

    def _quiet_call(self, call: ast.Call, order: int, body: Optional[_ClassBody]) -> bool:
        origin = self._origin(call.func, order, body)
        if origin is None:
            return False
        if any(isinstance(argument, ast.Starred) for argument in call.args) or any(
            keyword.arg is None for keyword in call.keywords
        ):
            return False
        arguments = [*call.args, *(keyword.value for keyword in call.keywords)]
        if origin in _QUIET_CONSTRUCTORS:
            return all(self._stored(argument, order, body) for argument in arguments)
        if origin.startswith("builtins.") and origin[9:] in _QUIET_BUILTIN_CONSTRUCTORS:
            return all(_is_constant(argument) for argument in arguments)
        if origin == "re.compile":
            return self._quiet_pattern(call, order, body)
        return False

    def _stored(self, argument: ast.expr, order: int, body: Optional[_ClassBody]) -> bool:
        """An argument a quiet constructor may keep: it is evaluated, never called.

        A constant, a name, a lambda with quiet defaults, or the docstring of
        a plain function the class body defined, as ``property(eos,
        eos.__doc__)`` passes it.
        """
        if _is_constant(argument) or isinstance(argument, ast.Name):
            return True
        if isinstance(argument, ast.Lambda):
            return not self._expression(argument, order, body)
        return (
            body is not None
            and isinstance(argument, ast.Attribute)
            and argument.attr == "__doc__"
            and isinstance(argument.value, ast.Name)
            and argument.value.id in body.functions
        )

    def _quiet_pattern(self, call: ast.Call, order: int, body: Optional[_ClassBody]) -> bool:
        """``re.compile`` of a constant pattern that compiles here without a warning.

        Compiling a pattern runs only the regular expression compiler, but a
        pattern it rejects raises at import and one it doubts warns, so the
        pattern is compiled here, once, to see which it is.
        """
        if call.keywords or not 1 <= len(call.args) <= 2:
            return False
        pattern = self._constant_text(call.args[0], order, body)
        flags = self._regex_flags(call.args[1], order, body) if len(call.args) == 2 else 0
        if pattern is None or flags is None:
            return False
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                re.compile(pattern, flags)
            except (re.error, TypeError, ValueError, OverflowError, RecursionError):
                return False
        return not caught

    def _constant_text(
        self, node: ast.expr, order: int, body: Optional[_ClassBody]
    ) -> Optional[str]:
        """The string ``node`` always evaluates to: literals, ``+``, and the module's own constants."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = self._constant_text(node.left, order, body)
            right = self._constant_text(node.right, order, body)
            return None if left is None or right is None else left + right
        if isinstance(node, ast.Name) and body is None and self._bindings is not None:
            binding = self._bindings.in_effect(node.id, order)
            if binding is None or binding.origin is not None or binding.is_import:
                return None
            statement = self._tree.body[binding.order]
            if (
                isinstance(statement, ast.Assign)
                and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)
            ):
                return self._constant_text(statement.value, binding.order, None)
        return None

    def _regex_flags(self, node: ast.expr, order: int, body: Optional[_ClassBody]) -> Optional[int]:
        if isinstance(node, ast.Constant) and type(node.value) is int:
            return node.value
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            left = self._regex_flags(node.left, order, body)
            right = self._regex_flags(node.right, order, body)
            return None if left is None or right is None else left | right
        origin = self._origin(node, order, body)
        if origin is not None and origin.startswith("re."):
            flag = getattr(re.RegexFlag, origin[3:], None)
            return int(flag) if isinstance(flag, re.RegexFlag) else None
        return None

    # -- names ----------------------------------------------------------------

    def _origin(self, node: ast.expr, order: int, body: Optional[_ClassBody]) -> Optional[str]:
        """The absolute dotted name ``node`` denotes where statement ``order`` runs, if known."""
        dotted = dotted_name(node)
        if dotted is None or self._bindings is None:
            return None
        head = dotted.split(".")[0]
        if body is not None and head in body.names:
            return None
        if not self._bindings.may_bind(head):
            return f"builtins.{dotted}" if head in vars(builtins) else None
        return self._bindings.resolve(dotted, order)

    def _lacks_set_name(self, name: str, order: int, body: _ClassBody) -> bool:
        """Whether the object ``name`` holds in a class body certainly has no ``__set_name__``."""
        if name in body.names:
            return name in body.functions
        if self._bindings is None:
            return False
        if not self._bindings.may_bind(name):
            return name in vars(builtins)
        binding = self._bindings.in_effect(name, order)
        if binding is None or binding.is_import:
            return False
        statement = self._tree.body[binding.order]
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return not statement.decorator_list
        return isinstance(statement, ast.Assign) and _is_constant(statement.value)

    def _quiet_base(
        self, base: ast.expr, order: int, machinery: _ClassMachinery = _RUNS_NO_CODE
    ) -> bool:
        """Whether subclassing ``base`` runs only the class machinery ``machinery`` accepts."""
        if isinstance(base, ast.Subscript):
            return self._origin(
                base.value, order, None
            ) in machinery.subscripted_bases and not self._annotation(base.slice, order, None)
        origin = self._origin(base, order, None)
        if origin in machinery.bases or origin in machinery.builtin_bases:
            return True
        dotted = dotted_name(base)
        if dotted is None or self._bindings is None or self._depth > 8:
            return False
        binding = self._bindings.in_effect(dotted.split(".")[0], order)
        if binding is None:
            return False
        if binding.class_qualname is not None and binding.class_qualname == dotted:
            node = self._tree.body[binding.order]
            return isinstance(node, ast.ClassDef) and self._quiet_ancestor(
                node, binding.order, machinery
            )
        if binding.is_import:
            return self._quiet_imported_class(dotted, machinery)
        return False

    def _quiet_ancestor(
        self, node: ast.ClassDef, order: int, machinery: _ClassMachinery = _RUNS_NO_CODE
    ) -> bool:
        """A class of this module whose decorators, machinery and members ``machinery`` accepts."""
        return (
            all(
                self._origin(decorator, order, None) in NAMESPACE_PRESERVING_DECORATORS
                for decorator in node.decorator_list
            )
            and self._quiet_keywords(node.keywords, order, machinery)
            and not _binds_any(node, machinery.forbidden_members)
            and all(self._quiet_base(base, order, machinery) for base in node.bases)
        )

    def _quiet_imported_class(
        self, dotted: str, machinery: _ClassMachinery = _RUNS_NO_CODE
    ) -> bool:
        """An imported class of the project, judged in the module that defines it."""
        if self._path is None or self._cache is None:
            return False
        sites = imported_definition_sites(str(self._path), dotted, self._cache)
        if not sites or len(sites) != 1:
            return False
        ((module, qualname),) = sites
        if "." in qualname:
            return False
        try:
            stat = module.stat()
        except OSError:
            return False
        key = (module, stat.st_mtime_ns, stat.st_size, f"{qualname} {machinery.label}")
        known = self._cache.quiet_classes.get(key)
        if known is not None:
            return known
        try:
            other = ImportTimeCode(
                read_source(module), path=module, cache=self._cache, depth=self._depth + 1
            )
        except (OSError, UnicodeError, SyntaxError, ValueError):
            return self._cache.quiet_classes.put(key, False)
        return self._cache.quiet_classes.put(key, other._quiet_named_class(qualname, machinery))

    def _quiet_named_class(self, name: str, machinery: _ClassMachinery) -> bool:
        """Whether the class the module binds to ``name`` once it has run is quiet to subclass."""
        return self._quiet_base(ast.Name(id=name, ctx=ast.Load()), len(self._tree.body), machinery)


def _is_constant(node: ast.expr) -> bool:
    """An expression of literals whose evaluation runs only Python's own arithmetic."""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_is_constant(item) for item in node.elts)
    if isinstance(node, ast.Dict):
        return all(key is not None and _is_constant(key) for key in node.keys) and all(
            _is_constant(value) for value in node.values
        )
    if isinstance(node, ast.UnaryOp):
        return _is_constant(node.operand)
    if isinstance(node, ast.BinOp):
        return _is_constant(node.left) and _is_constant(node.right)
    return False


def _is_main_guard(test: ast.expr) -> bool:
    """A test false wherever the module is imported, before it evaluates anything else.

    ``__name__ == "__main__"`` either way round, or a conjunction that opens
    with one and so stops there: true only for the module Python runs as the
    program.
    """
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        return _is_main_guard(test.values[0])
    if not (
        isinstance(test, ast.Compare)
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Eq)
        and len(test.comparators) == 1
    ):
        return False
    sides = (test.left, test.comparators[0])
    return any(
        isinstance(name, ast.Name)
        and name.id == "__name__"
        and isinstance(value, ast.Constant)
        and value.value == "__main__"
        for name, value in (sides, sides[::-1])
    )


def module_scope_statements(tree: ast.Module) -> Iterator[ast.stmt]:
    """Every statement the module runs in its own scope, in order: into compound statements, not definitions."""
    pending: List[ast.stmt] = list(reversed(tree.body))
    while pending:
        statement = pending.pop()
        yield statement
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        nested: List[ast.stmt] = []
        for field in ("body", "orelse", "finalbody", "handlers", "cases"):
            for item in getattr(statement, field, ()):
                if isinstance(item, (ast.ExceptHandler, ast.match_case)):
                    nested.extend(item.body)
                elif isinstance(item, ast.stmt):
                    nested.append(item)
        pending.extend(reversed(nested))


def _binds_any(node: ast.ClassDef, names: FrozenSet[str]) -> bool:
    """Whether the class body binds one of ``names``, such as an ``__init_subclass__``.

    An ``__init_subclass__`` runs for every subclass as it is built; a
    ``__getattribute__`` answers every attribute lookup on an instance.
    """
    return any(names & _statement_binds(inner) for inner in node.body)


def _statement_binds(statement: ast.stmt) -> FrozenSet[str]:
    """The names a class-body statement binds in the class namespace."""
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return frozenset({statement.name})
    if isinstance(statement, (ast.Import, ast.ImportFrom)):
        return frozenset(
            name for alias in statement.names if (name := imported_binding_name(alias))
        )
    return frozenset(
        node.id
        for node in ast.walk(statement)
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del))
    )


def _has_import_time_effects(module: Path, cache: ImportGraphCache) -> bool:
    """Whether importing ``module`` can run code beyond Python's own (``ImportTimeCode``)."""
    try:
        stat = module.stat()
    except OSError:
        return True
    key = (module, stat.st_mtime_ns, stat.st_size)
    known = cache.effects.get(key)
    if known is not None:
        return known
    try:
        code = ImportTimeCode(read_source(module), path=module, cache=cache)
    except (OSError, UnicodeError, SyntaxError, ValueError):
        return cache.effects.put(key, True)
    return cache.effects.put(key, any(code.statements()))


class ImportChange(Enum):
    """What importing a helper's host would change about importing its borrower."""

    UNKNOWN = "the imports cannot be inspected"
    RUNS_CODE = "a module the new import loads runs code at import"
    NEW_REQUIREMENT = "a module the new import loads requires a package that may be absent"
    NEW_TOP_LEVEL_PACKAGE = (
        "a module the new import loads is in a package the borrower never imports"
    )
    RUN_BY_PATH = "the borrower runs as a script, where the new import would not resolve"


def import_runs_new_code(host_file: str, borrower_file: str, cache: ImportGraphCache) -> bool:
    """Whether ``borrower`` importing ``host`` would change what importing the borrower does."""
    return import_change(host_file, borrower_file, cache) is not None


def import_change(
    host_file: str, borrower_file: str, cache: ImportGraphCache
) -> Optional[ImportChange]:
    """What ``borrower`` importing ``host`` would change about importing the borrower, if anything.

    A cross-file helper adds an import of the host to the borrower, which
    runs the host and the initializers of the packages enclosing it. If the
    borrower's import already certainly loads the host, nothing new runs.
    Otherwise every
    module the new import may load that the borrower's does not certainly
    load already must run no code at import (``ImportTimeCode``), require no
    package that may be absent where the borrower is installed
    (``_new_requirements``), and belong to a top-level package the borrower
    already relies on (``_new_top_level_packages``). An import in a function
    body runs only when the function is called, so it makes nothing present
    at the borrower's import. What the new import runs must be seen whole:
    one that enters a directory the model did not read is unknown.
    """
    program = cache.program_for(Path(host_file))
    host = program.origin(Path(host_file))
    borrower = program.origin(Path(borrower_file))
    already = _reachable_modules(
        [borrower, *program.package_initializers(borrower)],
        program,
        cache,
        "unconditionally",
        sees_all=False,
    )
    if already is None:
        return ImportChange.UNKNOWN
    added: Set[Path] = set()
    if host not in already:
        loaded = _reachable_modules(
            [host, *program.package_initializers(host)], program, cache, "at_import", sees_all=True
        )
        if loaded is None:
            return ImportChange.UNKNOWN
        added = loaded - already
        if any(_has_import_time_effects(program.in_run(module), cache) for module in added):
            return ImportChange.RUNS_CODE
        if _new_requirements(added, already, program, cache):
            return ImportChange.NEW_REQUIREMENT
        if _new_top_level_packages(added, already, borrower, program):
            return ImportChange.NEW_TOP_LEVEL_PACKAGE
    if _breaks_run_by_path(borrower, added | {host}, program):
        return ImportChange.RUN_BY_PATH
    return None


def host_has_stub(host_file: str, cache: ImportGraphCache) -> bool:
    """Whether a type checker may read a stub in place of ``host_file`` when another module imports it.

    A checker resolves ``alpha.a`` to ``alpha/a.pyi`` when there is one, so a
    helper added to ``alpha/a.py`` is not there for it: ``from alpha.a import
    __extracted_func_0`` elsewhere is an unknown symbol to pyright and a
    missing attribute to mypy (audit k30). A ``.pyi`` beside the module
    counts, and so do a stub-only ``<package>-stubs`` directory beside its
    top-level package or at the project root, which a checker reads in
    place of the package once installed, and the module's stub under the
    project's ``typings`` directory, pyright's default stub path. They are
    looked for under every name a checker may give the module
    (:func:`_checker_names`): mypy resolves even a relative import to an
    absolute name and searches for that.
    """
    module = Path(host_file).resolve()
    if module.with_suffix(".pyi").is_file():
        return True
    program = cache.program_for(module)
    root = program.model.root
    for parts, base in _checker_names(program.origin(module), program):
        for directory in dict.fromkeys((base, root)):
            package = directory / f"{parts[0]}-stubs"
            if package.is_dir():
                # A partial stub package leaves the modules it omits to the
                # runtime package; any other hides them from the checker.
                if not _is_partial_stub_package(package) or _stub_file(package, parts[1:]):
                    return True
        if _stub_file(root / "typings", parts):
            return True
    return False


def _checker_names(module: Path, program: ProgramImports) -> List[Tuple[Tuple[str, ...], Path]]:
    """The dotted names a type checker may give ``module``, each with the directory holding its top.

    The name the program's imports give it, and the one the package it is
    imported as part of gives it, counted from the directory holding that
    package, as a checker run there names it even when no import in the
    program spells it: ``src/alpha/b.py`` is ``alpha.b`` to ``mypy -p alpha``
    run in ``src``.
    """
    names: List[Tuple[Tuple[str, ...], Path]] = []
    name = program.model.module_name(module)
    if name is not None:
        location = program.model.names[name.partition(".")[0]].location
        if location is not None:
            names.append((tuple(name.split(".")), location.parent))
    context = program.model.context_of(module)
    if context is not None:
        base = context.parent
        relative = module.relative_to(base).with_suffix("").parts
        parts = relative[:-1] if relative[-1] == "__init__" else relative
        if parts and all(part.isidentifier() for part in parts):
            names.append((tuple(parts), base))
    return list(dict.fromkeys(names))


def _is_partial_stub_package(package: Path) -> bool:
    """Whether a ``<package>-stubs`` directory declares itself partial in its ``py.typed`` (PEP 561)."""
    try:
        marker = (package / "py.typed").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    return "partial" in marker.split()


def _stub_file(base: Path, parts: Sequence[str]) -> bool:
    """Whether ``base`` holds the stub of the module ``parts`` names, as a file or a package."""
    stub = base.joinpath(*parts)
    return (bool(parts) and stub.with_suffix(".pyi").is_file()) or (stub / "__init__.pyi").is_file()


def runs_as_script(source: str, tree: ast.Module, path: Path) -> bool:
    """Whether the module is written to run as a program, and so may be run by its path.

    ``__main__.py`` is, and so is a module whose first line is a ``#!``
    interpreter line, guard or no guard, and one with a main guard anywhere
    in its own scope, however it is spelled (:func:`_is_main_guard`).
    """
    return (
        path.name == "__main__.py"
        or source.startswith("#!")
        or any(
            isinstance(statement, ast.If) and _is_main_guard(statement.test)
            for statement in module_scope_statements(tree)
        )
    )


def leading_imports(tree: ast.Module) -> List[Union[ast.Import, ast.ImportFrom]]:
    """The imports the module's top level runs on every path before its first definition.

    A helper's import goes after the last of them (``_find_import_position``),
    so they are the imports that run before it wherever the module runs.
    """
    found: List[Union[ast.Import, ast.ImportFrom]] = []
    for statement in tree.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            break
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            found.append(statement)
    return found


def fails_run_by_path(tree: ast.Module) -> bool:
    """Whether running the module by its path already fails before a helper's import would run.

    Run by path, a module has no package, so a relative import among its
    leading imports raises ``ImportError`` there, as it always did.
    """
    return any(isinstance(node, ast.ImportFrom) and node.level for node in leading_imports(tree))


def _breaks_run_by_path(borrower: Path, loaded: Set[Path], program: ProgramImports) -> bool:
    """Whether ``python borrower.py`` could no longer import once the borrower imports the host.

    Run by its path, a module has its own directory on ``sys.path``, not the
    directory its package is imported from: ``python pkg/tool_b.py`` finds
    ``pkg`` only where something else put it on the path, so ``from
    pkg.tool_a import helper`` raises ``ModuleNotFoundError`` there while
    ``python -m pkg.tool_b`` still works. A module written to run as a
    program therefore imports a module the new import loads only when its
    top-level package is one its leading imports already import absolutely,
    which a run by path already needs, or one that its own directory holds;
    a module whose leading imports include a relative one fails by path
    already, at that import. The new import must then be absolute too, which
    materialization checks. Paths are the model's; the borrower is read
    where the run keeps it.
    """
    path = program.in_run(borrower)
    try:
        source = read_source(path)
        tree = ast.parse(source)
    except (OSError, UnicodeError, SyntaxError, ValueError):
        return True
    if not runs_as_script(source, tree, path) or fails_run_by_path(tree):
        return False
    required = {
        alias.name.split(".")[0]
        for node in leading_imports(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in leading_imports(tree)
        if isinstance(node, ast.ImportFrom) and node.module and not node.level
    }
    for module in loaded:
        top = _top_level_name(module, program)
        if top is None:
            return True
        location = program.model.names[top].location
        if top in required or (location is not None and location.parent == borrower.parent):
            continue
        return True
    return False


def _new_top_level_packages(
    added: Set[Path], present: Set[Path], borrower: Path, program: ProgramImports
) -> FrozenSet[Optional[str]]:
    """Top-level packages of the project the new import loads that the borrower does not rely on.

    A distribution ships the packages its metadata names, not the whole
    repository, and only what the borrower's package already relies on is
    known to ship with it: a helper in ``tests/test_b.py`` imported by
    ``zeta/a.py`` made the installed ``zeta.a`` raise
    ``ModuleNotFoundError``. It relies on its own package, on what its
    import certainly loads, and, by the rule new imports are spelled by, on
    every top-level package its modules import at run time (docs/DECISIONS.md,
    "Import names come from the program"). A module the program's imports
    give no absolute name to, outside the borrower's own package, is always
    new.
    """
    own = program.model.context_of(borrower)
    available = set(program.model.attested_by(own)) if own is not None else set()
    available.update(
        top for module in present if (top := _top_level_name(module, program)) is not None
    )
    return frozenset(
        top
        for module in added
        if own is None or program.model.context_of(module) != own
        if (top := _top_level_name(module, program)) not in available
    )


def _top_level_name(module: Path, program: ProgramImports) -> Optional[str]:
    """The first component of the absolute name the program's imports give ``module``."""
    name = program.model.module_name(module)
    return None if name is None else name.partition(".")[0]


# Standard-library modules whose import does something visible.
_EFFECTFUL_STDLIB = frozenset({"this", "antigravity"})


def _new_requirements(
    added: Set[Path],
    present: Set[Path],
    program: ProgramImports,
    cache: ImportGraphCache,
) -> FrozenSet[str]:
    """Third-party modules the new import requires that the borrower's import does not.

    An import is inert as a statement, but it is also a requirement: a host
    doing ``import tornado`` cannot be imported where tornado is absent, so a
    borrower made to import it stopped importing in exactly those
    environments (gunicorn's sync worker). What the borrower already imports
    it already requires; the standard library is always there; a project's
    declared dependencies are installed wherever it is; and a name the
    program's imports place in the project is the project's own. Anything
    else is a new requirement, and the host is refused. An import guarded by
    ``try``/``except`` is how optional dependencies are spelled, and requires
    nothing.
    """

    def required(modules: Set[Path]) -> Set[str]:
        return {
            name for module in modules for name in _required_imports(program.in_run(module), cache)
        }

    names = {name for name in required(added) - required(present) if not program.is_local(name)}
    available = (
        set(sys.stdlib_module_names) - _EFFECTFUL_STDLIB
    ) | cache.installed_with_the_project(program.model.root)
    return frozenset(name for name in names if normalized_name(name) not in available)


def _required_imports(module: Path, cache: ImportGraphCache) -> FrozenSet[str]:
    """Top-level names of the absolute imports ``module`` runs unconditionally at import."""
    try:
        stat = module.stat()
    except OSError:
        return frozenset()
    key = (module, stat.st_mtime_ns, stat.st_size)
    known = cache.required_imports.get(key)
    if known is not None:
        return known
    try:
        source = read_source(module)
        tree = ast.parse(source)
    except (OSError, UnicodeError, SyntaxError):
        return cache.required_imports.put(key, frozenset())
    guards = TypeCheckingGuards.of(source, tree)
    names: Set[str] = set()
    pending: List[Tuple[ast.stmt, int]] = [(node, order) for order, node in enumerate(tree.body)]
    while pending:
        statement, order = pending.pop()
        if isinstance(statement, ast.Import):
            names.update(alias.name.split(".")[0] for alias in statement.names)
        elif isinstance(statement, ast.ImportFrom) and statement.level == 0 and statement.module:
            names.add(statement.module.split(".")[0])
        elif isinstance(statement, ast.If):
            # Either branch may be the one that runs; an import there is required
            # there. Under ``TYPE_CHECKING`` only the ``else`` ever runs.
            branches = (
                statement.orelse
                if guards.never_true(statement.test, order=order)
                else statement.body + statement.orelse
            )
            pending.extend((branch, order) for branch in branches)
    names.discard("__future__")
    return cache.required_imports.put(key, frozenset(names))


def would_create_import_cycle(
    canonical_file: str, replacement_files: Set[str], cache: ImportGraphCache
) -> bool:
    """Whether importing the helper from ``canonical_file`` into the other files closes a cycle.

    Every file the new imports execute, the host and the initializers of
    the packages enclosing it, is followed through its static imports,
    resolved as the program's import model resolves them
    (``ImportModel.files_reached``): initializers included, every candidate
    location of an ambiguous name included, and imports inside functions
    included conservatively. Dynamic imports cannot be resolved statically.
    A borrower reached is a cycle; so is a module on the way that cannot be
    read, or that imports what the model did not read, since neither can be
    shown safe.
    """
    program = cache.program_for(Path(canonical_file))
    host = program.origin(Path(canonical_file))
    borrowers = {program.origin(Path(path)) for path in replacement_files} - {host}
    if not borrowers:
        return False
    # Importing ``pkg.sub.helper`` runs ``pkg/__init__.py`` and
    # ``pkg/sub/__init__.py`` before the helper's module, so a cycle that
    # closes through one of those initializers is just as real as one through
    # the module itself (invoke: a vendored module importing a helper from
    # ``invoke.parser`` ran ``invoke/parser/__init__``, which reaches back
    # into the vendored package through ``invoke.util``).
    pending: List[Path] = [host, *program.package_initializers(host)]
    visited: Set[Path] = set()
    while pending:
        current = pending.pop()
        if current in borrowers:
            return True
        if current in visited:
            continue
        visited.add(current)
        edges = _import_edges(current, program, cache)
        if edges is None or edges.unseen:
            # If an import cannot be inspected, do not claim that it is safe.
            return True
        pending.extend(edges.files - visited)
    return False
