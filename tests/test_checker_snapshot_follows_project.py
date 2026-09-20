"""The checker's private copy follows the project and reports exactly what moved."""

from __future__ import annotations

from pathlib import Path

from towel.checker_project import CheckerSnapshot, CopyChange


def _project(root: Path) -> Path:
    root.mkdir()
    (root / "a.py").write_text("A = 1\n", encoding="utf-8")
    (root / "b.py").write_text("B = 1\n", encoding="utf-8")
    return root


def _kinds(changes: tuple[CopyChange, ...], snapshot: CheckerSnapshot) -> dict[str, str]:
    return {str(change.path.relative_to(snapshot.tree)): change.kind for change in changes}


def test_an_unchanged_project_moves_nothing_and_keeps_its_revision(tmp_path: Path) -> None:
    snapshot = CheckerSnapshot(_project(tmp_path / "p"))
    try:
        assert snapshot.follow_project() == ()
        assert snapshot.revision == 0
    finally:
        snapshot.close()


def test_edits_additions_and_removals_on_disk_reach_the_copy(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    snapshot = CheckerSnapshot(root)
    try:
        (root / "a.py").write_text("A = 22\n", encoding="utf-8")
        (root / "c.py").write_text("C = 1\n", encoding="utf-8")
        (root / "b.py").unlink()
        changes = snapshot.follow_project()
        assert _kinds(changes, snapshot) == {
            "a.py": "changed",
            "b.py": "deleted",
            "c.py": "created",
        }
        assert (snapshot.tree / "a.py").read_text(encoding="utf-8") == "A = 22\n"
        assert not (snapshot.tree / "b.py").exists()
        assert snapshot.revision == 1
        assert snapshot.follow_project() == ()
    finally:
        snapshot.close()


def test_a_same_size_rewrite_is_still_seen(tmp_path: Path) -> None:
    """Towel replaces files atomically; the inode tells even when size and clock do not."""
    root = _project(tmp_path / "p")
    snapshot = CheckerSnapshot(root)
    try:
        replacement = root / "a.py.new"
        replacement.write_text("A = 2\n", encoding="utf-8")
        replacement.replace(root / "a.py")
        assert _kinds(snapshot.follow_project(), snapshot) == {"a.py": "changed"}
    finally:
        snapshot.close()


def test_the_previous_candidate_gives_way_to_the_project_not_to_old_bytes(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    snapshot = CheckerSnapshot(root)
    try:
        first = snapshot.apply({str(root / "a.py"): "A = 'candidate'\n"})
        assert _kinds(first, snapshot) == {"a.py": "changed"}
        # The candidate is applied in place, in a different spelling than was shown.
        (root / "a.py").write_text("A = 'applied'\n", encoding="utf-8")
        second = snapshot.apply({str(root / "b.py"): "B = 2\n"})
        assert _kinds(second, snapshot) == {"a.py": "changed", "b.py": "changed"}
        assert (snapshot.tree / "a.py").read_text(encoding="utf-8") == "A = 'applied'\n"
    finally:
        snapshot.close()


def test_a_file_a_candidate_invented_leaves_with_it(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    snapshot = CheckerSnapshot(root)
    try:
        invented = snapshot.apply({str(root / "new.py"): "N = 1\n"})
        assert _kinds(invented, snapshot) == {"new.py": "created"}
        gone = snapshot.apply({})
        assert _kinds(gone, snapshot) == {"new.py": "deleted"}
        assert not (snapshot.tree / "new.py").exists()
    finally:
        snapshot.close()


def test_restating_the_project_rewrites_nothing(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    snapshot = CheckerSnapshot(root)
    try:
        assert snapshot.apply({str(root / "a.py"): "A = 1\n", str(root / "b.py"): "B = 1\n"}) == ()
    finally:
        snapshot.close()
