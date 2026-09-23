"""Plan helper renames lexically, without rewriting unrelated identifier tokens.

A module-level helper is renamed together with the importers that bind it;
a class-private helper (``__extracted_func_0`` in a class body, stored as
``_Box__extracted_func_0``) is named by its class, ``Box.__extracted_func_0``,
and renamed with its references in that class's body, to a name that is
class-private too; an older class-level helper defined once is renamed with
every attribute reference; a helper's parameter is renamed within the
helper's own scope. Only statically resolved names are supported: renaming
needs every consumer in the selected source tree, and dynamic lookup or an
escaping module object is rejected where it is visible in that tree.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
import io
import keyword
from pathlib import Path
import tokenize
import unicodedata
from typing import Iterable, Literal, NamedTuple, Sequence, cast

from .changes import ChangePlan
from .project_layout import ProjectLayout
from .source_text import decode_source
from .source_files import python_sources
from .unification.class_private import (
    class_qualnames,
    is_class_private,
    mangled,
    mangling_classes,
    mangling_prefix,
)
from .unification.models import GENERATED_HELPER_NAME, FunctionNode
from .unification.statement_facts import import_binding_names, imported_binding_name
from .unification.visitors import (
    OwnScopeVisitor,
    ScopeVisitor,
    visit_comprehension_generators,
    visit_comprehension_result,
)


def _class_head_expressions(node: ast.ClassDef) -> Iterable[ast.AST]:
    """The expressions a class statement evaluates in the enclosing scope."""
    yield from node.bases
    yield from node.decorator_list
    for item in node.keywords:
        yield item.value


def _outer_expressions(node: FunctionNode | ast.Lambda) -> Iterable[ast.AST]:
    yield from node.args.defaults
    yield from (value for value in node.args.kw_defaults if value is not None)
    for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
        if argument.annotation is not None:
            yield argument.annotation
    for optional_argument in (node.args.vararg, node.args.kwarg):
        if optional_argument is not None and optional_argument.annotation is not None:
            yield optional_argument.annotation
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        yield from node.decorator_list
        if node.returns is not None:
            yield node.returns


class _Bindings(OwnScopeVisitor):
    def __init__(self) -> None:
        self.local: set[str] = set()
        self.global_names: set[str] = set()
        self.nonlocal_names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.local.add(node.id)

    def visit_Global(self, node: ast.Global) -> None:
        self.global_names.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.nonlocal_names.update(node.names)

    def _nested_function(self, node: FunctionNode) -> None:
        self.local.add(node.name)
        for expression in _outer_expressions(node):
            self.visit(expression)

    def _lambda(self, node: ast.Lambda) -> None:
        for expression in _outer_expressions(node):
            self.visit(expression)

    def _nested_class(self, node: ast.ClassDef) -> None:
        self.local.add(node.name)
        for expression in _class_head_expressions(node):
            self.visit(expression)

    def visit_Import(self, node: ast.Import) -> None:
        self.local.update(import_binding_names(node))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.local.update(import_binding_names(node))

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.local.add(node.name)
        self.generic_visit(node)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name:
            self.local.add(node.name)
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name:
            self.local.add(node.name)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest:
            self.local.add(node.rest)
        self.generic_visit(node)

    def _comprehension(
        self, node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp
    ) -> None:
        # Comprehension iteration targets are local to the comprehension, while
        # assignment expressions bind in its containing non-comprehension scope.
        visit_comprehension_generators(self, node)
        visit_comprehension_result(self, node)


ScopeKind = Literal["module", "function", "class", "comprehension"]
"""The kinds of lexical scope the rename planner distinguishes."""


@dataclass(frozen=True, eq=False)
class _Scope:
    parent: _Scope | None
    kind: ScopeKind
    bindings: frozenset[str]
    globals: frozenset[str]
    nonlocals: frozenset[str]
    parameters: frozenset[str] = frozenset()


def _scope(
    parent: _Scope | None,
    kind: ScopeKind,
    nodes: Iterable[ast.AST],
    args: ast.arguments | None = None,
) -> _Scope:
    collector = _Bindings()
    for node in nodes:
        collector.visit(node)
    parameters: set[str] = set()
    if args is not None:
        parameters.update(arg.arg for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs))
        collector.local.update(parameters)
        parameters.update(arg.arg for arg in (args.vararg, args.kwarg) if arg is not None)
        collector.local.update(parameters)
    return _Scope(
        parent,
        kind,
        frozenset(collector.local - collector.global_names - collector.nonlocal_names),
        frozenset(collector.global_names),
        frozenset(collector.nonlocal_names),
        frozenset(parameters),
    )


class _Scopes(ScopeVisitor):
    def __init__(self, tree: ast.Module) -> None:
        self.root = _scope(None, "module", tree.body)
        self.current = self.root
        self._previous: list[_Scope] = []
        self.nodes: dict[ast.AST, _Scope] = {}
        self.visit(tree)

    def visit(self, node: ast.AST) -> None:
        self.nodes[node] = self.current
        super().visit(node)

    def _visit_definition_head(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda
    ) -> None:
        expressions = (
            _class_head_expressions(node)
            if isinstance(node, ast.ClassDef)
            else _outer_expressions(node)
        )
        for expression in expressions:
            self.visit(expression)

    def _enter_scope(self, node: ast.AST) -> None:
        self._previous.append(self.current)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self.current = _scope(self.current, "function", node.body, node.args)
        elif isinstance(node, ast.Lambda):
            self.current = _scope(self.current, "function", [node.body], node.args)
        elif isinstance(node, ast.ClassDef):
            self.current = _scope(self.current, "class", node.body)
        else:
            comprehension = cast(ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp, node)
            collector = _Bindings()
            for generator in comprehension.generators:
                collector.visit(generator.target)
            self.current = _Scope(
                self.current, "comprehension", frozenset(collector.local), frozenset(), frozenset()
            )

    def _leave_scope(self, node: ast.AST) -> None:
        self.current = self._previous.pop()

    def _bind_target(self, target: ast.AST) -> None:
        self.visit(target)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        target_scope = self.current
        while target_scope.kind == "comprehension" and target_scope.parent is not None:
            target_scope = target_scope.parent
        self.nodes[node.target] = target_scope


def _private_in_class(scope: _Scope, name: str) -> bool:
    if not name.startswith("__") or name.endswith("__"):
        return False
    current: _Scope | None = scope
    while current is not None:
        if current.kind == "class":
            return True
        current = current.parent
    return False


def _module_scope(scope: _Scope) -> _Scope:
    while scope.parent is not None:
        scope = scope.parent
    return scope


def _resolve(scope: _Scope, name: str) -> _Scope:
    current = scope
    while (parent := current.parent) is not None:
        if name in current.globals:
            return _module_scope(current)
        if name in current.bindings and name not in current.nonlocals:
            return current
        # Class namespaces do not provide lexical bindings to their methods,
        # comprehensions or nested classes.
        while parent.kind == "class" and parent.parent is not None:
            parent = parent.parent
        current = parent
    return current


@dataclass(frozen=True)
class _Module:
    path: Path
    name: str
    # The bytes on disk, which the change plan starts from and whose encoding
    # the rewritten file keeps.
    original: bytes
    # The decoded source as UTF-8, which the syntax tree's column offsets index.
    raw: bytes
    tree: ast.Module
    scopes: _Scopes


_DYNAMIC_NAMESPACE_NAMES = frozenset({"globals", "locals", "vars", "dir", "eval", "exec"})
_DYNAMIC_ATTRIBUTE_CALLS = frozenset({"getattr", "setattr", "hasattr", "delattr"})
_REFLECTIVE_BUILTINS = _DYNAMIC_NAMESPACE_NAMES | _DYNAMIC_ATTRIBUTE_CALLS


class _BuiltinReferences:
    """Lexically resolved reflective builtins, including imported and assigned aliases.

    Alias sets grow monotonically: a rebinding can make a refusal conservative,
    but cannot hide an earlier reflective use. Shadowed builtin spellings alone
    are not aliases. The same policy covers module, method and parameter names.
    """

    def __init__(self, module: _Module) -> None:
        self.scopes = module.scopes
        self.aliases: dict[tuple[_Scope, str], frozenset[str]] = {}
        nodes = list(ast.walk(module.tree))
        for node in nodes:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "builtins":
                        self._add(node, alias.asname or alias.name, frozenset({"builtins"}))
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == "builtins":
                for alias in node.names:
                    if alias.name in _REFLECTIVE_BUILTINS:
                        self._add(node, alias.asname or alias.name, frozenset({alias.name}))
        grew = True
        while grew:
            grew = False
            for node in nodes:
                if (
                    isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
                    and node.value is not None
                ):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        grew = self._assignment(target, node.value) or grew

    def _assignment(self, target: ast.AST, value: ast.AST) -> bool:
        if isinstance(target, ast.Name):
            return self._add(target, target.id, self.resolve(value))
        if isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List)):
            if len(target.elts) == len(value.elts):
                updates = [
                    self._assignment(left, right) for left, right in zip(target.elts, value.elts)
                ]
                return any(updates)
        return False

    def _add(self, node: ast.AST, name: str, origins: frozenset[str]) -> bool:
        key = (_resolve(self.scopes.nodes[node], name), name)
        previous = self.aliases.get(key, frozenset())
        updated = previous | origins
        self.aliases[key] = updated
        return updated != previous

    def resolve(self, node: ast.AST) -> frozenset[str]:
        """Reflective builtins this expression may denote, or the builtins module."""
        if isinstance(node, ast.Name):
            scope = _resolve(self.scopes.nodes[node], node.id)
            origins = self.aliases.get((scope, node.id), frozenset())
            if (
                scope is self.scopes.root
                and node.id not in scope.bindings
                and node.id in _REFLECTIVE_BUILTINS
            ):
                origins |= {node.id}
            return origins
        if isinstance(node, ast.Attribute) and node.attr in _REFLECTIVE_BUILTINS:
            if "builtins" in self.resolve(node.value):
                return frozenset({node.attr})
        return frozenset()

    def namespace_read(self, node: ast.AST) -> bool:
        return bool(self.resolve(node) & _DYNAMIC_NAMESPACE_NAMES)

    def looks_up(self, call: ast.Call, names: Iterable[str]) -> bool:
        if not self.resolve(call.func) & _DYNAMIC_ATTRIBUTE_CALLS:
            return False
        if len(call.args) < 2:
            return True
        name = call.args[1]
        return not isinstance(name, ast.Constant) or (
            isinstance(name.value, str) and name.value in names
        )


class _Edits:
    def __init__(self, module: _Module) -> None:
        self.module = module
        self.lines = module.raw.splitlines(keepends=True)
        self.offsets = [0]
        for line in self.lines:
            self.offsets.append(self.offsets[-1] + len(line))
        self.changes: dict[tuple[int, int], bytes] = {}

    def add(self, line: int, column: int, end_line: int, end_column: int, replacement: str) -> None:
        span = (self.offsets[line - 1] + column, self.offsets[end_line - 1] + end_column)
        encoded = replacement.encode("utf-8")
        if span in self.changes and self.changes[span] != encoded:
            raise ValueError("Conflicting rename specifications")
        self.changes[span] = encoded

    def node(self, node: ast.expr | ast.alias, replacement: str) -> None:
        if node.end_lineno is None or node.end_col_offset is None:
            raise ValueError("a parsed node carries its end position")
        self.add(node.lineno, node.col_offset, node.end_lineno, node.end_col_offset, replacement)

    def identifier(self, node: FunctionNode | ast.Global, old: str, new: str) -> None:
        source = self.module.raw.decode("utf-8")
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.start[0] < node.lineno:
                continue
            if token.start[0] > (node.end_lineno or node.lineno):
                break
            if token.type == tokenize.NAME and token.string == old:
                line = self.lines[token.start[0] - 1].decode("utf-8")
                self.add(
                    token.start[0],
                    len(line[: token.start[1]].encode("utf-8")),
                    token.end[0],
                    len(line[: token.end[1]].encode("utf-8")),
                    new,
                )
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    return

    def render(self) -> bytes:
        result = self.module.raw
        last_start = len(result)
        for (start, end), replacement in sorted(self.changes.items(), reverse=True):
            if end > last_start:
                raise ValueError("Overlapping rename edits")
            result = result[:start] + replacement + result[end:]
            last_start = start
        return result


def _rename_layout(target: Path) -> ProjectLayout:
    """Honor packaging configuration, but never infer imports from ambient Git roots.

    Without packaging metadata the explicit target is the import root. If the
    target itself is a classic package, its parent supplies that package name.
    An ancestor .git directory says nothing about Python's import search path.
    """
    directory = target.resolve()
    for candidate in (directory, *directory.parents):
        if any(
            (candidate / marker).is_file() for marker in ("pyproject.toml", "setup.cfg", "setup.py")
        ):
            return ProjectLayout.discover(directory)
    import_root = directory.parent if (directory / "__init__.py").is_file() else directory
    return ProjectLayout(project_root=directory, source_roots=[import_root])


def _module_name(layout: ProjectLayout, path: Path) -> str:
    name = layout.module_name_for(path)
    if name is None:
        raise ValueError(f"Cannot resolve module path for rename: {path}")
    return name.removesuffix(".__init__") if name != "__init__" else ""


class _ParameterSpec(NamedTuple):
    """A ``helper.parameter`` rename; ``owner`` is set when a class-private helper is meant."""

    helper: str
    parameter: str
    new: str
    file_filter: Path | None
    owner: _PrivateHelper | None = None

    @property
    def key(self) -> tuple[str, str]:
        """How the specification names its helper and parameter, for the missing-parameter report."""
        qualified = self.helper if self.owner is None else f"{self.owner.qualname}.{self.helper}"
        return qualified, self.parameter


def plan_renames(
    target: Path, specifications: Sequence[tuple[str, str, Path | None]]
) -> tuple[ChangePlan, int]:
    """Validate and plan all selected module definitions and their static consumers.

    A specification ``(old, new, file)`` renames a helper function. When
    ``old`` is ``helper.parameter`` it renames that parameter within the
    helper's own scope instead. ``Class.helper`` names a class-private
    helper by its class (a nested class by its dotted qualname), and
    ``Class.helper.parameter`` one of its parameters. Module-level helpers
    are renamed with their static importers; a class-private helper with its
    references in its class's body, to a name that must be class-private too
    (:func:`_validate_private_renames`); an older class-level helper is
    renamed together with every attribute reference to it, which is sound
    only because generated helper names are unique across the project.
    """
    for old, new, _ in specifications:
        _validate_names((*old.split("."), new))
    modules = _load_rename_modules(target)
    private, specifications = _class_private_specifications(modules, specifications)
    function_specifications, parameter_specifications = _split_specifications(specifications)
    parameter_specifications += [
        _ParameterSpec(helper.name, parameter, new, helper.module.path, helper)
        for helper, parameter, new in private
        if parameter is not None
    ]
    parameter_calls = _ParameterCallsites(modules, parameter_specifications)
    by_name = {module.name: module for module in modules}
    selected, method_specifications = _select_definitions(modules, function_specifications)
    _extend_to_generated_importers(modules, selected)
    _refuse_unresolved_helper_imports(modules, selected)
    method_renames = _validate_method_renames(modules, method_specifications)
    private_renames = _validate_private_renames(
        modules, [(helper, new) for helper, parameter, new in private if parameter is None]
    )
    if not selected and not method_renames and not private_renames and not parameter_specifications:
        return ChangePlan(()), 0
    destinations: set[tuple[str, str]] = set()
    for (module_name, _), new in selected.items():
        if (module_name, new) in destinations:
            raise ValueError(f"Several helpers would share the same new name: {module_name}:{new}")
        destinations.add((module_name, new))
    before: dict[str, bytes] = {}
    after: dict[str, str] = {}
    count = 0
    parameters_found: set[tuple[str, str]] = set()
    for module in modules:
        edits = _plan_module(module, selected, by_name)
        _plan_method_renames(module, method_renames, edits)
        _plan_private_renames(module, private_renames, edits)
        parameters_found |= _plan_parameter_renames(module, parameter_specifications, edits)
        parameter_calls.plan(module, edits)
        content = edits.render()
        if content != module.raw:
            before[str(module.path)] = module.original
            after[str(module.path)] = content.decode("utf-8")
            count += len(edits.changes)
    missing = {spec.key for spec in parameter_specifications if spec.key not in parameters_found}
    if missing:
        raise ValueError(f"No helper defines these parameters: {sorted(missing)}")
    return ChangePlan.from_sources(before, after), count


def _validate_names(names: Iterable[str]) -> None:
    """Refuse a name that is not a normalized, non-keyword Python identifier."""
    if any(
        not name.isidentifier()
        or keyword.iskeyword(name)
        or unicodedata.normalize("NFKC", name) != name
        for name in names
    ):
        raise ValueError("Renamings require normalized, non-keyword Python identifiers")


def _split_specifications(
    specifications: Sequence[tuple[str, str, Path | None]],
) -> tuple[list[tuple[str, str, Path | None]], list[_ParameterSpec]]:
    """Separate helper renames from ``helper.parameter`` renames."""
    parameter_specifications: list[_ParameterSpec] = []
    function_specifications: list[tuple[str, str, Path | None]] = []
    for old, new, file_filter in specifications:
        helper, _, parameter = old.partition(".")
        if "." in parameter:
            raise ValueError(f"{old} names no class-private helper defined in that class")
        if parameter:
            parameter_specifications.append(_ParameterSpec(helper, parameter, new, file_filter))
        else:
            function_specifications.append((old, new, file_filter))
    return function_specifications, parameter_specifications


@dataclass(frozen=True, eq=False)
class _PrivateHelper:
    """A class-private helper named by its class: ``Box.__extracted_func_0`` in ``module``."""

    module: _Module
    owner: ast.ClassDef
    qualname: str
    function: FunctionNode

    @property
    def name(self) -> str:
        return self.function.name

    @property
    def stored(self) -> str:
        """The name the class stores it under, ``_Box__extracted_func_0``."""
        return mangled(self.function.name, self.owner.name)


def _class_private_specifications(
    modules: Sequence[_Module], specifications: Sequence[tuple[str, str, Path | None]]
) -> tuple[list[tuple[_PrivateHelper, str | None, str]], list[tuple[str, str, Path | None]]]:
    """The specifications naming a class-private helper by its class, and the others.

    ``Box.__extracted_func_0`` and ``Box.__extracted_func_0.__param_0`` are
    read against the modules: a key names a class-private helper when a class
    of that qualname defines a function of that class-private name directly
    in its body. It must name exactly one; a key read no such way is left for
    the other kinds of rename.
    """
    private: list[tuple[_PrivateHelper, str | None, str]] = []
    remaining: list[tuple[str, str, Path | None]] = []
    for old, new, file_filter in specifications:
        parts = old.split(".")
        found: list[tuple[_PrivateHelper, str | None]] = []
        for split in range(1, len(parts)):
            qualname, helper, rest = ".".join(parts[:split]), parts[split], parts[split + 1 :]
            if len(rest) > 1 or not is_class_private(helper):
                continue
            for module in modules:
                if file_filter is not None and module.path.resolve() != file_filter.resolve():
                    continue
                for owner, class_qualname in class_qualnames(module.tree).items():
                    if class_qualname != qualname:
                        continue
                    found.extend(
                        (_PrivateHelper(module, owner, qualname, item), rest[0] if rest else None)
                        for item in owner.body
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and item.name == helper
                    )
        if not found:
            remaining.append((old, new, file_filter))
            continue
        if len(found) > 1:
            raise ValueError(
                f"{old} names more than one class-private helper; name the file as"
                f" 'path.py:{old}', and define the helper once in its class"
            )
        helper_found, parameter = found[0]
        private.append((helper_found, parameter, new))
    return private, remaining


def _spelled_names(node: ast.AST) -> tuple[str, ...]:
    """The identifiers ``node`` itself spells, each of which a class body would mangle."""
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, ast.Attribute):
        return (node.attr,)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return (node.name,)
    if isinstance(node, ast.arg):
        return (node.arg,)
    if isinstance(node, ast.keyword):
        return (node.arg,) if node.arg else ()
    if isinstance(node, (ast.Global, ast.Nonlocal)):
        return tuple(node.names)
    if isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)):
        return (node.name,) if node.name else ()
    if isinstance(node, ast.MatchMapping):
        return (node.rest,) if node.rest else ()
    if isinstance(node, ast.alias):
        bound = imported_binding_name(node)
        return (bound,) if bound else ()
    return ()


def _validate_private_renames(
    modules: Sequence[_Module], renames: Sequence[tuple[_PrivateHelper, str]]
) -> list[tuple[_PrivateHelper, str]]:
    """Check each class-private rename against every spelling of both stored names.

    A class-private helper is safe because no subclass can reach it, so the
    new name must be class-private too: ``__name``, not ending in ``__``.
    What the class stores it under, ``_Box__old``, may be spelled nowhere but
    as the definition and as ``obj.__old`` in the class's own body, which the
    rename rewrites: an explicit ``obj._Box__old`` anywhere, an unmangled
    ``__old`` in another class of the same name, a bare ``__old`` or the
    stored name as a string all refuse the batch, as does a lookup by a
    computed name or a namespace read in the class's body, which could build
    the stored name. The new stored name, ``_Box__new``, must be spelled
    nowhere at all.
    """
    if not renames:
        return []
    stored_spellings: dict[str, list[tuple[_Module, ast.AST, ast.ClassDef | None]]] = {}
    strings: set[str] = set()
    dynamic: set[int] = set()
    for module in modules:
        owners = mangling_classes(module.tree)
        builtins = _BuiltinReferences(module)
        for node in ast.walk(module.tree):
            owner = owners.get(node)
            for name in _spelled_names(node):
                stored = mangled(name, owner.name if owner is not None else None)
                stored_spellings.setdefault(stored, []).append((module, node, owner))
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                strings.add(node.value)
            if owner is not None and (
                builtins.namespace_read(node)
                or isinstance(node, ast.Call)
                and builtins.looks_up(node, ())
            ):
                dynamic.add(id(owner))
    accepted: list[tuple[_PrivateHelper, str]] = []
    destinations: set[tuple[int, str]] = set()
    for helper, new in renames:
        if new == helper.name:
            continue
        prefix = mangling_prefix(helper.owner.name)
        where = f"{helper.module.path}:{helper.qualname}.{helper.name}"
        if prefix is None:
            raise ValueError(f"Class {helper.qualname} mangles no name, so {where} is not private")
        if not is_class_private(new):
            raise ValueError(
                f"{where} is class-private, which is what keeps every subclass from"
                f" overriding it, so its new name must be too: start {new!r} with '__'"
                " and do not end it with '__'"
            )
        for module, node, owner in stored_spellings.get(helper.stored, []):
            if node is helper.function or (
                isinstance(node, ast.Attribute) and owner is helper.owner
            ):
                continue
            if owner is not None and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Mangling goes by name, so a class of the same name that is a
                # subclass or base of this one would share the attribute.
                raise ValueError(
                    f"{where}: another class named {owner.name} also defines {helper.stored}"
                    f" at {module.path}:{node.lineno}, and would share it if one derived from"
                    " the other; rename it manually"
                )
            raise ValueError(
                f"{where} is referenced as {helper.stored} outside the attribute references"
                f" in its class's body: {module.path}:{getattr(node, 'lineno', '?')};"
                " rename it manually"
            )
        if helper.stored in strings:
            raise ValueError(
                f"{where} is spelled {helper.stored!r} in a string; rename it manually"
            )
        if id(helper.owner) in dynamic:
            raise ValueError(
                f"Dynamic attribute lookup in {helper.qualname}'s body prevents renaming {where}"
            )
        new_stored = prefix + new
        if new_stored in stored_spellings or new_stored in strings:
            raise ValueError(
                f"New method name {new} in {helper.qualname} already appears in the project"
            )
        if (id(helper.owner), new) in destinations:
            raise ValueError(f"Several helpers of {helper.qualname} would share the new name {new}")
        destinations.add((id(helper.owner), new))
        accepted.append((helper, new))
    return accepted


def _plan_private_renames(
    module: _Module, renames: Sequence[tuple[_PrivateHelper, str]], edits: _Edits
) -> None:
    """Rename each class-private helper of ``module``: its definition and ``obj.__old`` in its class."""
    mine = [(helper, new) for helper, new in renames if helper.module is module]
    if not mine:
        return
    owners = mangling_classes(module.tree)
    for helper, new in mine:
        edits.identifier(helper.function, helper.name, new)
        for node in ast.walk(module.tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == helper.name
                and owners.get(node) is helper.owner
            ):
                if node.end_lineno is None or node.end_col_offset is None:
                    raise ValueError("a parsed node carries its end position")
                edits.add(
                    node.end_lineno,
                    node.end_col_offset - len(node.attr.encode("utf-8")),
                    node.end_lineno,
                    node.end_col_offset,
                    new,
                )


def _load_rename_modules(target: Path) -> list[_Module]:
    """Every module under ``target`` (symlinks excluded), parsed, compiled and scope-analyzed.

    The inventory commands skip a file they cannot read; a rename refuses
    instead, since it rewrites references across the whole tree and a module
    it could not see might hold one. Encodings are honoured the way the
    refactoring commands honour them (byte-order mark, coding cookie).
    """
    layout = _rename_layout(target)
    modules: list[_Module] = []
    for path in python_sources(target):
        original = path.read_bytes()
        text = decode_source(original)
        tree = ast.parse(text, filename=str(path))
        compile(tree, str(path), "exec")
        modules.append(
            _Module(
                path,
                _module_name(layout, path),
                original,
                text.encode("utf-8"),
                tree,
                _Scopes(tree),
            )
        )
    if len({module.name for module in modules}) != len(modules):
        raise ValueError("Ambiguous module paths in rename target")
    return modules


def _select_definitions(
    modules: Sequence[_Module], specifications: Sequence[tuple[str, str, Path | None]]
) -> tuple[dict[tuple[str, str], str], list[tuple[str, str]]]:
    """The module-level definitions each specification names, and the method renames among them.

    A helper defined only inside classes is a method rename; one defined only
    inside functions is refused, since nested scopes are not supported.
    """
    selected: dict[tuple[str, str], str] = {}
    method_specifications: list[tuple[str, str]] = []
    for old, new, file_filter in specifications:
        if old == new:
            continue
        for module in modules:
            if file_filter is not None and module.path.resolve() != file_filter.resolve():
                continue
            definitions = [
                node
                for node in ast.walk(module.tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == old
            ]
            module_definitions = [
                node for node in definitions if module.scopes.nodes[node] is module.scopes.root
            ]
            if definitions and not module_definitions:
                if all(module.scopes.nodes[node].kind == "class" for node in definitions):
                    method_specifications.append((old, new))
                    continue
                raise ValueError(
                    f"Nested helper rename requires explicit scope support: {module.path}:{old}"
                )
            if not module_definitions:
                continue
            if new in module.scopes.root.bindings:
                raise ValueError(
                    f"Rename would collide with existing identifier {new} in {module.path}"
                )
            key = (module.name, old)
            if key in selected and selected[key] != new:
                raise ValueError(f"Conflicting renames for {module.path}:{old}")
            selected[key] = new
    return selected, method_specifications


def _identifiers_in(node: ast.AST) -> set[str]:
    """Every identifier a subtree binds, references, or names as an attribute."""
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.add(child.id)
        elif isinstance(child, ast.arg):
            names.add(child.arg)
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(child.name)
        elif isinstance(child, ast.Attribute):
            names.add(child.attr)
        elif isinstance(child, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and child.name:
            names.add(child.name)
        elif isinstance(child, (ast.Global, ast.Nonlocal)):
            names.update(child.names)
        elif isinstance(child, ast.alias) and (bound := imported_binding_name(child)):
            names.add(bound)
        elif isinstance(child, ast.MatchMapping) and child.rest:
            names.add(child.rest)
    return names


def _import_origin(module: _Module, node: ast.ImportFrom) -> str:
    """Absolute module name an ``ImportFrom`` refers to."""
    package = module.name if module.path.name == "__init__.py" else module.name.rpartition(".")[0]
    if node.level:
        parts = package.split(".") if package else []
        if node.level > len(parts):
            raise ValueError(f"Unresolved relative import in {module.path}")
        return ".".join(
            parts[: len(parts) - node.level + 1] + ([node.module] if node.module else [])
        )
    return node.module or ""


def _extend_to_generated_importers(
    modules: Sequence[_Module], selected: dict[tuple[str, str], str]
) -> None:
    """Rename generated bindings that unaliased imports create in other modules.

    ``from pkg.a import __extracted_func_0`` binds a generated name in the
    importer; that binding was produced with the helper and should follow its
    rename, including through further re-exports. User aliases keep their
    own names. The closure runs to a fixed point over the module graph.
    """
    changed = True
    while changed:
        changed = False
        for module in modules:
            for node in ast.walk(module.tree):
                if (
                    not isinstance(node, ast.ImportFrom)
                    or module.scopes.nodes[node] is not module.scopes.root
                ):
                    continue
                origin = _import_origin(module, node)
                for alias in node.names:
                    new = selected.get((origin, alias.name))
                    if new is None or alias.asname is not None:
                        continue
                    if not GENERATED_HELPER_NAME.fullmatch(alias.name):
                        continue
                    key = (module.name, alias.name)
                    if key in selected:
                        continue
                    selected[key] = new
                    changed = True


def _refuse_unresolved_helper_imports(
    modules: Sequence[_Module], selected: dict[tuple[str, str], str]
) -> None:
    """Refuse when a renamed helper may be imported from a module the rename cannot name.

    The rename matches an import to its definition by module name. An
    out-of-place output sits in a directory named for the output, not the
    package, so ``from pkg.b import __extracted_func_0`` in it names no module
    the rename can see: the definition was renamed and the import left, and
    the adopted package failed to import. Any import of a renamed generated
    name from a module outside the target's names is such a case.
    """
    known = {module.name for module in modules}
    renamed = {name for _, name in selected}
    for module in modules:
        for node in ast.walk(module.tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            origin = _import_origin(module, node)
            if origin in known:
                continue
            for alias in node.names:
                if alias.name in renamed:
                    raise ValueError(
                        f"{module.path} imports {alias.name} from {origin}, which is not a"
                        " module of the rename target under that name. Adopt the output into"
                        " its project first and rename it there."
                    )


def _validate_method_renames(
    modules: Sequence[_Module], specifications: Sequence[tuple[str, str]]
) -> dict[str, str]:
    """Check that each class-level helper is unique and its new name unused anywhere."""
    if not specifications:
        return {}
    project_identifiers: set[str] = set()
    definitions: dict[str, int] = {}
    bare_references: set[str] = set()
    dynamic_calls: list[tuple[_BuiltinReferences, ast.Call]] = []
    for module in modules:
        builtins = _BuiltinReferences(module)
        project_identifiers |= _identifiers_in(module.tree)
        for node in ast.walk(module.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                definitions[node.name] = definitions.get(node.name, 0) + 1
            elif isinstance(node, ast.Name):
                bare_references.add(node.id)
            elif isinstance(node, ast.Call):
                dynamic_calls.append((builtins, node))
    renames: dict[str, str] = {}
    for old, new in specifications:
        if is_class_private(old) or is_class_private(new):
            # Mangling ties a class-private name to its class, which a bare key
            # does not name; the helper's key is ``path.py:Class.name``.
            raise ValueError(
                f"Class-private name mangling ties {old} to its class: rename it by its"
                f" class-qualified key, path.py:Class.{old}, to a name that starts with '__'"
                if is_class_private(old)
                else f"Class-private name mangling prevents safe rename: {old} -> {new}"
            )
        if any(name.startswith("__") and name.endswith("__") for name in (old, new)):
            raise ValueError(f"Special method behavior prevents safe rename: {old} -> {new}")
        if definitions.get(old, 0) != 1:
            raise ValueError(f"Class helper {old} must be defined exactly once to be renamed")
        if new in project_identifiers:
            raise ValueError(f"New method name {new} already appears in the project")
        if old in bare_references or any(
            aliases.looks_up(call, {old}) for aliases, call in dynamic_calls
        ):
            raise ValueError(f"Class helper {old} is referenced by name; rename it manually")
        if renames.get(old, new) != new:
            raise ValueError(f"Conflicting renames for class helper {old}")
        renames[old] = new
    if len(set(renames.values())) != len(renames):
        raise ValueError("Several class helpers would share the same new name")
    return renames


def _plan_method_renames(module: _Module, renames: dict[str, str], edits: _Edits) -> None:
    for node in ast.walk(module.tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in renames:
            edits.identifier(node, node.name, renames[node.name])
        elif isinstance(node, ast.Attribute) and node.attr in renames:
            if node.end_lineno is None or node.end_col_offset is None:
                raise ValueError("a parsed node carries its end position")
            edits.add(
                node.end_lineno,
                node.end_col_offset - len(node.attr.encode("utf-8")),
                node.end_lineno,
                node.end_col_offset,
                renames[node.attr],
            )


def _plan_parameter_renames(
    module: _Module,
    specifications: Sequence[_ParameterSpec],
    edits: _Edits,
) -> set[tuple[str, str]]:
    """Rename a helper's parameter throughout that helper's own scope.

    A class-private helper's specification names its one definition; any
    other names every function of the helper's name in the files it selects.
    """
    found: set[tuple[str, str]] = set()
    scopes = module.scopes
    builtins = _BuiltinReferences(module)
    for spec in specifications:
        helper, parameter, new, file_filter = (
            spec.helper,
            spec.parameter,
            spec.new,
            spec.file_filter,
        )
        if file_filter is not None and module.path.resolve() != file_filter.resolve():
            continue
        for function in ast.walk(module.tree):
            if (
                not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
                or function.name != helper
                or spec.owner is not None
                and function is not spec.owner.function
            ):
                continue
            arguments = [
                *function.args.posonlyargs,
                *function.args.args,
                *function.args.kwonlyargs,
                *(arg for arg in (function.args.vararg, function.args.kwarg) if arg is not None),
            ]
            matching = [arg for arg in arguments if arg.arg == parameter]
            if not matching:
                continue
            found.add(spec.key)
            if parameter == new:
                continue
            if new in _identifiers_in(function) or _private_in_class(scopes.nodes[function], new):
                raise ValueError(
                    f"Parameter rename would collide with {new} in {module.path}:{helper}"
                )
            if not function.body:
                continue
            if any(builtins.namespace_read(node) for node in ast.walk(function)):
                raise ValueError(
                    f"Dynamic namespace access prevents safe parameter rename: {module.path}:{helper}"
                )
            own_scope = scopes.nodes[function.body[0]]
            for node in ast.walk(function):
                if isinstance(node, (ast.Global, ast.Nonlocal)) and parameter in node.names:
                    raise ValueError(
                        f"Parameter {parameter} is redeclared in a nested scope: {module.path}"
                    )
            for arg in matching:
                if arg.end_lineno is None or arg.end_col_offset is None:
                    raise ValueError("a parsed node carries its end position")
                edits.add(
                    arg.lineno,
                    arg.col_offset,
                    arg.end_lineno,
                    arg.col_offset + len(parameter.encode("utf-8")),
                    new,
                )
            for node in ast.walk(function):
                if (
                    isinstance(node, ast.Name)
                    and node.id == parameter
                    and _resolve(scopes.nodes[node], parameter) is own_scope
                ):
                    edits.node(node, new)
    return found


def _plan_module(
    module: _Module, selected: dict[tuple[str, str], str], modules: dict[str, _Module]
) -> _Edits:
    return _ModulePlanner(module, selected, modules).plan()


@dataclass(frozen=True)
class _ParameterRename:
    module: _Module
    function: FunctionNode
    parameter: str
    replacement: str
    positional_only: bool


class _ParameterCallsites:
    """Rename keyword arguments only at resolved calls, refusing callable escapes.

    Imports and re-exports carry a function's identity across modules. A
    method is known by the name its class stores it under (a class-private
    ``__h`` in ``Box`` is ``_Box__h``, and so is ``obj.__h`` written in
    ``Box``'s body), which must be defined once in the project, as method
    renaming requires. Aliases or dynamic keyword dictionaries that cannot be
    rewritten safely are declined before any files are changed.
    """

    def __init__(
        self,
        modules: Sequence[_Module],
        specifications: Sequence[_ParameterSpec],
    ) -> None:
        self.symbols: dict[tuple[_Scope, str], tuple[_ParameterRename, ...]] = {}
        self.methods: dict[str, tuple[_ParameterRename, ...]] = {}
        self.modules = {module.name: module for module in modules}
        self.module_targets: set[str] = set()
        self.owners = {module.name: mangling_classes(module.tree) for module in modules}
        definitions: dict[str, int] = {}
        for module in modules:
            owners = self.owners[module.name]
            for node in ast.walk(module.tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    stored = self._stored(node.name, owners.get(node))
                    definitions[stored] = definitions.get(stored, 0) + 1
        for module in modules:
            owners = self.owners[module.name]
            for node in ast.walk(module.tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                arguments = (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
                for spec in specifications:
                    helper, parameter, new = spec.helper, spec.parameter, spec.new
                    if helper != node.name or parameter == new:
                        continue
                    if spec.owner is not None and node is not spec.owner.function:
                        continue
                    file_filter = spec.file_filter
                    if file_filter is not None and module.path.resolve() != file_filter.resolve():
                        continue
                    if not any(arg.arg == parameter for arg in arguments):
                        continue
                    if any(
                        not isinstance(decorator, ast.Name)
                        or decorator.id not in {"staticmethod", "classmethod"}
                        or decorator.id in module.scopes.root.bindings
                        or _resolve(module.scopes.nodes[decorator], decorator.id)
                        is not module.scopes.root
                        for decorator in node.decorator_list
                    ):
                        raise ValueError(
                            f"Decorated callable prevents safe parameter rename: {module.path}:{helper}"
                        )
                    target = _ParameterRename(
                        module,
                        node,
                        self._keyword_name(module, node, parameter),
                        new,
                        any(arg.arg == parameter for arg in node.args.posonlyargs),
                    )
                    scope = module.scopes.nodes[node]
                    if scope.kind == "class":
                        stored = self._stored(helper, owners.get(node))
                        if definitions[stored] != 1:
                            raise ValueError(
                                f"Class helper {helper} must be unique for parameter renaming"
                            )
                        self.methods[stored] = (*self.methods.get(stored, ()), target)
                    else:
                        key = (scope, helper)
                        self.symbols[key] = (*self.symbols.get(key, ()), target)
                        if scope is module.scopes.root:
                            self.module_targets.add(module.name)
        # A static import or re-export preserves the function's identity.
        grew = True
        while grew:
            grew = False
            for module in modules:
                for node in ast.walk(module.tree):
                    if not isinstance(node, ast.ImportFrom):
                        continue
                    origin = self.modules.get(_import_origin(module, node))
                    if origin is None:
                        continue
                    for alias in node.names:
                        targets = self.symbols.get((origin.scopes.root, alias.name), ())
                        if not targets:
                            continue
                        local = alias.asname or alias.name
                        key = (_resolve(module.scopes.nodes[node], local), local)
                        combined = tuple(dict.fromkeys((*self.symbols.get(key, ()), *targets)))
                        if combined != self.symbols.get(key, ()):
                            self.symbols[key] = combined
                            if key[0] is module.scopes.root:
                                self.module_targets.add(module.name)
                            grew = True

    @staticmethod
    def _stored(name: str, owner: ast.ClassDef | None) -> str:
        """What ``name`` written in ``owner``'s body (None: in no class) is stored as."""
        return mangled(name, owner.name if owner is not None else None)

    def _keyword_name(self, module: _Module, function: FunctionNode, name: str) -> str:
        """The keyword that reaches ``function``'s parameter ``name``: its stored name.

        A parameter ``__x`` of a method of ``Box`` is stored as ``_Box__x``, and
        a call's keywords are never mangled, so only ``_Box__x=`` reaches it.
        """
        return self._stored(name, self.owners[module.name].get(function))

    def _check_bindings(self, module: _Module) -> None:
        """Each tracked callable binding has one statically known definition."""
        for node in ast.walk(module.tree):
            name: str | None
            scope = module.scopes.nodes.get(node)
            if scope is None:
                continue
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                origin = (
                    self.modules.get(_import_origin(module, node))
                    if isinstance(node, ast.ImportFrom)
                    else None
                )
                for alias in node.names:
                    name = imported_binding_name(alias)
                    if name is None:
                        continue
                    targets = self.symbols.get((_resolve(scope, name), name), ())
                    imported = (
                        self.symbols.get((origin.scopes.root, alias.name), ())
                        if origin is not None
                        else ()
                    )
                    if targets and (
                        set(targets) != set(imported) or name in _resolve(scope, name).parameters
                    ):
                        raise ValueError(
                            f"Rebound callable prevents safe parameter rename: {module.path}:{name}"
                        )
                continue
            name = None
            if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
                name = node.id
            elif isinstance(
                node,
                (
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                    ast.ClassDef,
                    ast.ExceptHandler,
                    ast.MatchAs,
                    ast.MatchStar,
                ),
            ):
                name = node.name
            elif isinstance(node, ast.MatchMapping):
                name = node.rest
            elif isinstance(node, ast.arg):
                name = node.arg
            if name is None:
                continue
            targets = self.symbols.get((_resolve(scope, name), name), ())
            if targets and any(node is not target.function for target in targets):
                raise ValueError(
                    f"Rebound callable prevents safe parameter rename: {module.path}:{name}"
                )

    def plan(self, module: _Module, edits: _Edits) -> None:
        if not self.symbols and not self.methods:
            return
        self._check_bindings(module)
        resolver = _ModulePlanner(module, {}, self.modules, protected_modules=self.module_targets)
        for node in ast.walk(module.tree):
            if isinstance(node, ast.Import):
                resolver._plan_import(node)
            elif isinstance(node, ast.ImportFrom):
                resolver._plan_import_from(node)
        resolver._check_alias_bindings()
        helper_names = {name for _, name in self.symbols} | set(self.methods)
        for node in ast.walk(module.tree):
            if resolver.builtins.namespace_read(node) or (
                isinstance(node, ast.Call) and resolver.builtins.looks_up(node, helper_names)
            ):
                raise ValueError(
                    f"Dynamic namespace access prevents safe parameter rename: {module.path}"
                )
            targets: tuple[_ParameterRename, ...] = ()
            if isinstance(node, ast.Name):
                targets = self.symbols.get(
                    (_resolve(module.scopes.nodes[node], node.id), node.id), ()
                )
            elif isinstance(node, ast.Attribute):
                owner = self.owners[module.name].get(node)
                targets = self.methods.get(self._stored(node.attr, owner), ())
                origin_name = resolver._referenced_module(node.value)
                origin = self.modules.get(origin_name) if origin_name is not None else None
                if origin is not None:
                    targets = self.symbols.get((origin.scopes.root, node.attr), targets)
            parent = resolver.parents.get(node)
            if targets:
                if not isinstance(parent, ast.Call) or parent.func is not node:
                    raise ValueError(f"Callable escapes static parameter rename: {module.path}")
                self._keywords(parent, targets, edits)
            origin_name = resolver._referenced_module(node)
            if (
                origin_name is not None
                and any(
                    target == origin_name or target.startswith(origin_name + ".")
                    for target in self.module_targets
                )
                and (not isinstance(parent, ast.Attribute) or parent.value is not node)
            ):
                raise ValueError(f"Module object escapes static parameter rename: {module.path}")

    @staticmethod
    def _keywords(call: ast.Call, targets: Sequence[_ParameterRename], edits: _Edits) -> None:
        """Rename each keyword of ``call`` that passes a renamed parameter.

        CPython rewrites a private parameter name but never a call's keyword,
        so ``__param_0=`` passes ``__param_0`` wherever it is written, and only
        a keyword spelled as the parameter is stored, ``_Box__param_0=``,
        reaches ``Box``'s ``__param_0``.
        """
        replacements = {
            target.parameter: target.replacement for target in targets if not target.positional_only
        }
        if not replacements:
            return
        for argument in call.keywords:
            if argument.arg is None:
                raise ValueError(
                    f"Dynamic keyword arguments prevent safe parameter rename: {edits.module.path}"
                )
            if argument.arg in replacements:
                edits.add(
                    argument.lineno,
                    argument.col_offset,
                    argument.lineno,
                    argument.col_offset + len(argument.arg.encode("utf-8")),
                    replacements[argument.arg],
                )


class _ModulePlanner:
    """The rename edits one module needs, and every reason it cannot be renamed safely.

    Planning runs in three passes over the module: the imports, which decide
    what each local alias refers to; the alias bindings, which must be plain
    module-level names; and every other node, where references to renamed
    helpers are edited and each construct a static rename cannot follow is
    rejected. A rejection raises ValueError naming the module.
    """

    def __init__(
        self,
        module: _Module,
        selected: dict[tuple[str, str], str],
        modules: dict[str, _Module],
        *,
        protected_modules: set[str] | None = None,
    ) -> None:
        self.module = module
        self.selected = selected
        self.modules = modules
        self.protected_modules = set(protected_modules or ()) | {name for name, _ in selected}
        self.edits = _Edits(module)
        self.scopes = module.scopes
        self.builtins = _BuiltinReferences(module)
        self.parents = {
            child: parent
            for parent in ast.walk(module.tree)
            for child in ast.iter_child_nodes(parent)
        }
        # What each module alias, per binding scope, refers to.
        self.imports: dict[tuple[_Scope, str], str] = {}
        # Helpers defined in this module that are being renamed.
        self.local_renames = {
            old: new for (name, old), new in selected.items() if name == module.name
        }

    def plan(self) -> _Edits:
        for node in ast.walk(self.module.tree):
            if (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and node.id in self.local_renames.values()
                and _resolve(self.scopes.nodes[node], node.id) is self.scopes.root
            ):
                raise ValueError(
                    f"Rename would capture existing identifier {node.id} in {self.module.path}"
                )
        for node in ast.walk(self.module.tree):
            if isinstance(node, ast.Import):
                self._plan_import(node)
            elif isinstance(node, ast.ImportFrom):
                self._plan_import_from(node)
        self._check_alias_bindings()
        for node in ast.walk(self.module.tree):
            self._plan_node(node)
        return self.edits

    # -- imports -------------------------------------------------------------

    def _record_import(self, scope: _Scope, local: str, origin: str) -> None:
        key = (_resolve(scope, local), local)
        previous = self.imports.get(key)
        if (
            previous is not None
            and previous != origin
            and any(self._has_renamed_members(candidate) for candidate in (previous, origin))
        ):
            raise ValueError(
                f"Conflicting module aliases prevent safe rename: {self.module.path}:{local}"
            )
        self.imports[key] = origin

    def _plan_import(self, node: ast.Import) -> None:
        scope = self.scopes.nodes[node]
        for alias in node.names:
            local = imported_binding_name(alias)
            if local is None:
                continue
            if scope is self.scopes.root and local in self.local_renames:
                raise ValueError(
                    f"Imported binding redefines selected helper: {self.module.path}:{local}"
                )
            self._record_import(
                scope, local, alias.name if alias.asname else alias.name.split(".")[0]
            )

    def _plan_import_from(self, node: ast.ImportFrom) -> None:
        scope = self.scopes.nodes[node]
        origin = _import_origin(self.module, node)
        if any(alias.name == "*" for alias in node.names) and origin in self.protected_modules:
            raise ValueError(f"Star import prevents safe helper rename: {self.module.path}")
        for alias in node.names:
            local = imported_binding_name(alias)
            if local is None:
                continue
            if (
                scope is self.scopes.root
                and local in self.local_renames
                and self.selected.get((origin, alias.name)) != self.local_renames[local]
            ):
                raise ValueError(
                    f"Imported binding redefines selected helper: {self.module.path}:{local}"
                )
            renamed = self.selected.get((origin, alias.name))
            if renamed is not None:
                if self.selected.get((self.module.name, local)) == renamed and alias.asname is None:
                    # A generated binding selected by the closure: rename the
                    # import and, through local_renames, every reference here.
                    if renamed in self.module.scopes.root.bindings:
                        raise ValueError(
                            f"Rename would collide with existing identifier {renamed} "
                            f"in {self.module.path}"
                        )
                    self.edits.node(alias, renamed)
                else:
                    # Preserve an importing module's own binding and re-export API.
                    self.edits.node(alias, f"{renamed} as {local}")
            imported_module = f"{origin}.{alias.name}" if origin else alias.name
            if imported_module in self.modules:
                self._record_import(scope, local, imported_module)

    def _referenced_module(self, node: ast.AST) -> str | None:
        """The module a name or dotted attribute chain refers to, through the imports."""
        if isinstance(node, ast.Name):
            return self.imports.get((_resolve(self.scopes.nodes[node], node.id), node.id))
        if isinstance(node, ast.Attribute):
            parent_module = self._referenced_module(node.value)
            return f"{parent_module}.{node.attr}" if parent_module else None
        return None

    def _has_renamed_members(self, name: str) -> bool:
        """Whether module ``name``, or a module inside it, has a helper being renamed."""
        return any(
            candidate == name or candidate.startswith(name + ".")
            for candidate in self.protected_modules
        )

    def _check_alias_bindings(self) -> None:
        for (binding_scope, local), imported_module in self.imports.items():
            if self._has_renamed_members(imported_module) and (
                binding_scope.kind == "class" or local in binding_scope.parameters
            ):
                raise ValueError(f"Ambiguous module alias binding: {self.module.path}:{local}")

    # -- every other node ----------------------------------------------------

    def _plan_node(self, node: ast.AST) -> None:
        """Edit or reject one node; the checks run in a fixed order, first rejection wins."""
        if getattr(node, "type_params", ()):
            raise ValueError(
                f"Generic parameter scopes are not supported by helper renaming: "
                f"{self.module.path}"
            )
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.alias)):
            return
        self._plan_definition(node)
        if isinstance(node, ast.Name):
            self._plan_name(node)
        if isinstance(node, ast.Global):
            self._plan_global(node)
        if isinstance(node, ast.Attribute):
            self._plan_attribute(node)
        self._check_module_escape(node)
        self._check_dynamic_namespace(node)
        if isinstance(node, ast.Name):
            self._check_alias_rebinding(node)
        self._check_string_annotations(node)
        if isinstance(node, ast.Call):
            self._check_dynamic_lookup(node)
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            self._check_string_exports(node)

    def _plan_definition(self, node: ast.AST) -> None:
        """Rename a module-level helper definition; reject any other binding of its name."""
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and self.scopes.nodes[node] is self.scopes.root
            and node.name in self.local_renames
        ):
            self.edits.identifier(node, node.name, self.local_renames[node.name])
        binding_name = None
        if isinstance(node, (ast.ClassDef, ast.ExceptHandler, ast.MatchAs, ast.MatchStar)):
            binding_name = node.name
        elif isinstance(node, ast.MatchMapping):
            binding_name = node.rest
        if (
            binding_name in self.local_renames
            and _resolve(self.scopes.nodes[node], binding_name) is self.scopes.root
        ):
            raise ValueError(
                f"Non-function binding redefines selected helper: "
                f"{self.module.path}:{binding_name}"
            )

    def _plan_name(self, node: ast.Name) -> None:
        scope = self.scopes.nodes[node]
        if node.id not in self.local_renames:
            return
        if scope.kind == "class" and node.id in scope.bindings and isinstance(node.ctx, ast.Load):
            raise ValueError(f"Ambiguous class namespace lookup: {self.module.path}:{node.id}")
        if _resolve(scope, node.id) is not self.scopes.root:
            return
        new = self.local_renames[node.id]
        if _private_in_class(scope, node.id) or _private_in_class(scope, new):
            raise ValueError(
                f"Class-private name mangling prevents safe rename: {self.module.path}"
            )
        if _resolve(scope, new) is not self.scopes.root:
            raise ValueError(f"Rename would capture local identifier {new} in {self.module.path}")
        self.edits.node(node, new)

    def _plan_global(self, node: ast.Global) -> None:
        for old, new in self.local_renames.items():
            if old in node.names:
                if new in self.scopes.nodes[node].bindings:
                    raise ValueError(
                        f"Rename conflicts with global declaration: {self.module.path}:{new}"
                    )
                self.edits.identifier(node, old, new)

    def _plan_attribute(self, node: ast.Attribute) -> None:
        attribute_origin = self._referenced_module(node.value)
        if attribute_origin is None or (attribute_origin, node.attr) not in self.selected:
            return
        if not isinstance(node.ctx, ast.Load):
            raise ValueError(
                f"Mutation of imported helper requires manual rename: {self.module.path}"
            )
        if node.end_lineno is None or node.end_col_offset is None:
            raise ValueError("a parsed node carries its end position")
        new = self.selected[(attribute_origin, node.attr)]
        scope = self.scopes.nodes[node]
        if _private_in_class(scope, node.attr) or _private_in_class(scope, new):
            raise ValueError(
                f"Class-private attribute mangling prevents safe rename: {self.module.path}"
            )
        self.edits.add(
            node.end_lineno,
            node.end_col_offset - len(node.attr.encode("utf-8")),
            node.end_lineno,
            node.end_col_offset,
            new,
        )

    def _check_module_escape(self, node: ast.AST) -> None:
        """A renamed module used other than as the object of an attribute access escapes."""
        expression_origin = self._referenced_module(node)
        if expression_origin is None or not self._has_renamed_members(expression_origin):
            return
        parent = self.parents.get(node)
        if not isinstance(parent, ast.Attribute) or parent.value is not node:
            raise ValueError(
                f"Module object escapes static rename analysis: "
                f"{self.module.path}:{expression_origin}"
            )

    def _check_dynamic_namespace(self, node: ast.AST) -> None:
        if self.local_renames and self.builtins.namespace_read(node):
            raise ValueError(f"Dynamic namespace access prevents safe rename: {self.module.path}")

    def _check_alias_rebinding(self, node: ast.Name) -> None:
        if not isinstance(node.ctx, (ast.Store, ast.Del)):
            return
        imported = self.imports.get((_resolve(self.scopes.nodes[node], node.id), node.id))
        if imported is not None and self._has_renamed_members(imported):
            raise ValueError(
                f"Rebound module alias prevents safe rename: {self.module.path}:{node.id}"
            )

    def _check_string_annotations(self, node: ast.AST) -> None:
        annotations: list[ast.AST] = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            annotations.extend(
                arg.annotation
                for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
                if arg.annotation is not None
            )
            annotations.extend(
                arg.annotation
                for arg in (node.args.vararg, node.args.kwarg)
                if arg is not None and arg.annotation is not None
            )
            if node.returns is not None:
                annotations.append(node.returns)
        elif isinstance(node, ast.AnnAssign):
            annotations.append(node.annotation)
        if any(
            isinstance(part, ast.Constant)
            and isinstance(part.value, str)
            and any(
                name in part.value for name in (*self.local_renames, *self.local_renames.values())
            )
            for annotation in annotations
            for part in ast.walk(annotation)
        ):
            raise ValueError(f"String annotation requires manual rename: {self.module.path}")

    def _check_dynamic_lookup(self, node: ast.Call) -> None:
        if self.local_renames and self.builtins.looks_up(node, self.local_renames):
            raise ValueError(f"Dynamic name lookup prevents safe rename: {self.module.path}")

    def _check_string_exports(self, node: ast.Assign | ast.AnnAssign) -> None:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(
            isinstance(target, ast.Name) and target.id == "__all__" for target in targets
        ) and any(
            isinstance(value, ast.Constant)
            and isinstance(value.value, str)
            and value.value in self.local_renames
            for value in ast.walk(node)
        ):
            raise ValueError(f"Explicit string exports require manual rename: {self.module.path}")
