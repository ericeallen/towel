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

"""The settle marker goes where the language server analyzes it, however the root is spelled.

With ``include = ["src"]`` and a target outside it (``tests``), the marker went to
the included ``src``, read from the configuration with every link resolved. The
copy is made under macOS's ``/var/...``, which is ``/private/var/...`` resolved,
so the marker was a change outside the server's workspace: the run waited out
``UNSEEN_MARKER_TIMEOUT_SECONDS`` (61 s against 4 s; rich-click's ``tests``
took 71 s and 153 s) and then gave the server up for the command line.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

from towel import pyright_session
from towel.pyright_session import (
    FileChange,
    PyrightSession,
    _included_directories,
    pyright_scope,
)
from towel.type_inference import _pyright_langserver_command

requires_pyright = pytest.mark.skipif(
    importlib.util.find_spec("pyright") is None, reason="pyright absent"
)


def _r9py_project(real: Path, include: str) -> Path:
    """A ``src`` layout whose pyright includes ``include``, reached through a link to ``real``."""
    project = real / "project"
    files = {
        "pyproject.toml": f"[project]\nname = 'calc'\nversion = '0'\n\n[tool.pyright]\n{include}\n",
        "src/calc/__init__.py": "",
        "src/calc/core.py": "def total(prices: list[float]) -> float:\n    return sum(prices)\n",
        "tests/test_core.py": "from calc.core import total\n\nRESULT = total([1.0])\n",
    }
    for name, text in files.items():
        (project / name).parent.mkdir(parents=True, exist_ok=True)
        (project / name).write_text(text, encoding="utf-8")
    link = real.parent / f"{real.name}_link"
    link.symlink_to(real, target_is_directory=True)
    return link / "project"


def test_r9py_an_included_directory_is_spelled_as_the_root_is(tmp_path: Path) -> None:
    root = _r9py_project(tmp_path / "r9py_real", 'include = ["src"]')
    assert _included_directories(root) == [root / "src"]


def test_r9py_a_wildcard_include_still_leaves_the_marker_somewhere_analyzed(
    tmp_path: Path,
) -> None:
    root = _r9py_project(tmp_path / "r9py_real", 'include = ["src/*"]')
    session = SimpleNamespace(
        _scope=pyright_scope(root),
        _included=_included_directories(root),
        _root=root,
        _marker=SimpleNamespace(directory=None),
    )
    chosen = PyrightSession._marker_directory(
        session, [root / "tests/test_core.py"]  # type: ignore[arg-type]
    )
    assert session._included == [], "the include names no directory outright"
    assert chosen == root / "src", "the first directory walked whose files the include matches"


@requires_pyright
def test_r9py_a_change_outside_include_is_answered_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _r9py_project(tmp_path / "r9py_real", 'include = ["src"]')
    bound = 15.0
    monkeypatch.setattr(pyright_session, "UNSEEN_MARKER_TIMEOUT_SECONDS", bound)
    command = _pyright_langserver_command()
    assert command is not None
    session = PyrightSession(command, root, sys.executable)
    try:
        changed = root / "tests/test_core.py"
        changed.write_text(changed.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        started = time.monotonic()
        session.diagnostics_after({changed: FileChange.CHANGED}, beside=[changed])
        assert time.monotonic() - started < bound / 3
        assert session._marker.directory == root / "src"
    finally:
        session.close()
