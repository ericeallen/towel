"""Fault injection tests assert target bytes, modes and recovery state."""

import ast
import os
from pathlib import Path
import subprocess
import sys
from typing import Any
from unittest.mock import patch

import pytest

from towel.changes import ChangeConflict, ChangePlan, RecoveryRequired, apply_changes, recover
from towel.cli import _apply_rename_mappings
from towel.unification.refactor_engine import UnificationRefactorEngine


def make_plan(tmp_path):
    files = [tmp_path / "a.py", tmp_path / "b.py"]
    for path in files:
        path.write_bytes(b"value = 1\n")
        path.chmod(0o640)
    return files, ChangePlan.from_sources(
        {str(p): p.read_bytes() for p in files}, {str(p): "value = 2\n" for p in files}
    )


def test_success_preserves_modes_and_removes_journal(tmp_path):
    files, plan = make_plan(tmp_path)
    apply_changes(plan)
    assert all(
        p.read_bytes() == b"value = 2\n" and p.stat().st_mode & 0o777 == 0o640 for p in files
    )
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.py", "b.py"]


@pytest.mark.parametrize(
    "interruption", [OSError("disk failure"), KeyboardInterrupt(), SystemExit(2)]
)
def test_mid_batch_failure_restores_every_file(tmp_path, interruption):
    files, plan = make_plan(tmp_path)
    original = os.replace
    count = 0

    def fail_second_target(source, target):
        nonlocal count
        if Path(target) in files:
            count += 1
            if count == 2:
                raise interruption
        return original(source, target)

    with patch("towel.changes.os.replace", side_effect=fail_second_target):
        with pytest.raises(type(interruption)):
            apply_changes(plan)
    assert all(p.read_bytes() == b"value = 1\n" for p in files)
    assert not list(tmp_path.glob(".towel-*"))


def test_stale_plan_writes_nothing(tmp_path):
    files, plan = make_plan(tmp_path)
    files[1].write_text("external = 3\n")
    with pytest.raises(ChangeConflict):
        apply_changes(plan)
    assert files[0].read_bytes() == b"value = 1\n"
    assert files[1].read_text() == "external = 3\n"


def test_rollback_failure_retains_restartable_recovery(tmp_path):
    files, plan = make_plan(tmp_path)
    original = os.replace
    count = 0

    def fail_after_first(source, target):
        nonlocal count
        if Path(target) in files:
            count += 1
            if count > 1:
                raise OSError("unavailable filesystem")
        return original(source, target)

    with patch("towel.changes.os.replace", side_effect=fail_after_first):
        with pytest.raises(RecoveryRequired):
            apply_changes(plan)
    journal = next(tmp_path.glob(".towel-transaction-*"))
    with pytest.raises(RecoveryRequired):
        apply_changes(
            ChangePlan.from_sources(
                {str(p): p.read_bytes() for p in files}, {str(p): "value = 4\n" for p in files}
            )
        )
    recover(journal)
    assert all(p.read_bytes() == b"value = 1\n" for p in files)
    assert not journal.exists()


def test_killed_process_can_be_recovered_by_cli(tmp_path):
    files, _ = make_plan(tmp_path)
    script = """
import os,sys
from pathlib import Path
from towel.changes import ChangePlan,apply_changes
files=[Path(sys.argv[1])/'a.py',Path(sys.argv[1])/'b.py']
plan=ChangePlan.from_sources({str(p):p.read_bytes() for p in files},{str(p):'value = 2\\n' for p in files})
original=os.replace
def kill_after_first(source,target):
    original(source,target)
    if Path(target)==files[0]:
        os._exit(91)
os.replace=kill_after_first
apply_changes(plan)
"""
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], check=False)
    assert result.returncode == 91
    assert files[0].read_bytes() == b"value = 2\n"
    journal = next(tmp_path.glob(".towel-transaction-*"))
    recovery = subprocess.run(
        [sys.executable, "-c", "from towel.cli import main; main()", "recover", str(journal)],
        capture_output=True,
        text=True,
    )
    assert recovery.returncode == 0, recovery.stderr
    assert all(p.read_bytes() == b"value = 1\n" for p in files)


def test_external_edit_after_partial_failure_is_preserved(tmp_path):
    files, plan = make_plan(tmp_path)
    original = os.replace

    def edit_then_fail(source, target):
        if Path(target) == files[1]:
            files[0].write_text("external = 9\n")
            raise OSError("failed")
        original(source, target)

    with patch("towel.changes.os.replace", side_effect=edit_then_fail):
        with pytest.raises(RecoveryRequired):
            apply_changes(plan)
    journal = next(tmp_path.glob(".towel-transaction-*"))
    with pytest.raises(ChangeConflict):
        recover(journal)
    assert files[0].read_text() == "external = 9\n"
    assert files[1].read_bytes() == b"value = 1\n"


def test_invalid_later_rename_mapping_cannot_partially_apply(tmp_path):
    path = tmp_path / "source.py"
    source = "def __extracted_func_1():\n    return 1\n\ndef __extracted_func_2():\n    return 2\n"
    path.write_text(source)
    with pytest.raises(ValueError):
        _apply_rename_mappings(
            tmp_path, {"__extracted_func_1": "first", "__extracted_func_2": "class"}, False
        )
    assert path.read_text() == source


def test_planning_is_repeatable_does_not_mutate_and_rejects_stale_source(tmp_path):
    path = tmp_path / "source.py"
    path.write_text(
        "def a(x):\n    y = x + 1\n    z = y * 2\n    return z\n\ndef b(x):\n    y = x + 1\n    z = y * 2\n    return z\n"
    )
    engine = UnificationRefactorEngine(min_lines=3)
    proposal = engine.analyze_file(str(path))[0]
    original = ast.dump(proposal.extracted_function, include_attributes=True)
    plan = engine.plan_refactoring(proposal)
    assert engine.plan_refactoring(proposal) == plan
    assert ast.dump(proposal.extracted_function, include_attributes=True) == original
    path.write_text(path.read_text() + "\nexternal = 1\n")
    with pytest.raises(ChangeConflict):
        engine.plan_refactoring(proposal)


def test_hardlink_and_symlink_are_rejected(tmp_path):
    files, _ = make_plan(tmp_path)
    link = tmp_path / "alias.py"
    link.symlink_to(files[0])
    with pytest.raises(ChangeConflict):
        ChangePlan.from_sources({str(link): link.read_bytes()}, {str(link): "value = 2\n"})
    link.unlink()
    os.link(files[0], link)
    with pytest.raises(ChangeConflict):
        ChangePlan.from_sources(
            {str(files[0]): files[0].read_bytes()}, {str(files[0]): "value = 2\n"}
        )


def test_pending_child_transaction_blocks_parent_batch(tmp_path):
    child = tmp_path / "child"
    child.mkdir()
    files, plan = make_plan(child)
    original = os.replace
    target_writes = 0

    def fail_later(source, target):
        nonlocal target_writes
        if Path(target) in files:
            target_writes += 1
            if target_writes > 1:
                raise OSError("offline")
        return original(source, target)

    with patch("towel.changes.os.replace", side_effect=fail_later):
        with pytest.raises(RecoveryRequired):
            apply_changes(plan)
    other = tmp_path / "other.py"
    other.write_text("value = 1\n")
    paths = [files[0], other]
    larger = ChangePlan.from_sources(
        {str(p): p.read_bytes() for p in paths}, {str(p): "value = 3\n" for p in paths}
    )
    with pytest.raises(RecoveryRequired):
        apply_changes(larger)
    assert other.read_text() == "value = 1\n"
    recover(next(child.glob(".towel-transaction-*")))


@pytest.mark.parametrize("failure_call", range(1, 15))
def test_sync_failures_preserve_recoverable_state(tmp_path, failure_call):
    files, plan = make_plan(tmp_path)
    original = os.fsync
    calls = 0

    def fail_once(descriptor):
        nonlocal calls
        calls += 1
        if calls == failure_call:
            raise OSError("sync fault")
        return original(descriptor)

    try:
        with patch("towel.changes.os.fsync", side_effect=fail_once):
            apply_changes(plan)
    except OSError:
        for journal in tmp_path.glob(".towel-transaction-*"):
            recover(journal)
    contents = {p.read_bytes() for p in files}
    assert contents in ({b"value = 1\n"}, {b"value = 2\n"})
    assert all(p.stat().st_mode & 0o777 == 0o640 for p in files)


def test_crlf_fixed_point_uses_original_bytes_for_stale_check(tmp_path):
    path = tmp_path / "windows.py"
    path.write_bytes(
        b"def a(x):\r\n    y=x+1\r\n    z=y*2\r\n    return z\r\n\r\ndef b(x):\r\n    y=x+1\r\n    z=y*2\r\n    return z\r\n"
    )
    engine = UnificationRefactorEngine(min_lines=3)
    result, count, _ = engine.refactor_to_fixed_point(str(path), max_iterations=1)
    assert count == 1
    # The file keeps its line endings; the returned text is LF like every source in memory.
    assert b"\n" not in path.read_bytes().replace(b"\r\n", b"")
    assert path.read_bytes() == result.encode("utf-8").replace(b"\n", b"\r\n")
    scope: dict[str, Any] = {}
    exec(result, scope)
    assert scope["a"](3) == scope["b"](3) == 8
