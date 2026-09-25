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

"""A module with a stub of its own never hosts a helper another module imports.

A type checker reads ``alpha/a.pyi`` for ``alpha.a`` wherever it is
imported, so a helper added to ``alpha/a.py`` is invisible to it: ``from
alpha.a import __extracted_func_0`` in ``alpha/b.py`` is an unknown import
symbol to pyright and an attribute ``alpha.a`` lacks to mypy (audit case
k30). The helper goes to another participating module instead, or the pair
is declined. A stub in a ``<package>-stubs`` directory or under ``typings``
counts as well.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
import subprocess
import sys
import textwrap
from typing import Dict, List

import pytest

from towel.unification.refactor_engine import UnificationRefactorEngine

BODY = """
    print("begin {tag}")
    total = 0
    for item in items:
        if item > 0:
            total += item * scale
        else:
            total -= item
    count = len(items)
    print("{tag}", total, count)
    return total + count + {offset}
"""


def _function(name: str, tag: str, offset: int) -> str:
    return (
        f"def {name}(items: list[int], scale: int) -> int:\n"
        + textwrap.indent(textwrap.dedent(BODY.format(tag=tag, offset=offset)).strip("\n"), "    ")
        + "\n"
    )


def _write(root: Path, files: Dict[str, str]) -> None:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _mypy_errors(root: Path) -> List[str]:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--no-incremental",
            "--cache-dir=/dev/null",
            "--no-error-summary",
            "-p",
            "alpha",
        ],
        cwd=root / "src",
        capture_output=True,
        text=True,
        check=False,
    )
    return sorted(line for line in completed.stdout.splitlines() if ": error:" in line)


@pytest.mark.parametrize(
    "stubs, hosted",
    [
        ({"src/alpha/a.pyi": "STUB"}, True),
        ({"src/alpha-stubs/a.pyi": "STUB", "src/alpha-stubs/py.typed": "partial\n"}, True),
        # A stub package that is not partial hides every module it omits, b.py
        # included, from the checker: no module of the package can host.
        ({"src/alpha-stubs/a.pyi": "STUB"}, False),
        ({"typings/alpha/a.pyi": "STUB"}, True),
    ],
    ids=["sibling", "partial-stub-package", "stub-package", "typings"],
)
def test_the_typed_project_still_checks_after_the_extraction(
    tmp_path: Path, stubs: Dict[str, str], hosted: bool
) -> None:
    stub = "def fa(items: list[int], scale: int) -> int: ...\n"
    files = {
        "pyproject.toml": '[project]\nname = "alpha"\nversion = "0.1"\n',
        "src/alpha/__init__.py": "",
        "src/alpha/py.typed": "",
        "src/alpha/a.py": _function("fa", "a", 1),
        "src/alpha/b.py": _function("fb", "bb", 2),
        **{path: stub if text == "STUB" else text for path, text in stubs.items()},
    }
    _write(tmp_path, files)
    engine = UnificationRefactorEngine(
        min_lines=3, annotate_helpers=False, cross_module_helpers=True
    )
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        results, _ = engine.refactor_directory_to_fixed_point(
            str(tmp_path / "src" / "alpha"), str(tmp_path / "src" / "alpha"), progress="none"
        )
    assert (sum(applied for applied, _ in results.values()) > 0) == hosted
    assert "def __extracted_func" not in (tmp_path / "src" / "alpha" / "a.py").read_text()
    if "src/alpha/a.pyi" in stubs:
        # The checker reads the sibling stub in place of the module.
        assert _mypy_errors(tmp_path) == []
