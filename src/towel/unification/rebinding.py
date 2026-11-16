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

"""Conservative source evidence that a proposed redirect's binding may change."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from ..renaming import inferred_rename_root
from ..project_layout import find_project_root
from ..analysis_sources import parse_analysis_source
from ..source_files import python_sources
from ..source_text import try_read_source
from .statement_facts import pattern_capture_names


def rebinding_scope(
    paths: Sequence[str],
    *,
    declared_complete: bool,
    program_root: Path | None = None,
    project_roots: Mapping[str, Path] | None = None,
    excluded: Iterable[str] = (),
) -> tuple[bool, dict[str, str | None]]:
    """Read the consumer scope, including modules with no duplicate functions.

    Project metadata and package ancestry establish the same scope as helper
    renaming. Without an anchor only the explicitly analyzed program is known.
    CLI entry points supply a consumer root even without a complete scope.
    """
    # Completeness belongs to the original project, not to a temporary
    # staging directory whose name/layout may hide an unanchored src root.
    project_scopes = {
        path: project_roots[path] if project_roots is not None else find_project_root(Path(path))
        for path in paths
    }
    roots = {
        inferred_rename_root(Path(path), project_root=root) for path, root in project_scopes.items()
    }
    complete = declared_complete or bool(roots and None not in roots)
    expanded = set(Path(path).resolve() for path in paths)
    scan_roots = {root for root in roots if root is not None}
    # src is an import root. Its sibling tests and scripts are consumers too,
    # even when no packaging metadata establishes completeness.
    scan_roots.update(root.parent for root in project_scopes.values() if root.name == "src")
    if program_root is not None:
        scan_roots.add(program_root)
        scan_roots.update(project_scopes.values())
    if declared_complete:
        scan_roots.update(Path(path).parent for path in paths)
    sources: dict[str, str | None] = {}
    for root in sorted(scan_roots):
        try:
            expanded.update(path.resolve() for path in python_sources(root, excluded=excluded))
        except OSError:
            # A read-only consumer scope is evidence for redirecting; failure
            # to inspect it must not turn an ordinary extraction into a failure.
            sources[str(root)] = None
    sources.update((str(path), try_read_source(path)) for path in sorted(expanded))
    return complete, sources


class _BindingWrites(ast.NodeVisitor):
    """Stores/deletes of one module binding, including explicit global stores."""

    def __init__(self, name: str, definition_line: int) -> None:
        self.name = name
        self.definition_line = definition_line
        self.module_scope = True
        self.global_scope = False
        self.line: int | None = None

    def visit_Name(self, node: ast.Name) -> None:
        if (
            node.id == self.name
            and isinstance(node.ctx, (ast.Store, ast.Del))
            and (self.module_scope or self.global_scope)
        ):
            self.line = node.lineno

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name == self.name and (self.module_scope or self.global_scope):
            self.line = node.lineno
        self.generic_visit(node)

    def visit_Match(self, node: ast.Match) -> None:
        if (self.module_scope or self.global_scope) and any(
            self.name in pattern_capture_names(case.pattern) for case in node.cases
        ):
            self.line = node.lineno
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        if (self.module_scope or self.global_scope) and any(
            (alias.asname or alias.name.split(".")[0]) == self.name for alias in node.names
        ):
            self.line = node.lineno

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if (self.module_scope or self.global_scope) and any(
            (alias.asname or alias.name) in (self.name, "*") for alias in node.names
        ):
            self.line = node.lineno

    def _definition(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        if (
            (self.module_scope or self.global_scope)
            and node.name == self.name
            and node.lineno != self.definition_line
        ):
            self.line = node.lineno
        for child in ast.iter_child_nodes(node):
            if child not in node.body:
                self.visit(child)
        previous = self.module_scope, self.global_scope
        self.module_scope = False
        self.global_scope = any(
            isinstance(child, ast.Global) and self.name in child.names for child in ast.walk(node)
        )
        for child in node.body:
            self.visit(child)
        self.module_scope, self.global_scope = previous

    visit_FunctionDef = _definition
    visit_AsyncFunctionDef = _definition
    visit_ClassDef = _definition


def _namespace_mapping(node: ast.expr, *, provider: bool) -> bool:
    """Recognize direct reflective mappings without assuming a receiver identity."""
    if isinstance(node, ast.Attribute):
        return node.attr == "__dict__"
    if not isinstance(node, ast.Call):
        return False
    called = (
        node.func.id
        if isinstance(node.func, ast.Name)
        else node.func.attr if isinstance(node.func, ast.Attribute) else ""
    )
    return (called == "vars" and (bool(node.args) or provider)) or (
        called == "globals" and provider
    )


def _possibly_target_key(node: ast.expr, name: str) -> bool:
    return not isinstance(node, ast.Constant) or node.value == name


def _namespace_mapping_write(node: ast.AST, name: str, *, provider: bool) -> bool:
    if isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del)):
        return _namespace_mapping(node.value, provider=provider) and _possibly_target_key(
            node.slice, name
        )
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    if not _namespace_mapping(node.func.value, provider=provider):
        return False
    method = node.func.attr
    if method in ("clear", "popitem", "update", "__ior__"):
        return True
    return (
        method in ("pop", "setdefault", "__setitem__", "__delitem__")
        and bool(node.args)
        and _possibly_target_key(node.args[0], name)
    )


def _call_spelling(node: ast.expr, aliases: Mapping[str, str]) -> str:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        return _call_spelling(node.value, aliases) + "." + node.attr
    return ""


def _mutation_reference(node: ast.expr, aliases: Mapping[str, str]) -> bool:
    spelling = _call_spelling(node, aliases)
    return "patch" in spelling.split(".") or spelling.rsplit(".", 1)[-1] in (
        "setattr",
        "delattr",
        "__setattr__",
        "__delattr__",
    )


def _patch_may_rebind(node: ast.Call, name: str, aliases: Mapping[str, str]) -> bool:
    """Only a known, literal non-target attribute can discharge a mutation call.

    Unknown overloads, splats and computed names are evidence against reuse.
    Receiver identity is deliberately not inferred: module objects can escape.
    """
    spelling = _call_spelling(node.func, aliases)
    called = spelling.rsplit(".", 1)[-1]
    if not _mutation_reference(node.func, aliases):
        return False
    if any(isinstance(arg, ast.Starred) for arg in node.args) or any(
        keyword.arg is None for keyword in node.keywords
    ):
        return True
    keywords = {keyword.arg: keyword.value for keyword in node.keywords}
    if called == "multiple":
        return name in keywords or not keywords
    if called in ("dict", "__setattr__", "__delattr__"):
        return True
    if called == "patch":
        attribute = node.args[0] if node.args else keywords.get("target")
    elif called == "object":
        attribute = node.args[1] if len(node.args) > 1 else keywords.get("attribute")
    elif called in ("setattr", "delattr"):
        # MonkeyPatch also accepts a dotted target in its first argument.
        target = node.args[0] if node.args else keywords.get("target")
        if isinstance(target, ast.Constant) and isinstance(target.value, str):
            attribute = target
        else:
            attribute = node.args[1] if len(node.args) > 1 else keywords.get("name")
    else:
        return True
    return not (
        isinstance(attribute, ast.Constant)
        and isinstance(attribute.value, str)
        and attribute.value.rsplit(".", 1)[-1] != name
    )


def rebinding_reason(
    sources: Mapping[str, str | None],
    target_path: str,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> str | None:
    """A plain sentence locating possible rebinding; None means the scan is clean.

    Attribute receivers may escape through arbitrary aliases, so a store of
    the target attribute anywhere is conservative evidence. The scan therefore
    never assumes that an unfamiliar receiver cannot be the target module.
    """
    name = function.name
    for path, source in sources.items():
        if source is None:
            return f"Cannot redirect to {name}: {path}:1 cannot be read as Python source."
        try:
            tree = parse_analysis_source(source, path)
        except SyntaxError as error:
            return f"Cannot redirect to {name}: {path}:{error.lineno or 1} cannot be analyzed."
        line: int | None = None
        provider = Path(path).resolve() == Path(target_path).resolve()
        if provider:
            writes = _BindingWrites(name, function.lineno)
            writes.visit(tree)
            line = writes.line
        aliases = {
            alias.asname or alias.name: alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        for node in ast.walk(tree):
            # An escaped mutation primitive or namespace mapping can be used
            # under any alias. Decline at the escape instead of guessing its uses.
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)) and (
                node.value is not None
                and (
                    _namespace_mapping(node.value, provider=provider)
                    or _mutation_reference(node.value, aliases)
                )
            ):
                line = node.lineno
            if _namespace_mapping_write(node, name, provider=provider):
                line = getattr(node, "lineno", 1)
            if (
                isinstance(node, ast.Attribute)
                and node.attr == name
                and isinstance(node.ctx, (ast.Store, ast.Del))
            ):
                line = node.lineno
            if not isinstance(node, ast.Call):
                continue
            if _patch_may_rebind(node, name, aliases):
                line = node.lineno
        if line is not None:
            return f"Cannot redirect to {name}: {path}:{line} may rebind it."
    return None
