"""Publish a completed disposable copy before beginning refactoring."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile


def _refuse_case_collisions(source: Path, destination_parent: Path) -> None:
    """Refuse to copy two paths that differ only by case onto a case-insensitive volume.

    ``copytree`` would write the second over the first without a word.
    """
    seen: dict[str, Path] = {}
    collisions = []
    for path in source.rglob("*"):
        key = str(path.relative_to(source)).casefold()
        if key in seen and seen[key] != path:
            collisions.append((seen[key], path))
        seen.setdefault(key, path)
    if not collisions:
        return
    with tempfile.NamedTemporaryFile(prefix=".towel-case-", dir=destination_parent) as probe:
        swapped = Path(probe.name).with_name(Path(probe.name).name.swapcase())
        insensitive = swapped.exists()
    if insensitive:
        first, second = collisions[0]
        raise ValueError(
            f"{first.relative_to(source)} and {second.relative_to(source)} differ only by case"
            " and the output volume does not distinguish them"
        )


def copy_project(source: Path, destination: Path, *, allow_empty: bool = False) -> None:
    """Copy into a private sibling, so copy failure never leaves a partial output.

    Callers must have exclusive write access to the destination and its parent.
    Existing empty directories are allowed only for the directory API.
    """
    source = source.resolve()
    destination = destination.absolute()
    if destination.is_symlink():
        raise ValueError("Output must not be a symlink")
    resolved = destination.resolve()
    if source == resolved or source in resolved.parents or resolved in source.parents:
        raise ValueError("Copy input and output must be distinct and non-overlapping")
    if destination.exists() and not (
        allow_empty and destination.is_dir() and not any(destination.iterdir())
    ):
        raise ValueError("Output already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        _refuse_case_collisions(source, destination.parent)
    with tempfile.TemporaryDirectory(prefix=".towel-copy-", dir=destination.parent) as temporary:
        staged = Path(temporary) / "payload"
        if source.is_dir():
            shutil.copytree(source, staged, symlinks=True)
        else:
            shutil.copy2(source, staged)
        # Check again after the potentially long copy. Exclusive access is still
        # required for the final check/rename interval.
        if destination.exists() and not (
            allow_empty and destination.is_dir() and not any(destination.iterdir())
        ):
            raise ValueError("Output appeared or changed during copying")
        os.replace(staged, destination)
