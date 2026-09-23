"""Concurrent corpus projects must not refuse each other over a shared journal.

``apply_changes`` writes its recovery journal to the common parent of the files
a transaction changes, and refuses to start while a pending journal that may
cover its targets sits at any ancestor of them. A one-module project's single
output file has its output directory as that common parent, so a harness that
wrote every project's output straight into ``--work`` put one project's journal
at an ancestor of all the others: peewee's journal stopped astroid mid-run at
``--workers 4``. The harness therefore gives each project an output directory of
its own. These tests pin the harness layout and the transaction rule that makes
it necessary, so neither can drift back.
"""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Sequence

import pytest

from scripts import ecosystem_check as ecosystem
from towel.changes import ChangePlan, FileChange, RecoveryRequired, apply_changes

CANDIDATE = ecosystem.Candidate(
    Path("code_towel-0-py3-none-any.whl"), "code-towel", "0", "0", ("mypy", "pyright"), ()
)
ENVIRONMENT = ecosystem.ProjectEnvironment(
    Path(sys.executable), None, ecosystem.Environment("3", "0", ())
)


def _module_project(name: str) -> ecosystem.Project:
    """A one-module project, the shape whose output file sits in its parent."""
    return ecosystem.Project(name, "unused", "pinned", "package.py")


def _refactor_output(
    root: Path, project: ecosystem.Project, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Run ``check_project`` far enough to capture the output path it chooses."""
    source = root / project.name
    source.mkdir(parents=True)
    (source / "package.py").write_text("value = 1\n")
    monkeypatch.setattr(ecosystem, "clone", lambda *_: "pinned")
    monkeypatch.setattr(ecosystem, "environment", lambda *_: ENVIRONMENT)
    monkeypatch.setattr(ecosystem, "base_env", lambda *_: {})
    monkeypatch.setattr(ecosystem, "changed", lambda *_: (0, "fixture"))
    captured: list[Path] = []

    def run(
        command: Sequence[str], cwd: Path, env: dict[str, str], timeout: int, log: Path
    ) -> ecosystem.Phase:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("1 passed\n")
        if Path(command[0]).name == "towel":
            output = Path(command[command.index("dry") + 2])
            captured.append(output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("value = 2\n")
            return ecosystem.Phase(0, 0.0, "Applied 1 refactoring", str(log))
        return ecosystem.Phase(0, 0.0, ecosystem.summarize("1 passed\n"), str(log))

    monkeypatch.setattr(ecosystem, "run", run)
    ecosystem.check_project(project, root, CANDIDATE, 10, True)
    assert captured, "check_project never invoked the refactor"
    return captured[0]


def test_output_is_not_written_directly_into_the_work_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = _refactor_output(tmp_path, _module_project("peewee"), monkeypatch)
    assert output.parent != tmp_path, (
        "A one-module project's output file sits directly in its parent, so a parent "
        "of --work puts its recovery journal above every other project"
    )
    assert tmp_path in output.parents


def test_two_projects_never_share_an_output_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # One --work directory, two projects: the arrangement a corpus run uses.
    first = _refactor_output(tmp_path, _module_project("peewee"), monkeypatch)
    second = _refactor_output(tmp_path, _module_project("astroid"), monkeypatch)
    assert first.parent != second.parent
    # Neither project's output parent may be an ancestor of the other's targets,
    # which is the condition ``apply_changes`` scans for.
    assert first.parent not in second.parents
    assert second.parent not in first.parents


def _plan(path: Path) -> ChangePlan:
    path.write_text("value = 1\n")
    return ChangePlan((FileChange(path.resolve(), b"value = 1\n", b"value = 2\n", 0o644),))


def _journal_at(parent: Path) -> Path:
    """A pending journal with no durable manifest, as an interrupted run leaves."""
    journal = parent / ".towel-transaction-deadbeef"
    journal.mkdir(mode=0o700, parents=True)
    return journal


def test_a_pending_journal_blocks_a_transaction_beneath_it(tmp_path: Path) -> None:
    """The rule the layout works around: an ancestor's journal covers everything."""
    _journal_at(tmp_path)
    shared = tmp_path / "other-cleaned"
    with pytest.raises(RecoveryRequired):
        apply_changes(_plan(shared))


def test_a_sibling_projects_journal_leaves_another_project_alone(tmp_path: Path) -> None:
    """With one directory per project, a pending journal is not an ancestor."""
    _journal_at(tmp_path / "peewee-out")
    (tmp_path / "astroid-out").mkdir()
    apply_changes(_plan(tmp_path / "astroid-out" / "astroid-cleaned"))
    assert (tmp_path / "astroid-out" / "astroid-cleaned").read_text() == "value = 2\n"
