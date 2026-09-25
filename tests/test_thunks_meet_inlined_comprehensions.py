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

"""A call-site thunk is declined where an inlined comprehension would empty its cell.

From Python 3.12 a list, set or dict comprehension is compiled into the
frame of its function (PEP 709). Where that function reads a name from an
enclosing function and such a comprehension binds the same name, a lambda
of the function that reads the name raises ``NameError`` on 3.12 and 3.13.
The round-4 binding forms of the differential grammar found Towel handing a
helper exactly such a thunk (seeds 302, 409, 1209, 1450); the block itself
had no lambda, so the program had run.
"""

from __future__ import annotations

import ast
import sys
import textwrap
from typing import Dict, FrozenSet, Tuple

import pytest

from towel.unification.scope_analyzer import ScopeAnalyzer
from towel.unification.semantic_safety import thunk_meets_an_inlined_comprehension

SOURCES: Dict[str, Tuple[str, FrozenSet[str]]] = {
    "enclosing_cell_rebound_by_a_list_comprehension": (
        """
        def outer(scale, rows):
            def site():
                doubled = [scale * 2 for scale in rows]
                return doubled
        """,
        frozenset({"scale"}),
    ),
    "set_and_dict_comprehensions_too": (
        """
        def outer(scale, rows):
            def site():
                a = {scale for scale in rows}
                b = {scale: 1 for scale in rows}
                return a, b
        """,
        frozenset({"scale"}),
    ),
    "a_generator_expression_has_its_own_frame": (
        """
        def outer(scale, rows):
            def site():
                return list(scale * 2 for scale in rows)
        """,
        frozenset(),
    ),
    "the_functions_own_local": (
        """
        def site(scale, rows):
            doubled = [scale * 2 for scale in rows]
            return doubled
        """,
        frozenset(),
    ),
    "a_module_name": (
        """
        scale = 3
        def site(rows):
            doubled = [scale * 2 for scale in rows]
            return doubled
        """,
        frozenset(),
    ),
    "a_comprehension_inside_a_lambda_is_the_lambdas": (
        """
        def outer(scale, rows):
            def site():
                make = lambda: [scale for scale in rows]
                return make
        """,
        frozenset(),
    ),
}


@pytest.mark.parametrize("case", sorted(SOURCES))
def test_r9sb_a_thunk_reading_a_name_an_inlined_comprehension_rebinds(case: str) -> None:
    source, expected = SOURCES[case]
    tree = ast.parse(textwrap.dedent(source))
    analyzer = ScopeAnalyzer()
    analyzer.analyze(tree)
    site = next(
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "site"
    )
    call = ast.parse("helper(lambda: scale, rows)").body[0]
    assert thunk_meets_an_inlined_comprehension(call, site, analyzer) == expected
    no_thunk = ast.parse("helper(scale, rows)").body[0]
    assert thunk_meets_an_inlined_comprehension(no_thunk, site, analyzer) == frozenset()


def test_r9sb_the_interpreter_empties_the_cell_from_3_12() -> None:
    """The reason for the rule, on the interpreter running the suite."""
    namespace: Dict[str, object] = {}
    exec(  # noqa: S102 - the program is the test's own constant
        textwrap.dedent("""
        def outer(scale):
            def site():
                doubled = [scale * 2 for scale in [1]]
                return (lambda: scale)(), doubled
            return site()
        """),
        namespace,
    )
    outer = namespace["outer"]
    assert callable(outer)
    if sys.version_info >= (3, 12):
        with pytest.raises(NameError):
            outer(5)
    else:
        assert outer(5) == (5, [2])
