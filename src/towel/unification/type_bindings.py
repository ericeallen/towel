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

"""Resolve annotation structure and lexical type-parameter identity without imports.

Only the two supplied source strings are inspected. A name is its binding: a
class or an import is identified by the absolute name the program's own
imports give it (``module_names``, the import model's answer), so the
``Version`` a module imports relatively, the ``Version`` another defines, and
the checker's ``packaging.version.Version`` are one type. Where the program
names no module for a file, a relative import is identified by its location
and a class by where it is defined. A name imported only for the checker,
under ``if TYPE_CHECKING:``, is a binding like any other: every spelling this
module produces is written into a quoted annotation, which only a checker
reads. Ambiguous bindings are refused.

Resolution and spelling are separate. A term is resolved in its site's own
module, and carries the spelling the helper's module can write for it, or no
spelling at all (:func:`spellable`): a type only the site can name may still
be generalized away into a type variable, and a signature that would have to
write it is declined by the caller. A ``typing`` name the helper's module does
not bind is spelled bare, and the term records the import it needs
(:func:`required_imports`). Type-parameter atoms carry their declaration
metadata, which permits the caller to introduce a fresh helper parameter
rather than accidentally capturing the original binder.
"""

from __future__ import annotations

import ast
import builtins
from dataclasses import dataclass, field, replace
from enum import Enum
import os
import re
import typing
from collections.abc import Callable, Sequence

from .revealed_types import OpaqueType, parse_revealed


class TypeKind(Enum):
    """The small expression language accepted for prospective annotations."""

    ATOM = "atom"
    APPLY = "apply"
    TUPLE = "tuple"
    LIST = "list"
    UNION = "union"
    CONSTANT = "constant"


@dataclass(frozen=True)
class TypeParameter:
    """A scoped source binder and its independent bound or constraints."""

    identity: str
    bound: TypeTerm | None = None
    constraints: tuple[TypeTerm, ...] = ()


@dataclass(frozen=True)
class TypeTerm:
    """Canonical type structure; host spelling does not affect equality.

    An atom with an empty ``spelling`` has none where the helper is defined.
    ``imports`` are the ``(module, name)`` imports its spelling needs there.
    """

    kind: TypeKind
    name: str = ""
    spelling: str = field(default="", compare=False)
    children: tuple[TypeTerm, ...] = ()
    parameter: TypeParameter | None = None
    imports: tuple[tuple[str, str], ...] = field(default=(), compare=False)


_BUILTINS = frozenset(name for name, value in vars(builtins).items() if isinstance(value, type))
_BUILTIN_NAMES = frozenset(vars(builtins))
_ALIASES = {
    "typing.List": "builtins.list",
    "typing.Dict": "builtins.dict",
    "typing.Tuple": "builtins.tuple",
    "typing.Set": "builtins.set",
    "typing.FrozenSet": "builtins.frozenset",
    "typing.Type": "builtins.type",
    **{
        f"typing.{name}": f"collections.abc.{name}"
        for name in (
            "Callable",
            "Iterable",
            "Iterator",
            "Sequence",
            "MutableSequence",
            "Mapping",
            "MutableMapping",
            "Collection",
            "Container",
            "MutableSet",
            "Reversible",
            "Awaitable",
            "Coroutine",
            "AsyncIterable",
            "AsyncIterator",
            "Generator",
            "AsyncGenerator",
            "Hashable",
            "Sized",
        )
    },
    "typing.AbstractSet": "collections.abc.Set",
}
_UNSAFE = frozenset(
    f"typing.{name}"
    for name in ("Unknown", "ParamSpec", "TypeVarTuple", "Self", "Never", "NoReturn")
)
_ANY = "typing.Any"
"""Unsafe as a whole type, which says nothing; inside one (``dict[str, Any]``) it is the program's."""
_REVEALED_FORMS = frozenset(
    (
        # mypy writes its special forms bare, ``Union`` and ``Optional`` before 2.0.
        "Any",
        "Union",
        "Optional",
        "Literal",
        "LiteralString",
        "Tuple",
        "Type",
        "TypeGuard",
        "TypeIs",
        # pyright writes every typing name bare, as the program spelled it.
        "List",
        "Dict",
        "Set",
        "FrozenSet",
        "DefaultDict",
        "OrderedDict",
        "Counter",
        "Deque",
        "ChainMap",
        *(alias.removeprefix("typing.") for alias in _ALIASES if alias.startswith("typing.")),
        "IO",
        "TextIO",
        "BinaryIO",
        "Pattern",
        "Match",
        "ContextManager",
        "AsyncContextManager",
        "ItemsView",
        "KeysView",
        "ValuesView",
        "MappingView",
    )
)
"""``typing`` names a checker writes bare when the site binds nothing by that name."""
_QUOTED = re.compile(r"""("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')""")


def _outside_strings(text: str, rewrite: Callable[[str], str]) -> str:
    """Checker decorations must never rewrite the contents of Literal strings."""
    return "".join(
        part if index % 2 else rewrite(part) for index, part in enumerate(_QUOTED.split(text))
    )


def _canonical(name: str) -> str:
    if name.startswith("typing_extensions."):
        name = "typing." + name.removeprefix("typing_extensions.")
    return _ALIASES.get(name, name)


def _dotted(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        head = _dotted(node.value)
        return f"{head}.{node.attr}" if head else None
    return None


ModuleNames = Callable[[str], str | None]
"""The absolute name the program's imports give the module at a path, when they give one."""


def _package(file_path: str, module_name: str | None) -> str | None:
    """The package a module's relative imports resolve in, its ``__package__``."""
    if module_name is None:
        return None
    if os.path.basename(file_path) == "__init__.py":
        return module_name
    return module_name.rpartition(".")[0]


def _relative_target(package: str | None, level: int, module: str | None) -> str | None:
    """The absolute module ``from <level dots><module> import ...`` names, when known.

    ``None`` for a module whose package is unknown, and for an import that
    climbs out of its top-level package, which raises when it runs.
    """
    if not package:
        return None
    parts = package.split(".")
    if level - 1 >= len(parts):
        return None
    return ".".join(parts[: len(parts) - (level - 1)] + ([module] if module else []))


def _is_type_checking_guard(statement: ast.stmt) -> bool:
    """``if TYPE_CHECKING:``, which checkers recognize by the name alone."""
    if not isinstance(statement, ast.If):
        return False
    test = statement.test
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


@dataclass(frozen=True)
class _Binding:
    kind: str
    identity: str
    node: ast.AST


@dataclass(frozen=True)
class _Scope:
    node: ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
    bindings: dict[str, _Binding]
    wildcard: bool


@dataclass(frozen=True)
class _ClassParameter:
    declaration_identity: str
    term: TypeTerm


class _Collector(ast.NodeVisitor):
    """Record every write in one scope; repeated or conditional writes are unsafe.

    ``module_name`` is the file's absolute module name, when the program gives
    it one: a relative import and a module-level class are then identified by
    the absolute names a checker writes, as every other import already is.
    """

    def __init__(self, file_path: str, node: ast.AST, module_name: str | None = None) -> None:
        self.file_path = file_path
        self.scope_node = node
        self.module_name = module_name
        self.package = _package(file_path, module_name)
        self.bindings: dict[str, _Binding] = {}
        self.wildcard = False
        self.direct = False

    def bind(self, name: str, kind: str, identity: str, node: ast.AST) -> None:
        if name in self.bindings or not self.direct:
            kind = "unknown"
        self.bindings[name] = _Binding(kind, identity, node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.bind(node.id, "unknown", "", node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            dotted = _dotted(node)
            if dotted:
                self.bind(dotted.split(".")[0], "unknown", "", node)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.bind(
                alias.asname or alias.name.split(".")[0],
                "import",
                alias.name if alias.asname else alias.name.split(".")[0],
                node,
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if node.level:
            absolute = _relative_target(self.package, node.level, node.module)
            base = os.path.dirname(self.file_path)
            for _ in range(node.level - 1):
                base = os.path.dirname(base)
            module = absolute if absolute is not None else f"relative:{base}/{module}"
        for alias in node.names:
            if alias.name == "*":
                self.wildcard = True
            else:
                self.bind(alias.asname or alias.name, "import", f"{module}.{alias.name}", node)

    def visit_Assign(self, node: ast.Assign) -> None:
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            self.bind(node.targets[0].id, "assignment", "", node.value)
            self.visit(node.value)
        else:
            self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.visit(node.target)
        if node.value is not None:
            self.visit(node.value)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.bind(node.name, "unknown", "", node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.bind(node.name, "unknown", "", node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        module_level = isinstance(self.scope_node, ast.Module)
        identity = (
            f"{self.module_name}.{node.name}"
            if module_level and self.module_name is not None
            else f"local:{self.file_path}:{node.lineno}:{node.name}"
        )
        self.bind(node.name, "class" if module_level else "unknown", identity, node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        pass

    def visit_ListComp(
        self, node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp
    ) -> None:
        # A comprehension's iteration targets belong to its implicit scope.
        for child in ast.walk(node):
            if isinstance(child, ast.NamedExpr):
                self.visit(child.target)

    visit_SetComp = visit_ListComp
    visit_DictComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp

    def visit_Global(self, node: ast.Global) -> None:
        for name in node.names:
            self.bind(name, "unknown", "", node)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        for name in node.names:
            self.bind(name, "unknown", "", node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.bind(node.name, "unknown", "", node)
        self.generic_visit(node)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name:
            self.bind(node.name, "unknown", "", node)
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name:
            self.bind(node.name, "unknown", "", node)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest:
            self.bind(node.rest, "unknown", "", node)
        self.generic_visit(node)


_DIRECT = (
    ast.Import,
    ast.ImportFrom,
    ast.Assign,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
)
"""Statements whose bindings are unconditional in the scope that runs them."""


def _scope(
    node: ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    file_path: str,
    module_name: str | None = None,
) -> _Scope:
    collector = _Collector(file_path, node, module_name)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for argument in (
            node.args.posonlyargs
            + node.args.args
            + node.args.kwonlyargs
            + ([node.args.vararg] if node.args.vararg else [])
            + ([node.args.kwarg] if node.args.kwarg else [])
        ):
            collector.bindings[argument.arg] = _Binding("unknown", "", argument)
    for statement in node.body:
        if _is_type_checking_guard(statement) and isinstance(statement, ast.If):
            # Imported for the checker only: a binding for every annotation
            # this module writes, all of which are quoted. What the other
            # branch binds instead is a second, conditional binding.
            for guarded in statement.body:
                collector.direct = isinstance(guarded, (ast.Import, ast.ImportFrom))
                collector.visit(guarded)
            collector.direct = False
            for alternative in statement.orelse:
                collector.visit(alternative)
            continue
        collector.direct = isinstance(statement, _DIRECT)
        collector.visit(statement)
    # Writes made by nested functions can invalidate an otherwise stable import
    # or declaration. Ordinary nested locals do not affect this scope.
    for descendant in ast.walk(node):
        if isinstance(descendant, ast.Global) and isinstance(node, ast.Module):
            for name in descendant.names:
                collector.bindings[name] = _Binding("unknown", "", descendant)
        if isinstance(descendant, ast.Nonlocal) and not isinstance(node, ast.Module):
            for name in descendant.names:
                if name in collector.bindings:
                    collector.bindings[name] = _Binding("unknown", "", descendant)
    parameters: object = getattr(node, "type_params", ())
    if isinstance(parameters, list):
        for parameter in parameters:
            parameter_name: object = getattr(parameter, "name", None)
            if isinstance(parameter_name, str):
                identity = f"parameter:{file_path}:{getattr(node, 'lineno', 0)}:{parameter_name}"
                kind = "unknown" if parameter_name in collector.bindings else "parameter"
                collector.bindings[parameter_name] = _Binding(kind, identity, parameter)
    return _Scope(node, collector.bindings, collector.wildcard)


def _scopes(
    module: ast.Module, file_path: str, line: int, module_name: str | None = None
) -> tuple[_Scope, ...]:
    result = [_scope(module, file_path, module_name)]
    current: ast.AST = module
    while True:
        candidates = [
            node
            for node in ast.walk(current)
            if node is not current
            and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.lineno <= line <= (node.end_lineno or node.lineno)
        ]
        if not candidates:
            break
        outer = min(
            candidates,
            key=lambda node: (node.lineno, -(node.end_lineno or node.lineno)),
        )
        result.append(_scope(outer, file_path))
        current = outer
    return tuple(result)


class TypeResolver:
    """Resolve one site's types for a module helper or a named host class.

    Class parameters are exposed separately; the caller chooses whether their
    identities remain bound or are freshened for the helper's actual dispatch.
    Bounds and constraints always use module-visible spellings because fresh
    TypeVar declarations are emitted outside the class. ``module_names`` gives
    each file's absolute module name (see the module docstring); without it,
    relative imports and module-level classes keep location identities.
    """

    def __init__(
        self,
        source: str,
        file_path: str,
        line: int,
        host_source: str,
        host_file: str,
        *,
        host_class: str | None = None,
        module_names: ModuleNames | None = None,
    ) -> None:
        self.file_path = os.path.abspath(file_path)
        self.host_file = os.path.abspath(host_file)
        self.host_class_parameters: tuple[TypeTerm, ...] = ()
        self.host_class_parameter_identities: frozenset[str] = frozenset()
        self._source_class_parameters: tuple[_ClassParameter, ...] = ()
        self._module_resolver: TypeResolver | None = None
        self._module_names = module_names
        try:
            module = ast.parse(source)
            host = ast.parse(host_source)
        except (SyntaxError, ValueError):
            self.scopes: tuple[_Scope, ...] = ()
            self.host: _Scope | None = None
        else:
            self.scopes = _scopes(module, self.file_path, line, self._module_name(self.file_path))
            self.host = _scope(host, self.host_file, self._module_name(self.host_file))
            if host_class is not None:
                self._configure_class_host(source, line, host_source, host, host_class)
        self._class_identities = frozenset(
            binding.identity
            for scope in self.scopes
            for binding in scope.bindings.values()
            if binding.kind == "class"
        )

    def _module_name(self, path: str) -> str | None:
        name = self._module_names(path) if self._module_names is not None else None
        if name is None or not all(part.isidentifier() for part in name.split(".")):
            return None
        return name

    def _configure_class_host(
        self,
        source: str,
        line: int,
        host_source: str,
        host: ast.Module,
        host_class: str,
    ) -> None:
        classes = [
            node for node in host.body if isinstance(node, ast.ClassDef) and node.name == host_class
        ]
        if len(classes) != 1 or self.host is None:
            self.host = None
            return
        class_scope = _scope(classes[0], self.host_file, self._module_name(self.host_file))
        self._module_resolver = TypeResolver(
            source,
            self.file_path,
            line,
            host_source,
            self.host_file,
            module_names=self._module_names,
        )
        host_resolver = TypeResolver(
            host_source,
            self.host_file,
            classes[0].lineno,
            host_source,
            self.host_file,
            module_names=self._module_names,
        )
        host_parameters = host_resolver._declared_class_parameters(len(host_resolver.scopes) - 1)
        source_parameters: list[_ClassParameter] = []
        for index, scope in enumerate(self.scopes):
            if isinstance(scope.node, ast.ClassDef):
                parameters = self._module_resolver._declared_class_parameters(index)
                if parameters is None:
                    self.host = None
                    return
                source_parameters.extend(parameters)
        if host_parameters is None or class_scope.wildcard:
            self.host = None
            return
        self.host = _Scope(host, {**self.host.bindings, **class_scope.bindings}, self.host.wildcard)
        self._source_class_parameters = tuple(source_parameters)
        self.host_class_parameters = tuple(parameter.term for parameter in host_parameters)
        self.host_class_parameter_identities = frozenset(
            identity
            for term in self.host_class_parameters
            for identity in type_parameter_identities(term)
        )

    def _declared_class_parameters(self, index: int) -> tuple[_ClassParameter, ...] | None:
        """Prove class binders from explicit parameters and resolved generic bases."""
        scope = self.scopes[index]
        if not isinstance(scope.node, ast.ClassDef) or scope.wildcard:
            return None
        declared: object = getattr(scope.node, "type_params", ())
        explicit = isinstance(declared, list) and bool(declared)
        terms: dict[str, TypeTerm] = {}
        if isinstance(declared, list):
            for parameter in declared:
                name: object = getattr(parameter, "name", None)
                if not isinstance(name, str):
                    return None
                term = self._name(name, index + 1, frozenset(), False)
                if term is None or term.parameter is None:
                    return None
                terms[term.parameter.identity] = term
        for base in scope.node.bases:
            term = self._resolve(base, index + 1 if explicit else index, frozenset(), False)
            if term is None:
                return None
            if (
                term.kind is TypeKind.APPLY
                and term.children
                and term.children[0].name in ("typing.Generic", "typing.Protocol")
                and any(argument.parameter is None for argument in term.children[1:])
            ):
                return None
            if term.kind is TypeKind.APPLY and not all(
                self._known_base_argument(argument) for argument in term.children[1:]
            ):
                # An imported bare name might itself be an imported TypeVar.
                # Without its declaration we cannot prove the class is closed.
                return None
            for parameter_term in _parameter_terms(term):
                assert parameter_term.parameter is not None
                terms.setdefault(parameter_term.parameter.identity, parameter_term)
        result: list[_ClassParameter] = []
        for identity, term in terms.items():
            assert term.parameter is not None
            binding = scope.bindings.get(term.spelling)
            if binding is not None and binding.kind != "parameter":
                return None
            parameter_identity = (
                identity
                if explicit
                else f"class-parameter:{self.file_path}:{scope.node.lineno}:{identity}"
            )
            parameter = replace(term.parameter, identity=parameter_identity)
            result.append(
                _ClassParameter(
                    identity,
                    replace(term, name=parameter_identity, parameter=parameter),
                )
            )
        return tuple(result)

    def _known_base_argument(self, term: TypeTerm) -> bool:
        """Whether a generic base's argument is known not to be an imported type variable."""
        if term.parameter is not None:
            return True
        if term.kind is TypeKind.ATOM:
            return term.name in self._class_identities or term.name.startswith(
                ("builtins.", "local:", "typing.", "collections.abc.")
            )
        children = term.children[1:] if term.kind is TypeKind.APPLY else term.children
        return all(self._known_base_argument(child) for child in children)

    def resolve(self, annotation: ast.expr) -> TypeTerm | None:
        """Resolve a declared annotation, including a quoted forward annotation."""
        return _informative(self._resolve(annotation, len(self.scopes), frozenset(), False))

    def resolve_revealed(self, text: str) -> TypeTerm | None:
        """Resolve checker spelling, validating decorated type variables in scope.

        The spelling is read by :func:`~towel.unification.revealed_types.parse_revealed`.
        Its dotted names are the checker's absolute names; a callable no
        parameter list can state is an opaque atom, identified by its spelling
        in this site's file, which only a type variable can stand for. A
        generic callable is instantiated at the type variables of its own
        binders' names in this scope, which any instantiation of it permits.
        """
        failed = False

        def undecorate(match: re.Match[str]) -> str:
            nonlocal failed
            name, qualifier = match.group(1), match.group(2)
            term = self._name(name, len(self.scopes), frozenset(), False)
            if term is None or term.parameter is None:
                failed = True
            if qualifier:
                found = self._lookup(name, len(self.scopes))
                if found is not None and found[0].kind == "parameter":
                    if getattr(self.scopes[found[1]].node, "name", None) != qualifier:
                        failed = True
                elif not any(
                    getattr(scope.node, "name", None) == qualifier for scope in self.scopes
                ):
                    failed = True
            return name

        text = _outside_strings(
            text.strip(),
            lambda part: re.sub(r"\b([A-Za-z_]\w*)(?:@([A-Za-z_]\w*)|`-?\d+)\b", undecorate, part),
        )
        if failed:
            return None
        node = parse_revealed(text, callable_name="collections.abc.Callable", unwritable="opaque")
        if node is None:
            return None
        return _informative(self._resolve(node, len(self.scopes), frozenset(), True))

    def _lookup(self, name: str, limit: int) -> tuple[_Binding, int] | None:
        for index in range(limit - 1, -1, -1):
            scope = self.scopes[index]
            if scope.wildcard:
                return _Binding("unknown", "", scope.node), index
            if name in scope.bindings:
                return scope.bindings[name], index
        return None

    def _import_identity(self, text: str, limit: int) -> str | None:
        head, separator, tail = text.partition(".")
        found = self._lookup(head, limit)
        if found is None or found[0].kind != "import":
            return None
        return _canonical(found[0].identity + (separator + tail if separator else ""))

    def _revealed_identity(self, text: str, limit: int) -> str:
        """What a checker means by ``text``: an absolute name, or a bare typing form."""
        if "." not in text and text in _REVEALED_FORMS and self._lookup(text, limit) is None:
            return _canonical(f"typing.{text}")
        return _canonical(text)

    def _host_spelling(self, identity: str) -> str | None:
        if self.host is None or self.host.wildcard:
            return None
        if identity.startswith("builtins."):
            name = identity.removeprefix("builtins.")
            if name in _BUILTINS and name not in self.host.bindings:
                return name
        for name, binding in sorted(self.host.bindings.items()):
            if binding.kind == "class" and binding.identity == identity:
                return name
            if binding.kind != "import":
                continue
            imported = _canonical(binding.identity)
            if imported == identity:
                return name
            if identity.startswith(imported + "."):
                return name + identity[len(imported) :]
            # typing.Callable and collections.abc.Callable share identity.
            for alias, canonical in _ALIASES.items():
                if canonical == identity and alias.startswith(imported + "."):
                    return name + alias[len(imported) :]
        return None

    def _spelling(self, identity: str) -> tuple[str, tuple[tuple[str, str], ...]]:
        """How the host writes ``identity``, and what it must import to; ``""`` when it cannot.

        A ``typing`` name the host does not bind at all is written bare, with
        its import from ``typing``: every alias of it names the same type.
        """
        spelled = self._host_spelling(identity)
        if spelled is not None:
            return spelled, ()
        name = _typing_name(identity)
        if (
            name is None
            or self.host is None
            or self.host.wildcard
            or name in self.host.bindings
            or name in _BUILTIN_NAMES
        ):
            return "", ()
        return name, (("typing", name),)

    def _name(
        self, text: str, limit: int, active: frozenset[str], revealed: bool
    ) -> TypeTerm | None:
        if revealed and "." in text:
            # A checker writes absolute names, whatever the site's scope binds.
            identity = _canonical(text)
        else:
            head = text.split(".")[0]
            found = self._lookup(head, limit)
            if found is not None and "." not in text:
                binding, index = found
                if binding.kind in ("assignment", "parameter"):
                    return self._parameter(text, binding, index, active)
                if binding.kind == "class":
                    identity = binding.identity
                elif binding.kind == "import":
                    identity = _canonical(binding.identity)
                else:
                    return None
            elif found is not None:
                identity = self._import_identity(text, limit) or ""
            elif text in _BUILTINS:
                identity = f"builtins.{text}"
            elif revealed and text in _REVEALED_FORMS:
                identity = _canonical(f"typing.{text}")
            else:
                return None
        if not identity or identity in _UNSAFE or identity == "typing.TypeVar":
            return None
        spelling, imports = self._spelling(identity)
        return TypeTerm(TypeKind.ATOM, identity, spelling, imports=imports)

    def _parameter(
        self, name: str, binding: _Binding, index: int, active: frozenset[str]
    ) -> TypeTerm | None:
        node = binding.node
        identity = (
            binding.identity or f"parameter:{self.file_path}:{getattr(node, 'lineno', 0)}:{name}"
        )
        if identity in active:
            return None
        bound: ast.expr | None = None
        constraints: tuple[ast.expr, ...] = ()
        if binding.kind == "parameter":
            if type(node).__name__ != "TypeVar" or getattr(node, "default_value", None) is not None:
                return None
            raw_bound: object = getattr(node, "bound", None)
            if isinstance(raw_bound, ast.Tuple):
                constraints = tuple(raw_bound.elts)
            elif isinstance(raw_bound, ast.expr):
                bound = raw_bound
        elif isinstance(node, ast.Call):
            constructor = _dotted(node.func)
            if (
                constructor is None
                or self._import_identity(constructor, index + 1) != "typing.TypeVar"
                or not node.args
                or not isinstance(node.args[0], ast.Constant)
                or node.args[0].value != name
            ):
                return None
            constraints = tuple(node.args[1:])
            for keyword in node.keywords:
                if keyword.arg == "bound":
                    bound = keyword.value
                elif (
                    keyword.arg not in ("covariant", "contravariant", "infer_variance")
                    or not isinstance(keyword.value, ast.Constant)
                    or type(keyword.value.value) is not bool
                ):
                    return None
        else:
            return None
        if (bound is not None and constraints) or len(constraints) == 1:
            return None
        seen = active | {identity}
        domain_resolver = self._module_resolver or self
        resolved_bound = (
            _informative(domain_resolver._resolve(bound, index + 1, seen, False))
            if bound is not None
            else None
        )
        resolved_constraints = tuple(
            _informative(domain_resolver._resolve(item, index + 1, seen, False))
            for item in constraints
        )
        if (bound is not None and resolved_bound is None) or any(
            item is None for item in resolved_constraints
        ):
            return None
        terms = tuple(item for item in resolved_constraints if item is not None)
        if any(
            contains_type_parameter(item)
            for item in terms + ((resolved_bound,) if resolved_bound else ())
        ):
            return None
        parameter = TypeParameter(identity, resolved_bound, terms)
        term = TypeTerm(TypeKind.ATOM, identity, name, parameter=parameter)
        for class_parameter in reversed(self._source_class_parameters):
            if class_parameter.declaration_identity == identity:
                return class_parameter.term
        return term

    def _resolve(
        self,
        node: ast.expr,
        limit: int,
        active: frozenset[str],
        revealed: bool,
        literal: bool = False,
    ) -> TypeTerm | None:
        if not self.scopes or self.host is None:
            return None
        if isinstance(node, OpaqueType):
            return TypeTerm(TypeKind.ATOM, f"opaque:{self.file_path}:{node.spelling}")
        if isinstance(node, ast.Constant):
            if isinstance(node.value, str) and not literal:
                try:
                    expression = ast.parse(node.value, mode="eval").body
                except (SyntaxError, ValueError):
                    return None
                return self._resolve(expression, limit, active, revealed)
            if node.value is None or node.value is Ellipsis or literal:
                return TypeTerm(
                    TypeKind.CONSTANT,
                    "..." if node.value is Ellipsis else repr(node.value),
                )
            return None
        if (
            literal
            and isinstance(node, ast.UnaryOp)
            and isinstance(node.op, (ast.USub, ast.UAdd))
            and isinstance(node.operand, ast.Constant)
            and type(node.operand.value) is int
        ):
            return TypeTerm(TypeKind.CONSTANT, ast.unparse(node))
        dotted = _dotted(node)
        if dotted is not None:
            return self._name(dotted, limit, active, revealed)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            left = self._resolve(node.left, limit, active, revealed)
            right = self._resolve(node.right, limit, active, revealed)
            return _union((left, right))
        if isinstance(node, (ast.List, ast.Tuple)):
            children = tuple(
                self._resolve(item, limit, active, revealed, literal) for item in node.elts
            )
            if any(item is None for item in children):
                return None
            return TypeTerm(
                TypeKind.LIST if isinstance(node, ast.List) else TypeKind.TUPLE,
                children=tuple(item for item in children if item is not None),
            )
        if isinstance(node, ast.Subscript):
            constructor = _dotted(node.value)
            canonical = self._import_identity(constructor, limit) if constructor else None
            if revealed and constructor and canonical is None:
                canonical = self._revealed_identity(constructor, limit)
            args = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
            if not args:
                return None
            if canonical in ("typing.Optional", "typing.Union"):
                if canonical == "typing.Optional" and len(args) != 1:
                    return None
                members = tuple(self._resolve(arg, limit, active, revealed) for arg in args)
                if canonical == "typing.Optional":
                    members += (TypeTerm(TypeKind.CONSTANT, "None"),)
                return _union(members)
            head = self._resolve(node.value, limit, active, revealed)
            if head is None or head.parameter is not None or head.name == "typing.Annotated":
                return None
            children = tuple(
                self._resolve(arg, limit, active, revealed, head.name == "typing.Literal")
                for arg in args
            )
            if any(item is None for item in children):
                return None
            return TypeTerm(
                TypeKind.APPLY,
                children=(head,) + tuple(item for item in children if item is not None),
            )
        return None


def _typing_name(identity: str) -> str | None:
    """The name ``typing`` exports for ``identity``, when it exports one."""
    if identity.startswith("typing."):
        name = identity.removeprefix("typing.")
        return name if name.isidentifier() and hasattr(typing, name) else None
    for alias, canonical in _ALIASES.items():
        if canonical == identity:
            return alias.removeprefix("typing.")
    return None


def _informative(term: TypeTerm | None) -> TypeTerm | None:
    """``term``, unless it is ``Any`` as a whole, which states no relationship at all."""
    if term is not None and term.kind is TypeKind.ATOM and term.name == _ANY:
        return None
    return term


def contains_type_parameter(term: TypeTerm) -> bool:
    """Whether a type includes a source or freshly generated generic binder."""
    return bool(type_parameter_identities(term))


def _parameter_terms(term: TypeTerm) -> tuple[TypeTerm, ...]:
    own = (term,) if term.parameter is not None else ()
    return own + tuple(
        parameter for child in term.children for parameter in _parameter_terms(child)
    )


def type_parameter_identities(term: TypeTerm) -> frozenset[str]:
    """Every scoped parameter identity mentioned by an immutable type term."""
    own = frozenset((term.parameter.identity,)) if term.parameter is not None else frozenset()
    return own.union(*(type_parameter_identities(child) for child in term.children))


def spellable(term: TypeTerm) -> bool:
    """Whether the helper's module can write ``term``: every name in it has a spelling there."""
    if term.kind is TypeKind.ATOM:
        return bool(term.spelling)
    return all(spellable(child) for child in term.children)


def required_imports(terms: Sequence[TypeTerm]) -> tuple[tuple[str, str], ...]:
    """The ``(module, name)`` imports writing ``terms`` needs, each once, in order of use."""
    found: dict[tuple[str, str], None] = {}

    def collect(term: TypeTerm) -> None:
        found.update(dict.fromkeys(term.imports))
        for child in term.children:
            collect(child)

    for term in terms:
        collect(term)
    return tuple(found)


def _union(members: tuple[TypeTerm | None, ...]) -> TypeTerm | None:
    if not members or any(member is None for member in members):
        return None
    flattened: set[TypeTerm] = set()
    for member in members:
        if member is not None:
            flattened.update(member.children if member.kind is TypeKind.UNION else (member,))
    ordered = tuple(sorted(flattened, key=_term_key))
    return ordered[0] if len(ordered) == 1 else TypeTerm(TypeKind.UNION, children=ordered)


def _term_key(term: TypeTerm) -> str:
    """Order unions by identity, never the aliases chosen for their rendering."""
    return repr((term.kind.value, term.name, tuple(_term_key(child) for child in term.children)))


def render_type(term: TypeTerm) -> ast.expr:
    """Render a resolved term; callers replace source parameters before emission.

    Raises ``ValueError`` for a term that is not :func:`spellable`: a name the
    helper's module cannot write is never replaced by one it would misread.
    """
    if term.kind is TypeKind.ATOM:
        if not term.spelling:
            raise ValueError(f"{term.name} has no spelling where the helper is defined")
        return ast.parse(term.spelling, mode="eval").body
    if term.kind is TypeKind.CONSTANT:
        return ast.parse(term.name, mode="eval").body
    children = [render_type(child) for child in term.children]
    if term.kind is TypeKind.APPLY:
        argument = (
            children[1] if len(children) == 2 else ast.Tuple(elts=children[1:], ctx=ast.Load())
        )
        return ast.Subscript(value=children[0], slice=argument, ctx=ast.Load())
    if term.kind is TypeKind.LIST:
        return ast.List(elts=children, ctx=ast.Load())
    if term.kind is TypeKind.TUPLE:
        return ast.Tuple(elts=children, ctx=ast.Load())
    if term.kind is TypeKind.UNION and children:
        result = children[0]
        for child in children[1:]:
            result = ast.BinOp(left=result, op=ast.BitOr(), right=child)
        return result
    raise ValueError(f"Invalid type term: {term!r}")
