"""A helper that computes nothing is declined, whatever skip_trivial_helpers says.

Two ``return`` statements whose whole expressions differ unify to ``return
__param_0``: rich's ``Tag.markup`` and ``MofNCompleteColumn.render`` became
``def _extracted_func_24(__param_0, self): return __param_0``, which each site
called with its own expression, from another module in rich's case. Such a
helper shares no operation with its sites, only the ``return``, as a pair of
blocks that each return a name they were given never did; click, rich,
packaging and pygments each had one. A helper that shares an operation, however
small, is still extracted, and one that only forwards to a call is still
governed by ``skip_trivial_helpers``.
"""

from __future__ import annotations

import ast
from pathlib import Path
import textwrap

import pytest

from tests.test_helpers import function_def, write_module
from towel.unification.block_analysis import BlockAnalysis
from towel.unification.refactor_engine import UnificationRefactorEngine

RICH_PAIR = '''
class Text:
    def __init__(self, text, style=""):
        self.text = text
        self.style = style


class Tag:
    @property
    def markup(self):
        """Get the string representation of this tag."""
        return (
            f"[{self.name}]"
            if self.parameters is None
            else f"[{self.name}={self.parameters}]"
        )


class Column:
    def render(self, task):
        """Show completed/total."""
        completed = int(task.completed)
        total = int(task.total) if task.total is not None else "?"
        total_width = len(str(total))
        return Text(
            f"{completed:{total_width}d}{self.separator}{total}",
            style="progress.download",
        )
'''

TUPLE_PAIR = """
def first(left, top):
    return (
        left,
        top,
    )


def second(width, height):
    return (
        width,
        height,
    )
"""


@pytest.mark.parametrize("skip_trivial_helpers", [True, False])
@pytest.mark.parametrize("source", [RICH_PAIR, TUPLE_PAIR], ids=["value", "tuple"])
def test_unrelated_returns_get_no_helper(
    tmp_path: Path, source: str, skip_trivial_helpers: bool
) -> None:
    path = write_module(tmp_path, source)
    engine = UnificationRefactorEngine(skip_trivial_helpers=skip_trivial_helpers)
    assert engine.analyze_file(path) == []


def test_blocks_that_each_return_a_name_they_were_given_get_no_helper(tmp_path: Path) -> None:
    path = write_module(
        tmp_path,
        "def first(value):\n    return value\n\n\ndef second(other):\n    return other\n",
    )
    for skip in (True, False):
        assert (
            UnificationRefactorEngine(min_lines=1, skip_trivial_helpers=skip).analyze_file(path)
            == []
        )


def _helper(body: str) -> ast.FunctionDef:
    return function_def(
        "def helper(self, __param_0, __param_1, value):\n" + textwrap.indent(body, "    ")
    )


@pytest.mark.parametrize(
    "body",
    [
        "return __param_0",
        "return (__param_0, self)",
        "return (value, (__param_1, 0))",
        "return",
        "return None",
        "global total\nreturn __param_0",
        # The thunks the sites pass are their own code, evaluated.
        "return __param_0()",
        "return (__param_0(), __param_1)",
        "__param_0()",
    ],
)
def test_a_helper_that_hands_back_what_it_is_given_computes_nothing(body: str) -> None:
    assert BlockAnalysis._helper_computes_nothing(_helper(body))


@pytest.mark.parametrize(
    "body",
    [
        # An operation of the user's own, however small.
        "return __param_0.name",
        "return __param_0 + 1",
        "return not __param_0",
        "return [__param_0]",
        "return (*__param_0,)",
        "return __param_0(value)",
        "return forward(__param_0)",
        "raise __param_0",
        "if __param_0:\n    return value\nreturn __param_1",
        # Binding names is the renaming filter's concern, which the setting governs.
        "total = __param_0\nreturn total",
    ],
)
def test_a_helper_that_runs_an_operation_computes_something(body: str) -> None:
    assert not BlockAnalysis._helper_computes_nothing(_helper(body))
