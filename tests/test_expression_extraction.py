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

"""The helper of an expression candidate, and the calls written where the expression stood.

Audit case 44 (finding: "a reviewer wants ``_may_post(member, now)``"): one
five-line condition in an ``if`` in two functions and in a generator's ``if``
clause in a third. The helper is ``def helper(free variables): return E``
and each site becomes ``helper(arguments)`` in the expression's place, with
the parentheses that only grouped it. Strict mode writes ``return True if E
else False`` where every site only tests the value.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path
from typing import List, Tuple

import pytest

from towel.api import UnificationRefactorEngine
from towel.unification.expression_sites import (
    expression_at,
    expression_sites,
    is_whole_body,
    replacement_span,
)
from towel.unification.models import ExpressionPlace
from towel.unification.splicing import BlockColumns, splice_block
from tests.test_callback_parameters import (
    helper_calls,
    refactored,
    refactored_scenario,
    run_script,
)
from tests.test_class_object_annotations import requires_mypy
from tests.test_cli_integration import invoke

CONDITION = """(
        member.is_active
        and not member.is_banned
        and member.email_verified
        and (now - member.created_at).days >= 7
        and member.warnings < 3
    )"""

MEMBER = """
class Member:
    def __init__(self, name, active=True, banned=False, verified=True, days=30, warnings=0):
        self.name, self.is_active, self.is_banned = name, active, banned
        self.email_verified, self.created_at, self.warnings = verified, days, warnings


class Days:
    def __init__(self, days):
        self.days = days


class Now:
    def __sub__(self, created):
        return Days(created)


MEMBERS = [
    Member("ada"),
    Member("bob", active=False),
    Member("cy", banned=True),
    Member("dee", verified=False),
    Member("eve", days=3),
    Member("fay", warnings=3),
]
"""
"""Members that make each operand of ``CONDITION`` decide at least once."""


def helper_definitions(source: str) -> List[str]:
    """Each generated helper of ``source``, unparsed."""
    return [
        ast.unparse(node)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name
    ]


# -- The audit's case ---------------------------------------------------------


def test_case_44_gives_one_predicate_used_at_all_three_places(tmp_path: Path) -> None:
    """Audit case 44: 1.949 answered "No duplicates found!"."""
    generated, same = refactored_scenario(tmp_path, "expressions/expr_condition")
    assert same
    (helper,) = helper_definitions(generated)
    assert helper.startswith(
        "def __extracted_func_0(member, now):\n    return member.is_active and"
    )
    assert "True if" not in helper
    assert helper_calls(generated) == ["__extracted_func_0(member, now)"] * 3
    assert generated.count("\n    if __extracted_func_0(member, now):\n") == 1
    assert generated.count("\n        if __extracted_func_0(member, now):\n") == 1
    assert "        if __extracted_func_0(member, now)\n    )" in generated
    assert "member.is_banned" in helper and generated.count("member.is_banned") == 1


def test_case_44_in_strict_mode_tests_each_operand_exactly_once(tmp_path: Path) -> None:
    """All three places only test the condition, so strict mode's helper returns True or False."""
    generated, same = refactored_scenario(tmp_path, "expressions/expr_condition", strict=True)
    assert same
    (helper,) = helper_definitions(generated)
    assert helper.startswith(
        "def __extracted_func_0(member, now):\n    return True if member.is_active"
    )
    assert helper.endswith("else False")
    assert helper_calls(generated) == ["__extracted_func_0(member, now)"] * 3


IN_STATEMENTS = MEMBER + f"""

def can_post(member, now):
    if {CONDITION}:
        return True
    return False


def visible_authors(members, now):
    return sorted(
        member.name
        for member in members
        if {CONDITION}
    )


print([can_post(member, Now()) for member in MEMBERS], visible_authors(MEMBERS, Now()))
"""


@pytest.mark.parametrize("strict", [False, True])
def test_the_boundary_example_is_extracted(tmp_path: Path, strict: bool) -> None:
    """The lane's example ``b``: the expression inside two different statements."""
    generated, same = refactored(tmp_path, IN_STATEMENTS, strict=strict)
    assert same
    assert helper_calls(generated) == ["__extracted_func_0(member, now)"] * 2
    assert "    if __extracted_func_0(member, now):\n        return True" in generated
    assert "        if __extracted_func_0(member, now)\n    )" in generated


WHOLE_STATEMENTS = MEMBER + f"""

def can_post(member, now):
    allowed = {CONDITION}
    return "yes" if allowed else "no"


def badge(member, now):
    allowed = {CONDITION}
    print(member.name, allowed)
    return allowed


print([(can_post(member, Now()), badge(member, Now())) for member in MEMBERS])
"""


def test_a_statement_candidate_wins_a_tie_with_its_expression(tmp_path: Path) -> None:
    """The lane's example ``a`` (the ruling of 2026-11-16): relaxed mode's output is what it was.

    The assignment is a candidate of statements and its value a candidate
    expression over the same lines; the statement's proposal is kept.
    """
    for name, options in (("with", {}), ("without", {"expression_candidates": False})):
        path = tmp_path / f"{name}.py"
        path.write_text(WHOLE_STATEMENTS)
        engine = UnificationRefactorEngine(min_lines=3, strict=False, **options)  # type: ignore[arg-type]
        engine.refactor_to_fixed_point(str(path))
    with_expressions = (tmp_path / "with.py").read_text()
    assert with_expressions == (tmp_path / "without.py").read_text()
    (helper,) = helper_definitions(with_expressions)
    assert "allowed = member.is_active" in helper and helper.endswith("return allowed")


def test_strict_mode_extracts_the_expression_where_it_declined_the_statement(
    tmp_path: Path,
) -> None:
    """Strict mode declines the assignment (the helper would own ``allowed``); the value binds nothing."""
    generated, same = refactored(tmp_path, WHOLE_STATEMENTS, strict=True)
    assert same
    assert generated.count("    allowed = __extracted_func_0(member, now)\n") == 2
    (helper,) = helper_definitions(generated)
    assert "allowed" not in helper and "True if" not in helper


# -- The call in the expression's place ---------------------------------------


def _replaced(source: str, call: str = "helper(a, b)") -> str:
    """``source`` with ``call`` written in place of its one expression candidate."""
    source = textwrap.dedent(source)
    function = ast.parse(source).body[0]
    assert isinstance(function, ast.FunctionDef)
    found = expression_sites(function, 3)
    # The condition, or the list where the example has no condition.
    site = next(
        (s for s in found if isinstance(s.expression, ast.BoolOp)),
        next((s for s in found if isinstance(s.expression, ast.List)), None),
    )
    assert site is not None
    (first, last), columns = replacement_span(source, site)
    lines = source.splitlines(keepends=True)
    lines[first - 1 : last] = splice_block(lines[first - 1 : last], columns, call).lines
    result = "".join(lines)
    ast.parse(result)
    return result


@pytest.mark.parametrize(
    "written, expected",
    [
        # Parentheses that only group the expression go with it.
        (
            "def f(a, b):\n    if (\n        a.x\n        and b.y\n        and a.z\n    ):\n        pass\n",
            "def f(a, b):\n    if helper(a, b):\n        pass\n",
        ),
        (
            "def f(a, b):\n    v = (a.x\n         and b.y\n         and a.z)\n    return v\n",
            "def f(a, b):\n    v = helper(a, b)\n    return v\n",
        ),
        # A keyword written against the parenthesis needs a blank before a name.
        (
            "def f(a, b):\n    return(\n        a.x\n        and b.y\n        and a.z\n    )\n",
            "def f(a, b):\n    return helper(a, b)\n",
        ),
        (
            "def f(a, b):\n    return not[\n        a.x,\n        b.y,\n        a.z,\n    ]\n",
            "def f(a, b):\n    return not helper(a, b)\n",
        ),
        # A call's own parentheses stay with the call.
        (
            "def f(a, b):\n    return g(\n        a.x\n        and b.y\n        and a.z\n    )\n",
            "def f(a, b):\n    return g(\n        helper(a, b)\n    )\n",
        ),
        (
            "def f(a, b):\n    return g((\n        a.x\n        and b.y\n        and a.z\n    ), b)\n",
            "def f(a, b):\n    return g(helper(a, b), b)\n",
        ),
        # What follows the expression on its last line stays.
        (
            "def f(a, b):\n    return [a.x\n            and b.y\n            and a.z, b]\n",
            "def f(a, b):\n    return [helper(a, b), b]\n",
        ),
        # Parentheses that hold a comment beside the expression stay, with the comment.
        (
            "def f(a, b):\n    if (  # why\n        a.x\n        and b.y\n        and a.z\n    ):\n        pass\n",
            "def f(a, b):\n    if (  # why\n        helper(a, b)\n    ):\n        pass\n",
        ),
        # Columns count bytes: text before the expression may not be ASCII.
        (
            'def f(a, b):\n    v = {"é": (\n        a.x\n        and b.y\n        and a.z\n    )}\n    return v\n',
            'def f(a, b):\n    v = {"é": helper(a, b)}\n    return v\n',
        ),
    ],
)
def test_the_call_takes_the_place_of_the_expression_and_its_grouping_parentheses(
    written: str, expected: str
) -> None:
    assert _replaced(written) == expected


def test_a_generator_written_as_a_calls_only_argument_keeps_the_calls_parentheses() -> None:
    source = "def f(a, b):\n    return any(\n        x.ok\n        for x in a\n        if x.y and b\n    )\n"
    function = ast.parse(source).body[0]
    assert isinstance(function, ast.FunctionDef)
    (site,) = [
        s for s in expression_sites(function, 3) if isinstance(s.expression, ast.GeneratorExp)
    ]
    assert site.shares_parentheses
    (first, last), columns = replacement_span(source, site)
    lines = source.splitlines(keepends=True)
    lines[first - 1 : last] = splice_block(lines[first - 1 : last], columns, "helper(a, b)").lines
    assert "".join(lines) == "def f(a, b):\n    return any(helper(a, b))\n"


def test_a_multi_line_call_continues_inside_the_statement_it_is_written_in() -> None:
    """The formatter may break a long call; its later lines stand inside the site's brackets."""
    lines = ["    if (\n", "        a.x and b.y\n", "    ):\n"]
    spliced = splice_block(lines, BlockColumns(7, 5), "helper(\n    a,\n    b,\n)")
    assert "".join(spliced.lines) == "    if helper(\n        a,\n        b,\n    ):\n"


COMMENTED = MEMBER + """

def can_post(member, now):
    if (
        member.is_active  # deactivated accounts keep their posts
        and not member.is_banned
        # a ban outlives a warning
        and member.warnings < 3
        and member.email_verified
    ):
        return True
    return False


def badge(member, now):
    allowed = (
        member.is_active
        and not member.is_banned
        and member.warnings < 3
        and member.email_verified
    )
    return allowed


print([(can_post(member, Now()), bool(badge(member, Now()))) for member in MEMBERS])
"""


def test_comments_between_the_operands_move_into_the_helper(tmp_path: Path) -> None:
    """They stood inside the site's parentheses; the helper is written with parentheses to hold them."""
    generated, same = refactored(tmp_path, COMMENTED)
    assert same
    helper = generated[generated.index("def __extracted_func_0") : generated.index("def can_post")]
    assert "    return (" in helper
    assert "member.is_active  # deactivated accounts keep their posts\n" in helper
    assert "# a ban outlives a warning\n" in helper
    assert generated.count("# a ban outlives a warning") == 1
    assert generated.count("# deactivated accounts keep their posts") == 1
    assert "    if __extracted_func_0(member):\n" in generated


METHODS = """
class Shop:
    def __init__(self, limit):
        self.limit = limit
        self.open = True

    def first(self, order):
        if (
            self.open
            and order > self.limit
            and order % 2 == 0
            and order < 100
        ):
            return "big"
        return "small"

    def second(self, orders):
        return [
            order
            for order in orders
            if (
                self.open
                and order > self.limit
                and order % 2 == 0
                and order < 100
            )
        ]


shop = Shop(3)
print(shop.first(4), shop.first(2), shop.second([1, 4, 5, 6, 200]))
"""


@pytest.mark.parametrize("strict", [False, True])
def test_two_methods_of_one_class_share_a_method(tmp_path: Path, strict: bool) -> None:
    """The helper is placed as for statements: here a method, called through the receiver."""
    generated, same = refactored(tmp_path, METHODS, strict=strict)
    assert same
    assert helper_calls(generated) == ["self.__extracted_func_0(order)"] * 2
    assert "    def __extracted_func_0(self, order):" in generated


DIFFERING = MEMBER + """

def posts(member, now):
    return [
        member.is_active
        and not member.is_banned
        and member.email_verified
        and member.warnings < 3
    ]


def votes(member, now):
    return [
        member.is_active
        and not member.is_banned
        and member.email_verified
        and member.warnings < 1
    ]


print([(posts(member, Now()), votes(member, Now())) for member in MEMBERS])
"""


@pytest.mark.parametrize("strict", [False, True])
def test_a_differing_constant_becomes_a_parameter(tmp_path: Path, strict: bool) -> None:
    """Differing parts become parameters by the rules that serve statements."""
    generated, same = refactored(tmp_path, DIFFERING, strict=strict)
    assert same
    assert helper_calls(generated) == [
        "__extracted_func_0(member, 3)",
        "__extracted_func_0(member, 1)",
    ]


@pytest.mark.parametrize("mode", ["--no-strict", "--strict"])
def test_two_modules_share_the_helper_only_across_modules(tmp_path: Path, mode: str) -> None:
    """With ``--cross-module`` the other module imports the helper; without it, nothing pairs."""
    source = tmp_path / "src"
    package = source / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "posting.py").write_text(
        f"def can_post(member, now):\n    if {CONDITION}:\n        return True\n    return False\n"
    )
    (package / "listing.py").write_text(
        "from pkg.posting import can_post\n\n\n"
        "def visible_authors(members, now):\n"
        f"    return [member.name for member in members if {CONDITION}]\n"
    )
    options = ["--no-interactive", "--no-types", "--no-format", "--progress", "none", mode]
    alone = invoke(["dry", str(source), str(tmp_path / "alone"), *options])
    assert alone.status == 0, alone.stderr
    assert "__extracted_func" not in (tmp_path / "alone" / "pkg" / "listing.py").read_text()
    shared = invoke(["dry", str(source), str(tmp_path / "shared"), "--cross-module", *options])
    assert shared.status == 0, shared.stderr
    listing = (tmp_path / "shared" / "pkg" / "listing.py").read_text()
    posting = (tmp_path / "shared" / "pkg" / "posting.py").read_text()
    assert "from pkg.posting import __extracted_func_0" in listing
    assert "for member in members if __extracted_func_0(now, member)]" in listing
    assert "def __extracted_func_0(now, member):" in posting
    assert "    if __extracted_func_0(now, member):\n        return True" in posting


SHARED_PARENTHESIS_LINE = """
def f1(x, y):
    return (
        x > 0
        and x < 100
        and x % 2 == 0
        and x != 42
    ), (
        y > 0
        and y < 100
        and y % 2 == 0
        and y != 42
    )


def f2(z):
    if (
        z > 0
        and z < 100
        and z % 2 == 0
        and z != 42
    ):
        return 1
    return 0


print(f1(4, 5), f1(42, 2), f2(4), f2(42))
"""


@pytest.mark.parametrize("mode", ["--no-strict", "--strict"])
def test_two_sites_that_share_a_parenthesis_line_are_not_both_taken(
    tmp_path: Path, mode: str
) -> None:
    """Review of the ``expressions`` lane, defect 4: ``towel dry`` stopped with "Overlapping replacements".

    The call replaces an expression with its grouping parentheses, so the
    pair's call in ``f1`` reaches the line ``), (``; the other site of
    ``f1`` begins on that line. Clustering judged overlap by the
    expressions' own lines and took both.
    """
    source = tmp_path / "src"
    source.mkdir()
    (source / "mod.py").write_text(SHARED_PARENTHESIS_LINE)
    options = ["--no-interactive", "--no-types", "--no-format", "--progress", "none", mode]
    result = invoke(["dry", str(source), str(tmp_path / "out"), *options])
    assert result.status == 0, result.stdout + result.stderr
    changed = (tmp_path / "out" / "mod.py").read_text()
    # Strict mode declines: ``f1`` uses the value and ``f2`` only tests it.
    assert ("__extracted_func_0(" in changed) == (mode == "--no-strict")
    assert run_script(source / "mod.py") == run_script(tmp_path / "out" / "mod.py")


CLOSING_LINE_COMMENT = """
def f1(x):
    ok = (
        x > 0
        and x < 100
        and x % 2 == 0
        and x != 42
    )  {comment}
    return ok


def f2(z):
    return (
        z > 0
        and z < 100
        and z % 2 == 0
        and z != 42
    )


print(f1(4), f1(42), f2(4), f2(0))
"""


@pytest.mark.parametrize("strict", [False, True], ids=["relaxed", "strict"])
@pytest.mark.parametrize("comment", ["# pragma: no cover", "# the posting rule"])
def test_a_comment_after_the_closing_parenthesis_stays(
    tmp_path: Path, comment: str, strict: bool
) -> None:
    """Review of the ``expressions`` lane, defect 5: the comment was deleted with the parentheses.

    The call takes the place of the parentheses that only group the
    expression, and the comment written after ``)`` went with them; a
    ``# pragma: no cover`` lost that way changed what coverage measured.
    Those parentheses stay when a comment follows them, and the comment
    stays on its line.
    """
    generated, same = refactored(
        tmp_path, CLOSING_LINE_COMMENT.format(comment=comment), strict=strict
    )
    assert same
    if strict:
        assert helper_calls(generated) == ["__extracted_func_0(x)", "__extracted_func_0(z)"]
    else:
        # f2's whole body is the expression, so relaxed mode calls f2 (shape,
        # "Redirect to an existing function only within a clean consumer scope").
        assert helper_calls(generated) == []
        assert "        f2(x)\n" in generated, generated
    assert f")  {comment}\n" in generated, generated


# -- A later pass finds the helper it made ------------------------------------


@pytest.mark.parametrize("exact", [False, True])
def test_a_helpers_whole_body_is_recognized_in_both_forms(exact: bool) -> None:
    """So that a third site found on a later pass calls the helper, not a helper of the helper."""
    body = "return True if a.x and b.y else False" if exact else "return a.x and b.y"
    function = ast.parse(f"def helper(a, b):\n    {body}\n").body[0]
    assert isinstance(function, ast.FunctionDef)
    returned = function.body[0]
    assert isinstance(returned, ast.Return) and returned.value is not None
    value = returned.value.test if isinstance(returned.value, ast.IfExp) else returned.value
    place = ExpressionPlace(
        (value.lineno, value.col_offset), (value.end_lineno or 0, value.end_col_offset or 0), exact
    )
    assert is_whole_body(place, function.body)
    assert not is_whole_body(place._replace(exact_truth=not exact), function.body)
    longer = ast.parse(f"def helper(a, b):\n    {body}\n    pass\n").body[0]
    assert isinstance(longer, ast.FunctionDef)
    assert not is_whole_body(place, longer.body)


THREE_PASSES = MEMBER + f"""

def can_post(member, now):
    if {CONDITION}:
        return True
    return False


def may_vote(member, now):
    return "vote" if {CONDITION} else "wait"


def summary(members, now):
    count = 0
    for member in members:
        while {CONDITION}:
            count += 1
            break
    return count


print([can_post(m, Now()) for m in MEMBERS], [may_vote(m, Now()) for m in MEMBERS], summary(MEMBERS, Now()))
"""


@pytest.mark.parametrize("strict", [False, True])
def test_every_site_calls_one_helper(tmp_path: Path, strict: bool) -> None:
    """No helper of a helper: the sites of a file share the first helper made."""
    generated, same = refactored(tmp_path, THREE_PASSES, strict=strict)
    assert same
    assert len(helper_definitions(generated)) == 1
    assert helper_calls(generated) == ["__extracted_func_0(member, now)"] * 3


# -- What the user sees -------------------------------------------------------


def test_the_preview_shows_the_call_in_its_statement(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "m.py").write_text(IN_STATEMENTS)
    result = invoke(["preview", str(source), "--no-types", "--no-format", "--progress", "none"])
    assert result.status == 0, result.stderr
    assert "+    if __extracted_func_0(member, now):" in result.stdout
    assert "+        if __extracted_func_0(member, now)" in result.stdout
    assert "+def __extracted_func_0(member, now):" in result.stdout


def test_the_expression_of_a_place_is_read_back_from_its_text() -> None:
    source = 'def f(a, b):\n    v = {"é": (\n        a.x.y\n        and b.y.z\n    )}\n'
    function = ast.parse(source).body[0]
    assert isinstance(function, ast.FunctionDef)
    (site,) = [s for s in expression_sites(function, 2) if isinstance(s.expression, ast.BoolOp)]
    found = expression_at(source, site.place)
    assert found is not None and ast.unparse(found) == "a.x.y and b.y.z"
    assert expression_at("x = 1\n", site.place) is None


TYPED = """
from dataclasses import dataclass


@dataclass
class Member:
    name: str
    is_active: bool
    is_banned: bool
    warnings: int = 0


def can_post(member: Member, floor: int) -> bool:
    if (
        member.is_active
        and not member.is_banned
        and member.warnings < floor
        and len(member.name) > 2
    ):
        return True
    return False


def visible_authors(members: list[Member], floor: int) -> list[str]:
    return sorted(
        member.name
        for member in members
        if (
            member.is_active
            and not member.is_banned
            and member.warnings < floor
            and len(member.name) > 2
        )
    )
"""


def _typed(tmp_path: Path, *options: str) -> Tuple[str, str]:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    (source / "members.py").write_text(TYPED)
    result = invoke(
        [
            "dry",
            str(source),
            str(output),
            "--no-interactive",
            "--no-format",
            "--progress",
            "none",
            *options,
        ]
    )
    assert result.status == 0, result.stderr
    return (output / "members.py").read_text(), result.stderr


@requires_mypy
@pytest.mark.parametrize("mode", ["--no-strict", "--strict"])
def test_a_typed_run_annotates_the_helper_from_its_sites(tmp_path: Path, mode: str) -> None:
    """The comprehension's variable has the type of an element of what it ranges over.

    The checker is asked at the start of the statement holding the
    expression, where the variable does not exist yet, about
    ``next(iter(members))``; the returned type is the expression's.
    """
    generated, errors = _typed(tmp_path, "--types", mode)
    assert "inference failed" not in errors
    assert "def __extracted_func_0(member: Member, floor: int) -> bool:" in generated
    assert "Any" not in generated
