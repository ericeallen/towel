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

"""A generated import names a module the installed project actually has.

The layout readers reimplement five build backends, and checked against built
wheels they named 102 files of seven corpus projects wrongly (``src.foo.a``
for a ``setup.cfg`` src layout they never read) and 937 more when a project
directory is named like its package (``foo.src.foo.a``). The checker cannot
see it: without a project configuration it names modules from the same root.
Each case runs Towel, then imports the result the way the installed project
would be imported, which is the oracle.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap
from typing import Mapping

import towel

from towel.project_layout import ProjectLayout
from towel.unification.insertion import relative_import_module

_BLOCK = """
def {name}(values):
    {prologue}
    total = 0
    for value in values:
        if value > 1:
            total += value * 2
        else:
            total -= value
    total = total + 1
    return total
"""


def _write(root: Path, files: Mapping[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")


def _dry(cwd: Path, target: str, output: Path) -> str:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "towel.cli",
            "dry",
            target,
            str(output),
            "--no-interactive",
            "--cross-module",
            "--progress",
            "none",
            "--no-types",
            "--min-lines",
            "3",
        ],
        capture_output=True,
        text=True,
        cwd=cwd,
        # The Towel under test, wherever the subprocess runs from.
        env=dict(os.environ, PYTHONPATH=str(Path(towel.__file__).resolve().parents[1])),
        timeout=600,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout + result.stderr


def _imports(sys_path: Path, *modules: str) -> None:
    for module in modules:
        result = subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            capture_output=True,
            text=True,
            cwd=sys_path,
            env={"PYTHONPATH": str(sys_path), "PYTHONDONTWRITEBYTECODE": "1"},
            timeout=60,
        )
        assert result.returncode == 0, f"import {module}: {result.stderr}"


def _generated_imports(root: Path, *, required: bool = True) -> str:
    """The helper imports Towel wrote; there must be one, or the case tested nothing."""
    lines = [
        line
        for path in sorted(root.rglob("*.py"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if "extracted" in line and line.startswith("from ")
    ]
    assert lines or not required, "no cross-module helper was imported"
    return "\n".join(lines)


def test_sibling_top_level_packages_are_not_reached_by_climbing_above_them(
    tmp_path: Path,
) -> None:
    """``..api.checkout`` from ``utils/v.py`` climbs out of ``utils``, which Python refuses."""
    source = tmp_path / "project"
    _write(
        source,
        {
            "api/__init__.py": "",
            "api/checkout.py": _BLOCK.format(
                name="checkout", limit=1, prologue="values = list(values)"
            ),
            "utils/__init__.py": "",
            # A package borrows only from one it already imports.
            "utils/validators.py": "import api.checkout\n\n\n"
            + _BLOCK.format(name="validate", limit=2, prologue="print(len(values))"),
        },
    )
    output = tmp_path / "out"
    _dry(tmp_path, str(source), output)
    generated = _generated_imports(output)
    assert "from .." not in generated, generated
    _imports(output, "api.checkout", "utils.validators")


def test_relative_import_is_refused_above_the_top_package(tmp_path: Path) -> None:
    _write(tmp_path, {"api/__init__.py": "", "api/c.py": "", "utils/__init__.py": ""})
    (tmp_path / "utils" / "v.py").write_text("", encoding="utf-8")
    assert relative_import_module(tmp_path / "api" / "c.py", tmp_path / "utils" / "v.py") is None
    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    assert (
        relative_import_module(tmp_path / "api" / "c.py", tmp_path / "utils" / "v.py") == "..api.c"
    )


def test_an_unread_setup_cfg_src_layout_never_names_src(tmp_path: Path) -> None:
    """setup.cfg's ``package_dir = =src`` is not read; the markers say ``foo.a``, the reader ``src.foo.a``."""
    project = tmp_path / "proj"
    _write(
        project,
        {
            "setup.cfg": "[metadata]\nname = foo\n\n[options]\npackage_dir =\n    =src\npackages = find:\n\n[options.packages.find]\nwhere = src\n",
            "pyproject.toml": '[build-system]\nrequires = ["setuptools"]\nbuild-backend = "setuptools.build_meta"\n',
            "src/foo/__init__.py": "",
            "src/foo/a.py": _BLOCK.format(
                name="total_a", limit=1, prologue="values = list(values)"
            ),
            "src/foo/b.py": _BLOCK.format(name="total_b", limit=2, prologue="print(len(values))"),
        },
    )
    _dry(project, "src/foo", tmp_path / "out")
    generated = _generated_imports(tmp_path / "out")
    assert "src." not in generated, generated
    adopted = tmp_path / "adopted"
    (adopted).mkdir()
    (tmp_path / "out").rename(adopted / "foo")
    _imports(adopted, "foo.a", "foo.b")


def test_a_project_directory_named_like_its_package_is_not_a_package(tmp_path: Path) -> None:
    """From the project root, ``foo/src/foo/a.py`` was named ``foo.src.foo.a``."""
    project = tmp_path / "foo"
    _write(
        project,
        {
            "pyproject.toml": (
                '[project]\nname = "foo"\nversion = "0"\n'
                '[build-system]\nrequires = ["setuptools"]\n'
                'build-backend = "setuptools.build_meta"\n'
            ),
            "src/foo/__init__.py": "",
            "src/foo/a.py": _BLOCK.format(
                name="total_a", limit=1, prologue="values = list(values)"
            ),
            "tools/b.py": "import foo.a\n\n\n"
            + _BLOCK.format(name="total_b", limit=2, prologue="print(len(values))"),
        },
    )
    output = tmp_path / "out"
    log = _dry(tmp_path, str(project), output)
    # ``tools/b.py`` is in no package, so no relative import reaches it, and
    # the two derivations of an absolute name disagree: declining is correct.
    generated = _generated_imports(output, required=False)
    assert "foo.src" not in generated and "src.foo" not in generated, generated
    assert generated or "can be shown to resolve" in log, log


def test_a_hatch_include_without_sources_keeps_the_path_it_names(tmp_path: Path) -> None:
    """``include = ["/src/foo"]`` ships ``src/foo/a.py`` as ``src.foo.a``: nothing relocates it."""
    _write(
        tmp_path,
        {
            "pyproject.toml": (
                '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n'
                '[project]\nname = "foo"\nversion = "0"\n'
                '[tool.hatch.build.targets.wheel]\ninclude = ["/src/foo"]\n'
            ),
            "src/foo/__init__.py": "",
            "src/foo/a.py": "",
        },
    )
    module = tmp_path / "src" / "foo" / "a.py"
    assert ProjectLayout.discover(module).module_name_for(module) == "src.foo.a"
