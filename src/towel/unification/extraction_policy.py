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

"""Explicit function exclusions, read from actual signature comments.

Only ``# towel: no-extract`` immediately after the signature's final colon
marks a definition. Its complete original source, including decorators and
nested bodies, is opaque; its line number and surrounding module may change.
"""

from __future__ import annotations

import ast
import io
import tokenize
from dataclasses import dataclass
from typing import List, Optional, Tuple

from ..source_text import source_lines
from .exceptions import RefactoringError

LineRange = Tuple[int, int]


@dataclass(frozen=True)
class ProtectedDefinition:
    """One marked definition's lexical home and exact original text."""

    ancestry: Tuple[str, ...]
    line_range: LineRange
    text: str


def _marked_header_lines(source: str) -> frozenset[int]:
    marked: set[int] = set()
    depth = 0
    previous: Optional[tokenize.TokenInfo] = None
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if (
            token.type == tokenize.COMMENT
            and token.string.strip() == "# towel: no-extract"
            and depth == 0
            and previous is not None
            and previous.type == tokenize.OP
            and previous.string == ":"
            and previous.end[0] == token.start[0]
        ):
            marked.add(token.start[0])
        if token.type == tokenize.OP:
            if token.string in "([{":
                depth += 1
            elif token.string in ")]}":
                depth -= 1
        previous = token
    return frozenset(marked)


def _definition_end(node: ast.FunctionDef | ast.AsyncFunctionDef, lines: List[str]) -> int:
    """Include indented trailing comments; lower-indent comments belong outside the body."""
    end = node.end_lineno or node.lineno
    body_line = lines[node.body[0].lineno - 1]
    whitespace = body_line[: len(body_line) - len(body_line.lstrip(" \t\f"))]
    # A form feed resets Python indentation before subsequent spaces/tabs.
    indent = len(whitespace.rsplit("\f", 1)[-1].expandtabs(8))
    for index in range(end, len(lines)):
        line = lines[index]
        if not line.strip():
            continue
        whitespace = line[: len(line) - len(line.lstrip(" \t\f"))]
        column = len(whitespace.rsplit("\f", 1)[-1].expandtabs(8))
        if not line.lstrip(" \t\f").startswith("#") or column < indent:
            break
        end = index + 1
    return end


def protected_definitions(source: str, tree: ast.AST) -> Tuple[ProtectedDefinition, ...]:
    """Marked functions, preserving discovery order, without mutating the tree.

    A marker in a multiline signature belongs on its closing colon line.
    Strings, standalone comments, decorators and inline-suite comments do not
    mark functions. All functions remain available to binding analysis.
    """
    if "# towel: no-extract" not in source:
        return ()
    marked = _marked_header_lines(source)
    if not marked:
        return ()
    lines = source_lines(source)
    found: List[ProtectedDefinition] = []
    ancestry: List[str] = []

    class Collector(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            ancestry.append(node.name)
            if any(node.lineno <= line < node.body[0].lineno for line in marked):
                start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
                end = _definition_end(node, lines)
                found.append(
                    ProtectedDefinition(
                        tuple(ancestry), (start, end), "".join(lines[start - 1 : end])
                    )
                )
            self.generic_visit(node)
            ancestry.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            ancestry.append(node.name)
            self.generic_visit(node)
            ancestry.pop()

    Collector().visit(tree)
    return tuple(found)


def overlaps_protected(span: LineRange, protected: Tuple[LineRange, ...]) -> bool:
    """Whether a candidate touches a marked definition or any of its nested bodies."""
    return any(span[0] <= end and start <= span[1] for start, end in protected)


def require_protected_definitions_unchanged(before: str, after: str) -> None:
    """Reject loss, movement to another lexical home, insertion or rewriting inside a marker."""
    if "# towel: no-extract" not in before:
        return
    original = protected_definitions(before, ast.parse(before))
    if not original:
        return
    rendered = protected_definitions(after, ast.parse(after))
    # Physical positions can shift when an unrelated helper is inserted above.
    if tuple((d.ancestry, d.text) for d in original) != tuple(
        (d.ancestry, d.text) for d in rendered
    ):
        raise RefactoringError("Refactoring changes a function marked # towel: no-extract")
