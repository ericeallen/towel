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

"""What mypy says is never lost on the way to a verdict.

An error the oracle cannot read is still an error.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Sequence

import pytest

from towel import type_inference
from towel.type_inference import CheckSuccess, MypyInferrer

pytestmark = pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")


class _Reporting(MypyInferrer):
    """A checker whose build says exactly ``messages``."""

    def __init__(self, messages: Sequence[str]) -> None:
        super().__init__()
        self._messages = tuple(messages)

    def _build_errors(self, *arguments: object, **keywords: object) -> object:  # type: ignore[override]
        return type_inference._BuildMessages(self._messages)


def test_an_error_without_a_line_is_still_an_error(tmp_path: Path) -> None:
    """``path: error: ...`` has no line, and was dropped as though it were a note."""
    module = tmp_path / "m.py"
    module.write_text("X = 1\n", encoding="utf-8")
    oracle = _Reporting([f"{module}: error: Something about the whole module"])
    try:
        result = oracle.check_project({str(module): "X = 1\n"})
    finally:
        oracle.close()
    assert isinstance(result, CheckSuccess), result
    assert [(error.path, error.line) for error in result.errors] == [(str(module), None)]
    assert "Something about the whole module" in result.errors[0].message


def test_an_error_naming_no_file_is_still_an_error(tmp_path: Path) -> None:
    module = tmp_path / "m.py"
    module.write_text("X = 1\n", encoding="utf-8")
    oracle = _Reporting(["error: Something about the whole build", f"{module}:1: note: aside"])
    try:
        result = oracle.check_project({str(module): "X = 1\n"})
    finally:
        oracle.close()
    assert isinstance(result, CheckSuccess), result
    assert [error.message for error in result.errors] == ["Something about the whole build"]
