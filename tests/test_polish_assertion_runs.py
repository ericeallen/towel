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

"""Re-audit problem 2: a repeated assertion run is not one useful operation."""

import ast
from pathlib import Path

import pytest

from towel.unification.models import Replacement
from towel.unification.profitability import worthwhile_helper
from towel.unification.refactor_engine import UnificationRefactorEngine


@pytest.mark.parametrize("surrounding", ["prefix", "suffix", "both", "neither"])
@pytest.mark.parametrize("literal", [False, True])
def test_repeated_statement_runs_are_not_bundled(surrounding: str, literal: bool) -> None:
    """Re-audit 2: same-shaped assertions are refused even inside a mixed body."""
    body = ["assert expected == config(first)", "assert expected == config(second)"]
    if literal:
        body = ["assert True == config('yes')", "assert True == config('on')"]
    if surrounding in ("prefix", "both"):
        body.insert(0, "config.reset()")
    if surrounding in ("suffix", "both"):
        body.append("assert expected == config(last, default=default)")
    node = ast.parse(
        "def helper(config, expected, first, second, last, default):\n    " + "\n    ".join(body)
    ).body[0]
    assert isinstance(node, ast.FunctionDef)
    before = ast.dump(node)
    sites = [Replacement((i, i), ast.parse("helper(c, e, a, b, z, d)").body[0]) for i in range(8)]
    assert not worthwhile_helper(node, sites)
    assert ast.dump(node) == before


def test_single_assertion_is_still_a_useful_helper() -> None:
    """Re-audit 2: the natural one-case abstraction remains eligible."""
    node = ast.parse("def helper(config, key):\n    assert config(key, cast=bool) is True").body[0]
    assert isinstance(node, ast.FunctionDef)
    sites = [Replacement((i, i), ast.parse("helper(c, k)").body[0]) for i in range(8)]
    assert worthwhile_helper(node, sites)


def test_boolean_test_tails_stay_visible(tmp_path: Path) -> None:
    """Re-audit 2: differing boolean test lengths must not select arbitrary tails."""
    source = ""
    for name, expected, keys in (
        ("yes", "True", ("yes", "on", "one", "y", "true")),
        ("no", "False", ("no", "off", "zero", "n", "false", "empty")),
    ):
        source += f"def test_{name}(config):\n"
        source += "".join(f"    assert {expected} is config({key!r}, cast=bool)\n" for key in keys)
        source += f"    assert {expected} is config('integer', default={int(expected == 'True')}, cast=bool)\n\n"
    path = tmp_path / "subject.py"
    path.write_text(source)
    engine = UnificationRefactorEngine()
    output, _, _ = engine.refactor_to_fixed_point(
        str(path), output_path=str(tmp_path / "out.py"), progress="none"
    )
    helpers = [
        node
        for node in ast.walk(ast.parse(output))
        if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name
    ]
    assert all(sum(isinstance(stmt, ast.Assert) for stmt in helper.body) <= 1 for helper in helpers)


@pytest.mark.parametrize("wrapper", ["if ready:", "try:", "for ready in flags:"])
def test_nested_statement_runs_are_not_bundled(wrapper: str) -> None:
    """Re-audit 2: enclosing control flow must not hide an arbitrary assertion bundle."""
    source = (
        "def helper(config, first, second, expected, ready, flags):\n"
        f"    {wrapper}\n"
        "        assert expected == config(first)\n"
        "        assert expected == config(second)\n"
    )
    if wrapper == "try:":
        source += "    finally:\n        config.close()\n"
    helper = ast.parse(source).body[0]
    assert isinstance(helper, ast.FunctionDef)
    sites = [
        Replacement((index, index), ast.parse("helper(c, a, b, e, r, f)").body[0])
        for index in range(20)
    ]
    assert not worthwhile_helper(helper, sites)
