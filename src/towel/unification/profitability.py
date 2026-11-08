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
from typing import Sequence

from .argument_ownership import render_argument_handoff
from .models import Replacement


def _syntax_cost(nodes: Sequence[ast.AST]) -> int:
    """Count readable syntax without punctuation, wrapping or identifier length."""
    return sum(
        isinstance(node, (ast.stmt, ast.expr, ast.arg, ast.keyword, ast.comprehension))
        for tree in nodes
        for node in ast.walk(tree)
    )


def worthwhile_helper(
    helper: ast.FunctionDef,
    replacements: Sequence[Replacement],
    rendered_callers: Sequence[ast.AST] | None = None,
) -> bool:
    """Whether the complete generated syntax is smaller than the repeated work.

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
        return False
    if rendered_callers is not None and len(rendered_callers) != sites:
        raise ValueError("Every replacement needs its rendered caller")
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
    return generated_cost < original_cost
