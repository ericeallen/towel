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

"""Writing a call in place of a block's text, and nothing else.

A block is a run of statements, and a line can hold more than the block:
``a = n + 1; b = a * 2`` where the block starts at ``b``, ``b = 1; c = 2``
where it ends at ``b``, ``if x: a; b`` where it is the body written after
its header's colon, or a line whose first part closes a statement continued
from the line above. Replacing the block's lines whole would delete what
else they hold. So a splice replaces exactly the text from where the block's
first statement begins to where its last one ends, and keeps the rest of
its first and last lines around the call.

What is kept before the block is kept verbatim, and the call follows it on
the same line: a ``;`` or a header's colon already separates them. What
follows the block is kept when it holds code (``; c = 2``, or a backslash
continuing the line): the call is a simple statement, so the ``;`` that
separated the block from it still does. What follows the block is dropped
when it is only a ``;``, blanks, or a comment: the comment moves into the
helper with the block (``block_comments.site_comments`` reads every comment
up to the end of the block's last line), as it does from a line of the
block's alone. A tool directive governs its whole line, so on a line the
block shares with code that stays (``shared_lines``) it declines the pair
instead (``block_comments``, ``directive_on_shared_line``).
"""

from __future__ import annotations

import ast
import textwrap
from dataclasses import dataclass
from typing import FrozenSet, Sequence, Tuple

from ..source_text import source_lines


@dataclass(frozen=True)
class BlockColumns:
    """Where a block begins on its first line and ends on its last, as the syntax tree counts.

    Both columns count UTF-8 bytes, as ``col_offset`` and ``end_col_offset``
    do; ``character_column`` converts them for a line of text.
    """

    start: int
    end: int

    @classmethod
    def of(cls, block: Sequence[ast.stmt]) -> BlockColumns:
        """The columns of ``block``, a nonempty run of parsed statements."""
        end = block[-1].end_col_offset
        if end is None:
            raise ValueError("a parsed statement records where it ends")
        return cls(block[0].col_offset, end)


@dataclass(frozen=True)
class Splice:
    """The lines that replace a block's lines, and the text of theirs they replace."""

    lines: Tuple[str, ...]
    replaced: str


def character_column(line: str, byte_offset: int) -> int:
    """The index in ``line`` of the AST column ``byte_offset``, which counts UTF-8 bytes."""
    if line.isascii():
        return byte_offset
    return len(line.encode("utf-8")[:byte_offset].decode("utf-8"))


def holds_code(rest_of_line: str) -> bool:
    """Whether the text after a statement on its line is more than a ``;``, blanks and a comment.

    It is another statement (``; c = 2``) or a backslash that continues the
    line; either way, dropping it would change the program.
    """
    rest = rest_of_line.strip()
    if rest.startswith(";"):
        rest = rest[1:].lstrip()
    return bool(rest) and not rest.startswith("#")


def splice_block(block_lines: Sequence[str], columns: BlockColumns, code: str) -> Splice:
    """``block_lines`` with ``code`` written in place of the block they hold.

    ``block_lines`` run from the block's first line to its last, each with
    its line ending; ``code`` is the call, rendered at column zero, whose
    lines after the first continue it inside brackets, so they take the
    first line's indentation for looks alone.
    """
    if not block_lines:
        raise ValueError("a block spans at least one line")
    first, last = block_lines[0], block_lines[-1]
    # Before the block there is only its indentation, or code that stays.
    head, tail = _around(first, last, columns)
    kept_tail = tail if holds_code(tail) else ""
    indentation = first[: len(first) - len(first.lstrip())]
    code_lines = code.split("\n")
    text = (
        head
        + code_lines[0]
        + "".join("\n" + (indentation + line if line.strip() else "") for line in code_lines[1:])
        + (kept_tail or "\n")
    )
    written = "".join(block_lines)
    removed = written[len(head) : len(written) - len(kept_tail)]
    return Splice(tuple(source_lines(text)), textwrap.dedent(indentation + removed).rstrip("\n"))


def shared_lines(lines: Sequence[str], block: Sequence[ast.stmt]) -> FrozenSet[int]:
    """The block's first and last lines, where each also holds code the splice keeps.

    ``lines`` are the module's, ``block`` a nonempty run of its statements.
    A comment is not such code: it moves into the helper.
    """
    first_line, last_line = block[0].lineno, block[-1].end_lineno or block[-1].lineno
    head, tail = _around(lines[first_line - 1], lines[last_line - 1], BlockColumns.of(block))
    return frozenset(
        line
        for line, shared in ((first_line, bool(head.strip())), (last_line, holds_code(tail)))
        if shared
    )


def _around(first: str, last: str, columns: BlockColumns) -> Tuple[str, str]:
    """The text before a block on its first line, and after it on its last."""
    return (
        first[: character_column(first, columns.start)],
        last[character_column(last, columns.end) :],
    )


def whole_lines(block_lines: Sequence[str]) -> BlockColumns:
    """The columns of a block that is all of ``block_lines``, as a replacement built by hand is."""
    first, last = block_lines[0], block_lines[-1]
    indentation = first[: len(first) - len(first.lstrip())]
    return BlockColumns(len(indentation.encode("utf-8")), len(last.rstrip("\n").encode("utf-8")))
