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

"""Where a project's configuration lives: its root, its pyproject.toml, its package chain."""

from __future__ import annotations

import logging
from pathlib import Path
import subprocess
import sys

import pytest

from towel.changes import apply_changes
from towel.project_layout import find_project_root, is_package_dir, load_pyproject, package_chain
from towel.unification.refactor_engine import UnificationRefactorEngine


def test_the_nearest_packaging_marker_is_the_root(tmp_path: Path) -> None:
    for marker in ("pyproject.toml", "setup.cfg", "setup.py"):
        project = tmp_path / marker.replace(".", "_")
        module = project / "src" / "pkg" / "mod.py"
        module.parent.mkdir(parents=True)
        module.write_text("x = 1\n")
        (project / marker).write_text("")
        assert find_project_root(module) == project.resolve()
        assert find_project_root(module.parent) == project.resolve()


def test_without_a_marker_a_package_is_rooted_above_its_top(tmp_path: Path) -> None:
    """A VCS root does not establish ``sys.path``; classic package ancestry does."""
    (tmp_path / ".git").mkdir()
    package = tmp_path / "work" / "pkg" / "sub"
    package.mkdir(parents=True)
    for directory in (package, package.parent):
        (directory / "__init__.py").write_text("")
    (package / "m.py").write_text("x = 1\n")
    assert find_project_root(package / "m.py") == (tmp_path / "work").resolve()
    plain = tmp_path / "scripts"
    plain.mkdir()
    assert find_project_root(plain) == plain.resolve()


def test_a_malformed_pyproject_is_read_as_empty_and_said_so(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    assert load_pyproject(tmp_path) == {}
    (tmp_path / "pyproject.toml").write_text("this = not = valid [[\n")
    with caplog.at_level(logging.WARNING, logger="towel"):
        assert load_pyproject(tmp_path) == {}
    assert "could not be parsed" in caplog.text
    (tmp_path / "pyproject.toml").write_text("[tool.black]\nline-length = 79\n")
    assert load_pyproject(tmp_path) == {"tool": {"black": {"line-length": 79}}}


def test_a_package_is_a_directory_holding_an_initializer(tmp_path: Path) -> None:
    file_path = tmp_path / "not_a_dir.py"
    file_path.write_text("pass\n")
    assert not is_package_dir(file_path)
    package = tmp_path / "pkg"
    package.mkdir()
    assert not is_package_dir(package)
    (package / "__init__.py").write_text("")
    assert is_package_dir(package)
    inner = package / "inner"
    inner.mkdir()
    (inner / "__init__.py").write_text("")
    assert package_chain(inner / "m.py") == [inner, package]
    assert package_chain(tmp_path / "m.py") == []


@pytest.mark.parametrize("packaged", [False, True])
def test_vcs_root_does_not_control_crossfile_imports(tmp_path: Path, packaged: bool) -> None:
    (tmp_path / ".git").mkdir()
    target = tmp_path / "consumer"
    target.mkdir()
    if packaged:
        (target / "__init__.py").write_text("")
    body = "    y=x+1\n    z=y*2\n    return z\n"
    a, b = target / "a.py", target / "b.py"
    a.write_text("def first(x):\n" + body)
    # Two top-level modules share nothing that ships them together unless
    # one already imports the other.
    b.write_text(("" if packaged else "import a\n") + "def second(x):\n" + body)
    imports = (
        "from consumer.a import first; from consumer.b import second"
        if packaged
        else "from a import first; from b import second"
    )
    command = [sys.executable, "-c", imports + "; print(first(2),second(2))"]
    cwd = tmp_path if packaged else target
    before = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=True)
    engine = UnificationRefactorEngine(min_lines=2, cross_module_helpers=True)
    proposal = engine.analyze_files([str(a), str(b)], progress="none")[0]
    apply_changes(engine.plan_refactoring(proposal))
    after = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=True)
    assert before.stdout == after.stdout == "6 6\n"
