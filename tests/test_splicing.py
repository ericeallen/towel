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

"""The call replaces exactly its block's text, and every other statement stays.

A line can hold statements outside the block: ``a = n + 1; b = a * 2`` with
the block starting at ``b`` once lost ``a = n + 1``, since the splice
replaced whole lines (audit 1.772, P1-2). The unit tests pin each shape a
block's first or last line can take; the property checks, on generated
modules full of ``;`` lines and one-line compound bodies, that splicing a
call in place of any run of statements leaves the module's tree exactly the
original with that run replaced by the call.
"""

from __future__ import annotations

import ast
import copy
import random
from typing import List, Optional, Sequence, Tuple

import pytest

from towel.source_text import source_lines
from towel.unification.splicing import (
    BlockColumns,
    character_column,
    holds_code,
    splice_block,
    whole_lines,
)

BodyPath = Tuple[object, ...]
"""A path to a statement list: field names and indices from the module down."""


def _body(tree: ast.AST, path: Sequence[object]) -> List[ast.stmt]:
    node: object = tree
    for step in path:
        if isinstance(step, int):
            assert isinstance(node, list)
            node = node[step]
        else:
            node = getattr(node, str(step))
    assert isinstance(node, list)
    return node


def _spliced(
    source: str, path: Sequence[object], first: int, last: int, code: str
) -> Tuple[str, str]:
    """``source`` with statements ``first``..``last`` of the list at ``path`` replaced by ``code``."""
    tree = ast.parse(source)
    block = _body(tree, path)[first : last + 1]
    lines = source_lines(source)
    start_line, end_line = block[0].lineno, block[-1].end_lineno or block[-1].lineno
    spliced = splice_block(lines[start_line - 1 : end_line], BlockColumns.of(block), code)
    lines[start_line - 1 : end_line] = spliced.lines
    return "".join(lines), spliced.replaced


FUNCTION_BODY = ("body", 0, "body")
IF_BODY = ("body", 0, "body", 0, "body")


@pytest.mark.parametrize(
    "source, path, first, last, expected, replaced",
    [
        pytest.param(
            "def f(n):\n    a = n + 1; b = a * 2\n    c = a + b\n    return c\n",
            FUNCTION_BODY,
            1,
            3,
            "def f(n):\n    a = n + 1; return h(a)\n",
            "b = a * 2\nc = a + b\nreturn c",
            id="starts-at-the-second-statement-of-a-line",
        ),
        pytest.param(
            "def f(n):\n    a = n\n    b = 1; c = 2\n    return c\n",
            FUNCTION_BODY,
            0,
            1,
            "def f(n):\n    return h(a); c = 2\n    return c\n",
            "a = n\nb = 1",
            id="ends-at-the-first-statement-of-a-line",
        ),
        pytest.param(
            "def f():\n    a = 1; b = 2; c = 3\n",
            FUNCTION_BODY,
            1,
            1,
            "def f():\n    a = 1; return h(a); c = 3\n",
            "b = 2",
            id="the-middle-statement-of-a-line",
        ),
        pytest.param(
            "def f(x):\n    if x: a = 1; b = 2\n    return 0\n",
            IF_BODY,
            0,
            1,
            "def f(x):\n    if x: return h(a)\n    return 0\n",
            "a = 1; b = 2",
            id="a-compound-statement-s-body-on-its-header-line",
        ),
        pytest.param(
            "def f(x):\n    if x: a = 1; b = 2; c = 3\n",
            IF_BODY,
            1,
            1,
            "def f(x):\n    if x: a = 1; return h(a); c = 3\n",
            "b = 2",
            id="inside-a-header-line-body",
        ),
        pytest.param(
            "def f(x):\n    while x: x -= 1; b = 2\n    else: c = 1; d = 2\n",
            ("body", 0, "body", 0, "orelse"),
            0,
            1,
            "def f(x):\n    while x: x -= 1; b = 2\n    else: return h(a)\n",
            "c = 1; d = 2",
            id="an-else-clause-on-its-header-line",
        ),
        pytest.param(
            "def f():\n    x = 1 + \\\n        2; b = 3\n    c = 4\n",
            FUNCTION_BODY,
            1,
            2,
            "def f():\n    x = 1 + \\\n        2; return h(a)\n",
            # The change log shows the block under its first line's indentation.
            "    b = 3\nc = 4",
            id="after-a-statement-continued-onto-its-line",
        ),
        pytest.param(
            "def f():\n    a = 1\n    b = 2; \\\n    c = 3\n",
            FUNCTION_BODY,
            0,
            1,
            "def f():\n    return h(a); \\\n    c = 3\n",
            "a = 1\nb = 2",
            id="before-a-backslash-that-continues-its-line",
        ),
        pytest.param(
            "def f():\n    b = 1 + \\\n        2\n    c = 3\n",
            FUNCTION_BODY,
            0,
            1,
            "def f():\n    return h(a)\n",
            "b = 1 + \\\n    2\nc = 3",
            id="a-statement-it-continues-with-a-backslash",
        ),
        pytest.param(
            "def f():\n    a = 1; b = 2  # why b\n    c = 3\n",
            FUNCTION_BODY,
            1,
            2,
            "def f():\n    a = 1; return h(a)\n",
            "b = 2  # why b\nc = 3",
            id="a-comment-after-the-statement-the-block-starts-at",
        ),
        pytest.param(
            "def f():\n    a = 1\n    b = 2  # why b\n    c = 3\n",
            FUNCTION_BODY,
            0,
            1,
            "def f():\n    return h(a)\n    c = 3\n",
            "a = 1\nb = 2  # why b",
            id="a-comment-after-the-block-goes-with-it",
        ),
        pytest.param(
            "def f():\n    a = 1\n    b = 2; c = 3  # why c\n",
            FUNCTION_BODY,
            0,
            1,
            "def f():\n    return h(a); c = 3  # why c\n",
            "a = 1\nb = 2",
            id="a-comment-after-a-statement-the-block-leaves",
        ),
        pytest.param(
            "def f():\n    a = 1\n    b = 2;\n    c = 3\n",
            FUNCTION_BODY,
            0,
            1,
            "def f():\n    return h(a)\n    c = 3\n",
            "a = 1\nb = 2;",
            id="a-trailing-semicolon",
        ),
        pytest.param(
            'def f():\n    s = "é日"; b = len(s)\n    c = b\n',
            FUNCTION_BODY,
            1,
            2,
            'def f():\n    s = "é日"; return h(a)\n',
            "b = len(s)\nc = b",
            id="columns-count-utf8-bytes",
        ),
        pytest.param(
            'def f():\n    a = 1\n    b = "é"; c = 3\n',
            FUNCTION_BODY,
            0,
            1,
            "def f():\n    return h(a); c = 3\n",
            'a = 1\nb = "é"',
            id="an-end-column-after-multibyte-text",
        ),
        pytest.param(
            "def f():\n    a = 1\n    b = 2; c = 3",
            FUNCTION_BODY,
            0,
            1,
            "def f():\n    return h(a); c = 3",
            "a = 1\nb = 2",
            id="a-last-line-without-a-newline",
        ),
    ],
)
def test_each_shape_of_a_block_s_first_and_last_line(
    source: str, path: BodyPath, first: int, last: int, expected: str, replaced: str
) -> None:
    text, before = _spliced(source, path, first, last, "return h(a)")
    assert text == expected
    assert before == replaced
    ast.parse(text)


def test_a_call_over_several_lines_continues_at_the_line_s_indentation() -> None:
    source = "def f(n):\n    a = n + 1; b = a * 2\n    return b\n"
    text, _ = _spliced(source, FUNCTION_BODY, 1, 2, "return h(\n    a,\n\n    n,\n)")
    assert text == "def f(n):\n    a = n + 1; return h(\n        a,\n\n        n,\n    )\n"
    ast.parse(text)


def test_whole_lines_splice_as_they_always_did() -> None:
    lines = source_lines("    x = 1\n    y = 2  # note\n")
    spliced = splice_block(lines, whole_lines(lines), "return h(\n    x,\n)")
    assert spliced.lines == ("    return h(\n", "        x,\n", "    )\n")
    assert spliced.replaced == "x = 1\ny = 2  # note"


@pytest.mark.parametrize(
    "rest, code",
    [
        ("\n", False),
        ("", False),
        ("  # note\n", False),
        (";\n", False),
        (" ;  # note\n", False),
        ("; c = 2\n", True),
        (";c = 2  # note\n", True),
        (" \\\n", True),
        ("; \\\n", True),
    ],
)
def test_what_after_a_statement_is_code(rest: str, code: bool) -> None:
    assert holds_code(rest) is code


def test_character_column_converts_utf8_byte_offsets() -> None:
    line = '    s = "é日\U0001f600"; b = 2\n'
    statement = ast.parse(line.strip()).body[1]
    offset = statement.col_offset + len(line) - len(line.lstrip())
    assert line[character_column(line, offset) :].startswith("b = 2")


# -- The property --------------------------------------------------------------

CALL = "__towel_call__(\n    1,\n)"
"""The replacement: a call over several lines, as a formatter lays out a long one."""


class _ModuleWriter:
    """Small modules whose lines hold several statements, in every way Python allows."""

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng
        self._counter = 0

    def _name(self) -> str:
        self._counter += 1
        return self._rng.choice(["a", "b", "été", "名"]) + str(self._counter)

    def _simple(self) -> str:
        choice = self._rng.randrange(7)
        name = self._name()
        if choice == 0:
            return f"{name} = {self._rng.randrange(100)}"
        if choice == 1:
            return f"{name} = 'é日{self._rng.randrange(9)}'"
        if choice == 2:
            return f"print({name!r})"
        if choice == 3:
            return f"{name} = (1 +\n        2)"
        if choice == 4:
            return f"{name} = 1 + \\\n        2"
        if choice == 5:
            return "pass"
        return f"{name} = [{self._rng.randrange(9)},\n  {self._rng.randrange(9)}]"

    def _simple_line(self, indent: str) -> str:
        """One logical line of simple statements, maybe with a trailing ``;`` or comment."""
        statements = [self._simple() for _ in range(self._rng.randint(1, 3))]
        joiner = self._rng.choice(["; ", ";", " ; "])
        text = joiner.join(statements)
        ending = self._rng.choice(["", "", ";", "  # cé", "; # c"])
        return indent + text + ending + "\n"

    def _compound(self, indent: str, depth: int) -> str:
        header = self._rng.choice(["if x:", "while x:", "for i in xs:", "with cm:"])
        if depth > 1 or self._rng.random() < 0.5:
            # The body written after the header's colon.
            text = indent + header + " " + self._simple_line("").lstrip()
            if header.startswith("if") and self._rng.random() < 0.5:
                text += indent + "else: " + self._simple_line("").lstrip()
            return text
        return indent + header + "\n" + self._suite(indent + "    ", depth + 1)

    def _suite(self, indent: str, depth: int) -> str:
        parts = []
        for _ in range(self._rng.randint(1, 4)):
            if self._rng.random() < 0.3:
                parts.append(self._compound(indent, depth))
            else:
                parts.append(self._simple_line(indent))
        return "".join(parts)

    def module(self) -> str:
        return "def f(x, xs, cm):\n" + self._suite("    ", 0)


def _bodies(tree: ast.AST) -> List[List[ast.stmt]]:
    found: List[List[ast.stmt]] = []
    for node in ast.walk(tree):
        for field in ("body", "orelse"):
            value = getattr(node, field, None)
            if isinstance(value, list) and value and isinstance(value[0], ast.stmt):
                found.append(value)
    return found


def _expected_tree(tree: ast.Module, body_index: int, first: int, last: int) -> str:
    """The dump of ``tree`` with statements ``first``..``last`` of body ``body_index`` made the call."""
    copied = copy.deepcopy(tree)
    body = _bodies(copied)[body_index]
    body[first : last + 1] = ast.parse(CALL).body
    return ast.dump(copied)


def _check(source: str, body_index: int, first: int, last: int) -> Optional[str]:
    """Why splicing that run of ``source`` goes wrong, or None."""
    tree = ast.parse(source)
    block = _bodies(tree)[body_index][first : last + 1]
    lines = source_lines(source)
    start_line, end_line = block[0].lineno, block[-1].end_lineno or block[-1].lineno
    spliced = splice_block(lines[start_line - 1 : end_line], BlockColumns.of(block), CALL)
    result = lines[: start_line - 1] + list(spliced.lines) + lines[end_line:]
    text = "".join(result)
    try:
        written = ast.parse(text)
    except SyntaxError as error:
        return f"the spliced module does not parse: {error}\n{text}"
    if ast.dump(written) != _expected_tree(tree, body_index, first, last):
        return f"the call does not stand exactly where the block did:\n{text}"
    # Writing the block's own text back where the call stands gives the original tree.
    segment = "".join(lines[start_line - 1 : end_line])
    begin = character_column(lines[start_line - 1], block[0].col_offset)
    end_offset = block[-1].end_col_offset or 0
    finish = (
        len(segment) - len(lines[end_line - 1]) + character_column(lines[end_line - 1], end_offset)
    )
    original_block = segment[begin:finish]
    call_text = "".join(spliced.lines)
    call_start = call_text.index("__towel_call__(")
    call_end = call_text.index(")", call_start) + 1
    restored = (
        "".join(lines[: start_line - 1])
        + call_text[:call_start]
        + original_block
        + call_text[call_end:]
        + "".join(lines[end_line:])
    )
    if ast.dump(ast.parse(restored)) != ast.dump(tree):
        return f"the block's text written back changes the module:\n{restored}"
    return None


def _shares_a_line(source: str, block: Sequence[ast.stmt]) -> bool:
    """Whether code outside ``block`` stands on its first or last line."""
    lines = source_lines(source)
    first, last = lines[block[0].lineno - 1], lines[(block[-1].end_lineno or 0) - 1]
    head = first[: character_column(first, block[0].col_offset)]
    tail = last[character_column(last, block[-1].end_col_offset or 0) :]
    return bool(head.strip()) or holds_code(tail)


def test_splicing_any_run_of_statements_changes_nothing_else() -> None:
    rng = random.Random(1772)
    writer = _ModuleWriter(rng)
    checked = shared = 0
    for _ in range(100):
        source = writer.module()
        tree = ast.parse(source)
        for body_index, body in enumerate(_bodies(tree)):
            for first in range(len(body)):
                for last in range(first, len(body)):
                    problem = _check(source, body_index, first, last)
                    assert problem is None, f"{problem}\n--- original:\n{source}"
                    checked += 1
                    shared += _shares_a_line(source, body[first : last + 1])
    # Not vacuous: thousands of runs, a third or more sharing a line with other code.
    assert checked > 2000 and shared > checked // 3, (checked, shared)
