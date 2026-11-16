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
import builtins
import copy
from dataclasses import dataclass
from typing import (
    FrozenSet,
    Iterable,
    Iterator,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
)

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


# -- What a coroutine's boundary does to an exception -------------------------------
#
# Awaiting the helper adds a coroutine boundary between the block and the
# function it came from, and two exceptions do not cross it unchanged
# (docs/design/DECISIONS.md, "A block that awaits becomes an async helper
# that each site awaits"):
#
# - A ``StopIteration`` leaving a coroutine becomes ``RuntimeError("coroutine
#   raised StopIteration")`` (PEP 479). In the original it reached the
#   function's own handlers as ``StopIteration``; only at the function's own
#   boundary did it become a ``RuntimeError`` (and in an async generator one
#   that says "async generator raised StopIteration").
# - A ``GeneratorExit`` that ``close()`` delivers at an ``await`` inside the
#   helper is raised again in the caller once the helper returns (PEP 380),
#   even where the block, as the original ran it, stopped it and went on.
#
# ``StopAsyncIteration`` and a ``GeneratorExit`` the block raises itself
# leave a coroutine unchanged.


def _builtin_exception(name: str) -> Optional[type]:
    value = getattr(builtins, name, None)
    return value if isinstance(value, type) and issubclass(value, BaseException) else None


@dataclass(frozen=True)
class _ModuleNames:
    """What the module's code binds: every name, and those bound only by a plain ``class``."""

    bound: FrozenSet[str]
    classes_only: FrozenSet[str]


def _module_names(module: Optional[ast.AST]) -> _ModuleNames:
    """Every name the module binds anywhere, in any scope, and those only an undecorated ``class`` binds."""
    if module is None:
        return _ModuleNames(frozenset(), frozenset())
    names: Set[str] = set()
    other: Set[str] = set()
    for node in ast.walk(module):
        if isinstance(node, ast.ClassDef):
            names.add(node.name)
            if node.decorator_list:
                other.add(node.name)
            continue
        bound: Optional[str] = None
        if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            bound = node.id
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            bound = node.name
        elif isinstance(node, ast.arg):
            bound = node.arg
        elif isinstance(node, ast.alias):
            bound = node.asname or node.name.split(".", 1)[0]
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
            bound = node.name
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            other.update(node.names)
        if bound is not None:
            names.add(bound)
            other.add(bound)
    return _ModuleNames(frozenset(names), frozenset(names - other))


def _may_catch(handler: ast.ExceptHandler, raised: Tuple[type, ...], names: _ModuleNames) -> bool:
    """Whether ``handler`` may catch any of ``raised``, each a builtin exception class.

    Matching an exception compares classes (``PyType_IsSubtype``), not
    ``__instancecheck__``. A class the module defines with a ``class``
    statement is therefore never a base of a builtin exception; a builtin
    exception class read by its own name, which no code of the module
    rebinds, is known. A bare ``except`` and anything else may catch it.
    """
    if handler.type is None:
        return True
    elements = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    for element in elements:
        if not isinstance(element, ast.Name):
            return True
        if element.id in names.classes_only:
            continue
        known = None if element.id in names.bound else _builtin_exception(element.id)
        if known is None or any(issubclass(exception, known) for exception in raised):
            return True
    return False


def _path_to(root: ast.AST, target: ast.AST) -> Optional[List[ast.AST]]:
    """The nodes from ``root`` down to ``target`` in ``root``'s own frame, both included."""
    if root is target:
        return [root]
    for child in ast.iter_child_nodes(root):
        if isinstance(child, _NEW_SCOPES):
            continue
        path = _path_to(child, target)
        if path is not None:
            return [root, *path]
    return None


def _in_body(statement: ast.AST, child: ast.AST) -> bool:
    return any(item is child for item in getattr(statement, "body", ()))


def _observed_around(function: ast.AST, block: Sequence[ast.stmt], names: _ModuleNames) -> bool:
    """Whether something in ``function`` around the block sees the type of an exception it raises.

    A handler of a ``try`` whose body holds the block, if it may catch
    ``StopIteration`` or ``RuntimeError``; a ``with`` or ``async with`` whose
    body holds it, since its exit receives the exception.
    """
    path = _path_to(function, block[0]) if block else None
    if path is None:
        return True
    for outer, inner in zip(path[1:], path[2:]):
        if not _in_body(outer, inner):
            continue
        if isinstance(outer, (ast.With, ast.AsyncWith)):
            return True
        if isinstance(outer, (ast.Try, ast.TryStar)) and any(
            _may_catch(handler, (StopIteration, RuntimeError), names) for handler in outer.handlers
        ):
            return True
    return False


def _is_async_generator(function: ast.AST) -> bool:
    return isinstance(function, ast.AsyncFunctionDef) and any(
        isinstance(node, (ast.Yield, ast.YieldFrom))
        for statement in function.body
        for node in _own_frame(statement)
    )


def _suspension_points(statement: ast.AST) -> Iterator[ast.AST]:
    for node in _own_frame(statement):
        if isinstance(node, (ast.Await, ast.AsyncFor, ast.AsyncWith)) or (
            isinstance(node, ast.comprehension) and node.is_async == 1
        ):
            yield node


def _ends_or_suspends(statements: Sequence[ast.stmt]) -> bool:
    """Whether a ``finally`` suite may end the exception passing through it, or suspend."""
    return any(
        isinstance(node, (ast.Return, ast.Break, ast.Continue)) or suspends(node)
        for statement in statements
        for node in _own_frame(statement)
    )


def _may_stop_generator_exit(block: Sequence[ast.stmt], names: _ModuleNames) -> bool:
    """Whether the block may stop a ``GeneratorExit`` raised at one of its suspension points.

    Within the block, a suspension point stands in the body of a ``try``
    whose handler may catch ``GeneratorExit``, in a ``try`` whose ``finally``
    may return, break, continue or suspend, or in the body of a ``with`` or
    ``async with``, whose exit may suppress it or suspend.
    """
    for statement in block:
        for point in _suspension_points(statement):
            path = _path_to(statement, point) or []
            for outer, inner in zip(path, path[1:]):
                if isinstance(outer, (ast.With, ast.AsyncWith)) and _in_body(outer, inner):
                    return True
                if not isinstance(outer, (ast.Try, ast.TryStar)):
                    continue
                if any(item is inner for item in outer.finalbody):
                    continue
                if outer.finalbody and _ends_or_suspends(outer.finalbody):
                    return True
                if _in_body(outer, inner) and any(
                    _may_catch(handler, (GeneratorExit,), names) for handler in outer.handlers
                ):
                    return True
    return False


def exception_changes_at_coroutine_boundary(
    module: Optional[ast.AST], function: ast.AST, block: Sequence[ast.stmt]
) -> bool:
    """Whether an exception would reach the code around the block differently through an async helper.

    Only a block that awaits becomes a coroutine. Any call, attribute or
    operator it evaluates can let a ``StopIteration`` out of it, so the block
    is refused wherever the function would see that exception's type: under
    a handler or context manager around the block, or in an async generator,
    whose own boundary words the ``RuntimeError`` differently. It is refused
    as well where it may stop a ``GeneratorExit`` that the helper's caller
    would raise again.
    """
    if not awaits_in_own_frame(block):
        return False
    names = _module_names(module)
    return (
        _is_async_generator(function)
        or _observed_around(function, block, names)
        or _may_stop_generator_exit(block, names)
    )
