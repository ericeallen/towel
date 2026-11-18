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

import logging
from pathlib import Path
import re

import pytest

from towel.diagnostics import REJECTIONS
from towel.unification.models import RejectReason
from towel.unification import materialize
from towel.unification.exceptions import RefactoringError, UnprofitableHelper
from towel.unification.refactor_engine import UnificationRefactorEngine
from tests.test_cli_integration import LITERAL_DUPLICATES as DUPLICATES, invoke


@pytest.mark.parametrize("at_materialization", [False, True])
def test_repeated_cases_get_their_own_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, at_materialization: bool
) -> None:
    """Integration 7: explain the repeated cases at whichever step declines them."""
    from towel.unification import pair_evaluation

    source = tmp_path / "cases.py"
    source.write_text(
        "def yes(config):\n"
        "    assert config('yes', cast=bool) is True\n"
        "    assert config('on', cast=bool) is True\n"
        "    assert config('one', cast=bool) is True\n"
        "def no(config):\n"
        "    assert config('no', cast=bool) is False\n"
        "    assert config('off', cast=bool) is False\n"
        "    assert config('zero', cast=bool) is False\n"
    )
    if at_materialization:
        monkeypatch.setattr(pair_evaluation, "helper_rejection", lambda *args: None)
    result = invoke(["preview", str(source), "--explain", "--summary", "--no-types", "--no-format"])
    assert result.status == 0, result
    assert (
        "1  This helper would bundle independent repetitions of the same operation."
        in result.stdout
    )
    assert "[repeated_statement_group]" in result.stdout
    assert "does not repay" not in result.stdout


def test_materializer_raises_a_specific_usefulness_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "example.py"
    source.write_text(DUPLICATES)
    engine = UnificationRefactorEngine()
    proposals = engine.analyze_file(str(source))
    assert proposals
    monkeypatch.setattr(
        materialize, "helper_rejection", lambda *args: RejectReason.UNPROFITABLE_HELPER
    )
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
    monkeypatch.setattr(
        materialize, "helper_rejection", lambda *args: RejectReason.UNPROFITABLE_HELPER
    )
    # The developer trace is DEBUG_PROPOSAL_REJECTIONS's, no longer --explain's (2.0).
    level = REJECTIONS.level
    REJECTIONS.setLevel(logging.DEBUG)
    try:
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
    finally:
        REJECTIONS.setLevel(level)
    assert result.status == 0, result.stderr
    assert re.search(r"^ +1  .*does not repay.*\[unprofitable helper\]$", result.stdout, re.M)
    assert "example.py:" in result.stdout
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

    monkeypatch.setattr(materialize, "helper_rejection", broken)
    result = invoke(["preview", str(source), "--no-types", "--no-format", "--progress", "none"])
    assert result.status == 0, result.stderr
    assert re.search(r"^ +1  .*\[could not be rendered\]$", result.stdout, re.M)
    assert "test rendering failure" in result.stderr
    assert "unprofitable helper" not in result.stdout


def test_unexpected_programming_errors_still_abort_without_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, destination = tmp_path / "example.py", tmp_path / "out.py"
    source.write_text(DUPLICATES)

    def broken(*args: object) -> bool:
        raise RuntimeError("unexpected implementation failure")

    monkeypatch.setattr(materialize, "helper_rejection", broken)
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


def test_mixed_declines_explain_profitability_only_after_the_unprofitable_pairs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Typed round 2 finding 6: real unprofitable pairs keep their own explanation."""
    from towel.cli_reporting import reason_lines
    from towel.unification.fixed_point import RunReport

    report = RunReport(
        declined_pairs={"unprofitable_helper": 4},
        declined_proposals={"refused by the type checker": 1},
    )
    # Since 2.0 (the results lane) each reason is one line: count, sentence, [code].
    output = "\n".join(reason_lines(report))
    assert "[refused by the type checker]" in output
    lines = output.splitlines()
    explanation = next(line for line in lines if "does not repay" in line)
    assert explanation.lstrip().startswith("4 ")
    assert explanation.endswith("[unprofitable_helper]")


def test_unresolved_import_summary_names_the_first_module_and_remedies(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Typed audit item 3: an unresolved import needs a name and actionable remedies."""
    from towel.cli_reporting import reason_lines
    from towel.unification.fixed_point import RunReport

    report = RunReport(
        declined_proposals={
            "not verifiable: its file holds a name the type checker cannot type": 2,
        },
        first_unverifiable_reason='Cannot find implementation or library stub for module named "missing_widget"',
    )
    output = "\n".join(reason_lines(report, first_unverifiable=report.first_unverifiable_reason))
    counted = next(line for line in output.splitlines() if "could not verify" in line)
    assert counted.lstrip().startswith("2  The type checker could not verify")
    assert "missing_widget" in output
    assert "--no-types" in output
    assert "install the project's dependencies where Towel runs" in output
    # The reason's code appears only as the bracketed code of the results lane's table.
    code = "[not verifiable: its file holds a name the type checker cannot type]"
    assert "its file holds a name" not in output.replace(code, "")
    assert "does not repay" not in output


@pytest.mark.parametrize("applied", [0, 1])
@pytest.mark.parametrize("proposal_unprofitable", [False, True])
def test_profitability_explanation_follows_only_a_reported_cost_decline(
    capsys: pytest.CaptureFixture[str], applied: int, proposal_unprofitable: bool
) -> None:
    """Typed round 2 finding 6: typing and hidden pair counts never get a cost explanation."""
    from towel.cli_reporting import reason_lines, shows_pairs
    from towel.unification.fixed_point import DeclineReason, RunReport

    declines: dict[DeclineReason, int] = {"refused by the type checker": 1}
    if proposal_unprofitable:
        declines["unprofitable helper"] = 2
    report = RunReport(declined_pairs={"unprofitable_helper": 4}, declined_proposals=declines)
    # Since 2.0 the pair and proposal counts that share the cost sentence are one
    # line, and pairs are shown only when nothing was applied (the results lane).
    lines = reason_lines(report, pairs=shows_pairs(applied, False))
    explanations = [line for line in lines if "does not repay" in line]
    assert len(explanations) == int(proposal_unprofitable or applied == 0)
    for line in explanations:
        assert "[unprofitable" in line
        assert "type checker" not in line
