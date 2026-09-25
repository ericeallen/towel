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

"""A check that fails for the project as it stands refuses the run at once (round 4, P2-4).

With ``--exclude scripts`` every candidate check of sqlmodel failed the same
way, each proposal was declined as not judged, and the run worked for 663 s
and 893 s before exiting 1 with nothing applied. When a candidate's check
fails, the same check of the same files as they stand, no change applied, now
says whose failure it is: one the unchanged project shares stops the run,
naming it; one it does not is the candidate's, declined as before.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import textwrap
from typing import Mapping, Sequence

import pytest

from towel.type_inference import CheckFailure, CheckResult, MypyInferrer
from towel.unification.exceptions import CheckerCannotCheckTheProject, RefactoringError
from towel.unification.refactor_engine import UnificationRefactorEngine

requires_mypy = pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")

TWO_PAIRS = """\
    def first(values: list[int]) -> int:
        total = 0
        for value in values:
            total += value * 2
        return total


    def second(values: list[int]) -> int:
        total = 0
        for value in values:
            total += value * 2
        return total + 1


    def third(names: list[str]) -> str:
        joined = ""
        for name in names:
            joined += name.upper()
        return joined


    def fourth(names: list[str]) -> str:
        joined = ""
        for name in names:
            joined += name.upper()
        return joined + "!"
    """


class _FailingAfterTheBaseline(MypyInferrer):
    """mypy whose every check after the first fails; ``only_changed`` fails changed text alone."""

    def __init__(self, *, only_changed: bool) -> None:
        super().__init__()
        self.only_changed = only_changed
        self.checks = 0

    def check_project(
        self, sources: Mapping[str, str], *, excluded_paths: Sequence[str] = ()
    ) -> CheckResult:
        self.checks += 1
        changed = any(
            Path(path).read_text(encoding="utf-8") != text for path, text in sources.items()
        )
        if self.checks > 1 and (changed or not self.only_changed):
            return CheckFailure("CompileError: scripts/tool.py: error: Source file found twice")
        return super().check_project(sources, excluded_paths=excluded_paths)


def _run(tmp_path: Path, oracle: _FailingAfterTheBaseline) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\n", encoding="utf-8")
    (tmp_path / "m.py").write_text(textwrap.dedent(TWO_PAIRS), encoding="utf-8")
    engine = UnificationRefactorEngine(min_lines=3, type_oracle=oracle)
    try:
        engine.refactor_directory_to_fixed_point(str(tmp_path), str(tmp_path), progress="none")
    finally:
        oracle.close()


@requires_mypy
def test_r9my_a_failure_the_unchanged_project_shares_refuses_at_the_first_candidate(
    tmp_path: Path,
) -> None:
    oracle = _FailingAfterTheBaseline(only_changed=False)
    with pytest.raises(CheckerCannotCheckTheProject, match="found twice"):
        _run(tmp_path, oracle)
    assert oracle.checks == 3, "the baseline, one candidate, and that candidate's files unchanged"
    assert "__extracted_func" not in (tmp_path / "m.py").read_text(encoding="utf-8")


@requires_mypy
def test_r9my_a_failure_only_the_candidate_has_declines_it_as_not_judged(tmp_path: Path) -> None:
    oracle = _FailingAfterTheBaseline(only_changed=True)
    with pytest.raises(RefactoringError, match="not judged") as raised:
        _run(tmp_path, oracle)
    assert not isinstance(raised.value, CheckerCannotCheckTheProject)
