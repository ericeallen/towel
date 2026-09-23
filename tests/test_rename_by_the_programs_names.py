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

"""A rename matches each import to the module the program's own imports mean by it.

``rename-helpers`` follows an import of a helper to its definition by module
name. It named modules from packaging metadata, and a ``setup.cfg`` src
layout, which no reader read, named ``src/alpha/a.py`` ``src.alpha.a``; the
tests' ``from alpha.a import __extracted_func_0`` then matched nothing, and
the rename refused the whole project. Modules are now named as the program's
imports name them (``ImportModel.module_name``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from typing import Mapping

import pytest

from tests.test_cli_integration import invoke
from towel.renaming import plan_renames

HELPER = "def __extracted_func_0(value):\n    return value + 1\n"

_SRC_LAYOUT: Mapping[str, str] = {
    "pyproject.toml": (
        '[build-system]\nrequires = ["setuptools>=61"]\nbuild-backend = "setuptools.build_meta"\n'
    ),
    "setup.cfg": (
        "[metadata]\nname = sample\nversion = 0\n\n"
        "[options]\npackage_dir =\n    =src\npackages = find:\n\n"
        "[options.packages.find]\nwhere = src\n"
    ),
    "src/alpha/__init__.py": "",
    "src/alpha/a.py": HELPER + "\n\ndef fa(value):\n    return __extracted_func_0(value)\n",
    "src/alpha/b.py": (
        "from .a import __extracted_func_0\n\n\n"
        "def fb(value):\n    return __extracted_func_0(value) * 2\n"
    ),
    "tests/test_a.py": (
        "import alpha.a\nfrom alpha.a import __extracted_func_0\n\n\n"
        "def test_a():\n    return __extracted_func_0(3), alpha.a.__extracted_func_0(4)\n"
    ),
}


def _write(root: Path, files: Mapping[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")
    return root


def _results(root: Path) -> str:
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            "import sys\n"
            f"sys.path[:0] = [{str(root / 'tests')!r}, {str(root / 'src')!r}]\n"
            "import test_a\nfrom alpha.a import fa\nfrom alpha.b import fb\n"
            "print(test_a.test_a(), fa(1), fb(2))\n",
        ],
        capture_output=True,
        text=True,
        env={"PATH": os.environ.get("PATH", "")},
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def test_a_src_layout_helper_is_renamed_with_every_importer(tmp_path: Path) -> None:
    root = _write(tmp_path / "project", _SRC_LAYOUT)
    expected = _results(root)
    mapping = tmp_path / "renames.json"
    mapping.write_text(json.dumps({"__extracted_func_0": "increment"}), encoding="utf-8")
    result = invoke(["rename-helpers", str(root), "--rename-file", str(mapping)])
    assert result.status == 0, result.stdout + result.stderr
    assert "def increment(" in (root / "src/alpha/a.py").read_text(encoding="utf-8")
    assert "from .a import increment" in (root / "src/alpha/b.py").read_text(encoding="utf-8")
    test = (root / "tests/test_a.py").read_text(encoding="utf-8")
    assert "from alpha.a import increment" in test and "alpha.a.increment(4)" in test
    assert "extracted_func" not in "".join(
        path.read_text(encoding="utf-8") for path in root.rglob("*.py")
    )
    assert _results(root) == expected


def test_an_import_of_a_name_the_program_leaves_ambiguous_is_refused(tmp_path: Path) -> None:
    """A stale ``build/lib/alpha`` beside ``src/alpha``: which one the tests import is unknown."""
    root = _write(
        tmp_path / "project",
        {**_SRC_LAYOUT, "build/lib/alpha/__init__.py": "", "build/lib/alpha/a.py": HELPER},
    )
    with pytest.raises(ValueError, match="not a module of the rename target"):
        plan_renames(root, [("__extracted_func_0", "increment", root / "src/alpha/a.py")])
