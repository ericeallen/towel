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

Two are decided from the proposal alone and cost no check: a lambda at the call
that needs a test's narrowing, and, under mypy, a collection whose partial type
the block completed. Two need the checker's verdict, because whether the code
after the call needed the narrower type, and whether another declaration of an
attribute survives, are questions of declared types: they cost the one check
that shows them.
"""

from __future__ import annotations

from pathlib import Path

from tests.typed_fixtures import apply_one, requires_mypy


def _declined(outcome: object, reason: str) -> str:
    error = getattr(outcome, "error")
    assert error is not None, getattr(outcome, "module")
    message = str(error)
    assert f"No helper signature can type this extraction: it {reason}" in message, message
    return message


@requires_mypy
def test_a_narrowing_the_code_after_the_call_relied_on_is_declined_after_one_check(
    tmp_path: Path,
) -> None:
    """packaging's ``Version.__lt__``/``__le__``: ``if self._key_cache is None: ...`` then ``<``."""
    outcome = apply_one(
        tmp_path,
        """
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
        """,
        pick="__lt__ and __le__",
    )
    message = _declined(outcome, "narrows what its caller reads after the call")
    assert "_key_cache" in message
    assert outcome.prospective_checks == 1, outcome.checked_helpers


@requires_mypy
def test_a_lambda_that_needs_a_tests_narrowing_is_declined_without_a_check(
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
            return value * 2
        """,
        pick="describe_total and describe_done",
    )
    message = _declined(outcome, "narrows what a call-site lambda reads")
    assert "task.total is not None" in message
    assert outcome.prospective_checks == 0, outcome.checked_helpers


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
