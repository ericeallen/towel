"""Resolving a project's import graph on the filesystem, for the cycle guard and helper placement.

Which files an import statement reaches, which module defines an imported name,
and whether a new import would close a static cycle. Results are cached per
run in an ``ImportGraphCache`` the engine owns.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import (
    Dict,
    FrozenSet,
    Iterable,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
)

from .bounded_cache import BoundedCache
from .exceptions import UnsupportedLayoutError
from ..project_layout import ProjectLayout, package_chain
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
            Tuple[Path, int, int, FrozenSet[Path]], Optional[FrozenSet[Path]]
        ] = BoundedCache(limit)
        self.bindings: BoundedCache[Tuple[Path, int, int], Optional[Dict[str, Tuple[str, ...]]]] = (
            BoundedCache(limit)
        )
        self.module_files: BoundedCache[Tuple[Path, Tuple[str, ...]], FrozenSet[Path]] = (
            BoundedCache(limit)
        )
        self.source_roots: BoundedCache[Path, Tuple[Path, ...]] = BoundedCache(limit)
        # Resolving a path walks the filesystem; the class-hierarchy lookup
        # resolves every class's file per base-class reference.
        self.resolved_paths: BoundedCache[str, Path] = BoundedCache(limit)

    def resolve(self, path: str) -> Path:
        """``Path(path).resolve()``, once per spelling for the life of the cache."""
        resolved = self.resolved_paths.get(path)
        if resolved is None:
            resolved = self.resolved_paths.put(path, Path(path).resolve())
        return resolved


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

    Out-of-place refactoring writes a package's modules into a flat output
    directory while they keep their original absolute imports, so the leading
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


def _import_edges(
    current: Path, roots: FrozenSet[Path], cache: ImportGraphCache
) -> Optional[FrozenSet[Path]]:
    """Local modules ``current`` imports, or None when its imports cannot be inspected."""
    try:
        stat = current.stat()
    except OSError:
        return None
    key = (current, stat.st_mtime_ns, stat.st_size, roots)
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
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                dependencies.update(_module_files_relocated(roots, alias.name.split("."), cache))
                dependencies.update(_suffix_in_tree(tree_root, alias.name.split("."), cache))
        elif isinstance(node, ast.ImportFrom):
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
            else:
                dependencies.update(_module_files_relocated(roots, components, cache))
                dependencies.update(_suffix_in_tree(tree_root, components, cache))
                for alias in node.names:
                    if alias.name != "*":
                        dependencies.update(
                            _module_files_relocated(roots, [*components, alias.name], cache)
                        )
                        dependencies.update(
                            _suffix_in_tree(tree_root, [*components, alias.name], cache)
                        )
    return cache.edges.put(key, frozenset(dependencies))


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
