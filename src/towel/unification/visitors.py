"""
Top-level AST visitors used by the refactoring engine.

These classes were extracted from nested definitions in refactor_engine.py to improve
readability and enable a clearer compiler-like pipeline structure.
"""
from __future__ import annotations

import ast
from typing import Callable, List, Optional, Set, Tuple, Union, Literal

MethodKind = Literal["instance", "classmethod", "staticmethod"]


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
        self.class_stack.append(node.name)
        self.generic_visit(node)
        self.class_stack.pop()


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
        self.class_stack.append(node.name)
        self.generic_visit(node)
        self.class_stack.pop()

    def _record(self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef]) -> None:
        ancestry = [n for n in self.func_stack if n is not None]
        self.sink(node, self.class_stack[-1], self.func_stack[-1], ancestry)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._record(node)
        self.func_stack.append(node.name)
        self.generic_visit(node)
        self.func_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._record(node)
        self.func_stack.append(node.name)
        self.generic_visit(node)
        self.func_stack.pop()


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
            implicit_name = self.implicit_name or ("self" if self.method_kind == "instance" else "cls")
            if self.method_kind in {"instance", "classmethod"}:
                n.args = self.drop_positional(n.args, implicit_name)
                n.keywords = self.drop_keyword(n.keywords, implicit_name)
                attr = ast.Attribute(value=ast.Name(id=implicit_name, ctx=ast.Load()), attr=self.new_name, ctx=ast.Load())
                ast.copy_location(attr, n.func)
                n.func = attr
            elif self.method_kind == "staticmethod" and self.class_name:
                attr = ast.Attribute(value=ast.Name(id=self.class_name, ctx=ast.Load()), attr=self.new_name, ctx=ast.Load())
                ast.copy_location(attr, n.func)
                n.func = attr
        return n


class LoopReturnFinder(ast.NodeVisitor):
    def __init__(self) -> None:
        self.has_loop_return = False
        self.in_loop = False

    def visit_For(self, node: ast.For) -> None:  # noqa: N802
        old_in_loop = self.in_loop
        self.in_loop = True
        self.generic_visit(node)
        self.in_loop = old_in_loop

    def visit_While(self, node: ast.While) -> None:  # noqa: N802
        old_in_loop = self.in_loop
        self.in_loop = True
        self.generic_visit(node)
        self.in_loop = old_in_loop

    def visit_Return(self, node: ast.Return) -> None:  # noqa: N802
        if self.in_loop:
            self.has_loop_return = True

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        return None

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        return None


class NameCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.used: Set[str] = set()

    def visit_Name(self, n: ast.Name) -> None:  # noqa: N802
        if isinstance(n.ctx, ast.Load):
            self.used.add(n.id)
        self.generic_visit(n)

    def visit_FunctionDef(self, n: ast.FunctionDef) -> None:  # noqa: N802
        return None

    def visit_AsyncFunctionDef(self, n: ast.AsyncFunctionDef) -> None:  # noqa: N802
        return None


class AugAssignFinder(ast.NodeVisitor):
    def __init__(self) -> None:
        self.aug_assign_targets: Set[str] = set()

    def visit_AugAssign(self, node: ast.AugAssign) -> None:  # noqa: N802
        if isinstance(node.target, ast.Name):
            self.aug_assign_targets.add(node.target.id)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        return None

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        return None


class AssignTargetVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.assigned_names: Set[str] = set()
        self.declared_global_in_block: Set[str] = set()
        self.declared_nonlocal_in_block: Set[str] = set()

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        for t in node.targets:
            if isinstance(t, ast.Name):
                self.assigned_names.add(t.id)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:  # noqa: N802
        if isinstance(node.target, ast.Name):
            self.assigned_names.add(node.target.id)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
        if isinstance(node.target, ast.Name):
            self.assigned_names.add(node.target.id)
        self.generic_visit(node)

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
    def __init__(self, source: str, target_name: str) -> None:
        self.source = source
        self.target_name = target_name
        self.result: Optional[Tuple[int, str]] = None

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        if node.name == self.target_name and hasattr(node, "end_lineno"):
            lines = self.source.splitlines()
            class_line = lines[node.lineno - 1]
            indent = class_line[: len(class_line) - len(class_line.lstrip())]
            end_lineno = getattr(node, "end_lineno", None)
            if isinstance(end_lineno, int):
                insert_line = end_lineno - 1
            else:
                insert_line = node.lineno
            self.result = (insert_line, indent)
        self.generic_visit(node)


class FuncLocator(ast.NodeVisitor):
    def __init__(self, source: str, function_name: str) -> None:
        self.source = source
        self.function_name = function_name
        self.result: Optional[Tuple[int, str]] = None

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        if node.name == self.function_name:
            lines = self.source.splitlines()
            fn_line = lines[node.lineno - 1]
            indent = fn_line[: len(fn_line) - len(fn_line.lstrip())]
            body = list(node.body)
            start_idx = 0
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                start_idx = 1
            insert_line: Optional[int] = None
            last_def_end: Optional[int] = None
            for i, stmt in enumerate(body[start_idx:], start=start_idx):
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
        else:
            self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        if node.name == self.function_name:
            lines = self.source.splitlines()
            fn_line = lines[node.lineno - 1]
            indent = fn_line[: len(fn_line) - len(fn_line.lstrip())]
            body = list(node.body)
            start_idx = 0
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                start_idx = 1
            insert_line: Optional[int] = None
            last_def_end: Optional[int] = None
            for i, stmt in enumerate(body[start_idx:], start=start_idx):
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
        else:
            self.generic_visit(node)
