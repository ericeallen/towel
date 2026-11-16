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

"""Restore original spellings only when reparsing proves the accepted AST.

Statements keep their source, with changed expressions substituted by span.
Synthetic statements and conflicting comment layouts retain ordinary rendering.
Call arguments come from the caller's source. Indentation never changes the
physical lines inside multiline string tokens.
"""

from __future__ import annotations

import ast
import io
import tokenize
from collections import Counter
from dataclasses import dataclass
from typing import Iterator, Optional, Sequence, Tuple

from ..canonical_ast import canonical_dump


@dataclass(frozen=True)
class Edit:
    start: int
    end: int
    text: str


def _offset(text: str, line: int, column: int) -> int:
    lines = text.splitlines(keepends=True)
    # AST columns count UTF-8 bytes, whereas text offsets count characters.
    characters = len(lines[line - 1].encode("utf-8")[:column].decode("utf-8"))
    return sum(len(part) for part in lines[: line - 1]) + characters


def _bounds(text: str, node: ast.AST) -> Optional[Tuple[int, int]]:
    line = getattr(node, "lineno", None)
    end_line = getattr(node, "end_lineno", None)
    end_column = getattr(node, "end_col_offset", None)
    if line is None or end_line is None or end_column is None:
        return None
    if not 1 <= line <= end_line <= len(text.splitlines()):
        return None
    try:
        start = _offset(text, line, getattr(node, "col_offset", 0))
        end = _offset(text, end_line, end_column)
    except UnicodeDecodeError:
        return None
    return (start, end) if start < end else None


def _edits(text: str, changes: Sequence[Edit]) -> str:
    for edit in sorted(changes, key=lambda edit: edit.start, reverse=True):
        text = text[: edit.start] + edit.text + text[edit.end :]
    return text


def _comments(text: str) -> Tuple[str, ...]:
    try:
        return tuple(
            token.string
            for token in tokenize.generate_tokens(io.StringIO(text).readline)
            if token.type == tokenize.COMMENT
        )
    except (tokenize.TokenError, IndentationError):
        return ()


def _string_lines(text: str) -> set[int]:
    """Lines whose indentation belongs to a multiline literal's value."""
    lines: set[int] = set()
    fstrings: list[int] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.STRING:
                lines.update(range(token.start[0] + 1, token.end[0] + 1))
            elif token.type == getattr(tokenize, "FSTRING_START", -1):
                fstrings.append(token.start[0])
            elif token.type == getattr(tokenize, "FSTRING_END", -1) and fstrings:
                lines.update(range(fstrings.pop() + 1, token.end[0] + 1))
    except (tokenize.TokenError, IndentationError):
        pass
    return lines


def reindent_source(text: str, prefix: str, old_prefix: str = "") -> str:
    """Move code indentation without changing lines inside string tokens."""
    protected = _string_lines(text)
    return "\n".join(
        line if number in protected else prefix + line.removeprefix(old_prefix) if line else ""
        for number, line in enumerate(text.split("\n"), 1)
    )


def indentation_unit(source: str) -> str:
    """The first actual suite's indent, ignoring strings and continuation lines."""
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.INDENT:
                return "\t" if "\t" in token.string else token.string
    except (tokenize.TokenError, IndentationError):
        pass
    return "    "


def indent_rendered(text: str, prefix: str, unit: str = "    ") -> str:
    """Indent a snippet and translate four-space levels to the file's unit."""
    protected = _string_lines(text)
    result: list[str] = []
    for number, line in enumerate(text.split("\n"), 1):
        if number in protected:
            result.append(line)
            continue
        if not line.strip():
            result.append("")
            continue
        spaces = len(line) - len(line.lstrip(" "))
        levels, remainder = divmod(spaces, 4)
        result.append(prefix + unit * levels + " " * remainder + line[spaces:])
    indented = "\n".join(result)
    try:
        parsed = ast.parse("if True:\n" + indented).body[0] if prefix else ast.parse(indented)
        actual: ast.AST
        if prefix:
            assert isinstance(parsed, ast.If)
            actual = ast.Module(body=parsed.body, type_ignores=[])
        else:
            actual = parsed
        if canonical_dump(actual) == canonical_dump(ast.parse(text)):
            return indented
    except (SyntaxError, ValueError):
        pass
    return reindent_source(text, prefix)


def _paired(left: ast.AST, right: ast.AST) -> Iterator[Tuple[ast.AST, ast.AST]]:
    """Corresponding immediate children of structurally equal trees."""
    yield from zip(ast.iter_child_nodes(left), ast.iter_child_nodes(right))


def _checked(text: str, fallback: str) -> str:
    try:
        same = canonical_dump(ast.parse(text)) == canonical_dump(ast.parse(fallback))
        return text if same else fallback
    except (SyntaxError, ValueError):
        return fallback


def preserve_call_source(node: ast.AST, source: str) -> str:
    """Restore every unchanged original expression from this caller's source."""
    rendered = ast.unparse(node)
    if not isinstance(node, (ast.stmt, ast.Module)):
        return rendered
    expected = node if isinstance(node, ast.Module) else ast.Module(body=[node], type_ignores=[])
    changes: list[Edit] = []

    def visit(original: ast.AST, emitted: ast.AST) -> None:
        bounds = _bounds(source, original)
        target = _bounds(rendered, emitted)
        if isinstance(original, ast.expr) and bounds is not None and target is not None:
            spelling = source[bounds[0] : bounds[1]]
            try:
                parsed = ast.parse("(" + spelling + "\n)", mode="eval").body
            except (SyntaxError, ValueError):
                parsed = None
            if parsed is not None and canonical_dump(parsed) == canonical_dump(original):
                if "\n" in spelling:
                    spelling = "(" + spelling + "\n)"
                elif isinstance(original, (ast.Tuple, ast.NamedExpr, ast.Yield)):
                    spelling = "(" + spelling + ")"
                changes.append(Edit(*target, spelling))
                return
        for child, written in _paired(original, emitted):
            visit(child, written)

    visit(expected, ast.parse(rendered))
    return _checked(_edits(rendered, changes), rendered)


def _fstring_quotes(original: str, rendered: str) -> str:
    """Keep a substituted f-string's opening and closing delimiters when safe."""
    old_start = next((i for i, char in enumerate(original) if char in "\"'"), None)
    new_start = next((i for i, char in enumerate(rendered) if char in "\"'"), None)
    if old_start is None or new_start is None:
        return rendered
    old_quote = original[old_start]
    if original.startswith(old_quote * 3, old_start):
        old_quote *= 3
    new_quote = rendered[new_start]
    if rendered.startswith(new_quote * 3, new_start):
        new_quote *= 3
    interior = rendered[new_start + len(new_quote) : -len(new_quote)]
    # Reusing a delimiter inside an expression would require Python 3.12's
    # f-string grammar. Keep the renderer's portable spelling in that case.
    if old_quote in interior:
        return rendered
    candidate = original[:old_start] + old_quote + interior + old_quote
    return _checked(candidate, rendered)


def _expression_edits(original: ast.AST, expected: ast.AST, source: str) -> Optional[list[Edit]]:
    """Substitute expression spans; a change to statement structure declines."""
    if canonical_dump(original) == canonical_dump(expected):
        return []
    if type(original) is type(expected):
        changes: list[Edit] = []
        compatible = True
        for field, value in ast.iter_fields(original):
            other = getattr(expected, field, None)
            pairs: Sequence[Tuple[ast.AST, ast.AST]]
            if isinstance(value, ast.AST) and isinstance(other, ast.AST):
                pairs = [(value, other)]
            elif isinstance(value, list) and isinstance(other, list) and len(value) == len(other):
                if not all(
                    isinstance(a, ast.AST) and isinstance(b, ast.AST) for a, b in zip(value, other)
                ):
                    compatible = value == other
                    if not compatible:
                        break
                    continue
                pairs = list(zip(value, other))
            elif value == other:
                continue
            else:
                compatible = False
                break
            for left, right in pairs:
                edits = _expression_edits(left, right, source)
                if edits is None:
                    compatible = False
                    break
                changes.extend(edits)
            if not compatible:
                break
        if compatible:
            return changes
    if isinstance(original, ast.expr) and isinstance(expected, ast.expr):
        bounds = _bounds(source, original)
        if bounds is not None:
            text = ast.unparse(expected)
            if isinstance(original, ast.JoinedStr) and isinstance(expected, ast.JoinedStr):
                text = _fstring_quotes(source[bounds[0] : bounds[1]], text)
            if not isinstance(
                expected,
                (ast.Name, ast.Constant, ast.Call, ast.Attribute, ast.Subscript, ast.JoinedStr),
            ):
                text = "(" + text + ")"
            return [Edit(*bounds, text)]
    return None


def preserve_helper_source(
    node: ast.AST, rendered: str, source: str, line_range: Tuple[int, int]
) -> str:
    """Copy source statements, substituting expressions and keeping comments."""
    if not isinstance(node, (ast.stmt, ast.Module)):
        return rendered
    expected = node if isinstance(node, ast.Module) else ast.Module(body=[node], type_ignores=[])
    tree = ast.parse(source)
    candidates = [
        statement
        for statement in ast.walk(tree)
        if isinstance(statement, ast.stmt)
        and line_range[0] <= statement.lineno
        and (statement.end_lineno or statement.lineno) <= line_range[1]
    ]
    changes: list[Edit] = []

    def locations(item: ast.AST) -> Counter[Tuple[int, int]]:
        return Counter(
            (child.lineno, child.col_offset)
            for child in ast.walk(item)
            if isinstance(child, ast.expr) and hasattr(child, "lineno")
        )

    def visit(original: ast.AST, emitted: ast.AST) -> None:
        if isinstance(original, ast.stmt) and isinstance(emitted, ast.stmt):
            matching = [candidate for candidate in candidates if type(candidate) is type(original)]
            scores = locations(original)
            matching.sort(
                key=lambda candidate: sum((scores & locations(candidate)).values()), reverse=True
            )
            for candidate in matching:
                if not (scores & locations(candidate)):
                    continue
                edits = _expression_edits(candidate, original, source)
                bounds, target = _bounds(source, candidate), _bounds(rendered, emitted)
                if edits is None or bounds is None or target is None:
                    continue
                text = _edits(source, edits)
                end = bounds[1] + sum(len(edit.text) - (edit.end - edit.start) for edit in edits)
                text = text[bounds[0] : end]
                old_prefix = source.splitlines()[candidate.lineno - 1][: candidate.col_offset]
                new_prefix = " " * emitted.col_offset
                text = reindent_source(old_prefix + text, new_prefix, old_prefix).removeprefix(
                    new_prefix
                )
                if _comments(text) != _comments(rendered[target[0] : target[1]]):
                    continue
                previous = candidate.lineno - 2
                if (
                    candidate.lineno > line_range[0]
                    and previous >= 0
                    and not source.splitlines()[previous].strip()
                ):
                    text = "\n" + new_prefix + text
                changes.append(Edit(*target, text))
                return
        for child, written in _paired(original, emitted):
            visit(child, written)

    visit(expected, ast.parse(rendered))
    return _checked(_edits(rendered, changes), rendered)
