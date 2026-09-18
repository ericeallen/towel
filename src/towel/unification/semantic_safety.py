"""Conservative guards for extractions that change execution context."""

from __future__ import annotations

import ast
import os
from pathlib import Path
from collections import OrderedDict
from typing import (
    TYPE_CHECKING,
    Dict,
    FrozenSet,
    Generic,
    Iterable,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
    TypeVar,
    Union,
)
from weakref import WeakKeyDictionary

from .binding_detector import BindingDetector
from .project_layout import ProjectLayout
from .scope_analyzer import ScopeAnalyzer, pattern_capture_names
from .visitors import OwnScopeVisitor

if TYPE_CHECKING:
    from .substitution import Substitution


def uses_class_private_names(nodes: Iterable[ast.AST]) -> bool:
    """Whether moving these nodes to a different class changes name mangling."""
    for statement in nodes:
        for node in ast.walk(statement):
            name = (
                node.id
                if isinstance(node, ast.Name)
                else (node.attr if isinstance(node, ast.Attribute) else "")
            )
            if name.startswith("__") and not name.endswith("__"):
                return True
    return False


def nested_bindings_escape(
    function: Union[ast.FunctionDef, ast.AsyncFunctionDef], nodes: Iterable[ast.AST]
) -> bool:
    """Reject nested extraction whose local writes are observable outside it.

    The engine's return-variable analysis covers top-level statement slices.
    A loop/branch body needs control-flow liveness, including reads on the next
    loop iteration. Until that analysis exists, require its bindings to remain
    entirely inside the extracted block. Attribute/subscript mutations do not
    rebind their base objects and are not counted as local writes.
    """
    block = tuple(nodes)
    if all(node in function.body for node in block):
        return False
    extracted = {child for statement in block for child in ast.walk(statement)}
    detector = BindingDetector()
    for statement in block:
        detector.visit(statement)
    bound = {binding.name for binding in detector.bindings}
    # Deletion changes the original local binding too, and is not a binding
    # construct reported by BindingDetector. Store contexts also conservatively
    # include declarations that do not initialize a value.
    bound.update(
        node.id
        for node in extracted
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del))
    )
    if not bound:
        return False
    for node in ast.walk(function):
        if node in extracted:
            continue
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, (ast.Load, ast.Del))
            and node.id in bound
        ):
            return True
        if (
            isinstance(node, ast.AugAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id in bound
        ):
            return True
    return False


def snapshots_rebound_external_names(
    analyzer: ScopeAnalyzer,
    function: Union[ast.FunctionDef, ast.AsyncFunctionDef],
    nodes: Iterable[ast.AST],
) -> bool:
    """Reject snapshots of external bindings with visible rebinding hazards.

    Explicit global/nonlocal declarations identify bindings another function can
    update during the extraction. Namespace reflection makes that identification
    unreliable, so its presence conservatively disqualifies external snapshots.
    Opaque mutation originating outside the analyzed module is not modeled.
    """
    scope = analyzer.node_scopes.get(function)
    root = analyzer.root_scope
    if scope is None or root is None:
        return True
    hazards = analyzer.external_binding_hazards
    if hazards is None:
        return True
    rebound = hazards.rebound
    unresolved = hazards.unresolved_nonlocal
    reflective = hazards.reflective
    for statement in nodes:
        for node in ast.walk(statement):
            if not isinstance(node, ast.Name) or not isinstance(node.ctx, ast.Load):
                continue
            binding = scope.lookup(node.id)
            if binding is None:
                if unresolved or reflective or (root.scope_id, node.id) in rebound:
                    return True
                continue
            # A local captured by a nested function is a mutable cell too.
            # Passing it to the helper snapshots it before that function runs.
            if (binding.scope_id, node.id) in rebound:
                return True
            if binding.scope_id == scope.scope_id:
                continue
            if unresolved or reflective:
                return True
    return False


def has_comprehension_assignment(nodes: Iterable[ast.AST]) -> bool:
    """Whether a comprehension writes a binding in its containing function.

    This requires return/liveness analysis across the comprehension boundary;
    until that is modeled, such a comprehension must remain in its caller.
    """
    return any(
        isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp))
        and any(isinstance(child, ast.NamedExpr) for child in ast.walk(node))
        for statement in nodes
        for node in ast.walk(statement)
    )


# Attributes and callees whose behavior depends on the call stack, the active
# traceback, or a module's own source text. Moving code into a helper adds a
# frame and shifts line numbers, so a program that reads any of these can
# observe the refactoring even when its result is unchanged. These power the
# pre-run warning; they are names to look for, not a guarantee of breakage.
_FRAME_SENSITIVE_ATTRS = frozenset(
    {
        "f_back",
        "f_locals",
        "f_globals",
        "f_lineno",
        "f_code",
        "tb_frame",
        "tb_next",
        "tb_lineno",
        "__traceback__",
    }
)
_FRAME_SENSITIVE_CALLEES = frozenset(
    {
        "_getframe",
        "currentframe",
        "stack",
        "getouterframes",
        "getframeinfo",
        "extract_stack",
        "print_stack",
        "extract_tb",
        "walk_tb",
        "walk_stack",
        "format_exc",
        "format_stack",
        "print_exc",
    }
)
_SOURCE_OBSERVING_CALLEES = frozenset(
    {"getsource", "getsourcelines", "getsourcefile", "findsource", "getframeinfo"}
)


def frame_sensitivity_markers(source: str) -> FrozenSet[str]:
    """Frame-, traceback-, and source-observing constructs a module contains.

    A module in the returned-empty case is not proof of safety; a callee that
    inspects frames internally is invisible here. This exists to warn a person
    which files to review, not to decide any single extraction.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return frozenset()
    markers: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if node.attr in _FRAME_SENSITIVE_ATTRS:
                markers.add("frame")
            if node.attr == "__traceback__":
                markers.add("traceback")
        elif isinstance(node, ast.Call):
            callee = node.func
            name = callee.attr if isinstance(callee, ast.Attribute) else getattr(callee, "id", "")
            if name == "warn" and any(kw.arg == "stacklevel" for kw in node.keywords):
                markers.add("stacklevel-warning")
            if name in _FRAME_SENSITIVE_CALLEES:
                markers.add("frame")
            if name in _SOURCE_OBSERVING_CALLEES:
                markers.add("source")
    return frozenset(markers)


def is_namespace_access_call(node: ast.AST) -> bool:
    """Whether ``node`` is a call that reads or writes the caller's namespace.

    That is ``locals()``, ``globals()``, ``eval()``, ``exec()``, or the
    no-argument ``vars()`` (which returns ``locals()``). A local variable,
    parameter, or attribute that merely shares one of these names is not such a
    call, so callers can rely on this to avoid false positives on shadowing.
    """
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
        return False
    name = node.func.id
    if name in {"locals", "globals", "eval", "exec"}:
        return True
    return name == "vars" and not node.args and not node.keywords


def requires_original_frame(nodes: Iterable[ast.AST]) -> bool:
    """Reject suspension and operations that inspect the original call frame.

    Generator delegation needs a separate transformation preserving send/throw
    and return values. Moving frame inspection into a helper is not equivalent.
    Unknown shadowing of these call names is deliberately treated conservatively.
    """
    block = tuple(nodes)
    if has_external_loop_control(block) or has_comprehension_assignment(block):
        return True
    for statement in block:
        for node in ast.walk(statement):
            if isinstance(node, (ast.Yield, ast.YieldFrom, ast.Await, ast.AsyncFor, ast.AsyncWith)):
                return True
            if isinstance(node, ast.Call):
                if is_namespace_access_call(node):
                    return True
                if (
                    isinstance(node.func, ast.Name)
                    and node.func.id == "super"
                    and not node.args
                    and not node.keywords
                ):
                    return True
                if _is_frame_relative_call(node):
                    return True
    return False


_FRAME_RELATIVE_CALLEES = frozenset(
    {"_getframe", "currentframe", "stack", "getouterframes", "extract_stack", "print_stack"}
)


def _is_frame_relative_call(call: ast.Call) -> bool:
    """Calls whose result depends on how many frames sit above them.

    ``warnings.warn(..., stacklevel=n)`` attributes the warning to the n-th
    caller; a helper adds one frame. Frame and stack inspection is likewise
    relative to the current frame. Only direct, recognizably named calls are
    detected; a callee that inspects frames internally is not.
    """
    callee = call.func
    name = callee.attr if isinstance(callee, ast.Attribute) else getattr(callee, "id", "")
    if name == "warn" and any(keyword.arg == "stacklevel" for keyword in call.keywords):
        return True
    return name in _FRAME_RELATIVE_CALLEES


class _LoopControlVisitor(OwnScopeVisitor):
    def __init__(self) -> None:
        self.depth = 0
        self.external = False

    def visit_Break(self, node: ast.Break) -> None:
        if self.depth == 0:
            self.external = True

    def visit_Continue(self, node: ast.Continue) -> None:
        if self.depth == 0:
            self.external = True

    def _visit_loop(self, node: Union[ast.For, ast.AsyncFor, ast.While]) -> None:
        self.depth += 1
        for statement in node.body:
            self.visit(statement)
        self.depth -= 1
        # A loop's else-suite is outside that loop's break/continue scope.
        for statement in node.orelse:
            self.visit(statement)

    visit_For = _visit_loop
    visit_AsyncFor = _visit_loop
    visit_While = _visit_loop

    def _nested_class(self, node: ast.ClassDef) -> None:
        """A loop inside a nested class body is not the block's loop."""


def has_external_loop_control(nodes: Iterable[ast.AST]) -> bool:
    """Whether break/continue targets a loop outside the extraction boundary."""

    visitor = _LoopControlVisitor()
    for statement in nodes:
        visitor.visit(statement)
    return visitor.external


K = TypeVar("K")
V = TypeVar("V")


class _Bounded(Generic[K, V]):
    """A small least-recently-used table; the newest entries survive."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._table: "OrderedDict[K, V]" = OrderedDict()

    def get(self, key: K) -> Optional[V]:
        if key in self._table:
            self._table.move_to_end(key)
            return self._table[key]
        return None

    def __contains__(self, key: K) -> bool:
        return key in self._table

    def put(self, key: K, value: V) -> V:
        self._table[key] = value
        self._table.move_to_end(key)
        while len(self._table) > self._limit:
            self._table.popitem(last=False)
        return value

    def clear(self) -> None:
        self._table.clear()


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
        self.edges: _Bounded[Tuple[Path, int, int, FrozenSet[Path]], Optional[FrozenSet[Path]]] = (
            _Bounded(limit)
        )
        self.bindings: _Bounded[Tuple[Path, int, int], Optional[Dict[str, Tuple[str, ...]]]] = (
            _Bounded(limit)
        )
        self.module_files: _Bounded[Tuple[Path, Tuple[str, ...]], FrozenSet[Path]] = _Bounded(limit)
        self.source_roots: _Bounded[Path, Tuple[Path, ...]] = _Bounded(limit)

    def clear(self) -> None:
        for table in (self.edges, self.bindings, self.module_files, self.source_roots):
            table.clear()


DEFAULT_IMPORT_GRAPH = ImportGraphCache()
"""For callers without an engine of their own."""


def _source_roots(path: Path, cache: ImportGraphCache) -> Optional[Tuple[Path, ...]]:
    """The project's source roots as seen from ``path``, or None when the layout is unknown."""
    roots = cache.source_roots.get(path)
    if roots is None:
        try:
            roots = tuple(ProjectLayout.discover(path).source_roots)
        except ValueError:
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
    """The topmost package directory containing ``module``."""
    directory = module.parent
    while (directory.parent / "__init__.py").is_file() and directory.parent != directory:
        directory = directory.parent
    return directory


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
        tree = ast.parse(current.read_text(encoding="utf-8"))
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
    try:
        tree = ast.parse(current.read_text(encoding="utf-8"))
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
                if alias.name == "*":
                    continue
                bindings[alias.asname or alias.name] = (*prefix, *module, alias.name)
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
    current_file: str, dotted_name: str, cache: ImportGraphCache = DEFAULT_IMPORT_GRAPH
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
    directory = module.parent
    while directory not in roots:
        initializer = directory / "__init__.py"
        if not initializer.is_file():
            break
        initializers.append(initializer.resolve())
        parent = directory.parent
        if parent == directory:
            break
        directory = parent
    return initializers


def would_create_import_cycle(
    canonical_file: str, replacement_files: Set[str], cache: ImportGraphCache = DEFAULT_IMPORT_GRAPH
) -> bool:
    """Check whether adding imports of the helper closes a local import cycle.

    Follow static imports through local modules, including modules without any
    candidate functions and package initializers. Imports inside functions are
    included conservatively. Dynamic imports cannot be resolved statically.
    """
    canonical = Path(canonical_file).resolve()
    targets = {Path(path).resolve() for path in replacement_files} - {canonical}
    if not targets:
        return False
    common_root = Path(os.path.commonpath([str(path.parent) for path in targets | {canonical}]))
    source_roots = cache.source_roots.get(canonical)
    if source_roots is None:
        source_roots = cache.source_roots.put(
            canonical, tuple(ProjectLayout.discover(canonical).source_roots)
        )
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


def bound_names(nodes: Iterable[ast.AST]) -> Set[str]:
    """Every name a node binds or unbinds, including inside nested scopes.

    This over-approximates scope: a nested function's local counts too. The
    guards below use it to decide whether a nested scope and the extracted
    block could share a binding, where over-approximation only rejects.
    """
    names: Set[str] = set()
    for statement in nodes:
        for node in ast.walk(statement):
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                names.add(node.id)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                names.add(node.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.match_case):
                names.update(pattern_capture_names(node.pattern))
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    if alias.name != "*":
                        names.add(alias.asname or alias.name.split(".")[0])
    return names


def _deleted_names(nodes: Iterable[ast.AST]) -> Set[str]:
    """Names a block unbinds: explicit ``del`` and implicit except-clause cleanup."""
    names: Set[str] = set()
    for statement in nodes:
        for node in ast.walk(statement):
            if isinstance(node, ast.Delete):
                for target in node.targets:
                    names.update(
                        child.id
                        for child in ast.walk(target)
                        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Del)
                    )
            elif isinstance(node, ast.ExceptHandler) and node.name:
                names.add(node.name)
    return names


def unbinds_external_name(
    function: Union[ast.FunctionDef, ast.AsyncFunctionDef],
    nodes: Iterable[ast.AST],
    bound_before: Set[str],
) -> bool:
    """Whether the block deletes a binding that outlives it.

    Deleting a helper parameter leaves the caller's variable bound; the
    original raised ``UnboundLocalError`` on the next read. ``except ... as e``
    deletes ``e`` when the handler exits, so it is a deletion too. Global and
    nonlocal names are rejected because the helper holds no such declaration.
    """
    deleted = _deleted_names(nodes)
    if not deleted:
        return False
    declared: Set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            declared.update(node.names)
    return bool(deleted & (bound_before | declared))


_NESTED_SCOPE_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.GeneratorExp)


def _loaded_names(node: ast.AST) -> Set[str]:
    return {
        child.id
        for child in ast.walk(node)
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
    }


class _ScopeFacts:
    """Nested scopes and top-level bindings of one function, computed once.

    The closure guard below runs once per candidate block; walking the whole
    function each time made it quadratic in the function's size per block.
    """

    def __init__(self, function: Union[ast.FunctionDef, ast.AsyncFunctionDef]) -> None:
        self.nested: Tuple[Tuple[ast.AST, FrozenSet[str]], ...] = tuple(
            (node, frozenset(_loaded_names(node)))
            for node in ast.walk(function)
            if isinstance(node, _NESTED_SCOPE_TYPES) and node is not function
        )
        self.top_level: Tuple[Tuple[ast.stmt, FrozenSet[str]], ...] = tuple(
            (statement, frozenset(bound_names([statement]))) for statement in function.body
        )


_SCOPE_FACTS: "WeakKeyDictionary[ast.AST, _ScopeFacts]" = WeakKeyDictionary()


def _scope_facts(function: Union[ast.FunctionDef, ast.AsyncFunctionDef]) -> _ScopeFacts:
    facts = _SCOPE_FACTS.get(function)
    if facts is None:
        facts = _ScopeFacts(function)
        _SCOPE_FACTS[function] = facts
    return facts


def nested_scopes_cross_block_boundary(
    function: Union[ast.FunctionDef, ast.AsyncFunctionDef], nodes: Iterable[ast.AST]
) -> bool:
    """Reject extraction when a closure and the block share a mutable binding.

    A nested function, lambda or generator reads its free names when it runs,
    not when it is defined. If it is defined outside the block and the block
    rebinds one of those names, the helper rebinds its own local instead of the
    caller's cell. If it is defined inside the block and the caller rebinds one
    of its free names after the block, the helper's parameter snapshot goes
    stale. Both cases are rejected; reads of names bound only before a
    top-level block remain eligible.
    """
    block = tuple(nodes)
    extracted = {child for statement in block for child in ast.walk(statement)}
    written_in_block = bound_names(block)
    facts = _scope_facts(function)
    if written_in_block and any(
        loaded & written_in_block for scope, loaded in facts.nested if scope not in extracted
    ):
        return True
    captured: Set[str] = set()
    inside = False
    for scope, loaded in facts.nested:
        if scope in extracted:
            inside = True
            captured.update(loaded)
    if not inside:
        return False
    captured -= written_in_block
    if not captured:
        return False
    top_level = all(node in function.body for node in block)
    block_end = max(getattr(node, "end_lineno", 0) or 0 for node in block)
    for statement, bound in facts.top_level:
        if statement in extracted:
            continue
        if top_level and (getattr(statement, "lineno", 0) or 0) <= block_end:
            continue
        if bound & captured:
            return True
    return False


def is_eagerly_evaluable(expression: ast.AST) -> bool:
    """Whether hoisting this expression to the call site is unobservable.

    A parameter argument runs once, before the block, even when the block would
    have evaluated it later, repeatedly, conditionally, or not at all. Only
    expressions with no effects, no failure modes, and no fresh identity may
    move that way: local names, literals, and tuples of those. Attribute
    access can run a property, subscripts and operators can call arbitrary
    methods, calls are effects by definition, and mutable displays allocate.
    """
    if isinstance(expression, (ast.Constant, ast.Name)):
        return True
    # A tuple of such values is immutable, so one evaluation is as good as
    # many. List, set and dict displays create a fresh mutable object each
    # time they run; hoisting one out of a loop would alias every iteration.
    if isinstance(expression, ast.Tuple):
        return all(is_eagerly_evaluable(element) for element in expression.elts)
    if isinstance(expression, ast.UnaryOp) and isinstance(expression.operand, ast.Constant):
        return isinstance(expression.op, (ast.USub, ast.UAdd, ast.Invert, ast.Not))
    return False


def has_impure_eager_parameters(substitution: "Substitution") -> bool:
    """Whether any eagerly passed parameter argument may be observable when hoisted.

    Lambda-lifted parameters and forwarded callees are evaluated inside the
    helper at the original position, so any expression is acceptable there.
    Call this after extraction, which is when callee parameters are known.
    """
    deferred = (
        set(substitution.function_params)
        | set(substitution.params_used_as_callee)
        | set(substitution.inlined_parameters)
    )
    return any(
        not is_eagerly_evaluable(expression)
        for name, expressions in substitution.param_expressions.items()
        if name not in deferred
        for _, expression in expressions
    )


def defer_impure_parameters(
    substitution: "Substitution", template_block: Iterable[ast.AST]
) -> None:
    """Turn parameters whose arguments cannot be hoisted into zero-argument thunks.

    The helper then evaluates ``__param_n()`` at the original position, as often
    and as conditionally as the block did. Parameters already lambda-lifted keep
    their arguments; parameters used only as callees are forwarded lazily by
    the extractor and need no thunk.
    """
    callees: Set[str] = set()
    for statement in template_block:
        for node in ast.walk(statement):
            if isinstance(node, ast.Call):
                parameter = substitution.get_param_for_expr(0, node.func)
                if parameter is not None:
                    callees.add(parameter)
    for name, expressions in substitution.param_expressions.items():
        if name in substitution.function_params or name in callees:
            continue
        if any(not is_eagerly_evaluable(expression) for _, expression in expressions):
            substitution.function_params[name] = []


def moves_scope_declaration(
    function: Union[ast.FunctionDef, ast.AsyncFunctionDef], nodes: Iterable[ast.AST]
) -> bool:
    """Whether the block carries a ``global``/``nonlocal`` declaration the caller still needs.

    A declaration inside the block moves into the helper with it. Any remaining
    use of that name in the caller then silently becomes a local access. Only
    declarations at the block's own scope count; a nested function's own
    declarations move with that function.
    """
    block = tuple(nodes)
    extracted = {child for statement in block for child in ast.walk(statement)}
    declared: Set[str] = set()
    for statement in block:
        for node in walk_own_scope(statement):
            if isinstance(node, (ast.Global, ast.Nonlocal)):
                declared.update(node.names)
    if not declared:
        return False
    for node in ast.walk(function):
        if node in extracted or node is function:
            continue
        if isinstance(node, ast.Name) and node.id in declared:
            return True
        if isinstance(node, (ast.Global, ast.Nonlocal)) and declared & set(node.names):
            return True
    return False


def walk_own_scope(node: ast.AST) -> Iterable[ast.AST]:
    """Yield nodes of ``node`` without entering nested function or class scopes."""
    pending = [node]
    while pending:
        current = pending.pop()
        yield current
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        pending.extend(ast.iter_child_nodes(current))
