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

"""A borrower that belongs to a distribution borrows a helper only from its own distribution.

In a monorepo, ``beta`` depends on ``alpha``, and each is released on its own.
Towel made ``beta/b.py`` import a helper from ``alpha/a.py``, so the new beta
installed against the released alpha raised ``ImportError: cannot import name
'__extracted_func_0' from 'alpha.a'``. A distribution is the nearest directory
with a ``setup.py``, a ``setup.cfg`` declaring metadata or options, or a
``pyproject.toml`` with a ``[project]``, ``[build-system]`` or ``[tool.poetry]``
table; a module in none, such as a test beside the packages, keeps the rule
it had.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Dict, Optional

import pytest

from towel.shipped_files import distribution_root
from towel.unification.import_graph import ImportChange, ImportGraphCache, import_change
from towel.unification.refactor_engine import UnificationRefactorEngine

_BODY = (
    "def summarize_{n}(rows, factor):\n    marker = 0\n    total = 0\n    for r in rows:\n"
    "        total += len(r) * factor\n    label = f'n={{total}}'\n    marker += 1\n"
    "    return '{N}' + label.upper() + str(marker)\n"
)


def _write(root: Path, files: Dict[str, str]) -> Path:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(textwrap.dedent(text))
    return root


@pytest.mark.parametrize(
    "configuration, declares",
    [
        ({"pyproject.toml": "[project]\nname = 'p'\nversion = '0'\n"}, True),
        ({"pyproject.toml": "[build-system]\nbuild-backend = 'hatchling.build'\n"}, True),
        ({"pyproject.toml": "[tool.poetry]\nname = 'p'\n"}, True),
        ({"pyproject.toml": "[tool.ruff]\nline-length = 100\n"}, False),
        ({"setup.py": "from setuptools import setup\nsetup()\n"}, True),
        ({"setup.cfg": "[metadata]\nname = p\n"}, True),
        ({"setup.cfg": "[flake8]\nmax-line-length = 100\n"}, False),
    ],
    ids=["project", "build-system", "poetry", "tool-only", "setup.py", "setup.cfg", "flake8-only"],
)
def test_what_makes_a_distribution(
    tmp_path: Path, configuration: Dict[str, str], declares: bool
) -> None:
    root = _write(tmp_path / "member", {**configuration, "src/m/a.py": ""})
    found: Optional[Path] = distribution_root(root / "src/m/a.py", tmp_path)
    assert found == (root if declares else None)


_MONOREPO = {
    "packages/alpha/pyproject.toml": "[project]\nname = 'alpha'\nversion = '0.1'\n",
    "packages/alpha/src/alpha/__init__.py": "",
    "packages/alpha/src/alpha/a.py": _BODY.format(n="a", N="A"),
    "packages/beta/pyproject.toml": "[project]\nname = 'beta'\nversion = '0.1'\n"
    "dependencies = ['alpha']\n",
    "packages/beta/src/beta/__init__.py": "",
    "packages/beta/src/beta/b.py": "import alpha\n\n\n" + _BODY.format(n="b", N="B"),
    "tests/test_alpha.py": "import alpha.a\n\n\ndef test_it():\n    assert alpha.a\n",
}


def _change(root: Path, host: str, borrower: str) -> Optional[ImportChange]:
    """What the import would change in a run over the whole repository, as the engine reads it."""
    cache = ImportGraphCache()
    cache.begin_run(root.resolve(), root.resolve())
    return import_change(str(root / host), str(root / borrower), cache)


def test_a_borrower_in_another_distribution_is_refused(tmp_path: Path) -> None:
    root = _write(tmp_path, _MONOREPO)
    alpha, beta = "packages/alpha/src/alpha/a.py", "packages/beta/src/beta/b.py"
    assert _change(root, alpha, beta) is ImportChange.OTHER_DISTRIBUTION
    assert _change(root, beta, alpha) is ImportChange.OTHER_DISTRIBUTION


def test_a_module_in_no_distribution_keeps_its_rule(tmp_path: Path) -> None:
    """A test beside the packages borrows from the package it imports, as before."""
    root = _write(tmp_path, _MONOREPO)
    assert _change(root, "packages/alpha/src/alpha/a.py", "tests/test_alpha.py") is None


def _python_path(*paths: Path) -> Dict[str, str]:
    return {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": os.pathsep.join(str(path) for path in paths),
    }


def test_r9xh_the_new_beta_imports_against_the_released_alpha(tmp_path: Path) -> None:
    released = _write(tmp_path / "released", _MONOREPO)
    root = tmp_path / "repo"
    shutil.copytree(released, root)
    engine = UnificationRefactorEngine(min_lines=3, cross_module_helpers=True)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        engine.refactor_directory_to_fixed_point(str(root), str(root), progress="none")
    assert "from alpha" not in (root / "packages/beta/src/beta/b.py").read_text()
    ran = subprocess.run(
        [sys.executable, "-c", "import beta.b; print(beta.b.summarize_b(['ab'], 2))"],
        capture_output=True,
        text=True,
        env=_python_path(released / "packages/alpha/src", root / "packages/beta/src"),
    )
    assert ran.stdout == "BN=41\n", ran.stderr
