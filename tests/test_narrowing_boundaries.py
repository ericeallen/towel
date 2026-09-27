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

"""Caller refinements constrain boundaries before expensive typed materialization.

The positive examples matter as much as the refusals: a larger invalid window
must not hide useful code within the narrowed region. See docs/DECISIONS.md,
2026-09-27, for the policy this suite protects.

These expectations are approved design requirements. A changed proposal list
is not permission to change them. First show that the test contradicts the
recorded rule, or record a separately agreed rule change (CONTRIBUTING.md,
"Preserve the intent of policy regressions"). Keep the rationale with the
test. Negative examples prevent wasted typed validation; positive examples
prevent achieving a lower failure count by losing valid work.
"""

from __future__ import annotations

import ast
from pathlib import Path
import textwrap

import pytest

from tests.typed_fixtures import CountingMypy, STRICT, apply_one, requires_mypy
from towel.unification.narrowing import caller_narrowing_leaves_with_block
from towel.unification.refactor_engine import UnificationRefactorEngine

PACKAGING = """
def _key(a: int, b: int) -> tuple[int, int]:
    return (a, b)

class Version:
    def __init__(self, a: int, b: int) -> None:
        self.a = a
        self.b = b
        self._key_cache: tuple[int, int] | None = None

    def __lt__(self, other: object) -> bool:
        if isinstance(other, Version):
            if self._key_cache is None:
                self._key_cache = _key(self.a, self.b)
            if other._key_cache is None:
                other._key_cache = _key(other.a, other.b)
            return self._key_cache < other._key_cache
        return NotImplemented

    def __le__(self, other: object) -> bool:
        if isinstance(other, Version):
            if self._key_cache is None:
                self._key_cache = _key(self.a, self.b)
            if other._key_cache is None:
                other._key_cache = _key(other.a, other.b)
            return self._key_cache <= other._key_cache
        return NotImplemented
"""


@requires_mypy
def test_packaging_cache_initialization_never_reaches_signature_validation(tmp_path: Path) -> None:
    """Avoid the six repeated failing checks measured on packaging, before paying for them.

    Catching a type error during application would preserve soundness but defeat
    this performance requirement. The positive extraction below prevents
    satisfying this test by disabling discovery."""
    path = tmp_path / "version.py"
    path.write_text(textwrap.dedent(PACKAGING))
    (tmp_path / "pyproject.toml").write_text(STRICT)
    checker = CountingMypy()
    try:
        engine = UnificationRefactorEngine(
            min_lines=2, reuse_existing_functions=False, type_oracle=checker
        )
        assert engine.analyze_file(str(path)) == []
        # Discovery may establish the original baseline, but must never ask
        # the project checker about an extracted helper for this shape.
        assert all(
            "__extracted_func" not in text for check in checker.checked for text in check.values()
        )
    finally:
        checker.close()


INNER = """
class Cache:
    def __init__(self) -> None:
        self.value: int | None = None

    def first(self, value: int, items: list[int]) -> int:
        if self.value is None:
            items.append(value * 2)
            items.append(value + 7)
            items.sort()
            self.value = sum(items)
        return self.value + 1

    def second(self, value: int, items: list[int]) -> int:
        if self.value is None:
            items.append(value * 2)
            items.append(value + 7)
            items.sort()
            self.value = sum(items)
        return self.value * 2
"""


@requires_mypy
def test_extract_inside_guard_keeps_both_test_and_refining_assignment(tmp_path: Path) -> None:
    """Keep useful duplication removal, the caller's proof, and one successful typed check.

    Keeping only the condition is insufficient: the assignment establishes
    the nonoptional type on that branch. Rejecting the entire method also loses
    valid work: the list computation can move while both statements stay."""
    outcome = apply_one(tmp_path, INNER, pick="first and second")
    assert outcome.error is None, outcome.error
    assert outcome.module is not None
    assert outcome.prospective_checks == 1, outcome.checked_helpers
    tree = ast.parse(outcome.module)
    for method in ast.walk(tree):
        if not isinstance(method, ast.FunctionDef) or method.name not in {"first", "second"}:
            continue
        guard = method.body[0]
        assert isinstance(guard, ast.If)
        assert ast.unparse(guard.test) == "self.value is None"
        assert ast.unparse(guard.body[-1]) == "self.value = sum(items)"
        assert "extracted_func" in ast.unparse(guard.body[0])
    for source in (INNER, outcome.module):
        namespace: dict[str, object] = {}
        exec(source + "\nresult = (Cache().first(3, []), Cache().second(3, []))", namespace)
        assert namespace["result"] == (17, 32)


@requires_mypy
def test_narrowing_and_all_dependent_code_can_move_together(tmp_path: Path) -> None:
    """A blanket ban on assertions loses this valid whole computation.

    No dependent caller code remains outside the helper, so no fact is lost."""
    outcome = apply_one(
        tmp_path,
        """
        def first(value: str | None) -> str:
            assert value is not None
            cleaned = value.strip()
            return cleaned.upper()

        def second(value: str | None) -> str:
            assert value is not None
            cleaned = value.strip()
            return cleaned.lower()
        """,
        pick="first and second",
    )
    assert outcome.error is None, outcome.error
    assert any(isinstance(node, ast.Assert) for node in ast.walk(outcome.helper()))
    assert outcome.prospective_checks == 1, outcome.checked_helpers


@pytest.mark.parametrize(
    "tail, refuses",
    [
        ("return value.upper()", True),
        ("value = 'replacement'\nreturn value.upper()", False),
        ("assert value is not None\nreturn value.upper()", False),
        ("return 0", False),
        ("return value", False),
        ("return value.__class__", False),
        ("return value + accepts_none", False),
        ("if value is not None:\n    return value.upper()\nreturn ''", False),
        (
            "if isinstance(value, int):\n    return str(value)\nelse:\n    return value.upper()",
            True,
        ),
        ("def nested(value):\n    return value.upper()\nreturn 0", False),
    ],
)
def test_assertion_boundary_tracks_later_reads_and_rebindings(tail: str, refuses: bool) -> None:
    """Distinguish a required refinement from ordinary reads and replacement facts.

    Narrowing str|int|None to int in one branch leaves its else branch needing
    the earlier not-None fact. An unknown reflected operator may accept None,
    so its typing remains the checker's job."""
    source = "def f(value):\n    assert value is not None\n" + textwrap.indent(tail, "    ")
    function = ast.parse(source).body[0]
    assert isinstance(function, ast.FunctionDef)
    assert caller_narrowing_leaves_with_block(function, function.body[:1]) is refuses


def test_local_assignment_returned_by_a_helper_can_carry_its_refined_type() -> None:
    """A typed return assigned by the caller can transmit the value's refined type."""
    function = ast.parse(
        "def f(value):\n    if value is None:\n        value = 1\n    return value + 1"
    ).body[0]
    assert isinstance(function, ast.FunctionDef)
    assert not caller_narrowing_leaves_with_block(function, function.body[:1])


def test_narrowing_in_a_sibling_branch_does_not_constrain_this_branch() -> None:
    """Sibling branches are alternatives, not sequential uses of one branch's proof."""
    function = ast.parse(
        "def f(value, choose):\n"
        "    if choose:\n"
        "        assert value is not None\n"
        "        print(value)\n"
        "    else:\n"
        "        print(value)\n"
    ).body[0]
    assert isinstance(function, ast.FunctionDef)
    branch = function.body[0]
    assert isinstance(branch, ast.If)
    assert not caller_narrowing_leaves_with_block(function, branch.body)


def test_the_enclosing_guard_already_supplies_a_redundant_assertion() -> None:
    """Moving a redundant assertion loses no fact supplied by a retained guard."""
    function = ast.parse(
        "def f(value):\n    if value is not None:\n"
        "        assert value is not None\n        print(value)\n        return value.upper()\n"
    ).body[0]
    assert isinstance(function, ast.FunctionDef)
    branch = function.body[0]
    assert isinstance(branch, ast.If)
    assert not caller_narrowing_leaves_with_block(function, branch.body[:2])


def test_an_assignment_invalidates_the_enclosing_guards_refinement() -> None:
    """An intervening write makes the inner assertion necessary again."""
    function = ast.parse(
        "def f(value):\n    if value is not None:\n"
        "        value = optional()\n        assert value is not None\n"
        "        print(value)\n        return value.upper()\n"
    ).body[0]
    assert isinstance(function, ast.FunctionDef)
    branch = function.body[0]
    assert isinstance(branch, ast.If)
    assert caller_narrowing_leaves_with_block(function, branch.body[1:3])


@pytest.mark.parametrize(
    "source",
    [
        "def f(obj):\n    alias = obj\n    assert alias.value is not None\n    return alias.value.upper()",
        "def f(value):\n    assert value is not None\n    discarded = [value for value in (1, 2)]\n    return value.upper()",
    ],
)
def test_returning_an_object_or_binding_a_comprehension_does_not_carry_the_fact(
    source: str,
) -> None:
    """Neither returning Box nor binding a comprehension target transmits this fact.

    Box retains its declared optional attribute type when returned; a temporary
    attribute refinement does not travel with it. A comprehension target is a
    distinct local and cannot count as rebinding the caller's narrowed name."""
    function = ast.parse(source).body[0]
    assert isinstance(function, ast.FunctionDef)
    assert caller_narrowing_leaves_with_block(function, function.body[:2])
