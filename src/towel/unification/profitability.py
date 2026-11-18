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

"""Require shared logic to repay the complete generated abstraction.

This conservative readability heuristic counts syntax, not physical lines or
identifier lengths. It never relaxes a semantic guard. The helper body estimates
the work originally repeated at every site; a differing caller expression may
make that estimate conservative. Generated signatures, annotations, suppliers,
ownership boxes, deletions and calls all count against the benefit.
"""

from __future__ import annotations

import ast
from collections import Counter
import copy
from typing import Iterator, Literal, Sequence

from ..canonical_ast import canonical_dump
from .argument_ownership import render_argument_handoff
from .models import RejectReason, Replacement
from .parameters import parameter_names
from .statement_facts import mentioned_names


def _syntax_cost(nodes: Sequence[ast.AST]) -> int:
    """Count readable syntax without punctuation, wrapping or identifier length."""
    return sum(
        isinstance(node, (ast.stmt, ast.expr, ast.arg, ast.keyword, ast.comprehension))
        for tree in nodes
        for node in ast.walk(tree)
    )


def _independent_cases(
    cases: Sequence[ast.AST], parameters: set[str], *, signature_uses: Counter[str] | None = None
) -> bool:
    """Compare complete operations, keeping shared inputs and dependencies in place."""
    if len(cases) < 2 or any(type(case) is not type(cases[0]) for case in cases):
        return False
    statement_names = [mentioned_names(case) for case in cases]
    for index, statement in enumerate(cases):
        written = {
            node.id
            for node in ast.walk(statement)
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del))
        }
        if written and any(
            written & used for other, used in enumerate(statement_names) if other != index
        ):
            # A normalization pipeline may repeat its syntax while each step
            # consumes the preceding result. It is one computation, not a
            # bundle of independent test cases.
            return False
    uses = [parameters & used for used in statement_names]
    shared = set.intersection(*uses)
    groups = [used - shared for used in uses]
    if signature_uses is not None:
        if not groups[0] or set.union(*uses) != parameters:
            return False
        # A private input used elsewhere in the helper links its case to other
        # work. Shared context (such as a receiver) may also appear outside.
        inside = _name_counts(cases)
        if any(inside[name] != signature_uses[name] for group in groups for name in group):
            return False
    if any(len(group) != len(groups[0]) for group in groups):
        return False
    if sum(map(len, groups)) != len(set.union(*groups)):
        return False

    shapes: list[str] = []
    for statement, group in zip(cases, groups):
        normalized = copy.deepcopy(statement)
        names: dict[str, str] = {}
        for node in ast.walk(normalized):
            if isinstance(node, ast.Name) and node.id in group:
                # Not a legal identifier: cannot collide with source names.
                node.id = names.setdefault(node.id, f"!case-input-{len(names)}")
            elif isinstance(node, ast.Call):
                # Literal arguments can be case data. Keep the actual operations
                # that produce an argument: x + 1 and x + 2 are different steps.
                for argument in [*node.args, *(kw.value for kw in node.keywords)]:
                    if isinstance(argument, ast.Constant):
                        argument.value = f"!literal-{type(argument.value).__name__}"
        shapes.append(canonical_dump(normalized))
    return len(set(shapes)) == 1


def _name_counts(nodes: Sequence[ast.AST]) -> Counter[str]:
    return Counter(
        node.id for tree in nodes for node in ast.walk(tree) if isinstance(node, ast.Name)
    )


def _nested_suites(statements: Sequence[ast.stmt]) -> Iterator[list[ast.stmt]]:
    for statement in statements:
        for node in ast.walk(statement):
            for _, field in ast.iter_fields(node):
                if isinstance(field, list) and len(field) >= 2 and isinstance(field[0], ast.stmt):
                    yield field


def _bundles_cases(helper: ast.FunctionDef, statements: Sequence[ast.stmt]) -> bool:
    """Refuse a whole repeated operation, or a signature made of copies of one input group."""
    parameters = set(parameter_names(helper.args))
    if _independent_cases(statements, parameters):
        return True
    if len(parameters) < 2:
        return False

    signature_uses = _name_counts(statements)
    return any(
        _repeated_input_groups(suite, parameters, signature_uses)
        for suite in (statements, *_nested_suites(statements))
    )


def _repeated_input_groups(
    suite: Sequence[ast.stmt], parameters: set[str], signature_uses: Counter[str]
) -> bool:
    # A batch can have fixed setup and each case can take several statements.
    # Every parameter must occur in the repeated units. Code that only uses
    # their shared context does not contribute another group.
    uses = [parameters & mentioned_names(stmt) for stmt in suite]
    if len(uses) < 2 or set.union(*uses) != parameters:
        return False
    context = set.intersection(*(used for used in uses if used))
    inputs = [stmt for stmt, used in zip(suite, uses) if used - context]
    for width in range(1, len(inputs) // 2 + 1):
        if len(inputs) % width or len(inputs) // width > len(parameters):
            continue
        cases = [
            ast.Module(body=inputs[start : start + width], type_ignores=[])
            for start in range(0, len(inputs), width)
        ]
        if _independent_cases(cases, parameters, signature_uses=signature_uses):
            return True
    return False


HelperRejection = Literal[RejectReason.UNPROFITABLE_HELPER, RejectReason.REPEATED_STATEMENT_GROUP]


def helper_rejection(
    helper: ast.FunctionDef,
    replacements: Sequence[Replacement],
    rendered_callers: Sequence[ast.AST] | None = None,
) -> HelperRejection | None:
    """Why the complete generated helper would be less useful than its call sites.

    The final helper must already include placement and ownership additions.
    Analysis and materialization supply the same rendered callers publication
    uses, including method dispatch and mixed ownership holders. For an already
    placed plain helper, replacements alone retain the ownership prefix.
    Branches and nested computations contribute their real structure.
    """
    statements = [
        statement
        for statement in helper.body
        if not isinstance(statement, (ast.Global, ast.Nonlocal, ast.Pass))
        and not (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant))
    ]
    sites = len(replacements)
    if sites < 2:
        return RejectReason.UNPROFITABLE_HELPER
    if rendered_callers is not None and len(rendered_callers) != sites:
        raise ValueError("Every replacement needs its rendered caller")
    if _bundles_cases(helper, statements):
        return RejectReason.REPEATED_STATEMENT_GROUP
    callers = (
        list(rendered_callers)
        if rendered_callers is not None
        else [
            (
                render_argument_handoff(replacement.node, replacement.argument_handoff, helper.name)
                if replacement.argument_handoff is not None
                else replacement.node
            )
            for replacement in replacements
        ]
    )
    caller_cost = _syntax_cost(callers)
    original_cost = _syntax_cost(statements) * sites
    generated_cost = _syntax_cost([helper]) + caller_cost
    return RejectReason.UNPROFITABLE_HELPER if generated_cost >= original_cost else None


def worthwhile_helper(
    helper: ast.FunctionDef,
    replacements: Sequence[Replacement],
    rendered_callers: Sequence[ast.AST] | None = None,
) -> bool:
    """Whether the helper passes every usefulness check."""
    return helper_rejection(helper, replacements, rendered_callers) is None
