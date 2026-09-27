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

"""Correlated contracts should reach the project checker before lossy unions.

The goal is avoiding real rejected checks while retaining precise accepted
extractions. A reduction in accepted work or an Any fallback is not success.
Do not weaken the one-check expectation merely because inference regresses.
"""

from pathlib import Path

from tests.typed_fixtures import apply_one, requires_mypy


@requires_mypy
def test_collection_result_keeps_its_input_element_type_on_the_first_check(tmp_path: Path) -> None:
    outcome = apply_one(
        tmp_path,
        """
        def integers(values: list[int]) -> tuple[int, ...]:
            count = len(values)
            result = tuple(values)
            print(count)
            return result

        def strings(values: list[str]) -> tuple[str, ...]:
            count = len(values)
            result = tuple(values)
            print(count)
            return result
    """,
        pick="integers and strings",
    )
    assert outcome.error is None
    assert len(outcome.checked_helpers) == 1, outcome.checked_helpers
    assert "TypeVar" in (outcome.module or "")
    assert "Any" not in outcome.signature()


@requires_mypy
def test_callback_result_keeps_its_callers_contract_on_the_first_check(tmp_path: Path) -> None:
    outcome = apply_one(
        tmp_path,
        """
        from collections.abc import Callable

        def first(produce: Callable[[], list[str]]) -> list[str]:
            value = produce()
            description = repr(value)
            print(description)
            return value

        def second(produce: Callable[[], int]) -> int:
            value = produce()
            description = repr(value)
            print(description)
            return value
    """,
        pick="first and second",
    )
    assert outcome.error is None
    assert len(outcome.checked_helpers) == 1, outcome.checked_helpers
    assert "TypeVar" in (outcome.module or "")
    assert "Any" not in outcome.signature()


@requires_mypy
def test_comparison_helper_records_the_revealed_notimplemented_alternative(tmp_path: Path) -> None:
    """mypy treats returned NotImplemented as Any; only __eq__ has the return exemption.

    Retain that observed result alternative on the first signature. It is not
    missing type information, and making the class operand a thunk preserves
    isinstance narrowing, so speculative generic signatures are unnecessary.
    """
    outcome = apply_one(
        tmp_path,
        """
        class First:
            value: int = 1
            flag: bool = True

            def __eq__(self, other: object) -> bool:
                if not isinstance(other, First):
                    return NotImplemented
                return self.value == other.value and self.flag == other.flag

        class Second:
            value: int = 2
            flag: bool = False

            def __eq__(self, other: object) -> bool:
                if not isinstance(other, Second):
                    return NotImplemented
                return self.value == other.value and self.flag == other.flag
    """,
        pick="__eq__ and __eq__",
    )
    assert outcome.error is None
    assert len(outcome.checked_helpers) == 1, outcome.checked_helpers
    helper = outcome.helper()
    assert helper.returns is not None
    assert "bool | _typing.Any" in outcome.signature(), outcome.signature()
    assert "TypeVar" not in (outcome.module or "")
