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

"""The recovery journal's validation guards, one corrupt journal per guard.

``apply_changes`` writes a journal before replacing any target and
``recover`` trusts nothing in it: the directory's name, owner and mode, the
manifest's shape, every record's fields, each backup's digest and each
target's current bytes are checked before the first file is restored. Each
case here starts from a genuinely interrupted transaction (the second
target's replacement fails and rollback fails too, so the journal is
retained), damages one thing, and asserts the exact refusal. A Hypothesis
round trip then interrupts a plan over several files after a random number
of replacements and checks that ``recover`` restores every byte and mode.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import stat
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple
from unittest.mock import patch

import pytest
from hypothesis import event, given, settings
from hypothesis import strategies as st

from towel.changes import (
    ChangeConflict,
    ChangePlan,
    FileChange,
    RecoveryRequired,
    apply_changes,
    journals_covering,
    recover,
)

BOUNDED = settings(max_examples=50, deadline=None, derandomize=True)


def _plan(
    paths: List[Path], before: bytes = b"value = 1\n", after: str = "value = 2\n"
) -> ChangePlan:
    for path in paths:
        path.write_bytes(before)
        path.chmod(0o640)
    return ChangePlan.from_sources(
        {str(p): p.read_bytes() for p in paths}, {str(p): after for p in paths}
    )


def _interrupted_transaction(root: Path) -> Tuple[List[Path], Path]:
    """Two targets; the first is replaced, the second fails, and rollback fails: journal retained."""
    files = [root / "a.py", root / "b.py"]
    plan = _plan(files)
    original = os.replace
    target_writes = 0

    def fail_after_first(source: Any, target: Any) -> None:
        nonlocal target_writes
        if Path(target) in files:
            target_writes += 1
            if target_writes > 1:
                raise OSError("unavailable filesystem")
        original(source, target)

    with patch("towel.changes.os.replace", side_effect=fail_after_first):
        with pytest.raises(RecoveryRequired):
            apply_changes(plan)
    journal = next(root.glob(".towel-transaction-*"))
    assert files[0].read_bytes() == b"value = 2\n" and files[1].read_bytes() == b"value = 1\n"
    return files, journal


def _rewrite_manifest(journal: Path, edit: Callable[[List[Dict[str, Any]]], Any]) -> None:
    records = json.loads((journal / "manifest.json").read_text())
    replacement = edit(records)
    (journal / "manifest.json").write_text(
        json.dumps(records if replacement is None else replacement)
    )


def _set(field: str, value: Any) -> Callable[[List[Dict[str, Any]]], None]:
    def edit(records: List[Dict[str, Any]]) -> None:
        records[0][field] = value

    return edit


def _duplicate_path(records: List[Dict[str, Any]]) -> None:
    records[1]["path"] = records[0]["path"]


def _damage_backup(journal: Path, files: List[Path]) -> Path:
    (journal / "0").write_bytes(b"value = 1  # tampered\n")
    return journal


def _rename_journal(journal: Path, files: List[Path]) -> Path:
    renamed = journal.with_name(".not-a-transaction")
    journal.rename(renamed)
    return renamed


def _open_permissions(journal: Path, files: List[Path]) -> Path:
    journal.chmod(0o755)
    return journal


def _edit_target_before_recovery(journal: Path, files: List[Path]) -> Path:
    files[0].write_text("edited = True\n")
    return journal


def _manifest(edit: Callable[[List[Dict[str, Any]]], Any]) -> Callable[[Path, List[Path]], Path]:
    def corrupt(journal: Path, files: List[Path]) -> Path:
        _rewrite_manifest(journal, edit)
        return journal

    return corrupt


CORRUPTIONS: List[Tuple[str, Callable[[Path, List[Path]], Path], type, str]] = [
    ("directory-name", _rename_journal, ChangeConflict, "Invalid transaction directory"),
    ("directory-mode", _open_permissions, ChangeConflict, "owner-only journal"),
    (
        "manifest-not-a-list",
        _manifest(lambda records: {"records": records}),
        ChangeConflict,
        "Invalid transaction manifest",
    ),
    (
        "record-not-a-dict",
        _manifest(lambda records: [1]),
        ChangeConflict,
        "Invalid transaction record",
    ),
    (
        "mode-not-an-int",
        _manifest(_set("mode", "640")),
        ChangeConflict,
        "Invalid transaction path/mode",
    ),
    (
        "mode-out-of-range",
        _manifest(_set("mode", 0o10000)),
        ChangeConflict,
        "Invalid transaction path/mode",
    ),
    (
        "path-not-a-string",
        _manifest(_set("path", 7)),
        ChangeConflict,
        "Invalid transaction path/mode",
    ),
    (
        "digest-not-a-string",
        _manifest(_set("before", 5)),
        ChangeConflict,
        "Invalid transaction digests",
    ),
    ("path-escapes-root", _manifest(_set("path", "../a.py")), ChangeConflict, "escapes its root"),
    ("path-absolute", _manifest(_set("path", "/etc/passwd")), ChangeConflict, "escapes its root"),
    (
        "path-duplicated",
        _manifest(_duplicate_path),
        ChangeConflict,
        "escapes its root or is duplicated",
    ),
    ("backup-damaged", _damage_backup, ChangeConflict, "Damaged transaction backup"),
    (
        "target-edited-before-recovery",
        _edit_target_before_recovery,
        ChangeConflict,
        "External edit preserved",
    ),
]


@pytest.mark.parametrize(
    "corrupt, error, message",
    [case[1:] for case in CORRUPTIONS],
    ids=[case[0] for case in CORRUPTIONS],
)
def test_recover_refuses_a_damaged_journal_and_touches_nothing(
    tmp_path: Path,
    corrupt: Callable[[Path, List[Path]], Path],
    error: type,
    message: str,
) -> None:
    files, journal = _interrupted_transaction(tmp_path)
    journal = corrupt(journal, files)
    snapshot = {p: (p.read_bytes(), stat.S_IMODE(p.stat().st_mode)) for p in files}
    with pytest.raises(error, match=message):
        recover(journal)
    assert {p: (p.read_bytes(), stat.S_IMODE(p.stat().st_mode)) for p in files} == snapshot
    assert journal.is_dir(), "a refused journal is retained for the operator"


UNRESTORABLE = [case for case in CORRUPTIONS if case[0] not in ("directory-name", "directory-mode")]


@pytest.mark.parametrize(
    "corrupt", [case[1] for case in UNRESTORABLE], ids=[case[0] for case in UNRESTORABLE]
)
def test_r9p2_a_refusal_to_restore_names_a_step_that_works(
    tmp_path: Path, corrupt: Callable[[Path, List[Path]], Path]
) -> None:
    """Round 4's D6: ``recover`` refused a damaged journal and said nothing more.

    Every refusal to restore from a journal Towel trusts now ends in a
    command that succeeds: moving the journal aside, after which no run
    finds a journal over its files.
    """
    files, journal = _interrupted_transaction(tmp_path)
    journal = corrupt(journal, files)
    with pytest.raises(ChangeConflict) as refused:
        recover(journal)
    named = re.search(r"aside: (mv .+)$", str(refused.value))
    assert named is not None, refused.value
    subprocess.run(shlex.split(named.group(1)), check=True)
    assert not journal.exists()
    assert journals_covering({path.resolve() for path in files}) == []


def test_recover_refuses_a_journal_owned_by_another_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files, journal = _interrupted_transaction(tmp_path)
    monkeypatch.setattr(os, "getuid", lambda: journal.stat().st_uid + 1)
    with pytest.raises(ChangeConflict, match="owned by the current user"):
        recover(journal)
    assert files[0].read_bytes() == b"value = 2\n" and journal.is_dir()


def test_recover_resolves_a_symbolic_link_above_the_journal(tmp_path: Path) -> None:
    """macOS's ``/tmp`` is a link to ``/private/tmp``; a journal named through it is the same one."""
    real = tmp_path / "real"
    real.mkdir()
    files, journal = _interrupted_transaction(real)
    (tmp_path / "alias").symlink_to(real)
    recover(tmp_path / "alias" / journal.name)
    assert all(path.read_bytes() == b"value = 1\n" for path in files)
    assert not journal.exists()


def test_recover_through_a_relative_path_with_parent_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "work").mkdir()
    files, journal = _interrupted_transaction(tmp_path)
    monkeypatch.chdir(tmp_path / "work")
    recover(Path("..") / journal.name)
    assert all(path.read_bytes() == b"value = 1\n" for path in files)


@pytest.mark.parametrize(
    "spell, reason",
    [
        (lambda journal: journal.with_name("missing-journal"), "a journal's name begins with"),
        (lambda journal: journal.with_name(".towel-transaction-gone"), "does not exist"),
    ],
    ids=["not-a-journal-name", "missing"],
)
def test_recover_says_why_a_path_names_no_journal(
    tmp_path: Path, spell: Callable[[Path], Path], reason: str
) -> None:
    _, journal = _interrupted_transaction(tmp_path)
    with pytest.raises(ChangeConflict, match=reason):
        recover(spell(journal))
    assert journal.is_dir()


def test_recover_refuses_a_link_named_like_a_journal_and_says_why(tmp_path: Path) -> None:
    """Followed, the link would restore files beside it rather than beside the journal."""
    (tmp_path / "elsewhere").mkdir()
    files, journal = _interrupted_transaction(tmp_path / "elsewhere")
    link = tmp_path / ".towel-transaction-link"
    link.symlink_to(journal)
    with pytest.raises(ChangeConflict, match="is a symbolic link, not a journal a run wrote"):
        recover(link)
    assert files[0].read_bytes() == b"value = 2\n" and journal.is_dir()


def test_recover_names_the_remedy_for_a_journal_whose_mode_was_changed(tmp_path: Path) -> None:
    files, journal = _interrupted_transaction(tmp_path)
    journal.chmod(0o755)
    with pytest.raises(ChangeConflict, match=f"chmod 700 {journal} and then run towel recover"):
        recover(journal)
    journal.chmod(0o700)
    recover(journal)
    assert all(path.read_bytes() == b"value = 1\n" for path in files)


def test_recover_refuses_a_target_edited_during_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Validation passed; the file changed before its restore step read it back."""
    import towel.changes as changes

    files, journal = _interrupted_transaction(tmp_path)
    original = changes._safe_target
    checks_of_first = 0

    def edit_between_validation_and_restore(path: Path) -> None:
        nonlocal checks_of_first
        if path == files[0]:
            checks_of_first += 1
            if checks_of_first == 2:
                files[0].write_text("edited = True\n")
        original(path)

    monkeypatch.setattr(changes, "_safe_target", edit_between_validation_and_restore)
    with pytest.raises(ChangeConflict, match="External edit during recovery"):
        recover(journal)
    assert files[0].read_text() == "edited = True\n"
    assert files[1].read_bytes() == b"value = 1\n"
    assert journal.is_dir()


def test_recover_without_a_manifest_discards_the_journal(tmp_path: Path) -> None:
    """No manifest means no target was replaced, so there is nothing to restore."""
    files, journal = _interrupted_transaction(tmp_path)
    (journal / "manifest.json").unlink()
    recover(journal)
    assert not journal.exists()
    # Whatever the targets hold is left alone: only the manifest says what to restore.
    assert files[0].read_bytes() == b"value = 2\n" and files[1].read_bytes() == b"value = 1\n"


def test_recover_retains_a_journal_with_an_unexpected_entry(tmp_path: Path) -> None:
    files, journal = _interrupted_transaction(tmp_path)
    (journal / "notes.txt").write_text("not ours\n")
    with pytest.raises(RecoveryRequired, match="Unexpected journal entry"):
        recover(journal)
    # The originals were restored before cleanup refused the stray file.
    assert all(p.read_bytes() == b"value = 1\n" for p in files)
    assert (journal / "complete").is_file() and (journal / "notes.txt").is_file()


def test_recover_is_restartable_once_a_journal_is_marked_complete(tmp_path: Path) -> None:
    files, journal = _interrupted_transaction(tmp_path)
    recover(journal)
    assert all(p.read_bytes() == b"value = 1\n" for p in files) and not journal.exists()


def test_apply_changes_refuses_duplicate_targets_in_one_plan(tmp_path: Path) -> None:
    path = tmp_path / "a.py"
    path.write_bytes(b"value = 1\n")
    change = FileChange(path.resolve(), b"value = 1\n", b"value = 2\n", 0o644)
    with pytest.raises(ChangeConflict, match="Duplicate change targets"):
        apply_changes(ChangePlan((change, change)))
    assert path.read_bytes() == b"value = 1\n" and not list(tmp_path.glob(".towel-*"))


def test_planning_refuses_two_names_for_one_file(tmp_path: Path) -> None:
    """Two spellings of one path would replace the file twice; planning refuses the second."""
    path = tmp_path / "a.py"
    path.write_bytes(b"value = 1\n")
    # A raw string keeps the ``.`` segment that ``Path`` would collapse.
    spellings = [str(path), os.path.join(str(tmp_path), ".", "a.py")]
    assert len(set(spellings)) == 2
    with pytest.raises(ChangeConflict, match="Duplicate change target"):
        ChangePlan.from_sources(
            {name: path.read_bytes() for name in spellings},
            {name: "value = 2\n" for name in spellings},
        )
    assert path.read_bytes() == b"value = 1\n"


def test_apply_changes_with_an_empty_plan_writes_nothing(tmp_path: Path) -> None:
    apply_changes(ChangePlan(()))
    assert not list(tmp_path.glob(".towel-*"))


def test_apply_changes_refuses_a_symlinked_target_in_a_plan(tmp_path: Path) -> None:
    """A plan built elsewhere may name a symlink; the pre-check refuses it before journaling."""
    real = tmp_path / "real.py"
    real.write_bytes(b"value = 1\n")
    link = tmp_path / "alias.py"
    link.symlink_to(real)
    change = FileChange(link, b"value = 1\n", b"value = 2\n", 0o644)
    with pytest.raises(ChangeConflict, match="Symlink or noncanonical target"):
        apply_changes(ChangePlan((change,)))
    assert real.read_bytes() == b"value = 1\n" and not list(tmp_path.glob(".towel-*"))


def test_apply_changes_refuses_a_plan_spanning_filesystems(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One target reports another device: nothing is journaled or written."""
    files = [tmp_path / "a.py", tmp_path / "b.py"]
    plan = _plan(files)
    original_stat = Path.stat
    foreign = files[1].resolve()

    class OnAnotherDevice:
        def __init__(self, result: os.stat_result) -> None:
            self._result = result

        def __getattr__(self, name: str) -> Any:
            if name == "st_dev":
                return self._result.st_dev + 1
            return getattr(self._result, name)

    def stat_with_a_foreign_device(self: Path, *args: Any, **kwargs: Any) -> Any:
        result = original_stat(self, *args, **kwargs)
        return OnAnotherDevice(result) if self == foreign else result

    monkeypatch.setattr(Path, "stat", stat_with_a_foreign_device)
    with pytest.raises(ChangeConflict, match="cannot span filesystems"):
        apply_changes(plan)
    assert all(p.read_bytes() == b"value = 1\n" for p in files)
    assert not list(tmp_path.glob(".towel-*"))


# ---------------------------------------------------------------------------
# Round trip: interrupt after j of k replacements, recover, compare bytes and modes.

MODES = (0o600, 0o640, 0o644, 0o664, 0o755)


class _Crash(BaseException):
    """The process dies: no rollback runs inside ``apply_changes``."""


@st.composite
def interrupted_plans(draw: st.DrawFn) -> Tuple[List[Tuple[bytes, int]], int]:
    count = draw(st.integers(1, 4))
    originals = [
        (
            f"value = {draw(st.integers(0, 3))}\n".encode()
            + draw(st.sampled_from([b"", b"# note\n"])),
            draw(st.sampled_from(MODES)),
        )
        for _ in range(count)
    ]
    return originals, draw(st.integers(0, count))


@BOUNDED
@given(interrupted_plans())
def test_recover_restores_every_byte_and_mode_after_an_interruption(
    tmp_path_factory: pytest.TempPathFactory, case: Tuple[List[Tuple[bytes, int]], int]
) -> None:
    originals, replaced_before_crash = case
    root = tmp_path_factory.mktemp("journal")
    files = [root / f"m{index}.py" for index in range(len(originals))]
    for path, (content, mode) in zip(files, originals):
        path.write_bytes(content)
        path.chmod(mode)
    plan = ChangePlan.from_sources(
        {str(p): p.read_bytes() for p in files},
        {str(p): f"value = {10 + i}\n" for i, p in enumerate(files)},
    )
    assert len(plan.changes) == len(files)
    original_replace = os.replace
    target_replacements = 0

    def crash_after(source: Any, target: Any) -> None:
        nonlocal target_replacements
        if Path(target) in files:
            if target_replacements >= replaced_before_crash:
                raise _Crash()
            target_replacements += 1
        original_replace(source, target)

    # Three outcomes. Nothing replaced: the in-process rollback has nothing to
    # undo, succeeds, and the crash itself propagates with no journal left.
    # Some replaced: rollback's own os.replace crashes too, so the journal is
    # retained for ``recover``. All replaced: the batch committed.
    with patch("towel.changes.os.replace", side_effect=crash_after):
        if replaced_before_crash == 0:
            with pytest.raises(_Crash):
                apply_changes(plan)
        elif replaced_before_crash < len(files):
            with pytest.raises(RecoveryRequired):
                apply_changes(plan)
        else:
            apply_changes(plan)
    journals = list(root.glob(".towel-transaction-*"))
    if 0 < replaced_before_crash < len(files):
        assert len(journals) == 1
        assert (
            sum(p.read_bytes() != content for p, (content, _) in zip(files, originals))
            == replaced_before_crash
        )
        recover(journals[0])
        expected = originals
        event("interrupted after %d of %d" % (replaced_before_crash, len(files)))
    elif replaced_before_crash == 0:
        assert not journals
        expected = originals
        event("crashed before the first replacement")
    else:
        assert not journals
        expected = [(f"value = {10 + i}\n".encode(), mode) for i, (_, mode) in enumerate(originals)]
        event("completed")
    assert [(p.read_bytes(), stat.S_IMODE(p.stat().st_mode)) for p in files] == expected
    assert sorted(p.name for p in root.iterdir()) == sorted(p.name for p in files)
