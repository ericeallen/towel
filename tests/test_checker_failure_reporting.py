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

"""A checker that cannot run is not a verdict, and the run must not report one.

When every candidate's check failed (the checker timed out or crashed), each
drop was logged as a rendering failure and the command printed "No
refactorings found! Termination: fixed_point" and exited 0: a checker that
never answered read as a project with nothing to do.
"""

from __future__ import annotations

import contextlib
import io
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

from towel import cli
from towel.type_inference import (
    CheckFailure,
    CheckResult,
    CheckSuccess,
    RevealKey,
    RevealRequest,
    Subtyping,
)
from towel.unification.exceptions import RefactoringError
from towel.unification.refactor_engine import UnificationRefactorEngine
from tests.probe_answers import answer_probes

PAIR = "".join(
    f"def {name}(value):\n    total = value + 1\n    doubled = total * 2\n"
    f"    answer = doubled - {offset}\n    return answer\n\n\n"
    for name, offset in (("first", 3), ("second", 4))
)


class _Oracle:
    """Passes the original project and hands each candidate to ``verdict``."""

    def __init__(self, verdict: Callable[[Mapping[str, str]], CheckResult]) -> None:
        self.verdict = verdict
        self.checks = 0

    def check_project(
        self, sources: Mapping[str, str], *, excluded_paths: Sequence[str] = ()
    ) -> CheckResult:
        self.checks += 1
        return CheckSuccess() if self.checks == 1 else self.verdict(sources)

    def check(self, file_path: str, source: str) -> CheckResult:
        return self.check_project({file_path: source})

    def reveal(self, requests: Sequence[RevealRequest]) -> Mapping[RevealKey, str]:
        return answer_probes(requests)

    def is_subtype(
        self, file_path: str, source: str, pairs: Sequence[tuple[str, str]]
    ) -> Sequence[Subtyping]:
        return [Subtyping.UNKNOWN for _ in pairs]

    def close(self) -> None:
        pass


def _timed_out(_sources: Mapping[str, str]) -> CheckResult:
    return CheckFailure("the checker timed out")


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.py").write_text(PAIR)
    (project / "b.py").write_text(PAIR.replace("first", "third").replace("second", "fourth"))
    return project


def _engine(verdict: Callable[[Mapping[str, str]], CheckResult]) -> UnificationRefactorEngine:
    return UnificationRefactorEngine(
        min_lines=3,
        reuse_existing_functions=False,
        annotate_helpers=False,
        type_oracle=_Oracle(verdict),
    )


@pytest.mark.parametrize("in_place", [True, False])
def test_a_run_the_checker_never_answered_raises_and_publishes_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, in_place: bool
) -> None:
    project = _project(tmp_path)
    output = project if in_place else tmp_path / "out"
    with pytest.raises(RefactoringError, match=r"(?s)checker could not run.*timed out.*--no-types"):
        with contextlib.redirect_stdout(io.StringIO()):
            _engine(_timed_out).refactor_directory_to_fixed_point(
                str(project), str(output), progress="none"
            )
    assert "type checker could not check" in caplog.text
    assert "could not be rendered" not in caplog.text
    assert (project / "a.py").read_text() == PAIR
    assert in_place or not output.exists()


def test_a_single_file_the_checker_never_answered_raises(tmp_path: Path) -> None:
    project = _project(tmp_path)
    with pytest.raises(RefactoringError, match="checker could not run"):
        with contextlib.redirect_stdout(io.StringIO()):
            _engine(_timed_out).refactor_to_fixed_point(str(project / "a.py"), progress="none")


def test_checker_failures_beside_applied_refactorings_are_counted(tmp_path: Path) -> None:
    project = _project(tmp_path)
    original_b = (project / "b.py").read_text()
    b_path = str((project / "b.py").resolve())

    def fails_for_b(sources: Mapping[str, str]) -> CheckResult:
        touched_b = any(
            str(Path(path).resolve()) == b_path and text != original_b
            for path, text in sources.items()
        )
        return CheckFailure("the checker crashed") if touched_b else CheckSuccess()

    engine = _engine(fails_for_b)
    with contextlib.redirect_stdout(io.StringIO()):
        results, _ = engine.refactor_directory_to_fixed_point(
            str(project), str(project), progress="none"
        )
    assert set(results) == {str(project / "a.py")}
    assert engine.checker_failures >= 1


def _dry(arguments: list[str], oracle: _Oracle) -> tuple[int, str]:
    stdout = io.StringIO()
    status = 0
    with (
        patch.object(sys, "argv", ["towel", "dry", *arguments]),
        patch.object(cli, "_type_oracle", return_value=oracle),
        contextlib.redirect_stdout(stdout),
        contextlib.redirect_stderr(stdout),
    ):
        try:
            cli.main()
        except SystemExit as exit_result:
            status = int(exit_result.code or 0)
    return status, stdout.getvalue()


def test_the_command_exits_nonzero_when_the_checker_prevented_every_refactoring(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    output = tmp_path / "out"
    status, printed = _dry(
        [str(project), str(output), "--no-interactive", "--progress", "none", "--no-format"],
        _Oracle(_timed_out),
    )
    assert status == 1, printed
    assert "No refactorings found" not in printed
    assert "checker could not run" in printed
    assert not output.exists()
