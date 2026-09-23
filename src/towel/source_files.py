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

"""Discover project Python sources with one exclusion policy for every command."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

PROBE_PREFIX = "_towel_probe_"


def is_probe_file(path: Path) -> bool:
    """Whether the filename belongs to an ephemeral type-checker probe."""
    return path.name.startswith(PROBE_PREFIX)


def _raise_walk_error(error: OSError) -> None:
    """An unreadable subtree may contain consumers; never report a partial inventory."""
    raise error


def _is_python_source(path: Path) -> bool:
    """A regular Python file whose name does not belong to a checker probe."""
    return (
        path.name.endswith(".py")
        and not path.is_symlink()
        and path.is_file()
        and not is_probe_file(path)
    )


def python_sources(
    directory: Path, *, recursive: bool = True, excluded: Iterable[str] = ()
) -> list[Path]:
    """Sorted regular sources, excluding symlinks, tool directories and environments.

    Prune directories before entering them: ignored source, especially an
    environment's incompatible fixtures, is outside both extraction and rename
    analysis. The explicitly requested root may itself have an unusual name.
    An unreadable included directory fails discovery instead of hiding sources.
    An explicitly supplied regular Python file is returned on its own.
    A missing explicit root has no sources; errors encountered within an
    existing root still fail the inventory.
    """
    try:
        directory.stat()
    except FileNotFoundError:
        return []
    if directory.is_file():
        return [directory] if _is_python_source(directory) else []
    ignored = {"__pycache__", "venv", "env", "node_modules", *excluded}
    found: list[Path] = []
    for parent, directories, filenames in os.walk(
        directory, followlinks=False, onerror=_raise_walk_error
    ):
        root = Path(parent)
        directories[:] = sorted(
            name
            for name in directories
            if recursive
            and not name.startswith(".")
            and name not in ignored
            and not (root / name).is_symlink()
            and not (root / name / "pyvenv.cfg").is_file()
        )
        for name in filenames:
            path = root / name
            if _is_python_source(path):
                found.append(path)
    return sorted(found)
