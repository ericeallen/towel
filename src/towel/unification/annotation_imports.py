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

"""The names a helper's annotations need its module to bind, bound so that nothing else changes.

A name a module binds is visible beyond the annotation it was bound for.
Every later reference in the module reads it, and every consumer reads it as
an attribute. Where the module has no ``__all__``, every module that
star-imports it takes it too, whatever that module had bound under the name
before: ``from typing import Callable`` written into ``pkg/a.py`` turned
``collections.abc.Callable`` into ``typing.Callable`` in a module doing
``from pkg.a import *``, and ``case Callable():`` there raised ``TypeError``.
The import under ``TYPE_CHECKING`` never runs, but a checker reads it as a
binding like any other, so it too reaches every star-importer's checker.

So every name Towel binds for an annotation is private and free: it starts
with an underscore, which a star import does not take unless the provider's
``__all__`` lists it, and it is a word no text of the module spells, not in
code, a string or a comment, so no reference or binding of the module's own
can meet it. Nothing the program already names changes meaning. The
annotations are spelled through those names:

* a ``typing`` name the module does not bind is reached through the module
  ``typing`` itself, ``import typing as _typing`` and ``_typing.Callable``,
  one binding however many names the annotations use. ``TYPE_CHECKING`` is
  reached the same way, ``if _typing.TYPE_CHECKING:``, which mypy recognises
  by the attribute's name and pyright by the alias of ``typing``;
  ``from typing import TYPE_CHECKING as _TYPE_CHECKING`` would be recognised
  by neither, and the guard's imports would read as possibly unbound;
* a class imported for the checker alone is imported under a private alias,
  ``from pkg.models import Item as _Item``, and spelled ``"_Item"``.

``_Callable`` for ``typing.Callable`` would read shorter, and would need an
alias per name and still a second binding for the guard; ``_typing.Callable``
says what it is where it stands. A binding the module already makes is used
instead of a new one when it certainly denotes the same object wherever the
generated code reads it: it is the only binding of that name anywhere in the
module, in any scope, it is a top-level import that runs before the point new
imports go, and, for a public name, no star import could replace it. So
``import typing`` gives ``typing.Callable``, and the ``_typing`` an earlier
refactoring of the module wrote is reused rather than joined by a second.

The one assumption is the one the helpers' own names rest on: a star import
brings a private name only from a provider whose ``__all__`` lists it.
"""

from __future__ import annotations

import ast
import copy
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Mapping, Optional, Sequence, Set, Tuple

from ..canonical_ast import canonical_dump
from .annotation_ladder import fresh_name
from .exceptions import RefactoringError
from .import_graph import TypeCheckingGuards
from .module_bindings import import_origin
from .statement_facts import bindings_of, imported_binding_name

_TYPING_ORIGINS = frozenset({"typing"})
_TYPE_CHECKING_ORIGINS = frozenset({"typing.TYPE_CHECKING", "typing_extensions.TYPE_CHECKING"})
_WORD = re.compile(r"[^\W\d]\w*")


def words_of(*texts: str) -> FrozenSet[str]:
    """Every identifier-shaped word of ``texts``: code, strings and comments alike.

    Read as the compiler reads identifiers, after NFKC normalization, so a
    full-width spelling of a name is that name.
    """
    return frozenset(
        word for text in texts for word in _WORD.findall(unicodedata.normalize("NFKC", text))
    )


@dataclass(frozen=True)
class GuardedImport:
    """An import written under the module's ``TYPE_CHECKING`` guard, for a checker alone."""

    module: str
    """The module as the program spells it from here, relative or absolute (``.models``)."""
    name: str
    bound_as: str

    @property
    def statement(self) -> str:
        suffix = "" if self.bound_as == self.name else f" as {self.bound_as}"
        return f"from {self.module} import {self.name}{suffix}"


@dataclass(frozen=True)
class AnnotationImports:
    """What the host must gain for the helper's annotations, and how they spell each name.

    ``typing_import`` is the new ``import typing as <alias>`` line, when the
    annotations need ``typing`` and the module binds no usable name for it.
    ``guard_test`` is the test of the guard to write, when type-only imports
    are new and no guard exists to join. ``respellings`` maps each name the
    annotations were written with to the expression the host reaches it by.
    """

    typing_import: Optional[str] = None
    guard_test: Optional[str] = None
    guarded: Tuple[GuardedImport, ...] = ()
    respellings: Mapping[str, str] = field(default_factory=dict)


def annotation_imports(
    source: str,
    taken: FrozenSet[str],
    typing_names: Sequence[str],
    checking_imports: Sequence[Tuple[str, str]],
    import_line: int,
    joins_guard: bool,
) -> AnnotationImports:
    """The bindings the annotations need in the module ``source``, each private, free, or reused.

    ``taken`` are the words the module and the generated code already spell;
    no new binding takes one. ``typing_names`` are the ``typing`` names the
    annotations use that the module does not bind; ``checking_imports`` the
    ``(module, name)`` pairs they need a checker to import. ``import_line``
    is the 0-based line new imports are inserted before, and ``joins_guard``
    whether a guard is already there for the type-only imports to join.
    """
    tree = ast.parse(source)
    chosen: Set[str] = set()
    respellings: Dict[str, str] = {}

    def respell(name: str, spelling: str) -> None:
        if respellings.setdefault(name, spelling) != spelling:
            raise RefactoringError(
                f"The helper's annotations need {name!r} for two different imports"
            )

    guarded: List[GuardedImport] = []
    for module, name in dict.fromkeys(checking_imports):
        existing = _joinable_guarded_import(source, tree, module, name)
        if existing is not None:
            respell(name, existing)
            continue
        alias = fresh_name(name if name.startswith("_") else f"_{name}", set(taken | chosen))
        chosen.add(alias)
        guarded.append(GuardedImport(module, name, alias))
        respell(name, alias)
    guard_test: Optional[str] = None
    needs_typing = bool(typing_names)
    if guarded and not joins_guard:
        if _reused(tree, "TYPE_CHECKING", _TYPE_CHECKING_ORIGINS, import_line):
            guard_test = "TYPE_CHECKING"
        else:
            needs_typing = True
    typing_import: Optional[str] = None
    if needs_typing:
        typing_module = _typing_module(tree, import_line)
        if typing_module is None:
            typing_module = fresh_name("_typing", set(taken | chosen))
            chosen.add(typing_module)
            typing_import = f"import typing as {typing_module}"
        if guarded and not joins_guard and guard_test is None:
            guard_test = f"{typing_module}.TYPE_CHECKING"
        for name in typing_names:
            respell(name, f"{typing_module}.{name}")
    return AnnotationImports(typing_import, guard_test, tuple(guarded), respellings)


def _typing_module(tree: ast.Module, import_line: int) -> Optional[str]:
    """A name the module binds to ``typing`` that generated code can use, preferring ``typing``."""
    candidates = sorted(
        {
            name
            for statement in tree.body
            if isinstance(statement, ast.Import)
            for alias in statement.names
            if alias.name == "typing" and (name := imported_binding_name(alias)) is not None
        },
        key=lambda name: (name != "typing", name),
    )
    return next(
        (name for name in candidates if _reused(tree, name, _TYPING_ORIGINS, import_line)), None
    )


def _reused(tree: ast.Module, name: str, origins: FrozenSet[str], import_line: int) -> bool:
    """Whether ``name`` certainly denotes one of ``origins`` wherever generated code reads it.

    Its only binding anywhere in the module is a top-level import of one of
    ``origins`` that ends before ``import_line``, and a public name is not
    exposed to a star import, which could rebind it.
    """
    sites = _binding_sites(tree, name)
    if len(sites) != 1:
        return False
    statement = sites[0]
    if statement not in tree.body or not isinstance(statement, (ast.Import, ast.ImportFrom)):
        return False
    if (statement.end_lineno or statement.lineno) > import_line:
        return False
    if not name.startswith("_") and _has_star_import(tree):
        return False
    return any(
        imported_binding_name(alias) == name and import_origin(statement, alias) in origins
        for alias in statement.names
    )


def _joinable_guarded_import(
    source: str, tree: ast.Module, module: str, name: str
) -> Optional[str]:
    """The name a module-level guard already imports ``name`` from ``module`` under, if usable.

    Usable when that import is the only binding of the name anywhere in the
    module, and, for a public name, no star import could rebind it for the
    checker.
    """
    guards = TypeCheckingGuards.of(source, tree)
    for order, statement in enumerate(tree.body):
        if not isinstance(statement, ast.If) or not guards.never_true(statement.test, order=order):
            continue
        for inner in statement.body:
            if not isinstance(inner, ast.ImportFrom):
                continue
            if "." * inner.level + (inner.module or "") != module:
                continue
            for alias in inner.names:
                bound = imported_binding_name(alias)
                if alias.name != name or bound is None:
                    continue
                if _binding_sites(tree, bound) == [inner] and (
                    bound.startswith("_") or not _has_star_import(tree)
                ):
                    return bound
    return None


def _has_star_import(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names)
        for node in ast.walk(tree)
    )


def _binding_sites(tree: ast.Module, name: str) -> List[ast.AST]:
    """Every node of the module that binds ``name`` in any scope; an import counts as its statement.

    Parameters, ``global`` and ``nonlocal`` declarations and type parameters
    count too: each is a scope in which the name means something else.
    """
    sites: List[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if any(imported_binding_name(alias) == name for alias in node.names):
                sites.append(node)
        elif isinstance(node, ast.arg):
            if node.arg == name:
                sites.append(node)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            if name in node.names:
                sites.append(node)
        elif getattr(node, "name", None) == name and not isinstance(node, ast.alias):
            sites.append(node)  # def, class, except-as, match capture, type parameter
        elif isinstance(node, ast.MatchMapping) and node.rest == name:
            sites.append(node)
        elif isinstance(node, ast.Name) and node.id == name:
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                sites.append(node)
    return sites


def bound_by(statements: Sequence[ast.stmt]) -> FrozenSet[str]:
    """The names ``statements`` bind in the scope they run in."""
    return frozenset(
        name
        for statement in statements
        for name in bindings_of(statement, into_nested_scopes=False)
    )


def refuse_public_or_taken(names: FrozenSet[str], taken: FrozenSet[str]) -> None:
    """Refuse a generated module-level binding that is public or that the module already spells."""
    for name in sorted(names):
        if not name.startswith("_"):
            raise RefactoringError(
                f"A generated declaration would bind the public name {name!r}, which star"
                " imports and every consumer of the module would see"
            )
        if name in taken:
            raise RefactoringError(
                f"A generated declaration would bind {name!r}, which the module already spells"
            )


def respelled_helper(helper: ast.FunctionDef, respellings: Mapping[str, str]) -> ast.FunctionDef:
    """A copy of ``helper`` whose annotations spell each name as ``respellings`` says.

    Only annotations are rewritten: the signature's, and those of the
    helper's own local variables, which a function never evaluates. The body
    is the program's code and keeps reading what it read, and so does any
    function, class or lambda nested in it, whose annotations may run.
    """
    if not respellings:
        return helper
    rewritten = copy.deepcopy(helper)
    arguments = rewritten.args
    for parameter in (
        *arguments.posonlyargs,
        *arguments.args,
        *arguments.kwonlyargs,
        *(variadic for variadic in (arguments.vararg, arguments.kwarg) if variadic is not None),
    ):
        if parameter.annotation is not None:
            parameter.annotation = _respelled(parameter.annotation, respellings)
    if rewritten.returns is not None:
        rewritten.returns = _respelled(rewritten.returns, respellings)
    pending: List[ast.AST] = list(rewritten.body)
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(node, ast.AnnAssign):
            node.annotation = _respelled(node.annotation, respellings)
        pending.extend(ast.iter_child_nodes(node))
    return rewritten


def respelled_declarations(
    declarations: Sequence[ast.stmt], respellings: Mapping[str, str]
) -> Tuple[ast.stmt, ...]:
    """Type-variable declarations whose bounds and constraints spell names as ``respellings`` says.

    A declaration is ``X = TypeVar("X", <constraint>..., bound=<bound>)``; the
    first argument is the variable's own name, not a type, and is kept.
    """
    if not respellings:
        return tuple(declarations)
    rewritten: List[ast.stmt] = []
    for declaration in declarations:
        declaration = copy.deepcopy(declaration)
        if isinstance(declaration, ast.Assign) and isinstance(declaration.value, ast.Call):
            call = declaration.value
            call.args = call.args[:1] + [
                _respelled(argument, respellings) for argument in call.args[1:]
            ]
            for keyword in call.keywords:
                keyword.value = _respelled(keyword.value, respellings)
        rewritten.append(declaration)
    return tuple(rewritten)


def _respelled(annotation: ast.expr, respellings: Mapping[str, str]) -> ast.expr:
    """``annotation`` with each respelled name replaced, inside a string annotation too."""
    expression: ast.expr = annotation
    quoted = isinstance(annotation, ast.Constant) and isinstance(annotation.value, str)
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        try:
            expression = ast.parse(annotation.value.strip(), mode="eval").body
        except SyntaxError:
            return annotation
    replaced = _NameRespeller(respellings).visit(copy.deepcopy(expression))
    if not isinstance(replaced, ast.expr) or canonical_dump(replaced) == canonical_dump(expression):
        return annotation
    if quoted:
        return ast.copy_location(ast.Constant(value=ast.unparse(replaced)), annotation)
    return replaced


class _NameRespeller(ast.NodeTransformer):
    def __init__(self, respellings: Mapping[str, str]) -> None:
        self.respellings = respellings

    def visit_Name(self, node: ast.Name) -> ast.expr:
        spelling = self.respellings.get(node.id)
        if spelling is None or not isinstance(node.ctx, ast.Load):
            return node
        return ast.copy_location(ast.parse(spelling, mode="eval").body, node)
