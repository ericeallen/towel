# Copyright 2026 Eric Allen
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

"""What the guards are told about where an expression candidate stands.

Audit case 44 made an expression inside a statement a candidate, and the
engine judges it as the one-statement block ``return E``. The guards ask
where a block stands in its function: what is bound on every path to it,
what it binds, which names its function resolves. For an expression those
answers come from its statement's header and from the comprehension around
it, not from a statement of the function's body, and this module pins them.
The hostile programs that rest on them are in
``tests/test_expression_guards.py``.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path
from typing import List

from towel.api import UnificationRefactorEngine
from towel.unification.expression_sites import (
    comprehension_locals,
    expression_sites,
    standing_block,
)
from towel.unification.semantic_safety import (
    bound_where_block_stands,
    function_scope_names,
    nested_bindings_escape,
)
from tests.test_candidate_index import analyzed_functions

CONDITION = "(\n            a.one\n            and b.two\n            and c.three\n        )"


def _conditions(function: ast.AST) -> List[List[ast.stmt]]:
    """The standing block of each ``and`` among the expression candidates of ``function``."""
    assert isinstance(function, ast.FunctionDef)
    return [
        [standing_block(site)]
        for site in expression_sites(function, 3)
        if isinstance(site.expression, ast.BoolOp)
    ]


def _bound_at(source: str) -> List[List[str]]:
    """For each condition of the first function, the names bound where it stands."""
    function = ast.parse(textwrap.dedent(source)).body[0]
    assert isinstance(function, ast.FunctionDef)
    return [sorted(bound_where_block_stands(function, block)) for block in _conditions(function)]


def test_the_names_bound_where_an_expression_stands() -> None:
    """What a call written in the expression's place may read without raising.

    The names bound on every path to the statement, less what a loop's body
    may delete before its test runs again, plus a guard's captures and the
    variables of the comprehension clauses that have run, less those of the
    clauses that have not: they hide the function's own.
    """
    assert _bound_at(f"""
    def f(a, flag):
        if flag:
            b = 1
        c = 2
        return {CONDITION}
    """) == [["a", "c", "flag"]]
    assert _bound_at(f"""
    def f(a, b, c):
        while {CONDITION}:
            del b
        for item in a:
            if {CONDITION}:
                del c
    """) == [["a", "c"], ["a", "item"]]
    assert _bound_at(f"""
    def f(a, c):
        match a:
            case [b, *rest] if {CONDITION}:
                pass
    """) == [["a", "b", "c", "rest"]]
    assert _bound_at(f"""
    def f(a, rows, c):
        return [
            1
            for b in rows
            if {CONDITION}
            for c in b
            if {CONDITION}
        ]
    """) == [["a", "b", "rows"], ["a", "b", "c", "rows"]]


def test_a_statement_block_is_bound_as_before() -> None:
    """Statements are placed by their first statement, as they always were."""
    function = ast.parse("def f(a):\n    b = a\n    c = b\n    return c\n").body[0]
    assert isinstance(function, ast.FunctionDef)
    assert bound_where_block_stands(function, function.body[1:]) == {"a", "b"}
    assert comprehension_locals(function.body[1:]) == frozenset()
    assert bound_where_block_stands(function, []) == set()


def test_a_comprehensions_variables_are_its_own_where_an_expression_stands(
    tmp_path: Path,
) -> None:
    """They are not the module's names, whatever the module binds; the helper must be given them."""
    path = tmp_path / "m.py"
    path.write_text(
        'b = "the module\'s"\n\n\n'
        "def f(a, rows, c):\n    return [\n        1\n        for b in rows\n"
        f"        if {CONDITION}\n    ]\n\n\n"
        f"def g(a, c):\n    return {CONDITION}\n"
    )
    inside, outside = analyzed_functions([str(path)])
    (block,) = _conditions(inside.node)
    assert comprehension_locals(block) == {"b"}
    assert function_scope_names(inside.node, inside.scope_analyzer, block) == {"a", "b", "c"}
    # Read from a function that does not bind it, ``b`` is the module's name.
    (plain,) = _conditions(outside.node)
    assert function_scope_names(outside.node, outside.scope_analyzer, plain) == {"a", "c"}


def test_an_expression_binds_nothing_in_its_function(tmp_path: Path) -> None:
    """A comprehension's target inside the expression is not a variable later code could miss."""
    path = tmp_path / "m.py"
    path.write_text(textwrap.dedent("""
            def f(rows, item):
                if item:
                    found = [
                        item.name
                        for item in rows
                        if item.ok
                    ]
                    return found, item
                return item
            """))
    (function,) = analyzed_functions([str(path)])
    (site,) = expression_sites(function.node, 3)
    block: List[ast.stmt] = [standing_block(site)]
    assert not nested_bindings_escape(function.node, block)
    # The assignment that holds it does bind a name read later.
    assert nested_bindings_escape(function.node, [site.anchor])
    engine = UnificationRefactorEngine()
    snapshot = engine._compute_block_binding_snapshot(function.node, block, (4, 8), {})
    assert not snapshot.bound_in_block and not snapshot.initially_bound
    assert {"rows", "item"} <= snapshot.bound_before_block
