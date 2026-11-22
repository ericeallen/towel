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

"""A block repeated inside one function is a candidate, as a block repeated in two is.

Audit finding 2 (engine map E, scenario 34): three four-line stanzas of one
function got "No duplicates found!", because blocks paired only with blocks
of another function. Since 2.0 two blocks of one function pair when they
share no line, and every guard a pair of two functions meets applies to
them unchanged. The helper goes where a helper for two functions of that
scope would go.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path
from typing import List, Tuple

import pytest

from towel.unification.pipeline import analyze_scopes, collect_functions, parse_modules
from towel.unification.refactor_engine import UnificationRefactorEngine
from tests.test_callback_parameters import helper_calls, refactored, refactored_scenario


def _helpers(source: str) -> List[ast.FunctionDef]:
    return [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name
    ]


@pytest.mark.parametrize("strict", [False, True], ids=["relaxed", "strict"])
def test_config_stanzas_in_one_function_share_a_helper(tmp_path: Path, strict: bool) -> None:
    """Audit scenario 34: ``if parser.has_option(...)`` stanzas repeated in ``load_settings``.

    Two stanzas share a helper of four parameters. The third reads another
    section under another key, which takes two more parameters, and that
    helper does not repay itself.
    """
    generated, same = refactored_scenario(tmp_path, "reach/34_config_stanzas", strict=strict)
    assert same
    (helper,) = _helpers(generated)
    assert isinstance(ast.parse(generated).body[2], ast.FunctionDef)  # module level
    assert helper_calls(generated) == [
        f"{helper.name}(parser, 'host', settings, '127.0.0.1')",
        f"{helper.name}(parser, 'root', settings, '/srv/www')",
    ]
    assert 'parser.has_option("logging", "file")' in generated


NESTED = """
def outer(rows):
    def inner(table):
        if "a" in rows:
            table["a"] = rows["a"].strip()
        else:
            table["a"] = "none"
        if "b" in rows:
            table["b"] = rows["b"].strip()
        else:
            table["b"] = "nothing"
        return table
    return inner({})


print(outer({"a": " 1 "}))
"""


def test_a_nested_function_s_helper_goes_into_its_enclosing_function(tmp_path: Path) -> None:
    """Where a helper for two functions nested in ``outer`` would go: inside ``outer``."""
    generated, same = refactored(tmp_path, NESTED)
    assert same
    outer = ast.parse(generated).body[0]
    assert isinstance(outer, ast.FunctionDef)
    assert [type(node).__name__ for node in outer.body] == ["FunctionDef", "FunctionDef", "Return"]
    assert "extracted_func" in outer.body[1].name  # type: ignore[attr-defined]
    assert len(helper_calls(generated)) == 2


METHOD = """
class Settings:
    def load(self, rows, table):
        if "a" in rows:
            table["a"] = rows["a"].strip()
        else:
            table["a"] = "none"
        if "b" in rows:
            table["b"] = rows["b"].strip()
        else:
            table["b"] = "nothing"
        return table


print(Settings().load({"a": " 1 "}, {}))
"""


def test_a_method_s_helper_goes_where_one_for_two_methods_would(tmp_path: Path) -> None:
    """The block reads no receiver, so, as for two methods of the class, a module function."""
    generated, same = refactored(tmp_path, METHOD)
    assert same
    module = ast.parse(generated)
    assert isinstance(module.body[0], ast.FunctionDef) and "extracted_func" in module.body[0].name
    assert len(helper_calls(generated)) == 2


REASSIGNED = """
def totals(rows, table):
    total = 0
    for row in rows:
        total += row["a"]
    table["a"] = total
    total = 0
    for row in rows:
        total += row["b"]
    table["b"] = total
    return table


print(totals([{"a": 1, "b": 2}], {}))
"""


def test_the_guards_of_two_functions_apply_inside_one(tmp_path: Path) -> None:
    """The second stanza reassigns ``total``, bound by the first: declined as for two functions."""
    path = tmp_path / "totals.py"
    path.write_text(REASSIGNED)
    engine = UnificationRefactorEngine(min_lines=3, skip_trivial_helpers=False)
    assert engine.analyze_file(str(path)) == []
    assert set(engine.declined_pairs) <= {
        "unsafe_reassignment_block1",
        "unsafe_reassignment_block2",
    }
    assert engine.declined_pairs


def _same_function_pairs(
    tmp_path: Path, source: str, min_lines: int
) -> List[Tuple[int, int, int, int]]:
    path = tmp_path / "module.py"
    path.write_text(textwrap.dedent(source))
    functions = collect_functions(analyze_scopes(parse_modules([str(path)])))
    engine = UnificationRefactorEngine(min_lines=min_lines)
    return [
        (*pair.block1_range, *pair.block2_range)
        for pair in engine.find_block_pairs(functions, progress="none")
        if pair.function1_node is pair.function2_node
    ]


def test_blocks_of_one_function_pair_only_when_they_share_no_line(tmp_path: Path) -> None:
    """Earlier block first, disjoint lines: overlapping or nested blocks never pair."""
    pairs = _same_function_pairs(
        tmp_path,
        """
        def f(x):
            a = x + 1; b = a + 2
            c = b + 3
            d = c + 4
            return d
        """,
        min_lines=1,
    )
    assert pairs
    assert all(end1 < start2 for _, end1, start2, _ in pairs)
    assert (3, 3, 4, 4) in pairs  # c = …, then d = …
    assert not any(start1 == 2 and start2 == 2 for start1, _, start2, _ in pairs)


def test_a_function_too_short_for_two_blocks_is_not_paired_with_itself(tmp_path: Path) -> None:
    assert (
        _same_function_pairs(tmp_path, "def f(x):\n    a = x + 1\n    return a\n", min_lines=2)
        == []
    )


def test_the_proposal_says_the_code_is_repeated_in_one_function(tmp_path: Path) -> None:
    path = tmp_path / "settings.py"
    path.write_text(NESTED)
    engine = UnificationRefactorEngine(min_lines=3, skip_trivial_helpers=False)
    (proposal,) = engine.analyze_file(str(path))
    assert proposal.description == "Extract code repeated in inner"


def test_cap_restricts_only_pairs_inside_one_function(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Final-speed lane: bound a repetitive function without losing its matches in other functions."""
    from towel.unification import refactor_engine

    path = tmp_path / "repeated.py"
    body = "".join(f"    value = {index} + 1\n" for index in range(8))
    path.write_text(f"def many():\n{body}\ndef other():\n{body}")
    functions = collect_functions(analyze_scopes(parse_modules([str(path)])))
    engine = UnificationRefactorEngine(min_lines=1, expression_candidates=False)
    all_pairs = engine.find_block_pairs(functions, progress="none")
    all_within = [p for p in all_pairs if p.function1_node is p.function2_node]
    limit = 5
    kept = {
        f.node: set(
            sorted(
                {
                    span
                    for p in all_within
                    if p.function1_node is f.node
                    for span in (p.block1_range, p.block2_range)
                }
            )[:limit]
        )
        for f in functions
    }
    expected = [
        p
        for p in all_pairs
        if p.function1_node is not p.function2_node
        or (p.block1_range in kept[p.function1_node] and p.block2_range in kept[p.function1_node])
    ]
    assert 0 < len(expected) < len(all_pairs)
    monkeypatch.setattr(refactor_engine, "MAX_SAME_FUNCTION_WINDOWS", limit, raising=False)
    assert engine.find_block_pairs(functions, progress="none") == expected
    # Project the global pair budget after applying the function cap. Otherwise
    # a capped function could still evict a bucket needed by two other functions.
    engine.max_candidate_pairs = len(expected)
    assert engine.find_block_pairs(functions, progress="none") == expected
