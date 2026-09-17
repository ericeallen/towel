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

"""Type annotations for an extracted helper, taken from what its call sites declare.

Towel does not infer types. A helper parameter is annotated only when every
call site passes something whose type the site already states: a parameter of
the enclosing function that carries an annotation and is never rebound, or a
literal of a builtin type. The return is annotated when every site returns the
helper's value from a function with a declared return type, when the helper
returns locals the block annotated, or when the helper returns nothing.

An annotation is copied as the site spelled it. It is inserted unquoted only
when it cannot fail to resolve where the helper is defined: every name is a
builtin, the module defers annotations with ``from __future__ import
annotations``, or every name is bound by a module-level import, which precedes
the helper. Otherwise it is inserted as a string, which never evaluates and
which type checkers resolve in the module. Across modules only builtin names
are used, since a site's imports are not the host's.

Helpers are annotated only in code that already uses annotations somewhere
among the sites, so an unannotated project stays that way.
"""

from __future__ import annotations

import ast
import builtins
import copy
from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Sequence, Set, Union

from .semantic_safety import _walk_own_scope

FunctionNode = Union[ast.FunctionDef, ast.AsyncFunctionDef]

_BUILTIN_NAMES: Set[str] = set(dir(builtins))


@dataclass(frozen=True)
class CallSite:
    """One replacement: the generated call and the function and module it sits in."""

    statement: ast.stmt
    call: ast.Call
    function: FunctionNode
    module: ast.Module
    file_path: str


def call_in_statement(statement: ast.AST, helper_name: str) -> Optional[ast.Call]:
    """The positional call to ``helper_name`` that a generated statement wraps, if any."""
    value: Optional[ast.expr]
    if isinstance(statement, (ast.Return, ast.Expr)):
        value = statement.value
    elif isinstance(statement, ast.Assign):
        value = statement.value
    else:
        return None
    if isinstance(value, ast.Await):
        value = value.value
    if (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == helper_name
        and not value.keywords
        and not any(isinstance(argument, ast.Starred) for argument in value.args)
    ):
        return value
    return None


def annotate_helper(
    helper: ast.FunctionDef,
    sites: Sequence[CallSite],
    host_file: str,
    return_variables: Sequence[str],
) -> ast.FunctionDef:
    """A copy of ``helper`` carrying every annotation the sites agree on."""
    annotated = copy.deepcopy(helper)
    if not sites or not any(_uses_annotations(site.function) for site in sites):
        return annotated
    host = next((site.module for site in sites if site.file_path == host_file), None)
    same_module = all(site.file_path == host_file for site in sites)
    parameters = annotated.args.posonlyargs + annotated.args.args
    for index, parameter in enumerate(parameters):
        candidates = [_argument_annotation(site, index) for site in sites]
        parameter.annotation = _agreed(candidates, host, same_module)
    annotated.returns = _agreed(
        [_return_annotation(site, annotated, return_variables) for site in sites],
        host,
        same_module,
    )
    return annotated


def _uses_annotations(function: FunctionNode) -> bool:
    arguments = function.args
    every = arguments.posonlyargs + arguments.args + arguments.kwonlyargs
    if arguments.vararg:
        every.append(arguments.vararg)
    if arguments.kwarg:
        every.append(arguments.kwarg)
    return function.returns is not None or any(arg.annotation is not None for arg in every)


def _parameter_annotations(function: FunctionNode) -> Dict[str, ast.expr]:
    arguments = function.args
    return {
        arg.arg: arg.annotation
        for arg in arguments.posonlyargs + arguments.args + arguments.kwonlyargs
        if arg.annotation is not None
    }


def _own_scope_nodes(function: FunctionNode) -> Iterator[ast.AST]:
    """Every node of the function's body that is not inside a nested scope."""
    for statement in function.body:
        yield from _walk_own_scope(statement)


def _rebinds(function: FunctionNode, name: str) -> bool:
    """Whether the function's own scope binds ``name`` again after its parameter."""
    for node in _own_scope_nodes(function):
        if (
            isinstance(node, ast.Name)
            and node.id == name
            and isinstance(node.ctx, (ast.Store, ast.Del))
        ):
            return True
        if isinstance(node, (ast.Global, ast.Nonlocal)) and name in node.names:
            return True
        if isinstance(node, ast.ExceptHandler) and node.name == name:
            return True
        if isinstance(node, (ast.Import, ast.ImportFrom)) and any(
            (alias.asname or alias.name.split(".")[0]) == name for alias in node.names
        ):
            return True
        if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name == name:
            return True
        if isinstance(node, ast.MatchMapping) and node.rest == name:
            return True
    return False


def _argument_annotation(site: CallSite, index: int) -> Optional[ast.expr]:
    """What the site declares about the type of its ``index``-th argument."""
    if index >= len(site.call.args):
        return None
    argument = site.call.args[index]
    if isinstance(argument, ast.Name):
        annotation = _parameter_annotations(site.function).get(argument.id)
        if annotation is None or _rebinds(site.function, argument.id):
            return None
        return annotation
    if isinstance(argument, ast.Constant):
        return _literal_type(argument.value)
    return None


def _literal_type(value: object) -> Optional[ast.expr]:
    # bool first: True is an int.
    for kind in (bool, int, float, complex, str, bytes):
        if type(value) is kind:
            return ast.Name(id=kind.__name__, ctx=ast.Load())
    return None


def _return_annotation(
    site: CallSite, helper: ast.FunctionDef, return_variables: Sequence[str]
) -> Optional[ast.expr]:
    if return_variables:
        declared = _annotated_locals(helper)
        if any(name not in declared for name in return_variables):
            return None
        annotations = [declared[name] for name in return_variables]
        if len(annotations) == 1:
            return annotations[0]
        return ast.Subscript(
            value=ast.Name(id="tuple", ctx=ast.Load()),
            slice=ast.Tuple(elts=[copy.deepcopy(a) for a in annotations], ctx=ast.Load()),
            ctx=ast.Load(),
        )
    if isinstance(site.statement, ast.Return):
        return site.function.returns
    if isinstance(site.statement, ast.Expr):
        return ast.Constant(value=None)
    return None


def _annotated_locals(helper: ast.FunctionDef) -> Dict[str, ast.expr]:
    """Names the helper body binds with an annotated assignment, in its own scope."""
    found: Dict[str, ast.expr] = {}
    for node in _own_scope_nodes(helper):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id not in found
        ):
            found[node.target.id] = node.annotation
    return found


def _agreed(
    candidates: Sequence[Optional[ast.expr]],
    host: Optional[ast.Module],
    same_module: bool,
) -> Optional[ast.expr]:
    """One annotation when every site supplies the same one and it resolves in the host."""
    present = [candidate for candidate in candidates if candidate is not None]
    if not present or len(present) != len(candidates):
        return None
    first = present[0]
    if any(ast.dump(candidate) != ast.dump(first) for candidate in present[1:]):
        return None
    return _spelled_for_host(copy.deepcopy(first), host, same_module)


def _spelled_for_host(
    annotation: ast.expr, host: Optional[ast.Module], same_module: bool
) -> Optional[ast.expr]:
    """The annotation as it can be written where the helper is defined, or None."""
    expression = annotation
    quoted = False
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        try:
            expression = ast.parse(annotation.value, mode="eval").body
        except SyntaxError:
            return None
        quoted = True
    names = _referenced_names(expression)
    if names <= _BUILTIN_NAMES:
        return expression
    if not same_module or host is None:
        return None
    if _defers_annotations(host) or names - _BUILTIN_NAMES <= _import_bound_names(host):
        return expression
    return annotation if quoted else ast.Constant(value=ast.unparse(expression))


def _referenced_names(expression: ast.expr) -> Set[str]:
    return {node.id for node in ast.walk(expression) if isinstance(node, ast.Name)}


def _defers_annotations(module: ast.Module) -> bool:
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in module.body
    )


def _import_bound_names(module: ast.Module) -> Set[str]:
    bound: Set[str] = set()
    for node in module.body:
        if isinstance(node, ast.Import):
            bound.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            bound.update(alias.asname or alias.name for alias in node.names if alias.name != "*")
    return bound


def annotated_names(annotated: ast.FunctionDef) -> List[str]:
    """Parameters that received an annotation, for diagnostics."""
    return [arg.arg for arg in annotated.args.posonlyargs + annotated.args.args if arg.annotation]
