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

"""What a run tells its user, and when ``dry`` asks (audit findings 3 and 7).

The user-perspective audit of 1.949 found: "No duplicates found!" for a
directory with no Python files; three wordings of nothing found; internal
reason codes in place of reasons; ``--explain`` printing either nothing more
or the whole developer trace; ``dry`` asking before any analysis and naming
no path; and ``preview`` crashing on a file ``dry`` skips. Each test names
the item it pins.
"""

from __future__ import annotations

import io
import json
import logging
import re
import shutil
import signal
import subprocess
from pathlib import Path
from typing import List

import pytest

from tests.test_cli_integration import DUPLICATES, CliResult, invoke
from towel import cli
from towel.cli_reporting import source_diff
from towel.diagnostics import LOG, REJECTIONS

FLAGS = ["--no-types", "--no-format", "--progress", "none"]

PRICING = '''"""Price formatting helpers for the storefront."""


def format_price_usd(amount):
    rounded = round(amount, 2)
    whole, _, frac = f"{rounded:.2f}".partition(".")
    whole = f"{int(whole):,}"
    return "$" + whole + "." + frac


def format_price_chf(amount):
    rounded = round(amount, 2)
    whole, _, frac = f"{rounded:.2f}".partition(".")
    whole = f"{int(whole):,}"
    return "CHF " + whole + "." + frac
'''
"""The audit's scenario 01: similar code, all of it left alone."""

UTF16_MODULE = "def wide(x):\n    return x\n".encode("utf-16")


def _outcomes(tmp_path: Path) -> dict[str, Path]:
    """The same small input in each of the four outcomes."""
    cases = {
        "no_python_files": {"README.txt": "Nothing to see.\n"},
        "no_repeated_code": {"lone.py": "def lone(x):\n    return x + 1\n"},
        "all_left_alone": {"pricing.py": PRICING},
        "applied": {"example.py": DUPLICATES},
    }
    made = {}
    for name, files in cases.items():
        directory = tmp_path / name
        directory.mkdir()
        for file_name, text in files.items():
            (directory / file_name).write_text(text)
        made[name] = directory
    return made


EXPECTED = {
    "no_python_files": "No Python files found under {target}.",
    "no_repeated_code": "No repeated code found in 1 file.",
    "all_left_alone": "Found 3 places with similar code and extracted none.",
}


@pytest.mark.parametrize("outcome", sorted(EXPECTED))
def test_each_outcome_has_one_wording_in_preview_dry_and_quick(
    tmp_path: Path, outcome: str
) -> None:
    """Item 2: four honest outcomes, worded the same by every command, each exit status 0."""
    target = _outcomes(tmp_path)[outcome]
    sentence = EXPECTED[outcome].format(target=target)
    preview = invoke(["preview", str(target), *FLAGS])
    quick = invoke(["preview", str(target), "--quick", *FLAGS])
    dry = invoke(["dry", str(target), str(tmp_path / "out"), "--no-interactive", *FLAGS])
    for result in (preview, quick, dry):
        assert result.status == 0, result
        assert sentence in result.stdout
        for retired in ("No duplicates found", "No refactorings found", "Termination:"):
            assert retired not in result.stdout
    # Item 1: every run says what it looked at.
    files = 0 if outcome == "no_python_files" else 1
    census = f"Analysed {files} Python file{'' if files == 1 else 's'} ("
    assert all(census in result.stdout for result in (preview, quick, dry))
    assert (
        json.loads(invoke(["preview", str(target), "--json", *FLAGS]).stdout)["outcome"] == outcome
    )


def test_an_applied_run_counts_and_lists_what_it_changes(tmp_path: Path) -> None:
    """Items 2 and 7: preview lists the files it would change, as dry does."""
    target = _outcomes(tmp_path)["applied"]
    preview = invoke(["preview", str(target), "--summary", *FLAGS])
    assert preview.status == 0, preview
    assert "Would apply 1 refactoring(s)." in preview.stdout
    assert "Analysed 1 Python file (2 functions)." in preview.stdout
    assert "Would change 1 file(s):\n  example.py: 1 refactoring(s)\n" in preview.stdout
    assert "Extract common code from first and second" in preview.stdout
    # Pairs the last analysis found in the code the run itself wrote are not news.
    assert "left alone" not in preview.stdout


def test_the_census_counts_what_was_read_and_skips_an_undecodable_file_once(
    tmp_path: Path,
) -> None:
    """Item 1, and robustness R6: a UTF-16 file beside a module is named once, with why."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "example.py").write_text(DUPLICATES)
    (project / "wide.py").write_bytes(UTF16_MODULE)
    result = invoke(["dry", str(project), str(tmp_path / "out"), "--no-interactive", *FLAGS])
    assert result.status == 0, result
    assert "Analysed 1 Python file (2 functions). Skipped 1 file it could not read." in (
        result.stdout
    )
    assert result.stderr.count("wide.py") == 1
    assert re.search(r"Skipping \S*wide\.py: it is not UTF-8 text", result.stderr)
    document = json.loads(invoke(["preview", str(project), "--json", *FLAGS]).stdout)
    assert document["analysed"]["files"] == 1
    assert document["analysed"]["functions"] == 2
    assert [entry["path"] for entry in document["analysed"]["skipped"]] == ["wide.py"]


@pytest.mark.parametrize("mode", [[], ["--summary"], ["--diff"], ["--json"]])
def test_preview_survives_a_file_it_cannot_decode(tmp_path: Path, mode: List[str]) -> None:
    """Item 6 (robustness R6): preview compares bytes first and decodes only changed files."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "example.py").write_text(DUPLICATES)
    (project / "wide.py").write_bytes(UTF16_MODULE)
    result = invoke(["preview", str(project), *mode, *FLAGS])
    assert result.status == 0, result
    assert "Traceback" not in result.stdout + result.stderr
    if mode != ["--summary"]:
        diff = json.loads(result.stdout)["diff"] if mode == ["--json"] else result.stdout
        assert "+++ after/example.py" in diff and "wide.py" not in diff


def test_a_changed_file_that_does_not_decode_is_named_and_left_out_of_the_diff(
    tmp_path: Path,
) -> None:
    """Item 6: a changed file the diff cannot decode gets one warning naming it."""
    before, after = tmp_path / "before", tmp_path / "after"
    before.mkdir()
    after.mkdir()
    (before / "wide.py").write_bytes(UTF16_MODULE)
    (after / "wide.py").write_bytes(UTF16_MODULE + "x = 1\n".encode("utf-16"))
    (before / "same.py").write_text("x = 1\n")
    (after / "same.py").write_text("x = 1\n")
    stderr = io.StringIO()
    handler = logging.StreamHandler(stderr)
    LOG.addHandler(handler)
    try:
        assert source_diff(before, after, ()) == ""
    finally:
        LOG.removeHandler(handler)
    assert "Not showing the change to" in stderr.getvalue()
    assert "wide.py" in stderr.getvalue()


def test_reasons_come_before_the_closing_line_and_the_largest_places_follow(
    tmp_path: Path,
) -> None:
    """Items 3 and 4: one line per reason, then the places, before "Input files were not changed"."""
    target = _outcomes(tmp_path)["all_left_alone"]
    result = invoke(["preview", str(target), *FLAGS])
    lines = result.stdout.splitlines()
    assert lines[-1] == "Input files were not changed."
    why = lines.index(
        "Why code was left alone (most frequent first; --explain locates each place):"
    )
    assert re.match(r"^  2  Moving the block .*\[owned_binding_frame_boundary\]$", lines[why + 1])
    assert re.match(
        r"^  1  The shared code does not repay .*\[unprofitable_helper\]$", lines[why + 2]
    )
    places = lines.index("Places left alone, largest first:")
    assert lines[places + 1].startswith(
        "  pricing.py:5-8 and pricing.py:12-15: The shared code does not repay the helper"
    )
    assert len([line for line in lines[places + 1 :] if line.startswith("  pricing.py:")]) == 3
    assert str(tmp_path) not in result.stdout


def _many_left_alone(root: Path, count: int) -> Path:
    root.mkdir()
    for index in range(count):
        body = PRICING.replace("format_price_", f"format_{index}_price_")
        (root / f"module_{index}.py").write_text(body)
    return root


def test_five_largest_places_by_default_and_every_one_with_explain(tmp_path: Path) -> None:
    """Item 4: five lines by default; ``--explain`` lists all, from the last analysis only."""
    target = _many_left_alone(tmp_path / "project", 3)
    plain = invoke(["preview", str(target), *FLAGS])
    explained = invoke(["preview", str(target), "--explain", *FLAGS])
    located = re.compile(r"^  module_\d\.py:\d+-\d+ and module_\d\.py:\d+-\d+: ", re.MULTILINE)
    shown = located.findall(plain.stdout)
    every = located.findall(explained.stdout)
    assert len(shown) == 5
    assert "The 5 largest of" in plain.stdout and "(--explain lists them all)" in plain.stdout
    assert len(every) == len(set(every)) > 5
    assert "Places left alone, largest first:" in explained.stdout
    assert "REJECT[" not in explained.stdout + explained.stderr
    sizes = [
        sum(int(b) - int(a) + 1 for a, b in re.findall(r":(\d+)-(\d+)", line))
        for line in re.findall(r"^  module_.*$", explained.stdout, re.MULTILINE)
    ]
    assert sizes == sorted(sizes, reverse=True)


def test_the_developer_trace_stays_with_its_setting(tmp_path: Path) -> None:
    """Item 4: ``REJECT[code]: ...`` is still written under DEBUG_PROPOSAL_REJECTIONS."""
    target = _outcomes(tmp_path)["all_left_alone"]
    level = REJECTIONS.level
    REJECTIONS.setLevel(logging.DEBUG)
    try:
        traced = invoke(["preview", str(target), *FLAGS])
    finally:
        REJECTIONS.setLevel(level)
    assert "REJECT[unprofitable_helper]: " in traced.stderr
    assert "REJECT[" not in invoke(["preview", str(target), *FLAGS]).stderr


def _dry(tmp_path: Path, stdin: str, *extra: str) -> tuple[CliResult, Path]:
    target = _outcomes(tmp_path)["applied"]
    output = tmp_path / "cleaned"
    return invoke(["dry", str(target), str(output), *extra, *FLAGS], stdin=stdin), output


@pytest.mark.parametrize("answer", ["y\n", "Y\n", "yes\n", "YES\n"])
def test_dry_asks_after_the_analysis_and_names_the_destination(tmp_path: Path, answer: str) -> None:
    """Item 5: the question follows the analysis, on stderr, saying what and where."""
    result, output = _dry(tmp_path, answer)
    assert result.status == 0, result
    assert "1 refactoring(s) in 1 file(s): example.py; will write " in result.stderr
    assert f"{output}/." in result.stderr
    assert "Write these changes? [y/N] " in result.stderr
    assert "Write these changes?" not in result.stdout
    assert "pass --no-interactive" in result.stderr
    assert (output / "example.py").read_text() != DUPLICATES


@pytest.mark.parametrize("answer", ["n\n", "\n", "", "maybe\n"])
def test_declining_writes_nothing_and_exits_1(tmp_path: Path, answer: str) -> None:
    """Item 5: anything but yes cancels, with one plain line and the documented status."""
    result, output = _dry(tmp_path, answer)
    assert result.status == 1
    assert result.stderr.rstrip().endswith("Cancelled; nothing was written.")
    assert "Error:" not in result.stderr
    assert not output.exists()


def test_nothing_to_apply_asks_nothing(tmp_path: Path) -> None:
    """Item 5: with nothing to apply there is no question."""
    target = _outcomes(tmp_path)["all_left_alone"]
    result = invoke(["dry", str(target), str(tmp_path / "out"), *FLAGS], stdin="")
    assert result.status == 0, result
    assert "Write these changes?" not in result.stderr


def test_the_question_comes_after_the_time_limit_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 5: the timer is stopped before the prompt, so a slow answer is not a time-out."""
    timers: list[tuple[float, float]] = []
    confirm = cli._confirm

    def recording(prompt: str) -> bool:
        timers.append(signal.getitimer(signal.ITIMER_REAL))
        return confirm(prompt)

    monkeypatch.setattr(cli, "_confirm", recording)
    result, output = _dry(tmp_path, "y\n", "--time-limit", "600")
    assert result.status == 0, result
    assert timers == [(0.0, 0.0)]


def _git(directory: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *arguments],
        cwd=directory,
        check=True,
        capture_output=True,
    )


@pytest.mark.skipif(shutil.which("git") is None, reason="git absent")
@pytest.mark.parametrize("dirty", [True, False])
def test_in_place_warns_of_uncommitted_changes_to_the_files_it_would_change(
    tmp_path: Path, dirty: bool
) -> None:
    """Item 5: a warning, not a refusal; in-place summaries name files relative to the target."""
    target = _outcomes(tmp_path)["applied"]
    _git(target, "init", "-q")
    _git(target, "add", ".")
    _git(target, "commit", "-qm", "start")
    if dirty:
        with (target / "example.py").open("a") as module:
            module.write("# a local edit\n")
    result = invoke(["dry", str(target), str(target), *FLAGS], stdin="y\n")
    assert result.status == 0, result
    assert f"will change {target}/ in place" in result.stderr
    warned = "have changes not committed to git, which this run would change further: example.py"
    assert (warned in result.stderr) is dirty
    assert "\n  example.py: 1 refactoring(s)\n" in result.stdout
    assert str(target / "example.py") not in result.stdout


def test_a_file_input_with_a_directory_output_is_refused(tmp_path: Path) -> None:
    """Item 7: ``towel dry f.py outdir/`` used to write a file named outdir."""
    source = tmp_path / "f.py"
    source.write_text(DUPLICATES)
    result = invoke(["dry", str(source), str(tmp_path / "outdir") + "/", "--no-interactive"])
    assert result.status == 1
    assert "the output must name a file" in result.stderr
    assert f"{tmp_path / 'outdir'}/f.py" in result.stderr
    assert not (tmp_path / "outdir").exists()


def test_inventory_with_no_helpers_prints_nothing_extra(tmp_path: Path) -> None:
    """Item 7: ``--inventory`` dumped an empty inventory's whole JSON."""
    (tmp_path / "again").mkdir()
    target = _outcomes(tmp_path)["no_repeated_code"]
    result = invoke(["preview", str(target), "--inventory", *FLAGS])
    assert result.status == 0, result
    assert "Helper inventory:" not in result.stdout
    applied = invoke(
        ["preview", str(_outcomes(tmp_path / "again")["applied"]), "--inventory", *FLAGS]
    )
    assert "Helper inventory:" in applied.stdout


def test_quick_with_stats_is_an_argument_error(tmp_path: Path) -> None:
    """Item 7: ``--quick --stats`` printed a notice where the other combinations are errors."""
    result = invoke(["preview", str(tmp_path), "--quick", "--stats"])
    assert result.status == 2
    assert result.stdout == ""
    assert "--quick cannot be combined with" in result.stderr and "--stats" in result.stderr


def test_a_non_python_target_warns_on_stderr(tmp_path: Path) -> None:
    """Item 7: the warning went to stdout, where a report's reader takes it for the result."""
    source = tmp_path / "script"
    source.write_text(DUPLICATES)
    for arguments in (["preview", str(source)], ["preview", str(source), "--quick"]):
        result = invoke([*arguments, *FLAGS])
        assert result.status == 0, result
        assert "is not a Python file (.py)" in result.stderr
        assert "is not a Python file" not in result.stdout


def test_a_run_stopped_at_the_cap_says_so_in_words(tmp_path: Path) -> None:
    """Item 2: "Termination: fixed_point" said nothing; the cap is worth saying."""
    target = _outcomes(tmp_path)["applied"]
    capped = invoke(["preview", str(target), "--max-refactorings", "1", *FLAGS])
    assert "Stopped at the limit of 1 refactoring (--max-refactorings)." in capped.stdout
    assert (
        json.loads(
            invoke(["preview", str(target), "--max-refactorings", "1", "--json", *FLAGS]).stdout
        )["termination"]
        == "iteration_cap"
    )
