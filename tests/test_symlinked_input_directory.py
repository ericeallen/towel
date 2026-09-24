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

"""Directory mode given a symlink to a package: the link is followed, links inside it are not.

``copy_project`` resolves the input before copying, so the output is a real
directory tree even when the input path is a symlink, while ``copytree`` keeps
the symlinks *inside* the tree as symlinks. Discovery skips symlinked modules
(``_find_python_files``), and ``changes.py`` refuses to write through a
symlink, so a module linked into the package is neither analyzed nor rewritten:
its target keeps its bytes and the output keeps the link. These tests pin that
contract for both the command line and the directory runner.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path
from typing import NamedTuple
from unittest.mock import patch

import pytest

from towel import cli
from towel.unification.refactor_engine import UnificationRefactorEngine

BODY = "    total = 0\n    for item in items:\n        total += item * {factor}\n    return total\n"
MODULE = "def a1(items):\n" + BODY.format(factor=2) + "\n\ndef a2(items):\n" + BODY.format(factor=3)
LINKED = "def b1(items):\n" + BODY.format(factor=2)


class Layout(NamedTuple):
    link: Path
    target: Path
    linked_module: Path


def _package_behind_a_symlink(tmp_path: Path) -> Layout:
    """``tmp_path/link -> real``; ``real/pkg/b.py -> tmp_path/outside.py``."""
    real = tmp_path / "real"
    package = real / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "a.py").write_text(MODULE)
    outside = tmp_path / "outside.py"
    outside.write_text(LINKED)
    (package / "b.py").symlink_to(outside)
    link = tmp_path / "link"
    link.symlink_to(real)
    return Layout(link, real, outside)


def _assert_output_tree(out: Path, layout: Layout) -> None:
    assert out.is_dir() and not out.is_symlink()
    assert not (out / "pkg").is_symlink()
    refactored = (out / "pkg" / "a.py").read_text()
    assert "def __extracted_func_0(" in refactored
    assert refactored.count("__extracted_func_0(") == 3  # definition and two call sites
    linked = out / "pkg" / "b.py"
    assert linked.is_symlink() and linked.resolve() == layout.linked_module.resolve()
    assert layout.linked_module.read_text() == LINKED
    assert (layout.target / "pkg" / "a.py").read_text() == MODULE


def _dry(arguments: list[str]) -> tuple[int, str]:
    stdout = io.StringIO()
    status = 0
    with (
        patch.object(sys, "argv", ["towel", "dry", *arguments]),
        contextlib.redirect_stdout(stdout),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        try:
            cli.main()
        except SystemExit as exit_result:
            status = int(exit_result.code or 0)
    return status, stdout.getvalue()


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation needs a privilege")
def test_cli_dry_follows_a_symlinked_input_directory_but_not_links_inside_it(
    tmp_path: Path,
) -> None:
    layout = _package_behind_a_symlink(tmp_path)
    out = tmp_path / "out"
    status, stdout = _dry(
        [
            str(layout.link),
            str(out),
            "--no-interactive",
            "--progress",
            "none",
            "--no-types",
            "--no-format",
        ]
    )
    assert status == 0, stdout
    assert "Applied 1 refactoring(s) across 1 file(s)" in stdout
    assert f"{out / 'pkg' / 'a.py'}: 1 refactoring(s)" in stdout
    assert "b.py" not in stdout
    _assert_output_tree(out, layout)


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation needs a privilege")
def test_directory_runner_follows_a_symlinked_input_directory_but_not_links_inside_it(
    tmp_path: Path,
) -> None:
    layout = _package_behind_a_symlink(tmp_path)
    out = tmp_path / "out"
    engine = UnificationRefactorEngine(min_lines=3)
    with contextlib.redirect_stdout(io.StringIO()):
        results, termination = engine.refactor_directory_to_fixed_point(
            str(layout.link), str(out), progress="none"
        )
    assert termination == "fixed_point"
    assert {Path(path).relative_to(out): count for path, (count, _) in results.items()} == {
        Path("pkg/a.py"): 1
    }
    _assert_output_tree(out, layout)
