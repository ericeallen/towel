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

"""Which names in a block denote a callee that reads the source or position of its call.

inline-snapshot's ``snapshot("<item 1>")`` reads the literal where it is
called and keys the snapshot by that position; moved into a helper as
``snapshot(__param_1)``, one position sees two values and raises
``UsageError`` (rich-click: 68 of 151 tests). A block is therefore declined
(``source_reading_callee``) when it refers to a callee of
``KNOWN_SOURCE_READERS``, whether or not an argument of the call would be
parameterized: the move alone changes what the callee reads.

Names are resolved by binding: ``from inline_snapshot import snapshot as
snap`` makes ``snap(...)`` a reader, as do ``import inline_snapshot as ins;
ins.snapshot(...)``, a star import of the library, ``s = snapshot`` and a
parameter defaulting to it. Every import and plain assignment in the module
counts, at any depth, so a local that shadows such a name is taken for it;
that only declines.

``snapshot_arg()`` reads the call of the function it is called from. A
function or method of the module whose own body calls it reads its own call
in turn, and so, transitively, does one that calls such a function, and a
class whose ``__init__`` or ``__new__`` does. They are matched by name, bare
or as an attribute on any receiver, which over-approximates and only
declines.

Every other callee that reads its caller's frame or source is reflection,
which Towel does not model (docs/KNOWN_LIMITATIONS.md), and so is a listed
reader reached through a value (a parameter, a container, ``getattr``) or
re-exported by another module of the project.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from types import MappingProxyType
from typing import Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Set, Tuple, Union
from weakref import WeakKeyDictionary

from .known_source_readers import (
    KINDS_AT_OR_BELOW,
    KNOWN_SOURCE_READERS,
    KnownSourceReader,
    known_source_reader,
    nearest_listed,
    prefixes,
)
from .models import FunctionNode
from .module_bindings import import_origin
from .scope_analyzer import ScopeAnalyzer
from .semantic_safety import walk_own_scope
from .statement_facts import memoized_per_node

_CALLED = "()"
"""The part of a spelling that stands for calling what precedes it: ``f()`` is ``f`` and ``()``."""

_DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_CONSTRUCTORS = frozenset({"__init__", "__new__"})
"""A class whose constructor reads its call is a reader under the class's name."""

Definition = Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef]


@dataclass(frozen=True)
class ModuleSourceReaders:
    """What a module's names may denote among the listed readers.

    ``origins`` maps each name an import or a plain assignment binds,
    anywhere in the module, to every listed name it may denote, as
    ``nearest_listed`` keeps it; ``star_imported`` holds the modules
    star-imported without a level. ``project_readers`` maps the name of each
    definition of the module that reads its own call to the listed reader
    that makes it one. ``roots`` holds the names that may begin a spelling
    of a listed reader; a spelling rooted anywhere else is none.
    """

    origins: Mapping[str, FrozenSet[str]]
    star_imported: FrozenSet[str]
    project_readers: Mapping[str, str]
    roots: FrozenSet[str]

    def denotations(self, root: str, suffix: str) -> Set[str]:
        """The absolute names ``root`` followed by ``suffix`` may denote."""
        return {f"{head}{suffix}" for head in _heads(root, self.origins, self.star_imported)}


def _heads(
    root: str, origins: Mapping[str, Iterable[str]], star_imported: Iterable[str]
) -> Set[str]:
    """What the name ``root`` may denote: what it was imported or assigned as, or a star import's."""
    heads: Set[str] = set(origins.get(root, ()))
    heads.update(f"{module}.{root}" for module in star_imported)
    return heads


_BY_MODULE: "WeakKeyDictionary[ast.AST, ModuleSourceReaders]" = WeakKeyDictionary()


def module_source_readers(module: Optional[ast.AST]) -> ModuleSourceReaders:
    """The module's resolution of the listed readers, computed once per tree.

    With no module (an analyzer that never ran), nothing is bound, so
    nothing is a reader.
    """
    if module is None:
        return _analyzed(ast.Module(body=[], type_ignores=[]))
    known = _BY_MODULE.get(module)
    if known is None:
        known = _BY_MODULE[module] = _analyzed(module)
    return known


def _analyzed(module: ast.AST) -> ModuleSourceReaders:
    found: Dict[str, Set[str]] = {}
    star_imported: Set[str] = set()
    aliases: List[Tuple[str, ast.expr]] = []
    definitions: List[Definition] = []
    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            for alias in node.names:
                origin = import_origin(node, alias)
                if origin is not None:
                    _keep(found, alias.asname or alias.name.split(".")[0], [origin])
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module:
                continue
            for alias in node.names:
                if alias.name == "*":
                    star_imported.add(node.module)
                else:
                    _keep(found, alias.asname or alias.name, [f"{node.module}.{alias.name}"])
        else:
            aliases.extend(_alias_pairs(node))
            if isinstance(node, _DEFINITIONS):
                definitions.append(node)
    origins = _aliased(found, aliases, frozenset(star_imported))
    roots = set(origins)
    roots.update(
        entry.origin[len(star) + 1 :].split(".")[0]
        for star in star_imported
        for entry in KNOWN_SOURCE_READERS
        if entry.origin.startswith(f"{star}.")
    )
    partial = ModuleSourceReaders(
        origins, frozenset(star_imported), MappingProxyType({}), frozenset(roots)
    )
    passes_on = any(
        "caller" in KINDS_AT_OR_BELOW.get(prefix, ())
        for root in roots
        for head in _heads(root, origins, star_imported)
        for prefix in prefixes(head)
    )
    project = _project_readers(definitions, partial) if passes_on else {}
    return ModuleSourceReaders(
        partial.origins, partial.star_imported, MappingProxyType(project), partial.roots
    )


def _keep(found: Dict[str, Set[str]], name: str, origins: Iterable[str]) -> bool:
    """Record what ``name`` may denote among ``origins``, as the list cares; whether that grew.

    Only names above or below a listed reader are kept, each as
    ``nearest_listed`` gives it, so there are finitely many and a fixed
    point over aliases is reached however a chain is rebound in a loop
    (``node = node.next``).
    """
    kept = {nearest for origin in origins if (nearest := nearest_listed(origin)) is not None}
    known = found.setdefault(name, set())
    grew = not kept <= known
    known.update(kept)
    return grew


def _aliased(
    found: Dict[str, Set[str]],
    aliases: Sequence[Tuple[str, ast.expr]],
    star_imported: FrozenSet[str],
) -> Mapping[str, FrozenSet[str]]:
    """The imports' names with what every alias of them may denote, to a fixed point.

    ``k = a.b``, ``k: T = a.b``, ``(k := a.b)``, ``k = f()`` (what the call
    returns), a pairwise tuple assignment, and a parameter's default each
    make ``k`` denote whatever the value may.
    """
    spelled = [(target, _spelling(value)) for target, value in aliases]
    relevant = [(target, value) for target, value in spelled if value is not None]
    grew = True
    while grew:
        grew = False
        for target, (root, suffix) in relevant:
            heads = _heads(root, found, star_imported)
            if heads:
                grew = _keep(found, target, [f"{head}{suffix}" for head in heads]) or grew
    return MappingProxyType({name: frozenset(heads) for name, heads in found.items() if heads})


def _alias_pairs(node: ast.AST) -> List[Tuple[str, ast.expr]]:
    """The (name, value) pairs ``node`` binds by a plain assignment, a walrus or a default."""
    if isinstance(node, ast.Assign):
        return [pair for target in node.targets for pair in _assigned(target, node.value)]
    if isinstance(node, ast.AnnAssign) and node.value is not None:
        return _assigned(node.target, node.value)
    if isinstance(node, ast.NamedExpr):
        return _assigned(node.target, node.value)
    if isinstance(node, (*_FUNCTIONS, ast.Lambda)):
        arguments = node.args
        positional = [*arguments.posonlyargs, *arguments.args]
        pairs = [
            (argument.arg, default)
            for argument, default in zip(
                positional[len(positional) - len(arguments.defaults) :], arguments.defaults
            )
        ]
        pairs.extend(
            (argument.arg, default)
            for argument, default in zip(arguments.kwonlyargs, arguments.kw_defaults)
            if default is not None
        )
        return pairs
    return []


def _assigned(target: ast.expr, value: ast.expr) -> List[Tuple[str, ast.expr]]:
    if isinstance(target, ast.Name):
        return [(target.id, value)]
    if (
        isinstance(target, (ast.Tuple, ast.List))
        and isinstance(value, (ast.Tuple, ast.List))
        and len(target.elts) == len(value.elts)
    ):
        return [
            pair for inner, item in zip(target.elts, value.elts) for pair in _assigned(inner, item)
        ]
    return []


def _spelling(node: ast.AST) -> Optional[Tuple[str, str]]:
    """``a.b().c`` as its root name and the rest, ``("a", ".b().c")``; None for any other root."""
    parts: List[str] = []
    while True:
        if isinstance(node, ast.Attribute):
            parts.append(f".{node.attr}")
            node = node.value
        elif isinstance(node, ast.Call):
            parts.append(_CALLED)
            node = node.func
        elif isinstance(node, ast.Name):
            return node.id, "".join(reversed(parts))
        else:
            return None


def _listed(candidate: str) -> Optional[KnownSourceReader]:
    """The entry ``candidate`` is, or is an attribute or call result of; None if there is none.

    What a reader returns or holds is taken to read as well, which only
    declines.
    """
    for prefix in prefixes(candidate):
        entry = known_source_reader(prefix)
        if entry is not None:
            return entry
    return None


class _Resolution:
    """The readers the code of one module refers to."""

    def __init__(self, readers: ModuleSourceReaders) -> None:
        self._readers = readers

    def mentions(self, node: ast.AST) -> bool:
        """Whether ``node`` spells a name that may begin a reader.

        The cheap test of a whole scope before ``reader`` looks at each of its
        nodes: a reader is spelled from a root name that mentions one, or by
        an attribute that does, so code where no node mentions one refers to
        none.
        """
        project = self._readers.project_readers
        if isinstance(node, ast.Name):
            return node.id in self._readers.roots or node.id in project
        return isinstance(node, ast.Attribute) and node.attr in project

    def reader(self, node: ast.AST) -> Optional[str]:
        """The listed reader ``node`` refers to, named by its entry; None when it refers to none."""
        project = self._readers.project_readers
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in project:
            return project[node.id]
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.ctx, ast.Load)
            and node.attr in project
        ):
            return project[node.attr]
        if not isinstance(node, (ast.Name, ast.Attribute, ast.Call)):
            return None
        if not isinstance(node, ast.Call) and not isinstance(node.ctx, ast.Load):
            return None
        spelled = _spelling(node)
        if spelled is None or spelled[0] not in self._readers.roots:
            return None
        for candidate in sorted(self._readers.denotations(*spelled)):
            entry = _listed(candidate)
            if entry is not None:
                return entry.origin
        return None

    def first(self, statement: ast.AST) -> Optional[str]:
        """The first reader ``statement`` refers to, at any depth."""
        nodes = list(ast.walk(statement))
        if not any(map(self.mentions, nodes)):
            return None
        for node in nodes:
            found = self.reader(node)
            if found is not None:
                return found
        return None


def _project_readers(
    definitions: Sequence[Definition], readers: ModuleSourceReaders
) -> Dict[str, str]:
    """The module's definitions that read their own call, by name, each with the entry to blame.

    A function whose own scope refers to a ``caller`` reader reads its own
    call, and so does one whose own scope calls such a function, to a fixed
    point. A class reads its call when its ``__init__`` or ``__new__`` does.
    """
    functions = [node for node in definitions if isinstance(node, _FUNCTIONS)]
    own = {
        id(function): [item for statement in function.body for item in walk_own_scope(statement)]
        for function in functions
    }
    resolution = _Resolution(readers)
    found: Dict[str, str] = {}
    for function in functions:
        if not any(map(resolution.mentions, own[id(function)])):
            continue
        for item in own[id(function)]:
            origin = resolution.reader(item)
            entry = None if origin is None else known_source_reader(origin)
            if entry is not None and entry.kind == "caller":
                found.setdefault(function.name, entry.origin)
                break
    grew = bool(found)
    while grew:
        grew = False
        for function in functions:
            if function.name in found:
                continue
            for item in own[id(function)]:
                name = (
                    item.id
                    if isinstance(item, ast.Name)
                    else item.attr if isinstance(item, ast.Attribute) else None
                )
                if name is not None and name in found:
                    found[function.name] = found[name]
                    grew = True
                    break
    for definition in definitions:
        if isinstance(definition, ast.ClassDef):
            for statement in definition.body:
                if isinstance(statement, _FUNCTIONS) and statement.name in _CONSTRUCTORS:
                    blamed = found.get(statement.name)
                    if blamed is not None:
                        found.setdefault(definition.name, blamed)
    return found


_READER_OF: "WeakKeyDictionary[ast.AST, str]" = WeakKeyDictionary()
"""Each statement's reader, or ``""`` for none.

A statement's verdict depends on it and its module, which are fixed for as
long as it lives, and a statement belongs to every block that spans it."""


def source_reader_in(analyzer: Optional[ScopeAnalyzer], nodes: Sequence[ast.stmt]) -> Optional[str]:
    """The listed reader the block refers to, named by its entry; None when it refers to none."""
    resolution = _Resolution(
        module_source_readers(None if analyzer is None else analyzer.analyzed_tree)
    )
    for statement in nodes:
        found = memoized_per_node(_READER_OF, statement, lambda item: resolution.first(item) or "")
        if found:
            return found
    return None


def calls_source_reader(
    analyzer: Optional[ScopeAnalyzer], function: FunctionNode, nodes: Sequence[ast.stmt]
) -> bool:
    """Whether the block refers to a listed reader (the guard-memo form)."""
    return source_reader_in(analyzer, nodes) is not None
