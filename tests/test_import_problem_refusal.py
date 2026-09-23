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

"""With --cross-module, a run refuses when the program's imports leave its own package's names in doubt.

A helper shared across modules is imported by the name the program's own
imports give its host. Before anything is written, a ``--cross-module`` run
names every problem those imports have, with the remedy. One that involves
the package being refactored refuses the run: anyio's stale
``build/lib/anyio`` beside ``anyio`` makes every name of the package
ambiguous. One that involves only other modules, a broken fixture in the
test data, is reported and the run goes on; the model already declines the
names it involves. Without ``--cross-module`` no import that runs is written,
so no problem can matter, and none is reported.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap
from typing import Mapping

import towel
from towel.cli import _problems_involving
from towel.import_model import build_import_model

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

_WITHIN = """
def {name}(words):
    print({tag!r})
    seen = []
    for word in words:
        cleaned = word.strip().lower()
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
    return ", ".join(seen)
"""


def _project(root: Path, extra: Mapping[str, str] = {}) -> Path:
    """``zzalpha`` under ``src``: a block shared by two modules, another within ``b``."""
    files = {
        "pyproject.toml": (
            '[build-system]\nrequires = ["setuptools>=61"]\n'
            'build-backend = "setuptools.build_meta"\n'
            '[project]\nname = "zzalpha"\nversion = "0"\n'
            '[tool.setuptools.packages.find]\nwhere = ["src"]\n'
        ),
        "src/zzalpha/__init__.py": "",
        "src/zzalpha/a.py": _BLOCK.format(name="fa", tag="a"),
        "src/zzalpha/b.py": _BLOCK.format(name="fb", tag="b")
        + _WITHIN.format(name="gb", tag="gb")
        + _WITHIN.format(name="hb", tag="hb"),
        "tests/test_a.py": "import zzalpha.a\n",
        **extra,
    }
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
    return root


_STALE_COPY = {"build/lib/zzalpha/__init__.py": "", "build/lib/zzalpha/a.py": "VALUE = 1\n"}
_BROKEN_TEST_DATA = {
    "tests/data/broken/__init__.py": "",
    "tests/data/broken/mod.py": "from .missing import thing\n",
}


def _towel(root: Path, command: str, *flags: str) -> subprocess.CompletedProcess[str]:
    target = "src/zzalpha"
    arguments = [command, target, target] if command == "dry" else [command, target]
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "towel.cli",
            *arguments,
            "--progress",
            "none",
            *(("--no-interactive", "--no-types", "--no-format") if command == "dry" else ()),
            *flags,
        ],
        capture_output=True,
        text=True,
        cwd=root,
        env=dict(os.environ, PYTHONPATH=str(Path(towel.__file__).resolve().parents[1])),
        timeout=600,
    )


def _sources(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*.py"))
    }


def test_a_problem_of_the_package_being_refactored_refuses_the_run(tmp_path: Path) -> None:
    root = _project(tmp_path / "project", _STALE_COPY)
    before = _sources(root)
    for command in ("dry", "preview"):
        refused = _towel(root, command, "--cross-module")
        assert refused.returncode == 1, refused.stdout + refused.stderr
        assert "Refusing to share helpers across the modules of" in refused.stderr, refused.stderr
        assert "zzalpha could be any of: build/lib/zzalpha; src/zzalpha" in refused.stderr
        assert "--exclude <directory name> (for example --exclude build)" in refused.stderr
        assert "APPLYING" not in refused.stdout and "Analyzing" not in refused.stdout
        assert _sources(root) == before
    # Set the stray copy aside and the run shares the helper.
    ran = _towel(root, "dry", "--cross-module", "--exclude", "build")
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "could be any of" not in ran.stderr
    assert "from .a import __extracted_func" in (root / "src/zzalpha/b.py").read_text()


def test_a_problem_only_in_the_test_data_is_reported_and_the_run_goes_on(tmp_path: Path) -> None:
    root = _project(tmp_path / "project", _BROKEN_TEST_DATA)
    ran = _towel(root, "dry", "--cross-module")
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "tests/data/broken/mod.py:1: from .missing import thing names .missing" in ran.stderr
    assert "--exclude <directory name>" in ran.stderr
    assert "from .a import __extracted_func" in (root / "src/zzalpha/b.py").read_text()


def test_without_cross_module_no_problem_is_reported_or_refuses(tmp_path: Path) -> None:
    """Only a same-file helper can be written, and no import that runs depends on the names."""
    root = _project(tmp_path / "project", _STALE_COPY)
    ran = _towel(root, "dry")
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "could be any of" not in ran.stderr and "--exclude <directory name>" not in ran.stderr
    b = (root / "src/zzalpha/b.py").read_text()
    assert "def __extracted_func_0(" in b, b
    assert "import" not in (root / "src/zzalpha/a.py").read_text()
    previewed = _towel(root, "preview")
    assert previewed.returncode == 0, previewed.stdout + previewed.stderr
    assert "could be any of" not in previewed.stderr


def test_which_problems_involve_the_target(tmp_path: Path) -> None:
    root = _project(
        tmp_path / "project",
        {
            **_STALE_COPY,
            **_BROKEN_TEST_DATA,
        },
    )
    model = build_import_model(root)
    described = {problem.describe(model.root) for problem in model.problems}
    target = root / "src" / "zzalpha"
    involved = {problem.describe(model.root) for problem in _problems_involving(model, target)}
    assert any("could be any of" in text for text in involved)
    assert not any("tests/data/broken" in text for text in involved)
    assert any("tests/data/broken" in text for text in described - involved)
    # From the project root every file lies under the target.
    assert {
        problem.describe(model.root) for problem in _problems_involving(model, root)
    } == described
