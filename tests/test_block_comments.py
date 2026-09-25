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

"""Reading a block's comments, deciding the helper's, and writing them into it.

The engine-level behaviour is in ``test_moved_comments``; these pin the
three steps of ``towel.unification.block_comments`` on their own: where a
comment is anchored, which comments a helper carries from its sites, and
that writing them in keeps the rendered tree and survives a formatter.
"""

from __future__ import annotations

import ast
import copy
import textwrap
from typing import List, Sequence, Tuple

import pytest

from towel.unification.block_comments import (
    CommentConflict,
    CommentPlacementError,
    ConflictKind,
    HelperComments,
    Placement,
    SiteComments,
    argument_lines,
    directive_conflict,
    is_directive,
    is_file_directive,
    merge_comments,
    site_comments,
    weave_comments,
)


def _block(source: str, first: int = 0) -> Tuple[str, List[ast.stmt]]:
    """``source`` dedented, and the body of its first function from statement ``first`` on."""
    text = textwrap.dedent(source).lstrip()
    function = ast.parse(text).body[0]
    assert isinstance(function, ast.FunctionDef)
    return text, function.body[first:]


def _helper(block: Sequence[ast.stmt]) -> ast.FunctionDef:
    helper = ast.FunctionDef(
        name="__extracted_func_0",
        args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
        body=[copy.deepcopy(statement) for statement in block],
        decorator_list=[],
        returns=None,
    )
    return ast.fix_missing_locations(helper)


def _merged(helper: ast.FunctionDef, *sites: SiteComments) -> HelperComments:
    merged = merge_comments(helper.body, [(site, True) for site in sites], 0)
    assert isinstance(merged, HelperComments), merged
    return merged


@pytest.mark.parametrize(
    "text",
    [
        "# type: ignore",
        "# type: ignore[arg-type]",
        "# type: List[int]",
        "#type:ignore",
        "# pyright: ignore[reportGeneralTypeIssues]",
        "# noqa",
        "# noqa: E501,W291",
        "# NOQA",
        "# pragma: no cover",
        "# pragma: no branch",
        "# nosec B101",
        "# pylint: disable=invalid-name",
        "# fmt: off",
        "# fmt: skip",
        "# isort: skip",
        "# yapf: disable",
        "# noinspection PyProtectedMember",
        "# mypy: ignore-errors",
        "# flake8: noqa",
        "# something  # noqa: E721",
    ],
)
def test_tool_directives_are_recognized(text: str) -> None:
    assert is_directive(text)


# Every directive form the reader recognizes, checked against each tool's
# documented syntax: (comment, configures its whole file, governs the next line
# of code from a line of its own). An ignore on a line of its own governs the
# next line for ty and ruff (the next logical line), pyre, pyrefly (which reads
# every checker's ``<tool>: ignore`` so, ``type: ignore`` included), Semgrep,
# Fixit and PyCharm's ``noinspection``; ty and pyrefly also accept it trailing.
DIRECTIVE_FORMS = [
    ("# type: ignore", False, True),
    ("# type: ignore[arg-type]", False, True),
    ("#type:ignore", False, True),
    ("# type: ignore[ty:unresolved-attribute]", False, True),
    ("# type: List[int]", False, False),
    ("# pyright: ignore[reportGeneralTypeIssues]", False, True),
    ("# pyright: ignore [reportPrivateUsage, reportGeneralTypeIssues]", False, True),
    ("# pyright: strict", True, False),
    ("# pyright: basic", True, False),
    ("# pyright: standard, reportPrivateUsage=false", True, False),
    ("# mypy: ignore-errors", True, False),
    ('# mypy: disable-error-code="attr-defined"', True, False),
    ("# ty: ignore[unresolved-attribute]", False, True),
    ("# ty:ignore[unresolved-attribute]", False, True),
    ("#ty:ignore", False, True),
    ("# ty : ignore[missing-argument, invalid-argument-type]", False, True),
    ("# ty: ignore  # noqa: E501", False, True),
    ("# pyrefly: ignore", False, True),
    ("# pyrefly: ignore[bad-return]", False, True),
    ("#  pyrefly:  ignore  [  bad-return  ]", False, True),
    ("# pyrefly: ignore-errors", True, False),
    ("# pyrefly: ignore-errors[bad-assignment]", True, False),
    ("# zuban: ignore[attr-defined]", False, True),
    ("# pyre-ignore", False, True),
    ("# pyre-ignore[16]", False, True),
    ("# pyre-fixme[7]: Expected `int` but got `str`.", False, True),
    ("# pyre-strict", True, False),
    ("# pyre-unsafe", True, False),
    ("# pyre-ignore-all-errors", True, False),
    ("# pyre-ignore-all-errors[16]", True, False),
    ("# pytype: disable=attribute-error", False, False),
    ("# pytype: skip-file", True, False),
    ("# noqa", False, False),
    ("# noqa: E501,W291", False, False),
    ("# NOQA", False, False),
    ("# flake8: noqa", True, False),
    ("# flake8: noqa: E501", True, False),
    ("# ruff: noqa", True, False),
    ("# ruff: noqa: F841", True, False),
    ("# ruff: file-ignore[F401, ARG001]", True, False),
    ("# ruff: ignore[ARG001]", False, True),
    ("# ruff: disable[E501]", False, False),
    ("# ruff: enable[E501]", False, False),
    ("# pragma: no cover", False, False),
    ("# pragma: no branch", False, False),
    ("# nosec", False, False),
    ("# nosec B101", False, False),
    ("# pylint: disable=invalid-name", False, False),
    ("# pylint: disable-next=invalid-name", False, True),
    ("# pylint: skip-file", True, False),
    ("# noinspection PyProtectedMember", False, True),
    ("# nosemgrep", False, True),
    ("# nosemgrep: rule-id-1, rule-id-2", False, True),
    ("# lint-ignore: NoInheritFromObject", False, True),
    ("# lint-fixme: NoInheritFromObject", False, True),
    ("# lint-ignore", False, True),
    ("# fmt: off", False, False),
    ("# fmt: skip", False, False),
    ("# yapf: disable", False, False),
    ("# autopep8: off", False, False),
    ("# isort: skip", False, False),
    ("# isort: skip_file", True, False),
    # isort's code-sorting comments sort the statement after them.
    ("# isort: list", False, True),
    ("# isort: unique-tuple", False, True),
    ("# pycln: skip", False, False),
    ("# nopycln: import", False, False),
    ("# codespell:ignore", False, False),
    ("# something  # noqa: E721", False, False),
]


@pytest.mark.parametrize("text, file_wide, next_line", DIRECTIVE_FORMS)
def test_every_directive_form_is_read_as_its_tool_reads_it(
    text: str, file_wide: bool, next_line: bool
) -> None:
    assert is_directive(text)
    assert is_file_directive(text) is file_wide
    # On a line of its own inside a block: does it reach the line after it?
    source, block = _block(f"""
        def f(record):
            total = 0
            {text}
            total += compute(record.alpha)
            return total
        """)
    (comment,) = site_comments(source, block).comments
    assert (comment.reach == {4}) is next_line
    # Above a block's first statement it would govern the call instead.
    starting = site_comments(source, block[1:])
    assert bool(starting.directed_start) is next_line
    if next_line:
        conflict = directive_conflict(block[1:], [starting])
        assert conflict is not None and conflict.kind is ConflictKind.DIRECTIVE_AROUND_BLOCK


def test_an_ignore_inside_brackets_governs_the_next_physical_line_of_code() -> None:
    """ty's rule inside a multi-line logical line; pyrefly's, whatever the line holds."""
    source, block = _block("""
        def f(record):
            total = compute(
                record.alpha,
                # ty: ignore[unresolved-attribute]

                record.beta,
                record.gamma,
            )
            return total
        """)
    (comment,) = site_comments(source, block).comments
    assert comment.reach == {6}


def test_an_ignore_above_a_compound_statement_governs_its_header() -> None:
    source, block = _block("""
        def f(record):
            total = 0
            # ruff: ignore[SIM102]
            if (record.alpha
                    and record.beta):
                total += compute(record.gamma)
            return total
        """)
    (comment,) = site_comments(source, block).comments
    assert comment.reach == {4, 5}


@pytest.mark.parametrize(
    "text",
    [
        "# the return type is an int",
        "# pragmatic choice",
        "# see the formatting notes",
        "# TODO: should this be ''?",
        "# isort would move this",
    ],
)
def test_prose_is_not_a_directive(text: str) -> None:
    assert not is_directive(text)


def test_file_directives_are_told_apart() -> None:
    assert is_file_directive("# flake8: noqa")
    assert is_file_directive("# ruff: noqa: E501")
    assert is_file_directive("# pyright: strict")
    assert not is_file_directive("# pyright: ignore")
    assert not is_file_directive("# noqa: E501")


def test_each_directive_reaches_the_code_its_tool_applies_it_to() -> None:
    source, block = _block("""
        def f(values, other):
            total = 0  # noqa: E741
            for value in values:  # pragma: no cover
                total += value
            # noinspection PyUnresolvedReferences
            result = compute(
                total,
                other,
            )
            check(  # pylint: disable=no-member
                result,
            )
            # fmt: off
            table = [1,2,
                     3,4]
            # fmt: on
            note = 1  # a plain comment
            flag = 2  # flake8: noqa
            return result
        """)
    site = site_comments(source, block)
    reach = {comment.text: comment.reach for comment in site.comments}
    assert reach["# noqa: E741"] == {2}
    # Coverage excludes the loop the header opens, not only its line.
    assert reach["# pragma: no cover"] == {3}
    assert site.excluded == {3, 4}
    assert reach["# noinspection PyUnresolvedReferences"] == {6, 7, 8, 9}
    # pylint's disable on a statement's line covers the whole statement.
    assert reach["# pylint: disable=no-member"] == {10, 11, 12}
    assert reach["# fmt: off"] == reach["# fmt: on"] == {13, 14, 15, 16}
    assert reach["# a plain comment"] == frozenset()
    # A directive for the whole file reaches its module wherever code goes.
    assert reach["# flake8: noqa"] == frozenset()


def test_comments_outside_the_block_belong_to_the_call_site() -> None:
    source, block = _block("""
        def f(x):
            # above the block
            y = x + 1  # on the block's first line
            z = y * 2
            # below the block
            return z
        """)
    comments = site_comments(source, block[:2])
    assert [comment.text for comment in comments.comments] == ["# on the block's first line"]


def test_anchors_name_the_code_beside_each_comment() -> None:
    source, block = _block("""
        def f(values, limit):
            total = 0  # starts empty
            for value in values:
                if value > limit:  # large
                    total += compute(
                        value,  # the value itself
                        # then its limit
                        limit,
                    )
                    # after the large case
                # ahead of else
                else:  # pragma: no cover
                    total -= 1
            return total
        """)
    anchors = {
        comment.text: (comment.anchor.placement, comment.anchor.node, comment.anchor.punctuation)
        for comment in site_comments(source, block).comments
    }
    placement, node, punctuation = anchors["# starts empty"]
    assert placement is Placement.TRAILING and node.statement == 0 and not node.steps
    placement, node, punctuation = anchors["# large"]
    assert placement is Placement.TRAILING and node.steps[-1][:2] == ("test", -1)
    assert punctuation == (":",)
    placement, node, punctuation = anchors["# the value itself"]
    assert placement is Placement.TRAILING and node.steps[-1][:2] == ("args", 0)
    assert punctuation == (",",)
    placement, node, _ = anchors["# then its limit"]
    assert placement is Placement.LEADING and node.steps[-1][:2] == ("args", 1)
    placement, node, _ = anchors["# after the large case"]
    assert placement is Placement.CLOSING and node.steps[-1] == ("body", 0, "AugAssign")
    placement, node, _ = anchors["# ahead of else"]
    assert placement is Placement.ABOVE_HEADER and node.steps[-1] == ("orelse", 0, "AugAssign")
    placement, node, _ = anchors["# pragma: no cover"]
    assert placement is Placement.HEADER and node.steps[-1] == ("orelse", 0, "AugAssign")


def test_a_comment_after_a_multiline_string_ends_its_line() -> None:
    source, block = _block('''
        def f():
            text = """one
        two"""  # the text
            size = len(text)
            return size
        ''')
    (comment,) = site_comments(source, block).comments
    assert comment.anchor.placement is Placement.TRAILING and comment.anchor.node.statement == 0


def test_grouping_parentheses_and_trailing_commas_are_recorded() -> None:
    source, block = _block("""
        def f(key, a, b):
            chosen = (
                a if key else b  # type: ignore
            )
            items = [
                a,  # first
                b,
            ]
            return chosen, items
        """)
    grouped, first = site_comments(source, block).comments
    assert grouped.anchor.group == grouped.anchor.node
    assert grouped.anchor.exploded is None
    assert first.anchor.group is None
    assert first.anchor.exploded is not None and first.anchor.exploded.steps[-1][2] == "List"


def test_explanatory_comments_are_the_union_of_the_sites() -> None:
    source_a, block_a = _block("""
        def f(x):
            w = x
            # shared note
            y = w + 1  # first site's note
            return y
        """)
    source_b, block_b = _block("""
        def g(x):
            w = x
            # shared note
            # second site's note
            y = w + 1
            return y
        """)
    helper = _helper(block_a)
    merged = _merged(helper, site_comments(source_a, block_a), site_comments(source_b, block_b))
    # Each place keeps the first site's comments, then any other site adds.
    assert sorted(comment.text for comment in merged.comments) == [
        "# first site's note",
        "# second site's note",
        "# shared note",
    ]
    text = weave_comments(helper, helper, merged).text
    assert text.split("\n")[2:6] == [
        "    # shared note",
        "    # second site's note",
        "    y = w + 1  # first site's note",
        "    return y",
    ]


def _conflict(first: str, second: str, *, same_module: bool = True) -> CommentConflict:
    source_a, block_a = _block(first)
    source_b, block_b = _block(second)
    result = merge_comments(
        _helper(block_a).body,
        [
            (site_comments(source_a, block_a), True),
            (site_comments(source_b, block_b), same_module),
        ],
        0,
    )
    assert isinstance(result, CommentConflict), result
    return result


def test_a_directive_at_one_site_only_is_a_conflict() -> None:
    conflict = _conflict(
        """
        def f(x):
            y = x + 1  # type: ignore[operator]
            return y
        """,
        """
        def g(x):
            y = x + 1
            return y
        """,
    )
    assert conflict.kind is ConflictKind.DIRECTIVES_DIFFER
    assert "# type: ignore[operator]" in conflict.detail


def test_a_directive_on_another_line_is_a_conflict() -> None:
    conflict = _conflict(
        """
        def f(x):
            y = x + 1  # noqa: E501
            return y
        """,
        """
        def g(x):
            y = x + 1
            return y  # noqa: E501
        """,
    )
    assert conflict.kind is ConflictKind.DIRECTIVES_DIFFER


def test_an_unclosed_region_is_a_conflict() -> None:
    conflict = _conflict(
        """
        def f(x):
            w = x
            # fmt: off
            y = w + 1
            return y
        """,
        """
        def g(x):
            w = x
            # fmt: off
            y = w + 1
            return y
        """,
    )
    assert conflict.kind is ConflictKind.DIRECTIVE_OUTLIVES_BLOCK


def test_a_file_directive_bound_for_another_module_is_a_conflict() -> None:
    site = """
        def f(x):
            y = x + 1  # ruff: noqa: E501
            return y
        """
    conflict = _conflict(site, site, same_module=False)
    assert conflict.kind is ConflictKind.DIRECTIVE_OUTLIVES_BLOCK


def test_an_ignore_over_an_argument_is_a_conflict() -> None:
    source, block = _block("""
        def f(record):
            y = record.alpha.count()  # type: ignore[attr-defined]
            return y
        """)
    statement = block[0]
    assert isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Call)
    site = site_comments(source, block, argument_lines([statement.value.func]))
    result = merge_comments(_helper(block).body, [(site, True), (site, True)], 0)
    assert isinstance(result, CommentConflict)
    assert result.kind is ConflictKind.DIRECTIVE_ON_ARGUMENT


def _expressions(source: str) -> List[ast.expr]:
    return [
        statement.value for statement in ast.parse(source).body if isinstance(statement, ast.Expr)
    ]


def test_names_and_literals_are_not_code_an_ignore_covered() -> None:
    assert argument_lines(_expressions("x\n1\n-1\n'text'")) == frozenset()
    assert argument_lines(_expressions("\nf(x)")) == frozenset({2})


@pytest.mark.parametrize(
    "source",
    [
        """
        def f(values, limit):
            total = sum(  # all of them
                values,
            )
            for value in values:  # each
                # a note
                total += (value +  # the value
                          limit)
            else:
                pass  # nothing
            return total  # done
        """,
        """
        def f(data):
            try:  # may fail
                parsed = load(data)
            except (ValueError,  # bad value
                    TypeError):
                parsed = None
            # between
            finally:  # always
                close(data)
            return {
                # the key
                "parsed": parsed,
            }
        """,
    ],
)
def test_writing_the_comments_keeps_the_rendered_tree(source: str) -> None:
    text, block = _block(source)
    helper = _helper(block)
    merged = _merged(helper, site_comments(text, block))
    woven = weave_comments(helper, helper, merged)
    assert ast.dump(ast.parse(woven.text)) == ast.dump(ast.parse(ast.unparse(helper)))
    written = [line.split("#", 1)[1].strip() for line in woven.text.split("\n") if "#" in line]
    original = [line.split("#", 1)[1].strip() for line in text.split("\n") if "#" in line]
    assert written == original


def test_a_formatter_that_splits_a_directive_from_its_code_is_detected() -> None:
    text, block = _block("""
        def f(values):
            total = compute(values, 1, 2)  # pyright: ignore
            return total
        """)
    helper = _helper(block)
    woven = weave_comments(helper, helper, _merged(helper, site_comments(text, block)))
    assert woven.keeps_directives(woven.text)
    joined = "def __extracted_func_0():\n    total = compute(values, 1, 2)  # pyright: ignore\n"
    assert woven.keeps_directives(joined + "    return total\n")
    split = (
        "def __extracted_func_0():\n    total = compute(\n        values, 1, 2\n"
        "    )  # pyright: ignore\n    return total\n"
    )
    assert not woven.keeps_directives(split)


def test_a_helper_after_its_type_declarations_is_found_and_woven() -> None:
    text, block = _block("""
        def f(items):
            first = items[0]  # the head
            return first
        """)
    helper = _helper(block)
    module = ast.Module(body=[ast.parse("T = TypeVar('T')").body[0], helper], type_ignores=[])
    woven = weave_comments(module, helper, _merged(helper, site_comments(text, block)))
    assert woven.text.split("\n")[0] == "T = TypeVar('T')"
    assert "    first = items[0]  # the head" in woven.text.split("\n")


def test_a_helper_that_does_not_render_to_itself_is_refused() -> None:
    text, block = _block("""
        def f(items):
            first = items[0]  # the head
            return first
        """)
    helper = _helper(block)
    merged = _merged(helper, site_comments(text, block))
    other = _helper(block[1:])
    with pytest.raises(CommentPlacementError):
        weave_comments(other, helper, merged)


def test_another_sites_note_where_the_first_ends_its_line_goes_above() -> None:
    """tornado's queues test: two different notes on one line read as one long comment."""
    source_a, block_a = _block("""
        def f(waiter):
            done = waiter.done()
            check(done)  # Final waiter is still active.
            return done
        """)
    source_b, block_b = _block("""
        def g(waiter):
            done = waiter.done()
            check(done)  # Final waiters still active.
            return done
        """)
    helper = _helper(block_a)
    merged = _merged(helper, site_comments(source_a, block_a), site_comments(source_b, block_b))
    lines = weave_comments(helper, helper, merged).text.split("\n")
    assert lines[2:4] == [
        "    # Final waiters still active.",
        "    check(done)  # Final waiter is still active.",
    ]


def test_a_note_on_an_argument_a_parameter_replaced_stays_beside_the_parameter() -> None:
    """docutils' tex2mathml: the second site's notes on its own flags."""
    source_a, block_a = _block("""
        def f():
            args = ['pandoc',
                    '--mathml',
                    '--from=latex',
                    ]
            return args
        """)
    source_b, block_b = _block("""
        def g():
            args = ['ttm',
                    '-L',  # source is LaTeX snippet
                    '-r']  # output MathML snippet
            return args
        """)
    helper = _helper(block_a)
    # The elements differ, so the helper holds a parameter in each's place.
    assignment = helper.body[0]
    assert isinstance(assignment, ast.Assign) and isinstance(assignment.value, ast.List)
    assignment.value.elts = [ast.Name(id=f"__param_{index}", ctx=ast.Load()) for index in range(3)]
    ast.fix_missing_locations(helper)
    merged = _merged(helper, site_comments(source_a, block_a), site_comments(source_b, block_b))
    text = weave_comments(helper, helper, merged).text
    assert "__param_1,  # source is LaTeX snippet" in text, text
    assert "]  # output MathML snippet" in text
