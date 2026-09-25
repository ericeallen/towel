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

"""A parameter's value must be Python on its own, outside the subscript it came from.

A slice is written only as a subscript's index or an element of the tuple
that is one, so ``a[0, :50]`` holds ``(0, :50)``, which is no expression
anywhere else. Round 4's real-code audit found numbagg's ``data[0, :50] =
np.nan`` against ``data[1] = ...`` passed to a helper as ``lambda: (0,
:50)``: with a formatter every directory run failed on it, and without one
the proposal was built only to be dropped as "could not be rendered".
"""

from __future__ import annotations

import ast
from pathlib import Path
import textwrap
from typing import Dict, List

import pytest

from towel.unification.parameterization import _parameterizable
from towel.unification.refactor_engine import UnificationRefactorEngine


def _index(source: str) -> ast.expr:
    """The index of the subscript ``source``, as the unifier meets it."""
    subscript = ast.parse(source, mode="eval").body
    assert isinstance(subscript, ast.Subscript)
    return subscript.slice


@pytest.mark.parametrize(
    "value, stands_alone",
    [
        (_index("a[0, :50]"), False),
        (_index("a[:50]"), False),
        (_index("a[0, 1:2:3]"), False),
        (_index("a[0, 1]"), True),
        (_index("a[b[1:2], 3]"), True),
        (_index("a[b[0, :2], 3]"), True),
        (ast.parse("a[0, :50]", mode="eval").body, True),
    ],
    ids=[
        "tuple-with-slice",
        "slice",
        "tuple-with-step",
        "tuple",
        "inner-subscript",
        "inner-tuple-subscript",
        "whole-subscript",
    ],
)
def test_r9p2_a_value_holding_a_stray_slice_is_never_a_parameter(
    value: ast.expr, stands_alone: bool
) -> None:
    """Parameterizable exactly when the value, as Towel writes it, parses outside its subscript."""
    try:
        ast.parse(f"lambda: {ast.unparse(value)}", mode="eval")
        parses = True
    except SyntaxError:
        parses = False
    assert parses is stands_alone
    assert _parameterizable([value, ast.Constant(value=1)]) is stands_alone


R9P2_GRID = textwrap.dedent("""
    import math


    class Grid:
        def __init__(self, shape):
            self.shape = shape
            self.cells = {}

        def astype(self, kind):
            return self

        def __setitem__(self, key, value):
            self.cells[repr(key)] = value


    class Rng:
        def __init__(self, seed):
            self.seed = seed

        def standard_normal(self, shape):
            return Grid(shape)


    def masked_rows(dtype):
        rng = Rng(0)
        data = rng.standard_normal((4, 300)).astype(dtype)
        data[0, :50] = math.nan
        data[1, 100:160] = math.nan
        print("masked", sorted(data.cells))
        return len(data.cells)


    def constant_row(dtype):
        rng = Rng(0)
        data = rng.standard_normal((3, 100)).astype(dtype)
        data[1] = dtype(1e8)
        print("constant", data.shape, sorted(data.cells))
        return data.shape
    """)


def test_r9p2_numbaggs_tuple_index_builds_no_proposal_that_cannot_render(tmp_path: Path) -> None:
    source = tmp_path / "r9p2_grid"
    source.mkdir()
    (source / "grid.py").write_text(R9P2_GRID)
    engine = UnificationRefactorEngine()
    engine.refactor_directory_to_fixed_point(str(source), str(tmp_path / "out"), progress="none")
    assert "could not be rendered" not in engine.run_report.declined_proposals
    assert (tmp_path / "out" / "grid.py").read_text() == R9P2_GRID


def test_r9p2_tuple_indices_differing_inside_still_share_a_helper(tmp_path: Path) -> None:
    """The slice's own bounds are values: only a tuple holding a slice is refused whole."""
    source = tmp_path / "r9p2_rows"
    source.mkdir()
    module = textwrap.dedent("""
        def first(data):
            data[0, :50] = 1
            total = sum(data.values())
            print("first", total)
            return total


        def second(data):
            data[1, :60] = 1
            total = sum(data.values())
            print("first", total)
            return total
        """)
    (source / "rows.py").write_text(module)
    engine = UnificationRefactorEngine(min_lines=2)
    engine.refactor_directory_to_fixed_point(str(source), str(tmp_path / "out"), progress="none")
    written = (tmp_path / "out" / "rows.py").read_text()
    assert "__extracted_func_0" in written

    class Recording(Dict[str, object]):
        def __setitem__(self, key: object, value: object) -> None:
            super().__setitem__(repr(key), value)

    def run(text: str) -> List[object]:
        namespace: Dict[str, object] = {}
        exec(compile(text, "rows.py", "exec"), namespace)
        results: List[object] = []
        for name in ("first", "second"):
            data = Recording()
            function = namespace[name]
            assert callable(function)
            results.append((function(data), sorted(data)))
        return results

    assert run(written) == run(module)
