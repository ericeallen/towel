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

"""Original-source certificates for fresh local outputs inside an IF-only suite."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import FrozenSet, Literal, Optional, Sequence, Set, Tuple, Union

from ..canonical_ast import exact_dump
from .assignment_analyzer import own_scope_bindings
from .ast_paths import Path, path_map, resolve_path
from .definite_assignment import definitely_bound_after
from .models import FunctionNode, source_digest_of
from .parameters import parameter_names

SuiteField = Literal["body", "orelse"]
IfSuiteStep = Tuple[SuiteField, int]


@dataclass(frozen=True)
class IfTransferSite:
    module_digest: str
    function_position: Tuple[int, int]
    block_position: Tuple[int, int, int]


@dataclass(frozen=True)
class IfBindingTransfer:
    """Immutable source locations and binding facts; no AST or owner survives in this value."""

    site: IfTransferSite
    function_path: Path
    suite_path: Tuple[IfSuiteStep, ...]
    suite_field: SuiteField
    start: int
    count: int
    initialized_names: FrozenSet[str]
    normal_exit_bound: FrozenSet[str]
    returned_names: Tuple[str, ...]


def _unique_tree(tree: ast.AST) -> bool:
    """A fresh graph check before local visitors; marker sharing alone is harmless."""
    seen: Set[int] = set()
    pending: list[ast.AST] = [tree]
    markers = (ast.operator, ast.unaryop, ast.boolop, ast.cmpop, ast.expr_context)
    while pending:
        node = pending.pop()
        children = tuple(ast.iter_child_nodes(node))
        if isinstance(node, markers):
            if children:
                return False
            continue
        if id(node) in seen:
            return False
        seen.add(id(node))
        pending.extend(children)
    return True


def _source_corresponds(module: ast.Module, source: str) -> bool:
    if not _unique_tree(module):
        return False
    try:
        actual = exact_dump(module)
        return any(
            actual == exact_dump(ast.parse(source, type_comments=comments))
            for comments in (False, True)
        )
    except (SyntaxError, ValueError, RecursionError):
        return False


def _selected_node_ids(nodes: Sequence[ast.stmt]) -> Optional[FrozenSet[int]]:
    """Only an early refusal: safely scan the selected tree before source proof."""
    forbidden = (
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.ClassDef,
        ast.Lambda,
        ast.Global,
        ast.Nonlocal,
        ast.NamedExpr,
        ast.ListComp,
        ast.SetComp,
        ast.DictComp,
        ast.GeneratorExp,
    )
    markers = (ast.operator, ast.unaryop, ast.boolop, ast.cmpop, ast.expr_context)
    seen: Set[int] = set()
    pending: list[ast.AST] = list(nodes)
    while pending:
        node = pending.pop()
        if isinstance(node, forbidden):
            return None
        if isinstance(node, markers):
            continue
        if id(node) in seen:
            return None
        seen.add(id(node))
        pending.extend(ast.iter_child_nodes(node))
    return frozenset(seen)


def _suite_location(
    statements: Sequence[ast.stmt],
    nodes: Sequence[ast.stmt],
) -> Optional[Tuple[Tuple[IfSuiteStep, ...], SuiteField, int]]:
    """Cheap IF-only eligibility; source ownership is still proved separately."""
    pending: list[Tuple[Sequence[ast.stmt], Tuple[IfSuiteStep, ...], SuiteField]] = [
        (statements, (), "body")
    ]
    seen: Set[int] = set()
    while pending:
        suite, path, field = pending.pop()
        for index, statement in enumerate(suite):
            if id(statement) in seen:
                return None
            seen.add(id(statement))
            if statement is nodes[0]:
                selected = suite[index : index + len(nodes)]
                if (
                    path
                    and len(selected) == len(nodes)
                    and all(a is b for a, b in zip(selected, nodes))
                ):
                    return path, field, index
            if isinstance(statement, ast.If):
                inner = path + ((field, index),)
                pending.append((statement.orelse, inner, "orelse"))
                pending.append((statement.body, inner, "body"))
    return None


def _fresh_flow(
    statements: Sequence[ast.stmt],
    names: FrozenSet[str],
    definite: FrozenSet[str] = frozenset(),
    possible: FrozenSet[str] = frozenset(),
) -> Optional[FrozenSet[str]]:
    """Reject reads before initialization and repeated writes on an IF-only path.

    Definite normal-exit facts come from the existing assignment analysis;
    the possible set only prevents a later write from replacing any earlier
    branch's initialization. Alternative arms start with the same entry facts.
    """
    for statement in statements:
        expression: Optional[ast.expr]
        if isinstance(statement, ast.If):
            expression = statement.test
            targets: Set[str] = set()
        elif isinstance(statement, ast.Assign) and all(
            isinstance(t, ast.Name) for t in statement.targets
        ):
            expression = statement.value
            targets = {t.id for t in statement.targets if isinstance(t, ast.Name)}
        elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            expression = statement.value
            targets = {statement.target.id} if expression is not None else set()
        elif isinstance(statement, ast.Expr):
            expression = statement.value
            targets = set()
        elif isinstance(statement, ast.Pass):
            continue
        else:
            return None
        if (
            targets & possible
            or expression is not None
            and any(
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and node.id in names
                and node.id not in definite
                for node in ast.walk(expression)
            )
        ):
            return None
        if isinstance(statement, ast.If):
            arms = [
                _fresh_flow(arm, names, definite, possible)
                for arm in (statement.body, statement.orelse)
            ]
            if any(arm is None for arm in arms):
                return None
            possible = possible | frozenset().union(*(arm for arm in arms if arm is not None))
        else:
            possible = possible | targets
        after = definitely_bound_after((statement,))
        if after is None:
            return None
        definite = definite | frozenset(after)
    return possible


def certify_if_binding_transfer(
    source: str,
    module: ast.Module,
    function: FunctionNode,
    nodes: Sequence[ast.stmt],
    site: IfTransferSite,
    returned_names: Tuple[str, ...],
) -> Optional[IfBindingTransfer]:
    """Local facts only refuse early; every admission still reproves exact original source."""
    if not nodes:
        return None
    try:
        if not _unique_tree(function):
            return None
        location = _suite_location(function.body, nodes)
        if location is None:
            return None
        selected = _selected_node_ids(nodes)
        if selected is None:
            return None
        if (
            site.module_digest != source_digest_of(source)
            or site.function_position != (function.lineno, function.col_offset)
            or site.block_position != (nodes[0].lineno, nodes[0].col_offset, len(nodes))
        ):
            return None
        names = frozenset(binding.name for binding in own_scope_bindings(nodes))
        if not names or names & frozenset(parameter_names(function.args)):
            return None
        if any(
            binding.name in names and binding.node_id not in selected
            for binding in own_scope_bindings(function.body)
        ):
            return None
        start_position = (nodes[0].lineno, nodes[0].col_offset)
        for node in ast.walk(function):
            if node is not function and isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
            ):
                if any(
                    isinstance(child, ast.Name) and child.id in names for child in ast.walk(node)
                ):
                    return None
            if isinstance(node, (ast.Global, ast.Nonlocal)) and names & set(node.names):
                return None
            if isinstance(node, ast.Name) and node.id in names:
                if (
                    isinstance(node.ctx, ast.Del)
                    or id(node) not in selected
                    and isinstance(node.ctx, ast.Load)
                    and (node.lineno, node.col_offset) < start_position
                ):
                    return None
        exit_bound = definitely_bound_after(nodes)
        if (
            exit_bound is None
            or not names <= exit_bound
            or not set(returned_names) <= names
            or len(set(returned_names)) != len(returned_names)
        ):
            return None
        if _fresh_flow(nodes, names) is None:
            return None
    except (AttributeError, TypeError, ValueError, RecursionError):
        return None
    if not _source_corresponds(module, source):
        return None
    function_path = path_map(module.body).get(function)
    if function_path is None:
        return None
    path, field, index = location
    return IfBindingTransfer(
        site,
        function_path,
        path,
        field,
        index,
        len(nodes),
        names,
        frozenset(exit_bound),
        returned_names,
    )


def verify_if_binding_transfer(
    certificate: IfBindingTransfer, source: str, module: ast.Module
) -> bool:
    """Reprove stored facts on the current source-owned tree, including after reparse."""
    try:
        function = resolve_path(module.body, certificate.function_path)
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return False
        owner: Union[FunctionNode, ast.If] = function
        for field, index in certificate.suite_path:
            if index < 0 or field not in ("body", "orelse"):
                return False
            if field == "body":
                statements = owner.body
            elif isinstance(owner, ast.If):
                statements = owner.orelse
            else:
                return False
            next_owner = statements[index]
            if not isinstance(next_owner, ast.If):
                return False
            owner = next_owner
        if not isinstance(owner, ast.If) or certificate.suite_field not in ("body", "orelse"):
            return False
        suite = owner.body if certificate.suite_field == "body" else owner.orelse
        nodes = suite[certificate.start : certificate.start + certificate.count]
    except (AttributeError, IndexError, TypeError):
        return False
    rebuilt = certify_if_binding_transfer(
        source, module, function, nodes, certificate.site, certificate.returned_names
    )
    return rebuilt == certificate
