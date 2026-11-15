# Copyright 2026 Eric Allen
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

"""Final usefulness refusals stay distinct from broken rendering and programming errors."""

from pathlib import Path

import pytest

from towel.unification import materialize
from towel.unification.exceptions import RefactoringError, UnprofitableHelper
from towel.unification.refactor_engine import UnificationRefactorEngine
from tests.test_cli_integration import DUPLICATES, invoke


def test_materializer_raises_a_specific_usefulness_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "example.py"
    source.write_text(DUPLICATES)
    engine = UnificationRefactorEngine()
    proposals = engine.analyze_file(str(source))
    assert proposals
    monkeypatch.setattr(materialize, "worthwhile_helper", lambda *args: False)
    with pytest.raises(UnprofitableHelper, match="interface outweighs"):
        engine.apply_refactoring(str(source), proposals[0])
    assert source.read_text() == DUPLICATES


@pytest.mark.parametrize("directory", [False, True])
def test_final_usefulness_refusal_is_counted_without_a_rendering_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, directory: bool
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    source = root / "example.py"
    source.write_text(DUPLICATES)
    monkeypatch.setattr(materialize, "worthwhile_helper", lambda *args: False)
    result = invoke(
        [
            "preview",
            str(root if directory else source),
            "--no-types",
            "--no-format",
            "--progress",
            "none",
            "--explain",
        ]
    )
    assert result.status == 0, result.stderr
    assert "unprofitable helper 1" in result.stdout
    assert "does not repay" in result.stdout
    assert "UNPROFITABLE HELPER:" in result.stderr
    assert "could not be rendered" not in result.stdout + result.stderr
    assert "RENDER FAILED" not in result.stderr
    assert source.read_text() == DUPLICATES


def test_a_real_rendering_failure_keeps_its_existing_classification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "example.py"
    source.write_text(DUPLICATES)

    def broken(*args: object) -> bool:
        raise RefactoringError("test rendering failure")

    monkeypatch.setattr(materialize, "worthwhile_helper", broken)
    result = invoke(["preview", str(source), "--no-types", "--no-format", "--progress", "none"])
    assert result.status == 0, result.stderr
    assert "could not be rendered 1" in result.stdout
    assert "test rendering failure" in result.stderr
    assert "unprofitable helper" not in result.stdout


def test_unexpected_programming_errors_still_abort_without_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, destination = tmp_path / "example.py", tmp_path / "out.py"
    source.write_text(DUPLICATES)

    def broken(*args: object) -> bool:
        raise RuntimeError("unexpected implementation failure")

    monkeypatch.setattr(materialize, "worthwhile_helper", broken)
    with pytest.raises(RuntimeError, match="unexpected implementation failure"):
        invoke(
            [
                "dry",
                str(source),
                str(destination),
                "--no-interactive",
                "--no-types",
                "--no-format",
                "--progress",
                "none",
            ]
        )
    assert source.read_text() == DUPLICATES
    assert not destination.exists()


def test_typing_decline_does_not_inherit_candidate_profitability_explanation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Typed audit item 3 (R10): typing refusals are not helper-cost refusals."""
    from towel.cli import _print_declined
    from towel.unification.fixed_point import RunReport

    report = RunReport(
        declined_pairs={"unprofitable_helper": 4},
        declined_proposals={"refused by the type checker": 1},
    )
    _print_declined(report, applied=0)
    output = capsys.readouterr().out
    assert "refused by the type checker" in output
    assert "does not repay" not in output


def test_unresolved_import_summary_names_the_first_module_and_remedies(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Typed audit item 3: an unresolved import needs a name and actionable remedies."""
    from towel.cli import _print_declined
    from towel.unification.fixed_point import RunReport

    report = RunReport(
        declined_proposals={
            "not verifiable: its file holds a name the type checker cannot type": 2,
        },
        first_unverifiable_reason='Cannot find implementation or library stub for module named "missing_widget"',
    )
    _print_declined(report, applied=0)
    output = capsys.readouterr().out
    assert "could not verify 2 proposed refactoring(s)" in output
    assert "missing_widget" in output
    assert "--no-types" in output
    assert "install the project's dependencies where Towel runs" in output
    assert "its file holds a name" not in output
    assert "does not repay" not in output
