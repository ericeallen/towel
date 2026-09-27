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

"""Recover value differences and nested suites that the candidate finder missed.

The positive regressions require useful extraction, not merely equivalent
unchanged output. The negative cases keep store targets, patterns and control
statements out of value parameterization. Runtime traces check evaluation
order, capture scope, exception subgroup handling and exception-target cleanup.
See CONTRIBUTING.md, "Preserve the intent of policy regressions", before
weakening these requirements to accommodate a changed proposal list.
"""

from __future__ import annotations

import ast
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import textwrap

import pytest

from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.unifier import Unifier


@pytest.mark.parametrize(
    "left,right",
    [
        ("consume(value)", "consume(value + 1)"),
        ("items = [value]", "items = [value + 1]"),
        ("items = (value,)", "items = (source.value,)"),
        ("items = {value}", "items = {load(value)}"),
        ("items = {value: key}", "items = {load(value): key}"),
        ("items = {key: value}", "items = {key: source.value}"),
        ("result = value and done", "result = load(value) and done"),
        ("result = seed < value", "result = seed < load(value)"),
    ],
)
def test_different_value_kinds_in_list_fields_can_be_parameterized(left: str, right: str) -> None:
    """An AST list field must not make Name versus Call/Attribute/BinOp a hard refusal."""
    substitution = Unifier().unify_blocks([ast.parse(left).body, ast.parse(right).body], [{}, {}])
    assert substitution is not None
    assert len(substitution.param_expressions) == 1


@pytest.mark.parametrize(
    "left,right",
    [
        ("holder.field = value", "holder[index] = value"),
        ("del holder.field", "del holder[index]"),
        ("consume(*values)", "consume(value)"),
        ("consume(value := load())", "consume(load())"),
        ("break", "continue"),
        ("return value", "raise problem"),
        (
            "match subject:\n    case [value]:\n        consume(value)",
            "match subject:\n    case {'key': value}:\n        consume(value)",
        ),
    ],
)
def test_list_field_recovery_does_not_parameterize_bindings_or_control(
    left: str, right: str
) -> None:
    """These differing nodes are not independently evaluable expression values."""
    assert Unifier().unify_blocks([ast.parse(left).body, ast.parse(right).body], [{}, {}]) is None


def _apply_pair(tmp_path: Path, source: str, *, covering: str) -> str:
    path = tmp_path / "module.py"
    path.write_text(textwrap.dedent(source))
    engine = UnificationRefactorEngine(
        min_lines=2, reuse_existing_functions=False, type_oracle=None, annotate_helpers=False
    )
    proposals = [
        p
        for p in engine.analyze_file(str(path))
        if p.description == "Extract common code from f and g"
    ]
    required_lines = [
        index
        for index, line in enumerate(textwrap.dedent(source).splitlines(), 1)
        if covering in line
    ]
    assert len(required_lines) == 2
    proposals = [
        p
        for p in proposals
        if all(
            any(start <= line <= end for start, end in (r.line_range for r in p.replacements))
            for line in required_lines
        )
    ]
    assert proposals, "The recovery must produce an applicable extraction."
    chosen = max(
        proposals,
        key=lambda p: sum(end - start for start, end in (r.line_range for r in p.replacements)),
    )
    changed = engine.apply_refactoring(str(path), chosen)
    assert changed != textwrap.dedent(source)
    assert "__extracted_func" in changed
    compile(changed, str(path), "exec")
    return changed


def _observe(source: str, invocation: str) -> str:
    output = StringIO()
    with redirect_stdout(output):
        exec(compile(textwrap.dedent(source) + "\n" + invocation, "fixture.py", "exec"), {})
    return output.getvalue()


EXPRESSIONS = """
def load(n):
    print('load', n)
    return n + 10

class Number(int):
    def __mul__(self, other):
        print('multiply', int(self), other)
        return int(self) * other

def f(n):
    x = load(n) * 3
    print('x', x)
    y = x - 4
    return y

def g(n):
    x = load(n * 2) * 3
    print('x', x)
    y = x - 4
    return y
"""


def test_different_call_argument_kinds_extract_without_reordering_effects(tmp_path: Path) -> None:
    """The recovered argument difference still passes through ordinary thunk safety."""
    changed = _apply_pair(tmp_path, EXPRESSIONS, covering="x = load(")
    invocation = "print(f(Number(1)), g(Number(2)))"
    before = _observe(EXPRESSIONS, invocation)
    assert before == "load 1\nx 33\nmultiply 2 2\nload 4\nx 42\n29 38\n"
    assert _observe(changed, invocation) == before


MATCH_CASES = """
def mark(value):
    print('guard', value)
    return value >= 0

def f(cmd, n):
    match cmd:
        case {'go': captured} if mark(captured):
            total = n * 3 + captured
            total = total + 7
            print('go', total)
            return total
        case _:
            return 0

def g(cmd, n):
    match cmd:
        case {'run': captured} if mark(captured):
            total = n * 3 + captured
            total = total + 7
            print('go', total)
            return total
        case _:
            return 1
"""


def _case_headers(source: str) -> list[tuple[str, str | None]]:
    return [
        (ast.dump(node.pattern), ast.dump(node.guard) if node.guard is not None else None)
        for node in ast.walk(ast.parse(textwrap.dedent(source)))
        if isinstance(node, ast.match_case)
    ]


def test_match_case_bodies_extract_with_captures_and_guards_unchanged(tmp_path: Path) -> None:
    """Bodies are candidates even when their enclosing patterns cannot unify."""
    changed = _apply_pair(tmp_path, MATCH_CASES, covering="print('go', total)")
    assert _case_headers(changed) == _case_headers(MATCH_CASES)
    invocation = (
        "print(f({'go': 4}, 2), f({'go': -1}, 2), f({}, 0), g({'run': 5}, 3), g({'go': 5}, 3))"
    )
    before = _observe(MATCH_CASES, invocation)
    assert before == "guard 4\ngo 17\nguard -1\nguard 5\ngo 21\n17 0 0 21 1\n"
    assert _observe(changed, invocation) == before


def test_match_capture_reassignment_keeps_the_existing_escape_guard(tmp_path: Path) -> None:
    """Enumeration does not bypass the existing guard on nested bindings escaping.

    Captures read by a helper are supported above. Recovering this caller-visible
    write requires the separately deferred nested-block liveness work; merely
    discovering its body must not introduce a helper that loses the write.
    """
    source = """
def f(subject):
    match subject:
        case {'go': captured}:
            captured = captured * 3
            captured = captured + 7
            print('capture', captured)
        case _:
            return None
    return captured

def g(subject):
    match subject:
        case {'run': captured}:
            captured = captured * 3
            captured = captured + 7
            print('capture', captured)
        case _:
            return None
    return captured
"""
    path = tmp_path / "module.py"
    path.write_text(source)
    engine = UnificationRefactorEngine(
        min_lines=2, reuse_existing_functions=False, type_oracle=None
    )
    assert engine.analyze_file(str(path)) == []
    invocation = "print(f({'go': 2}), g({'run': 3}), f({}), g({}))"
    before = _observe(source, invocation)
    assert before == "capture 13\ncapture 16\n13 16 None None\n"


@pytest.mark.parametrize("control", ["break", "continue"])
def test_match_body_loop_control_stays_with_the_callers_loop(tmp_path: Path, control: str) -> None:
    """Recover the body prefix without emitting a helper containing a free break/continue."""
    source = """
def f(items):
    result = []
    for n in items:
        match n:
            case 0:
                total = n * 3
                total = total + 7
                result.append(total)
                CONTROL
    return result

def g(items):
    result = []
    for n in items:
        match n:
            case 1:
                total = n * 3
                total = total + 7
                result.append(total)
                CONTROL
    return result
""".replace("CONTROL", control)
    changed = _apply_pair(tmp_path, source, covering="result.append(total)")
    cases = [node for node in ast.walk(ast.parse(changed)) if isinstance(node, ast.match_case)]
    assert len(cases) == 2
    assert all(
        isinstance(case.body[-1], ast.Break if control == "break" else ast.Continue)
        for case in cases
    )
    invocation = "print(f([0, 1, 0]), g([0, 1, 1]))"
    before = _observe(source, invocation)
    assert before == ("[7] [10]\n" if control == "break" else "[7, 7] [10, 10]\n")
    assert _observe(changed, invocation) == before


EXCEPT_STAR = """
def f(n, mixed=False):
    try:
        if n > 100:
            print('big')
        errors = [ValueError(n)]
        if mixed:
            errors.append(TypeError('keep'))
        raise ExceptionGroup('f', errors)
    except* ValueError as eg:
        total = n * 3
        total = total + 7
        print('caught', total, len(eg.exceptions))
    finally:
        print('finally-f')
    try:
        eg
    except UnboundLocalError:
        print('cleared-f')
    return n

def g(n, mixed=False):
    try:
        for i in range(n):
            pass
        errors = [ValueError(n)]
        if mixed:
            errors.append(TypeError('keep'))
        raise ExceptionGroup('g', errors)
    except* ValueError as eg:
        total = n * 3
        total = total + 7
        print('caught', total, len(eg.exceptions))
    finally:
        print('finally-g')
    try:
        eg
    except UnboundLocalError:
        print('cleared-g')
    return n + 1
"""


def test_except_star_body_extraction_keeps_subgroups_cleanup_and_finally(tmp_path: Path) -> None:
    """A handler helper consumes its subgroup while the original except* owns target cleanup."""
    changed = _apply_pair(tmp_path, EXCEPT_STAR, covering="print('caught', total")
    invocation = """
print(f(2), g(3))
for function in (f, g):
    try:
        function(4, mixed=True)
    except ExceptionGroup as remaining:
        print('unhandled', [type(error).__name__ for error in remaining.exceptions])
"""
    before = _observe(EXCEPT_STAR, invocation)
    assert "cleared-f\n" in before and "cleared-g\n" in before
    assert before.count("unhandled ['TypeError']") == 2
    assert _observe(changed, invocation) == before
    tree = ast.parse(changed)
    handlers = [
        node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler) and node.name == "eg"
    ]
    assert len(handlers) == 2
    assert all("__extracted_func" in ast.unparse(handler) for handler in handlers)


def test_enumeration_visits_except_star_finally_and_each_match_case() -> None:
    """Enumerate statement suites, never the case pattern or handler header itself."""
    function = ast.parse("""
def f(subject):
    match subject:
        case {'x': captured}:
            first = captured * 3
            second = first + 7
        case _:
            first = 1
            second = 2
    try:
        consume(subject)
    except* ValueError as group:
        first = len(group.exceptions)
        second = first + 7
    finally:
        first = 3
        second = 4
""").body[0]
    assert isinstance(function, ast.FunctionDef)
    engine = UnificationRefactorEngine(min_lines=2, type_oracle=None)
    blocks = engine._extract_code_blocks(function)
    match = function.body[0]
    tried = function.body[1]
    assert isinstance(match, ast.Match) and isinstance(tried, ast.TryStar)
    for suite in [*(case.body for case in match.cases), tried.handlers[0].body, tried.finalbody]:
        assert any(nodes == suite for _, nodes in blocks)
