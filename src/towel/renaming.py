"""Plan lexical module-helper renames without rewriting unrelated identifier tokens.

Only statically resolved module helpers/imports are supported. Renaming an API
requires exclusive access and all consumers in the selected source tree; dynamic
lookup and escaping module objects are rejected where visible in that tree.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
import io
import keyword
from pathlib import Path
import tokenize
import unicodedata
from typing import Iterable, Sequence

from .changes import ChangePlan
from .unification.project_layout import ProjectLayout
from .unification.semantic_safety import is_namespace_access_call
from .unification.visitors import visit_comprehension_result

Function = ast.FunctionDef | ast.AsyncFunctionDef


def _outer_expressions(node: Function | ast.Lambda) -> Iterable[ast.AST]:
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


class _Bindings(ast.NodeVisitor):
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

    def visit_FunctionDef(self, node: Function) -> None:
        self.local.add(node.name)
        for expression in _outer_expressions(node):
            self.visit(expression)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for expression in _outer_expressions(node):
            self.visit(expression)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.local.add(node.name)
        for expression in (*node.bases, *node.decorator_list):
            self.visit(expression)
        for keyword_node in node.keywords:
            self.visit(keyword_node.value)

    def visit_Import(self, node: ast.Import) -> None:
        self.local.update(alias.asname or alias.name.split(".")[0] for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.local.update(alias.asname or alias.name for alias in node.names)

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

    def visit_ListComp(
        self, node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp
    ) -> None:
        # Comprehension iteration targets are local to the comprehension, while
        # assignment expressions bind in its containing non-comprehension scope.
        for generator in node.generators:
            self.visit(generator.iter)
            for condition in generator.ifs:
                self.visit(condition)
        visit_comprehension_result(self, node)

    visit_SetComp = visit_ListComp
    visit_DictComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp


@dataclass(frozen=True, eq=False)
class _Scope:
    parent: _Scope | None
    kind: str
    bindings: frozenset[str]
    globals: frozenset[str]
    nonlocals: frozenset[str]
    parameters: frozenset[str] = frozenset()


def _scope(
    parent: _Scope | None, kind: str, nodes: Iterable[ast.AST], args: ast.arguments | None = None
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


class _Scopes(ast.NodeVisitor):
    def __init__(self, tree: ast.Module) -> None:
        self.root = _scope(None, "module", tree.body)
        self.current = self.root
        self.nodes: dict[ast.AST, _Scope] = {}
        self.visit(tree)

    def visit(self, node: ast.AST) -> None:
        self.nodes[node] = self.current
        super().visit(node)

    def _body(self, scope: _Scope, nodes: Iterable[ast.AST]) -> None:
        previous = self.current
        self.current = scope
        for node in nodes:
            self.visit(node)
        self.current = previous

    def visit_FunctionDef(self, node: Function) -> None:
        for expression in _outer_expressions(node):
            self.visit(expression)
        self._body(_scope(self.current, "function", node.body, node.args), node.body)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for expression in _outer_expressions(node):
            self.visit(expression)
        self._body(_scope(self.current, "function", [node.body], node.args), [node.body])

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for expression in (*node.bases, *node.decorator_list):
            self.visit(expression)
        for item in node.keywords:
            self.visit(item.value)
        self._body(_scope(self.current, "class", node.body), node.body)

    def visit_ListComp(
        self, node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp
    ) -> None:
        self.visit(node.generators[0].iter)
        collector = _Bindings()
        for generator in node.generators:
            collector.visit(generator.target)
        nested = _Scope(
            self.current, "comprehension", frozenset(collector.local), frozenset(), frozenset()
        )
        previous = self.current
        self.current = nested
        for index, generator in enumerate(node.generators):
            self.visit(generator.target)
            if index:
                self.visit(generator.iter)
            for condition in generator.ifs:
                self.visit(condition)
        visit_comprehension_result(self, node)
        self.current = previous

    visit_SetComp = visit_ListComp
    visit_DictComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp

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


def _resolve(scope: _Scope, name: str) -> _Scope:
    current = scope
    while current.parent is not None:
        if name in current.globals:
            while current.parent is not None:
                current = current.parent
            return current
        if name in current.bindings and name not in current.nonlocals:
            return current
        parent = current.parent
        assert parent is not None
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
    raw: bytes
    tree: ast.Module
    scopes: _Scopes


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
        assert node.end_lineno is not None and node.end_col_offset is not None
        self.add(node.lineno, node.col_offset, node.end_lineno, node.end_col_offset, replacement)

    def identifier(self, node: Function | ast.Global, old: str, new: str) -> None:
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


def plan_renames(
    target: Path, specifications: Sequence[tuple[str, str, Path | None]]
) -> tuple[ChangePlan, int]:
    """Validate and plan all selected module definitions and their static consumers.

    A specification ``(old, new, file)`` renames a helper function. When
    ``old`` is ``helper.parameter`` it renames that parameter within the
    helper's own scope instead. Module-level helpers are renamed with their
    static importers; a helper defined inside a class is renamed together
    with every attribute reference to it, which is sound only because
    generated helper names are unique across the project.
    """
    parameter_specifications: list[tuple[str, str, str, Path | None]] = []
    function_specifications: list[tuple[str, str, Path | None]] = []
    for old, new, file_filter in specifications:
        helper, _, parameter = old.partition(".")
        names = (helper, parameter, new) if parameter else (helper, new)
        if any(
            not name.isidentifier()
            or keyword.iskeyword(name)
            or unicodedata.normalize("NFKC", name) != name
            for name in names
        ):
            raise ValueError("Renamings require normalized, non-keyword Python identifiers")
        if parameter:
            parameter_specifications.append((helper, parameter, new, file_filter))
        else:
            function_specifications.append((old, new, file_filter))
    specifications = function_specifications
    layout = _rename_layout(target)
    modules: list[_Module] = []
    for path in sorted(target.rglob("*.py")):
        if path.is_symlink():
            continue
        raw = path.read_bytes()
        tree = ast.parse(raw.decode("utf-8"), filename=str(path))
        compile(tree, str(path), "exec")
        modules.append(_Module(path, _module_name(layout, path), raw, tree, _Scopes(tree)))
    by_name = {module.name: module for module in modules}
    if len(by_name) != len(modules):
        raise ValueError("Ambiguous module paths in rename target")
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
    _extend_to_generated_importers(modules, selected)
    method_renames = _validate_method_renames(modules, method_specifications)
    if not selected and not method_renames and not parameter_specifications:
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
        parameters_found |= _plan_parameter_renames(module, parameter_specifications, edits)
        content = edits.render()
        if content != module.raw:
            before[str(module.path)] = module.raw
            after[str(module.path)] = content.decode("utf-8")
            count += len(edits.changes)
    missing = {
        (helper, parameter)
        for helper, parameter, _, _ in parameter_specifications
        if (helper, parameter) not in parameters_found
    }
    if missing:
        raise ValueError(f"No helper defines these parameters: {sorted(missing)}")
    return ChangePlan.from_sources(before, after), count


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
        elif isinstance(child, ast.alias):
            names.add(child.asname or child.name.split(".")[0])
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
                    if not _GENERATED_HELPER.fullmatch(alias.name):
                        continue
                    key = (module.name, alias.name)
                    if key in selected:
                        continue
                    selected[key] = new
                    changed = True


def _validate_method_renames(
    modules: Sequence[_Module], specifications: Sequence[tuple[str, str]]
) -> dict[str, str]:
    """Check that each class-level helper is unique and its new name unused anywhere."""
    if not specifications:
        return {}
    project_identifiers: set[str] = set()
    definitions: dict[str, int] = {}
    bare_references: set[str] = set()
    for module in modules:
        project_identifiers |= _identifiers_in(module.tree)
        for node in ast.walk(module.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                definitions[node.name] = definitions.get(node.name, 0) + 1
            elif isinstance(node, ast.Name):
                bare_references.add(node.id)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in {"getattr", "setattr", "hasattr", "delattr"}:
                    for argument in node.args:
                        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                            bare_references.add(argument.value)
    renames: dict[str, str] = {}
    for old, new in specifications:
        if _mangled(old) or _mangled(new):
            raise ValueError(f"Class-private name mangling prevents safe rename: {old} -> {new}")
        if definitions.get(old, 0) != 1:
            raise ValueError(f"Class helper {old} must be defined exactly once to be renamed")
        if new in project_identifiers:
            raise ValueError(f"New method name {new} already appears in the project")
        if old in bare_references:
            raise ValueError(f"Class helper {old} is referenced by name; rename it manually")
        if renames.get(old, new) != new:
            raise ValueError(f"Conflicting renames for class helper {old}")
        renames[old] = new
    if len(set(renames.values())) != len(renames):
        raise ValueError("Several class helpers would share the same new name")
    return renames


def _mangled(name: str) -> bool:
    return name.startswith("__") and not name.endswith("__")


def _plan_method_renames(module: _Module, renames: dict[str, str], edits: _Edits) -> None:
    for node in ast.walk(module.tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in renames:
            edits.identifier(node, node.name, renames[node.name])
        elif isinstance(node, ast.Attribute) and node.attr in renames:
            assert node.end_lineno is not None and node.end_col_offset is not None
            edits.add(
                node.end_lineno,
                node.end_col_offset - len(node.attr.encode("utf-8")),
                node.end_lineno,
                node.end_col_offset,
                renames[node.attr],
            )


def _plan_parameter_renames(
    module: _Module,
    specifications: Sequence[tuple[str, str, str, Path | None]],
    edits: _Edits,
) -> set[tuple[str, str]]:
    """Rename a helper's parameter throughout that helper's own scope."""
    found: set[tuple[str, str]] = set()
    scopes = module.scopes
    for helper, parameter, new, file_filter in specifications:
        if file_filter is not None and module.path.resolve() != file_filter.resolve():
            continue
        for function in ast.walk(module.tree):
            if (
                not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
                or function.name != helper
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
            found.add((helper, parameter))
            if parameter == new:
                continue
            if new in _identifiers_in(function) or _private_in_class(scopes.nodes[function], new):
                raise ValueError(
                    f"Parameter rename would collide with {new} in {module.path}:{helper}"
                )
            if not function.body:
                continue
            own_scope = scopes.nodes[function.body[0]]
            for node in ast.walk(function):
                if isinstance(node, (ast.Global, ast.Nonlocal)) and parameter in node.names:
                    raise ValueError(
                        f"Parameter {parameter} is redeclared in a nested scope: {module.path}"
                    )
            for arg in matching:
                assert arg.end_lineno is not None and arg.end_col_offset is not None
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


_GENERATED_HELPER = re.compile(r"_{1,2}extracted_func(?:_\d+)?")


def _plan_module(
    module: _Module, selected: dict[tuple[str, str], str], modules: dict[str, _Module]
) -> _Edits:
    edits = _Edits(module)
    scopes = module.scopes
    parents = {
        child: parent for parent in ast.walk(module.tree) for child in ast.iter_child_nodes(parent)
    }
    imports: dict[tuple[_Scope, str], str] = {}
    local_renames = {old: new for (name, old), new in selected.items() if name == module.name}
    package = module.name if module.path.name == "__init__.py" else module.name.rpartition(".")[0]

    def record_import(scope: _Scope, local: str, origin: str) -> None:
        key = (_resolve(scope, local), local)
        previous = imports.get(key)
        if (
            previous is not None
            and previous != origin
            and any(
                name == candidate or name.startswith(candidate + ".")
                for name, _ in selected
                for candidate in (previous, origin)
            )
        ):
            raise ValueError(
                f"Conflicting module aliases prevent safe rename: {module.path}:{local}"
            )
        imports[key] = origin

    for node in ast.walk(module.tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        scope = scopes.nodes[node]
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                if scope is scopes.root and local in local_renames:
                    raise ValueError(
                        f"Imported binding redefines selected helper: {module.path}:{local}"
                    )
                record_import(
                    scope, local, alias.name if alias.asname else alias.name.split(".")[0]
                )
            continue
        if node.level:
            parts = package.split(".") if package else []
            if node.level > len(parts):
                raise ValueError(f"Unresolved relative import in {module.path}")
            origin = ".".join(
                parts[: len(parts) - node.level + 1] + ([node.module] if node.module else [])
            )
        else:
            origin = node.module or ""
        if any(alias.name == "*" for alias in node.names) and any(
            name == origin for name, _ in selected
        ):
            raise ValueError(f"Star import prevents safe helper rename: {module.path}")
        for alias in node.names:
            local = alias.asname or alias.name
            if (
                scope is scopes.root
                and local in local_renames
                and selected.get((origin, alias.name)) != local_renames[local]
            ):
                raise ValueError(
                    f"Imported binding redefines selected helper: {module.path}:{local}"
                )
            renamed = selected.get((origin, alias.name))
            if renamed is not None:
                if selected.get((module.name, local)) == renamed and alias.asname is None:
                    # A generated binding selected by the closure: rename the
                    # import and, through local_renames, every reference here.
                    if renamed in module.scopes.root.bindings:
                        raise ValueError(
                            f"Rename would collide with existing identifier {renamed} in {module.path}"
                        )
                    edits.node(alias, renamed)
                else:
                    # Preserve an importing module's own binding and re-export API.
                    edits.node(alias, f"{renamed} as {local}")
            imported_module = f"{origin}.{alias.name}" if origin else alias.name
            if imported_module in modules:
                record_import(scope, local, imported_module)

    def referenced_module(node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return imports.get((_resolve(scopes.nodes[node], node.id), node.id))
        if isinstance(node, ast.Attribute):
            parent_module = referenced_module(node.value)
            return f"{parent_module}.{node.attr}" if parent_module else None
        return None

    def has_renamed_members(name: str) -> bool:
        return any(
            candidate == name or candidate.startswith(name + ".") for candidate, _ in selected
        )

    for (binding_scope, local), imported_module in imports.items():
        if has_renamed_members(imported_module) and (
            binding_scope.kind == "class" or local in binding_scope.parameters
        ):
            raise ValueError(f"Ambiguous module alias binding: {module.path}:{local}")

    for node in ast.walk(module.tree):
        if getattr(node, "type_params", ()):
            raise ValueError(
                f"Generic parameter scopes are not supported by helper renaming: {module.path}"
            )
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.alias)):
            continue
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and scopes.nodes[node] is scopes.root
            and node.name in local_renames
        ):
            edits.identifier(node, node.name, local_renames[node.name])
        binding_name = None
        if isinstance(node, (ast.ClassDef, ast.ExceptHandler, ast.MatchAs, ast.MatchStar)):
            binding_name = node.name
        elif isinstance(node, ast.MatchMapping):
            binding_name = node.rest
        if (
            binding_name in local_renames
            and _resolve(scopes.nodes[node], binding_name) is scopes.root
        ):
            raise ValueError(
                f"Non-function binding redefines selected helper: {module.path}:{binding_name}"
            )
        if isinstance(node, ast.Name):
            scope = scopes.nodes[node]
            if node.id in local_renames:
                if (
                    scope.kind == "class"
                    and node.id in scope.bindings
                    and isinstance(node.ctx, ast.Load)
                ):
                    raise ValueError(f"Ambiguous class namespace lookup: {module.path}:{node.id}")
                if _resolve(scope, node.id) is scopes.root:
                    new = local_renames[node.id]
                    if _private_in_class(scope, node.id) or _private_in_class(scope, new):
                        raise ValueError(
                            f"Class-private name mangling prevents safe rename: {module.path}"
                        )
                    if _resolve(scope, new) is not scopes.root:
                        raise ValueError(
                            f"Rename would capture local identifier {new} in {module.path}"
                        )
                    edits.node(node, new)
        if isinstance(node, ast.Global):
            for old, new in local_renames.items():
                if old in node.names:
                    if new in scopes.nodes[node].bindings:
                        raise ValueError(
                            f"Rename conflicts with global declaration: {module.path}:{new}"
                        )
                    edits.identifier(node, old, new)
        if isinstance(node, ast.Attribute):
            attribute_origin = referenced_module(node.value)
            if attribute_origin is not None and (attribute_origin, node.attr) in selected:
                if not isinstance(node.ctx, ast.Load):
                    raise ValueError(
                        f"Mutation of imported helper requires manual rename: {module.path}"
                    )
                assert node.end_lineno is not None and node.end_col_offset is not None
                new = selected[(attribute_origin, node.attr)]
                if _private_in_class(scopes.nodes[node], node.attr) or _private_in_class(
                    scopes.nodes[node], new
                ):
                    raise ValueError(
                        f"Class-private attribute mangling prevents safe rename: {module.path}"
                    )
                edits.add(
                    node.end_lineno,
                    node.end_col_offset - len(node.attr.encode("utf-8")),
                    node.end_lineno,
                    node.end_col_offset,
                    new,
                )
        expression_origin = referenced_module(node)
        if expression_origin is not None and has_renamed_members(expression_origin):
            parent = parents.get(node)
            if not isinstance(parent, ast.Attribute) or parent.value is not node:
                raise ValueError(
                    f"Module object escapes static rename analysis: {module.path}:{expression_origin}"
                )
        if (
            isinstance(node, ast.Name)
            and local_renames
            and node.id in {"globals", "locals", "vars", "eval", "exec"}
        ):
            # A bare reference to one of these names can alias the builtin
            # (``lookup = globals``) and later read the module namespace by
            # string, which a rename would silently break. Only flag it when the
            # name actually resolves to the builtin: a local variable or
            # parameter that merely shadows the spelling (``vars = set()``) does
            # no dynamic namespace access.
            resolved = _resolve(scopes.nodes[node], node.id)
            if resolved is scopes.root and node.id not in resolved.bindings:
                raise ValueError(f"Dynamic namespace access prevents safe rename: {module.path}")
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            imported = imports.get((_resolve(scopes.nodes[node], node.id), node.id))
            if imported is not None and has_renamed_members(imported):
                raise ValueError(
                    f"Rebound module alias prevents safe rename: {module.path}:{node.id}"
                )
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
            and any(old in part.value for old in local_renames)
            for annotation in annotations
            for part in ast.walk(annotation)
        ):
            raise ValueError(f"String annotation requires manual rename: {module.path}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if local_renames and is_namespace_access_call(node):
                raise ValueError(f"Dynamic namespace access prevents safe rename: {module.path}")
            if node.func.id in {"getattr", "setattr", "hasattr", "delattr"} and any(
                isinstance(argument, ast.Constant) and argument.value in local_renames
                for argument in node.args
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
            ):
                raise ValueError(f"Dynamic name lookup prevents safe rename: {module.path}")
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(
                isinstance(target, ast.Name) and target.id == "__all__" for target in targets
            ) and any(
                isinstance(value, ast.Constant)
                and isinstance(value.value, str)
                and value.value in local_renames
                for value in ast.walk(node)
            ):
                raise ValueError(f"Explicit string exports require manual rename: {module.path}")
    return edits
