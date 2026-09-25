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

"""What Towel's private pyright copy holds, and where it refuses to be made.

A pyright-configured typed run was refused whenever a ``.json``, ``.toml``,
``.ini`` or ``.cfg`` anywhere in the tree was not UTF-8 (a Latin-1 test
fixture), and whenever a directory was linked from outside the project,
though neither is anything pyright reads as the project's. Only pyright's own
configuration files still refuse when they are not UTF-8. A link is now a link
in the copy: pyright enumerates a linked directory only when it leads inside
what is included, and imports through it wherever it leads, as in the project.
A link into the project used to be copied under its own name, so an import
through it read the file as it was, not as the candidate made it.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Mapping

import pytest

from towel.checker_project import CheckerSnapshot
from towel.type_inference import CheckFailure, CheckSuccess, PyrightOracle

requires_pyright = pytest.mark.skipif(
    importlib.util.find_spec("pyright") is None, reason="pyright absent"
)

_R9PY_LATIN1 = b'{"name": "caf\xe9"}\n'


def _r9py_project(root: Path, files: Mapping[str, str] = {}) -> Path:
    tree = {
        "pyproject.toml": "[project]\nname = 'pkg'\nversion = '0'\n\n[tool.pyright]\n",
        "pkg/__init__.py": "",
        "pkg/mod.py": "def f() -> int:\n    return 1\n",
        **files,
    }
    for name, text in tree.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    return root.resolve()


def test_r9py_data_that_is_not_utf8_is_copied_as_it_is(tmp_path: Path) -> None:
    project = _r9py_project(tmp_path / "r9py_project")
    for name in ("tests/data/latin1.json", "tests/data/latin1.cfg", "docs/pyproject.toml"):
        (project / name).parent.mkdir(parents=True, exist_ok=True)
        (project / name).write_bytes(
            b"[project]\nname = 'caf\xe9'\n" if name.endswith(".toml") else _R9PY_LATIN1
        )
    snapshot = CheckerSnapshot(project)
    try:
        assert (snapshot.tree / "tests/data/latin1.json").read_bytes() == _R9PY_LATIN1
        assert (snapshot.tree / "tests/data/latin1.cfg").read_bytes() == _R9PY_LATIN1
        assert (snapshot.tree / "docs/pyproject.toml").is_file(), "no [tool.pyright] in it"
    finally:
        snapshot.close()


@pytest.mark.parametrize(
    "name, content",
    [
        ("sub/pyrightconfig.json", _R9PY_LATIN1),
        ("sub/pyproject.toml", b"[tool.pyright]\nvenv = 'caf\xe9'\n"),
    ],
    ids=["pyrightconfig", "pyproject-with-pyright"],
)
def test_r9py_a_pyright_configuration_that_is_not_utf8_is_still_refused(
    tmp_path: Path, name: str, content: bytes
) -> None:
    project = _r9py_project(tmp_path / "r9py_project")
    (project / name).parent.mkdir(parents=True)
    (project / name).write_bytes(content)
    with pytest.raises(ValueError, match="not UTF-8: .*" + name.replace("/", ".")):
        CheckerSnapshot(project)


def test_r9py_an_environment_is_left_out_of_the_copy(tmp_path: Path) -> None:
    project = _r9py_project(
        tmp_path / "r9py_project",
        {"env/pyvenv.cfg": "home = /usr/bin\n", "env/lib/site.py": "X = 1\n"},
    )
    snapshot = CheckerSnapshot(project)
    try:
        assert not (snapshot.tree / "env").exists()
        assert (snapshot.tree / "pkg/mod.py").is_file()
    finally:
        snapshot.close()


def test_r9py_links_are_links_in_the_copy(tmp_path: Path) -> None:
    project = _r9py_project(
        tmp_path / "r9py_project",
        {".venv/pyvenv.cfg": "home = /usr/bin\n", ".venv/lib/lib.py": "L = 1\n"},
    )
    outside = tmp_path / "r9py_assets"
    (outside / "shared").mkdir(parents=True)
    (outside / "shared/helpers.py").write_text("H = 1\n", encoding="utf-8")
    (project / "docs_assets").symlink_to(outside, target_is_directory=True)
    (project / "alias").symlink_to(project / "pkg", target_is_directory=True)
    (project / "installed").symlink_to(project / ".venv/lib", target_is_directory=True)
    snapshot = CheckerSnapshot(project)
    try:
        copied = snapshot.tree
        assert os.readlink(copied / "docs_assets") == str(outside.resolve())
        assert os.readlink(copied / "installed") == str(project / ".venv/lib"), "left out"
        assert os.readlink(copied / "alias") == "pkg", "to the counterpart, inside the copy"
        snapshot.apply({str(project / "pkg/mod.py"): "def f() -> str:\n    return ''\n"})
        assert "-> str" in (copied / "alias/mod.py").read_text(encoding="utf-8")
        changed = str(outside.resolve() / "shared/helpers.py")
        assert snapshot.unshown([changed, str(project / "pkg/mod.py")]) == [
            (changed, str(project / "docs_assets"))
        ]
    finally:
        snapshot.close()
    assert (outside / "shared/helpers.py").is_file(), "removing the copy leaves the target"


def test_r9py_a_link_back_to_its_own_directory_is_refused(tmp_path: Path) -> None:
    project = _r9py_project(tmp_path / "r9py_project")
    (project / "pkg/loop").symlink_to(project, target_is_directory=True)
    with pytest.raises(ValueError, match="cyclic"):
        CheckerSnapshot(project)


@requires_pyright
def test_r9py_an_import_through_a_link_sees_the_candidate(tmp_path: Path) -> None:
    project = _r9py_project(
        tmp_path / "r9py_project",
        {"app/use.py": "from alias.mod import f\n\nTOTAL: int = f() + 1\n"},
    )
    (project / "alias").symlink_to(project / "pkg", target_is_directory=True)
    outside = tmp_path / "r9py_vendor"
    outside.mkdir()
    (outside / "v.py").write_text("V = 1\n", encoding="utf-8")
    (project / "vendor").symlink_to(outside, target_is_directory=True)
    oracle = PyrightOracle(language_server=False)
    try:
        module = str(project / "pkg/mod.py")
        before = oracle.check_project({module: "def f() -> int:\n    return 1\n"})
        after = oracle.check_project({module: "def f() -> str:\n    return ''\n"})
        # As a run over both would ask: the project's own check reads v.py through the link.
        through = oracle.check_project(
            {module: "def f() -> int:\n    return 1\n", str(outside / "v.py"): "V = 2\n"}
        )
    finally:
        oracle.close()
    assert before == CheckSuccess(), before
    assert isinstance(after, CheckSuccess)
    assert any(error.path.endswith("use.py") for error in after.errors), after
    assert isinstance(through, CheckFailure) and "through the link" in through.reason
