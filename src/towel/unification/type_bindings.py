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

Only the two supplied source strings are inspected. Ambiguous bindings and names
the helper's module cannot spell are refused. Type-parameter atoms are the one
exception: their declaration metadata permits the caller to introduce a fresh
helper parameter rather than accidentally capturing the original binder.
"""

from __future__ import annotations

import ast
import builtins
from dataclasses import dataclass, field
from enum import Enum
import os
import re
from collections.abc import Callable


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
    """Canonical type structure; host spelling does not affect equality."""

    kind: TypeKind
    name: str = ""
    spelling: str = field(default="", compare=False)
    children: tuple[TypeTerm, ...] = ()
    parameter: TypeParameter | None = None


_BUILTINS = frozenset(name for name, value in vars(builtins).items() if isinstance(value, type))
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
    f"typing.{name}" for name in ("Any", "Unknown", "ParamSpec", "TypeVarTuple", "Self")
)
_QUOTED = re.compile(r"""("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')""")


def _outside_strings(text: str, rewrite: Callable[[str], str]) -> str:
    """Checker decorations must never rewrite the contents of Literal strings."""
    return "".join(
        part if index % 2 else rewrite(part) for index, part in enumerate(_QUOTED.split(text))
    )


def _split_top_level(text: str, separator: str = ",") -> tuple[str, ...] | None:
    """Split checker signature fields, respecting nested types and literal text."""
    quoted_spans = {
        index for match in _QUOTED.finditer(text) for index in range(match.start(), match.end())
    }
    stack: list[str] = []
    start = 0
    parts: list[str] = []
    for index, character in enumerate(text):
        if index in quoted_spans:
            continue
        if character in "([{":
            stack.append(character)
        elif character in ")]}":
            if not stack or stack.pop() != {")": "(", "]": "[", "}": "{"}[character]:
                return None
        elif character == separator and not stack:
            parts.append(text[start:index].strip())
            start = index + 1
    if stack:
        return None
    if text[start:].strip():
        parts.append(text[start:].strip())
    return tuple(parts)


def _strip_literal_markers(text: str) -> str:
    """Remove mypy's inferred-literal suffix, only on complete Literal forms."""
    quoted = {
        index for match in _QUOTED.finditer(text) for index in range(match.start(), match.end())
    }
    removed: set[int] = set()
    for match in re.finditer(r"\bLiteral\[", text):
        if match.start() in quoted:
            continue
        depth = 1
        for index in range(match.end(), len(text)):
            if index in quoted:
                continue
            if text[index] == "[":
                depth += 1
            elif text[index] == "]":
                depth -= 1
                if depth == 0:
                    if text[index + 1 : index + 2] == "?":
                        removed.add(index + 1)
                    break
    return "".join(character for index, character in enumerate(text) if index not in removed)


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


class _Collector(ast.NodeVisitor):
    """Record every write in one scope; repeated or conditional writes are unsafe."""

    def __init__(self, file_path: str, node: ast.AST) -> None:
        self.file_path = file_path
        self.scope_node = node
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
            base = os.path.dirname(self.file_path)
            for _ in range(node.level - 1):
                base = os.path.dirname(base)
            module = f"relative:{base}/{module}"
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
        kind = "class" if isinstance(self.scope_node, ast.Module) else "unknown"
        self.bind(node.name, kind, f"local:{self.file_path}:{node.lineno}:{node.name}", node)

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


def _scope(
    node: ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    file_path: str,
) -> _Scope:
    collector = _Collector(file_path, node)
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
        collector.direct = isinstance(
            statement,
            (
                ast.Import,
                ast.ImportFrom,
                ast.Assign,
                ast.FunctionDef,
                ast.AsyncFunctionDef,
                ast.ClassDef,
            ),
        )
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


def _scopes(module: ast.Module, file_path: str, line: int) -> tuple[_Scope, ...]:
    result = [_scope(module, file_path)]
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
    """Resolve one site's types in its lexical scope for a module-level helper."""

    def __init__(
        self, source: str, file_path: str, line: int, host_source: str, host_file: str
    ) -> None:
        self.file_path = os.path.abspath(file_path)
        self.host_file = os.path.abspath(host_file)
        try:
            module = ast.parse(source)
            host = ast.parse(host_source)
        except (SyntaxError, ValueError):
            self.scopes: tuple[_Scope, ...] = ()
            self.host: _Scope | None = None
        else:
            self.scopes = _scopes(module, self.file_path, line)
            self.host = _scope(host, self.host_file)

    def resolve(self, annotation: ast.expr) -> TypeTerm | None:
        """Resolve a declared annotation, including a quoted forward annotation."""
        return self._resolve(annotation, len(self.scopes), frozenset(), False)

    def resolve_revealed(self, text: str) -> TypeTerm | None:
        """Resolve checker spelling, validating decorated type variables in scope."""
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
        text = _strip_literal_markers(text)
        if text.startswith(("def ", "(")):
            return self._callable(text)
        try:
            node = ast.parse(text, mode="eval").body
        except (SyntaxError, ValueError):
            return None
        return self._resolve(node, len(self.scopes), frozenset(), True)

    def _callable(self, text: str) -> TypeTerm | None:
        """Preserve fixed positional callable structure without erasing domains."""
        match = re.fullmatch(r"(?:def )?\((.*)\)\s*->\s*(.+)", text)
        if match is None:
            return None
        fields = _split_top_level(match.group(1))
        spelling = self._host_spelling("collections.abc.Callable")
        if fields is None or spelling is None:
            return None
        arguments: list[TypeTerm] = []
        for parameter_text in fields:
            if parameter_text == "/":
                continue
            pieces = _split_top_level(parameter_text, ":")
            if (
                parameter_text.startswith("*")
                or pieces is None
                or len(pieces) not in (1, 2)
                or (len(pieces) == 2 and not pieces[0].isidentifier())
            ):
                return None
            kind = self.resolve_revealed(pieces[-1])
            if kind is None:
                return None
            arguments.append(kind)
        result = self.resolve_revealed(match.group(2))
        if result is None:
            return None
        return TypeTerm(
            TypeKind.APPLY,
            children=(
                TypeTerm(TypeKind.ATOM, "collections.abc.Callable", spelling),
                TypeTerm(TypeKind.LIST, children=tuple(arguments)),
                result,
            ),
        )

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

    def _name(
        self, text: str, limit: int, active: frozenset[str], revealed: bool
    ) -> TypeTerm | None:
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
        elif revealed and text.startswith(
            ("builtins.", "typing.", "typing_extensions.", "collections.abc.")
        ):
            identity = _canonical(text)
        elif revealed and "." in text and self._host_spelling(_canonical(text)):
            identity = _canonical(text)
        else:
            return None
        if not identity or identity in _UNSAFE or identity == "typing.TypeVar":
            return None
        spelling = self._host_spelling(identity)
        return TypeTerm(TypeKind.ATOM, identity, spelling) if spelling else None

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
        resolved_bound = self._resolve(bound, index + 1, seen, False) if bound is not None else None
        resolved_constraints = tuple(
            self._resolve(item, index + 1, seen, False) for item in constraints
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
        return TypeTerm(TypeKind.ATOM, identity, name, parameter=parameter)

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
                canonical = _canonical(constructor)
                if constructor == "Literal" and self._lookup("Literal", limit) is None:
                    canonical = "typing.Literal"
            args = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
            if canonical in ("typing.Optional", "typing.Union"):
                if canonical == "typing.Optional" and len(args) != 1:
                    return None
                members = tuple(self._resolve(arg, limit, active, revealed) for arg in args)
                if canonical == "typing.Optional":
                    members += (TypeTerm(TypeKind.CONSTANT, "None"),)
                return _union(members)
            head = self._resolve(node.value, limit, active, revealed)
            if revealed and canonical == "typing.Literal" and head is None:
                # A literal value always belongs to its concrete builtin type.
                # Keep exact Literal structure when the host can spell it; do
                # not invent a new typing import merely for inferred constants.
                widened: list[TypeTerm | None] = []
                for argument in args:
                    try:
                        value = ast.literal_eval(argument)
                    except (ValueError, TypeError, SyntaxError):
                        return None
                    if value is None:
                        widened.append(TypeTerm(TypeKind.CONSTANT, "None"))
                    elif type(value) in (bool, int, str, bytes):
                        widened.append(
                            self._name(f"builtins.{type(value).__name__}", limit, active, True)
                        )
                    else:
                        return None
                return _union(tuple(widened))
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


def contains_type_parameter(term: TypeTerm) -> bool:
    """Whether a type includes a source or freshly generated generic binder."""
    return term.parameter is not None or any(
        contains_type_parameter(child) for child in term.children
    )


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
    """Render a resolved term; callers replace source parameters before emission."""
    if term.kind is TypeKind.ATOM:
        return ast.parse(term.spelling or term.name, mode="eval").body
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
