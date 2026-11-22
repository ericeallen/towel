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

from tests.test_cli_integration import LITERAL_DUPLICATES, invoke
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
    assert (sentence in result.stdout) is explain
    summary = "1 file holds module-level code or early-return guards that Towel does not examine"
    assert (
        result.stdout.splitlines().count(summary + ("." if explain else "; --explain lists them."))
        == 1
    )
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


@pytest.mark.parametrize("command", ["preview", "quick", "dry"])
@pytest.mark.parametrize("found", [False, True])
def test_coverage_is_one_line_until_the_reader_asks_for_files(
    tmp_path: Path, command: str, found: bool
) -> None:
    """Integration 7: many files get one qualification only when nothing was found."""
    root = tmp_path / "project"
    root.mkdir()
    (root / "first.py").write_text(CASES[1].source + "print('first')\n")
    (root / "second.py").write_text("print('second')\n" + (LITERAL_DUPLICATES if found else ""))
    args = _arguments(command, root, tmp_path / "out", False)
    result = invoke(args)
    assert result.status == 0, result
    summary = "2 files hold module-level code or early-return guards that Towel does not examine"
    assert result.stdout.splitlines().count(summary + "; --explain lists them.") == int(not found)
    assert "is not examined:" not in result.stdout
    assert "are not examined on their own:" not in result.stdout
    if found:
        assert (
            "refactoring(s)" if command != "quick" else "refactoring opportunities"
        ) in result.stdout
        assert "No repeated code" not in result.stdout

    explained = invoke([*_arguments(command, root, tmp_path / "explained", False), "--explain"])
    assert explained.status == 0, explained
    assert explained.stdout.splitlines().count(summary + ".") == 1
    for name in ("first.py", "second.py"):
        assert (
            f"Module-level code is not examined: {name} has 1 executable statement."
            in explained.stdout
        )
    assert (
        "Early-return guards are not examined on their own: first.py has 1 guard."
        in explained.stdout
    )


def test_a_declined_match_is_a_finding_for_the_coverage_summary(tmp_path: Path) -> None:
    """Integration 7: a named match left alone also makes the blanket qualification unnecessary."""
    source = tmp_path / "feed.py"
    source.write_text(
        "print('module')\n"
        + "".join(
            f"def {name}(items):\n    for item in items:\n        yield item + 1\n        yield item * 2\n"
            for name in ("first", "second")
        )
    )
    result = invoke(_arguments("preview", source, tmp_path / "out.py", False))
    assert result.status == 0, result
    assert "with similar code and extracted none." in result.stdout
    assert "module-level code or early-return guards" not in result.stdout
    assert "Module-level code is not examined:" not in result.stdout


@pytest.mark.parametrize("explain", [False, True])
def test_mixed_coverage_limits_have_their_own_headings(tmp_path: Path, explain: bool) -> None:
    """Cap census 3: the cap and the unrelated script's code both qualify the search."""
    many = tmp_path / "many.py"
    many.write_text(
        "def f(g, x):\n"
        + "".join(f"    g.send({' + '.join(['x'] * (3 * i + 1))})\n" for i in range(60))
    )
    (tmp_path / "script.py").write_text(
        "a = 1\nb = 2\nc = a + b\n"
        "def guard(x):\n    if x < 0:\n        return None\n"
        "    if x > 10:\n        return None\n    return x\n"
    )
    result = invoke(
        [
            "preview",
            str(tmp_path),
            "--strict",
            "--no-types",
            "--no-format",
            "--progress",
            "none",
            *(["--explain"] if explain else []),
        ]
    )
    assert result.status == 0, result
    lines = result.stdout.splitlines()
    module = next(
        i for i, line in enumerate(lines) if line.startswith("1 file holds module-level code")
    )
    limited = next(
        i
        for i, line in enumerate(lines)
        if line.startswith("Towel limited its search for repeated blocks inside 1 function.")
    )
    if explain:
        assert lines[module + 1].startswith("Module-level code is not examined: script.py has 3")
        assert lines[module + 2].startswith(
            "Early-return guards are not examined on their own: script.py has 2"
        )
        assert lines[limited + 1].startswith("many.py:1: f has 1,091")
