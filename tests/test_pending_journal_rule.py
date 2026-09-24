"""A pending journal stops an in-place run only when it may name a file the run would change.

The rule is the README's: a journal names files relative to the directory it
sits in, so only one in a directory above a file can name it, and one whose
manifest cannot be read is taken to name every file beneath it. The third
audit found the command line refusing whenever any ``.towel-transaction-*``
path lay under the target: an empty directory of that name, one inside
``.git``, a journal naming only files the run never touches. Each case of
that audit is here, run through the command line in place, with what it
must do; and every refusal's remedy is carried out, as a user would, to
show that it works and the run then proceeds.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Callable, List, Optional

import pytest

from tests.test_cli_integration import CliResult, invoke
from tests.test_recovery_journal import _interrupted_transaction

DUPLICATES = """\
def first(rows):
    print("start")
    total = 0
    for row in rows:
        total += row * 2
    total = total + 1
    return total * 3


def second(items):
    print("begin")
    total = 0
    for row in items:
        total += row * 2
    total = total + 1
    return total * 5
"""


def _project(root: Path) -> Path:
    """The auditor's project: ``pkg`` holds a duplicate to extract and an unrelated module."""
    package = root / "pkg"
    package.mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nname = "p"\nversion = "0"\n')
    (package / "__init__.py").write_text("")
    (package / "mod.py").write_text(DUPLICATES)
    (package / "other.py").write_text("unrelated = 1\n")
    return package


def _crafted_journal(directory: Path, named: str) -> Path:
    """A readable journal whose manifest names ``named``, as the auditor wrote one."""
    directory.mkdir(parents=True)
    (directory / "0").write_text("x")
    record = {"path": named, "mode": 0o644, "before": "00", "after": "11"}
    (directory / "manifest.json").write_text(json.dumps([record]))
    directory.chmod(0o700)
    return directory


def _dry_in_place(package: Path) -> CliResult:
    return invoke(
        [
            "dry",
            str(package),
            str(package),
            "--no-interactive",
            "--no-types",
            "--no-format",
            "--progress",
            "none",
        ]
    )


def _unrelated_journal_in_a_subdirectory(root: Path, package: Path) -> Optional[Path]:
    _crafted_journal(package / "vendor" / ".towel-transaction-0badf00d", "other.py")
    return None


def _journal_inside_git(root: Path, package: Path) -> Optional[Path]:
    _crafted_journal(package / ".git" / ".towel-transaction-0badf00d", "other.py")
    return None


def _empty_directory_beside_no_source(root: Path, package: Path) -> Optional[Path]:
    (package / "data" / ".towel-transaction-x").mkdir(parents=True)
    return None


def _readable_ancestor_journal_naming_another_file(root: Path, package: Path) -> Optional[Path]:
    _crafted_journal(root / ".towel-transaction-deadbeef", "zzz")
    return None


def _interrupted_transaction_over_the_package(root: Path, package: Path) -> Optional[Path]:
    _, journal = _interrupted_transaction(package)
    return journal


def _journal_without_a_manifest_above_the_project(root: Path, package: Path) -> Optional[Path]:
    """An interrupted run's journal before its manifest was durable: it covers all beneath it."""
    journal = root / ".towel-transaction-5eed0001"
    journal.mkdir(mode=0o700)
    (journal / "0").write_bytes(b"value = 1\n")
    (journal / "manifest.pending").write_text("[]")
    return journal


def _empty_directory_at_the_target(root: Path, package: Path) -> Optional[Path]:
    """Not a journal a run wrote (its mode is not 0700), but it may still cover every file."""
    journal = package / ".towel-transaction-x"
    journal.mkdir()
    journal.chmod(0o755)
    return journal


Case = Callable[[Path, Path], Optional[Path]]

PROCEEDS: List[Case] = [
    _unrelated_journal_in_a_subdirectory,
    _journal_inside_git,
    _empty_directory_beside_no_source,
    _readable_ancestor_journal_naming_another_file,
]
REFUSES: List[Case] = [
    _interrupted_transaction_over_the_package,
    _journal_without_a_manifest_above_the_project,
    _empty_directory_at_the_target,
]


@pytest.mark.parametrize("case", PROCEEDS, ids=[case.__name__.strip("_") for case in PROCEEDS])
def test_a_journal_that_names_no_file_the_run_changes_does_not_stop_it(
    tmp_path: Path, case: Case
) -> None:
    package = _project(tmp_path / "project")
    case(tmp_path / "project", package)
    result = _dry_in_place(package)
    assert result.status == 0, result.stderr
    assert "Applied 1 refactoring" in result.stdout
    assert (package / "mod.py").read_text() != DUPLICATES


def _carry_out(remedy: str) -> None:
    """Do what a refusal says, in order: restore a journal's mode, then recover it."""
    for mode, path in re.findall(r"chmod (\d+) (/\S+?)(?=,|;| |$)", remedy):
        os.chmod(path, int(mode, 8))
    commands = re.findall(r"towel recover (/\S+?)(?=,|;| |$)", remedy)
    assert commands, remedy
    result = invoke(["recover", commands[-1]])
    assert result.status == 0, result.stderr


@pytest.mark.parametrize("case", REFUSES, ids=[case.__name__.strip("_") for case in REFUSES])
def test_a_journal_that_may_name_a_changed_file_stops_the_run_with_a_remedy_that_works(
    tmp_path: Path, case: Case
) -> None:
    root = tmp_path / "project"
    package = _project(root)
    journal = case(root, package)
    assert journal is not None
    refused = _dry_in_place(package)
    assert refused.status == 1
    assert refused.stderr.startswith("Error: Refusing to refactor in place: ")
    assert str(journal) in refused.stderr
    assert (package / "mod.py").read_text() == DUPLICATES
    _carry_out(refused.stderr.strip())
    assert not journal.exists()
    result = _dry_in_place(package)
    assert result.status == 0, result.stderr
    assert "Applied 1 refactoring" in result.stdout


def test_a_journal_another_user_owns_names_its_owner_as_the_one_to_recover_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _project(tmp_path / "project")
    _, journal = _interrupted_transaction(package)
    monkeypatch.setattr(os, "getuid", lambda: journal.stat().st_uid + 1)
    refused = _dry_in_place(package)
    assert refused.status == 1
    assert "belongs to another user" in refused.stderr
    assert "only its owner can recover it" in refused.stderr
    assert (package / "mod.py").read_text() == DUPLICATES


def test_preview_warns_only_of_a_journal_that_may_name_its_files(tmp_path: Path) -> None:
    package = _project(tmp_path / "project")
    _journal_inside_git(tmp_path / "project", package)
    quiet = invoke(["preview", str(package)])
    assert quiet.status == 0 and "journal" not in quiet.stderr
    _, journal = _interrupted_transaction(package)
    warned = invoke(["preview", str(package)])
    assert warned.status == 0
    assert f"recover it first: towel recover {journal}" in warned.stderr
