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

"""The visitor bases every AST analysis is built on, and the shared visitors.

Three Template Method bases fix how a traversal treats scopes, so an
analysis states only what it does with what it finds: ``OwnScopeVisitor``
for collectors that read one scope's own code, ``DefinitionDepthVisitor``
for those that track how deeply a definition sits, and ``ScopeVisitor``
for analyses that follow Python's lexical scopes in one visiting order.
The concrete visitors here (function collection, method-call rewriting,
loop-return, name and free-name collection, augmented-assignment and
assignment targets, class and function insertion points) are the ones more
than one module needs.
"""

from __future__ import annotations

import ast
from ..source_text import source_lines
from .models import FunctionNode, MethodKind
from .parameters import parameter_names
from typing import (
    Callable,
    Iterable,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
    Type,
    TypeGuard,
    TypeVar,
    Union,
    cast,
)

T = TypeVar("T")
NodeT = TypeVar("NodeT", bound=ast.AST)


def all_instances(items: Sequence[object], kind: Type[T]) -> TypeGuard[List[T]]:
    """Whether every item is a ``kind``, narrowing the sequence for the type checker."""
    return all(isinstance(item, kind) for item in items)


def visit_as(transformer: ast.NodeTransformer, node: NodeT) -> NodeT:
    """``transformer.visit(node)`` as a node of the same kind.

    ``NodeTransformer.visit`` is typed as returning ``Any``; every transformer
    here returns a node of the kind it was given (an expression for an
    expression, a statement for a statement), and this is the one place
    that says so.
    """
    return cast(NodeT, transformer.visit(node))


class OwnScopeVisitor(ast.NodeVisitor):
    """A visitor of one scope's own code (Template Method).

    The traversal is fixed here: a nested ``def`` or ``async def`` is handed
    to :meth:`_nested_function`, a nested ``class`` to :meth:`_nested_class`,
    a lambda to :meth:`_lambda`, and a comprehension to
    :meth:`_comprehension`. The defaults are what most collectors want: a
    nested function is not entered (its body is another scope), while a
    class body, a lambda, and a comprehension are, since they run where they
    stand. A subclass records a definition's name, or declines to enter one
    of these, by overriding the hook, never by redefining the traversal.
    """

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._nested_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._nested_function(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._nested_class(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._lambda(node)

    def visit_ListComp(
        self, node: Union[ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp]
    ) -> None:
        self._comprehension(node)

    visit_SetComp = visit_ListComp
    visit_DictComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp

    def _nested_function(self, node: FunctionNode) -> None:
        """Hook: a function defined in this scope. The default does not enter it."""

    def _nested_class(self, node: ast.ClassDef) -> None:
        """Hook: a class defined in this scope. The default enters its body."""
        self.generic_visit(node)

    def _lambda(self, node: ast.Lambda) -> None:
        """Hook: a lambda in this scope. The default enters it."""
        self.generic_visit(node)

    def _comprehension(
        self, node: Union[ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp]
    ) -> None:
        """Hook: a comprehension in this scope. The default enters it."""
        self.generic_visit(node)


class DefinitionDepthVisitor(ast.NodeVisitor):
    """A visitor that enters every definition and knows how deeply it sits (Template Method).

    Each function or class definition is announced to :meth:`_enter_definition`
    with ``_depth`` still at the enclosing level, entered, and then announced
    to :meth:`_leave_definition`. Subclasses keep whatever stack they need in
    those hooks.
    """

    _depth = 0

    def visit_FunctionDef(
        self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef]
    ) -> None:
        self._enter_definition(node)
        self._depth += 1
        try:
            self.generic_visit(node)
        finally:
            self._depth -= 1
            self._leave_definition(node)

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef

    def _enter_definition(
        self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef]
    ) -> None:
        """Hook: about to enter ``node``, whose enclosing depth is ``_depth``."""

    def _leave_definition(
        self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef]
    ) -> None:
        """Hook: ``node`` has been entered and left."""


class ScopeVisitor(ast.NodeVisitor):
    """An analysis that follows Python's lexical scopes (Template Method).

    The order in which a definition's parts are visited is fixed here, once,
    for every scope-tracking analysis: the expressions evaluated in the
    enclosing scope, the name the definition binds there, the new scope, its
    parameters, its body, and finally anything the analysis visits after. A
    comprehension evaluates its first iterable outside, then binds every
    target inside its own scope before the remaining iterables, conditions,
    and result are visited, which is Python's scoping. Subclasses implement
    :meth:`_enter_scope` and :meth:`_leave_scope` and override the binding
    hooks; the two hooks with a traversal of their own, :meth:`_lambda` and
    :meth:`_comprehension`, exist for an analysis that must keep a lambda or
    a comprehension in the enclosing scope, and say so where they do.
    """

    def visit_FunctionDef(self, node: FunctionNode) -> None:
        self._visit_definition_head(node)
        self._bind_definition_name(node)
        self._enter_scope(node)
        self._bind_parameters(node.args)
        self._visit_statements(node.body)
        self._leave_scope(node)
        self._visit_definition_tail(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_definition_head(node)
        self._bind_definition_name(node)
        self._enter_scope(node)
        self._visit_class_body(node)
        self._leave_scope(node)
        self._visit_definition_tail(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._lambda(node)

    def visit_ListComp(
        self, node: Union[ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp]
    ) -> None:
        self._comprehension(node)

    visit_SetComp = visit_ListComp
    visit_DictComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp

    # -- hooks with a traversal of their own ---------------------------------

    def _lambda(self, node: ast.Lambda) -> None:
        """A lambda is a scope of its own: its parameters bind inside, its body runs there."""
        self._visit_definition_head(node)
        self._enter_scope(node)
        self._bind_parameters(node.args)
        self.visit(node.body)
        self._leave_scope(node)

    def _comprehension(
        self, node: Union[ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp]
    ) -> None:
        """A comprehension is a scope of its own; only its first iterable is evaluated outside."""
        if node.generators:
            self.visit(node.generators[0].iter)
        self._enter_scope(node)
        for generator in node.generators:
            self._bind_target(generator.target)
        for index, generator in enumerate(node.generators):
            if index:
                self.visit(generator.iter)
            for condition in generator.ifs:
                self.visit(condition)
        visit_comprehension_result(self, node)
        self._leave_scope(node)

    # -- primitive operations ------------------------------------------------

    def _enter_scope(self, node: ast.AST) -> object:
        """Begin the scope ``node`` introduces."""
        raise NotImplementedError

    def _leave_scope(self, node: ast.AST) -> None:
        """End the scope ``node`` introduced."""
        raise NotImplementedError

    def _visit_definition_head(
        self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda]
    ) -> None:
        """Hook: the parts evaluated in the enclosing scope before the definition (decorators, defaults, bases)."""

    def _visit_definition_tail(
        self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef]
    ) -> None:
        """Hook: parts an analysis visits after the definition's scope has been left."""

    def _bind_definition_name(
        self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef]
    ) -> None:
        """Hook: the definition's name is bound in the enclosing scope."""

    def _bind_parameters(self, args: ast.arguments) -> None:
        """Hook: a callable's parameters are bound in the scope just entered."""

    def _bind_target(self, target: ast.AST) -> None:
        """Hook: an assignment-like target is bound in the current scope."""

    def _visit_statements(self, statements: Sequence[ast.stmt]) -> None:
        for statement in statements:
            self.visit(statement)

    def _visit_class_body(self, node: ast.ClassDef) -> None:
        """Hook: the class body, in the class's scope."""
        self._visit_statements(node.body)


def evaluated_before_definition(
    node: Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda],
) -> List[ast.expr]:
    """What a definition evaluates in the enclosing scope before it exists.

    Its decorators, and a callable's defaults and keyword-only defaults:
    ``lambda v, s=k: ...`` and ``def f(s=k)`` read ``k`` where they stand,
    once, when the definition runs.
    """
    evaluated: List[ast.expr] = []
    if not isinstance(node, ast.Lambda):
        evaluated.extend(node.decorator_list)
    if not isinstance(node, ast.ClassDef):
        evaluated.extend(node.args.defaults)
        evaluated.extend(value for value in node.args.kw_defaults if value is not None)
    return evaluated


def annotation_expressions(node: FunctionNode) -> List[ast.expr]:
    """A function's parameter and return annotations, in source order.

    They are evaluated when the definition runs unless the module postpones
    annotations (and lazily, when first read, from Python 3.14): either way
    they read names where the function is defined.
    """
    arguments = node.args
    annotated = [
        *arguments.posonlyargs,
        *arguments.args,
        *([arguments.vararg] if arguments.vararg else []),
        *arguments.kwonlyargs,
        *([arguments.kwarg] if arguments.kwarg else []),
    ]
    found = [argument.annotation for argument in annotated if argument.annotation is not None]
    if node.returns is not None:
        found.append(node.returns)
    return found


def type_parameter_expressions(node: ast.AST) -> List[ast.expr]:
    """What a PEP 695 definition's type parameters evaluate, lazily: bounds, constraints, defaults.

    Attribute inspection keeps this importable on Python 3.11, whose AST has
    no type parameters, and reads the defaults Python 3.13 added.
    """
    parameters: object = getattr(node, "type_params", ())
    if not isinstance(parameters, list):
        return []
    found: List[ast.expr] = []
    for parameter in parameters:
        for attribute in ("bound", "default_value"):
            value: object = getattr(parameter, attribute, None)
            if isinstance(value, ast.expr):
                found.append(value)
    return found


class FunctionCollector(DefinitionDepthVisitor):
    """Collect functions with enclosing class/function context for a module tree.

    Calls sink with (node, class_name, enclosing_function, ancestry).
    """

    def __init__(
        self,
        sink: Callable[
            [FunctionNode, Optional[str], Optional[str], List[str]],
            None,
        ],
    ) -> None:
        self.sink = sink
        self.class_stack: List[Optional[str]] = [None]
        self.func_stack: List[Optional[str]] = [None]

    def _enter_definition(
        self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef]
    ) -> None:
        if isinstance(node, ast.ClassDef):
            self.class_stack.append(node.name)
            return
        ancestry = [n for n in self.func_stack if n is not None]
        self.sink(node, self.class_stack[-1], self.func_stack[-1], ancestry)
        self.func_stack.append(node.name)

    def _leave_definition(
        self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef]
    ) -> None:
        (self.class_stack if isinstance(node, ast.ClassDef) else self.func_stack).pop()


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


class LoopReturnFinder(OwnScopeVisitor):
    """Detect whether a code block contains return statements inside loops.

    Used to identify control flow patterns that may prevent safe refactoring.
    """

    def __init__(self) -> None:
        self.has_loop_return = False
        self.in_loop = False

    def visit_For(self, node: ast.For) -> None:
        _visit_loop_and_restore_flag(self, node)

    def visit_While(self, node: ast.While) -> None:
        _visit_loop_and_restore_flag(self, node)

    def visit_Return(self, node: ast.Return) -> None:
        if self.in_loop:
            self.has_loop_return = True


class NameCollector(OwnScopeVisitor):
    """Collect all names referenced in Load context within a code block.

    Stops at nested function boundaries to avoid capturing scopes outside the block.
    """

    def __init__(self) -> None:
        self.used: Set[str] = set()

    def visit_Name(self, n: ast.Name) -> None:
        _record_load_name_and_visit(n, self.used, self)

    def visit_AnnAssign(self, n: ast.AnnAssign) -> None:
        # An annotation inside a function body is never evaluated.
        if n.value is not None:
            self.visit(n.value)
        self.visit(n.target)


class FreeNameCollector(NameCollector):
    """Collect the names a node reads from its own scope.

    A lambda's parameters are bound by the lambda and read inside it, so a
    forwarding ``lambda *args, **kwargs: f(*args, **kwargs)`` reads ``f``
    and nothing else; ``NameCollector`` would report ``args`` and ``kwargs``.
    """

    def _lambda(self, node: ast.Lambda) -> None:
        for default in [*node.args.defaults, *node.args.kw_defaults]:
            if default is not None:
                self.visit(default)
        inner = FreeNameCollector()
        inner.visit(node.body)
        self.used |= inner.used - set(parameter_names(node.args))


class AugAssignFinder(OwnScopeVisitor):
    """Find all variables modified by augmented assignment operators (+=, -=, etc.).

    Stops at nested function boundaries to avoid capturing scopes outside the block.
    """

    def __init__(self) -> None:
        self.aug_assign_targets: Set[str] = set()

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        _record_simple_assignment(node, self.aug_assign_targets, self)


class AssignTargetVisitor(OwnScopeVisitor):
    """Collect all assignment targets and global/nonlocal declarations in a block.

    Tracks simple name assignments from Assign, AugAssign, and AnnAssign nodes,
    plus global and nonlocal declarations. Stops at nested function boundaries.
    """

    def __init__(self) -> None:
        self.assigned_names: Set[str] = set()
        self.declared_global_in_block: Set[str] = set()
        self.declared_nonlocal_in_block: Set[str] = set()

    def visit_Assign(self, node: ast.Assign) -> None:
        for t in node.targets:
            _record_name_target(t, self.assigned_names)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        _record_simple_assignment(node, self.assigned_names, self)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        _record_simple_assignment(node, self.assigned_names, self)

    def visit_Global(self, node: ast.Global) -> None:
        for n in node.names:
            self.declared_global_in_block.add(n)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        for n in node.names:
            self.declared_nonlocal_in_block.add(n)


class ClassLocator(DefinitionDepthVisitor):
    """Locate a class definition by name and determine its insertion point.

    Returns the line number and indentation suitable for inserting a new method
    at the end of the target class body.
    """

    def __init__(self, source: str, target_name: str) -> None:
        self.source = source
        self.target_name = target_name
        self.result: Optional[Tuple[int, str]] = None
        self.matches = 0
        self._depth = 0

    def _enter_definition(
        self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef]
    ) -> None:
        if isinstance(node, ast.ClassDef) and node.name == self.target_name:
            self.matches += 1
            # Only a unique module-level class is an unambiguous target. A
            # class nested in a function is a fresh object per call, and two
            # classes sharing a name cannot be told apart by name.
            # A body written on the header's line (``class Base: pass``) takes
            # no further statement: a line appended after it is either an
            # unexpected indent or, at the header's own indentation, a
            # statement outside the class.
            if (
                self._depth == 0
                and self.matches == 1
                and node.body
                and not body_shares_header_line(source_lines(self.source), node)
            ):
                indent = _compute_indent(self.source, node.body[0].lineno)
                self.result = ((node.end_lineno or node.lineno) - 1, indent)
            else:
                self.result = None


class FuncLocator(OwnScopeVisitor):
    """Locate a function definition by name and determine its helper insertion point.

    Returns the line number and indentation suitable for inserting a new helper
    function inside the target function, after any existing nested definitions
    but before the first executable statement. Every function is entered until
    the named one is found; that one is not, so a same-named function nested
    in it cannot displace the result.
    """

    def __init__(self, source: str, function_name: str) -> None:
        self.source = source
        self.function_name = function_name
        self.result: Optional[Tuple[int, str]] = None

    def _nested_function(self, node: FunctionNode) -> None:
        if node.name != self.function_name:
            self.generic_visit(node)
            return
        indent = _compute_indent(self.source, node.lineno)
        body = body_without_docstring(node.body)
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
    node: Union[ast.AnnAssign, ast.AugAssign], destination: Set[str], visitor: ast.NodeVisitor
) -> None:
    target = getattr(node, "target", None)
    if isinstance(target, ast.Name):
        destination.add(target.id)
    visitor.generic_visit(node)


def body_without_docstring(body: Sequence[ast.stmt]) -> List[ast.stmt]:
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


def body_shares_header_line(lines: Sequence[str], node: ast.ClassDef) -> bool:
    """Whether the class's first statement is written after its header's colon.

    ``class Base: pass`` and a header split over several lines whose last one
    carries the body alike: the text before the first statement on its line is
    more than indentation. ``col_offset`` counts UTF-8 bytes, so the line is
    compared as bytes.
    """
    if not node.body or not 0 < node.body[0].lineno <= len(lines):
        return False
    first = node.body[0]
    return bool(lines[first.lineno - 1].encode("utf-8")[: first.col_offset].strip())


def _compute_indent(source: str, lineno: int) -> str:
    lines = source_lines(source)
    index = max(0, min(len(lines) - 1, lineno - 1))
    line = lines[index] if lines else ""
    return line[: len(line) - len(line.lstrip())]


def visit_each(visitor: ast.NodeVisitor, nodes: Iterable[ast.AST]) -> None:
    """Visit every node in turn; the visitor accumulates whatever it collects."""
    for node in nodes:
        visitor.visit(node)


def visit_comprehension_generators(
    visitor: ast.NodeVisitor,
    node: Union[ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp],
) -> None:
    """Visit each generator's iterable and conditions, in order; the targets are left to the caller."""
    for generator in node.generators:
        visitor.visit(generator.iter)
        for condition in generator.ifs:
            visitor.visit(condition)


def visit_comprehension_result(
    visitor: ast.NodeVisitor,
    node: Union[ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp],
) -> None:
    """Visit what a comprehension produces: the key and value of a dict, else the element."""
    if isinstance(node, ast.DictComp):
        visitor.visit(node.key)
        visitor.visit(node.value)
    else:
        visitor.visit(node.elt)
