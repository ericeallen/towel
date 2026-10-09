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

"""An extraction no helper signature can type is declined at once, under the reason that says why.

The annotation ladder used to try every rung on these and decline with "every
helper annotation variant introduces project type errors", spending two to four
project checks per hearing to learn what one refusal already showed, or what
the proposal alone shows. The corpus study's eight such declines (packaging's
three comparison pairs, rich's progress columns and JSON and bar ``__init__``s,
mistune's image attributes, nox's environments) are four patterns; each has a
miniature here, run under strict mypy as those projects are.

Caller-side assertions and guarded initialization are now kept at the call site
during construction (test_narrowing_boundaries.py). The checker-side judgment
remains a backstop for narrowing forms construction does not recognize.

A collection whose partial type the block completed is decided from the
proposal alone and costs no check. A deferred result needing narrowing, code
after a call needing narrowing, and attribute declarations leaving their class
need the checker's verdict: they cost the checks that establish the refusal.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Optional

import pytest

from tests.typed_fixtures import apply_one, requires_mypy
from towel.type_inference import TypeDiagnostic
from towel.unification.annotation_ladder import (
    Rejection,
    Unanswerable,
    narrowing_refused_in_thunk,
)
from towel.unification.exceptions import Untypeable


def _declined(outcome: object, reason: str) -> str:
    error = getattr(outcome, "error")
    assert error is not None, getattr(outcome, "module")
    message = str(error)
    assert f"No helper signature can type this extraction: it {reason}" in message, message
    return message


@requires_mypy
def test_a_lambda_that_needs_a_tests_narrowing_is_declined_after_one_check(
    tmp_path: Path,
) -> None:
    """rich's progress columns: ``int(task.total) if task.total is not None else completed``."""
    outcome = apply_one(
        tmp_path,
        """
        class Task:
            def __init__(self, total: float | None, completed: float, finished: bool) -> None:
                self.total = total
                self.completed = completed
                self.finished = finished


        def describe_total(task: Task, completed: int) -> int:
            value = (
                int(task.total)
                if task.total is not None
                else completed
            )
            return value + 1


        def describe_done(task: Task, fallback: int) -> int:
            value = (
                int(task.completed)
                if task.finished
                else fallback
            )
            return value + 1
        """,
        pick="describe_total and describe_done",
    )
    message = _declined(outcome, "narrows what a call-site lambda reads")
    assert "task.total is not None" in message
    assert outcome.prospective_checks == 1, outcome.checked_helpers
    assert all("Any" not in signature for signature in outcome.checked_helpers)


@requires_mypy
def test_a_nullable_result_aliased_before_consumption_is_declined_after_one_check(
    tmp_path: Path,
) -> None:
    outcome = apply_one(
        tmp_path,
        """
        class Task:
            def __init__(self, total: float | None, completed: float, finished: bool) -> None:
                self.total = total
                self.completed = completed
                self.finished = finished

        def describe_total(task: Task, completed: int) -> int:
            if task.total is not None:
                total = task.total
                value = int(total)
            else:
                value = completed
            return value + 1

        def describe_done(task: Task, fallback: int) -> int:
            if task.finished:
                total = task.completed
                value = int(total)
            else:
                value = fallback
            return value + 1
        """,
        pick="describe_total and describe_done",
    )
    message = _declined(outcome, "narrows what a call-site lambda reads")
    assert "task.total is not None" in message
    assert "float | None" in message
    assert outcome.prospective_checks == 1, outcome.checked_helpers
    assert all("Any" not in signature for signature in outcome.checked_helpers)


@requires_mypy
def test_attribute_declarations_moved_out_of_their_classes_are_declined_after_one_check(
    tmp_path: Path,
) -> None:
    """nox's ``CondaEnv``/``VirtualEnv.__init__``: the only assignments that declared the attributes."""
    outcome = apply_one(
        tmp_path,
        """
        import os


        class ProcessEnv:
            def __init__(self, bin_paths: list[str]) -> None:
                self.bin_paths = bin_paths


        class CondaEnv(ProcessEnv):
            def __init__(self, location: str, interpreter: str | None = None) -> None:
                self.location_name = location
                self.location = os.path.abspath(location)
                self.interpreter = interpreter
                super().__init__(["conda"])

            def describe(self) -> str:
                return f"{self.location_name} {self.location} {self.interpreter}"


        class VirtualEnv(ProcessEnv):
            def __init__(self, location: str, interpreter: str | None = None) -> None:
                self.location_name = location
                self.location = os.path.abspath(location)
                self.interpreter = interpreter
                super().__init__(["venv"])

            def describe(self) -> str:
                return f"{self.interpreter} at {self.location_name} ({self.location})"
        """,
        pick="__init__ and __init__",
    )
    message = _declined(outcome, "declares its class's attributes")
    assert "location_name" in message or "interpreter" in message or "location" in message
    assert outcome.prospective_checks == 1, outcome.checked_helpers


@requires_mypy
def test_a_partial_type_the_block_completed_is_declined_without_a_check(tmp_path: Path) -> None:
    """mistune's image attributes: ``attrs = {}`` filled by the block, now passed to the helper first."""
    outcome = apply_one(
        tmp_path,
        """
        from typing import Any


        def image_attributes(options: dict[str, Any]) -> dict[str, Any]:
            attrs = {}
            if "alt" in options:
                attrs["alt"] = options["alt"]
            width = options.get("width")
            return {"attrs": attrs, "width": width}


        def figure_attributes(options: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
            if "caption" in options:
                extra["caption"] = options["caption"]
            height = options.get("height")
            return {"extra": extra, "height": height}
        """,
        pick="image_attributes and figure_attributes",
    )
    message = _declined(outcome, "completes its caller's partial type")
    assert "attrs = {}" in message
    assert outcome.prospective_checks == 0, outcome.checked_helpers


@requires_mypy
def test_an_annotated_collection_is_no_partial_type(tmp_path: Path) -> None:
    """The same block with ``attrs: dict[str, Any] = {}`` is typed, and applied."""
    outcome = apply_one(
        tmp_path,
        """
        from typing import Any


        def image_attributes(options: dict[str, Any]) -> dict[str, Any]:
            attrs: dict[str, Any] = {}
            if "alt" in options:
                attrs["alt"] = options["alt"]
            width = options.get("width")
            return {"attrs": attrs, "width": width}


        def figure_attributes(options: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
            if "caption" in options:
                extra["caption"] = options["caption"]
            height = options.get("height")
            return {"extra": extra, "height": height}
        """,
        pick="image_attributes and figure_attributes",
    )
    assert outcome.error is None, outcome.error


def _refused_caller_operand(helper: ast.FunctionDef, call: ast.Call) -> Optional[Unanswerable]:
    module = ast.unparse(helper) + "\n\ndef caller(task):\n    return " + ast.unparse(call) + "\n"
    line = len(module.splitlines())
    rejection = Rejection(
        (
            TypeDiagnostic(
                "/p/module.py",
                'Argument 1 to "int" has incompatible type "float | None"; expected "SupportsInt"',
                line,
            ),
        ),
        "/p/module.py",
        helper.name,
        module,
        {"/p/module.py": module},
    )
    return narrowing_refused_in_thunk(helper, rejection)


@pytest.mark.parametrize(
    "body",
    [
        "return result() if predicate() else fallback",
        "if predicate():\n    return result()\nreturn fallback",
        "if not predicate():\n    return fallback\nelse:\n    return result()",
        "while predicate():\n    return result()\nreturn fallback",
        "return predicate() and result()",
    ],
)
def test_a_deferred_predicate_governs_a_checker_refused_thunk(body: str) -> None:
    """Deferring the test preserves the relationship when its operand is actually refused."""
    source = "def helper(predicate, result, fallback):\n" + "\n".join(
        "    " + line for line in body.splitlines()
    )
    helper = ast.parse(source).body[0]
    assert isinstance(helper, ast.FunctionDef)
    call = ast.parse(
        "helper(lambda: task.total is not None, lambda: int(task.total), 0)", mode="eval"
    ).body
    assert isinstance(call, ast.Call)
    original = (ast.dump(helper), ast.dump(call))
    verdict = _refused_caller_operand(helper, call)
    assert verdict is not None
    assert verdict.reason is Untypeable.NARROWING_READ_IN_THUNK
    assert "task.total is not None" in verdict.detail
    assert (ast.dump(helper), ast.dump(call)) == original


@pytest.mark.parametrize(
    "test, predicate, result",
    [
        ("predicate(0)", "lambda: task.total is not None", "lambda: int(task.total)"),
        ("predicate(unused=0)", "lambda: task.total is not None", "lambda: int(task.total)"),
        ("predicate.__call__()", "lambda: task.total is not None", "lambda: int(task.total)"),
        ("predicate()", "lambda unused=task: task.total is not None", "lambda: int(task.total)"),
        ("predicate()", "lambda unused: task.total is not None", "lambda: int(task.total)"),
        ("predicate()", "lambda *unused: task.total is not None", "lambda: int(task.total)"),
        ("predicate()", "lambda **unused: task.total is not None", "lambda: int(task.total)"),
        ("predicate", "lambda: task.total is not None", "lambda: int(task.total)"),
        ("predicate()", "lambda: task.finished", "lambda: int(task.total)"),
        ("predicate()", "lambda: task.total >= 0", "lambda: int(task.total)"),
        ("predicate()", "lambda: task.total is not None", "lambda: int(task.completed)"),
    ],
)
def test_only_the_exact_deferred_predicate_proves_a_lost_refinement(
    test: str, predicate: str, result: str
) -> None:
    """Unsupported calls, binders and unrelated reads retain checker fallback."""
    helper = ast.parse(
        f"def helper(predicate, result, fallback):\n    return result() if {test} else fallback"
    ).body[0]
    assert isinstance(helper, ast.FunctionDef)
    call = ast.parse(f"helper({predicate}, {result}, 0)", mode="eval").body
    assert isinstance(call, ast.Call)
    assert _refused_caller_operand(helper, call) is None


def test_a_deferred_truth_test_keeps_the_checker_refinement_backstop() -> None:
    """Only an actual operand diagnostic establishes that a truth test was needed."""
    module = (
        "def helper(predicate, result):\n"
        "    return result() if predicate() else 0\n"
        "\n"
        "def caller(task):\n"
        "    return helper(lambda: task.total, lambda: task.total / 2)\n"
    )
    helper = ast.parse(module).body[0]
    assert isinstance(helper, ast.FunctionDef)
    for diagnostic, expected in (
        ('Unsupported operand types for / ("None" and "int")', True),
        ('Argument 1 to "helper" has incompatible type "float | None"; expected "float"', False),
    ):
        rejection = Rejection(
            (TypeDiagnostic("/p/module.py", diagnostic, 5),),
            "/p/module.py",
            "helper",
            module,
            {"/p/module.py": module},
        )
        verdict = narrowing_refused_in_thunk(helper, rejection)
        assert (verdict is not None) is expected
        if verdict is not None:
            assert verdict.reason is Untypeable.NARROWING_READ_IN_THUNK
