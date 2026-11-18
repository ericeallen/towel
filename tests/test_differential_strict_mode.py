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

"""The differential fuzzer runs Towel in the mode it is asked for.

A release is validated in both modes (relaxed, the default, and strict), so
``python -m tests.differential.fuzz --strict`` must reach the engine, appear in
each failure's label, and be part of the command that reruns it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.differential import fuzz, runner
from tests.differential.grammar import generate_case
from tests.hostile_execution import ScopeWatch

pytestmark = pytest.mark.relaxed_mode
"""The engine's default is what the unflagged run must reach, so --towel-strict leaves it."""


@pytest.mark.parametrize("strict", [False, True])
def test_the_engine_is_constructed_in_the_requested_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, strict: bool
) -> None:
    constructed: dict[str, Any] = {}

    class Engine:
        def __init__(self, **options: Any) -> None:
            constructed.update(options)

        def refactor_to_fixed_point(self, *_: object, **__: object) -> tuple[None, int, None]:
            return None, 0, None

        def refactor_directory_to_fixed_point(
            self, *_: object, **__: object
        ) -> tuple[dict[str, tuple[int, None]], None]:
            return {}, None

    monkeypatch.setattr(runner, "UnificationRefactorEngine", Engine)
    case = generate_case(1)
    runner._refactor(case, runner.Mode(strict=strict), tmp_path, None, ScopeWatch())
    assert constructed["strict"] is strict


def test_the_label_names_strict_mode_and_the_default_names_nothing() -> None:
    assert runner.Mode(strict=True).label == "--strict --no-types"
    assert runner.Mode(cross_module=True, strict=True, types=True).label == (
        "--cross-module --strict"
    )
    assert runner.Mode().label == "--no-types"


def test_the_option_parses_and_defaults_to_relaxed() -> None:
    assert fuzz._arguments(["--strict"]).strict is True
    assert fuzz._arguments([]).strict is False
