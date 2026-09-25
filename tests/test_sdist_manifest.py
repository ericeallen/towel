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

"""Every tracked file the test suite reads is one MANIFEST.in puts in the sdist.

Round 4's hygiene audit found MANIFEST.in including only ``*.py`` and
``*.md`` under ``tests/``: the 33 ``pyproject.toml`` and ``pytest.ini`` files
of the cross-file battery's fixtures were left out, and three of its tests
failed when run from the sdist. This evaluates MANIFEST.in over the tracked
files as setuptools does, for the directives it uses, and fails on any it
does not know rather than misjudge it.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path, PurePosixPath
import shutil
import subprocess
from typing import Callable, FrozenSet, List

import pytest

REPOSITORY = Path(__file__).resolve().parents[1]

READ_BY_THE_SUITE = (
    "tests",
    "test_examples",
    "test_examples_expected_output",
    "test_examples_crossfile",
    "test_examples_crossfile_expected_output",
)


def _path_matches(pattern: str, path: str) -> bool:
    """A MANIFEST.in path glob as distutils reads it: no wildcard crosses a ``/``."""
    names, parts = PurePosixPath(path).parts, PurePosixPath(pattern).parts
    return len(names) == len(parts) and all(map(fnmatch.fnmatchcase, names, parts))


def _name_matches(pattern: str, path: str) -> bool:
    return fnmatch.fnmatchcase(PurePosixPath(path).name, pattern)


def _under(directory: str, path: str) -> bool:
    return path.startswith(directory.rstrip("/") + "/")


def _matcher(command: str, arguments: List[str]) -> Callable[[str], bool]:
    """Which paths one MANIFEST.in line names."""
    if command in ("include", "exclude"):
        return lambda path: any(_path_matches(pattern, path) for pattern in arguments)
    if command in ("global-include", "global-exclude"):
        return lambda path: any(_name_matches(pattern, path) for pattern in arguments)
    if command in ("recursive-include", "recursive-exclude"):
        directory, patterns = arguments[0], arguments[1:]
        return lambda path: _under(directory, path) and any(
            _name_matches(pattern, path) for pattern in patterns
        )
    if command in ("graft", "prune"):
        (directory,) = arguments
        return lambda path: _under(directory, path)
    raise AssertionError(f"MANIFEST.in directive this test does not model: {command}")


def _shipped(tracked: FrozenSet[str], manifest: str) -> FrozenSet[str]:
    """The tracked files MANIFEST.in puts in the sdist, its lines applied in order."""
    shipped: FrozenSet[str] = frozenset()
    for line in manifest.splitlines():
        words = line.split("#", 1)[0].split()
        if not words:
            continue
        command, arguments = words[0], words[1:]
        named = frozenset(path for path in tracked if _matcher(command, arguments)(path))
        if command in ("include", "global-include", "recursive-include", "graft"):
            shipped |= named
        else:
            shipped -= named
    return shipped


def _tracked() -> FrozenSet[str]:
    if shutil.which("git") is None or not (REPOSITORY / ".git").exists():
        pytest.skip("not a git checkout, so there is no list of tracked files")
    listed = subprocess.run(
        ["git", "-C", str(REPOSITORY), "ls-files", "-z"],
        capture_output=True,
        check=True,
    )
    return frozenset(name for name in listed.stdout.decode("utf-8").split("\0") if name)


def test_r9p2_every_tracked_file_the_suite_reads_is_in_the_sdist() -> None:
    tracked = _tracked()
    shipped = _shipped(tracked, (REPOSITORY / "MANIFEST.in").read_text(encoding="utf-8"))
    read = {path for path in tracked if any(_under(top, path) for top in READ_BY_THE_SUITE)}
    assert read, "the suite reads tracked files"
    assert sorted(read - shipped) == []


def test_r9p2_the_manifest_model_reads_the_directives_as_setuptools_does() -> None:
    tracked = frozenset(
        {
            "README.md",
            "a/b.py",
            "a/c/d.py",
            "a/c/e.toml",
            "a/f.txt",
            "top.py",
            "x/__pycache__/y.pyc",
        }
    )
    manifest = (
        "include README.md *.py\n"
        "recursive-include a *.py *.toml\n"
        "graft x\n"
        "prune a/c\n"
        "include a/c/e.toml\n"
        "global-exclude *.py[cod]  # a comment\n"
    )
    assert _shipped(tracked, manifest) == {"README.md", "top.py", "a/b.py", "a/c/e.toml"}
