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

"""Share one implementation among equal generated module helpers."""

from __future__ import annotations

import ast
import copy
from dataclasses import dataclass
from typing import Mapping

from ..canonical_ast import canonical_dump
from .models import is_generated_helper_name
from .parameters import parameter_names
from .rebinding import rebinding_reason
from .visitors import annotation_expressions
from .extraction_policy import protected_definitions


@dataclass(frozen=True)
class MergedHelpers:
    source: str
    aliases: tuple[tuple[str, str], ...]


def _body_key(function: ast.FunctionDef) -> str:
    """Normalize ordinary local and positional parameter names, never globals."""
    body = ast.Module(body=copy.deepcopy(function.body), type_ignores=[])
    nodes = list(ast.walk(body))
    # Leave scoped bindings and introspection exact. They need a scope-aware
    # equivalence proof, not the ordinary local alpha conversion below.
    if any(
        isinstance(
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
                ast.Global,
                ast.Nonlocal,
                ast.Import,
                ast.ImportFrom,
                ast.ExceptHandler,
                ast.Match,
            ),
        )
        or isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"locals", "vars", "eval", "exec", "globals"}
        or isinstance(node, ast.Attribute)
        and node.attr == "f_locals"
        for node in nodes
    ):
        return canonical_dump(function.args) + ":" + canonical_dump(body)
    parameters = set(parameter_names(function.args))
    positional = {
        argument.arg: f"!parameter-{index}"
        for index, argument in enumerate((*function.args.posonlyargs, *function.args.args))
    }
    local_names = {
        node.id for node in nodes if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    } - parameters
    renames: dict[str, str] = {}
    for node in nodes:
        if isinstance(node, ast.Name):
            if node.id in positional:
                node.id = positional[node.id]
            elif node.id in local_names:
                node.id = renames.setdefault(node.id, f"!local-{len(renames)}")
    return canonical_dump(body)


def _signature_key(function: ast.FunctionDef) -> str:
    arguments = copy.deepcopy(function.args)
    for index, argument in enumerate((*arguments.posonlyargs, *arguments.args)):
        argument.arg = f"!parameter-{index}"
    return canonical_dump(arguments)


def _only_positional_calls(name: str, program: Mapping[str, str | None]) -> bool:
    """Whether a renamed parameter's keyword spelling cannot reach a caller."""
    for source in program.values():
        if source is None:
            return False
        tree = ast.parse(source)
        direct_calls = {
            id(node.func)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and not node.keywords
        }
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Name)
                and node.id == name
                and isinstance(node.ctx, ast.Load)
                or isinstance(node, ast.Attribute)
                and node.attr == name
            ) and id(node) not in direct_calls:
                return False
            if isinstance(node, ast.ImportFrom) and any(
                alias.name in (name, "*") for alias in node.names
            ):
                return False
    return True


def _inert_header(function: ast.FunctionDef) -> bool:
    return not (
        function.decorator_list
        or function.args.defaults
        or any(value is not None for value in function.args.kw_defaults)
        or getattr(function, "type_params", False)
        or function.type_comment
        or any(
            not isinstance(annotation, ast.Constant)
            or not (isinstance(annotation.value, str) or annotation.value is None)
            for annotation in annotation_expressions(function)
        )
    )


def merge_module_helpers(
    path: str, source: str, program: Mapping[str, str | None], *, max_merges: int | None = None
) -> MergedHelpers:
    """Replace later equal helpers with aliases, preserving callers and imports.

    Signatures and annotations match; positional parameter names can differ
    only when the later helper has exclusively direct positional callers.
    Evaluated headers and unstable module bindings are excluded. Generated names
    may alias one another; user-written functions keep independent identities.
    """
    if max_merges is not None and max_merges <= 0:
        return MergedHelpers(source, ())
    tree = ast.parse(source)
    protected = {definition.line_range[0] for definition in protected_definitions(source, tree)}
    first_by_key: dict[tuple[str, str, str], ast.FunctionDef] = {}
    replacements: list[tuple[ast.FunctionDef, str]] = []
    for function in tree.body:
        if not (
            isinstance(function, ast.FunctionDef)
            and is_generated_helper_name(function.name)
            and function.lineno not in protected
            and _inert_header(function)
        ):
            continue
        if rebinding_reason(program, path, function) is not None:
            continue
        key = (
            _signature_key(function),
            canonical_dump(function.returns) if function.returns is not None else "",
            _body_key(function),
        )
        first = first_by_key.setdefault(key, function)
        if first is not function and (
            canonical_dump(function.args) == canonical_dump(first.args)
            or _only_positional_calls(function.name, program)
        ):
            replacements.append((function, first.name))
            if max_merges is not None and len(replacements) >= max_merges:
                break
    lines = source.splitlines(keepends=True)
    for function, target in reversed(replacements):
        newline = "\r\n" if lines[function.lineno - 1].endswith("\r\n") else "\n"
        lines[function.lineno - 1 : function.end_lineno] = [f"{function.name} = {target}{newline}"]
    merged = "".join(lines)
    ast.parse(merged)
    return MergedHelpers(
        merged, tuple((function.name, target) for function, target in replacements)
    )
