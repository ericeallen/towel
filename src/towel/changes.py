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

"""Immutable byte changes with atomic replacement and recoverable rollback.

A batch is not globally atomic to concurrent readers. Its journal is durable before
any target is replaced; interrupted batches can be rolled back with ``recover``.
Detected external edits stop rollback. Exclusive write access is required during
application and recovery; portable check/replace cannot exclude racing writers.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import secrets
from pathlib import Path
import shlex
from .source_text import encode_like
import stat
import tempfile
from typing import List, Mapping, Optional, Set, Tuple, TypedDict

from .diagnostics import LOG


class ChangeConflict(ValueError):
    """A target changed after planning, or cannot safely be replaced."""


class StaleSource(ChangeConflict):
    """Source bytes or mode changed; a fresh analysis may produce a valid plan."""


class RecoveryRequired(OSError):
    """Rollback could not finish; the journal must be retained for recovery."""


class ExternalEdit(ChangeConflict):
    """A file a journal would restore was edited since; recovering would discard the edit."""


JOURNAL_PREFIX = ".towel-transaction-"
"""How the name of every journal begins; the rest is random, so concurrent runs never share one."""


@dataclass(frozen=True)
class FileChange:
    """One file's bytes before and after the change, with the mode to write it back under."""

    path: Path
    before: bytes
    after: bytes
    mode: int


@dataclass(frozen=True)
class ChangePlan:
    """The file changes of one refactoring, applied together or not at all."""

    changes: tuple[FileChange, ...]

    @classmethod
    def from_sources(cls, before: Mapping[str, bytes], after: Mapping[str, str]) -> ChangePlan:
        """The plan that takes each file from its original bytes to its new source.

        Every target must be a regular file, named once; each new source must
        compile. Files whose bytes would not change are left out.
        """
        changes = []
        seen: set[Path] = set()
        for name, source in sorted(after.items()):
            path = Path(name).resolve()
            if Path(name).is_symlink():
                raise ChangeConflict(f"Symlink target: {name}")
            _safe_target(path)
            if path in seen:
                raise ChangeConflict(f"Duplicate change target: {path}")
            seen.add(path)
            original = before[name]
            updated = encode_like(original, source)
            compile(updated, str(path), "exec")
            if original != updated:
                changes.append(
                    FileChange(path, original, updated, stat.S_IMODE(path.stat().st_mode))
                )
        return cls(tuple(changes))


def _safe_target(path: Path) -> None:
    if path.resolve() != path:
        raise ChangeConflict(f"Symlink or noncanonical target: {path}")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ChangeConflict(f"Target must be a regular file with one link: {path}")


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_and_sync(descriptor: int, content: bytes, mode: int) -> None:
    """Write ``content`` to an open descriptor and flush it durably to disk."""
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fchmod(stream.fileno(), mode)
        os.fsync(stream.fileno())


def _write_new(path: Path, content: bytes, mode: int = 0o600) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    _write_and_sync(descriptor, content, mode)


def _replace(path: Path, content: bytes, mode: int) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".towel-stage-", dir=path.parent)
    temporary = Path(name)
    try:
        _write_and_sync(descriptor, content, mode)
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _check(change: FileChange, expected: bytes) -> None:
    _safe_target(change.path)
    if (
        change.path.read_bytes() != expected
        or stat.S_IMODE(change.path.stat().st_mode) != change.mode
    ):
        raise StaleSource(f"Target changed since planning: {change.path}")


class _ManifestRecord(TypedDict):
    """One entry of the transaction manifest: a target and the digests either side of it."""

    path: str
    mode: int
    before: str
    after: str


def _manifest_record(record: object) -> _ManifestRecord:
    """``record`` as the manifest writer produced it, or a ChangeConflict naming what is wrong."""
    if not isinstance(record, dict):
        raise ChangeConflict("Invalid transaction record")
    relative, mode = record.get("path"), record.get("mode")
    before, after = record.get("before"), record.get("after")
    if not isinstance(relative, str) or not isinstance(mode, int) or not 0 <= mode <= 0o7777:
        raise ChangeConflict("Invalid transaction path/mode")
    if not isinstance(before, str) or not isinstance(after, str):
        raise ChangeConflict("Invalid transaction digests")
    return {"path": relative, "mode": mode, "before": before, "after": after}


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _foreign_entries(journal: Path) -> List[str]:
    """The names in ``journal`` that no journal writes, which ``_cleanup`` will not remove."""
    return sorted(
        path.name
        for path in journal.iterdir()
        if path.name not in ("manifest.json", "manifest.pending", "complete", "complete.pending")
        and not path.name.removesuffix(".after").isdecimal()
    )


def _cleanup(journal: Path) -> None:
    # Only files created by this journal format, never an arbitrary tree.
    entries = sorted(journal.iterdir(), key=lambda item: item.name == "complete")
    if _foreign_entries(journal):
        raise RecoveryRequired(f"Unexpected journal entry; retained {journal}")
    for path in entries:
        path.unlink()
    journal.rmdir()
    _sync_directory(journal.parent)


def _named_files(journal: Path) -> Optional[Set[Path]]:
    """The files a pending journal's manifest names, or None when it has no readable one."""
    try:
        records: object = json.loads((journal / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    if not isinstance(records, list):
        return None
    named: Set[Path] = set()
    for record in records:
        relative = record.get("path") if isinstance(record, dict) else None
        if not isinstance(relative, str):
            return None
        named.add(journal.parent / relative)
    return named


def _journal_covers(journal: Path, targets: Set[Path]) -> bool:
    """Whether a pending journal concerns any of ``targets``.

    A journal sits at the common parent of the files its transaction
    changed, so one at a shared ancestor (a file refactored directly in the
    temporary directory, say) must not block every unrelated run beneath
    it. Its manifest names the files; a journal whose manifest is not yet
    durable, or not readable, is taken to cover everything beneath it.
    """
    named = _named_files(journal)
    return named is None or bool(named & targets)


def journals_covering(targets: Set[Path]) -> List[Path]:
    """The pending journals that may concern any of ``targets``, which are resolved paths.

    A journal names files relative to the directory it sits in, so only one
    in a directory holding a target, or above it, can name one; any other,
    beneath the targets' common directory or not, concerns none of them.
    """
    parents = {parent for target in targets for parent in target.parents}
    return sorted(
        path
        for parent in parents
        for path in parent.glob(f"{JOURNAL_PREFIX}*")
        if _journal_covers(path, targets)
    )


SET_ASIDE_PREFIX = ".towel-set-aside-"
"""What a journal recovery cannot use is renamed to; no run reads a name beginning so."""


def _set_aside(journal: Path) -> str:
    """The command that moves ``journal`` out of every run's way, keeping what it holds."""
    aside = journal.parent / (SET_ASIDE_PREFIX + journal.name.removeprefix(JOURNAL_PREFIX))
    return f"mv {shlex.quote(str(journal))} {shlex.quote(str(aside))}"


def _instead_of_recovery(journal: Path, error: ChangeConflict) -> str:
    """What works when ``recover`` refuses the trusted ``journal`` for ``error``.

    An edit made since the interrupted change can be resolved, and recovery
    then proceeds. Anything else means recovery would restore the wrong
    bytes or none, so the files are left to the user and the journal is
    moved aside, where it blocks no run and keeps the bytes it holds.
    """
    if isinstance(error, ExternalEdit):
        return (
            "Recovering would discard that edit: resolve it, so the file holds what the"
            " interrupted change left or what it replaced, and then run towel recover"
            f" {journal}; or keep the files as they are and move the journal aside:"
            f" {_set_aside(journal)}"
        )
    return (
        "Its numbered files hold the bytes the interrupted change replaced; restore by hand"
        f" whatever you need from them, then move it aside: {_set_aside(journal)}"
    )


def _recovery_obstacle(journal: Path) -> Optional[ChangeConflict]:
    """Why ``recover`` would refuse the journal directory ``journal`` once trusted; None if it would not.

    The same checks ``recover`` makes, and nothing written, so that a remedy
    naming ``towel recover`` names a command that succeeds.
    """
    try:
        _restoration(journal)
    except ChangeConflict as error:
        return error
    foreign = _foreign_entries(journal)
    if foreign:
        return ChangeConflict(f"it holds {', '.join(foreign)}, which no journal a run writes holds")
    return None


def pending_journal_remedy(journal: Path, changing: str = "this change writes") -> str:
    """Why the pending ``journal`` stands in the way of a file ``changing``, and what resolves it.

    That is ``towel recover`` where recovery will read the journal and
    restore from it, and otherwise why it will not and what to do instead,
    so that a refusal never ends in a command that fails too.
    """
    if _named_files(journal) is None:
        why = (
            f"{journal}, a pending transaction journal, has no manifest that can be read, so it"
            f" is taken to name every file beneath {journal.parent}, including one {changing}"
        )
    else:
        why = f"{journal}, a pending transaction journal, names a file {changing}"
    obstacle: Optional[ChangeConflict] = None
    try:
        directory = _journal_directory(journal)
        distrust = _distrust(directory)
    except ChangeConflict as error:
        distrust = str(error).removeprefix("Invalid transaction directory: ")
    except OSError as error:
        distrust = f"{journal} cannot be examined ({error})"
    else:
        try:
            obstacle = _recovery_obstacle(directory)
        except OSError as error:
            # Unreadable to a journal's owner, it cannot be restored from;
            # to anyone else, who may read it is what stops them.
            obstacle = ChangeConflict(str(error)) if distrust is None else None
    # A journal recovery could not restore from is moved aside, whoever may
    # read it, so what stops recovery is said before who may.
    if obstacle is not None:
        return (
            f"{why}, and towel recover cannot restore from it as it stands: {obstacle}."
            f" {_instead_of_recovery(journal, obstacle)}"
        )
    if distrust is None:
        return f"{why}; recover it first: towel recover {journal}"
    return f"{why}, and towel recover will not read it as it stands: {distrust}"


def apply_changes(plan: ChangePlan) -> None:
    """Apply a complete plan, rolling back caught failures including interruptions."""
    if os.name != "posix":
        raise ValueError("Recoverable application currently requires POSIX filesystem semantics")
    if not plan.changes:
        return
    if len({change.path for change in plan.changes}) != len(plan.changes):
        raise ChangeConflict("Duplicate change targets")
    for change in plan.changes:
        compile(change.after, str(change.path), "exec")
        _check(change, change.before)
    root = Path(os.path.commonpath([str(change.path.parent) for change in plan.changes]))
    pending = journals_covering({change.path for change in plan.changes})
    if pending:
        raise RecoveryRequired(pending_journal_remedy(pending[0]))
    if any(change.path.stat().st_dev != root.stat().st_dev for change in plan.changes):
        raise ChangeConflict("A transaction cannot span filesystems")
    journal = root / f"{JOURNAL_PREFIX}{secrets.token_hex(4)}"
    try:
        journal.mkdir(mode=0o700)
    except FileExistsError as error:
        raise RecoveryRequired(f"Another transaction is in progress: {journal}") from error
    ready = False
    try:
        records = []
        for index, change in enumerate(plan.changes):
            _write_new(journal / str(index), change.before)
            _write_new(journal / f"{index}.after", change.after, change.mode)
            records.append(
                {
                    "path": str(change.path.relative_to(root)),
                    "mode": change.mode,
                    "before": _digest(change.before),
                    "after": _digest(change.after),
                }
            )
        _write_new(journal / "manifest.pending", json.dumps(records).encode("utf-8"))
        os.replace(journal / "manifest.pending", journal / "manifest.json")
        _sync_directory(journal)
        _sync_directory(root)
        ready = True
        for index, change in enumerate(plan.changes):
            _check(change, change.before)
            os.replace(journal / f"{index}.after", change.path)
            _sync_directory(change.path.parent)
        _write_new(journal / "complete.pending", b"committed\n")
        os.replace(journal / "complete.pending", journal / "complete")
        _sync_directory(journal)
    except BaseException as failure:
        if ready:
            try:
                recover(journal)
            except BaseException as recovery_error:
                raise RecoveryRequired(
                    f"Recovery required: towel recover {journal}"
                ) from recovery_error
        else:
            try:
                _cleanup(journal)
            except RecoveryRequired as cleanup_error:
                raise RecoveryRequired(f"{cleanup_error} (after: {failure})") from failure
        raise
    try:
        _cleanup(journal)
    except OSError as error:
        LOG.warning("Changes committed; journal cleanup requires attention: %s: %s", journal, error)


def _journal_directory(journal: Path) -> Path:
    """``journal`` spelled canonically, or a ChangeConflict saying why it names no journal.

    A journal restores the files beside it, whose paths must be canonical, so
    the directory it sits in is resolved: a path through a symbolic link, such
    as macOS's ``/tmp``, names the same journal. The journal itself must be
    the directory a run wrote, not a link to one, which would restore files
    beside the link rather than beside the journal.
    """
    absolute = journal.absolute()
    if not absolute.name.startswith(JOURNAL_PREFIX):
        raise ChangeConflict(
            f"Invalid transaction directory: {journal}: a journal's name begins with"
            f" {JOURNAL_PREFIX}"
        )
    canonical = absolute.parent.resolve() / absolute.name
    try:
        info = canonical.lstat()
    except FileNotFoundError:
        raise ChangeConflict(f"Invalid transaction directory: {journal} does not exist") from None
    if stat.S_ISLNK(info.st_mode):
        raise ChangeConflict(
            f"Invalid transaction directory: {journal} is a symbolic link, not a journal a run"
            " wrote, and a journal restores the files beside it; recover the journal it points"
            " to by that journal's own path, or remove the link"
        )
    if not stat.S_ISDIR(info.st_mode):
        raise ChangeConflict(
            f"Invalid transaction directory: {journal} is not a directory, so not a journal a"
            " run wrote; rename or remove it"
        )
    return canonical


def _distrust(journal: Path) -> Optional[str]:
    """Why ``recover`` will not trust the journal directory ``journal``, and what would; None if it will.

    Every journal a run writes is a directory only its owner can enter, so
    one that another user owns, or whose mode differs, is not read.
    """
    info = journal.stat()
    if info.st_uid != os.getuid():
        return (
            f"{journal} belongs to another user (uid {info.st_uid}); only its owner can recover"
            f" it, with towel recover {journal}"
        )
    mode = stat.S_IMODE(info.st_mode)
    if mode != 0o700:
        return (
            f"{journal} has mode {mode:04o}, and every journal Towel writes has mode 0700; if"
            f" Towel wrote it, restore that with chmod 700 {journal} and then run towel recover"
            f" {journal}, and if it did not, rename or remove it"
        )
    return None


def _restoration(journal: Path) -> Optional[List[Tuple[Path, bytes, int, str]]]:
    """What recovering the journal directory ``journal`` restores, every file checked first.

    Each is a target, the bytes and mode to restore it to, and the digest of
    what the change wrote there. None when there is nothing to restore: the
    change committed, or failed before its manifest was durable. A
    ``ChangeConflict`` says why recovery cannot proceed; nothing is written.
    """
    if (journal / "complete").is_file():
        return None
    manifest = journal / "manifest.json"
    if not manifest.exists():
        # A durable manifest always precedes the first target replacement.
        return None
    _safe_target(manifest)
    try:
        records: object = json.loads(manifest.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as error:
        raise ChangeConflict(f"Invalid transaction manifest: {error}") from error
    if not isinstance(records, list):
        raise ChangeConflict("Invalid transaction manifest")
    originals: List[Tuple[Path, bytes, int, str]] = []
    seen: Set[Path] = set()
    for index, record in enumerate(records):
        entry = _manifest_record(record)
        relative, mode = entry["path"], entry["mode"]
        before, after = entry["before"], entry["after"]
        path = journal.parent / relative
        if Path(relative).is_absolute() or ".." in Path(relative).parts or path in seen:
            raise ChangeConflict("Transaction path escapes its root or is duplicated")
        seen.add(path)
        backup = journal / str(index)
        _safe_target(backup)
        content = backup.read_bytes()
        if _digest(content) != before:
            raise ChangeConflict(f"Damaged transaction backup: {backup}")
        _safe_target(path)
        current = path.read_bytes()
        if _digest(current) not in (before, after) or stat.S_IMODE(path.stat().st_mode) != mode:
            raise ExternalEdit(f"External edit preserved; resolve before recovery: {path}")
        originals.append((path, content, mode, after))
    return originals


def recover(journal: Path) -> None:
    """Restore originals from a trusted local journal, refusing conflicting edits.

    Validate the entire journal and all targets before restoring its first file.
    Recovery is itself restartable after interruption.
    """
    journal = _journal_directory(journal)
    distrust = _distrust(journal)
    if distrust is not None:
        raise ChangeConflict(
            f"Recovery requires an owner-only journal owned by the current user: {distrust}"
        )
    try:
        originals = _restoration(journal)
    except ChangeConflict as error:
        raise type(error)(f"{error}. {_instead_of_recovery(journal, error)}") from error
    if originals is None:
        _cleanup(journal)
        return
    for path, content, mode, after in reversed(originals):
        _safe_target(path)
        current = path.read_bytes()
        if current == content:
            continue
        if _digest(current) != after:
            raise ChangeConflict(f"External edit during recovery: {path}")
        _replace(path, content, mode)
    _write_new(journal / "complete", b"rolled back\n")
    _sync_directory(journal)
    _cleanup(journal)
