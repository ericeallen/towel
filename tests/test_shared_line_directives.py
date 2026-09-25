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

"""A tool directive on a line the block shares with code that stays declines the pair.

A directive governs its whole physical line. When the block starts after
another statement on its line (``a = 1; b = 2  # noqa`` with the block at
``b``), the comment moves into the helper and ``a = 1``, which stays, is no
longer covered by it; when the block ends before another statement, the
comment stays with that statement and the helper takes a copy it never had.
Either way the directive reaches other code than it did, so the pair is
declined (``directive_on_shared_line``), for every form of directive the
comment table knows. A plain comment on such a line moves as before.
"""

from __future__ import annotations

import ast
from pathlib import Path
import textwrap
from typing import List, Optional, Set

import pytest

from towel.unification.block_comments import (
    DEFAULT_EXCLUSION,
    directive_conflict,
    is_directive,
    site_comments,
)
from towel.unification.refactor_engine import UnificationRefactorEngine

# One comment for each form ``block_comments._DIRECTIVE`` recognizes.
DIRECTIVES = [
    "# type: ignore",
    "# type: ignore[attr-defined]",
    "# type: int",
    "# pyright: ignore[reportAttributeAccessIssue]",
    "# mypy: disable-error-code=misc",
    "# pytype: disable=attribute-error",
    "# ty: ignore[unresolved-attribute]",
    "# pyre-ignore[16]",
    "# pyre-fixme[16]",
    "# pyre-strict",
    "# pyre-unsafe",
    "# pyre-ignore-all-errors",
    "# noqa",
    "# noqa: E702",
    "# flake8: noqa",
    "# ruff: noqa: E702",
    "# pragma: no cover",
    "# nosec",
    "# nosec B101",
    "# pylint: disable=invalid-name",
    "# noinspection PyUnresolvedReferences",
    "# fmt: skip",
    "# fmt: off",
    "# fmt: on",
    "# yapf: disable",
    "# yapf: enable",
    "# isort: skip",
    "# autopep8: off",
    "# autopep8: on",
    "# pycln: import",
    "# nopycln: import",
    "# codespell: ignore",
    "# type: ignore  # noqa: E702",
]

STARTS_MID_LINE = """\
def f(n):
    a = n + 1; b = a * 2  {comment}
    c = a + b
    return c
"""
"""The block is ``b``, ``c`` and the return: ``a = n + 1`` stays on the call's line."""

ENDS_MID_LINE = """\
def f(n):
    a = n + 1
    b = a * 2; c = a + b  {comment}
    return c
"""
"""The block is ``a`` and ``b``: ``c = a + b`` stays on the call's line."""

SHAPES = {"starts-mid-line": (STARTS_MID_LINE, 1, 4), "ends-mid-line": (ENDS_MID_LINE, 0, 2)}


def _conflict(template: str, first: int, last: int, comment: str, exclusion: str) -> Optional[str]:
    source = template.format(comment=comment)
    function = ast.parse(source).body[0]
    assert isinstance(function, ast.FunctionDef)
    block: List[ast.stmt] = function.body[first:last]
    found = directive_conflict(block, [site_comments(source, block, (), exclusion)])
    return None if found is None else found.kind.value


@pytest.mark.parametrize("shape", sorted(SHAPES))
@pytest.mark.parametrize("comment", DIRECTIVES)
def test_a_directive_on_a_shared_line_declines(shape: str, comment: str) -> None:
    assert is_directive(comment)
    assert _conflict(*SHAPES[shape], comment, DEFAULT_EXCLUSION) == "directive_on_shared_line"


@pytest.mark.parametrize("shape", sorted(SHAPES))
@pytest.mark.parametrize("comment", ["# why b doubles a", "# see RFC 7231 section 4.3", ""])
def test_a_plain_comment_on_a_shared_line_moves(shape: str, comment: str) -> None:
    assert _conflict(*SHAPES[shape], comment, DEFAULT_EXCLUSION) is None


@pytest.mark.parametrize("comment", ["# noqa: E702", "# pragma: no cover", "# type: ignore"])
def test_a_directive_on_a_line_the_block_holds_whole_still_moves(comment: str) -> None:
    # The block starts at ``a``: the line moves into the helper entire.
    assert _conflict(STARTS_MID_LINE, 0, 3, comment, "") is None
    assert _conflict(ENDS_MID_LINE, 0, 4, comment, "") is None


def test_a_pragma_the_project_does_not_exclude_by_is_a_plain_comment() -> None:
    assert _conflict(ENDS_MID_LINE, 0, 2, "# pragma: no cover", "") is None


def test_a_configured_exclusion_matching_code_on_a_shared_line_declines() -> None:
    template = "def f(n):\n    a = n + 1; b = a * 2{comment}\n    c = a + b\n    return c\n"
    assert _conflict(template, 1, 4, "", r"a = n \+ 1") == "directive_on_shared_line"
    assert _conflict(template, 1, 4, "", r"c = a \+ b") is None


def _engine() -> UnificationRefactorEngine:
    return UnificationRefactorEngine(min_lines=3, parameterize_constants=True)


@pytest.mark.parametrize(
    "source, shared",
    [
        # Assign and annotated assignment cannot pair, so the blocks that
        # pair start after the ``;``; the lines below it still may move.
        (
            """\
            def f1(n):
                a = n + 1; b = a * 2  # noqa: E702
                c = a + b
                print("f1", a, b, c)
                return c
            def f2(n):
                a: int = n + 2; b = a * 2  # noqa: E702
                c = a + b
                print("f2", a, b, c)
                return c
            """,
            {2, 7},
        ),
        (
            """\
            def g1(items):
                total = sum(items)
                print("g1 total", total)
                count = len(items); log.append(count)  # type: ignore[attr-defined]
                return total + count
            def g2(items):
                total = sum(items)
                print("g2 total", total)
                count = len(items); scaled = count * 3  # type: ignore[attr-defined]
                return total + scaled
            """,
            {4, 9},
        ),
    ],
    ids=["starts-mid-line", "ends-mid-line"],
)
def test_the_engine_declines_such_a_pair_by_name(
    tmp_path: Path, source: str, shared: Set[int]
) -> None:
    path = tmp_path / "m.py"
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    engine = _engine()
    proposals = engine.analyze_file(str(path))
    assert "directive_on_shared_line" in engine.declined_pairs
    touched = {
        line
        for proposal in proposals
        for replacement in proposal.replacements
        for line in range(replacement.line_range[0], replacement.line_range[1] + 1)
    }
    assert not touched & shared
