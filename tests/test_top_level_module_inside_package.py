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

"""A top-level name found only as a module file inside a package leaves that name in doubt.

The third audit's P1-6: ``pkg/c.py`` does ``import helpers_top``, which only
``pkg/helpers_top.py`` provides, so ``c.py`` runs with ``pkg`` itself on
``sys.path``, as the top-level module ``c``. The model looked for such a name
only among directories, called it external, and let ``c.py`` borrow a helper
through ``from .a import ...``, which fails exactly where ``c.py`` runs. Now the
module is the name's location, the conflict with ``pkg`` used as a package is
the decided problem, and the run refuses; ``c.py`` is never given a new import.
A bare ``import toml`` beside a package's own ``toml.py`` still names the
library whenever something else provides it.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap
from typing import Mapping, Optional

import pytest

import towel
from towel.cli import STRAY_COPY_REMEDY
from towel.import_model import (
    InstalledProbe,
    NameStatus,
    OutsideProvider,
    ProviderKind,
    TopLevelInsidePackage,
    build_import_model,
    installed_outside,
)


def _write(root: Path, files: Mapping[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
    return root


def _standard_library_only(name: str, root: Path) -> Optional[OutsideProvider]:
    if name in sys.stdlib_module_names or name in sys.builtin_module_names:
        return installed_outside(name, root)
    return None


def _installed_zztoml(name: str, root: Path) -> Optional[OutsideProvider]:
    if name == "zztoml":
        return OutsideProvider(ProviderKind.MODULE, "/site-packages/zztoml/__init__.py")
    return _standard_library_only(name, root)


_AUDITED = {
    "pyproject.toml": '[project]\nname = "pkg"\nversion = "0"\n',
    "pkg/__init__.py": "",
    "pkg/a.py": """
        def fa(xs):
            total = 0
            for x in xs:
                total += x * 3
            print("fa", total)
            return total + 1
    """,
    "pkg/c.py": """
        import helpers_top


        def fc(xs):
            total = 0
            for x in xs:
                total += x * 3
            print("fc", total, helpers_top.SCALE)
            return total + 1
    """,
    "pkg/helpers_top.py": "SCALE = 3\n",
    "tests/__init__.py": "",
    "tests/test_x.py": "import pkg.a\n",
}


def test_the_module_is_the_names_location_and_its_importer_gains_nothing(tmp_path: Path) -> None:
    model = build_import_model(_write(tmp_path, _AUDITED), installed=_standard_library_only)
    root = model.root
    helpers = model.names["helpers_top"]
    assert helpers.status is NameStatus.ATTESTED and helpers.candidates == (
        root / "pkg/helpers_top.py",
    )
    inside = [problem for problem in model.problems if isinstance(problem, TopLevelInsidePackage)]
    assert inside == [
        TopLevelInsidePackage("helpers_top", root / "pkg/helpers_top.py", root / "pkg")
    ]
    assert model.spelling(root / "pkg/c.py", root / "pkg/a.py") is None
    assert model.spelling(root / "pkg/a.py", root / "pkg/c.py") is None


@pytest.mark.parametrize(
    "files, installed",
    [
        ({}, _installed_zztoml),
        (
            {"pyproject.toml": '[project]\nname = "pkg"\ndependencies = ["zztoml"]\n'},
            _standard_library_only,
        ),
    ],
    ids=["installed", "required"],
)
def test_a_library_named_like_a_packages_own_module_is_still_the_library(
    tmp_path: Path, files: Mapping[str, str], installed: InstalledProbe
) -> None:
    """``pkg/zztoml.py`` wraps the library that ``import zztoml`` names."""
    root = _write(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/zztoml.py": "import zztoml\n",
            "pkg/load.py": "import zztoml\n",
            "tests/test_x.py": "import pkg.load\n",
            **files,
        },
    )
    model = build_import_model(root, installed=installed)
    assert model.names["zztoml"].status is NameStatus.EXTERNAL
    assert model.problems == ()


@pytest.mark.parametrize(
    "files",
    [
        # tqdm's keras.py and platformdirs' android.py import the library they are named for.
        {"pkg/zzkeras.py": "import zzkeras\n"},
        # mistune's benchmark imports the markdown library, named like mistune/markdown.py.
        {"pkg/zzkeras.py": "", "benchmark/bench.py": "import zzkeras\n"},
        # httpx's test imports conftest for the checker alone.
        {
            "pkg/zzkeras.py": "",
            "pkg/b.py": "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import zzkeras\n",
        },
    ],
    ids=["imports-itself", "imported-from-elsewhere", "type-checking-only"],
)
def test_an_import_that_does_not_run_beside_the_module_names_the_library(
    tmp_path: Path, files: Mapping[str, str]
) -> None:
    root = _write(
        tmp_path,
        {"pkg/__init__.py": "", "pkg/a.py": "", "tests/test_x.py": "import pkg.a\n", **files},
    )
    model = build_import_model(root, installed=_standard_library_only)
    assert model.names["zzkeras"].status is NameStatus.EXTERNAL
    assert model.problems == ()


def test_a_module_beside_its_importer_is_its_location_at_any_depth(tmp_path: Path) -> None:
    root = _write(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/sub/__init__.py": "",
            "pkg/sub/c.py": "import zzhelpers\n",
            "pkg/sub/zzhelpers.py": "",
            "tests/test_x.py": "import pkg.sub.c\n",
        },
    )
    model = build_import_model(root, installed=_standard_library_only)
    (problem,) = [p for p in model.problems if isinstance(p, TopLevelInsidePackage)]
    assert problem.location == model.root / "pkg/sub/zzhelpers.py"
    assert problem.package == model.root / "pkg"
    assert model.spelling(model.root / "pkg/sub/c.py", model.root / "pkg/__init__.py") is None


def test_a_script_beside_the_module_it_imports_gains_no_import(tmp_path: Path) -> None:
    """A stray ``__init__.py`` does not make a package: ``scripts/run.py`` runs by path.

    Nothing uses ``scripts`` as a package, so nothing is in doubt, and
    ``helpers`` is the module beside the script. Both run as top-level
    modules, with the script's directory on the path, where the relative
    import their directory's package would offer fails; so they are given
    none and host nothing.
    """
    root = _write(
        tmp_path,
        {
            "scripts/__init__.py": "",
            "scripts/run.py": "import helpers\n",
            "scripts/helpers.py": "",
            "scripts/other.py": "",
        },
    )
    model = build_import_model(root, installed=_standard_library_only)
    assert model.names["helpers"].trusted
    assert model.problems == ()
    scripts = model.root / "scripts"
    assert model.spelling(scripts / "run.py", scripts / "other.py") is None
    assert model.spelling(scripts / "helpers.py", scripts / "other.py") is None
    assert model.spelling(scripts / "other.py", scripts / "helpers.py") is None
    assert model.spelling(scripts / "other.py", scripts / "__init__.py") is not None


def test_the_audited_run_refuses(tmp_path: Path) -> None:
    root = _write(tmp_path / "project", _AUDITED)
    before = {path: path.read_bytes() for path in root.rglob("*.py")}
    ran = subprocess.run(
        [
            sys.executable,
            "-m",
            "towel.cli",
            "dry",
            str(root),
            str(root),
            "--no-interactive",
            "--progress",
            "none",
            "--no-types",
            "--no-format",
            "--cross-module",
        ],
        capture_output=True,
        text=True,
        env=dict(os.environ, PYTHONPATH=str(Path(towel.__file__).resolve().parents[1])),
        timeout=600,
    )
    assert ran.returncode == 1, ran.stdout + ran.stderr
    assert (
        "helpers_top is imported as a top-level name, and its only location pkg/helpers_top.py"
        " is inside pkg, which the program also imports as a package"
    ) in ran.stderr
    assert STRAY_COPY_REMEDY in ran.stderr
    assert {path: path.read_bytes() for path in root.rglob("*.py")} == before
