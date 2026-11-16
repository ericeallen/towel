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

"""Code in an analysed file that Towel never considers as a candidate, counted per file.

A candidate is a run of whole statements inside a function. Two kinds of code
that a reviewer would call duplicated are therefore never examined: statements
at module level (a script's body, a configuration block), and a guard that
returns early on some paths only (``if not x: return None``), since a helper
could not return from its caller. A run that finds nothing can say so with
these counts (audit finding 2, engine map E).
"""

from __future__ import annotations

import ast
from typing import Iterator, List, Optional

from .extractor import has_complete_return_coverage
from .models import NotExamined

_DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom)


def _is_docstring(statement: ast.stmt) -> bool:
    return isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant)


def _main_guard_body(statement: ast.stmt) -> Optional[List[ast.stmt]]:
    """The body of ``if __name__ == "__main__":``, or None for any other statement."""
    if not isinstance(statement, ast.If) or statement.orelse:
        return None
    test = statement.test
    if (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
    ):
        return statement.body
    return None


def module_level_statements(module: ast.Module) -> int:
    """The module's own executable statements: not definitions, imports or docstrings.

    The body of an ``if __name__ == "__main__":`` block counts statement by
    statement, since that is where a script's work stands.
    """
    count = 0
    for statement in module.body:
        if isinstance(statement, _DEFINITIONS) or _is_docstring(statement):
            continue
        body = _main_guard_body(statement)
        if body is not None:
            count += sum(not isinstance(inner, _DEFINITIONS) for inner in body)
            continue
        count += 1
    return count


def _functions(node: ast.AST) -> Iterator[ast.AST]:
    for child in ast.walk(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield child


def _own_statements(function: ast.AST) -> Iterator[ast.stmt]:
    pending = list(getattr(function, "body", []))
    while pending:
        statement = pending.pop()
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        yield statement
        for field in ("body", "orelse", "finalbody", "handlers", "cases"):
            for child in getattr(statement, field, []) or []:
                if isinstance(child, ast.stmt):
                    pending.append(child)
                elif isinstance(child, (ast.ExceptHandler, ast.match_case)):
                    pending.extend(child.body)


def early_return_guards(module: ast.Module) -> int:
    """``if`` statements in functions that return on some of their paths but not all."""
    return sum(
        1
        for function in _functions(module)
        for statement in _own_statements(function)
        if isinstance(statement, ast.If)
        and any(isinstance(node, ast.Return) for node in _returns_in(statement))
        and not has_complete_return_coverage([statement])
    )


def _returns_in(statement: ast.stmt) -> Iterator[ast.AST]:
    pending: list[ast.AST] = [statement]
    while pending:
        node = pending.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        pending.extend(ast.iter_child_nodes(node))


def not_examined(path: str, tree: ast.AST) -> Optional[NotExamined]:
    """What in this file Towel does not examine, or None when there is nothing."""
    if not isinstance(tree, ast.Module):
        return None
    found = NotExamined(path, module_level_statements(tree), early_return_guards(tree))
    return found if found.module_statements or found.early_return_guards else None
