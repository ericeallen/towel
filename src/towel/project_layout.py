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

"""Where a project's configuration lives: its root, its ``pyproject.toml``, its package chain.

The project root is where staging copies from, where formatters, checkers
and the import model read their configuration, and what the helper-name
scan reads whole. Module names are not decided here: they are the names the
program's own imports give its modules (``towel.import_model``;
docs/DECISIONS.md, "Import names come from the program").
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from .diagnostics import LOG

import tomllib


def load_pyproject(project_root: Path) -> Dict[str, Any]:
    """Best-effort load of pyproject.toml using the available TOML parser.

    Returns an empty dict if parsing fails or file does not exist.
    """
    pyproject_path = project_root / "pyproject.toml"
    if not pyproject_path.exists():
        return {}

    try:
        with pyproject_path.open("rb") as f:
            return tomllib.load(f)
    except OSError:
        return {}
    except ValueError as error:
        LOG.warning(
            "%s could not be parsed; none of its settings are read: %s",
            pyproject_path,
            error,
        )
        return {}


def is_package_dir(path: Path) -> bool:
    """Whether ``path`` is a regular package: a directory holding ``__init__.py``.

    Namespace packages (PEP 420) have no marker file; whether a bare
    directory is imported as one is something only the program's imports
    say (``towel.import_model``), not the directory.
    """
    return path.is_dir() and (path / "__init__.py").exists()


def package_chain(path: Path) -> List[Path]:
    """The regular packages enclosing ``path``, innermost first.

    Ascends from the containing directory while each directory holds an
    ``__init__.py``; the chain ends at the first directory that does not,
    so every entry is importable through the ones after it.
    """
    chain: List[Path] = []
    directory = path.parent
    while is_package_dir(directory):
        chain.append(directory)
        if directory.parent == directory:
            break
        directory = directory.parent
    return chain


def find_project_root(start_path: Path) -> Path:
    """Find nearest ancestor that looks like the project root."""
    start = start_path.resolve()
    base_dir = start.parent if start.is_file() else start

    # Prefer a nearby directory containing packaging markers, but fall back to
    # the provided directory when none are found while walking upward.
    markers = {"pyproject.toml", "setup.cfg", "setup.py"}
    current = base_dir
    while True:
        if any((current / m).exists() for m in markers):
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    # VCS roots do not establish sys.path. Honor classic package ancestry;
    # otherwise the explicit source directory is the only available anchor.
    while (base_dir / "__init__.py").is_file():
        base_dir = base_dir.parent
    return base_dir
