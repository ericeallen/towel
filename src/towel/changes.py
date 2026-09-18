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
from pathlib import Path
import stat
import tempfile
from typing import Mapping

from .diagnostics import LOG


class ChangeConflict(ValueError):
    """A target changed after planning, or cannot safely be replaced."""


class RecoveryRequired(OSError):
    """Rollback could not finish; the journal must be retained for recovery."""


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
            updated = source.encode("utf-8")
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
        raise ChangeConflict(f"Target changed since planning: {change.path}")


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _cleanup(journal: Path) -> None:
    # Only files created by this journal format, never an arbitrary tree.
    entries = sorted(journal.iterdir(), key=lambda item: item.name == "complete")
    if any(
        path.name not in ("manifest.json", "manifest.pending", "complete", "complete.pending")
        and not path.name.removesuffix(".after").isdecimal()
        for path in entries
    ):
        raise RecoveryRequired(f"Unexpected journal entry; retained {journal}")
    for path in entries:
        path.unlink()
    journal.rmdir()
    _sync_directory(journal.parent)


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
    parents = {parent for change in plan.changes for parent in change.path.parents}
    pending = sorted(path for parent in parents for path in parent.glob(".towel-transaction-*"))
    if pending:
        raise RecoveryRequired(f"Recover the existing transaction first: {pending[0]}")
    if any(change.path.stat().st_dev != root.stat().st_dev for change in plan.changes):
        raise ChangeConflict("A transaction cannot span filesystems")
    journal = root / ".towel-transaction-active"
    journal.mkdir(mode=0o700)
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
    except BaseException:
        if ready:
            try:
                recover(journal)
            except BaseException as recovery_error:
                raise RecoveryRequired(
                    f"Recovery required: towel recover {journal}"
                ) from recovery_error
        else:
            _cleanup(journal)
        raise
    try:
        _cleanup(journal)
    except OSError as error:
        LOG.warning("Changes committed; journal cleanup requires attention: %s: %s", journal, error)


def recover(journal: Path) -> None:
    """Restore originals from a trusted local journal, refusing conflicting edits.

    Validate the entire journal and all targets before restoring its first file.
    Recovery is itself restartable after interruption.
    """
    journal = journal.absolute()
    if journal.resolve() != journal or not journal.name.startswith(".towel-transaction-"):
        raise ChangeConflict(f"Invalid transaction directory: {journal}")
    info = journal.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ChangeConflict("Recovery requires an owner-only journal owned by the current user")
    if (journal / "complete").is_file():
        _cleanup(journal)
        return
    manifest = journal / "manifest.json"
    if not manifest.exists():
        # A durable manifest always precedes the first target replacement.
        _cleanup(journal)
        return
    _safe_target(manifest)
    records: object = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ChangeConflict("Invalid transaction manifest")
    originals: list[tuple[Path, bytes, int, str]] = []
    seen: set[Path] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ChangeConflict("Invalid transaction record")
        relative, mode = record.get("path"), record.get("mode")
        before, after = record.get("before"), record.get("after")
        if not isinstance(relative, str) or not isinstance(mode, int) or not 0 <= mode <= 0o7777:
            raise ChangeConflict("Invalid transaction path/mode")
        if not isinstance(before, str) or not isinstance(after, str):
            raise ChangeConflict("Invalid transaction digests")
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
            raise ChangeConflict(f"External edit preserved; resolve before recovery: {path}")
        originals.append((path, content, mode, after))
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
