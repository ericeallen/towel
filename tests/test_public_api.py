# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Public imports keep exception identity and expose reviewable change plans."""

from pathlib import Path

import pytest

from towel import api, changes
from towel.unification import exceptions, models, refactor_engine


def test_public_exports_keep_the_existing_type_and_function_identities() -> None:
    for name in (
        "ChangeConflict",
        "ChangePlan",
        "ExternalEdit",
        "FileChange",
        "RecoveryRequired",
        "StaleSource",
        "apply_changes",
    ):
        assert getattr(api, name) is getattr(changes, name)
    assert api.TowelError is exceptions.TowelError
    assert api.RefactoringError is exceptions.RefactoringError
    assert api.RefactoringProposal is models.RefactoringProposal
    assert api.UnificationRefactorEngine is refactor_engine.UnificationRefactorEngine


def test_public_plan_can_be_reviewed_and_refuses_stale_source(tmp_path: Path) -> None:
    target = tmp_path / "example.py"
    target.write_bytes(b"answer = 1\n")
    plan = api.ChangePlan.from_sources(
        {str(target): target.read_bytes()}, {str(target): "answer = 2\n"}
    )
    assert plan.changes[0].before == b"answer = 1\n"
    assert plan.changes[0].after == b"answer = 2\n"
    target.write_bytes(b"answer = 3\n")
    with pytest.raises(api.StaleSource):
        api.apply_changes(plan)
    assert target.read_bytes() == b"answer = 3\n"
