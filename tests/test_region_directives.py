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

"""Region directives around, inside and across a moved block.

A region directive on a line of its own governs every line up to the
directive closing it: Black's and ruff's ``fmt: off``, yapf's ``yapf:
disable``, autopep8's ``autopep8: off``, isort's ``isort: off``, pylint's
``disable``, ruff's range suppression ``ruff: disable[...]`` and pytype's
``disable``. A block inside a closed region moves with both ends; a block
holding one end declines (``directive_outlives_block``); and a block inside
a region opened before it declines unless the region also reaches the helper
(``directive_around_block``). A formatter's region declines at any site,
since that site's layout would be formatted in the helper; a linter's only
when every site is inside one, since the helper is the code of a site the
linter already reports on. What closes a region is read as its tool reads
it: ``# FMT: ON`` closes nothing for Black, nor ``ruff: enable[F401]`` a
``ruff: disable[E501]``, nor a closer at another level of the code.
"""

from __future__ import annotations

import ast
import copy
import importlib.util
from pathlib import Path
import re
import shutil
import subprocess
import sys
import textwrap
from typing import List, Optional, Sequence, Tuple

import pytest

from tests.hostile_refactoring import refactor_script
from towel.unification.block_comments import (
    CommentConflict,
    SiteComments,
    merge_comments,
    site_comments,
)

HOSTILE = Path(__file__).parent / "hostile_cases"

# Each family's opener and the closer its tool reads.
FAMILIES = [
    ("# fmt: off", "# fmt: on"),
    ("# yapf: disable", "# yapf: enable"),
    ("# autopep8: off", "# autopep8: on"),
    ("# isort: off", "# isort: on"),
    ("# pylint: disable=invalid-name", "# pylint: enable=invalid-name"),
    ("# ruff: disable[E501]", "# ruff: enable[E501]"),
    ("# pytype: disable=attribute-error", "# pytype: enable=attribute-error"),
]

_PAIR = """
def f1(rows):
    head = rows[:1]
    {before}
    n = len(rows)
    print(n)
    {after}
    return head


def f2(rows):
    head = rows[1:]
    {before}
    n = len(rows)
    print(n)
    {after}
    return head
"""

# Where the block is, as (first statement, statement count) of each body,
# with the region opened before ``n = ...`` and closed after ``print(n)``.
POSITIONS = {
    "around": (1, 2, "directive_around_block"),
    "inside": (0, 4, None),
    "across": (1, 3, "directive_outlives_block"),
}


def _bodies(source: str, names: Sequence[str] = ("f1", "f2")) -> List[List[ast.stmt]]:
    tree = ast.parse(source)
    functions = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    return [functions[name].body for name in names]


def _verdict(
    source: str,
    blocks: Sequence[Sequence[ast.stmt]],
    home_class: Optional[str] = None,
    home_function: Optional[str] = None,
) -> Optional[str]:
    """The reason the sites decline their helper, or None when it carries their comments."""
    sites: List[SiteComments] = [site_comments(source, block) for block in blocks]
    helper = [copy.deepcopy(statement) for statement in blocks[0]]
    merged = merge_comments(helper, [(site, True) for site in sites], 0, home_class, home_function)
    return merged.kind.value if isinstance(merged, CommentConflict) else None


def _pair_verdict(source: str, first: int, count: int, **home: Optional[str]) -> Optional[str]:
    text = textwrap.dedent(source).lstrip()
    blocks = [body[first : first + count] for body in _bodies(text)]
    return _verdict(text, blocks, **home)


@pytest.mark.parametrize("position", sorted(POSITIONS))
@pytest.mark.parametrize("opener, closer", FAMILIES, ids=[family[0] for family in FAMILIES])
def test_each_region_directive_around_inside_and_across_a_block(
    opener: str, closer: str, position: str
) -> None:
    first, count, expected = POSITIONS[position]
    source = _PAIR.format(before=opener, after=closer)
    assert _pair_verdict(source, first, count) == expected


@pytest.mark.parametrize("opener, closer", FAMILIES, ids=[family[0] for family in FAMILIES])
def test_a_region_closed_before_the_block_does_not_reach_it(opener: str, closer: str) -> None:
    source = _PAIR.format(before=f"{opener}\n    {closer}", after="")
    assert _pair_verdict(source, 1, 2) is None


@pytest.mark.parametrize(
    "opener, closer",
    [
        # Black and ruff format read neither spelling as a closer.
        ("# fmt: off", "# FMT: ON"),
        ("# fmt: off", "#  fmt: on"),
        # ruff 0.16 matches a range by its codes, and reads ``ruff`` in lower case.
        ("# ruff: disable[E501]", "# ruff: enable[F401]"),
        ("# ruff: disable[E501, F841]", "# ruff: enable[F841, E501]"),
        ("# ruff: disable[E501]", "# RUFF: enable[E501]"),
        ("# ruff: disable[E501]", "# ruff: enable"),
        # pylint's enable ends only the messages it names.
        ("# pylint: disable=invalid-name,protected-access", "# pylint: enable=invalid-name"),
        # isort reads a closer only as the whole line ``# isort: on``.
        ("# isort: off", "# isort:on"),
    ],
)
def test_a_closer_its_tool_does_not_read_leaves_the_region_open(opener: str, closer: str) -> None:
    source = _PAIR.format(before=f"{opener}\n    {closer}", after="")
    assert _pair_verdict(source, 1, 2) == "directive_around_block"
    # Inside a block, the region it leaves open reaches past the block.
    inside = _PAIR.format(before=opener, after=closer)
    assert _pair_verdict(inside, 0, 4) == "directive_outlives_block"


@pytest.mark.parametrize("opener, closer", [FAMILIES[0], FAMILIES[4], FAMILIES[5]])
def test_a_closer_at_another_level_of_the_code_does_not_close_the_region(
    opener: str, closer: str
) -> None:
    """Measured with Black 26.5.1 and ruff 0.16.9: a closer inside a nested body ends nothing outside it."""
    source = _PAIR.format(
        before=f"{opener}\n    if rows:\n        {closer}\n        pass", after=""
    )
    assert _pair_verdict(source, 2, 2) == "directive_around_block"


def test_a_formatters_region_at_one_site_declines_a_linters_does_not() -> None:
    """The layout Black was kept off would be formatted in the helper; pylint reports what it did."""
    for opener, closer, expected in [
        ("# fmt: off", "# fmt: on", "directive_around_block"),
        ("# pylint: disable=invalid-name", "# pylint: enable=invalid-name", None),
    ]:
        source = textwrap.dedent(_PAIR.format(before=opener, after=closer)).lstrip()
        second = source.index("def f2")
        source = source[:second] + source[second:].replace(opener, "").replace(closer, "")
        assert _verdict(source, [body[1:3] for body in _bodies(source)]) == expected, opener


_MODULE = """
{prelude}
def f1(rows):
    head = rows[:1]
    n = len(rows)
    print(n)
    return head


def f2(rows):
    head = rows[1:]
    n = len(rows)
    print(n)
    return head
{postlude}
"""


@pytest.mark.parametrize(
    "prelude, postlude, expected",
    [
        # Opened before the first definition and never closed: the module
        # helper, written before that definition or after it, is inside.
        ("# pylint: disable=invalid-name", "", None),
        ("# fmt: off", "", None),
        ("# ruff: disable[E501]", "# ruff: enable[E501]", None),
        # Opened after a definition the helper goes before.
        (
            "def a():\n    pass\n\n\n# ruff: disable[E501]",
            "# ruff: enable[E501]",
            "directive_around_block",
        ),
    ],
)
def test_a_region_at_module_level_reaches_a_module_helper_only_if_it_covers_it(
    prelude: str, postlude: str, expected: Optional[str]
) -> None:
    assert _pair_verdict(_MODULE.format(prelude=prelude, postlude=postlude), 0, 3) == expected


def test_a_region_closed_between_the_sites_leaves_the_second_outside() -> None:
    source = _MODULE.format(prelude="# fmt: off", postlude="").replace(
        "\n\ndef f2", "\n# fmt: on\n\ndef f2"
    )
    # Only the first site is kept off by Black, and the helper is not.
    assert _pair_verdict(source, 0, 3) == "directive_around_block"


_CLASS = """
class Emitter:
    {top}
    def f1(self, rows):
        head = rows[:1]
        n = len(rows)
        print(n)
        return head

    {middle}
    def f2(self, rows):
        head = rows[1:]
        n = len(rows)
        print(n)
        return head
"""


@pytest.mark.parametrize(
    "top, middle, home, expected",
    [
        # fastjsonschema's generators: a class-wide disable reaches a method helper.
        ("# pylint: disable=invalid-name", "", "Emitter", None),
        ("# pylint: disable=invalid-name", "", None, "directive_around_block"),
        ("# fmt: off", "", "Emitter", None),
        # A method helper goes after the method holding a call, or at the
        # class's end; a linter's region opened before both methods reaches
        # it, a formatter's must cover the whole class.
        ("x = 1", "# fmt: off", "Emitter", "directive_around_block"),
        ("x = 1\n    # pylint: disable=invalid-name", "", "Emitter", None),
        ("x = 1\n    # fmt: off", "", "Emitter", "directive_around_block"),
    ],
)
def test_a_region_in_a_class_reaches_a_method_helper_only_if_it_covers_it(
    top: str, middle: str, home: Optional[str], expected: Optional[str]
) -> None:
    text = textwrap.dedent(_CLASS.format(top=top, middle=middle)).lstrip()
    blocks = [body[0:3] for body in _bodies(text)]
    assert _verdict(text, blocks, home_class=home) == expected


def test_pylint_carries_a_disable_into_the_statements_later_clauses() -> None:
    """A pragma in an ``if`` body governs its ``else`` too: pylint scopes it to the statement."""
    source = textwrap.dedent("""
        def f1(rows, flag):
            if flag:
                # pylint: disable=invalid-name
                x = 1
            else:
                n = len(rows)
                print(n)
            return 1


        def f2(rows, flag):
            if flag:
                # pylint: disable=invalid-name
                x = 1
            else:
                n = len(rows)
                print(n)
            return 1
        """).lstrip()
    statements = [body[0] for body in _bodies(source)]
    blocks = [statement.orelse for statement in statements if isinstance(statement, ast.If)]
    assert len(blocks) == 2 and _verdict(source, blocks) == "directive_around_block"


_EXCLUDED = """
def f1(rows):
    head = rows[:1]
    {start}
    for n in rows:
        print(n)
    {stop}
    return head


def f2(rows):
    head = rows[1:]
    {start}
    for n in rows:
        print(n)
    {stop}
    return head
"""
_REGION_REGEX = r"(?s)# nocover: start.*?# nocover: stop"


def _excluded_verdict(start: str, stop: str, first: int, count: int, pattern: str) -> Optional[str]:
    text = textwrap.dedent(_EXCLUDED.format(start=start, stop=stop)).lstrip()
    blocks = [body[first : first + count] for body in _bodies(text)]
    sites = [site_comments(text, block, exclusion=pattern) for block in blocks]
    merged = merge_comments(
        [copy.deepcopy(statement) for statement in blocks[0]], [(site, True) for site in sites], 0
    )
    return merged.kind.value if isinstance(merged, CommentConflict) else None


def test_a_coverage_exclusion_spanning_lines_is_a_region_too() -> None:
    """coverage.py 7.6 and later match an exclusion regex across lines, as a region's markers."""
    marks = ("# nocover: start", "# nocover: stop")
    # Around the block: the helper would be measured, and never run.
    assert _excluded_verdict(*marks, 1, 1, _REGION_REGEX) == "directive_around_block"
    # Across its edge: the lines left behind would stop being excluded.
    assert _excluded_verdict(*marks, 1, 2, _REGION_REGEX) == "directive_outlives_block"
    # Before it, the region is not the block's.
    assert _excluded_verdict(*marks, 0, 1, _REGION_REGEX) is None
    # Inside it, both markers move.
    assert _excluded_verdict(*marks, 0, 3, _REGION_REGEX) is None


def test_a_match_spilling_into_blank_lines_is_not_cut() -> None:
    """coverage.py's own ``...`` regex runs on over the blank lines after a stub body."""
    source = textwrap.dedent("""
        def f1(rows):
            n = len(rows)
            print(n)
            ...

        def f2(rows):
            n = len(rows)
            print(n)
            ...
        """).lstrip()
    from towel.unification.block_comments import DEFAULT_EXCLUSION

    blocks = [body[0:3] for body in _bodies(source)]
    sites = [site_comments(source, block, exclusion=DEFAULT_EXCLUSION) for block in blocks]
    assert not any(site.excluded_across for site in sites)
    assert not any(site.around for site in sites)


# -- The reproducer, with the tools as the oracle ---------------------------------

requires_ruff = pytest.mark.skipif(importlib.util.find_spec("ruff") is None, reason="ruff absent")


def _ruff(*arguments: str) -> Tuple[int, str]:
    completed = subprocess.run(
        [sys.executable, "-m", "ruff", *arguments],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    return completed.returncode, completed.stdout


def _regions(text: str) -> List[List[str]]:
    """The lines between each ``# fmt: off`` and its ``# fmt: on``, without their indentation."""
    found: List[List[str]] = []
    current: Optional[List[str]] = None
    for line in text.split("\n"):
        if line.strip() == "# fmt: off":
            current = []
        elif line.strip() == "# fmt: on" and current is not None:
            found.append(current)
            current = None
        elif current is not None:
            current.append(line)
    return [textwrap.dedent("\n".join(region)).split("\n") for region in found]


def _kept(region: List[str], text: str) -> bool:
    """Whether ``region``'s lines stand in ``text`` one after another, uniformly re-indented."""
    lines = text.split("\n")
    for start in range(len(lines) - len(region) + 1):
        window = lines[start : start + len(region)]
        if textwrap.dedent("\n".join(window)).split("\n") == region:
            return True
    return False


@requires_ruff
def test_the_reproducers_lint_and_layout_are_what_they_were(tmp_path: Path) -> None:
    """Round 4's P1-09: ruff went from passing to E501, and the matrix onto one line."""
    source = HOSTILE / "r9dr_region_directive_around_block.py"
    before, after = tmp_path / "before" / "m.py", tmp_path / "after" / "m.py"
    for path in (before, after):
        path.parent.mkdir()
        shutil.copy(source, path)
    refactor_script(after)
    lint = ["check", "--isolated", "--select", "E501", "--output-format", "concise", "--quiet"]
    status_before, report_before = _ruff(*lint, str(before))
    status_after, report_after = _ruff(*lint, str(after))
    assert (status_after, report_after.replace(str(after), "m.py")) == (
        status_before,
        report_before.replace(str(before), "m.py"),
    )
    text = after.read_text(encoding="utf-8")
    for region in _regions(before.read_text(encoding="utf-8")):
        assert _kept(region, text), "\n".join(region)
    assert re.search(r"1,   0,   0,", text)
