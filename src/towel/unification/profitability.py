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

"""Decline tiny straight-line abstractions whose interfaces outweigh sharing.

This is a conservative output-quality policy, not a semantic proof. A helper
with substantial control flow, nested computations, or more than two useful
statements stays subject to the ordinary safety and forwarding gates. Physical
line wrapping contributes no benefit. Final receivers and ownership holders
cost interface slots even when they have no explicit body reads.
"""

from __future__ import annotations

import ast
from typing import Sequence

from .models import Replacement

_COMPLEX_LOGIC = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Try,
    ast.TryStar,
    ast.With,
    ast.AsyncWith,
    ast.Match,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Lambda,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
    ast.IfExp,
    ast.BoolOp,
)


def worthwhile_helper(helper: ast.FunctionDef, replacements: Sequence[Replacement]) -> bool:
    """Whether the final interface repays a tiny straight-line body's sharing.

    Two direct inputs are cheap. Each further formal and each average lambda
    wrapper per site costs one unit. A useful statement saves one unit at
    each site beyond the first. Only bodies of at most two simple statements
    use this narrow calibration; branching and nested scope logic are exempt.
    The argument list must already include placement and ownership additions.
    """
    statements = [
        statement
        for statement in helper.body
        if not isinstance(statement, (ast.Global, ast.Nonlocal, ast.Pass))
        and not (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant))
    ]
    if len(statements) > 2 or any(
        isinstance(node, _COMPLEX_LOGIC) for statement in statements for node in ast.walk(statement)
    ):
        return True
    sites = len(replacements)
    if sites < 2:
        return False
    args = helper.args
    formals = (
        len(args.posonlyargs)
        + len(args.args)
        + len(args.kwonlyargs)
        + int(args.vararg is not None)
        + int(args.kwarg is not None)
    )
    wrappers = sum(
        isinstance(node, ast.Lambda)
        for replacement in replacements
        for node in ast.walk(replacement.node)
    )
    interface_cost = max(0, formals - 2) + (wrappers + sites - 1) // sites
    shared_work = len(statements) * (sites - 1)
    return interface_cost <= shared_work
