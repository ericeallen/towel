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
import re
from typing import Dict, Iterator, List, Optional, Sequence, Set, Tuple, Union

from .semantic_safety import _walk_own_scope
from ..type_inference import RevealRequest, TypeInferrer

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


def sites_use_annotations(sites: Sequence[CallSite]) -> bool:
    """Whether any site's function declares a type, the style gate for annotating."""
    return any(_uses_annotations(site.function) for site in sites)


_LITERAL = re.compile(r"Literal\[(?P<value>[^\]]*)\]\??")


def annotation_from_revealed(
    revealed: str, host: Optional[ast.Module], same_module: bool
) -> Optional[ast.expr]:
    """An annotation from mypy's spelling of a type, or None when it cannot be written.

    Inferred-literal markers (``Literal['x']?``) become the literal's builtin
    type and ``builtins.``/``?``/``*`` markers are dropped. Anything containing
    ``Any``, a callable, or an unresolvable name is declined; a dotted name is
    kept only when its head is bound in the host, or reduced to its last part
    when that is bound there.
    """
    text = revealed.strip()
    if not text or "Any" in text or "<" in text or text.startswith("def ") or "Never" in text:
        return None

    def literal_type(match: "re.Match[str]") -> str:
        try:
            value = ast.literal_eval(match.group("value"))
        except (ValueError, SyntaxError):
            return "Any"
        kind = _literal_type(value)
        return kind.id if isinstance(kind, ast.Name) else "Any"

    text = _LITERAL.sub(literal_type, text)
    if "Any" in text:
        return None
    text = text.replace("builtins.", "").replace("?", "").replace("*", "")
    try:
        expression = ast.parse(text, mode="eval").body
    except SyntaxError:
        return None
    reduced = _reduce_dotted_names(expression, host)
    if reduced is None:
        return None
    return _spelled_for_host(reduced, host, same_module)


def _reduce_dotted_names(expression: ast.expr, host: Optional[ast.Module]) -> Optional[ast.expr]:
    """Rewrite ``pkg.mod.Name`` to what the host can spell, or None if it cannot."""
    bound = _import_bound_names(host) | _defined_names(host) if host is not None else set()

    class Reducer(ast.NodeTransformer):
        failed = False

        def visit_Attribute(self, node: ast.Attribute) -> ast.expr:
            head = node
            while isinstance(head, ast.Attribute):
                head = head.value  # type: ignore[assignment]
            if isinstance(head, ast.Name) and head.id in bound:
                return node  # ``typing.Sequence`` with ``import typing`` in the host
            if node.attr in bound:
                return ast.Name(id=node.attr, ctx=ast.Load())
            self.failed = True
            return node

    reducer = Reducer()
    result = reducer.visit(copy.deepcopy(expression))
    return None if reducer.failed else result


def _defined_names(module: ast.Module) -> Set[str]:
    return {
        node.name
        for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


@dataclass(frozen=True)
class ApplySite:
    """A replacement as it stands in the file about to be rewritten."""

    file_path: str
    source: str
    start_line: int
    end_line: int
    indent: str
    statement: ast.stmt
    call: ast.Call


def infer_missing_annotations(
    helper: ast.FunctionDef,
    sites: Sequence[ApplySite],
    host_file: str,
    return_variables: Sequence[str],
    inferrer: TypeInferrer,
) -> ast.FunctionDef:
    """A copy of ``helper`` with bare parameters and return filled from a type inferrer.

    For each bare parameter, every site's argument expression is revealed at
    the start of its block, where the call will stand; a lambda argument is
    left alone. A bare return is revealed from the block's own ``return``
    expressions, or from the returned variables just after the block. Sites
    must agree, and the type must be writable where the helper is defined.
    """
    annotated = copy.deepcopy(helper)
    parameters = annotated.args.posonlyargs + annotated.args.args
    bare = [
        index
        for index, parameter in enumerate(parameters)
        if parameter.annotation is None
        and all(
            index < len(site.call.args) and not isinstance(site.call.args[index], ast.Lambda)
            for site in sites
        )
    ]
    want_return = annotated.returns is None and bool(sites)
    if not bare and not want_return:
        return annotated
    requests: List[RevealRequest] = []
    return_probes: List[Tuple[str, int, int]] = []
    for site in sites:
        if bare:
            requests.append(
                RevealRequest(
                    site.file_path,
                    site.source,
                    site.start_line,
                    site.indent,
                    tuple(ast.unparse(site.call.args[index]) for index in bare),
                )
            )
        if want_return:
            for line, indent, expressions in _return_probes(site, return_variables):
                requests.append(
                    RevealRequest(site.file_path, site.source, line, indent, expressions)
                )
                return_probes.append((site.file_path, line, len(expressions)))
    revealed = inferrer(requests)
    host = next((ast.parse(site.source) for site in sites if site.file_path == host_file), None)
    same_module = all(site.file_path == host_file for site in sites)
    for position, index in enumerate(bare):
        texts = [revealed.get((site.file_path, site.start_line, position)) for site in sites]
        parameters[index].annotation = _agreed_revealed(texts, host, same_module)
    if want_return:
        texts = [
            revealed.get((path, line, offset))
            for path, line, count in return_probes
            for offset in range(count)
        ]
        if return_variables and len(return_variables) > 1:
            annotated.returns = _agreed_tuple(texts, len(return_variables), host, same_module)
        elif texts:
            annotated.returns = _agreed_revealed(texts, host, same_module)
    return annotated


def _return_probes(
    site: ApplySite, return_variables: Sequence[str]
) -> List[Tuple[int, str, Tuple[str, ...]]]:
    """Where to reveal what the helper will return: (line, indent, expressions)."""
    lines = site.source.splitlines(keepends=True)
    if return_variables:
        after = site.end_line + 1
        if after > len(lines):
            return []
        return [(after, site.indent, tuple(return_variables))]
    if not isinstance(site.statement, ast.Return):
        return []
    block_source = "".join(lines[site.start_line - 1 : site.end_line])
    try:
        block = ast.parse(textwrap_dedent(block_source))
    except SyntaxError:
        return []
    probes: List[Tuple[int, str, Tuple[str, ...]]] = []
    for node in ast.walk(block):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        if isinstance(node, ast.Return) and node.value is not None:
            line = site.start_line + node.lineno - 1
            indent = lines[line - 1][: len(lines[line - 1]) - len(lines[line - 1].lstrip())]
            probes.append((line, indent, (ast.unparse(node.value),)))
    return probes


def textwrap_dedent(text: str) -> str:
    import textwrap

    return textwrap.dedent(text)


def _agreed_revealed(
    texts: Sequence[Optional[str]], host: Optional[ast.Module], same_module: bool
) -> Optional[ast.expr]:
    if not texts or any(text is None for text in texts):
        return None
    candidates = [annotation_from_revealed(cast_str(text), host, same_module) for text in texts]
    if any(candidate is None for candidate in candidates):
        return None
    first = candidates[0]
    assert first is not None
    if any(ast.dump(candidate) != ast.dump(first) for candidate in candidates[1:]):  # type: ignore[arg-type]
        return None
    return first


def _agreed_tuple(
    texts: Sequence[Optional[str]], width: int, host: Optional[ast.Module], same_module: bool
) -> Optional[ast.expr]:
    """``tuple[...]`` of the returned variables' types when every site agrees per position."""
    if len(texts) % width:
        return None
    columns = [texts[offset::width] for offset in range(width)]
    elements = [_agreed_revealed(column, host, same_module) for column in columns]
    if any(element is None for element in elements):
        return None
    return ast.Subscript(
        value=ast.Name(id="tuple", ctx=ast.Load()),
        slice=ast.Tuple(elts=[e for e in elements if e is not None], ctx=ast.Load()),
        ctx=ast.Load(),
    )


def cast_str(text: Optional[str]) -> str:
    assert text is not None
    return text


def annotated_names(annotated: ast.FunctionDef) -> List[str]:
    """Parameters that received an annotation, for diagnostics."""
    return [arg.arg for arg in annotated.args.posonlyargs + annotated.args.args if arg.annotation]
