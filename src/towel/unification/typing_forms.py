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

"""What a module's callees denote among the typing forms, resolved through its bindings.

A checker takes ``cast(Alpha, value)`` for typing's cast exactly when
``cast`` is bound to typing's object, however it is spelled: ``from typing
import cast as c`` makes ``c(Alpha, value)`` one, ``import typing as t``
makes ``t.cast`` one, and ``typing_extensions`` holds the same objects. A
project's own ``cast`` is an ordinary function, whose arguments may become
parameters: sqlglot's ``exp.cast(column, to)``, SQLAlchemy's ``cast(column,
Integer)``. So each dotted callee a block spells is resolved the way its
module binds it (``TypingFormResolver.forms_of``):

- through the module's globals on every path the module may take
  (``ModuleBindings.possible_origins``), and through the imports and plain
  aliases of its function and class bodies, whichever scope a block is in;
- an absolute import is taken at its word (``typing.cast``,
  ``sqlalchemy.cast``) unless it names a module of the project, which may
  re-export a form from a ``compat`` module of its own and is followed there,
  as a relative import is (``imported_definition_sites``);
- a star import from typing may bind any form, and one from anywhere else
  any name spelled as one.

Where a binding cannot be followed, because the import is conditional or sits
in a function body, the module does not parse, or the project's layout is
unknown, a spelling whose last name is a form's is taken to be that form:
pinning a form costs only an extraction, and missing one writes code no
checker accepts. A form a module of the project re-exports under a name of
its own (``L`` for ``Literal``) is not followed; re-exports keep the names
typing gives the forms.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Dict, FrozenSet, List, Mapping, NamedTuple, Optional, Sequence, Tuple

from .bounded_cache import BoundedCache
from .import_graph import ImportGraphCache, imported_definition_sites
from .module_bindings import ModuleBindings, dotted_name, global_bindings, import_origin
from .statement_facts import imported_binding_name
from .static_positions import TYPING_FORM_NAMES, TypingForms, callee_spellings
from ..source_text import try_read_source

_TYPING_MODULES = ("typing", "typing_extensions")

FORM_ORIGINS: Mapping[str, str] = MappingProxyType(
    {
        **{
            f"{module}.{name}": name
            for module in _TYPING_MODULES
            for name in (
                "TypeVar",
                "ParamSpec",
                "TypeVarTuple",
                "NewType",
                "NamedTuple",
                "TypedDict",
                "TypeAliasType",
                "cast",
                "assert_type",
                "Literal",
            )
        },
        "collections.namedtuple": "namedtuple",
        "enum.Enum": "Enum",
        "enum.IntEnum": "IntEnum",
        "enum.StrEnum": "StrEnum",
        "enum.Flag": "Flag",
        "enum.IntFlag": "IntFlag",
        "mypy_extensions.TypedDict": "TypedDict",
    }
)
"""Each typing form by the absolute name of an object a checker knows it as."""

_MAX_DEPTH = 8
"""Modules a re-export is followed through before its name is taken at its word."""


class ModuleText(NamedTuple):
    """A module as the analysis read it: where it is, and its source."""

    path: str
    source: str


@dataclass(frozen=True)
class _ScopedBinding:
    """A binding a function or class body makes: an import, or a plain alias of a dotted name."""

    origin: Optional[str]
    """The absolute dotted name the import binds, or the dotted name the
    alias assigns; None for a relative import."""
    is_import: bool


@dataclass(frozen=True)
class _ModuleFacts:
    """What one module's source binds, as far as typing forms are concerned."""

    globals: ModuleBindings
    scoped: Mapping[str, Tuple[_ScopedBinding, ...]]
    """The imports and plain aliases of function and class bodies, by the name each binds."""
    star_modules: Tuple[Optional[str], ...]
    """The module each star import names; None for a relative one."""

    def origins(self, head: str, depth: int = 0) -> Tuple[FrozenSet[str], bool]:
        """The absolute names ``head`` may denote, and whether an import may bind it to another.

        The second is true when some import binding ``head`` cannot be spelled
        absolutely: a relative import, which only the importing file's place
        in the project resolves.
        """
        found = set(self.globals.possible_origins(head))
        opaque = any(
            binding.is_import and binding.origin is None
            for binding in self.globals.bindings.get(head, ())
        )
        for binding in self.scoped.get(head, ()):
            if binding.origin is None:
                opaque = opaque or binding.is_import
            elif binding.is_import:
                found.add(binding.origin)
            elif depth < _MAX_DEPTH:
                inner_head, _, rest = binding.origin.partition(".")
                inner, inner_opaque = self.origins(inner_head, depth + 1)
                found.update(f"{origin}.{rest}" if rest else origin for origin in inner)
                opaque = opaque or inner_opaque
        found.update(
            f"{module}.{head}" for module in self.star_modules if module in _TYPING_MODULES
        )
        return frozenset(found), opaque

    @property
    def foreign_star(self) -> bool:
        """Whether a star import from a module other than typing's may bind any name."""
        return any(module not in _TYPING_MODULES for module in self.star_modules)


def _scoped_bindings(
    tree: ast.Module,
) -> Tuple[Mapping[str, Tuple[_ScopedBinding, ...]], Tuple[Optional[str], ...]]:
    """The imports and aliases of every function and class body, and every star import's module."""
    scoped: Dict[str, List[_ScopedBinding]] = {}
    stars: List[Optional[str]] = []
    pending: List[Tuple[ast.AST, bool]] = [(tree, False)]
    while pending:
        node, nested = pending.pop()
        if isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names):
            stars.append(None if node.level else node.module)
        elif nested and isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                name = imported_binding_name(alias)
                if name is not None:
                    scoped.setdefault(name, []).append(
                        _ScopedBinding(import_origin(node, alias), is_import=True)
                    )
        elif (
            nested
            and isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and (aliased := dotted_name(node.value)) is not None
        ):
            scoped.setdefault(node.targets[0].id, []).append(
                _ScopedBinding(aliased, is_import=False)
            )
        inner = nested or isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
        )
        pending.extend((child, inner) for child in ast.iter_child_nodes(node))
    return (
        MappingProxyType({name: tuple(found) for name, found in scoped.items()}),
        tuple(stars),
    )


# One module's facts, keyed by its source so a rewritten module never answers
# from the version it replaced; a pure function of the text.
_FACTS: BoundedCache[str, Optional[_ModuleFacts]] = BoundedCache(256)


def _module_facts(source: str) -> Optional[_ModuleFacts]:
    """What ``source`` binds, or None when it will not parse."""
    if source in _FACTS:
        return _FACTS[source]
    bindings = global_bindings(source)
    if bindings is None:
        return _FACTS.put(source, None)
    scoped, stars = _scoped_bindings(ast.parse(source))
    return _FACTS.put(source, _ModuleFacts(bindings, scoped, stars))


# What a spelling denotes as far as its own module shows, and whether that
# rests on an import to follow; keyed by the module's source and the spelling.
_LOCAL: BoundedCache[Tuple[str, str], Tuple[FrozenSet[str], bool]] = BoundedCache(65536)

# What a name a followed module binds denotes, keyed by the module file's
# path, modification time and size and the name, as the import graph keys
# what it reads.
_FOLLOWED: BoundedCache[Tuple[Path, int, int, str], FrozenSet[str]] = BoundedCache(8192)


def _named_form(spelling: str) -> FrozenSet[str]:
    """The form a spelling's last name spells, if any: what it is taken to be when unresolved."""
    return TYPING_FORM_NAMES & {spelling.rpartition(".")[2]}


def _local_forms(source: str, spelling: str) -> Tuple[FrozenSet[str], bool]:
    """What ``spelling`` denotes as ``source`` binds it, and whether an import must be followed."""
    key = (source, spelling)
    cached = _LOCAL.get(key)
    if cached is not None:
        return cached
    facts = _module_facts(source)
    if facts is None:
        return _LOCAL.put(key, (_named_form(spelling), False))
    head, _, rest = spelling.partition(".")
    origins, opaque = facts.origins(head)
    targets = {f"{origin}.{rest}" if rest else origin for origin in origins}
    forms = {FORM_ORIGINS[target] for target in targets if target in FORM_ORIGINS}
    if not rest and facts.foreign_star:
        forms |= _named_form(head)
    follow = bool(_named_form(spelling)) and (
        opaque or any(target not in FORM_ORIGINS for target in targets)
    )
    return _LOCAL.put(key, (frozenset(forms), follow))


@dataclass(frozen=True)
class TypingFormResolver:
    """Resolves the callees of one module to the typing forms they may denote."""

    module: ModuleText
    cache: ImportGraphCache
    depth: int = 0

    def forms_of(self, spelling: str) -> FrozenSet[str]:
        """The forms the dotted ``spelling`` may denote wherever the module uses it."""
        forms, follow = _local_forms(self.module.source, spelling)
        return forms | self._followed(spelling) if follow else forms

    def _followed(self, spelling: str) -> FrozenSet[str]:
        """What the module a spelling is imported from binds it to; its name when that is unknown.

        An import of no module of the project (``sqlalchemy``, ``typing``) is
        taken at its word, and so names no form it does not spell.
        """
        sites = imported_definition_sites(self.module.path, spelling, self.cache)
        if sites is None:
            return _named_form(spelling)
        found: FrozenSet[str] = frozenset()
        for file, qualname in sorted(sites):
            found |= self._forms_in(file, qualname)
        return found

    def _forms_in(self, file: Path, qualname: str) -> FrozenSet[str]:
        if self.depth >= _MAX_DEPTH:
            return _named_form(qualname)
        try:
            stat = file.stat()
        except OSError:
            return _named_form(qualname)
        key = (file, stat.st_mtime_ns, stat.st_size, qualname)
        cached = _FOLLOWED.get(key)
        if cached is not None:
            return cached
        source = try_read_source(file)
        if source is None:
            return _FOLLOWED.put(key, _named_form(qualname))
        other = TypingFormResolver(ModuleText(str(file), source), self.cache, self.depth + 1)
        return _FOLLOWED.put(key, other.forms_of(qualname))


def typing_forms_of(
    statements: Sequence[ast.AST], module: ModuleText, cache: ImportGraphCache
) -> TypingForms:
    """What the callees of ``statements``, a block of ``module``, denote among the typing forms."""
    resolver = TypingFormResolver(module, cache)
    return TypingForms(
        frozenset(
            (spelling, form)
            for statement in statements
            for spelling in callee_spellings(statement)
            for form in resolver.forms_of(spelling)
        )
    )
