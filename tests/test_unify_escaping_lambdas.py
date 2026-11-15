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

"""Audit D4: closed callbacks may escape without changing their keyword interface."""

import ast
from pathlib import Path

import pytest

from tests.test_created_object_escapes import _escapes
from towel.unification.unifier import Unifier
from towel.unification.refactor_engine import UnificationRefactorEngine


@pytest.mark.parametrize(
    "body",
    [
        "members.sort(key=lambda c: c.name)",
        "return lambda value: value + 1",
        "saved.append(lambda value: value * 2)",
        "callback = lambda value=external: value\nreturn callback",
        "return lambda: (local := 3) + local",
    ],
)
def test_closed_callbacks_may_escape(body: str) -> None:
    """Audit D4: callbacks whose bodies read only their own names may outlive a block."""
    assert not _escapes(body)


@pytest.mark.parametrize(
    "body",
    [
        "return lambda value: value + external",
        "return lambda value: (lambda: external)",
        "return lambda value: (yield value)",
    ],
)
def test_captured_callbacks_and_generators_still_decline(body: str) -> None:
    """Audit D4: deferred reads cannot migrate into a helper's cells."""
    assert _escapes(body)


@pytest.mark.parametrize(
    "left,right",
    [
        ("return lambda x: x", "return lambda y: y"),
        ("saved.append(lambda x: x)", "saved.append(lambda y: y)"),
        ("callback = lambda x: x", "callback = lambda y: y"),
    ],
)
def test_escaping_callback_parameters_keep_their_spelling(left: str, right: str) -> None:
    """Audit D4: keyword callers must still see their original parameter names."""
    assert Unifier().unify_blocks([ast.parse(left).body, ast.parse(right).body], [{}, {}]) is None


def test_immediately_invoked_lambda_can_still_alpha_rename() -> None:
    """Audit D4: a complete positional call cannot observe a parameter spelling."""
    blocks = [ast.parse(f"return (lambda {name}: {name} + 1)(3)").body for name in ("x", "y")]
    assert Unifier().unify_blocks(blocks, [{}, {}]) is not None


def test_different_escaping_signatures_are_not_extracted(tmp_path: Path) -> None:
    """Audit D4: the production pipeline must not merge incompatible keyword interfaces."""
    path = tmp_path / "callbacks.py"
    path.write_text("def first():\n    return lambda x: x\ndef second():\n    return lambda y: y\n")
    assert not UnificationRefactorEngine(min_lines=1, skip_trivial_helpers=False).analyze_file(
        str(path)
    )


@pytest.mark.parametrize(
    "suffix,accepted",
    [
        ("return result", True),
        ("return callback", False),
        ("saved.append(callback)\nreturn result", False),
        ("return callback(value=3)", False),
        ("", False),
    ],
)
def test_named_callback_requires_complete_positional_consumption(
    suffix: str, accepted: bool
) -> None:
    """Audit D4: preserve prior safe local calls without assuming a partial block is the whole function."""
    blocks = [
        ast.parse(f"callback = lambda {name}: {name} + 1\nresult = callback(3)\n{suffix}").body
        for name in ("value", "other")
    ]
    assert (Unifier().unify_blocks(blocks, [{}, {}]) is not None) is accepted


def test_unknown_consumer_can_retain_and_keyword_call_closed_callbacks(tmp_path: Path) -> None:
    """Audit D4: an actual extraction preserves calls after the creating function returns."""
    source = """
def first(consumer):
    consumer(lambda c: c.name)
    return 'first'

def second(consumer):
    consumer(lambda c: c.name)
    return 'second'
"""
    path = tmp_path / "retained.py"
    path.write_text(source)
    transformed, count, _ = UnificationRefactorEngine(
        min_lines=1, skip_trivial_helpers=False
    ).refactor_to_fixed_point(str(path))
    assert count > 0
    exercise = """
saved = []
assert first(saved.append) == 'first'
assert second(saved.append) == 'second'
class Member:
    name = 'Ada'
assert [callback(c=Member()) for callback in saved] == ['Ada', 'Ada']
"""
    for program in (source, transformed):
        exec(compile(program + exercise, "<retained-keyword-call>", "exec"), {})
