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

"""Keep recognized resources through helper return, or decline the block.

This is a list of known constructors, not a proof of resource ownership.
A listed call must be managed by a whole with statement in the block or
assigned directly to a local that still holds its result when the block ends.
Weak-reference referents need the same stable ownership. Wrappers and
constructors outside the list still require strict mode or explicit management.
"""

from __future__ import annotations

import ast
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from itertools import chain
from pathlib import Path
from typing import FrozenSet, Iterator, Mapping, Optional, Sequence
from weakref import WeakKeyDictionary

from ..analysis_sources import (
    ParserConfiguration,
    parse_analysis_source,
    parser_configuration,
    parsing_is_pure,
)
from ..source_text import try_read_source
from .assignment_analyzer import own_scope_bindings
from .bounded_cache import BoundedCache, memoizing
from .import_graph import ImportGraphCache
from .resource_budget import WorkLimitReached
from .resource_reexports import ReexportSearch, reachable_constructors
from .module_bindings import dotted_name, import_origin
from .models import is_generated_helper_name
from .parameters import parameter_nodes
from .statement_facts import imported_binding_name, memoized_per_node
from .visitors import evaluated_before_definition

# Finalization can remove a path, descriptor, lock, worker, image or mapping
# which the program still uses through another route. Additions to this closed
# list require a decision; this recognizer does not infer arbitrary ownership.
RESOURCE_CONSTRUCTORS: FrozenSet[str] = frozenset(
    {
        "builtins.open",
        "io.open",
        "io.FileIO",
        "os.fdopen",
        "socket.socket",
        "socket.socketpair",
        "socket.create_connection",
        # Deleted from disk on finalization; the path outlives the object.
        "tempfile.TemporaryDirectory",
        "tempfile.NamedTemporaryFile",
        "tempfile.TemporaryFile",
        "tempfile.SpooledTemporaryFile",
        # Workers are terminated on finalization; results and iterators the
        # pool handed out outlive the object.
        "multiprocessing.Pool",
        "multiprocessing.pool.Pool",
        "multiprocessing.pool.ThreadPool",
        # The Tk image is deleted on finalization; widgets refer to it by name.
        "tkinter.PhotoImage",
        "tkinter.BitmapImage",
        # Unmapped on finalization; exported buffers and the file are not.
        "mmap.mmap",
        # Named by the decision. The module's registry keeps a finalizer alive,
        # so returning it costs a name and protects nothing; it stays listed
        # until the decision's list is revised.
        "weakref.finalize",
    }
)

# Constructors that watch their first argument without keeping it alive. The
# object watched is the one whose early release the program could see: a
# dead reference, a proxy that raises, a callback that has already run.
WEAK_REFERENCE_CONSTRUCTORS: FrozenSet[str] = frozenset(
    {
        "weakref.ref",
        "weakref.proxy",
        "weakref.WeakMethod",
        "weakref.WeakValueDictionary",
        "weakref.finalize",
    }
)


@dataclass(frozen=True)
class _Origin:
    position: tuple[int, int]
    spelling: Optional[str]
    imported: bool
    conditional: bool


@dataclass(frozen=True)
class _Scope:
    parent: Optional[int]
    bindings: Mapping[str, tuple[_Origin, ...]]
    parameters: FrozenSet[str]
    stars: FrozenSet[str]
    globals: FrozenSet[str]
    nonlocals: FrozenSet[str]


@dataclass(frozen=True)
class _Call:
    node: ast.Call
    origins: FrozenSet[str]
    helpers: FrozenSet[str]


@dataclass(frozen=True)
class _Resolution:
    scopes: Mapping[int, _Scope]
    locations: Mapping[int, int]

    # A statement's lexical lookups depend on the module in which it occurs.
    # Keep only descendant calls as values, so synthetic statements can expire.
    calls: WeakKeyDictionary[ast.AST, tuple[_Call, ...]] = field(default_factory=WeakKeyDictionary)

    def calls_in(self, statement: ast.stmt) -> tuple[_Call, ...]:
        return memoized_per_node(self.calls, statement, lambda node: _calls_in(node, self))

    def origins(
        self,
        spelling: str,
        scope: int,
        position: tuple[int, int],
        depth: int = 0,
        *,
        deferred: bool = False,
    ) -> FrozenSet[str]:
        if depth >= 16:
            return frozenset()
        head, _, tail = spelling.partition(".")
        current = self.scopes[scope]
        events = current.bindings.get(head, ())
        earlier = [event for event in events if event.position < position]
        # A definite assignment supersedes previous imports; a conditional one
        # retains both possibilities. A parameter/local never falls through to
        # a same-spelled global import, even before its first assignment.
        last = (
            0
            if deferred
            else next((i for i in range(len(earlier) - 1, -1, -1) if not earlier[i].conditional), 0)
        )
        # Deferred lookups may run before a later shadowing assignment (or
        # after a deletion restores a builtin), so retain every possibility.
        found: set[str] = (
            {f"builtins.{spelling}"} if deferred and scope == 0 and head == "open" else set()
        )
        for event in earlier[last:]:
            if event.spelling is None:
                continue
            targets = (
                frozenset({event.spelling})
                if event.imported
                else self.origins(event.spelling, scope, event.position, depth + 1)
            )
            found.update(f"{target}.{tail}" if tail else target for target in targets)
        if earlier:
            return frozenset(found)
        if head in current.globals and scope:
            return self.origins(spelling, 0, (10**9, 0), depth + 1, deferred=True)
        if head in current.nonlocals and current.parent is not None:
            return self.origins(spelling, current.parent, (10**9, 0), depth + 1, deferred=True)
        if events or head in current.parameters:
            return frozenset(found)
        if current.parent is not None:
            return self.origins(spelling, current.parent, (10**9, 0), depth + 1, deferred=True)
        found.update(f"{prefix}{spelling}" for prefix in current.stars)
        if head == "open":
            found.add(f"builtins.{spelling}")
        return frozenset(found)


def _position(node: ast.AST) -> tuple[int, int]:
    return getattr(node, "lineno", 0), getattr(node, "col_offset", 0)


def _resolution(module: ast.AST) -> _Resolution:
    scopes: dict[int, _Scope] = {}
    locations: dict[int, int] = {}

    def scope_of(node: ast.AST, parent: Optional[int], key: int) -> None:
        body = getattr(node, "body", [])
        if not isinstance(body, list):
            body = [body]
        origins: dict[str, list[_Origin]] = {}
        stars: set[str] = set()
        globals_: set[str] = set()
        nonlocals: set[str] = set()
        nested: list[ast.AST] = []
        recorded: set[int] = set()
        parameters = (
            frozenset(arg.arg for arg in parameter_nodes(node.args))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
            else frozenset()
        )
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            parameters = frozenset(
                target.id
                for generator in node.generators
                for target in ast.walk(generator.target)
                if isinstance(target, ast.Name)
            )
            body = [condition for generator in node.generators for condition in generator.ifs]
            body += [generator.iter for generator in node.generators[1:]]
            # Unpacking dict comprehensions have no value on Python 3.15.
            elements = [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
            body += [element for element in elements if element is not None]

        def visit(current: ast.AST, conditional: bool = False) -> None:
            locations[id(current)] = key
            if isinstance(current, (ast.Global, ast.Nonlocal)):
                (globals_ if isinstance(current, ast.Global) else nonlocals).update(current.names)
            # Record only bindings made by this node, not its descendants.
            if isinstance(current, ast.Name) and isinstance(current.ctx, (ast.Store, ast.Del)):
                recorded.add(id(current))
                origins.setdefault(current.id, []).append(
                    _Origin(_position(current), None, False, conditional)
                )
            elif isinstance(current, (ast.Import, ast.ImportFrom)):
                for alias in current.names:
                    name = imported_binding_name(alias)
                    if name is not None:
                        recorded.add(id(alias))
                        origins.setdefault(name, []).append(
                            _Origin(
                                _position(current),
                                (
                                    "." * current.level
                                    + (current.module + "." if current.module else "")
                                    + alias.name
                                    if isinstance(current, ast.ImportFrom) and current.level
                                    else import_origin(current, alias)
                                ),
                                True,
                                conditional,
                            )
                        )
                    elif isinstance(current, ast.ImportFrom):
                        stars.add(
                            "." * current.level + (current.module + "." if current.module else "")
                        )
            elif isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                recorded.add(id(current))
                for expression in evaluated_before_definition(current):
                    visit(expression, conditional)
                origins.setdefault(current.name, []).append(
                    _Origin(_position(current), None, False, conditional)
                )
                nested.append(current)
                return
            elif isinstance(current, ast.Lambda):
                for expression in evaluated_before_definition(current):
                    visit(expression, conditional)
                nested.append(current)
                return
            elif isinstance(current, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                visit(current.generators[0].iter, conditional)
                nested.append(current)
                return
            for child in ast.iter_child_nodes(current):
                visit(
                    child,
                    conditional
                    or isinstance(
                        current,
                        (
                            ast.If,
                            ast.For,
                            ast.AsyncFor,
                            ast.While,
                            ast.Try,
                            ast.TryStar,
                            ast.With,
                            ast.AsyncWith,
                            ast.Match,
                            ast.BoolOp,
                            ast.IfExp,
                            ast.Assert,
                        ),
                    ),
                )
            if isinstance(current, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                value = current.value
                copied = dotted_name(value) if value is not None else None
                targets = current.targets if isinstance(current, ast.Assign) else [current.target]
                for target in targets:
                    if isinstance(target, ast.Name) and value is not None:
                        # Replace the store entry with the value's import origin.
                        entries = origins[target.id]
                        entries[-1] = _Origin(_position(current), copied, False, conditional)

        for statement in body:
            visit(statement)
        # Handler and pattern names are strings in the AST, not Name stores.
        positions = {
            id(child): _position(child) for statement in body for child in ast.walk(statement)
        }
        for binding in own_scope_bindings(body):
            if binding.node_id not in recorded:
                origins.setdefault(binding.name, []).append(
                    _Origin(positions.get(binding.node_id, (0, 0)), None, False, True)
                )
        scopes[key] = _Scope(
            parent,
            {
                name: tuple(sorted(events, key=lambda e: e.position))
                for name, events in origins.items()
            },
            parameters,
            frozenset(stars),
            frozenset(globals_),
            frozenset(nonlocals),
        )
        for child in nested:
            # A method's globals are outside the class namespace.
            enclosing = parent if isinstance(node, ast.ClassDef) else key
            scope_of(child, enclosing, id(child))

    scope_of(module, None, 0)
    return _Resolution(scopes, locations)


_AnswerKey = tuple[Path, FrozenSet[str], ParserConfiguration]


@dataclass(frozen=True)
class _StageAnswers:
    graph: ImportGraphCache
    root: Path
    answers: BoundedCache[_AnswerKey, FrozenSet[str]]
    searches: dict[ParserConfiguration, ReexportSearch] = field(default_factory=dict)
    programs: BoundedCache[Path, Path] = field(default_factory=lambda: BoundedCache(2048))


_STAGE_ANSWERS: ContextVar[Optional[_StageAnswers]] = ContextVar("resource_answers", default=None)


@contextmanager
def sharing_resource_answers(graph: ImportGraphCache, stage: Optional[Path]) -> Iterator[None]:
    """The engine reads its private stage throughout one analysis. It can write between analyses."""
    answers = _StageAnswers(graph, stage, BoundedCache(2048)) if stage is not None else None
    token = _STAGE_ANSWERS.set(answers)
    try:
        yield
    finally:
        _STAGE_ANSWERS.reset(token)


@dataclass(frozen=True)
class ResourceImports:
    """The importing file and the engine's current project import graph."""

    file_path: str
    cache: ImportGraphCache

    def follow(self, origins: FrozenSet[str]) -> FrozenSet[str]:
        """The recognized constructors reachable from these call origins."""
        if not origins or origins <= _RECOGNIZED:
            return origins
        stage = _STAGE_ANSWERS.get()
        configuration = parser_configuration()
        if stage is not None and (stage.graph is not self.cache or not memoizing()):
            stage = None
        file = (
            self.cache.resolve(self.file_path)
            if stage is not None
            else Path(self.file_path).resolve()
        )
        if stage is not None:
            scope: Optional[Path]
            if any(origin.startswith(".") for origin in origins):
                scope = file.parent
            else:
                scope = stage.programs.get(file)
                if scope is None:
                    scope = stage.programs[file] = self.cache.program_root_for(file)
            key = (scope, origins, configuration)
            previous = stage.answers.get(key)
            if previous is not None:
                return previous
        if self.cache.resource_budget.exhausted:
            return _MAY_BE_RESOURCE
        # Take ownership until completion. No interrupted or impure lookup
        # may leave a partially expanded search for the next caller.
        search = stage.searches.pop(configuration, None) if stage is not None else None
        if search is None:
            search = ReexportSearch(self.cache, _RECOGNIZED, stage.root if stage else None)

        def exported(file: Path, name: str) -> FrozenSet[str]:
            nonlocal stage
            source = try_read_source(file)
            if stage is not None and (
                source is None or not file.is_relative_to(stage.root) or not parsing_is_pure(source)
            ):
                stage = None
            if source is None:
                return frozenset()
            try:
                module = parse_analysis_source(source, str(file))
            except SyntaxError:
                return frozenset()
            resolution = memoized_per_node(_RESOLUTIONS, module, _resolution)
            return resolution.origins(name, 0, (10**9, 0), deferred=True)

        try:
            found = reachable_constructors(
                file, origins, self.cache, _RECOGNIZED, exported, search=search
            )
        except WorkLimitReached:
            return _MAY_BE_RESOURCE
        if stage is not None:
            stage.searches[configuration] = search
            return stage.answers.put(key, found)
        return found


_RECOGNIZED = RESOURCE_CONSTRUCTORS | WEAK_REFERENCE_CONSTRUCTORS
# The unknown call must be refused even in a with statement: it might watch
# its argument weakly. A union of known constructors alone loses that fact.
_MAY_BE_RESOURCE = _RECOGNIZED | {"<re-export work limit>"}


_RESOLUTIONS: WeakKeyDictionary[ast.AST, _Resolution] = WeakKeyDictionary()
_NONOWNING_HELPERS: WeakKeyDictionary[ast.AST, FrozenSet[str]] = WeakKeyDictionary()


@dataclass(frozen=True)
class ResourceGuard:
    names: FrozenSet[str] = frozenset()
    refusal: Optional[str] = None


def _own_scope(node: ast.AST) -> Iterator[ast.AST]:
    yield node
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
        for expression in evaluated_before_definition(node):
            yield from _own_scope(expression)
        return
    for child in ast.iter_child_nodes(node):
        yield from _own_scope(child)


def _calls_in(statement: ast.AST, resolution: Optional[_Resolution]) -> tuple[_Call, ...]:
    calls = []
    for node in _own_scope(statement):
        if not isinstance(node, ast.Call):
            continue
        spelling = dotted_name(node.func)
        if spelling is None:
            continue
        origins = (
            resolution.origins(spelling, resolution.locations.get(id(node), 0), _position(node))
            if resolution is not None
            else frozenset({"builtins.open" if spelling == "open" else spelling})
        )
        helpers = frozenset(
            origin
            for origin in {*origins, spelling}
            if is_generated_helper_name(origin.rsplit(".", 1)[-1])
        )
        if origins or helpers:
            calls.append(_Call(node, origins, helpers))
    return tuple(calls)


@dataclass(frozen=True)
class _HelperBody:
    name: str
    callees: FrozenSet[str]
    # Each call's lexical origins and whether a with statement manages its result.
    calls: tuple[tuple[FrozenSet[str], bool], ...]


_HELPER_BODIES: WeakKeyDictionary[ast.AST, tuple[_HelperBody, ...]] = WeakKeyDictionary()


def _helper_bodies(module: ast.AST) -> tuple[_HelperBody, ...]:
    resolution = memoized_per_node(_RESOLUTIONS, module, _resolution)
    bodies = []
    for helper in ast.walk(module):
        if not (
            isinstance(helper, (ast.FunctionDef, ast.AsyncFunctionDef))
            and is_generated_helper_name(helper.name)
        ):
            continue
        nodes = [node for statement in helper.body for node in _own_scope(statement)]
        managed = {node.context_expr for node in nodes if isinstance(node, ast.withitem)}
        calls = [call for statement in helper.body for call in resolution.calls_in(statement)]
        bodies.append(
            _HelperBody(
                helper.name,
                frozenset(name for call in calls for name in call.helpers),
                tuple((call.origins, call.node in managed) for call in calls),
            )
        )
    return tuple(bodies)


def _nonowning_helpers(
    module: ast.AST, imports: Optional[ResourceImports] = None
) -> FrozenSet[str]:
    """Retain the guard's guarantee across subsequent extraction passes.

    A helper's result can contain an owner the first pass deliberately kept.
    Until returned ownership is summarized by position, another helper must
    not swallow that call and drop its owner. Imported generated helpers are
    also left in place when their bodies are unavailable in this module.
    Ordinary factories are outside this deliberately syntactic guard.
    """
    helpers = memoized_per_node(_HELPER_BODIES, module, _helper_bodies)
    known = {helper.name for helper in helpers}
    owning: set[str] = set()
    dependencies: dict[str, set[str]] = {}
    for helper in helpers:
        dependencies.setdefault(helper.name, set()).update(helper.callees)
        for lexical, managed in helper.calls:
            origins = lexical if imports is None else imports.follow(lexical)
            if origins & WEAK_REFERENCE_CONSTRUCTORS or (
                origins & RESOURCE_CONSTRUCTORS and not managed
            ):
                owning.add(helper.name)
    while True:
        expanded = owning | {
            name for name, calls in dependencies.items() if calls & owning or calls - known
        }
        if expanded == owning:
            return frozenset(known - owning)
        owning = expanded


def _guarded_calls(
    calls: Iterator[_Call],
    module: Optional[ast.AST],
    imports: Optional[ResourceImports],
) -> Iterator[tuple[ast.Call, Optional[FrozenSet[str]]]]:
    """Calls whose lifetimes matter, in order. None means refuse the generated helper."""
    nonowning: Optional[FrozenSet[str]] = None
    for call in calls:
        if call.helpers:
            if nonowning is None and module is not None:
                nonowning = (
                    memoized_per_node(_NONOWNING_HELPERS, module, _nonowning_helpers)
                    if imports is None
                    else _nonowning_helpers(module, imports)
                )
            if nonowning is None or not call.helpers <= nonowning:
                yield call.node, None
                return
        origins = call.origins if imports is None else imports.follow(call.origins)
        if origins & _RECOGNIZED:
            yield call.node, origins


def resource_guard(
    block: Sequence[ast.stmt],
    module: Optional[ast.AST],
    imports: Optional[ResourceImports] = None,
) -> ResourceGuard:
    """Each recognized call is managed, retained by a stable name, or refused.

    Rebinding is conservatively checked across all paths. Returning a stable
    alias of a rebound direct target is deliberately not an ownership proof.
    The index caches immutable facts without retaining its weak-key AST.
    """
    resolution = (
        memoized_per_node(_RESOLUTIONS, module, _resolution) if module is not None else None
    )
    lexical_calls = (
        call
        for statement in block
        for call in (resolution.calls_in(statement) if resolution else _calls_in(statement, None))
    )
    calls = _guarded_calls(lexical_calls, module, imports)
    first = next(calls, None)
    if first is None:
        return ResourceGuard()
    nodes = [node for statement in block for node in _own_scope(statement)]
    parents = {id(child): node for node in nodes for child in ast.iter_child_nodes(node)}
    positions = {id(node): _position(node) for node in nodes}
    # A smaller prefix must not evade a refusal by leaving the rebinding in
    # the caller: aliases created by that prefix would otherwise disappear.
    owner = (
        next(
            (
                candidate
                for candidate in ast.walk(module)
                if isinstance(candidate, (ast.FunctionDef, ast.AsyncFunctionDef))
                and block
                and resolution is not None
                and id(candidate) == resolution.locations.get(id(block[0]))
            ),
            None,
        )
        if module is not None
        else None
    )
    lifetime_nodes = (
        [node for statement in owner.body for node in _own_scope(statement)]
        if owner is not None
        else nodes
    )
    lifetime_parents = {
        id(child): node for node in lifetime_nodes for child in ast.iter_child_nodes(node)
    }
    positions.update({id(node): _position(node) for node in lifetime_nodes})
    bindings = own_scope_bindings(owner.body if owner is not None else block)
    names: set[str] = set()
    captured_writes = (
        frozenset(
            name
            for descendant in ast.walk(owner)
            if isinstance(descendant, ast.Nonlocal)
            for name in descendant.names
        )
        if owner is not None
        else frozenset()
    )

    def stable(name: str, call: ast.Call, binding: Optional[ast.Name] = None) -> bool:
        return (
            name not in captured_writes
            and not any(
                event.name == name
                and event.node_id != (id(binding) if binding else None)
                and positions.get(event.node_id, (10**9, 0)) >= _position(call)
                for event in bindings
            )
            and not any(
                isinstance(node, ast.Name)
                and node.id == name
                and isinstance(node.ctx, ast.Del)
                and _position(node) > _position(call)
                for node in lifetime_nodes
            )
        )

    for node, origins in chain((first,), calls):
        if origins is None:
            return ResourceGuard(
                refusal=(
                    f"This block calls a generated helper (line {node.lineno}) whose resource ownership "
                    "Towel cannot preserve through another helper. Keep this call in place, or use `--strict`."
                )
            )
        if origins is _MAY_BE_RESOURCE:
            return ResourceGuard(
                refusal=(
                    f"This call (line {node.lineno}) may be a resource. Towel reached its re-export "
                    "work limit and cannot prove that the resource stays alive in a helper."
                )
            )
        resource = bool(origins & RESOURCE_CONSTRUCTORS)
        weak = bool(origins & WEAK_REFERENCE_CONSTRUCTORS)
        refusal = (
            f"This block creates or weakly references a resource (line {node.lineno}) "
            "that later code may depend on, and Towel cannot keep it alive in a helper. "
            "Use a `with` statement, or `--strict`."
        )
        parent = parents.get(id(node))
        if resource and isinstance(parent, ast.withitem) and parent.context_expr is node:
            continue
        # Textual positions cannot prove stability across a loop back edge.
        # Include loops outside a candidate prefix and comprehension bodies:
        # their generators are siblings of elt/key/value, not ancestors.
        ancestor = lifetime_parents.get(id(node))
        while ancestor is not None:
            if isinstance(
                ancestor,
                (
                    ast.For,
                    ast.AsyncFor,
                    ast.While,
                    ast.ListComp,
                    ast.SetComp,
                    ast.DictComp,
                    ast.GeneratorExp,
                ),
            ):
                return ResourceGuard(refusal=refusal)
            ancestor = lifetime_parents.get(id(ancestor))
        if resource:
            targets: Sequence[ast.expr] = ()
            if isinstance(parent, ast.Assign) and parent.value is node:
                targets = parent.targets
            elif isinstance(parent, (ast.AnnAssign, ast.NamedExpr)) and parent.value is node:
                targets = [parent.target]
            if not targets or any(not isinstance(target, ast.Name) for target in targets):
                return ResourceGuard(refusal=refusal)
            for target in targets:
                assert isinstance(target, ast.Name)
                if not stable(target.id, node, target):
                    return ResourceGuard(refusal=refusal)
                names.add(target.id)
        if weak:
            referent = (
                node.args[0]
                if node.args
                else next(
                    (
                        keyword.value
                        for keyword in node.keywords
                        if keyword.arg == "meth" and "weakref.WeakMethod" in origins
                    ),
                    None,
                )
            )
            if "weakref.WeakMethod" in origins and isinstance(referent, ast.Attribute):
                referent = referent.value
            if not isinstance(referent, ast.Name) or not stable(referent.id, node):
                return ResourceGuard(refusal=refusal)
            names.add(referent.id)
    return ResourceGuard(frozenset(names))


def resource_bound_names(
    block: Sequence[ast.stmt],
    module: Optional[ast.AST],
    imports: Optional[ResourceImports] = None,
) -> FrozenSet[str]:
    """Stable resource names; callers must also check the guard's refusal."""
    return resource_guard(block, module, imports).names
