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

"""Stable field/index paths for nodes within immutable source blocks."""

from __future__ import annotations

import ast
from typing import Dict, List, Sequence, Tuple

# A path names a node inside a block: the statement index, then (field, index)
# steps; ``index`` is -1 for a single-valued field.
Step = Tuple[str, int]
Path = Tuple[int, Tuple[Step, ...]]


def path_map(block: Sequence[ast.AST]) -> Dict[ast.AST, Path]:
    """Node paths in a tree; shared operator/context markers keep their last path.

    Expression occurrences must be unique, as ``has_shared_expressions``
    verifies before unification; parser-shared operator markers are never
    substitution targets.
    """
    paths: Dict[ast.AST, Path] = {}
    for statement_index, statement in enumerate(block):
        stack: List[Tuple[ast.AST, Tuple[Step, ...]]] = [(statement, ())]
        while stack:
            node, steps = stack.pop()
            paths[node] = (statement_index, steps)
            for field, value in ast.iter_fields(node):
                if isinstance(value, ast.AST):
                    stack.append((value, steps + ((field, -1),)))
                elif isinstance(value, list):
                    for index, item in enumerate(value):
                        if isinstance(item, ast.AST):
                            stack.append((item, steps + ((field, index),)))
    return paths


def resolve_path(block: Sequence[ast.AST], path: Path) -> ast.AST:
    """The node at ``path`` in ``block``."""
    statement_index, steps = path
    node: ast.AST = block[statement_index]
    for field, index in steps:
        value = getattr(node, field)
        node = value if index < 0 else value[index]
    return node


def has_shared_expressions(block: Sequence[ast.AST]) -> bool:
    """Whether an expression node occupies more than one occurrence path."""
    seen: set[ast.AST] = set()
    pending = list(block)
    while pending:
        node = pending.pop()
        if isinstance(node, ast.expr):
            if node in seen:
                return True
            seen.add(node)
        pending.extend(ast.iter_child_nodes(node))
    return False
