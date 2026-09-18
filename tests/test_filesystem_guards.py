"""copy_project refuses every destination it could not fill atomically."""

from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from towel import filesystem
from towel.filesystem import copy_project


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "src"
    source.mkdir()
    (source / "m.py").write_text("x = 1\n")
    return source


def test_a_symlinked_destination_is_refused(tmp_path: Path) -> None:
    source = _source(tmp_path)
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(ValueError, match="symlink"):
        copy_project(source, link)
    assert not any(real.iterdir())


def test_overlapping_paths_are_refused(tmp_path: Path) -> None:
    source = _source(tmp_path)
    (source / "pkg").mkdir()
    for destination in (source, source / "inside", source.parent):
        with pytest.raises(ValueError, match="distinct and non-overlapping"):
            copy_project(source, destination)
    with pytest.raises(ValueError, match="distinct and non-overlapping"):
        copy_project(source / "pkg", source)


def test_an_existing_destination_is_refused_unless_empty_and_allowed(tmp_path: Path) -> None:
    source = _source(tmp_path)
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "keep.txt").write_text("")
    with pytest.raises(ValueError, match="already exists"):
        copy_project(source, occupied, allow_empty=True)
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="already exists"):
        copy_project(source, empty)
    copy_project(source, empty, allow_empty=True)
    assert (empty / "m.py").read_text() == "x = 1\n"


def test_a_destination_appearing_during_the_copy_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "out"
    original = shutil.copytree

    def copytree_then_race(src: Path, dst: Path, **kwargs: object) -> object:
        result = original(src, dst, **kwargs)
        destination.mkdir()
        (destination / "intruder.txt").write_text("")
        return result

    monkeypatch.setattr(filesystem.shutil, "copytree", copytree_then_race)
    with pytest.raises(ValueError, match="appeared or changed"):
        copy_project(source, destination)
    assert sorted(p.name for p in destination.iterdir()) == ["intruder.txt"]
    assert not list(tmp_path.glob(".towel-copy-*"))
