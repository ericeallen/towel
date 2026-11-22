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

"""Recognize syntax whose execution or source spelling extraction has not proved.

Parsing and scope collection still traverse these nodes. Declining only the
candidate that contains them keeps unrelated code in the same file usable.
The checks use AST shape rather than interpreter ranges: Python 3.14 adds
TemplateStr/Interpolation, and 3.15 adds is_lazy and unpacking comprehensions.
"""

from __future__ import annotations

import ast
from typing import Iterable, Optional

from .run_syntax import run_syntax


def unsupported_syntax(nodes: Iterable[ast.AST]) -> Optional[str]:
    """A plain refusal sentence for the first unsupported construct, if any."""
    facts = run_syntax.get()
    for statement in nodes:
        refusal = (
            _unsupported_statement(statement)
            if facts is None
            else facts.unsupported.get(statement, _unsupported_statement)
        )
        if refusal is not None:
            return refusal
    return None


def _unsupported_statement(statement: ast.AST) -> Optional[str]:
    for node in ast.walk(statement):
        if type(node).__name__ in {"TemplateStr", "Interpolation"}:
            return "Template strings must keep their interpolation text and evaluation in place."
        if isinstance(node, (ast.Import, ast.ImportFrom)) and getattr(node, "is_lazy", False):
            return "Lazy imports must keep their deferred loading at the original site."
        if (
            isinstance(node, ast.MatchValue)
            and isinstance(node.value, ast.UnaryOp)
            and isinstance(node.value.op, ast.UAdd)
        ):
            return "Unary-plus match patterns have no verified extraction rule yet."
        if (
            isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp))
            and isinstance(node.elt, ast.Starred)
        ) or (isinstance(node, ast.DictComp) and node.value is None):
            return "Unpacking comprehensions must keep their iteration and cleanup in place."
    return None
