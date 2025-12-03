# Copyright 2025 Eric Allen
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

"""
Top-level AST visitors used by the refactoring engine.

These classes were extracted from nested definitions in refactor_engine.py to improve
readability and enable a clearer compiler-like pipeline structure.
"""

from __future__ import annotations

import ast
from typing import Callable, List, Optional, Set, Tuple, Union, Literal, Sequence, TypeVar

MethodKind = Literal["instance", "classmethod", "staticmethod"]
T = TypeVar("T")


class ClassCollector(ast.NodeVisitor):
    """Collect class metadata (qualname and base names) for a module tree.

    Produces tuples of (qualname, bases) via the provided sink callback.
    """

    def __init__(
        self,
        file_path: str,
        base_resolver: Callable[[ast.expr], Optional[str]],
        sink: Callable[[str, List[str]], None],
    ) -> None:
        self.file_path = file_path
        self.base_resolver = base_resolver
        self.sink = sink
        self.class_stack: List[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802 (ast API)
        qualname = ".".join(self.class_stack + [node.name]) if self.class_stack else node.name
        bases: List[str] = []
        for base in node.bases:
            resolved = self.base_resolver(base)
            if resolved:
                bases.append(resolved)
        self.sink(qualname, bases)
        _push_value_and_visit(self.class_stack, node.name, self, node)


class FunctionCollector(ast.NodeVisitor):
    """Collect functions with enclosing class/function context for a module tree.

    Calls sink with (node, class_name, enclosing_function, ancestry).
    """

    def __init__(
        self,
        sink: Callable[
            [Union[ast.FunctionDef, ast.AsyncFunctionDef], Optional[str], Optional[str], List[str]],
            None,
        ],
    ) -> None:
        self.sink = sink
        self.class_stack: List[Optional[str]] = [None]
        self.func_stack: List[Optional[str]] = [None]

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        _push_value_and_visit(self.class_stack, node.name, self, node)

    def _record(self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef]) -> None:
        ancestry = [n for n in self.func_stack if n is not None]
        self.sink(node, self.class_stack[-1], self.func_stack[-1], ancestry)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._record(node)
        _push_value_and_visit(self.func_stack, node.name, self, node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._record(node)
        _push_value_and_visit(self.func_stack, node.name, self, node)


class MethodCallRewriter(ast.NodeTransformer):
    """Rewrite calls to a helper function into proper method dispatch syntax.

    This mirrors the engine's previous inner-class behavior.
    """

    def __init__(
        self,
        drop_positional: Callable[[List[ast.expr], str], List[ast.expr]],
        drop_keyword: Callable[[List[ast.keyword], str], List[ast.keyword]],
        *,
        original_name: str,
        new_name: str,
        method_kind: Optional[MethodKind],
        implicit_name: Optional[str],
        class_name: Optional[str],
    ) -> None:
        self.drop_positional = drop_positional
        self.drop_keyword = drop_keyword
        self.original_name = original_name
        self.new_name = new_name
        self.method_kind = method_kind
        self.implicit_name = implicit_name
        self.class_name = class_name

    def visit_Call(self, n: ast.Call) -> ast.AST:
        self.generic_visit(n)
        if isinstance(n.func, ast.Name) and n.func.id == self.original_name and self.method_kind:
            implicit_name = self.implicit_name or (
                "self" if self.method_kind == "instance" else "cls"
            )
            if self.method_kind in {"instance", "classmethod"}:
                n.args = self.drop_positional(n.args, implicit_name)
                n.keywords = self.drop_keyword(n.keywords, implicit_name)
                attr = ast.Attribute(
                    value=ast.Name(id=implicit_name, ctx=ast.Load()),
                    attr=self.new_name,
                    ctx=ast.Load(),
                )
                ast.copy_location(attr, n.func)
                n.func = attr
            elif self.method_kind == "staticmethod" and self.class_name:
                attr = ast.Attribute(
                    value=ast.Name(id=self.class_name, ctx=ast.Load()),
                    attr=self.new_name,
                    ctx=ast.Load(),
                )
                ast.copy_location(attr, n.func)
                n.func = attr
        return n


class LoopReturnFinder(ast.NodeVisitor):
    """Detect whether a code block contains return statements inside loops.

    Used to identify control flow patterns that may prevent safe refactoring.
    """

    def __init__(self) -> None:
        self.has_loop_return = False
        self.in_loop = False

    def visit_For(self, node: ast.For) -> None:  # noqa: N802
        _visit_loop_and_restore_flag(self, node)

    def visit_While(self, node: ast.While) -> None:  # noqa: N802
        _visit_loop_and_restore_flag(self, node)

    def visit_Return(self, node: ast.Return) -> None:  # noqa: N802
        if self.in_loop:
            self.has_loop_return = True

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        return None

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        return None


class NameCollector(ast.NodeVisitor):
    """Collect all names referenced in Load context within a code block.

    Stops at nested function boundaries to avoid capturing scopes outside the block.
    """

    def __init__(self) -> None:
        self.used: Set[str] = set()

    def visit_Name(self, n: ast.Name) -> None:  # noqa: N802
        _record_load_name_and_visit(n, self.used, self)

    def visit_FunctionDef(self, n: ast.FunctionDef) -> None:  # noqa: N802
        return None

    def visit_AsyncFunctionDef(self, n: ast.AsyncFunctionDef) -> None:  # noqa: N802
        return None


class AugAssignFinder(ast.NodeVisitor):
    """Find all variables modified by augmented assignment operators (+=, -=, etc.).

    Stops at nested function boundaries to avoid capturing scopes outside the block.
    """

    def __init__(self) -> None:
        self.aug_assign_targets: Set[str] = set()

    def visit_AugAssign(self, node: ast.AugAssign) -> None:  # noqa: N802
        _record_simple_assignment(node, self.aug_assign_targets, self)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        return None

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        return None


class AssignTargetVisitor(ast.NodeVisitor):
    """Collect all assignment targets and global/nonlocal declarations in a block.

    Tracks simple name assignments from Assign, AugAssign, and AnnAssign nodes,
    plus global and nonlocal declarations. Stops at nested function boundaries.
    """

    def __init__(self) -> None:
        self.assigned_names: Set[str] = set()
        self.declared_global_in_block: Set[str] = set()
        self.declared_nonlocal_in_block: Set[str] = set()

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        for t in node.targets:
            _record_name_target(t, self.assigned_names)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:  # noqa: N802
        _record_simple_assignment(node, self.assigned_names, self)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
        _record_simple_assignment(node, self.assigned_names, self)

    def visit_Global(self, node: ast.Global) -> None:  # noqa: N802
        for n in node.names:
            self.declared_global_in_block.add(n)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:  # noqa: N802
        for n in node.names:
            self.declared_nonlocal_in_block.add(n)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        return None

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        return None


class ClassLocator(ast.NodeVisitor):
    """Locate a class definition by name and determine its insertion point.

    Returns the line number and indentation suitable for inserting a new method
    at the end of the target class body.
    """

    def __init__(self, source: str, target_name: str) -> None:
        self.source = source
        self.target_name = target_name
        self.result: Optional[Tuple[int, str]] = None

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        if node.name == self.target_name and hasattr(node, "end_lineno"):
            indent = _compute_indent(self.source, node.lineno)
            end_lineno = getattr(node, "end_lineno", None)
            if isinstance(end_lineno, int):
                insert_line = end_lineno - 1
            else:
                insert_line = node.lineno
            self.result = (insert_line, indent)
        self.generic_visit(node)


class FuncLocator(ast.NodeVisitor):
    """Locate a function definition by name and determine its helper insertion point.

    Returns the line number and indentation suitable for inserting a new helper
    function inside the target function, after any existing nested definitions
    but before the first executable statement.
    """

    def __init__(self, source: str, function_name: str) -> None:
        self.source = source
        self.function_name = function_name
        self.result: Optional[Tuple[int, str]] = None

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._maybe_capture_insertion_point(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._maybe_capture_insertion_point(node)

    def _maybe_capture_insertion_point(
        self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef]
    ) -> None:
        if node.name != self.function_name:
            self.generic_visit(node)
            return
        indent = _compute_indent(self.source, node.lineno)
        body = _body_without_docstring(node.body)
        insert_line: Optional[int] = None
        last_def_end: Optional[int] = None
        for stmt in body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                stmt_end = getattr(stmt, "end_lineno", stmt.lineno)
                if isinstance(stmt_end, int):
                    last_def_end = stmt_end
                continue
            insert_line = stmt.lineno - 1
            break
        if insert_line is None:
            if last_def_end is not None:
                insert_line = last_def_end
            else:
                insert_line = node.lineno
        self.result = (insert_line, indent)


def _push_value_and_visit(
    stack: List[T], value: T, visitor: ast.NodeVisitor, node: ast.AST
) -> None:
    stack.append(value)
    try:
        visitor.generic_visit(node)
    finally:
        stack.pop()


def _visit_loop_and_restore_flag(
    visitor: "LoopReturnFinder", node: Union[ast.For, ast.While]
) -> None:
    previous_flag = visitor.in_loop
    visitor.in_loop = True
    visitor.generic_visit(node)
    visitor.in_loop = previous_flag


def _record_load_name_and_visit(
    node: ast.Name, destination: Set[str], visitor: ast.NodeVisitor
) -> None:
    if isinstance(node.ctx, ast.Load):
        destination.add(node.id)
    visitor.generic_visit(node)


def _record_name_target(target: ast.AST, destination: Set[str]) -> None:
    if isinstance(target, ast.Name):
        destination.add(target.id)


def _record_simple_assignment(
    node: Union[ast.AnnAssign, ast.AugAssign], destination: Set[str], visitor: ast.AST
) -> None:
    target = getattr(node, "target", None)
    if isinstance(target, ast.Name):
        destination.add(target.id)
    visitor.generic_visit(node)


def _body_without_docstring(body: Sequence[ast.stmt]) -> List[ast.stmt]:
    body_list = list(body)
    if not body_list:
        return []

    first_stmt = body_list[0]
    if (
        isinstance(first_stmt, ast.Expr)
        and isinstance(first_stmt.value, ast.Constant)
        and isinstance(first_stmt.value.value, str)
    ):
        return body_list[1:]

    return body_list


def _compute_indent(source: str, lineno: int) -> str:
    lines = source.splitlines()
    index = max(0, min(len(lines) - 1, lineno - 1))
    line = lines[index] if lines else ""
    return line[: len(line) - len(line.lstrip())]
