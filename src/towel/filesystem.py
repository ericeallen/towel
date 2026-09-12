"""Publish a completed disposable copy before beginning refactoring."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile


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
