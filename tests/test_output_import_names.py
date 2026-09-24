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

"""An absolute import names the project the code belongs to, not the staging directory.

``towel dry src out`` writes a candidate into ``out`` for review. A module
named after that directory states a fact about a scratch path: the checker,
which checks the copy under the original project's names, cannot resolve it,
and the reader's import breaks the moment the output is adopted into the place
it was written for.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap

import pytest

requires_mypy = pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")


def _project(root: Path) -> Path:
    """Two modules of one package, under packaging metadata, sharing a method."""
    (root / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n", encoding="utf-8")
    package = root / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    for name, tag in (("alpha", "a"), ("beta", "b")):
        (package / f"{name}.py").write_text(
            textwrap.dedent(f"""
                from __future__ import annotations


                class {name.capitalize()}:
                    def __init__(self) -> None:
                        self.tag = "{tag}"

                    def render(self, width: int) -> str:
                        body = str(self.tag).strip()
                        padded = body.rjust(width, ".")
                        return padded.upper()
                """).lstrip(),
            encoding="utf-8",
        )
    return package


def _refactor(root: Path, source: Path, destination: Path) -> str:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "towel.cli",
            "dry",
            str(source),
            str(destination),
            "--no-interactive",
            "--cross-module",
            "--progress",
            "none",
            "--min-lines",
            "3",
        ],
        capture_output=True,
        text=True,
        cwd=root,
        timeout=600,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout + result.stderr


@requires_mypy
def test_the_staging_directory_never_appears_in_an_import(tmp_path: Path) -> None:
    package = _project(tmp_path)
    output = tmp_path / "candidate"
    log = _refactor(tmp_path, package, output)
    assert "Dropped a proposal" not in log, log
    written = "\n".join(path.read_text(encoding="utf-8") for path in sorted(output.glob("*.py")))
    assert "candidate." not in written, written
    assert "from pkg.alpha import" in written or "from .alpha import" in written, written


@requires_mypy
def test_the_reviewed_output_still_works_once_adopted(tmp_path: Path) -> None:
    """The point of the staging directory is that its contents replace the original."""
    package = _project(tmp_path)
    output = tmp_path / "candidate"
    _refactor(tmp_path, package, output)
    shutil.rmtree(package)
    output.rename(package)
    ran = subprocess.run(
        [sys.executable, "-c", "from pkg.beta import Beta; print(Beta().render(6))"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=120,
    )
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip().endswith("B")
    checked = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", str(package)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=600,
    )
    assert "Success" in checked.stdout, checked.stdout + checked.stderr
