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

"""Statements in the user guides that the 1.949 user audit found false stay corrected.

Each test names its audit item. Where the program can be asked, the test asks
it, so the guide follows the behaviour and not an earlier sentence; where the
behaviour is already pinned elsewhere, the test names that regression.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re

from tests.test_cli_integration import DUPLICATES, HELPER, invoke
from tests.test_documentation_navigation import _prose
from towel import cli

ROOT = Path(__file__).resolve().parents[1]


def _guide(name: str) -> str:
    return (ROOT / "docs" / name).read_text(encoding="utf-8")


def _compact(text: str) -> str:
    return " ".join(text.split())


def _section(text: str, heading: str) -> str:
    """The text under one heading, up to the next heading of the same or a higher level."""
    assert text.count(f"\n{heading}\n") == 1, f"Expected one {heading!r}"
    level = len(heading) - len(heading.lstrip("#"))
    lines: list[str] = []
    for line in text.split(f"\n{heading}\n", 1)[1].splitlines():
        marks = len(line) - len(line.lstrip("#"))
        if 0 < marks <= level and line[marks : marks + 1] == " ":
            break
        lines.append(line)
    return "\n".join(lines)


def test_usage_guide_does_not_promise_that_an_interrupted_write_changes_nothing() -> None:
    """Audit finding 12 (Kimi's review, finding 1): the one wrong statement that matters.

    The guide said "A failed or interrupted run leaves the project as it was". An
    in-place batch killed part-way leaves some files changed and a recovery journal:
    tests/test_change_transactions.py::test_killed_process_can_be_recovered_by_cli.
    A reader who believed the guide would not look for the journal.
    """
    section = _compact(_section(_guide("USAGE_GUIDE.md"), "## Command-Line Usage"))
    assert "interrupted run leaves the project as it was" not in section
    assert "can leave some files changed and others not" in section
    assert "`.towel-transaction-<id>` journal" in section
    assert "`towel recover` restores the original files" in section
    assert "(CLI_GUIDE.md#recovery-and-interrupted-writes)" in section
    reference = _compact(_section(_guide("CLI_GUIDE.md"), "## Output and in-place changes"))
    assert "they can observe a partially applied batch" in reference
    assert "interrupted batches retain a recovery journal" in reference


def test_output_path_rule_covers_a_separate_output_and_the_same_path_in_place(
    tmp_path: Path,
) -> None:
    """Audit finding 12 (documentation report D9, claim 8).

    The README said "The output path must not already exist or overlap the input",
    which is the rule for a separate output only: the input path given again as the
    output is how a project is refactored in place.
    """
    project = tmp_path / "project"
    project.mkdir()
    module = project / "module.py"
    module.write_text(DUPLICATES)
    existing = tmp_path / "existing"
    existing.mkdir()
    options = ["--no-interactive", "--no-types", "--no-format"]
    for output in (existing, project / "inside"):
        refused = invoke(["dry", str(project), str(output), *options])
        assert refused.status == 1, refused.stdout + refused.stderr
        assert module.read_text() == DUPLICATES
    in_place = invoke(["dry", str(project), str(project), *options])
    assert in_place.status == 0, in_place.stderr
    assert "__extracted_func_0" in module.read_text()

    for text in ((ROOT / "README.md").read_text(encoding="utf-8"), _guide("CLI_GUIDE.md")):
        rule = "A separate output path must not already exist or overlap the input."
        paragraphs = [part for part in map(_compact, text.split("\n\n")) if rule in part]
        assert len(paragraphs) == 1
        assert "input path again as the output" in paragraphs[0]
        assert "in place" in paragraphs[0]
        assert "The output path must not already exist" not in _compact(text)


def _visible_flags(command: str) -> tuple[set[str], set[str]]:
    """The options of one command that take no value: those with a `--no-` form, and the rest."""
    commands = next(
        action
        for action in cli._build_parser()._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    paired: set[str] = set()
    single: set[str] = set()
    for action in commands.choices[command]._actions:
        names = {name for name in action.option_strings if name.startswith("--")}
        if action.help is argparse.SUPPRESS or not names or action.nargs != 0:
            continue
        if isinstance(action, argparse.BooleanOptionalAction):
            paired |= {name for name in names if not name.startswith("--no-")}
        elif not isinstance(action, argparse._HelpAction):
            single |= names
    return paired, single


def test_command_line_guide_names_which_options_have_a_negative_form() -> None:
    """Audit finding 12 (documentation report D9, claim 17; Kimi's review, finding 9).

    The guide said "Boolean options have `--x` and `--no-x` forms". `--explain`,
    `--json`, `--summary`, `--diff`, `--inventory` and `--quick` have none, and
    `--no-explain` is an argument error. The guide now lists both kinds, and the
    parser is asked about each option it lists.
    """
    guide = _compact(_guide("CLI_GUIDE.md"))
    assert "Boolean options have `--x` and `--no-x` forms" not in guide
    sentence = re.search(
        r"come in pairs, `--x` and `--no-x`: (?P<paired>[^.]+)\. "
        r"Options that only ask for something have no `--no-x` form: (?P<single>[^.]+)\.",
        guide,
    )
    assert sentence is not None
    listed_paired = set(re.findall(r"`(--[a-z-]+)`", sentence["paired"]))
    listed_single = set(re.findall(r"`(--[a-z-]+)`", sentence["single"]))
    assert listed_single >= {
        "--explain",
        "--json",
        "--summary",
        "--diff",
        "--inventory",
        "--quick",
    }
    dry_paired, dry_single = _visible_flags("dry")
    preview_paired, preview_single = _visible_flags("preview")
    assert listed_paired and listed_paired <= dry_paired & preview_paired
    assert listed_single <= dry_single | preview_single
    assert {"--explain", "--json"} <= dry_single & preview_single
    rejected = invoke(["dry", "--no-explain", "project", "output"])
    assert rejected.status == 2 and "--no-explain" in rejected.stderr


def test_exit_status_two_is_documented_for_a_rejected_rename_mapping(tmp_path: Path) -> None:
    """Audit finding 12 (Kimi's review, finding 6).

    The compatibility contract gave status 2 as "Invalid command-line arguments" only,
    while `rename-helpers --help` promises it for a rejected mapping. A script that
    reads the table would take a name collision for its own mistake in the arguments.
    The consumer-scope refusal the row also names is pinned by
    tests/test_rename_consumer_scope.py.
    """
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\nversion = "0"\n')
    module = tmp_path / "module.py"
    module.write_text(HELPER)
    for name, mapping in (
        ("collision", '{"__extracted_func_0": "RESULT"}'),
        ("keyword", '{"__extracted_func_0": "class"}'),
    ):
        renames = tmp_path / f"{name}.json"
        renames.write_text(mapping)
        rejected = invoke(["rename-helpers", str(tmp_path), "--rename-file", str(renames)])
        assert rejected.status == 2, rejected.stdout + rejected.stderr
        assert module.read_text() == HELPER
    assert invoke(["rename-helpers", str(tmp_path), "--no-such-option"]).status == 2

    rows = [line for line in _guide("COMPATIBILITY.md").splitlines() if line.startswith("| `2` |")]
    assert len(rows) == 1
    assert "Invalid command-line arguments." in rows[0]
    assert "`rename-helpers`" in rows[0] and "rename mapping that was rejected" in rows[0]
    assert "nothing is renamed" in rows[0]


def _contents_and_headings(page: str) -> tuple[list[str], list[str]]:
    """The titles an "On this page" list names, and the page's sections below that list."""
    _, separator, rest = page.partition("\nOn this page:\n\n")
    assert separator, "Expected an 'On this page:' list"
    entries, _, body = rest.partition("\n\n")
    listed = re.findall(r"^- \[([^\]]+)\]\(#[^)]+\)$", entries, re.MULTILINE)
    assert len(listed) == len(entries.splitlines()), "Every entry is one linked title"
    return listed, re.findall(r"^#{2,3} (.+)$", _prose(body), re.MULTILINE)


def test_performance_page_lists_every_section_in_page_order() -> None:
    """Audit finding 12 (documentation report D9, claim 60).

    The "On this page" list omitted "How long it takes" and "Historical 1.792
    development comparisons" and gave "Measurement conditions" five places early.
    tests/test_documentation_navigation.py checks that each entry's anchor exists.
    """
    listed, headings = _contents_and_headings(_guide("PERFORMANCE.md"))
    assert listed == headings
    stale = (
        "# Page\n\nOn this page:\n\n- [Second](#second)\n- [First](#first)\n\n"
        "## First\n\n### Unlisted\n\n```text\n## Not a heading\n```\n\n## Second\n"
    )
    assert _contents_and_headings(stale) == (["Second", "First"], ["First", "Unlisted", "Second"])
