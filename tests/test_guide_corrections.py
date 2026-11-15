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
import tomllib

import pytest

from tests.test_cli_integration import DUPLICATES, HELPER, invoke
from tests.test_documentation_navigation import _prose
from towel import cli
from towel.api import UnificationRefactorEngine
from towel.changes import apply_changes as internal_apply_changes

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


def test_usage_guide_gives_no_instruction_that_needs_a_source_checkout() -> None:
    """Audit finding 12 (documentation report D8 and claim 50).

    The guide for people calling an installed Towel told them to run `just test-smoke`,
    a recipe of this repository's justfile, and to edit `DEFAULT_SIMILARITY_THRESHOLD`
    in `block_signature.py`, which for them means editing site-packages. The smoke
    suite stays documented where contributors read: tests/README.md.
    """
    guide = _guide("USAGE_GUIDE.md")
    assert not re.search(r"(?m)^just \S|`just \S", guide), "justfile recipes need a checkout"
    assert "DEFAULT_SIMILARITY_THRESHOLD" not in guide and "block_signature.py" not in guide
    assert "#fast-local-testing" not in guide
    assert "just test-smoke" in (ROOT / "tests" / "README.md").read_text(encoding="utf-8")


def test_usage_guide_is_titled_as_the_pages_that_link_it_call_it() -> None:
    """Audit finding 12 (documentation report D8).

    The page was titled "Towel - Usage Guide" while the index and the quick start send
    readers to the "Python API guide". A reader who follows the link should land on a
    page of that name.
    """
    title = _guide("USAGE_GUIDE.md").splitlines()[0]
    assert title == "# Python API guide"
    for page in ("README.md", "QUICKSTART.md"):
        names = re.findall(r"\[([^\]]+)\]\(USAGE_GUIDE\.md\)", _guide(page))
        assert names == [title.removeprefix("# ")], page


SUPPORTED_ENGINE_METHODS = {
    "analyze_file",
    "analyze_files",
    "plan_refactoring",
    "refactor_to_fixed_point",
    "refactor_directory_to_fixed_point",
}
UNSUPPORTED = "outside the supported `towel.api` surface"


def _sections(page: str) -> list[tuple[str, str]]:
    """Each heading with the text up to the next heading of any level, fences kept whole."""
    sections: list[tuple[str, str]] = []
    heading = ""
    lines: list[str] = []
    fenced = False
    for line in page.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        if not fenced and re.match(r"#{1,6} ", line):
            sections.append((heading, "\n".join(lines)))
            heading, lines = line, []
        else:
            lines.append(line)
    sections.append((heading, "\n".join(lines)))
    return sections


def _unsupported_uses(page: str) -> list[tuple[str, str]]:
    """Examples that reach past `towel.api` in a section that does not say so."""
    found: list[tuple[str, str]] = []
    for heading, text in _sections(page):
        if UNSUPPORTED in _compact(text):
            continue
        for block in re.findall(r"(?ms)^```python\n(.*?)^```", text):
            modules = set(re.findall(r"^\s*from (towel[\w.]*) import", block, re.MULTILINE))
            methods = set(re.findall(r"\bengine\.(\w+)\(", block))
            found += [(heading, name) for name in sorted(modules - {"towel.api"})]
            found += [(heading, name) for name in sorted(methods - SUPPORTED_ENGINE_METHODS)]
    return found


def test_usage_guide_examples_use_the_supported_api_or_say_that_they_do_not() -> None:
    """Audit finding 12 (Kimi's review, finding 5).

    The compatibility contract names `towel.api` and five engine methods as the
    supported surface. The guide imported `apply_changes` from `towel.changes` and
    called `analyze_directory` without saying that either is outside it.
    """
    contract = _compact(_section(_guide("COMPATIBILITY.md"), "## Python API"))
    for method in SUPPORTED_ENGINE_METHODS:
        assert f"`{method}`" in contract
    assert "`analyze_directory`" not in contract
    guide = _guide("USAGE_GUIDE.md")
    assert _unsupported_uses(guide) == []
    assert UNSUPPORTED in _compact(_section(guide, "## Understanding Proposals"))
    unmarked = (
        "## Tips\n\n```python\nfrom towel.changes import apply_changes\n"
        'proposals = engine.analyze_directory("src/")\n```\n'
    )
    assert _unsupported_uses(unmarked) == [
        ("## Tips", "towel.changes"),
        ("## Tips", "analyze_directory"),
    ]
    assert (
        _unsupported_uses(unmarked.replace("## Tips\n", f"## Tips\n\nThis is {UNSUPPORTED}.")) == []
    )


def test_selective_application_example_gives_the_result_of_the_calls_it_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The supported spelling must do what the guide's earlier, internal one did."""
    sources = {
        "utils.py": DUPLICATES,
        "orders.py": DUPLICATES.replace("first", "third").replace("second", "fourth"),
    }
    for name in ("before", "after"):
        (tmp_path / name / "src").mkdir(parents=True)
        for file, source in sources.items():
            (tmp_path / name / "src" / file).write_text(source)

    monkeypatch.chdir(tmp_path / "before")
    engine = UnificationRefactorEngine(max_parameters=5, min_lines=3)
    proposals = engine.analyze_directory("src/", progress="none")
    selected = next(proposal for proposal in proposals if "utils" in proposal.file_path)
    internal_apply_changes(engine.plan_refactoring(selected))

    monkeypatch.chdir(tmp_path / "after")
    section = _section(_guide("USAGE_GUIDE.md"), "### Selective Application")
    example = section.split("```python\n", 1)[1].split("```", 1)[0]
    assert "from towel.api import UnificationRefactorEngine, apply_changes" in example
    exec(compile(example, "docs/USAGE_GUIDE.md", "exec"), {})

    results = {
        name: {file: (tmp_path / name / "src" / file).read_text() for file in sources}
        for name in ("before", "after")
    }
    assert results["after"] == results["before"]
    assert "__extracted_func_0" in results["after"]["utils.py"]
    assert results["after"]["orders.py"] == sources["orders.py"]


def _command_blocks(page: str) -> list[list[str]]:
    """The command lines of each ```bash block, without their trailing comments."""
    return [
        [line.split("  #", 1)[0].rstrip() for line in block.splitlines() if line.strip()]
        for block in re.findall(r"(?ms)^```bash\n(.*?)^```", page)
    ]


def _bare_pip_installs(page: str) -> list[str]:
    """`pip install` lines that would reach a system Python: no environment was activated."""
    return [
        line
        for block in _command_blocks(page)
        for index, line in enumerate(block)
        if re.match(r"(python3? -m )?pip3? install\b", line)
        and not any("/bin/activate" in earlier for earlier in block[:index])
    ]


def test_install_instructions_do_not_depend_on_a_system_pip() -> None:
    """Audit finding 4: the first command a stranger runs must work.

    The README led with `pip install code-towel`. On macOS that is `pip: command not
    found`, and Homebrew's `python3 -m pip` answers externally-managed-environment
    (PEP 668; Debian marks its Python the same way, which was not run here). The
    section now leads with a tool install, then a virtual environment, and still
    warns about the unrelated `towel` distribution.

    Checked against the published 1.949 on macOS, 2026-11-14: `uv tool install
    code-towel` with only the 3.9 of macOS's developer tools on PATH fetched a Python
    and installed 1.949; `pipx install` (pipx run through uvx), the virtual-environment
    recipe on 3.13 and 3.14, the extras, the three upgrade commands and `uvx --from`
    all worked as printed.
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    install = _section(readme, "## Install")
    commands = [line for block in _command_blocks(install) for line in block]
    assert commands[0] == "uv tool install code-towel"
    assert "# or: pipx install code-towel" in install
    assert 'python3 -m venv .venv && .venv/bin/pip install "code-towel>=1.414"' in commands
    assert "belongs to an unrelated project" in _compact(install)
    for page in (readme, _guide("QUICKSTART.md")):
        assert _bare_pip_installs(page) == []
    assert _bare_pip_installs("```bash\npip install code-towel\n```\n") == [
        "pip install code-towel"
    ]
    assert _bare_pip_installs("```bash\npython3 -m pip install code-towel  # system\n```\n")
    activated = "```bash\n. .venv/bin/activate\npip install code-towel\n```\n"
    assert _bare_pip_installs(activated) == []

    classifiers = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["classifiers"]
    minors = sorted(
        (int(minor) for minor in re.findall(r"Python :: 3\.(\d+)", "\n".join(classifiers)))
    )
    supported = f"Towel runs on Python 3.{minors[0]} to 3.{minors[-1]}."
    assert supported in _compact(install), "The Install section states the classifiers' range"


def test_pip_recipes_cannot_fall_back_to_a_withdrawn_release() -> None:
    """Found while checking the install commands against the published 1.949.

    Releases 1.0.0 to 1.0.4 are yanked for broken import handling and accept Python
    3.9; every later release needs 3.11. The pip 21.2.4 that comes with the Python
    3.9.6 of macOS's developer tools therefore answers `pip install code-towel` by
    installing 1.0.4, whose `towel --version` prints 1.0.2. With a lower bound of
    1.414, the first release that is not yanked, the same pip exits 1.
    """
    history = _compact(_guide("maintainers/RELEASING.md"))
    assert "versions `1.0.0` through `1.0.4`; all of their wheel and source artifacts are" in (
        history
    )
    assert "`1.414` (September 15, 2026)" in history
    for page in ((ROOT / "README.md").read_text(encoding="utf-8"), _guide("QUICKSTART.md")):
        installs = [
            line
            for block in _command_blocks(page)
            for line in block
            if "pip install" in line and "code-towel" in line and "--upgrade" not in line
        ]
        assert installs, "Expected a pip-based recipe to check"
        for line in installs:
            assert ">=1.414" in line, line
