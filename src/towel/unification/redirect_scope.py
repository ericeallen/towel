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

"""Resolve a redirected callee at the original expression or statement site.

The helper-placement analyzer omits some Python bindings and lambda scopes.
Redirects instead need every lexical binder, including bindings elsewhere in
the same function and comprehension targets whose clauses have not run yet.
"""

from __future__ import annotations

import ast

from .lexical_scopes import ScopeNames, innermost_mentioning, nested_scope_names
from .models import Replacement
from .scope_analyzer import type_parameter_names
from .visitors import ScopeVisitor, annotation_expressions, evaluated_before_definition


class _RedirectScope(ScopeVisitor):
    def __init__(self, replacement: Replacement, name: str) -> None:
        self.replacement = replacement
        self.name = name
        self.scopes: list[ScopeNames] = []
        self.found = False
        self.reaches_module = True

    def visit(self, node: ast.AST) -> None:
        if self._is_site(node):
            self.found = True
            index = innermost_mentioning(self.scopes, self.name)
            if index is not None and self.name not in self.scopes[index].declared_global:
                self.reaches_module = False
            if (
                self.name.startswith("__")
                and not self.name.endswith("__")
                and any(scope.is_class for scope in self.scopes)
            ):
                self.reaches_module = False
        super().visit(node)

    def _is_site(self, node: ast.AST) -> bool:
        place = self.replacement.expression
        if place is not None:
            return (
                isinstance(node, ast.expr)
                and (node.lineno, node.col_offset) == place.start
                and (node.end_lineno, node.end_col_offset) == place.end
            )
        columns = self.replacement.columns
        return (
            isinstance(node, ast.stmt)
            and node.lineno == self.replacement.line_range[0]
            and (columns is None or node.col_offset == columns.start)
        )

    def _enter_scope(self, node: ast.AST) -> None:
        assert isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
                ast.ClassDef,
                ast.Lambda,
                ast.ListComp,
                ast.SetComp,
                ast.DictComp,
                ast.GeneratorExp,
            ),
        )
        # Type parameters belong to an enclosing annotation scope, visible
        # through a class boundary where ordinary class attributes are not.
        self.scopes.append(ScopeNames(local=type_parameter_names(node)))
        self.scopes.append(nested_scope_names(node))

    def _leave_scope(self, node: ast.AST) -> None:
        self.scopes.pop()
        self.scopes.pop()

    def _visit_definition_head(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda
    ) -> None:
        for expression in evaluated_before_definition(node):
            self.visit(expression)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for expression in annotation_expressions(node):
                self.visit(expression)
        elif isinstance(node, ast.ClassDef):
            for expression in [*node.bases, *(keyword.value for keyword in node.keywords)]:
                self.visit(expression)


def redirect_resolves_at_module(tree: ast.Module, replacement: Replacement, name: str) -> bool:
    """Whether ``name`` at the replacement site reaches the module namespace.

    Check the first original statement, or the expression's exact span, before
    entering any scope it creates. A whole comprehension replaced by a call
    therefore does not shadow that call; a call inside its result does.
    A missing location declines; without columns every statement beginning
    on that line must resolve the name at module scope.
    """
    visitor = _RedirectScope(replacement, name)
    visitor.visit(tree)
    return visitor.found and visitor.reaches_module
