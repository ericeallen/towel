# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""A newly imported top-level helper host must not introduce ordinary failures."""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
import subprocess
import sys

import pytest

from towel.unification.refactor_engine import UnificationRefactorEngine


def _write_project(root: Path, prefix: str, *, independent_import: bool) -> None:
    (root / "pyproject.toml").write_text('[project]\nname="fresh-host"\nversion="0"\n')
    body = """def {name}(seed):
    total = seed + 1
    result = total * 2
    added = result + 3
    return added * {factor}
"""
    (root / "a.py").write_text(prefix + body.format(name="first", factor=5))
    (root / "b.py").write_text(
        "def unused():\n    import a\n\n" + body.format(name="second", factor=7)
    )
    if independent_import:
        # This import is unconditional in its own module, but the driver's
        # execution never imports that module or calls b.unused.
        (root / "independent.py").write_text("import a\n")
    (root / "run.py").write_text("import b\nprint(b.second(3))\n")


def _run(root: Path) -> str:
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            f"import sys; sys.path.insert(0, {str(root)!r}); import run",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _refactor(root: Path) -> int:
    engine = UnificationRefactorEngine(min_lines=3, cross_module_helpers=True)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        applied, _ = engine.refactor_directory_to_fixed_point(str(root), str(root), progress="none")
    return sum(count for count, _ in applied.values())


@pytest.mark.parametrize("prefix", ["value = MISSING\n", "value = 1 / 0\n"])
def test_new_host_cannot_introduce_an_ordinary_import_failure(tmp_path: Path, prefix: str) -> None:
    _write_project(tmp_path, prefix, independent_import=True)
    assert _run(tmp_path) == "77\n"
    changed = _refactor(tmp_path)
    assert _run(tmp_path) == "77\n"
    assert changed == 0


@pytest.mark.parametrize("independent_import", [False, True])
def test_literal_top_level_host_requires_unconditional_import_evidence(
    tmp_path: Path, independent_import: bool
) -> None:
    _write_project(tmp_path, "value = 7\n", independent_import=independent_import)
    assert _run(tmp_path) == "77\n"
    changed = _refactor(tmp_path)
    assert _run(tmp_path) == "77\n"
    assert changed == (2 if independent_import else 0)
