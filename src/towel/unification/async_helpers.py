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

"""A helper whose body awaits is an ``async def``, and every call of it is awaited.

The engine builds every helper and call as a plain function and a plain call;
this module turns them into a coroutine function and ``await helper(…)`` where
they are rendered, so that one predicate decides both
(docs/design/DECISIONS.md, "A block that awaits becomes an async helper that
each site awaits").
"""

from __future__ import annotations

import ast
import copy
from typing import Iterable, Iterator, Union

_NEW_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _own_frame(node: ast.AST) -> Iterator[ast.AST]:
    """``node`` and what runs in its frame: nested functions, lambdas and classes left out."""
    yield node
    for child in ast.iter_child_nodes(node):
        if not isinstance(child, _NEW_SCOPES):
            yield from _own_frame(child)


def suspends(node: ast.AST) -> bool:
    """Whether evaluating ``node`` can suspend its coroutine: ``await``, ``async for`` or ``with``,
    or an async comprehension, outside any function or lambda it defines."""
    return any(
        isinstance(inner, (ast.Await, ast.AsyncFor, ast.AsyncWith))
        or (isinstance(inner, ast.comprehension) and inner.is_async == 1)
        for inner in _own_frame(node)
    )


def awaits_in_own_frame(statements: Iterable[ast.AST]) -> bool:
    """Whether a helper with this body must be an ``async def``."""
    return any(suspends(statement) for statement in statements)


def as_coroutine_function(helper: ast.FunctionDef) -> ast.AsyncFunctionDef:
    """The same definition, written ``async def``."""
    original = copy.deepcopy(helper)
    converted = ast.AsyncFunctionDef(
        name=original.name,
        args=original.args,
        body=original.body,
        decorator_list=original.decorator_list,
        returns=original.returns,
        type_comment=original.type_comment,
    )
    if hasattr(original, "type_params"):  # Python 3.12 and later
        setattr(converted, "type_params", getattr(original, "type_params"))
    return ast.copy_location(converted, original)


def with_coroutine_helper(node: ast.AST, helper_name: str) -> ast.AST:
    """``node``, or a module ending with it, with the helper named ``helper_name`` made async."""
    if isinstance(node, ast.FunctionDef) and node.name == helper_name:
        return as_coroutine_function(node)
    if isinstance(node, ast.Module) and node.body:
        last = node.body[-1]
        if isinstance(last, ast.FunctionDef) and last.name == helper_name:
            return ast.Module(
                body=[*node.body[:-1], as_coroutine_function(last)],
                type_ignores=list(node.type_ignores),
            )
    return node


class _AwaitHelperCalls(ast.NodeTransformer):
    def __init__(self, helper_name: str) -> None:
        self.helper_name = helper_name

    def _calls_helper(self, node: ast.AST) -> bool:
        if not isinstance(node, ast.Call):
            return False
        callee = node.func
        return (isinstance(callee, ast.Name) and callee.id == self.helper_name) or (
            isinstance(callee, ast.Attribute) and callee.attr == self.helper_name
        )

    def visit_Await(self, node: ast.Await) -> ast.AST:
        if self._calls_helper(node.value):
            return node
        self.generic_visit(node)
        return node

    def visit_Call(self, node: ast.Call) -> ast.AST:
        self.generic_visit(node)
        if self._calls_helper(node):
            return ast.copy_location(ast.Await(value=node), node)
        return node


def awaiting_helper_calls(node: ast.AST, helper_name: str) -> ast.AST:
    """A copy of ``node`` in which every call of the helper is awaited."""
    rewritten: Union[ast.AST, None] = _AwaitHelperCalls(helper_name).visit(copy.deepcopy(node))
    assert rewritten is not None
    return ast.fix_missing_locations(rewritten)
