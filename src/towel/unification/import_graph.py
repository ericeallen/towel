"""Resolving a project's import graph on the filesystem, for the cycle guard and helper placement.

Which files an import statement reaches, which module defines an imported name,
whether a new import would close a static cycle, and whether running a
module's top level can run code of its own (``ImportTimeCode``). Results are
cached per run in an ``ImportGraphCache`` the engine owns.
"""

from __future__ import annotations

import ast
import builtins
import configparser
import os
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
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
)

from .bounded_cache import BoundedCache
from .exceptions import UnsupportedLayoutError
from ..project_layout import ProjectLayout, find_project_root, load_pyproject, package_chain
from .module_bindings import NAMESPACE_PRESERVING_DECORATORS, dotted_name, global_bindings
from .statement_facts import (
    imported_binding_name,
)
from ..source_text import read_source


class ImportGraphCache:
    """What one run has learned about the project's import graph.

    A cross-file pair asks whether hosting a helper would close an import
    cycle, and answering re-reads every reachable module unless the edges are
    remembered (Sphinx: 243 modules per pair). Edges and import bindings are
    keyed by path, modification time, and size, so a rewritten file is
    re-read; module lookups and source roots are keyed by path. Every table
    is bounded, and the engine owns one instance per run; the module-level
    default serves callers that have no engine.
    """

    def __init__(self, limit: int = 8192) -> None:
        self.edges: BoundedCache[
            Tuple[Path, int, int, FrozenSet[Path], str], Optional[FrozenSet[Path]]
        ] = BoundedCache(limit)
        self.bindings: BoundedCache[Tuple[Path, int, int], Optional[Dict[str, Tuple[str, ...]]]] = (
            BoundedCache(limit)
        )
        self.module_files: BoundedCache[Tuple[Path, Tuple[str, ...]], FrozenSet[Path]] = (
            BoundedCache(limit)
        )
        self.source_roots: BoundedCache[Path, Tuple[Path, ...]] = BoundedCache(limit)
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


def layout_is_known(canonical_file: str, cache: ImportGraphCache) -> bool:
    """Whether the project around ``canonical_file`` has a layout Towel can model.

    Cross-file helpers need an import, and an import needs the project's
    source roots; a layout that discovery refuses leaves every cross-file
    pair in that project undecidable.
    """
    return _source_roots(Path(canonical_file).resolve(), cache) is not None


def _source_roots(path: Path, cache: ImportGraphCache) -> Optional[Tuple[Path, ...]]:
    """The project's source roots as seen from ``path``, or None when the layout is unknown."""
    roots = cache.source_roots.get(path)
    if roots is None:
        try:
            roots = tuple(ProjectLayout.discover(path).source_roots)
        except UnsupportedLayoutError:
            return None
        cache.source_roots.put(path, roots)
    return roots


def _module_files(
    base: Path, components: Iterable[str], cache: ImportGraphCache
) -> FrozenSet[Path]:
    key = (base, tuple(components))
    cached = cache.module_files.get(key)
    if cached is not None:
        return cached
    result: Set[Path] = set()
    cursor = base
    for component in key[1]:
        cursor = cursor / component
        initializer = cursor / "__init__.py"
        if initializer.is_file():
            result.add(initializer.resolve())
    module = cursor.with_suffix(".py")
    if module.is_file():
        result.add(module.resolve())
    return cache.module_files.put(key, frozenset(result))


def _module_files_relocated(
    roots: FrozenSet[Path], components: Sequence[str], cache: ImportGraphCache
) -> Set[Path]:
    """Files an absolute import resolves to across ``roots``, tolerating relocation.

    A package analyzed as a copy outside its project (a library caller's
    fixture, a copied checkout; ``towel dry`` itself stages the whole project,
    so it never does this) keeps its original absolute imports, so the leading
    package components (``starlette`` in ``starlette.websockets``) have no
    directory to match and the full path resolves to nothing. Only then do we
    retry against progressively shorter trailing suffixes, so
    ``starlette.websockets`` still resolves to a relocated ``websockets.py`` and
    the import-cycle guard sees the edge. The fallback can only *add* edges, so
    at worst the guard grows more conservative; a normally laid-out project
    resolves on the first attempt and never reaches it.
    """
    parts = list(components)
    files: Set[Path] = set()
    for root in roots:
        files |= set(_module_files(root, parts, cache))
    if files or len(parts) <= 1:
        return files
    for start in range(1, len(parts)):
        for root in roots:
            resolved = _module_files(root, parts[start:], cache)
            if resolved:
                files |= set(resolved)
                # The dropped leading components are the relocated package
                # itself, whose ``__init__`` executes when the import runs, so
                # importing ``pkg.sub`` from a relocated ``pkg`` also depends on
                # the root ``__init__.py``. Missing this edge let the tenacity
                # cycle (retry -> asyncio.retry -> tenacity/__init__ -> retry)
                # slip through.
                initializer = root / "__init__.py"
                if initializer.is_file():
                    files.add(initializer.resolve())
        if files:
            break
    return files


def _package_tree_root(module: Path) -> Path:
    """The topmost package directory containing ``module``, or its own directory."""
    packages = package_chain(module)
    return packages[-1] if packages else module.parent


def _suffix_in_tree(
    tree_root: Path, components: Sequence[str], cache: ImportGraphCache
) -> Set[Path]:
    """Files a dotted import resolves to inside the file's own package tree by suffix.

    ``sphinx.transforms.x`` from a file under a relocated ``sphinx-cleaned``
    resolves to ``transforms/x.py`` there; the dropped leading components
    are the package itself, whose initializer runs with the import.
    """
    parts = list(components)
    if not (tree_root / "__init__.py").is_file():
        return set()
    for start in range(1, len(parts)):
        found = _module_files(tree_root, parts[start:], cache)
        if found:
            return set(found) | {(tree_root / "__init__.py").resolve()}
    return set()


# Which import statements of a module count: every one (the cycle guard,
# which must not miss an edge), the ones its top level can run as it is
# imported (in any branch, but not in a function body), or only those its top
# level runs on every path (statements of the module body itself).
ImportExtent = Literal["everywhere", "at_import", "unconditionally"]


def _import_edges(
    current: Path,
    roots: FrozenSet[Path],
    cache: ImportGraphCache,
    extent: ImportExtent = "everywhere",
) -> Optional[FrozenSet[Path]]:
    """Local modules ``current`` imports, or None when its imports cannot be inspected."""
    try:
        stat = current.stat()
    except OSError:
        return None
    key = (current, stat.st_mtime_ns, stat.st_size, roots, extent)
    if key in cache.edges:
        return cache.edges.get(key)
    try:
        tree = ast.parse(read_source(current))
    except (OSError, UnicodeError, SyntaxError):
        return cache.edges.put(key, None)
    dependencies: Set[Path] = set()
    # The tree the file lives in, for resolving its own package's absolute
    # imports by suffix even when a full-path match exists elsewhere: an
    # out-of-place output sits beside the original clone (sphinx-cleaned next
    # to sphinx), and ``from sphinx.transforms import X`` resolved to the
    # original, where the helper import did not yet exist, so the cycle it
    # closed in the copy went unseen.
    tree_root = _package_tree_root(current)
    for node in _import_statements(tree, extent):
        dependencies.update(_statement_edges(node, current, roots, tree_root, cache))
    return cache.edges.put(key, frozenset(dependencies))


def _import_statements(
    tree: ast.Module, extent: ImportExtent
) -> List[Union[ast.Import, ast.ImportFrom]]:
    """The import statements of ``tree`` that ``extent`` counts."""
    if extent == "everywhere":
        return [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    if extent == "unconditionally":
        return [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    found: List[Union[ast.Import, ast.ImportFrom]] = []

    def visit(statements: Sequence[ast.stmt]) -> None:
        # A class body and every branch of a compound statement run, or may
        # run, as the module is imported; a function body does not.
        for statement in statements:
            if isinstance(statement, (ast.Import, ast.ImportFrom)):
                found.append(statement)
            elif not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for field in ("body", "orelse", "finalbody"):
                    visit(getattr(statement, field, []))
                for clause in (
                    *getattr(statement, "handlers", []),
                    *getattr(statement, "cases", []),
                ):
                    visit(clause.body)

    visit(tree.body)
    return found


def _statement_edges(
    node: Union[ast.Import, ast.ImportFrom],
    current: Path,
    roots: FrozenSet[Path],
    tree_root: Path,
    cache: ImportGraphCache,
) -> Set[Path]:
    """The local files one import statement of ``current`` loads."""
    dependencies: Set[Path] = set()
    if isinstance(node, ast.Import):
        for alias in node.names:
            dependencies.update(_module_files_relocated(roots, alias.name.split("."), cache))
            dependencies.update(_suffix_in_tree(tree_root, alias.name.split("."), cache))
        return dependencies
    components = node.module.split(".") if node.module else []
    if node.level:
        # A relative import resolves unambiguously against a computed
        # base; relocation does not apply, so no suffix fallback.
        base = current.parent
        for _ in range(node.level - 1):
            base = base.parent
        dependencies.update(_module_files(base, components, cache))
        for alias in node.names:
            if alias.name != "*":
                dependencies.update(_module_files(base, [*components, alias.name], cache))
        # ``from . import name`` (or ``from .. import name``) runs the
        # package's ``__init__`` whether ``name`` is a submodule or an
        # attribute defined there. With no module file to resolve to,
        # the edge to the initializer was missed, and a helper hosted
        # in a submodule got imported by that ``__init__``, which the
        # submodule imports back (beautifulsoup4's tests package).
        package = base
        for component in components:
            package = package / component
        initializer = package / "__init__.py"
        if initializer.is_file():
            dependencies.add(initializer.resolve())
        return dependencies
    dependencies.update(_module_files_relocated(roots, components, cache))
    dependencies.update(_suffix_in_tree(tree_root, components, cache))
    for alias in node.names:
        if alias.name != "*":
            dependencies.update(_module_files_relocated(roots, [*components, alias.name], cache))
            dependencies.update(_suffix_in_tree(tree_root, [*components, alias.name], cache))
    return dependencies


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


def _dotted_path_files(current: Path, roots: Iterable[Path], parts: Tuple[str, ...]) -> Set[Path]:
    """Files defining the module at ``parts``; a leading dotted part marks a relative path."""
    if parts and set(parts[0]) == {"."}:
        base = current.parent
        for _ in range(len(parts[0]) - 1):
            base = base.parent
        bases: Iterable[Path] = (base,)
        parts = parts[1:]
    else:
        bases = roots
    files = (_module_definition_file(base, parts) for base in bases)
    return {file for file in files if file is not None}


def module_and_qualname(
    current_file: str, dotted: str, cache: ImportGraphCache
) -> Optional[Tuple[str, str]]:
    """Split a fully qualified name into the module that defines it and the rest.

    A checker names a type by its whole path, ``pkg.mod.Outer.Inner``, and
    writing that down needs to know where the module ends and the class begins.
    Splitting at the last dot is wrong for a nested class, so the longest
    prefix that is a module file in this project wins. None when the layout
    gives no roots or no prefix names a module.
    """
    parts = tuple(dotted.split("."))
    if len(parts) < 2:
        return None
    roots = _source_roots(Path(current_file).resolve(), cache)
    if not roots:
        return None
    for end in range(len(parts) - 1, 0, -1):
        if any(_module_definition_file(root, parts[:end]) is not None for root in roots):
            return ".".join(parts[:end]), ".".join(parts[end:])
    return None


def imported_definition_sites(
    current_file: str, dotted_name: str, cache: ImportGraphCache
) -> Optional[FrozenSet[Tuple[Path, str]]]:
    """Where a name used in ``current_file`` may be defined, as (file, qualname) pairs.

    ``dotted_name`` is written as it appears in the module (``Base``,
    ``mod.Base``, ``pkg.mod.Outer.Inner``). Its longest dotted prefix bound
    by a module-level import names the module; the remainder is the
    qualified name inside it. Because ``from m import x`` may denote a
    submodule or an attribute, each split of the path into module and
    qualname whose module file exists is a candidate. None when the name
    is not bound by an unconditional module-level import, the module cannot
    be parsed, or the project layout gives no import roots.
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
    roots = _source_roots(current, cache)
    if roots is None:
        return None
    full = (*target, *remainder)
    sites: Set[Tuple[Path, str]] = set()
    # ``target`` may end in an attribute rather than a module, so every
    # split at or after the bound module's own length is tried.
    for split in range(max(1, len(target) - 1), len(full)):
        module, qualname = full[:split], full[split:]
        if not qualname:
            continue
        for file in _dotted_path_files(current, roots, module):
            sites.add((file, ".".join(qualname)))
    return frozenset(sites)


def _package_initializers(module: Path, roots: FrozenSet[Path]) -> List[Path]:
    """``__init__.py`` of every package enclosing ``module``, innermost first.

    Ascends while the directory is a package (has ``__init__.py``) and stops
    at a source root, whose own initializer is not executed by an absolute
    import of a module inside it.
    """
    initializers: List[Path] = []
    for directory in package_chain(module):
        if directory in roots:
            break
        initializers.append((directory / "__init__.py").resolve())
    return initializers


def _reachable_modules(
    start: Path,
    roots: FrozenSet[Path],
    cache: ImportGraphCache,
    extent: ImportExtent = "everywhere",
) -> Optional[Set[Path]]:
    """Every local module importing ``start`` runs, ``start`` and its package initializers included.

    ``extent`` says which of each module's imports count (``ImportExtent``):
    ``"unconditionally"`` gives the modules importing ``start`` certainly
    loads, ``"at_import"`` those it may. None when some import cannot be
    inspected.
    """
    pending = [start, *_package_initializers(start, roots)]
    visited: Set[Path] = set()
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        dependencies = _import_edges(current, roots, cache, extent)
        if dependencies is None:
            return None
        pending.extend(dependencies - visited)
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


@dataclass(frozen=True)
class _ClassMachinery:
    """The bases and metaclasses one judgment of class creation takes as Python's own."""

    label: str
    bases: FrozenSet[str]
    subscripted_bases: FrozenSet[str]
    metaclasses: FrozenSet[str]


# Subclassing runs only Python's class machinery: nothing of the project runs.
_RUNS_NO_CODE = _ClassMachinery(
    "runs-no-code", _QUIET_BASES, _QUIET_SUBSCRIPTED_BASES, _QUIET_METACLASSES
)
# Building the class leaves every plain function of its body a plain member.
_KEEPS_FUNCTIONS = _ClassMachinery(
    "keeps-functions",
    frozenset({"abc.ABC"}) | _ENUM_BASES,
    frozenset({"typing.Generic"}),
    _QUIET_METACLASSES | _ENUM_METACLASSES,
)

# Where the names an evaluated annotation subscripts may come from.
_ANNOTATION_MODULES = frozenset({"collections.abc", "typing", "typing_extensions"})
_BUILTIN_GENERICS = frozenset({"dict", "frozenset", "list", "set", "tuple", "type"})

# Facts of the running interpreter a module may branch on at import.
_PLATFORM_FACTS = frozenset({"os.name", "sys.byteorder", "sys.platform", "sys.version_info"})
_TYPE_CHECKING = frozenset({"typing.TYPE_CHECKING", "typing_extensions.TYPE_CHECKING"})

_BUILTIN_CLASSES = frozenset(
    f"builtins.{name}" for name, value in vars(builtins).items() if isinstance(value, type)
)


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
        if self._origin(test, order, body) in _TYPE_CHECKING:
            return True
        # The module's own ``TYPE_CHECKING = False``, bound once and never again.
        if not isinstance(test, ast.Name) or body is not None or self._bindings is None:
            return False
        binding = self._bindings.in_effect(test.id, order)
        if binding is None or len(self._bindings.bindings.get(test.id, ())) != 1:
            return False
        statement = self._tree.body[binding.order]
        return (
            isinstance(statement, ast.Assign)
            and isinstance(statement.value, ast.Constant)
            and statement.value.value is False
        )

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

    def leaves_functions_alone(self, name: str) -> bool:
        """Whether building the top-level class ``name`` leaves a plain function of its body alone.

        A helper placed in the body is seen by the class's metaclass, which
        builds the class from the namespace, and by the ``__init_subclass__``
        of every class on its method resolution order, its own included for
        each subclass: any of them may wrap it, register it, or drop it. So
        the metaclass must be ``type``, ``abc.ABCMeta`` or the enum
        metaclass, the class must define no ``__init_subclass__``, and every
        base must be a builtin class, ``abc.ABC``, ``typing.Generic[...]``,
        an enum, or a class of the project that qualifies in turn, each
        resolved through the module's imports (``_KEEPS_FUNCTIONS``). The
        class's decorators are :meth:`ModuleBindings.keeps_namespace`'s
        question.
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
            and self._quiet_keywords(node.keywords, order, _KEEPS_FUNCTIONS)
            and not _defines_init_subclass(node)
            and all(self._quiet_base(base, order, _KEEPS_FUNCTIONS) for base in node.bases)
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
        if origin in machinery.bases or origin in _BUILTIN_CLASSES:
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
        """A class of this module whose own machinery leaves its subclasses' creation alone."""
        return (
            all(
                self._origin(decorator, order, None) in NAMESPACE_PRESERVING_DECORATORS
                for decorator in node.decorator_list
            )
            and self._quiet_keywords(node.keywords, order, machinery)
            and not _defines_init_subclass(node)
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


def _defines_init_subclass(node: ast.ClassDef) -> bool:
    """Whether the class body binds ``__init_subclass__``, which then runs for every subclass."""
    return any("__init_subclass__" in _statement_binds(inner) for inner in node.body)


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

    A cross-file helper adds ``from host import helper`` to the borrower. If
    the borrower's import already certainly loads the host, nothing new runs.
    Otherwise every module the new import may load that the borrower's does
    not certainly load already must run no code at import
    (``ImportTimeCode``), require no package that may be absent where the
    borrower is installed (``_new_requirements``), and belong to a top-level
    package the borrower's import already loads (``_new_top_level_packages``).
    An import in a function body runs only when the function is called, so it
    makes nothing present at the borrower's import.
    """
    host = Path(host_file).resolve()
    borrower = Path(borrower_file).resolve()
    common_root = Path(os.path.commonpath([str(host.parent), str(borrower.parent)]))
    source_roots = _source_roots(host, cache)
    if source_roots is None:
        return ImportChange.UNKNOWN
    roots = frozenset(source_roots) | {common_root}
    already = _reachable_modules(borrower, roots, cache, "unconditionally")
    if already is None:
        return ImportChange.UNKNOWN
    added: Set[Path] = set()
    if host not in already:
        loaded = _reachable_modules(host, roots, cache, "at_import")
        if loaded is None:
            return ImportChange.UNKNOWN
        added = loaded - already
        if any(_has_import_time_effects(module, cache) for module in added):
            return ImportChange.RUNS_CODE
        if _new_requirements(added, already, roots, host, cache):
            return ImportChange.NEW_REQUIREMENT
        if _new_top_level_packages(added, already, source_roots):
            return ImportChange.NEW_TOP_LEVEL_PACKAGE
    if _breaks_run_by_path(borrower, added | {host}, source_roots):
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
    project's ``typings`` directory, pyright's default stub path. A module
    whose layout is unknown counts as stubbed.
    """
    module = Path(host_file).resolve()
    if module.with_suffix(".pyi").is_file():
        return True
    source_roots = _source_roots(module, cache)
    root = _holding_root(module, source_roots or ())
    if root is None:
        return True
    parts = module.relative_to(root).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        return False
    project = find_project_root(module)
    for base in dict.fromkeys((root, project)):
        package = base / f"{parts[0]}-stubs"
        if package.is_dir():
            # A partial stub package leaves the modules it omits to the
            # runtime package; any other hides them from the checker.
            if not _is_partial_stub_package(package) or _stub_file(package, parts[1:]):
                return True
    return _stub_file(project / "typings", parts)


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


def _breaks_run_by_path(borrower: Path, loaded: Set[Path], source_roots: Sequence[Path]) -> bool:
    """Whether ``python borrower.py`` could no longer import once the borrower imports the host.

    Run by its path, a module has its own directory on ``sys.path``, not the
    source root above it: ``python pkg/tool_b.py`` finds ``pkg`` only where
    something else put it on the path, so ``from pkg.tool_a import helper``
    raises ``ModuleNotFoundError`` there while ``python -m pkg.tool_b`` still
    works. A module written to run as a program therefore imports a module
    the new import loads only when its top-level package is one its leading
    imports already import absolutely, which a run by path already needs,
    or one that its own directory holds; a module whose leading imports
    include a relative one fails by path already, at that import. The new
    import must then be absolute too, which materialization checks.
    """
    try:
        source = read_source(borrower)
        tree = ast.parse(source)
    except (OSError, UnicodeError, SyntaxError, ValueError):
        return True
    if not runs_as_script(source, tree, borrower) or fails_run_by_path(tree):
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
        top = _top_level_name(module, source_roots)
        if top is None:
            return True
        if top in required or _holding_root(module, source_roots) == borrower.parent:
            continue
        return True
    return False


def _new_top_level_packages(
    added: Set[Path], present: Set[Path], source_roots: Sequence[Path]
) -> FrozenSet[Optional[str]]:
    """Top-level packages of the project the new import loads that the borrower's does not.

    A distribution ships the packages its metadata names, not the whole
    repository, and only what the borrower already imports is known to ship
    with it: a helper in ``tests/test_b.py`` imported by ``zeta/a.py`` made
    the installed ``zeta.a`` raise ``ModuleNotFoundError``. A module under
    no source root has no top-level package that could be named, and is
    always new.
    """
    available = _importable_top_levels(present, source_roots)
    return frozenset(
        top for module in added if (top := _top_level_name(module, source_roots)) not in available
    )


def _importable_top_levels(present: Set[Path], source_roots: Sequence[Path]) -> FrozenSet[str]:
    """The top-level packages importing the borrower certainly loads, its own included.

    Whatever the borrower's import loads ships wherever the borrower runs,
    so a new import of any of these requires nothing that was not there.
    """
    return frozenset(
        top for module in present if (top := _top_level_name(module, source_roots)) is not None
    )


def _top_level_name(module: Path, source_roots: Sequence[Path]) -> Optional[str]:
    """The first component of ``module``'s import name, from the deepest source root holding it."""
    root = _holding_root(module, source_roots)
    if root is None:
        return None
    parts = module.relative_to(root).parts
    return Path(parts[0]).stem if len(parts) == 1 else parts[0]


def _holding_root(module: Path, source_roots: Sequence[Path]) -> Optional[Path]:
    """The deepest source root ``module`` lies under, resolved; None when it lies under none."""
    holding = [root.resolve() for root in source_roots if module.is_relative_to(root.resolve())]
    return max(holding, key=lambda root: len(root.parts)) if holding else None


# Standard-library modules whose import does something visible.
_EFFECTFUL_STDLIB = frozenset({"this", "antigravity"})


def _new_requirements(
    added: Set[Path],
    present: Set[Path],
    roots: FrozenSet[Path],
    host: Path,
    cache: ImportGraphCache,
) -> FrozenSet[str]:
    """Third-party modules the new import requires that the borrower's import does not.

    An import is inert as a statement, but it is also a requirement: a host
    doing ``import tornado`` cannot be imported where tornado is absent, so a
    borrower made to import it stopped importing in exactly those
    environments (gunicorn's sync worker). What the borrower already imports
    it already requires; the standard library is always there; and a
    project's declared dependencies are installed wherever it is. Anything
    else is a new requirement, and the host is refused. An import guarded by
    ``try``/``except`` is how optional dependencies are spelled, and requires
    nothing.
    """

    def required(modules: Set[Path]) -> Set[str]:
        return {name for module in modules for name in _required_imports(module, cache)}

    names = required(added) - required(present)
    names = {name for name in names if not _is_local(name, roots)}
    available = (set(sys.stdlib_module_names) - _EFFECTFUL_STDLIB) | _declared_dependencies(host)
    return frozenset(name for name in names if _normalized(name) not in available)


def _is_local(name: str, roots: FrozenSet[Path]) -> bool:
    return any((root / name).is_dir() or (root / f"{name}.py").is_file() for root in roots)


def _normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "_", name).lower()


def _declared_dependencies(path: Path) -> Set[str]:
    """The import names the project's declared runtime dependencies provide, as best known.

    Read from PEP 621's ``[project].dependencies``, Poetry's
    ``[tool.poetry.dependencies]`` (its ``python`` entry and the optional
    dependencies only an extra installs aside) and setup.cfg's ``[options]
    install_requires``. A setup.py is not run, so a project declaring its
    dependencies only there declares none here, which refuses a host rather
    than accepting one. A distribution name is taken as its import name,
    normalized; one that differs (``PyYAML`` providing ``yaml``) is simply not
    recognized, with the same effect.
    """
    root = find_project_root(path)
    data = load_pyproject(root)
    project = data.get("project", {})
    declared = project.get("dependencies", []) if isinstance(project, dict) else []
    names = _requirement_names(declared if isinstance(declared, list) else [])
    return names | _poetry_dependencies(data) | _setup_cfg_install_requires(root)


def _requirement_names(requirements: Iterable[object]) -> Set[str]:
    """The normalized distribution names of PEP 508 requirement strings."""
    return {
        _normalized(match.group(0))
        for requirement in requirements
        if isinstance(requirement, str)
        for match in [re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", requirement.strip())]
        if match is not None
    }


def _poetry_dependencies(data: Mapping[str, object]) -> Set[str]:
    """The distributions ``[tool.poetry.dependencies]`` installs with the project itself."""
    tool = data.get("tool")
    poetry = tool.get("poetry") if isinstance(tool, dict) else None
    table = poetry.get("dependencies") if isinstance(poetry, dict) else None
    if not isinstance(table, dict):
        return set()
    return {
        _normalized(name)
        for name, specification in table.items()
        if isinstance(name, str)
        and name.lower() != "python"
        and not (isinstance(specification, dict) and specification.get("optional") is True)
    }


def _setup_cfg_install_requires(root: Path) -> Set[str]:
    """The distributions setup.cfg's ``[options] install_requires`` names, one per line.

    A ``file:`` directive names a file this does not read, and declares
    nothing here.
    """
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(root / "setup.cfg", encoding="utf-8")
    except (configparser.Error, OSError, UnicodeError):
        return set()
    value = parser.get("options", "install_requires", fallback="")
    if value.strip().startswith("file:"):
        return set()
    return _requirement_names(line.split("#", 1)[0] for line in value.splitlines())


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
        tree = ast.parse(read_source(module))
    except (OSError, UnicodeError, SyntaxError):
        return cache.required_imports.put(key, frozenset())
    names: Set[str] = set()
    pending: List[ast.stmt] = list(tree.body)
    while pending:
        statement = pending.pop()
        if isinstance(statement, ast.Import):
            names.update(alias.name.split(".")[0] for alias in statement.names)
        elif isinstance(statement, ast.ImportFrom) and statement.level == 0 and statement.module:
            names.add(statement.module.split(".")[0])
        elif isinstance(statement, ast.If) and not (
            isinstance(statement.test, ast.Name) and statement.test.id == "TYPE_CHECKING"
        ):
            # Either branch may be the one that runs; an import there is required there.
            pending.extend(statement.body + statement.orelse)
    names.discard("__future__")
    return cache.required_imports.put(key, frozenset(names))


def would_create_import_cycle(
    canonical_file: str, replacement_files: Set[str], cache: ImportGraphCache
) -> bool:
    """Check whether adding imports of the helper closes a local import cycle.

    Follow static imports through local modules, including modules without any
    candidate functions and package initializers. Imports inside functions are
    included conservatively. Dynamic imports cannot be resolved statically. A
    project whose layout cannot be modelled (see ``layout_is_known``) counts
    as a cycle: the helper's import cannot be shown safe there.
    """
    canonical = Path(canonical_file).resolve()
    targets = {Path(path).resolve() for path in replacement_files} - {canonical}
    if not targets:
        return False
    common_root = Path(os.path.commonpath([str(path.parent) for path in targets | {canonical}]))
    source_roots = _source_roots(canonical, cache)
    if source_roots is None:
        return True
    roots = frozenset(source_roots) | {common_root}

    # Importing ``pkg.sub.helper`` runs ``pkg/__init__.py`` and
    # ``pkg/sub/__init__.py`` before the helper's module, so a cycle that
    # closes through one of those initializers is just as real as one through
    # the module itself (invoke: a vendored module importing a helper from
    # ``invoke.parser`` ran ``invoke/parser/__init__``, which reaches back
    # into the vendored package through ``invoke.util``). Search from every
    # package initializer above the host as well as from the host.
    pending = [canonical, *_package_initializers(canonical, roots)]
    visited: Set[Path] = set()
    while pending:
        current = pending.pop()
        if current in targets:
            return True
        if current in visited:
            continue
        visited.add(current)
        dependencies = _import_edges(current, roots, cache)
        if dependencies is None:
            # If an import cannot be inspected, do not claim that it is safe.
            return True
        pending.extend(dependencies - visited)
    return False
