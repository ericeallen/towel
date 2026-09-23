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

"""The cycle guard follows each import to the files the program's own imports say it reaches.

The guard used to resolve ``import beta.hub`` against source roots read from
packaging metadata, falling back to matching trailing path components. A
``setup.cfg`` src layout gave it the project root as its only root, and
``src/beta/hub.py`` matched no suffix of the importing file's own tree, so
the edge from ``alpha/a_host.py`` through ``beta.hub`` back to
``alpha/b_borrower.py`` was never seen. The helper went into ``a_host``,
``b_borrower`` gained an import of it, and importing either module then
raised ``ImportError`` from a partially initialized module. The import model
(``ImportModel.files_reached``) places ``beta`` where the program's imports
find it, under ``src``.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap
from typing import List, Mapping, Tuple

import towel
from towel.unification.import_graph import ImportGraphCache, would_create_import_cycle

_BLOCK = """
def {name}(values):
    print({tag!r})
    total = 0
    for value in values:
        if value > 1:
            total += value * 2
        else:
            total -= value
    total = total + 1
    return total
"""

_FILES: Mapping[str, str] = {
    "pyproject.toml": (
        '[build-system]\nrequires = ["setuptools>=61"]\nbuild-backend = "setuptools.build_meta"\n'
    ),
    "setup.cfg": (
        "[metadata]\nname = sample\nversion = 0\n\n"
        "[options]\npackage_dir =\n    =src\npackages = find:\n\n"
        "[options.packages.find]\nwhere = src\n"
    ),
    "src/alpha/__init__.py": "",
    # The host the pair would take first: importing it runs beta.hub, which
    # reads a name the borrower defines.
    "src/alpha/a_host.py": "import beta.hub\n\n" + _BLOCK.format(name="fa", tag="a"),
    "src/alpha/b_borrower.py": "import beta\n\nVALUE = 7\n\n" + _BLOCK.format(name="fb", tag="b"),
    "src/beta/__init__.py": "",
    "src/beta/hub.py": "from alpha.b_borrower import VALUE\n",
}


def _write(root: Path) -> Path:
    for name, text in _FILES.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")
    return root


def _imports(root: Path, module: str) -> Tuple[int, str, List[str]]:
    """Import ``module`` first, in a fresh interpreter with only ``src`` on the path."""
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            f"import sys\nsys.path.insert(0, {str(root / 'src')!r})\nimport {module}\n"
            "from alpha.a_host import fa\nfrom alpha.b_borrower import fb\n"
            "print(fa([0, 2, 3]), fb([1, 5]))\n",
        ],
        capture_output=True,
        text=True,
        env={"PATH": os.environ.get("PATH", "")},
        timeout=60,
    )
    return completed.returncode, completed.stdout, completed.stderr.strip().splitlines()[-1:]


def test_the_guard_sees_a_cycle_through_a_sibling_package(tmp_path: Path) -> None:
    root = _write(tmp_path / "project")
    host, borrower = root / "src/alpha/a_host.py", root / "src/alpha/b_borrower.py"
    assert would_create_import_cycle(str(host), {str(borrower)}, ImportGraphCache())
    assert not would_create_import_cycle(str(borrower), {str(host)}, ImportGraphCache())


def test_the_helper_goes_where_importing_it_closes_no_cycle(tmp_path: Path) -> None:
    original = _write(tmp_path / "original")
    refactored = _write(tmp_path / "refactored")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "towel.cli",
            "dry",
            ".",
            ".",
            "--no-interactive",
            "--no-types",
            "--no-format",
            "--cross-module",
            "--progress",
            "none",
        ],
        capture_output=True,
        text=True,
        cwd=refactored,
        env=dict(os.environ, PYTHONPATH=str(Path(towel.__file__).resolve().parents[1])),
        timeout=600,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "def __extracted_func_0(" in (refactored / "src/alpha/b_borrower.py").read_text()
    assert (
        "from .b_borrower import __extracted_func_0"
        in (refactored / "src/alpha/a_host.py").read_text()
    )
    for first in ("alpha.a_host", "alpha.b_borrower"):
        expected = _imports(original, first)
        assert expected[0] == 0, expected
        assert _imports(refactored, first) == expected, first
