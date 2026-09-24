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

"""The comments of a moved block, and where they stand in its helper.

A helper is rendered from its syntax tree, and a syntax tree holds no
comments, so each comment of a duplicate is read from the source tokens
(``site_comments``) and recorded against the code it is written beside: the
node it follows or precedes, named by a path of typed steps from the
block's statements (``NodePath``), the punctuation between them, and the
brackets around it. The helper's body is the first site's block with
parameters substituted, so a path names the same code in the helper, down
to the node a parameter took the place of.

Rendering (``weave_comments``) writes each comment into the unparsed helper
at that place, before the project's formatter runs: at the end of the line
holding its code, inside the brackets it stood in, or on a line of its own
before or after its statement. Where the source had them, the grouping
parentheses around the comment and the trailing comma that kept a bracket
exploded are written too, so Black and ruff lay the code out around the
comment as the source had it. After formatting, every tool directive must
still stand on the line of the code it was written beside; a formatter that
moved one is not used for that helper (``WovenHelper.keeps_directives``).

Which comments the helper carries is decided from every site's
(``merge_comments``). An explanatory comment is kept wherever any site
carries it: it documents code the helper now holds, and one site's words
are not lost for another's silence. A tool directive (``is_directive``)
changes what a tool reports for its line, and the helper has one line where
the sites had several, so the sites must agree: every site must carry the
same directives at the same places (``DIRECTIVES_DIFFER`` otherwise), a
checker's ignore must not stand on a line where a site's code becomes an
argument of the call, which the ignore would no longer cover
(``DIRECTIVE_ON_ARGUMENT``), and a directive whose reach is a region or a
file must not reach past the moved code (``DIRECTIVE_OUTLIVES_BLOCK``).
"""

from __future__ import annotations

import ast
import bisect
import functools
import io
import re
import tokenize
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple, Union

from .exceptions import RefactoringError
from .substitution import Substitution
from ..source_text import source_lines

Position = Tuple[int, int]
"""A (line, column) place in a text; the line counts from 1 and the column in characters."""

Step = Tuple[str, int, str]
"""One step down a tree: the field, the index in it (-1 for a single node), and the node type reached."""


@dataclass(frozen=True)
class NodePath:
    """A node of a block: the statement it is in, and the typed steps down to it."""

    statement: int
    kind: str
    steps: Tuple[Step, ...] = ()

    def child(self, field_name: str, index: int, kind: str) -> NodePath:
        return NodePath(self.statement, self.kind, self.steps + ((field_name, index, kind),))

    def prefix(self, length: int) -> NodePath:
        return NodePath(self.statement, self.kind, self.steps[:length])


class Placement(Enum):
    """How a comment stands beside the node of its anchor."""

    TRAILING = "trailing"
    """At the end of the line, after the node and the punctuation that followed it."""
    FIRST_LINE = "first_line"
    """At the end of the statement's first line: its line held nothing of the block before it."""
    HEADER = "header"
    """At the end of the ``else:``, ``try:``, ``finally:`` or ``except:`` line opening the node's clause."""
    LEADING = "leading"
    """On a line of its own, before the node."""
    CLOSING = "closing"
    """On a line of its own, after the node and its punctuation."""
    ABOVE_HEADER = "above_header"
    """On a line of its own, before the header line of the node's clause."""


_OWN_LINE = frozenset({Placement.LEADING, Placement.CLOSING, Placement.ABOVE_HEADER})


@dataclass(frozen=True)
class Anchor:
    """Where a comment stands in its block's code."""

    node: NodePath
    placement: Placement
    punctuation: Tuple[str, ...] = ()
    # The expression that the innermost parentheses around the comment
    # enclosed, when they were grouping parentheses, which unparsing drops.
    group: Optional[NodePath] = None
    # The call, list, tuple, set or dict whose brackets enclosed the comment,
    # when a trailing comma held them exploded, which unparsing drops too.
    exploded: Optional[NodePath] = None

    @property
    def own_line(self) -> bool:
        return self.placement in _OWN_LINE


@dataclass(frozen=True)
class BlockComment:
    """One comment of a duplicate block, as written, with its anchor and its line there."""

    text: str
    anchor: Anchor
    line: int
    # For a comment at the end of a line, the first node that line started:
    # with the anchor, the span of code the comment was written beside.
    line_start: Optional[NodePath] = None


@dataclass(frozen=True)
class SiteComments:
    """The comments of one call site's block, and the lines where its code becomes an argument."""

    comments: Tuple[BlockComment, ...] = ()
    # Lines holding an expression of this block that its call passes as an
    # argument (or inside a lambda) and that is neither a name nor a literal.
    argument_lines: FrozenSet[int] = frozenset()
    # The block's tokens could not be read, so neither could its comments.
    unreadable: bool = False


@dataclass(frozen=True)
class HelperComment:
    """A comment the helper carries, anchored in the helper's body."""

    text: str
    anchor: Anchor
    directive: bool = False
    line_start: Optional[NodePath] = None


@dataclass(frozen=True)
class HelperComments:
    """The comments a helper carries; paths count statements from ``body_offset``."""

    body_offset: int = 0
    comments: Tuple[HelperComment, ...] = ()


class ConflictKind(Enum):
    """Why the sites' comments cannot all move into one helper."""

    DIRECTIVES_DIFFER = "directives_differ"
    DIRECTIVE_ON_ARGUMENT = "directive_on_argument"
    DIRECTIVE_OUTLIVES_BLOCK = "directive_outlives_block"


@dataclass(frozen=True)
class CommentConflict:
    """A reason the sites' comments decline the pair, with the comment it concerns."""

    kind: ConflictKind
    detail: str


class CommentPlacementError(RefactoringError):
    """The helper's comments could not be written into its rendered text."""


# -- Directives ----------------------------------------------------------------

_DIRECTIVE = re.compile(
    r"""\#\s*(?:
        type\s*:\s*\S                       # PEP 484 type comments and ignores
      | pyright\s*:\s*\S | mypy\s*:\s*\S | pytype\s*:\s*\S
      | pyre-(?:ignore|fixme|strict|unsafe|ignore-all-errors)\b
      | noqa\b | flake8[:=\s]\s*noqa\b | ruff\s*:\s*\S
      | pragma\b | nosec\b | pylint\s*:\s*\S | noinspection\b
      | fmt\s*:\s*(?:off|on|skip)\b | yapf\s*:\s*(?:disable|enable)\b
      | isort\s*:\s*\S | autopep8\s*:\s*(?:off|on)\b
      | pycln\s*:\s*\S | nopycln\b | codespell\s*:\s*ignore\b
    )""",
    re.IGNORECASE | re.VERBOSE,
)

_SILENCES_CHECKER = re.compile(
    r"\#\s*(?:type\s*:\s*ignore\b|pyright\s*:\s*ignore\b|pyre-(?:ignore|fixme)\b"
    r"|pytype\s*:\s*disable\b)",
    re.IGNORECASE,
)

_FILE_DIRECTIVE = re.compile(
    r"\#\s*(?:flake8[:=\s]\s*noqa\b|ruff\s*:\s*noqa\b|mypy\s*:|pyright\s*:\s*(?!ignore\b)\w"
    r"|isort\s*:\s*skip_file\b|pylint\s*:\s*skip-file\b|pytype\s*:\s*skip-file\b"
    r"|pyre-(?:strict|unsafe|ignore-all-errors)\b)",
    re.IGNORECASE,
)

# A region directive on a line of its own opens or closes a region that
# reaches the matching directive, or the end of the enclosing block.
_REGIONS = (
    ("fmt", re.compile(r"\#\s*fmt\s*:\s*off\b", re.I), re.compile(r"\#\s*fmt\s*:\s*on\b", re.I)),
    (
        "yapf",
        re.compile(r"\#\s*yapf\s*:\s*disable\b", re.I),
        re.compile(r"\#\s*yapf\s*:\s*enable\b", re.I),
    ),
    (
        "isort",
        re.compile(r"\#\s*isort\s*:\s*off\b", re.I),
        re.compile(r"\#\s*isort\s*:\s*on\b", re.I),
    ),
    (
        "autopep8",
        re.compile(r"\#\s*autopep8\s*:\s*off\b", re.I),
        re.compile(r"\#\s*autopep8\s*:\s*on\b", re.I),
    ),
    (
        "pylint",
        re.compile(r"\#\s*pylint\s*:\s*disable\b", re.I),
        re.compile(r"\#\s*pylint\s*:\s*enable\b", re.I),
    ),
)


def is_directive(text: str) -> bool:
    """Whether the comment ``text`` tells a tool something: an ignore, a pragma, a type.

    Type checkers (``type: ignore``, a PEP 484 type comment, ``pyright:``,
    ``mypy:``, pyre, pytype), linters (``noqa``, ``ruff:``, ``pylint:``,
    ``nosec``, PyCharm's ``noinspection``), coverage (``pragma``), and
    formatters and import sorters (``fmt:``, ``yapf:``, ``isort:``,
    ``autopep8:``, pycln, codespell). Any ``#`` segment of the comment may
    hold it, as in ``# type: ignore  # noqa: E721``.
    """
    return _DIRECTIVE.search(text) is not None


def silences_checker(text: str) -> bool:
    """Whether the comment ``text`` silences a type checker for its line."""
    return _SILENCES_CHECKER.search(text) is not None


def is_file_directive(text: str) -> bool:
    """Whether the comment ``text`` configures a tool for its whole file, wherever it stands."""
    return _FILE_DIRECTIVE.search(text) is not None


def _normalized(text: str) -> str:
    """A directive as its tools read it: without spacing, which none of them weighs."""
    return re.sub(r"\s+", "", text)


# -- Tokens --------------------------------------------------------------------

_INSIGNIFICANT = frozenset(
    {
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENCODING,
        tokenize.ENDMARKER,
    }
)
_FSTRING_START = getattr(tokenize, "FSTRING_START", None)
_FSTRING_END = getattr(tokenize, "FSTRING_END", None)
_OPENING = frozenset("([{")
_CLOSING = frozenset(")]}")
_CLAUSE_KEYWORDS = frozenset({"else", "elif", "except", "finally", "case"})
_HEADER_KEYWORDS = frozenset({"else", "try", "finally", "except"})


@dataclass(frozen=True)
class _Token:
    kind: int
    text: str
    start: Position
    end: Position


def _tokens(text: str) -> List[_Token]:
    """The significant tokens and the comments of ``text``, in order; raises on bad input."""
    return [
        _Token(token.type, token.string, token.start, token.end)
        for token in tokenize.generate_tokens(io.StringIO(text).readline)
        if token.type not in _INSIGNIFICANT
    ]


def _block_tokens(lines: Sequence[str], first: Position, last_line: int) -> Optional[List[_Token]]:
    """The tokens of the block from ``first`` to the end of ``last_line``, or None when unreadable.

    The block's lines are tokenized on their own, under a header that makes
    their indentation legal, since a block of a function body is indented;
    the whole text is the fallback for a block the header does not fit.
    """
    fragment = "if 1:\n" + "".join(lines[first[0] - 1 : last_line])
    shift = first[0] - 2
    try:
        shifted = [
            _Token(
                token.kind,
                token.text,
                (token.start[0] + shift, token.start[1]),
                (token.end[0] + shift, token.end[1]),
            )
            for token in _tokens(fragment)
            if token.start[0] > 1
        ]
    except (tokenize.TokenError, SyntaxError):
        try:
            shifted = [
                token
                for token in _tokens("".join(lines))
                if first[0] <= token.start[0] <= last_line
            ]
        except (tokenize.TokenError, SyntaxError):
            return None
    return [token for token in shifted if token.start >= first]


@functools.lru_cache(maxsize=8)
def _lines_of(source: str) -> Tuple[str, ...]:
    """``source`` split as the tokenizer counts lines, once per module text.

    Every generated call reads its block's comments from its module, and a
    module's pairs number in the thousands.
    """
    return tuple(source_lines(source))


def _column(line: str, byte_offset: int) -> int:
    """The character column of an AST column, which counts UTF-8 bytes."""
    if line.isascii():
        return byte_offset
    return len(line.encode("utf-8")[:byte_offset].decode("utf-8", errors="replace"))


# -- Reading a block's comments ------------------------------------------------


@dataclass(frozen=True)
class _Located:
    path: NodePath
    depth: int
    start: Position
    end: Position
    node: ast.AST


def _statement_like(node: ast.AST) -> bool:
    return isinstance(node, (ast.stmt, ast.ExceptHandler))


def _children(node: ast.AST) -> Iterable[Tuple[str, int, ast.AST]]:
    for field_name, value in ast.iter_fields(node):
        if isinstance(value, ast.AST):
            yield field_name, -1, value
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, ast.AST):
                    yield field_name, index, item


class _BlockIndex:
    """Every located node of a block, by where it starts and ends in the source."""

    def __init__(self, block: Sequence[ast.stmt], lines: Sequence[str]) -> None:
        located: List[_Located] = []
        pending: List[Tuple[ast.AST, NodePath, int]] = [
            (statement, NodePath(index, type(statement).__name__), 0)
            for index, statement in enumerate(block)
        ]
        while pending:
            node, path, depth = pending.pop()
            lineno = getattr(node, "lineno", None)
            end_lineno = getattr(node, "end_lineno", None)
            col = getattr(node, "col_offset", None)
            end_col = getattr(node, "end_col_offset", None)
            if lineno is not None and end_lineno is not None and col is not None:
                if end_col is not None:
                    located.append(
                        _Located(
                            path,
                            depth,
                            (lineno, _column(lines[lineno - 1], col)),
                            (end_lineno, _column(lines[end_lineno - 1], end_col)),
                            node,
                        )
                    )
            # The parts of an f-string are placed unreliably before Python
            # 3.12, and no comment can stand inside one before it.
            if isinstance(node, ast.JoinedStr):
                continue
            for field_name, index, child in _children(node):
                pending.append(
                    (child, path.child(field_name, index, type(child).__name__), depth + 1)
                )
        self._by_start: Dict[Position, _Located] = {}
        self._by_end: Dict[Position, _Located] = {}
        self._innermost_by_end: Dict[Position, _Located] = {}
        for entry in located:
            if entry.start not in self._by_start or entry.depth < self._by_start[entry.start].depth:
                self._by_start[entry.start] = entry
            if entry.end not in self._by_end or entry.depth < self._by_end[entry.end].depth:
                self._by_end[entry.end] = entry
            innermost = self._innermost_by_end.get(entry.end)
            if innermost is None or entry.depth > innermost.depth:
                self._innermost_by_end[entry.end] = entry
        self._ends = sorted(self._by_end)
        self._starts_by_line: Dict[int, List[_Located]] = {}
        for entry in sorted(self._by_start.values(), key=lambda item: item.start):
            self._starts_by_line.setdefault(entry.start[0], []).append(entry)
        self._statements = [entry for entry in located if _statement_like(entry.node)]

    def starting_at(self, position: Position) -> Optional[_Located]:
        """The outermost node starting at ``position``."""
        return self._by_start.get(position)

    def innermost_ending_at(self, position: Position) -> Optional[_Located]:
        """The most deeply nested node ending at ``position``: the one a closing bracket there ends."""
        return self._innermost_by_end.get(position)

    def ending_at_or_before(self, position: Position) -> Optional[_Located]:
        """The outermost of the nodes ending last at or before ``position``."""
        index = bisect.bisect_right(self._ends, position) - 1
        return self._by_end[self._ends[index]] if index >= 0 else None

    def first_starting_on(self, line: int, before: int) -> Optional[_Located]:
        """The outermost node starting first on ``line``, before column ``before``."""
        for entry in self._starts_by_line.get(line, ()):
            if entry.start[1] < before:
                return entry
        return None

    def statement_closed_before(self, position: Position, column: int) -> Optional[_Located]:
        """The statement a comment at ``position`` indented to ``column`` closes, if it is deeper.

        Of the statements ending last before the comment (a statement and the
        compound statements it ends), the one indented furthest but not past
        the comment: the comment trails that statement's body.
        """
        ended = [entry for entry in self._statements if entry.end <= position]
        if not ended:
            return None
        last = max(entry.end for entry in ended)
        candidates = [entry for entry in ended if entry.end == last and entry.start[1] <= column]
        return max(candidates, key=lambda entry: entry.start[1]) if candidates else None

    def innermost_statement_on(self, line: int) -> Optional[_Located]:
        """The most deeply nested statement whose lines include ``line``."""
        holding = [entry for entry in self._statements if entry.start[0] <= line <= entry.end[0]]
        return max(holding, key=lambda entry: entry.depth) if holding else None

    def statement_starting_after(self, position: Position) -> Optional[_Located]:
        """The first statement starting after ``position``."""
        later = [entry for entry in self._statements if entry.start > position]
        return min(later, key=lambda entry: (entry.start, entry.depth)) if later else None

    def spanning(self, start: Position, end: Position) -> Optional[_Located]:
        """The outermost node starting at ``start`` and ending at ``end``."""
        entry = self._by_start.get(start)
        found: Optional[_Located] = None
        for candidate in (entry, self._by_end.get(end)):
            if candidate is not None and candidate.start == start and candidate.end == end:
                if found is None or candidate.depth < found.depth:
                    found = candidate
        return found


_EXPLODABLE = (ast.Call, ast.List, ast.Tuple, ast.Set, ast.Dict)


def _bracket_context(
    tokens: Sequence[_Token], at: int, index: _BlockIndex
) -> Tuple[Optional[NodePath], Optional[NodePath]]:
    """The grouping and the exploded node of the innermost brackets around token ``at``."""
    stack: List[int] = []
    for position in range(at):
        text = tokens[position].text
        if tokens[position].kind == tokenize.OP and text in _OPENING:
            stack.append(position)
        elif tokens[position].kind == tokenize.OP and text in _CLOSING and stack:
            stack.pop()
    if not stack:
        return None, None
    opening = stack[-1]
    depth = 0
    closing: Optional[int] = None
    for position in range(opening, len(tokens)):
        token = tokens[position]
        if token.kind != tokenize.OP:
            continue
        if token.text in _OPENING:
            depth += 1
        elif token.text in _CLOSING:
            depth -= 1
            if depth == 0:
                closing = position
                break
    if closing is None:
        return None, None
    inner = [token for token in tokens[opening + 1 : closing] if token.kind != tokenize.COMMENT]
    group: Optional[NodePath] = None
    if tokens[opening].text == "(" and inner:
        spanned = index.spanning(inner[0].start, inner[-1].end)
        if spanned is not None:
            group = spanned.path
    exploded: Optional[NodePath] = None
    if inner and inner[-1].text == ",":
        owner = index.innermost_ending_at(tokens[closing].end)
        if owner is not None and isinstance(owner.node, _EXPLODABLE):
            exploded = owner.path
    return group, exploded


def _between(tokens: Sequence[_Token], start: Position, end: Position) -> Tuple[str, ...]:
    return tuple(
        token.text
        for token in tokens
        if token.kind != tokenize.COMMENT and token.start >= start and token.end <= end
    )


def _trailing_anchor(
    tokens: Sequence[_Token],
    at: int,
    index: _BlockIndex,
    first_on_line: Optional[_Token],
    last: NodePath,
) -> Anchor:
    comment = tokens[at]
    line = comment.start[0]
    before = index.ending_at_or_before(comment.start)
    if before is not None and before.end[0] == line:
        return Anchor(before.path, Placement.TRAILING, _between(tokens, before.end, comment.start))
    if first_on_line is not None and first_on_line.text in _HEADER_KEYWORDS:
        opened = index.statement_starting_after(comment.start)
        if opened is not None:
            return Anchor(opened.path, Placement.HEADER)
    holder = index.innermost_statement_on(line) or index.statement_starting_after(comment.start)
    return Anchor(holder.path if holder is not None else last, Placement.FIRST_LINE)


def _own_line_anchor(
    tokens: Sequence[_Token], at: int, index: _BlockIndex, last: NodePath
) -> Anchor:
    comment = tokens[at]
    following = next((token for token in tokens[at + 1 :] if token.kind != tokenize.COMMENT), None)
    if following is None:
        closed = index.statement_closed_before(comment.start, comment.start[1])
        return Anchor(closed.path if closed is not None else last, Placement.CLOSING)
    starting = index.starting_at(following.start)
    clause = following.text in _CLAUSE_KEYWORDS and following.kind == tokenize.NAME
    if (starting is not None and _statement_like(starting.node)) or (clause and starting is None):
        if comment.start[1] > following.start[1]:
            # Indented past what follows: the comment trails the body it is in.
            closed = index.statement_closed_before(comment.start, comment.start[1])
            if closed is not None:
                return Anchor(closed.path, Placement.CLOSING)
        if starting is not None:
            return Anchor(starting.path, Placement.LEADING)
        opened = index.statement_starting_after(following.start)
        if opened is not None:
            return Anchor(opened.path, Placement.ABOVE_HEADER)
    if starting is not None:
        return Anchor(starting.path, Placement.LEADING)
    before = index.ending_at_or_before(comment.start)
    if before is not None:
        return Anchor(before.path, Placement.CLOSING, _between(tokens, before.end, comment.start))
    holder = index.statement_starting_after(comment.start)
    return Anchor(holder.path if holder is not None else last, Placement.LEADING)


def site_comments(
    source: str, block: Sequence[ast.stmt], argument_lines: Iterable[int] = ()
) -> SiteComments:
    """The comments written between the first and last line of ``block``, anchored in it.

    ``source`` is the module ``block`` was parsed from. Comments above the
    block's first statement or below its last line belong to the call site
    and stay there. A block whose tokens cannot be read is marked so, and
    moves nowhere (``directive_conflict``).
    """
    arguments = frozenset(argument_lines)
    if not block:
        return SiteComments(argument_lines=arguments)
    lines = _lines_of(source)
    last_line = block[-1].end_lineno or block[-1].lineno
    if not any("#" in line for line in lines[block[0].lineno - 1 : last_line]):
        return SiteComments(argument_lines=arguments)
    first = (block[0].lineno, _column(lines[block[0].lineno - 1], block[0].col_offset))
    tokens = _block_tokens(lines, first, last_line)
    if tokens is None:
        return SiteComments(argument_lines=arguments, unreadable=True)
    positions = [
        position for position, token in enumerate(tokens) if token.kind == tokenize.COMMENT
    ]
    if not positions:
        return SiteComments(argument_lines=arguments)
    index = _BlockIndex(block, lines)
    last = NodePath(len(block) - 1, type(block[-1]).__name__)
    first_on_line: Dict[int, _Token] = {}
    for token in tokens:
        if token.kind != tokenize.COMMENT:
            first_on_line.setdefault(token.start[0], token)
    comments: List[BlockComment] = []
    for at in positions:
        comment = tokens[at]
        line = comment.start[0]
        if not lines[line - 1][: comment.start[1]].strip():
            anchor = _own_line_anchor(tokens, at, index, last)
            line_start = None
        else:
            anchor = _trailing_anchor(tokens, at, index, first_on_line.get(line), last)
            starter = index.first_starting_on(line, comment.start[1])
            line_start = starter.path if starter is not None else None
        group, exploded = _bracket_context(tokens, at, index)
        comments.append(
            BlockComment(
                comment.text.rstrip(),
                Anchor(anchor.node, anchor.placement, anchor.punctuation, group, exploded),
                line,
                line_start,
            )
        )
    return SiteComments(tuple(comments), arguments)


def _moves_to_the_call(expression: ast.AST) -> bool:
    """Whether a site's argument is code a directive could have covered: not a name or literal."""
    if isinstance(expression, (ast.Name, ast.Constant)):
        return False
    if isinstance(expression, ast.UnaryOp) and isinstance(expression.operand, ast.Constant):
        return False
    return True


def argument_lines(expressions: Iterable[ast.AST]) -> FrozenSet[int]:
    """The lines of the site's expressions its call passes, where they are code, not a name."""
    lines: set[int] = set()
    for expression in expressions:
        lineno = getattr(expression, "lineno", None)
        if lineno is None or not _moves_to_the_call(expression):
            continue
        lines.update(range(lineno, (getattr(expression, "end_lineno", None) or lineno) + 1))
    return frozenset(lines)


def call_argument_lines(substitution: Substitution, block_index: int) -> FrozenSet[int]:
    """The lines where block ``block_index``'s own code becomes an argument of its call.

    Each parameter the unifier introduced is passed that block's expression,
    as it is or inside a lambda, and a promoted literal its original one;
    both are written at the call site from then on.
    """
    expressions: List[ast.AST] = [
        expression
        for pairs in substitution.param_expressions.values()
        for index, expression in pairs
        if index == block_index
    ]
    expressions.extend(
        by_block[block_index]
        for by_block in substitution.promoted_literal_args.values()
        if block_index in by_block
    )
    return argument_lines(expressions)


# -- Deciding the helper's comments --------------------------------------------


def _child(node: ast.AST, field_name: str, index: int) -> Optional[ast.AST]:
    value = getattr(node, field_name, None)
    if index < 0:
        return value if isinstance(value, ast.AST) else None
    if isinstance(value, list) and index < len(value) and isinstance(value[index], ast.AST):
        item: ast.AST = value[index]
        return item
    return None


def _resolve(statements: Sequence[ast.AST], path: NodePath) -> Tuple[ast.AST, NodePath, bool]:
    """The node of ``statements`` at ``path``, its path there, and whether it stands where ``path`` does.

    Where the helper holds a parameter instead of the site's code, the path
    ends at the parameter, which stands in that code's place: exactly where
    the anchor was when it is the anchor that the parameter replaced, and
    only near it when the anchor was inside the replaced code. A path also
    ends early where an annotation variant respelled a subtree.
    """
    if path.statement >= len(statements):
        raise CommentPlacementError(
            f"The helper's body has no statement {path.statement} for a comment"
        )
    node = statements[path.statement]
    if type(node).__name__ != path.kind:
        return node, NodePath(path.statement, type(node).__name__), False
    for depth, (field_name, index, kind) in enumerate(path.steps):
        child = _child(node, field_name, index)
        if child is None:
            return node, path.prefix(depth), False
        if type(child).__name__ != kind:
            replaced = path.prefix(depth).child(field_name, index, type(child).__name__)
            return child, replaced, depth == len(path.steps) - 1
        node = child
    return node, path, True


def _projected(statements: Sequence[ast.AST], anchor: Anchor) -> Tuple[Anchor, bool]:
    """``anchor`` as it lands in ``statements``, and whether its node is there whole."""
    _, node, exact = _resolve(statements, anchor.node)
    group = _resolve(statements, anchor.group)[1] if anchor.group is not None else None
    exploded: Optional[NodePath] = None
    if anchor.exploded is not None and _resolve(statements, anchor.exploded)[2]:
        exploded = anchor.exploded
    punctuation = anchor.punctuation if exact else ()
    return Anchor(node, anchor.placement, punctuation, group, exploded), exact


_Place = Tuple[NodePath, Placement, Tuple[str, ...]]
"""Where a comment lands in the helper, whatever the brackets around it."""


def _place(anchor: Anchor) -> _Place:
    return anchor.node, anchor.placement, anchor.punctuation


def _directives(
    statements: Sequence[ast.AST], site: SiteComments
) -> Dict[Tuple[_Place, str], List[BlockComment]]:
    """The site's directives by where they land in the helper and how their tools read them."""
    found: Dict[Tuple[_Place, str], List[BlockComment]] = {}
    for comment in site.comments:
        if is_directive(comment.text):
            anchor, _ = _projected(statements, comment.anchor)
            found.setdefault((_place(anchor), _normalized(comment.text)), []).append(comment)
    return found


def _region_imbalance(site: SiteComments) -> Optional[BlockComment]:
    """The first region directive of ``site`` whose region crosses the block's edge."""
    opened: Dict[str, BlockComment] = {}
    for comment in site.comments:
        if not comment.anchor.own_line:
            continue
        for tool, opens, closes in _REGIONS:
            if opens.search(comment.text):
                opened.setdefault(tool, comment)
            elif closes.search(comment.text):
                if tool not in opened:
                    return comment
                del opened[tool]
    return min(opened.values(), key=lambda comment: comment.line) if opened else None


def directive_conflict(
    statements: Sequence[ast.AST], sites: Sequence[SiteComments]
) -> Optional[CommentConflict]:
    """Why the sites' tool directives cannot move into the helper's ``statements``, if they cannot.

    Every site must carry the same directives, written alike up to spacing,
    at the same places; a checker's ignore must not stand on a line where a
    site's code becomes an argument; and a region a directive opens or
    closes must not reach past the block.
    """
    for site in sites:
        if site.unreadable:
            return CommentConflict(
                ConflictKind.DIRECTIVES_DIFFER, "a site's comments cannot be tokenized"
            )
    if sites:
        reference = _directives(statements, sites[0])
        for site in sites[1:]:
            found = _directives(statements, site)
            for key in sorted(
                set(reference) | set(found), key=lambda key: (key[1], key[0][1].value)
            ):
                ours, theirs = reference.get(key, []), found.get(key, [])
                if len(ours) != len(theirs):
                    example = (ours or theirs)[0]
                    return CommentConflict(
                        ConflictKind.DIRECTIVES_DIFFER,
                        f"{example.text} (line {example.line}) is not at every site",
                    )
    for site in sites:
        for comment in site.comments:
            if (
                not comment.anchor.own_line
                and silences_checker(comment.text)
                and comment.line in site.argument_lines
            ):
                return CommentConflict(
                    ConflictKind.DIRECTIVE_ON_ARGUMENT,
                    f"line {comment.line}: {comment.text}",
                )
        crossing = _region_imbalance(site)
        if crossing is not None:
            return CommentConflict(
                ConflictKind.DIRECTIVE_OUTLIVES_BLOCK, f"line {crossing.line}: {crossing.text}"
            )
    return None


def merge_comments(
    statements: Sequence[ast.AST],
    sites: Sequence[Tuple[SiteComments, bool]],
    body_offset: int,
) -> Union[HelperComments, CommentConflict]:
    """The comments the helper carries from every site, or why the sites decline it.

    ``statements`` is the helper's body from ``body_offset`` on, and each
    site comes with whether it is in the helper's module. The directives
    must agree (``directive_conflict``) and a file-wide one must stay in its
    module; the first site's are carried. Every other comment is carried
    from every site that has it: the first site's in its order, then each
    other site's that the ones before did not already carry at that place,
    on a line of its own before that code where one of theirs ends its line.
    """
    conflict = directive_conflict(statements, [site for site, _ in sites])
    if conflict is not None:
        return conflict
    for site, same_module in sites:
        if same_module:
            continue
        for comment in site.comments:
            if is_file_directive(comment.text):
                return CommentConflict(
                    ConflictKind.DIRECTIVE_OUTLIVES_BLOCK,
                    f"line {comment.line}: {comment.text} would configure another module",
                )
    by_place: Dict[_Place, List[HelperComment]] = {}
    for position, (site, _) in enumerate(sites):
        carried: Dict[_Place, Counter[str]] = {
            place: Counter(comment.text for comment in comments)
            for place, comments in by_place.items()
        }
        for comment in site.comments:
            directive = is_directive(comment.text)
            if directive and position > 0:
                continue  # the same as the first site's, which are carried
            anchor, _ = _projected(statements, comment.anchor)
            already = carried.setdefault(_place(anchor), Counter())
            if already[comment.text] > 0:
                already[comment.text] -= 1
                continue
            line_start = None
            if directive and comment.line_start is not None:
                line_start = _resolve(statements, comment.line_start)[1]
            if position > 0 and not anchor.own_line and by_place.get(_place(anchor)):
                # Another site's note already ends that line; this one goes
                # on its own line before the same code rather than after it.
                anchor = _above(anchor)
            by_place.setdefault(_place(anchor), []).append(
                HelperComment(comment.text, anchor, directive, line_start)
            )
    return HelperComments(
        body_offset, tuple(comment for comments in by_place.values() for comment in comments)
    )


def _above(anchor: Anchor) -> Anchor:
    """``anchor`` moved from the end of its line to a line of its own before the same code."""
    placement = (
        Placement.ABOVE_HEADER if anchor.placement is Placement.HEADER else Placement.LEADING
    )
    return Anchor(anchor.node, placement, (), anchor.group, anchor.exploded)


# -- Writing the comments into the rendered helper -----------------------------


class _Rank(Enum):
    """The order of insertions at one offset of the rendered text."""

    AFTER_STATEMENT = 0
    ABOVE_HEADER = 1
    BEFORE_STATEMENT = 2
    OPEN_PAREN = 3
    COMMA = 4
    BEFORE_EXPRESSION = 5
    TRAILING = 6
    AFTER_EXPRESSION = 7
    CLOSE_PAREN = 8
    # A comment ending a line goes after a parenthesis written back there.
    END_OF_LINE = 9


@dataclass(frozen=True)
class _Insertion:
    offset: int
    rank: _Rank
    ordinal: int
    text: str
    # The comments this insertion writes; trailing ones at one place share a line.
    comments: Tuple[int, ...] = ()
    # Spaces of the rendered text at ``offset`` that the insertion replaces,
    # so that a line it breaks neither ends nor goes on with stray blanks.
    skip: int = 0


class _Layout:
    """Offsets, brackets and strings of a rendered text."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.lines = text.split("\n")
        self.line_starts: List[int] = []
        offset = 0
        for line in self.lines:
            self.line_starts.append(offset)
            offset += len(line) + 1
        self._token_ends: List[int] = []
        self._depth_after: List[int] = []
        self._strings: List[Tuple[int, int]] = []
        depth = 0
        fstring_start: List[int] = []
        for token in _tokens(text):
            start, end = self.offset(token.start), self.offset(token.end)
            if _FSTRING_START is not None and token.kind == _FSTRING_START:
                fstring_start.append(start)
                continue
            if _FSTRING_END is not None and token.kind == _FSTRING_END:
                opened = fstring_start.pop()
                if not fstring_start:
                    self._strings.append((opened, end))
                    self._token_ends.append(end)
                    self._depth_after.append(depth)
                continue
            if fstring_start:
                continue
            if token.kind == tokenize.STRING:
                self._strings.append((start, end))
            elif token.kind == tokenize.OP and token.text in _OPENING:
                depth += 1
            elif token.kind == tokenize.OP and token.text in _CLOSING:
                depth -= 1
            self._token_ends.append(end)
            self._depth_after.append(depth)

    def offset(self, position: Position) -> int:
        return self.line_starts[position[0] - 1] + position[1]

    def start_of(self, node: ast.AST) -> int:
        line = getattr(node, "lineno")
        return self.offset((line, _column(self.lines[line - 1], getattr(node, "col_offset"))))

    def end_of(self, node: ast.AST) -> int:
        line = getattr(node, "end_lineno")
        return self.offset((line, _column(self.lines[line - 1], getattr(node, "end_col_offset"))))

    def depth_at(self, offset: int) -> int:
        index = bisect.bisect_right(self._token_ends, offset) - 1
        return self._depth_after[index] if index >= 0 else 0

    def inside_string(self, offset: int) -> bool:
        return any(start < offset < end for start, end in self._strings)

    def line_end(self, offset: int) -> int:
        end = self.text.find("\n", offset)
        return len(self.text) if end < 0 else end

    def rest_is_blank(self, offset: int) -> bool:
        return not self.text[offset : self.line_end(offset)].strip()

    def after_punctuation(self, offset: int, punctuation: Sequence[str]) -> int:
        """``offset`` moved past the rendered tokens that match ``punctuation`` in order.

        A closing parenthesis the rendering lacks closed grouping parentheses
        it dropped, and is passed over; any other difference ends the match.
        """
        position = offset
        for expected in punctuation:
            rest = self.text[position:]
            stripped = rest.lstrip(" ")
            if stripped.startswith(expected) and _whole_token(stripped, expected):
                position += len(rest) - len(stripped) + len(expected)
            elif expected != ")":
                break
        return position

    def indentation(self, line: int) -> str:
        text = self.lines[line - 1]
        return text[: len(text) - len(text.lstrip(" \t"))]


def _whole_token(rest: str, expected: str) -> bool:
    """Whether ``rest`` begins with the token ``expected``, not a longer name that starts alike."""
    if not (expected[-1:].isalnum() or expected[-1:] == "_"):
        return True
    following = rest[len(expected) : len(expected) + 1]
    return not (following.isalnum() or following == "_")


def _statement_on_path(statements: Sequence[ast.AST], path: NodePath) -> ast.AST:
    """The innermost statement (or ``except`` clause) along ``path``."""
    node = statements[path.statement]
    found = node
    for field_name, index, _ in path.steps:
        child = _child(node, field_name, index)
        if child is None:
            break
        node = child
        if _statement_like(node):
            found = node
    return found


def _header_line(clause_first: ast.AST) -> int:
    """The rendered line of the keyword opening the clause whose first statement is given."""
    return int(getattr(clause_first, "lineno")) - 1


class _Weaver:
    """The insertions that write a helper's comments into its unparsed text."""

    def __init__(
        self, layout: _Layout, statements: Sequence[ast.AST], comments: Sequence[HelperComment]
    ) -> None:
        self.layout = layout
        self.statements = statements
        self.comments = comments
        self.insertions: List[_Insertion] = []
        self._groups: set[Tuple[int, int]] = set()
        self._commas: set[int] = set()

    def weave(self, safe: bool) -> List[_Insertion]:
        for ordinal, comment in enumerate(self.comments):
            if safe:
                self._safe(ordinal, comment)
            else:
                self._place(ordinal, comment)
        return self.insertions

    # Placement where the comment was ------------------------------------------

    def _place(self, ordinal: int, comment: HelperComment) -> None:
        anchor = comment.anchor
        node, _, exact = _resolve(self.statements, anchor.node)
        placement = anchor.placement
        layout = self.layout
        if placement is Placement.TRAILING:
            offset = layout.end_of(node)
            if exact and anchor.punctuation:
                offset = layout.after_punctuation(offset, anchor.punctuation)
            if layout.inside_string(offset):
                self._safe(ordinal, comment)
            elif layout.depth_at(offset) == 0 and self._regroup(offset, anchor, before=False):
                # Inside the parentheses the source wrapped the code in, the
                # comment stays on that code's line when a formatter splits it.
                self._explode(anchor)
                self._trailing(offset, ordinal, comment, continues=True)
            elif layout.rest_is_blank(offset):
                self._trailing(layout.line_end(offset), ordinal, comment)
            elif layout.depth_at(offset) > 0:
                self._explode(anchor)
                self._trailing(offset, ordinal, comment, continues=True)
            else:
                self._safe(ordinal, comment)
        elif placement is Placement.FIRST_LINE:
            statement = _statement_on_path(self.statements, anchor.node)
            self._trailing(layout.line_end(layout.start_of(statement)), ordinal, comment)
        elif placement is Placement.HEADER:
            line = _header_line(node)
            self._trailing(layout.line_end(layout.line_starts[line - 1]), ordinal, comment)
        elif placement is Placement.ABOVE_HEADER:
            line = _header_line(node)
            self._own_line(
                layout.line_starts[line - 1],
                layout.indentation(line),
                _Rank.ABOVE_HEADER,
                ordinal,
                comment,
            )
        elif _statement_like(node):
            if placement is Placement.LEADING:
                self._before_statement(node, ordinal, comment)
            else:
                self._after_statement(node, ordinal, comment)
        elif placement is Placement.LEADING:
            offset = layout.start_of(node)
            if self._bracketed(offset, anchor, before=True):
                self._explode(anchor)
                self._inside(offset, _Rank.BEFORE_EXPRESSION, ordinal, comment, node)
            else:
                self._safe(ordinal, comment)
        else:
            offset = layout.end_of(node)
            if exact and anchor.punctuation:
                offset = layout.after_punctuation(offset, anchor.punctuation)
            if self._bracketed(offset, anchor, before=False):
                self._explode(anchor)
                self._inside(offset, _Rank.AFTER_EXPRESSION, ordinal, comment, node)
            else:
                self._safe(ordinal, comment)

    def _bracketed(self, offset: int, anchor: Anchor, before: bool) -> bool:
        """Whether a line may break at ``offset``: inside brackets, or inside the source's parentheses."""
        layout = self.layout
        if layout.inside_string(offset):
            return False
        return layout.depth_at(offset) > 0 or self._regroup(offset, anchor, before)

    def _regroup(self, offset: int, anchor: Anchor, before: bool) -> bool:
        """Write back the grouping parentheses the source had around the comment, if they hold ``offset``.

        Unparsing drops parentheses that only group; the comment stood
        inside them, so they go back around their expression.
        """
        if anchor.group is None:
            return False
        group, _, _ = _resolve(self.statements, anchor.group)
        if _statement_like(group):
            return False
        start, end = self.layout.start_of(group), self.layout.end_of(group)
        if not (start <= offset < end if before else start < offset <= end):
            return False
        if (start, end) not in self._groups:
            self._groups.add((start, end))
            self.insertions.append(_Insertion(start, _Rank.OPEN_PAREN, -1, "("))
            self.insertions.append(_Insertion(end, _Rank.CLOSE_PAREN, -1, ")"))
        return True

    def _explode(self, anchor: Anchor) -> None:
        """Write back the trailing comma that held the comment's brackets open in the source."""
        if anchor.exploded is None:
            return
        node, _, exact = _resolve(self.statements, anchor.exploded)
        if not exact or not isinstance(node, _EXPLODABLE):
            return
        end = self.layout.end_of(node)
        closing = self.layout.text[end - 1 : end]
        inside = self.layout.text[self.layout.start_of(node) : end - 1].rstrip()
        if closing not in _CLOSING or inside.endswith((",", "(", "[", "{")):
            return
        if end - 1 not in self._commas:
            self._commas.add(end - 1)
            self.insertions.append(_Insertion(end - 1, _Rank.COMMA, -1, ","))

    def _continuation(self, comment: HelperComment) -> str:
        """The indentation of a line a comment breaks: one level inside its statement."""
        statement = _statement_on_path(self.statements, comment.anchor.node)
        return " " * (int(getattr(statement, "col_offset", 0)) + 4)

    def _blanks_after(self, offset: int) -> int:
        text = self.layout.text
        end = offset
        while end < len(text) and text[end] == " ":
            end += 1
        return end - offset

    def _trailing(
        self, offset: int, ordinal: int, comment: HelperComment, continues: bool = False
    ) -> None:
        if continues:
            self.insertions.append(
                _Insertion(
                    offset,
                    _Rank.TRAILING,
                    ordinal,
                    "  " + comment.text + "\n" + self._continuation(comment),
                    (ordinal,),
                    self._blanks_after(offset),
                )
            )
        else:
            self.insertions.append(
                _Insertion(offset, _Rank.END_OF_LINE, ordinal, "  " + comment.text, (ordinal,))
            )

    def _own_line(
        self, offset: int, indent: str, rank: _Rank, ordinal: int, comment: HelperComment
    ) -> None:
        self.insertions.append(
            _Insertion(offset, rank, ordinal, indent + comment.text + "\n", (ordinal,))
        )

    def _inside(
        self, offset: int, rank: _Rank, ordinal: int, comment: HelperComment, node: ast.AST
    ) -> None:
        indent = self._continuation(comment)
        text = self.layout.text
        start = offset
        if rank is _Rank.BEFORE_EXPRESSION:
            while start > 0 and text[start - 1] == " ":
                start -= 1
        self.insertions.append(
            _Insertion(
                start,
                rank,
                ordinal,
                "\n" + indent + comment.text + "\n" + indent,
                (ordinal,),
                offset - start if rank is _Rank.BEFORE_EXPRESSION else self._blanks_after(offset),
            )
        )

    def _before_statement(self, node: ast.AST, ordinal: int, comment: HelperComment) -> None:
        line = int(getattr(node, "lineno"))
        self._own_line(
            self.layout.line_starts[line - 1],
            self.layout.indentation(line),
            _Rank.BEFORE_STATEMENT,
            ordinal,
            comment,
        )

    def _after_statement(self, node: ast.AST, ordinal: int, comment: HelperComment) -> None:
        line = int(getattr(node, "end_lineno"))
        indent = self.layout.indentation(int(getattr(node, "lineno")))
        if line < len(self.layout.lines):
            self._own_line(
                self.layout.line_starts[line], indent, _Rank.AFTER_STATEMENT, ordinal, comment
            )
        else:
            self.insertions.append(
                _Insertion(
                    len(self.layout.text),
                    _Rank.AFTER_STATEMENT,
                    ordinal,
                    "\n" + indent + comment.text,
                    (ordinal,),
                )
            )

    # Placement that always holds ----------------------------------------------

    def _safe(self, ordinal: int, comment: HelperComment) -> None:
        """Place ``comment`` by its statement alone: on a line of its own, or ending one."""
        statement = _statement_on_path(self.statements, comment.anchor.node)
        if comment.anchor.placement is Placement.CLOSING:
            self._after_statement(statement, ordinal, comment)
        elif comment.anchor.own_line:
            self._before_statement(statement, ordinal, comment)
        else:
            body = getattr(statement, "body", None)
            if isinstance(body, list) and body:
                line = _header_line(body[0])
            else:
                line = int(getattr(statement, "end_lineno"))
            self._trailing(
                self.layout.line_end(self.layout.line_starts[line - 1]), ordinal, comment
            )


def _joined(insertions: Sequence[_Insertion]) -> List[_Insertion]:
    """The insertions with trailing comments at one place written on one line, in order."""
    joined: List[_Insertion] = []
    ordered = sorted(insertions, key=lambda item: (item.offset, item.rank.value, item.ordinal))
    for insertion in ordered:
        previous = joined[-1] if joined else None
        if (
            previous is not None
            and insertion.rank in (_Rank.TRAILING, _Rank.END_OF_LINE)
            and previous.rank is insertion.rank
            and previous.offset == insertion.offset
        ):
            # A comment breaking a line ends in the line break and indentation
            # that go on with the code; the joined comments share one.
            head, newline, rest = previous.text.partition("\n")
            text = head + insertion.text.partition("\n")[0] + newline + rest
            joined[-1] = _Insertion(
                previous.offset,
                previous.rank,
                previous.ordinal,
                text,
                previous.comments + insertion.comments,
                max(previous.skip, insertion.skip),
            )
        else:
            joined.append(insertion)
    return joined


def _applied(text: str, insertions: Sequence[_Insertion]) -> str:
    pieces: List[str] = []
    cursor = 0
    for insertion in insertions:
        if insertion.offset > cursor:
            pieces.append(text[cursor : insertion.offset])
        pieces.append(insertion.text)
        cursor = max(cursor, insertion.offset + insertion.skip)
    pieces.append(text[cursor:])
    return "".join(pieces)


def _comment_lines(text: str) -> Optional[List[int]]:
    """The line of each comment of ``text``, in order; None when it does not tokenize."""
    try:
        return [token.start[0] for token in _tokens(text) if token.kind == tokenize.COMMENT]
    except (tokenize.TokenError, SyntaxError):
        return None


def _helper_in(tree: ast.Module, position: int) -> Optional[ast.AST]:
    if position < len(tree.body) and isinstance(
        tree.body[position], (ast.FunctionDef, ast.AsyncFunctionDef)
    ):
        return tree.body[position]
    return None


@dataclass(frozen=True)
class _DirectiveCheck:
    """A directive the formatter must leave beside its code: its comment and that code's ends."""

    comment: int
    anchor: NodePath
    line_start: Optional[NodePath]


@dataclass(frozen=True)
class WovenHelper:
    """A helper's unparsed text with its comments written in, and how to check a formatting of it."""

    text: str
    position: int
    body_offset: int
    comment_count: int
    checks: Tuple[_DirectiveCheck, ...] = ()

    def keeps_directives(self, formatted: str) -> bool:
        """Whether ``formatted`` leaves every directive on the line of the code it was beside.

        The code a directive covered, from the first node its line started
        to the node it followed, must still end and start on the directive's
        line: a formatter that split that line under the comment, or moved
        the comment past a closing bracket, changed what the directive
        covers.
        """
        if not self.checks:
            return True
        try:
            tree = ast.parse(formatted)
        except SyntaxError:
            return False
        helper = _helper_in(tree, self.position)
        lines = _comment_lines(formatted)
        if helper is None or lines is None or len(lines) != self.comment_count:
            return False
        statements = getattr(helper, "body")[self.body_offset :]
        for check in self.checks:
            line = lines[check.comment]
            anchor, _, _ = _resolve(statements, check.anchor)
            if getattr(anchor, "end_lineno", line) != line:
                return False
            if check.line_start is not None:
                start, _, _ = _resolve(statements, check.line_start)
                if getattr(start, "lineno", line) != line:
                    return False
        return True


def weave_comments(node: ast.AST, helper: ast.AST, comments: HelperComments) -> WovenHelper:
    """``ast.unparse(node)`` with ``comments`` written where they stood in the helper's body.

    ``node`` is the helper itself, or a module whose last statement is the
    helper. The result has the syntax tree of the plain rendering, which is
    checked; where a comment cannot stand where it was written, it goes to
    its statement's line instead. Raises ``CommentPlacementError`` when even
    that does not hold.
    """
    text = ast.unparse(node)
    tree = ast.parse(text)
    position = len(tree.body) - 1 if isinstance(node, ast.Module) else 0
    rendered = _helper_in(tree, position)
    if rendered is None or ast.dump(rendered) != ast.dump(helper):
        raise CommentPlacementError("The helper does not render to itself")
    statements = getattr(rendered, "body")[comments.body_offset :]
    layout = _Layout(text)
    expected = ast.dump(tree)
    for safe in (False, True):
        insertions = _joined(_Weaver(layout, statements, comments.comments).weave(safe))
        woven = _applied(text, insertions)
        written = [insertion for insertion in insertions if insertion.comments]
        lines = _comment_lines(woven)
        try:
            same = ast.dump(ast.parse(woven)) == expected
        except SyntaxError:
            same = False
        if same and lines is not None and len(lines) == len(written):
            ordinal_to_comment = {
                ordinal: index
                for index, insertion in enumerate(written)
                for ordinal in insertion.comments
            }
            checks = tuple(
                _DirectiveCheck(
                    ordinal_to_comment[ordinal], comment.anchor.node, comment.line_start
                )
                for ordinal, comment in enumerate(comments.comments)
                if comment.directive
                and not comment.anchor.own_line
                and comment.anchor.placement is Placement.TRAILING
            )
            return WovenHelper(woven, position, comments.body_offset, len(written), checks)
    raise CommentPlacementError("The helper's comments cannot be written into its text")
