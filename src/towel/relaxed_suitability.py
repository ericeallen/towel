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

"""Evidence that a program observes guarantees only strict mode preserves.

This is a suitability hint, not a proof: recognise explicit uses of Python's
lifetime APIs and writes to known module functions, without guessing about
arbitrary dynamic calls. The caller supplies the whole program, including consumers
left out of extraction.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal, Mapping, Sequence, TypedDict

from .project_layout import find_project_root
from .program_files import program_files
from .source_text import try_read_source

FindingKind = Literal["finalizer", "weakref_callback", "gc_callback", "function_rebinding"]


class FindingJSON(TypedDict):
    """An additive, source-located suitability finding."""

    kind: FindingKind
    path: str
    line: int
    detail: str


@dataclass(frozen=True)
class RelaxedFinding:
    """One explicit use of behavior that relaxed mode does not preserve."""

    kind: FindingKind
    path: str
    line: int
    detail: str

    def to_json(self) -> FindingJSON:
        return {"kind": self.kind, "path": self.path, "line": self.line, "detail": self.detail}


def _module_name(path: Path, root: Path) -> str:
    relative = path.relative_to(root) if path.is_relative_to(root) else path
    parts = list(relative.with_suffix("").parts)
    if parts and parts[0] == "src":
        parts.pop(0)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _scope_nodes(tree: ast.AST) -> list[ast.AST]:
    """Nodes in this lexical scope, including but not entering nested scopes."""
    result: list[ast.AST] = []
    for child in ast.iter_child_nodes(tree):
        result.append(child)
        if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            result.extend(_scope_nodes(child))
    return result


def _string(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


class _Scanner(ast.NodeVisitor):
    def __init__(
        self, path: str, module: str, functions: frozenset[str], classes: frozenset[str]
    ) -> None:
        self.path = path
        self.module = module
        self.package = (
            module
            if path.endswith("/__init__.py") or path == "__init__.py"
            else module.rpartition(".")[0]
        )
        self.functions = functions
        self.classes = classes
        self.aliases: dict[str, str] = {
            name.removeprefix(f"{module}."): name
            for name in classes
            if name.rpartition(".")[0] == module
        }
        self.aliases.update({"globals": "builtins.globals", "setattr": "builtins.setattr"})
        self.class_name: str | None = None
        self.function_depth = 0
        self.monkeypatch_names: frozenset[str] = frozenset()
        self.global_names: frozenset[str] | None = None
        self.findings: list[RelaxedFinding] = []

    def _name(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id)
        if isinstance(node, ast.Attribute):
            owner = self._name(node.value)
            return f"{owner}.{node.attr}" if owner else None
        if isinstance(node, ast.Subscript) and self._name(node.value) == "sys.modules":
            if isinstance(node.slice, ast.Name) and node.slice.id == "__name__":
                return self.module
            return _string(node.slice)
        return None

    def _add(self, kind: FindingKind, node: ast.stmt | ast.expr, detail: str) -> None:
        self.findings.append(RelaxedFinding(kind, self.path, node.lineno, detail))

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.aliases[alias.asname or alias.name.split(".")[0]] = (
                alias.name if alias.asname else alias.name.split(".")[0]
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        owner = node.module or ""
        if node.level:
            package = self.package.split(".")
            if node.level > 1:
                package = package[: -(node.level - 1)]
            owner = ".".join([*package, owner] if owner else package)
        for alias in node.names:
            if alias.name != "*":
                self.aliases[alias.asname or alias.name] = f"{owner}.{alias.name}"

    def _nested_scope(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        old_aliases, old_globals = self.aliases, self.global_names
        old_class, old_monkeypatch = self.class_name, self.monkeypatch_names
        is_function = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        self.class_name = node.name if isinstance(node, ast.ClassDef) else None
        if is_function:
            self.function_depth += 1
        self.aliases = dict(self.aliases)
        nodes = _scope_nodes(node)
        self.global_names = frozenset(
            name for item in nodes if isinstance(item, ast.Global) for name in item.names
        )
        local = {
            item.id
            for item in nodes
            if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store)
        }
        local.update(
            item.name
            for item in nodes
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        )
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            arguments = {arg.arg for arg in ast.walk(node.args) if isinstance(arg, ast.arg)}
            local.update(arguments)
            self.monkeypatch_names = (self.monkeypatch_names - local) | (
                arguments & {"monkeypatch"}
            )
        for name in local - self.global_names:
            self.aliases.pop(name, None)
        self.generic_visit(node)
        self.aliases, self.global_names = old_aliases, old_globals
        self.class_name, self.monkeypatch_names = old_class, old_monkeypatch
        if is_function:
            self.function_depth -= 1

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for member in node.body:
            if (
                isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
                and member.name == "__del__"
            ):
                self._add("finalizer", member, f"{node.name}.__del__")
        self._nested_scope(node)
        qualified = f"{self.module}.{node.name}"
        self.classes = self.classes | {qualified}
        self.aliases[node.name] = qualified

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.aliases.pop(node.name, None)
        self._nested_scope(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.aliases.pop(node.name, None)
        self._nested_scope(node)

    def _write(self, target: ast.expr, node: ast.stmt) -> None:
        if isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                self._write(item, node)
            return
        name = self._name(target)
        if isinstance(target, ast.Name):
            # Definitions selected or decorated during import do not change lookups
            # while extracted code runs. Only explicit global writes in functions do.
            name = (
                f"{self.module}.{target.id}"
                if self.function_depth and target.id in (self.global_names or ())
                else None
            )
            if target.id == "__del__" and self.class_name is not None:
                self._add("finalizer", node, f"{self.class_name}.__del__")
        elif isinstance(target, ast.Attribute) and target.attr == "__del__":
            if self._name(target.value) in self.classes:
                self._add("finalizer", node, f"{ast.unparse(target.value)}.__del__")
        elif (
            isinstance(target, ast.Subscript)
            and isinstance(target.value, ast.Call)
            and self._name(target.value.func) == "builtins.globals"
            and not target.value.args
            and (member := _string(target.slice)) is not None
        ):
            name = f"{self.module}.{member}"
        if isinstance(target, ast.Name):
            self.aliases.pop(target.id, None)
            self.monkeypatch_names = self.monkeypatch_names - {target.id}
        if name in self.functions:
            self._add("function_rebinding", node, f"replacement of {name}")
        if name == "gc.callbacks" or (
            isinstance(target, ast.Subscript) and self._name(target.value) == "gc.callbacks"
        ):
            self._add("gc_callback", node, "gc.callbacks assignment")

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._write(target, node)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self._write(node.target, node)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._write(node.target, node)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = self._name(node.func)
        if name == "weakref.finalize":
            self._add("weakref_callback", node, "weakref.finalize")
        elif name in {"weakref.ref", "weakref.proxy", "weakref.WeakMethod"}:
            callback = (
                node.args[1]
                if len(node.args) > 1
                else next(
                    (keyword.value for keyword in node.keywords if keyword.arg == "callback"), None
                )
            )
            if callback is not None and not (
                isinstance(callback, ast.Constant) and callback.value is None
            ):
                self._add("weakref_callback", node, f"{name} with callback")
        elif name in {"gc.callbacks.append", "gc.callbacks.extend", "gc.callbacks.insert"}:
            self._add("gc_callback", node, name)
        replacement: str | None = None
        if name == "unittest.mock.patch" and node.args:
            replacement = _string(node.args[0])
        elif name == "unittest.mock.patch.object" and len(node.args) >= 2:
            owner, member = self._name(node.args[0]), _string(node.args[1])
            if owner and member:
                replacement = f"{owner}.{member}"
        elif (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "setattr"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in self.monkeypatch_names
        ):
            if len(node.args) >= 3:
                owner, member = self._name(node.args[0]), _string(node.args[1])
                if owner and member:
                    replacement = f"{owner}.{member}"
            elif len(node.args) == 2:
                replacement = _string(node.args[0])
        elif name == "builtins.setattr" and len(node.args) >= 3:
            owner, member = self._name(node.args[0]), _string(node.args[1])
            if owner and member:
                replacement = f"{owner}.{member}"
                if owner in self.classes and member == "__del__":
                    self._add("finalizer", node, f"{owner}.__del__")
        if replacement in self.functions:
            self._add("function_rebinding", node, f"patch of {replacement}")
        self.generic_visit(node)


def relaxed_findings(sources: Mapping[Path, str], *, root: Path) -> tuple[RelaxedFinding, ...]:
    """Inspect the supplied analysed files once, returning stable source order."""
    module_root = root
    while (module_root / "__init__.py") in sources or (module_root / "__init__.py").is_file():
        module_root = module_root.parent
    parsed: list[tuple[Path, str, ast.Module]] = []
    for path, source in sorted(sources.items()):
        try:
            tree = ast.parse(source, filename=str(path))
        except (SyntaxError, ValueError):
            continue
        parsed.append((path, _module_name(path, module_root), tree))
    functions = frozenset(
        f"{module}.{node.name}"
        for _, module, tree in parsed
        for node in _scope_nodes(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    classes = frozenset(
        f"{module}.{node.name}"
        for _, module, tree in parsed
        for node in _scope_nodes(tree)
        if isinstance(node, ast.ClassDef)
    )
    findings: list[RelaxedFinding] = []
    for path, module, tree in parsed:
        shown = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
        scanner = _Scanner(shown, module, functions, classes)
        scanner.visit(tree)
        findings.extend(scanner.findings)
    return tuple(
        sorted(set(findings), key=lambda item: (item.path, item.line, item.kind, item.detail))
    )


def findings_for_paths(paths: Iterable[Path], *, root: Path) -> tuple[RelaxedFinding, ...]:
    """Inspect an already filtered inventory; skipped/unreadable files add no evidence."""
    sources = {path: source for path in paths if (source := try_read_source(path)) is not None}
    return relaxed_findings(sources, root=root)


def findings_for_target(target: Path) -> tuple[RelaxedFinding, ...]:
    """Include consumers outside extraction, using the same root as other program scans."""
    root = find_project_root(target)
    paths = {path for path in program_files(root) if path.suffix == ".py"}
    if target.is_file():
        paths.add(target)
    return findings_for_paths(paths, root=root)


def relaxed_notice(findings: Sequence[RelaxedFinding]) -> str:
    """One notice containing the evidence and the remedy; empty without findings."""
    if not findings:
        return ""
    evidence = "; ".join(f"{item.path}:{item.line}: {item.detail}" for item in findings)
    return (
        "Note: relaxed mode assumes exact object lifetimes and function lookup timing "
        "are not observed; --strict (or strict = true under [tool.towel]) preserves them. "
        f"Found {evidence}."
    )
