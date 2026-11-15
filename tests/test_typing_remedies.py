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

"""Usability finding 6 / real-project audit R10: typing declines name remedies."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from towel.type_baseline import names_any_warning, unlooked_warning
from towel.type_inference import CheckFailure, CheckResult, CheckSuccess, TypeDiagnostic
from towel.unification.exceptions import RefactoringError
from towel.unification.refactor_engine import UnificationRefactorEngine
from tests.test_type_run_policy import _Oracle, _source


@pytest.mark.parametrize("reason", ["baseline", "import", "candidate", "candidate_failure"])
def test_typing_refusals_explain_both_remedies(tmp_path: Path, reason: str) -> None:
    """Finding 6/R10: the refusal itself must offer both ways to proceed."""
    path = tmp_path / "program.py"
    source = "import absent_dependency\n" + _source()
    path.write_text(source)

    def verdict(sources: Mapping[str, str]) -> CheckResult:
        if reason == "baseline":
            return CheckFailure("checker unavailable")
        if reason == "import":
            return CheckSuccess(
                (
                    TypeDiagnostic(
                        str(path),
                        'Cannot find implementation or library stub for module named "absent_dependency"',
                        1,
                    ),
                )
            )
        if sources[str(path)] != source:
            if reason == "candidate_failure":
                return CheckFailure("checker timed out")
            return CheckSuccess((TypeDiagnostic(str(path), "new type error"),))
        return CheckSuccess()

    engine = UnificationRefactorEngine(type_oracle=_Oracle(verdict))
    proposal = engine.analyze_file(str(path))[0]
    with pytest.raises(RefactoringError) as error:
        engine.apply_refactoring(str(path), proposal)
    message = str(error.value)
    assert "--no-types" in message
    assert "install the project's dependencies where Towel runs" in message
    assert path.read_text() == source


def test_warnings_for_unchanged_regions_name_remedies(tmp_path: Path) -> None:
    """Finding 6/R10: initial warnings explain the same actions as final declines."""
    path = str(tmp_path / "program.py")
    warning = names_any_warning(
        {path: (TypeDiagnostic(path, 'module "missing" is Any', 1),)}, tmp_path, {path}
    )
    assert warning is not None
    for message in (warning, unlooked_warning({path: [(1, 3)]}, tmp_path)):
        assert "--no-types" in message
        assert "install the project's dependencies where Towel runs" in message
