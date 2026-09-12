"""Conservative guards for extractions that change execution context."""

from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Iterable, Set, Union

from .binding_detector import BindingDetector
from .project_layout import ProjectLayout


def nested_bindings_escape(
    function: Union[ast.FunctionDef, ast.AsyncFunctionDef], nodes: Iterable[ast.AST]
) -> bool:
    """Reject nested extraction whose local writes are observable outside it.

    The engine's return-variable analysis covers top-level statement slices.
    A loop/branch body needs control-flow liveness, including reads on the next
    loop iteration. Until that analysis exists, require its bindings to remain
    entirely inside the extracted block. Attribute/subscript mutations do not
    rebind their base objects and are not counted as local writes.
    """
    block = tuple(nodes)
    if all(node in function.body for node in block):
        return False
    extracted = {child for statement in block for child in ast.walk(statement)}
    detector = BindingDetector()
    for statement in block:
        detector.visit(statement)
    bound = {binding.name for binding in detector.bindings}
    # Deletion changes the original local binding too, and is not a binding
    # construct reported by BindingDetector. Store contexts also conservatively
    # include declarations that do not initialize a value.
    bound.update(
        node.id
        for node in extracted
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del))
    )
    if not bound:
        return False
    for node in ast.walk(function):
        if node in extracted:
            continue
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, (ast.Load, ast.Del))
            and node.id in bound
        ):
            return True
        if (
            isinstance(node, ast.AugAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id in bound
        ):
            return True
    return False


def requires_original_frame(nodes: Iterable[ast.AST]) -> bool:
    """Reject suspension and operations that inspect the original call frame.

    Generator delegation needs a separate transformation preserving send/throw
    and return values. Moving frame inspection into a helper is not equivalent.
    Unknown shadowing of these call names is deliberately treated conservatively.
    """
    for statement in nodes:
        for node in ast.walk(statement):
            if isinstance(node, (ast.Yield, ast.YieldFrom, ast.Await, ast.AsyncFor, ast.AsyncWith)):
                return True
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in {"locals", "globals", "eval", "exec"}:
                    return True
                if node.func.id in {"vars", "super"} and not node.args and not node.keywords:
                    return True
    return False


def would_create_import_cycle(canonical_file: str, replacement_files: Set[str]) -> bool:
    """Check whether adding imports of the helper closes a local import cycle.

    Follow static imports through local modules, including modules without any
    candidate functions and package initializers. Imports inside functions are
    included conservatively. Dynamic imports cannot be resolved statically.
    """
    canonical = Path(canonical_file).resolve()
    targets = {Path(path).resolve() for path in replacement_files} - {canonical}
    if not targets:
        return False
    common_root = Path(os.path.commonpath([str(path.parent) for path in targets | {canonical}]))
    roots = set(ProjectLayout.discover(canonical).source_roots) | {common_root}

    def module_files(base: Path, components: Iterable[str]) -> Set[Path]:
        result: Set[Path] = set()
        cursor = base
        for component in components:
            cursor = cursor / component
            initializer = cursor / "__init__.py"
            if initializer.is_file():
                result.add(initializer.resolve())
        module = cursor.with_suffix(".py")
        if module.is_file():
            result.add(module.resolve())
        return result

    pending = [canonical]
    visited: Set[Path] = set()
    while pending:
        current = pending.pop()
        if current in targets:
            return True
        if current in visited:
            continue
        visited.add(current)
        try:
            tree = ast.parse(current.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, SyntaxError):
            # If an import cannot be inspected, do not claim that it is safe.
            return True
        dependencies: Set[Path] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for root in roots:
                        dependencies.update(module_files(root, alias.name.split(".")))
            elif isinstance(node, ast.ImportFrom):
                bases = roots
                if node.level:
                    base = current.parent
                    for _ in range(node.level - 1):
                        base = base.parent
                    bases = {base}
                components = node.module.split(".") if node.module else []
                for base in bases:
                    dependencies.update(module_files(base, components))
                    for alias in node.names:
                        if alias.name != "*":
                            dependencies.update(module_files(base, [*components, alias.name]))
        pending.extend(dependencies - visited)
    return False
