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

"""Where to ask the type checker whether it looks at a statement at all.

A checker does not check code it takes to be unreachable: a module that begins
``assert sys.platform == "win32" or not TYPE_CHECKING``, anywhere but Windows,
or a branch under ``if sys.platform == ...``, ``if sys.version_info ...`` or
``if not TYPE_CHECKING`` that its configured (or host) platform and Python make
false. It reports nothing there, so a check of a change there says nothing:
trio's CI runs mypy for linux, darwin and win32, and a helper Towel verified on
one platform failed the other two. Which code is unreachable is the checker's
own rule, and mypy's and pyright's differ in detail, so the question is put to
the checker rather than answered here: ``reveal_type(0)`` inserted before a
statement is answered (``Literal[0]``, or ``Any`` in a function mypy leaves
unchecked) exactly where the checker looks.

:func:`probe_plan` says where each statement's probe goes. A probe is a line of
its own, so a statement that shares its line with a compound statement's
header (``if x: return``) is first moved to a line of its own in the text the
checker is given; a ``;`` sibling shares the probe of the statement it follows,
and a decorated definition is probed before its first decorator. The plan also
names the start of every block, and the statement after every ``assert``, for
finding a module's unreachable regions before a run (:attr:`ProbePlan.blocks`).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
import functools
from types import MappingProxyType
import warnings
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

__all__ = ["PROBE", "Place", "ProbePlan", "probe_plan"]

PROBE = "(0)"
"""The probed expression: one every checker gives a type wherever it looks.

Parenthesized so that it is never an expression inference asks about, which
``ast.unparse`` spells without redundant parentheses.
"""


Place = Tuple[int, int]
"""Where a statement begins in the original: its first line, and the column on it."""


@dataclass(frozen=True)
class ProbePlan:
    """Where the probe for each statement of a module goes.

    ``text`` is the module with every one-line body on a line of its own, and
    ``sites`` maps where each statement begins in the original to the line of
    ``text`` its probe goes before and the indentation it takes there; an
    ``elif`` has none, since nothing can stand before it, and its body is
    probed instead. ``spans`` gives each statement's beginning and last line.
    ``blocks`` gives, for each block of statements -- a body, an ``else``, a
    handler, the statements after an ``assert`` -- where its first statement
    begins and the lines the block covers.
    """

    text: str
    sites: Mapping[Place, Tuple[int, str]]
    spans: Tuple[Tuple[Place, int], ...]
    blocks: Tuple[Tuple[Place, int, int], ...]


def _first_line(node: ast.stmt) -> int:
    decorators: Sequence[ast.expr] = getattr(node, "decorator_list", ())
    return min([node.lineno, *(decorator.lineno for decorator in decorators)])


def _statement_lists(node: ast.AST) -> List[List[ast.stmt]]:
    lists: List[List[ast.stmt]] = []
    for field in ("body", "orelse", "finalbody"):
        value = getattr(node, field, None)
        if isinstance(value, list) and value and isinstance(value[0], ast.stmt):
            lists.append(value)
    for handler in getattr(node, "handlers", ()):
        lists.append(handler.body)
    for case in getattr(node, "cases", ()):
        lists.append(case.body)
    return lists


def _indent_of(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def _place(node: ast.stmt, lines: Sequence[str]) -> Place:
    """Where ``node`` begins: its first decorator's ``@``, or its first token."""
    line = _first_line(node)
    if line != node.lineno:
        return line, len(_indent_of(lines[line - 1]))
    encoded = lines[line - 1].encode("utf-8")
    return line, len(encoded[: node.col_offset].decode("utf-8", "replace"))


@functools.lru_cache(maxsize=64)
def probe_plan(source: str) -> Optional[ProbePlan]:
    """Where to probe each statement of ``source``; ``None`` when no probe can be placed.

    ``None`` for a module that does not parse, or whose probed text would not:
    a caller then knows nothing about what the checker sees there, and takes
    it to see none of it. Pure in ``source``, so memoized.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # the analysis reports the module's own warnings
            tree = ast.parse(source)
    except SyntaxError:
        return None
    lines = source.split("\n")
    statements: List[ast.stmt] = [node for node in ast.walk(tree) if isinstance(node, ast.stmt)]
    places = {id(node): _place(node, lines) for node in statements}
    parents: Dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for block in _statement_lists(node):
            for child in block:
                parents[id(child)] = node
    # A body on its header's line (``if x: return``) begins after a colon;
    # everything from there on moves to a line of its own.
    splits: Dict[int, Tuple[int, str]] = {}
    for node in statements:
        line, column = places[id(node)]
        if not lines[line - 1][:column].rstrip().endswith(":"):
            continue
        if line in splits and splits[line][0] <= column:
            continue
        parent = parents.get(id(node))
        header = _indent_of(lines[_first_line(parent) - 1]) if isinstance(parent, ast.stmt) else ""
        splits[line] = (column, header + ("\t" if "\t" in header else "    "))
    moved: List[str] = []
    position: Dict[int, int] = {}
    for number, text in enumerate(lines, 1):
        position[number] = len(moved) + 1
        if number in splits:
            column, indent = splits[number]
            moved.append(text[:column].rstrip())
            moved.append(indent + text[column:])
        else:
            moved.append(text)
    # Nothing may stand before a module's ``from __future__`` imports, or its
    # docstring; they are the first thing the module runs, so the checker
    # looks at them wherever it looks at the module at all.
    futures = [
        index
        for index, node in enumerate(tree.body)
        if isinstance(node, ast.ImportFrom) and node.module == "__future__"
    ]
    first = {id(node) for node in tree.body[: futures[-1] + 1]} if futures else set()
    if tree.body and ast.get_docstring(tree, clean=False) is not None:
        first.add(id(tree.body[0]))
    sites: Dict[Place, Tuple[int, str]] = {}
    for node in statements:
        line, column = places[id(node)]
        if id(node) in first:
            continue
        if line in splits and column >= splits[line][0]:
            sites[(line, column)] = (position[line] + 1, splits[line][1])
        elif not lines[line - 1][column:].startswith("elif"):
            # A ``;`` sibling shares the probe of the line's first statement.
            sites[(line, column)] = (position[line], _indent_of(lines[line - 1]))
    text = "\n".join(moved)
    probed = text.split("\n")
    for at, indent in sorted(set(sites.values()), reverse=True):
        probed.insert(at - 1, f"{indent}reveal_type({PROBE})")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            compile("\n".join(probed), "<probes>", "exec", dont_inherit=True)
    except SyntaxError:
        return None
    spans = tuple(
        sorted({(places[id(node)], node.end_lineno or node.lineno) for node in statements})
    )
    blocks: set[Tuple[Place, int, int]] = set()
    for node in [tree, *statements]:
        for block in _statement_lists(node):
            end = block[-1].end_lineno or block[-1].lineno
            starts = [block[0]] + [
                after for before, after in zip(block, block[1:]) if isinstance(before, ast.Assert)
            ]
            for start in starts:
                place = places[id(start)]
                blocks.add((place, place[0], end))
    return ProbePlan(text, MappingProxyType(sites), spans, tuple(sorted(blocks)))
