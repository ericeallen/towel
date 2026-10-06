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

"""Transfer certified whole-body argument ownership into the helper frame.

Inference sees the original call; only an owned render copy receives the
holder and the call prefix. Partial blocks and unproved scopes retain their
ordinary frame-boundary refusal.
"""

from __future__ import annotations

import ast
import copy
import inspect
import sys
from types import CodeType
from typing import Dict, List, Optional, Sequence, Set, Tuple
from weakref import WeakKeyDictionary

from ..canonical_ast import canonical_dump
from .assignment_analyzer import own_scope_bindings
from .function_scope import function_names, identifiers
from .models import ArgumentHandoff, FunctionNode
from .parameters import parameter_names
from .scope_analyzer import ScopeAnalyzer, type_parameter_names
from .statement_facts import memoized_per_node
from .visitors import body_without_docstring


def _supported_runtime() -> bool:
    """The transfer proof uses CPython reference-count/local-slot cleanup."""
    return sys.implementation.name == "cpython"


_COMPILED_CODES: "WeakKeyDictionary[ast.AST, Tuple[CodeType, ...]]" = WeakKeyDictionary()


def _compiled_codes(module: ast.AST) -> Tuple[CodeType, ...]:
    """Immutable compiler facts, retained only while the immutable source AST lives."""
    if not isinstance(module, ast.Module):
        return ()
    try:
        pending = [
            compile(
                ast.fix_missing_locations(copy.deepcopy(module)),
                "<towel-ownership>",
                "exec",
                dont_inherit=True,
            )
        ]
    except (SyntaxError, TypeError, ValueError):
        return ()
    result = []
    while pending:
        code = pending.pop()
        result.append(code)
        pending.extend(value for value in code.co_consts if isinstance(value, CodeType))
    return tuple(result)


def _code(module: ast.Module, function: FunctionNode) -> Optional[CodeType]:
    first = min([function.lineno, *(node.lineno for node in function.decorator_list)])
    return next(
        (
            code
            for code in memoized_per_node(_COMPILED_CODES, module, _compiled_codes)
            if code.co_name == function.name and code.co_firstlineno == first
        ),
        None,
    )


def _fresh(stem: str, taken: Set[str]) -> str:
    name = stem
    index = 0
    while name in taken:
        index += 1
        name = f"{stem}_{index}"
    taken.add(name)
    return name


def _scoped_expression(
    expression: ast.expr, parameters: Sequence[str]
) -> Tuple[ast.expr, Set[ast.Name]]:
    """Own a copy and identify only reads resolving to the caller's parameters."""
    wrapper = ast.parse("def wrapper(): pass").body[0]
    assert isinstance(wrapper, ast.FunctionDef)
    wrapper.args.args = [ast.arg(arg=name) for name in parameters]
    value = copy.deepcopy(expression)
    wrapper.body = [ast.Expr(value=value)]
    module = ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[]))
    analyzer = ScopeAnalyzer()
    analyzer.analyze(module)
    scope = analyzer.node_scopes[wrapper]
    reads = {
        node
        for node in ast.walk(value)
        if isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Load)
        and node.id in parameters
        and (binding := analyzer.get_binding_for_name(node)) is not None
        and binding.scope_id == scope.scope_id
    }
    return value, reads


def _helper_call(statement: ast.AST, name: str) -> Optional[ast.Call]:
    found = [
        node
        for node in ast.walk(statement)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name
    ]
    return found[0] if len(found) == 1 else None


def argument_handoff_plan(
    function: FunctionNode,
    nodes: Sequence[ast.stmt],
    analyzer: ScopeAnalyzer,
    call_node: ast.stmt,
    helper_ast: ast.FunctionDef,
) -> Optional[ArgumentHandoff]:
    """Certify one complete body with immutable original parameters and fresh internal thunks."""
    module = analyzer.analyzed_tree
    body = body_without_docstring(function.body)
    scope = analyzer.node_scopes.get(function)
    if (
        not _supported_runtime()
        or not isinstance(function, ast.FunctionDef)
        or not isinstance(module, ast.Module)
        or scope is None
        or len(nodes) != len(body)
        or any(left is not right for left, right in zip(nodes, body))
        or getattr(helper_ast, "type_comment", None) is not None
        or scope.lookup("object") is not None
    ):
        return None
    names = set(parameter_names(function.args))
    if not names or any(name.startswith("__") and not name.endswith("__") for name in names):
        return None
    if any(binding.name in names for binding in own_scope_bindings(body)):
        return None
    # Deletion is not a binding fact. Conservatively include nested deletes,
    # while ordinary comprehension targets remain in their own scope.
    if any(
        isinstance(node, ast.Name) and isinstance(node.ctx, ast.Del) and node.id in names
        for node in ast.walk(function)
    ):
        return None
    # A nested nonlocal writer can replace a parameter after the handoff.
    if any(
        isinstance(node, ast.Nonlocal) and names.intersection(node.names)
        for node in ast.walk(function)
    ):
        return None
    code = _code(module, function)
    if (
        code is None
        or code.co_flags
        & (inspect.CO_GENERATOR | inspect.CO_COROUTINE | inspect.CO_ASYNC_GENERATOR)
        or names.intersection(code.co_cellvars)
    ):
        return None
    # Native binders remain in the unchanged caller's annotation scope. Local
    # annotations are not evaluated, even when CPython lists their binder as a
    # free variable; runtime reads/nested evaluated annotations need a separate
    # scope-preservation proof and remain conservative refusals.
    runtime = function_names(function)
    if (
        type_parameter_names(function)
        .intersection(code.co_freevars)
        .intersection(set(runtime.read) | set(runtime.nested))
    ):
        return None
    order = tuple(name for name in code.co_varnames if name in names)
    if set(order) != names:
        return None
    call = _helper_call(call_node, helper_ast.name)
    parameters = helper_ast.args.posonlyargs + helper_ast.args.args
    if (
        call is None
        or call.keywords
        or len(call.args) != len(parameters)
        or any(isinstance(value, ast.Starred) for value in call.args)
    ):
        return None
    helper_module = ast.fix_missing_locations(
        ast.Module(body=[copy.deepcopy(helper_ast)], type_ignores=[])
    )
    helper = helper_module.body[0]
    assert isinstance(helper, ast.FunctionDef)
    helper_code = _code(helper_module, helper)
    if helper_code is None:
        return None
    original_lambdas = {
        canonical_dump(node) for node in ast.walk(function) if isinstance(node, ast.Lambda)
    }
    parents = {
        child: parent for parent in ast.walk(helper_ast) for child in ast.iter_child_nodes(parent)
    }
    for index, argument in enumerate(call.args):
        _, reads = _scoped_expression(argument, order)
        if not reads or not isinstance(argument, ast.Lambda):
            continue
        if (
            canonical_dump(argument) in original_lambdas
            or argument.args.posonlyargs
            or argument.args.args
            or argument.args.kwonlyargs
            or argument.args.vararg
            or argument.args.kwarg
            or argument.args.defaults
            or argument.args.kw_defaults
            or any(
                isinstance(node, (ast.Lambda, ast.GeneratorExp)) for node in ast.walk(argument.body)
            )
            or parameters[index].arg in helper_code.co_cellvars
        ):
            return None
        # The fresh thunk stays internal, invoked directly, never stored or passed on.
        for node in ast.walk(helper_ast):
            if (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and node.id == parameters[index].arg
            ):
                parent = parents.get(node)
                if not isinstance(parent, ast.Call) or parent.func is not node:
                    return None
    taken = set(identifiers((module, helper_ast)))
    taken.update(
        name
        for tree in (module, helper_ast)
        for node in ast.walk(tree)
        for name in type_parameter_names(node)
    )
    box = _fresh("_towel_arguments", taken)
    captures = tuple((name, _fresh("_towel_argument", taken)) for name in order)
    return ArgumentHandoff(
        order,
        box,
        captures,
        function.name,
        function.lineno,
        canonical_dump(function),
        canonical_dump(call_node),
        helper_ast.name,
        canonical_dump(module),
    )


def verify_argument_handoff(
    plan: ArgumentHandoff, source: str, call_node: ast.stmt, helper_ast: ast.FunctionDef
) -> bool:
    """Re-prove eligibility against exact original source; metadata alone is not authority."""
    try:
        module = ast.parse(source)
    except SyntaxError:
        return False
    found = [
        node
        for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef)
        and node.name == plan.function_name
        and node.lineno == plan.function_line
    ]
    if (
        len(found) != 1
        or canonical_dump(module) != plan.module_dump
        or canonical_dump(found[0]) != plan.function_dump
        or canonical_dump(call_node) != plan.call_dump
    ):
        return False
    analyzer = ScopeAnalyzer()
    analyzer.analyze(module)
    refreshed = argument_handoff_plan(
        found[0], body_without_docstring(found[0].body), analyzer, call_node, helper_ast
    )
    return refreshed == plan


def _item(box: str, index: int) -> ast.expr:
    return ast.Subscript(
        value=ast.Subscript(
            value=ast.Name(id=box, ctx=ast.Load()), slice=ast.Constant(0), ctx=ast.Load()
        ),
        slice=ast.Constant(index),
        ctx=ast.Load(),
    )


class _ReplaceReads(ast.NodeTransformer):
    def __init__(self, reads: Set[ast.Name], replacements: Dict[str, ast.expr]) -> None:
        self.reads = reads
        self.replacements = replacements

    def visit_Name(self, node: ast.Name) -> ast.expr:
        return copy.deepcopy(self.replacements[node.id]) if node in self.reads else node


def render_argument_handoff(node: ast.AST, plan: ArgumentHandoff, helper_name: str) -> ast.Module:
    """Return an owned prefix/call module, with the last argument consuming the box."""
    result = copy.deepcopy(node)
    calls = [
        call
        for call in ast.walk(result)
        if isinstance(call, ast.Call)
        and (
            isinstance(call.func, ast.Name)
            and call.func.id == helper_name
            or isinstance(call.func, ast.Attribute)
            and call.func.attr == helper_name
        )
    ]
    if len(calls) != 1:
        raise ValueError("Argument handoff needs one generated call")
    call = calls[0]
    positions = {
        name: len(plan.parameters) - 1 - index for index, name in enumerate(plan.parameters)
    }
    captures = dict(plan.captures)

    def rewrite(expression: ast.expr) -> ast.expr:
        value, reads = _scoped_expression(expression, plan.parameters)
        replacements = {name: _item(plan.box_name, index) for name, index in positions.items()}
        if isinstance(value, ast.Lambda) and reads:
            used = sorted({read.id for read in reads})
            captured_values = [replacements[name] for name in used]
            replacements = {name: ast.Name(id=captures[name], ctx=ast.Load()) for name in used}
            thunk = _ReplaceReads(reads, replacements).visit(value)
            if not isinstance(thunk, ast.Lambda):
                raise ValueError("Argument capture must preserve the internal thunk")
            # A zero-argument thunk keeps its contextual Callable[[], T] type.
            # Optional lambda defaults defeat mypy's contextual inference.
            return ast.Call(
                func=ast.Lambda(
                    args=ast.arguments(
                        posonlyargs=[],
                        args=[ast.arg(arg=captures[name]) for name in used],
                        kwonlyargs=[],
                        kw_defaults=[],
                        defaults=[],
                    ),
                    body=thunk,
                ),
                args=captured_values,
                keywords=[],
            )
        rewritten = _ReplaceReads(reads, replacements).visit(value)
        if not isinstance(rewritten, ast.expr):
            raise ValueError("Argument rewriting must preserve an expression")
        return rewritten

    call.func = rewrite(call.func)
    call.args = [rewrite(argument) for argument in call.args]
    call.args.append(
        ast.Call(
            func=ast.Attribute(
                value=ast.Name(id=plan.box_name, ctx=ast.Load()), attr="pop", ctx=ast.Load()
            ),
            args=[],
            keywords=[],
        )
    )
    prefix: List[ast.stmt] = [
        ast.Assign(
            targets=[ast.Name(id=plan.box_name, ctx=ast.Store())],
            value=ast.List(
                elts=[
                    ast.Tuple(
                        elts=[
                            ast.Name(id=name, ctx=ast.Load()) for name in reversed(plan.parameters)
                        ],
                        ctx=ast.Load(),
                    )
                ],
                ctx=ast.Load(),
            ),
        ),
        # Keep deletion statements independent: a formatter can parenthesize
        # a long multi-target del into a different AST shape. The box already
        # holds every argument, so splitting these statements releases no owner.
        *[ast.Delete(targets=[ast.Name(id=name, ctx=ast.Del())]) for name in plan.parameters],
    ]
    if not isinstance(result, ast.stmt):
        raise ValueError("Argument handoff replaces one statement")
    return ast.fix_missing_locations(ast.Module(body=[*prefix, result], type_ignores=[]))
