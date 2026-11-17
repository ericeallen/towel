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

"""Every command a user page shows is run, and every output it shows is what the command printed.

The user-perspective audit of 1.949 found the guides accurate but hard to use:
the quick start told the reader to run `diff` and never showed the result, and
no page showed Towel doing anything. Towel 2.0's pages show the output of the
commands they give, so that output must not drift from the tool.

A page is read top to bottom, as a reader follows it, in a fresh directory:

- A fenced block whose preceding paragraph says "Save this as `PATH`" is written
  to PATH.
- Every ```bash block is run, one command at a time, in that directory, with
  `towel` and `python3` taken from the environment running the tests, and the
  reader answers yes to any question it asks. A command
  must exit 0 unless its comment says otherwise (`# exits 3`); `diff` may exit 1.
  Commands that install software, or use an environment such a command made,
  are not run (`NOT_RUN`); everything else is.
- A ```text, ```diff or ```json block that follows a ```bash block with nothing
  but blank lines between is the output of that block's commands: what they
  print to stdout and stderr together, as a terminal shows it. It must match
  exactly, line for line, after `_normalized` replaces what differs from run to
  run (the scratch directory, timestamps in `diff` headers, test timings). A line
  of the page holding only `...` stands for any number of lines.

Strict mode's output is compared only where strict mode's lifetime proofs are
certified (CPython 3.11 to 3.13); elsewhere its commands still run and must
succeed. The pages say what strict mode does on 3.14 and later.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Dict, List, Optional, Tuple

import pytest

from towel.unification import argument_ownership

pytestmark = pytest.mark.relaxed_mode

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = ROOT / "tests" / "documentation_projects"

OUTPUT_LANGUAGES = ("text", "diff", "json")
NOT_RUN: Tuple[Tuple[str, str], ...] = (
    (r"^(uv tool|pipx|uvx) ", "installs or runs a published release"),
    (r"^python3 -m venv ", "creates an environment and installs a published release into it"),
    (r"^(\.venv/bin/)?pip3? install ", "installs a published release"),
    (r"^\.venv/bin/", "uses the environment the page's skipped install created"),
    (r"^pre-commit ", "runs pre-commit, which fetches the hook from GitHub"),
)
"""Commands a page shows that the test does not run, each with the reason."""

HOME = "/home/you"
"""What the scratch directory reads as on a page, so a path a command prints is the reader's."""


@dataclass(frozen=True)
class Fence:
    """One fenced block of a page, with the paragraph before it."""

    language: str
    body: str
    line: int
    preceding: str
    adjacent_to_previous: bool


@dataclass(frozen=True)
class Command:
    """One command of a ```bash block and the exit statuses it may have."""

    text: str
    statuses: Tuple[int, ...]


def fences(page: str) -> List[Fence]:
    """The fenced blocks of a page in order, each with the prose that leads into it."""
    found: List[Fence] = []
    lines = page.splitlines()
    index = 0
    prose: List[str] = []
    since_previous: List[str] = []
    while index < len(lines):
        opening = re.match(r"^(`{3,})([\w+-]*)", lines[index])
        if not opening:
            prose.append(lines[index])
            since_previous.append(lines[index])
            index += 1
            continue
        marker, language = opening.groups()
        end = index + 1
        while end < len(lines) and not lines[end].startswith(marker):
            end += 1
        paragraph = "\n".join(prose).strip().split("\n\n")[-1]
        found.append(
            Fence(
                language=language,
                body="\n".join(lines[index + 1 : end]) + "\n",
                line=index + 1,
                preceding=paragraph,
                adjacent_to_previous=bool(found) and not "".join(since_previous).strip(),
            )
        )
        prose, since_previous = [], []
        index = end + 1
    return found


def commands(block: str) -> List[Command]:
    """The commands of a ```bash block: continuation lines joined, comments read for statuses."""
    found: List[Command] = []
    pending = ""
    for line in block.splitlines():
        if not pending and (not line.strip() or line.lstrip().startswith("#")):
            continue
        if line.rstrip().endswith("\\"):
            pending += line.rstrip()[:-1] + " "
            continue
        text, _, comment = (pending + line).partition("  #")
        pending = ""
        status = re.search(r"\bexits (\d+)", comment)
        if status:
            statuses: Tuple[int, ...] = (int(status.group(1)),)
        elif text.startswith("diff "):
            statuses = (0, 1)
        else:
            statuses = (0,)
        found.append(Command(text.strip(), statuses))
    assert not pending, f"a command ends in a continuation: {pending!r}"
    return found


def not_run_because(command: str) -> Optional[str]:
    return next((reason for pattern, reason in NOT_RUN if re.match(pattern, command)), None)


def saved_path(fence: Fence) -> Optional[str]:
    """The file a block is saved as, when the paragraph before it says so."""
    named = re.search(r"[Ss]ave (?:this|it) as `([^`]+)`", fence.preceding)
    return named.group(1) if named else None


def _timestamps(text: str) -> str:
    """`diff -u` headers end in the files' modification times."""
    return re.sub(r"(?m)^((?:---|\+\+\+) \S+)\t.*$", r"\1", text)


NOT_A_TERMINAL = "(Input is not a terminal; to write without this question, pass --no-interactive.)"


def _as_a_terminal_shows(text: str) -> str:
    """A question answered at a terminal shows the answer, and no note that input is piped."""
    text = "\n".join(line for line in text.splitlines() if line != NOT_A_TERMINAL)
    return re.sub(r"(?m)\[y/N\] ?$", "[y/N] y", text)


def _normalized(text: str, scratch: Path) -> str:
    text = _as_a_terminal_shows(text)
    for spelling in sorted({str(scratch.resolve()), str(scratch)}, key=len, reverse=True):
        text = text.replace(spelling, HOME)
    text = _timestamps(text)
    text = re.sub(r"(?m)^(Ran \d+ tests? in )\d+\.\d+s$", r"\g<1>0.000s", text)
    return "\n".join(line.rstrip() for line in text.splitlines()).strip("\n")


def matches(shown: str, printed: str) -> bool:
    """Whether output a page shows is what a command printed, `...` lines matching any lines."""
    pattern = "".join(
        r"(?:[^\n]*\n)*?" if line == "..." else re.escape(line) + r"\n"
        for line in shown.splitlines()
    )
    return re.fullmatch(pattern, printed + "\n") is not None


def strict_output_certified() -> bool:
    return argument_ownership._supported_runtime()


def _environment(scratch: Path) -> Dict[str, str]:
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("TOWEL_", "PYTHON", "GIT_", "COVERAGE_PROCESS"))
    }
    environment.update(
        PATH=f"{Path(sys.executable).parent}{os.pathsep}{environment.get('PATH', '')}",
        TOWEL_WORKERS="2",
        COLUMNS="100",
        TERM="dumb",
        NO_COLOR="1",
        # A terminal shows stdout and stderr in the order they were written.
        PYTHONUNBUFFERED="1",
        HOME=str(scratch),
        GIT_AUTHOR_NAME="You",
        GIT_AUTHOR_EMAIL="you@example.com",
        GIT_COMMITTER_NAME="You",
        GIT_COMMITTER_EMAIL="you@example.com",
        GIT_CONFIG_NOSYSTEM="1",
    )
    return environment


def run(command: Command, scratch: Path, where: str) -> str:
    """Run one command as a reader's shell would; return what the terminal showed."""
    completed = subprocess.run(
        ["bash", "-c", command.text],
        cwd=scratch,
        env=_environment(scratch),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        input="y\n",
        text=True,
        timeout=600,
        check=False,
    )
    assert completed.returncode in command.statuses, (
        f"{where}: `{command.text}` exited {completed.returncode}, "
        f"expected {' or '.join(map(str, command.statuses))}:\n{completed.stdout}"
    )
    return completed.stdout


@dataclass(frozen=True)
class Page:
    """A page whose commands are run, and the files its reader is assumed to have."""

    path: str
    project: Optional[str] = None
    git: bool = False


PAGES: Tuple[Page, ...] = (
    Page("README.md", project="readme"),
    Page("docs/QUICKSTART.md"),
    Page("docs/GENERATED_CODE.md", project="formatted"),
    Page("docs/WHY_LEFT_ALONE.md"),
    Page("docs/RELAXED_AND_STRICT.md", project="formatted"),
    Page("docs/KNOWN_LIMITATIONS.md", project="formatted"),
    Page("docs/RECIPES.md", project="recipes", git=True),
)
"""Every user page that shows a command; `test_every_page_that_shows_a_command_is_run` keeps it complete."""


def _prepare(page: Page, scratch: Path) -> None:
    if page.project:
        # The projects' Python files end in .py.txt, so the repository's checks and
        # licence headers leave them alone; the reader's copies are plain .py files.
        source = PROJECTS / page.project
        for path in sorted(source.rglob("*")):
            if path.is_file():
                relative = path.relative_to(source).as_posix()
                if relative.endswith(".py.txt"):
                    relative = relative.removesuffix(".txt")
                destination = scratch / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, destination)
    if page.git:
        environment = _environment(scratch)
        for command in (
            ["git", "init", "-q", "-b", "main"],
            ["git", "add", "-A"],
            ["git", "commit", "-q", "-m", "Before Towel"],
        ):
            subprocess.run(command, cwd=scratch, env=environment, check=True)


@dataclass(frozen=True)
class Transcript:
    """What following a page did: each command run, each skipped, each output compared."""

    ran: Tuple[str, ...]
    skipped: Tuple[Tuple[str, str], ...]
    compared: int


def follow(page: Page, scratch: Path) -> Transcript:
    """Do what the page says, in order, and check every output it shows."""
    _prepare(page, scratch)
    text = (ROOT / page.path).read_text(encoding="utf-8")
    ran: List[str] = []
    skipped: List[Tuple[str, str]] = []
    compared = 0
    printed: Optional[Tuple[str, bool]] = None
    for fence in fences(text):
        where = f"{page.path}:{fence.line}"
        target = saved_path(fence)
        if target:
            destination = scratch / target
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(fence.body, encoding="utf-8")
            printed = None
        elif fence.language == "bash":
            outputs: List[str] = []
            strict = False
            for command in commands(fence.body):
                reason = not_run_because(command.text)
                if reason:
                    skipped.append((command.text, reason))
                    continue
                outputs.append(run(command, scratch, where))
                ran.append(command.text)
                strict = strict or "--strict" in command.text.split()
            printed = ("".join(outputs), strict)
            continue
        elif fence.language in OUTPUT_LANGUAGES and fence.adjacent_to_previous and printed:
            output, strict = printed
            if not strict or strict_output_certified():
                actual = _normalized(output, scratch)
                shown = _normalized(fence.body, scratch)
                assert matches(
                    shown, actual
                ), f"{where}: the page shows\n{shown}\n\nbut the commands printed\n{actual}"
                compared += 1
        printed = None
    return Transcript(tuple(ran), tuple(skipped), compared)


@pytest.mark.parametrize("page", PAGES, ids=[page.path for page in PAGES])
def test_a_page_does_what_it_says(page: Page, tmp_path: Path) -> None:
    scratch = tmp_path / "you"
    scratch.mkdir()
    transcript = follow(page, scratch)
    assert transcript.ran, f"{page.path}: no command was run"


def _shows_a_command(text: str) -> bool:
    return any(fence.language == "bash" for fence in fences(text))


def _user_pages() -> List[str]:
    pages = ["README.md"]
    for path in sorted((ROOT / "docs").rglob("*.md")):
        relative = path.relative_to(ROOT).as_posix()
        if not relative.startswith(("docs/design/", "docs/maintainers/")):
            pages.append(relative)
    return pages


def test_every_page_that_shows_a_command_is_run() -> None:
    """A new page with a command block cannot be left out of `PAGES`."""
    listed = {page.path for page in PAGES}
    showing = [
        path
        for path in _user_pages()
        if _shows_a_command((ROOT / path).read_text(encoding="utf-8"))
    ]
    assert sorted(set(showing) - listed) == []


def test_an_output_that_differs_is_reported(tmp_path: Path) -> None:
    """The comparison fails on a changed line, so a page cannot drift silently."""
    assert matches("Would apply 1 refactoring(s).", "Would apply 1 refactoring(s).")
    assert not matches("Would apply 2 refactoring(s).", "Would apply 1 refactoring(s).")
    assert matches("first\n...\nlast", "first\nsecond\nthird\nlast")
    assert matches("first\n...\nlast", "first\nlast")
    assert not matches("first\n...\nlast", "first\nsecond")
    assert not matches("first", "first\nsecond")


def test_the_reader_s_directory_timestamps_and_timings_are_normalized(tmp_path: Path) -> None:
    printed = (
        f"Using [tool.towel] from {tmp_path}/demo/pyproject.toml\n"
        "--- demo/charges.py\t2026-11-17 06:32:16\n"
        "+++ cleaned/charges.py\t2026-11-17 06:32:36.123 +0000\n"
        "Ran 1 test in 0.012s\n"
        f"{NOT_A_TERMINAL}\n"
        "Write these changes? [y/N] \n"
    )
    assert _normalized(printed, tmp_path) == (
        f"Using [tool.towel] from {HOME}/demo/pyproject.toml\n"
        "--- demo/charges.py\n+++ cleaned/charges.py\nRan 1 test in 0.000s\n"
        "Write these changes? [y/N] y"
    )


def test_commands_are_read_as_a_shell_reads_them() -> None:
    block = (
        "# a comment\n"
        "towel preview demo\n"
        "towel preview demo --check   # exits 3 when Towel would change code\n"
        "diff -u a b\n"
        "towel dry demo \\\n    cleaned\n"
    )
    assert commands(block) == [
        Command("towel preview demo", (0,)),
        Command("towel preview demo --check", (3,)),
        Command("diff -u a b", (0, 1)),
        Command("towel dry demo      cleaned", (0,)),
    ]
    assert not_run_because("uv tool install code-towel") is not None
    assert not_run_because("towel preview .") is None


def test_a_saved_file_and_an_adjacent_output_are_recognized() -> None:
    page = (
        "Save this as `demo/a.py`:\n\n```python\nx = 1\n```\n\n"
        "```bash\ntowel preview demo\n```\n\n```text\nout\n```\n\n"
        "Then:\n\n```text\nnot an output\n```\n"
    )
    found = fences(page)
    assert [saved_path(fence) for fence in found] == ["demo/a.py", None, None, None]
    assert [fence.adjacent_to_previous for fence in found] == [False, True, True, False]


def test_the_hook_the_recipes_page_names_is_the_one_towel_ships() -> None:
    """pre-commit is not run (it would fetch the hook from GitHub); its manifest is read instead."""
    manifest = (ROOT / ".pre-commit-hooks.yaml").read_text(encoding="utf-8")
    entry = re.search(r"(?m)^  entry: (.+)$", manifest)
    assert entry is not None and re.search(r"(?m)^- id: towel-check$", manifest)
    recipes = (ROOT / "docs" / "RECIPES.md").read_text(encoding="utf-8")
    assert "- id: towel-check" in recipes
    assert f"`{entry.group(1)}`" in recipes
