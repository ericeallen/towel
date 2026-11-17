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

"""Re-audit problem 4: a negative result cannot speak for code never examined."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

import pytest

from tests.test_cli_integration import invoke
from towel.cli_reporting import run_outcome
from towel.unification.fixed_point import RunReport
from towel.unification.models import NotExamined


@dataclass(frozen=True)
class UnsupportedCase:
    source: str
    module_statements: int
    early_return_guards: int
    sentence: str


CASES = (
    UnsupportedCase(
        "left = [1, 2]\nprint(sum(left))\nright = [3, 4]\nprint(sum(right))\n",
        4,
        0,
        "Module-level code is not examined: example.py has 4 executable statements. "
        "Move code you want Towel to compare into a function.",
    ),
    UnsupportedCase(
        "def lookup(value):\n    if not value:\n        return None\n    return value\n",
        0,
        1,
        "Early-return guards are not examined on their own: example.py has 1 guard. "
        "Extract the shared condition into a function, keeping each return in its caller.",
    ),
)


def _arguments(command: str, target: Path, out: Path, strict: bool) -> list[str]:
    arguments = (
        ["dry", str(target), str(out), "--no-interactive"]
        if command == "dry"
        else ["preview", str(target), *(["--quick"] if command == "quick" else [])]
    )
    return [
        *arguments,
        "--strict" if strict else "--no-strict",
        "--no-types",
        "--no-format",
        "--progress",
        "none",
    ]


@pytest.mark.parametrize("case", CASES, ids=["module", "guard"])
@pytest.mark.parametrize("command", ["preview", "quick", "dry"])
@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("explain", [False, True])
def test_unsupported_code_is_named_without_an_all_clear(
    tmp_path: Path, case: UnsupportedCase, command: str, strict: bool, explain: bool
) -> None:
    """Problem 4, cases 31 and 20: every text command names its limits and a useful remedy."""
    source = tmp_path / "example.py"
    source.write_text(case.source)
    output = tmp_path / "out.py"
    result = invoke(
        [
            *_arguments(command, source, output, strict),
            *(["--explain"] if explain else []),
        ]
    )
    assert result.status == 0, result
    sentence = case.sentence.replace("example.py", "out.py") if command == "dry" else case.sentence
    assert sentence in result.stdout
    assert "No repeated code found in the code Towel examined in 1 file." in result.stdout
    assert "No repeated code found in 1 file." not in result.stdout
    assert "repeat each other" not in result.stdout
    assert source.read_text() == case.source
    if command == "dry":
        assert output.read_text() == case.source
    else:
        assert not output.exists()


@pytest.mark.parametrize("case", CASES, ids=["module", "guard"])
@pytest.mark.parametrize("command", ["preview", "dry"])
@pytest.mark.parametrize("strict", [False, True])
def test_json_carries_coverage_counts_and_qualifies_the_negative_outcome(
    tmp_path: Path, case: UnsupportedCase, command: str, strict: bool
) -> None:
    """Problem 4: machine readers receive the same limited conclusion and honest counts."""
    source = tmp_path / "example.py"
    source.write_text(case.source)
    result = invoke(
        [
            *_arguments(command, source, tmp_path / "out.py", strict),
            "--json",
        ]
    )
    assert result.status == 0, result
    document = json.loads(result.stdout)
    assert document["outcome"] == "not_fully_examined"
    assert document["analysed"]["not_examined"] == [
        {
            "path": "out.py" if command == "dry" else "example.py",
            "module_statements": case.module_statements,
            "early_return_guards": case.early_return_guards,
        }
    ]
    assert document["applied"] == 0


def test_unexamined_code_does_not_erase_positive_or_declined_findings() -> None:
    """Problem 4: coverage is independent of a positive finding elsewhere in the file."""
    report = RunReport(files_analysed=1, not_examined=(NotExamined("module.py", 2, 0),))
    assert run_outcome(report, 1) == "applied"
    assert (
        run_outcome(
            RunReport(
                files_analysed=1,
                not_examined=report.not_examined,
                declined_pairs={"unprofitable_helper": 1},
            ),
            0,
        )
        == "all_left_alone"
    )
