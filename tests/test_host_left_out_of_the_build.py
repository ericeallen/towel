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

"""A module the build leaves out never hosts a helper for one it ships.

The third audit's D5: hatch's ``exclude = ["src/shop/_devtools.py"]`` keeps one
module out of a wheel whose package ships, and Towel hosted the helper
``shop/stats.py`` shares with it there, so the installed ``shop.stats`` raised
ModuleNotFoundError. In the second shape setuptools' ``find`` excluded
``shop.devtools``, and a function-level import of it in ``shop/cli.py`` passed
for evidence that the directory ships. Now what the build configuration
declares left out is read, only to put a host in doubt
(``towel.shipped_files``), and only an import that runs whenever its module is
imported shows a directory ships. The fragments below cover each declaration
read; one wheel built by ``uv build`` checks the first shape installed.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap
from typing import FrozenSet, Mapping, Optional

import pytest

import towel
from towel.import_model import ImportModel, OutsideProvider, build_import_model, installed_outside
from towel.shipped_files import Artifact, left_out

_BOTH = frozenset({Artifact.SDIST, Artifact.WHEEL})
_WHEEL = frozenset({Artifact.WHEEL})


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


_SHOP = {
    "src/shop/__init__.py": "",
    "src/shop/stats.py": "",
    "src/shop/_devtools.py": "",
    "src/shop/devtools/__init__.py": "",
    "src/shop/devtools/dump.py": "",
    "tests/test_shop.py": "import shop.stats\n",
}


def _left_out(root: Path, files: Mapping[str, str]) -> Mapping[str, FrozenSet[Artifact]]:
    _write(root, {**_SHOP, **files})
    modules = [path.resolve() for path in root.rglob("*.py")]
    found = left_out(root, modules)
    return {
        path.relative_to(root.resolve()).as_posix(): artifacts for path, artifacts in found.items()
    }


@pytest.mark.parametrize(
    "files, expected",
    [
        (
            {
                "pyproject.toml": '[tool.hatch.build.targets.wheel]\nexclude = ["src/shop/_devtools.py"]\n'
            },
            {"src/shop/_devtools.py": _WHEEL},
        ),
        (
            {"pyproject.toml": '[tool.hatch.build]\nexclude = ["/src/shop/devtools/"]\n'},
            {"src/shop/devtools/__init__.py": _BOTH, "src/shop/devtools/dump.py": _BOTH},
        ),
        (
            {"pyproject.toml": '[tool.hatch.build.targets.sdist]\nexclude = ["_dev*.py"]\n'},
            {"src/shop/_devtools.py": _BOTH},
        ),
        (
            {"pyproject.toml": '[tool.hatch.build.targets.wheel]\npackages = ["src/shop"]\n'},
            {"tests/test_shop.py": _WHEEL},
        ),
        (
            {
                "pyproject.toml": "[tool.hatch.build.targets.wheel]\n"
                'only-include = ["src/shop/stats.py", "src/shop/__init__.py"]\n'
            },
            {
                "src/shop/_devtools.py": _WHEEL,
                "src/shop/devtools/__init__.py": _WHEEL,
                "src/shop/devtools/dump.py": _WHEEL,
                "tests/test_shop.py": _WHEEL,
            },
        ),
        (
            {"pyproject.toml": '[tool.hatch.build.targets.wheel]\ninclude = ["/src/shop/*.py"]\n'},
            {
                "src/shop/devtools/__init__.py": _WHEEL,
                "src/shop/devtools/dump.py": _WHEEL,
                "tests/test_shop.py": _WHEEL,
            },
        ),
        (
            {
                "pyproject.toml": '[tool.setuptools.packages.find]\nwhere = ["src"]\n'
                'exclude = ["shop.devtools*"]\n'
            },
            {"src/shop/devtools/__init__.py": _WHEEL, "src/shop/devtools/dump.py": _WHEEL},
        ),
        (
            {
                "setup.cfg": "[options]\npackage_dir =\n    =src\npackages = find:\n"
                "[options.packages.find]\nwhere = src\nexclude =\n    shop.devtools\n"
            },
            {"src/shop/devtools/__init__.py": _WHEEL, "src/shop/devtools/dump.py": _WHEEL},
        ),
        (
            {
                "pyproject.toml": '[tool.setuptools]\npackage-dir = {"" = "src"}\npackages = ["shop"]\n'
            },
            {"src/shop/devtools/__init__.py": _WHEEL, "src/shop/devtools/dump.py": _WHEEL},
        ),
        (
            {
                "MANIFEST.in": "exclude src/shop/_devtools.py\n"
                "recursive-exclude src/shop/devtools dump.py\n"
            },
            {"src/shop/_devtools.py": _BOTH, "src/shop/devtools/dump.py": _BOTH},
        ),
        (
            {"MANIFEST.in": "prune src/shop/devtools\nglobal-exclude test_*.py\n"},
            {
                "src/shop/devtools/__init__.py": _BOTH,
                "src/shop/devtools/dump.py": _BOTH,
                "tests/test_shop.py": _BOTH,
            },
        ),
        (
            {"pyproject.toml": '[tool.poetry]\nexclude = ["src/shop/_devtools.py"]\n'},
            {"src/shop/_devtools.py": _BOTH},
        ),
        (
            {"pyproject.toml": '[tool.pdm.build]\nexcludes = ["**/_devtools.py"]\n'},
            {"src/shop/_devtools.py": _BOTH},
        ),
        (
            {"pyproject.toml": '[tool.uv.build-backend]\nwheel-exclude = ["_devtools.py"]\n'},
            {"src/shop/_devtools.py": _WHEEL},
        ),
        (
            {"pyproject.toml": '[tool.flit.sdist]\nexclude = ["src/shop/_devtools.py"]\n'},
            {"src/shop/_devtools.py": _BOTH},
        ),
        (
            {"pyproject.toml": '[tool.scikit-build]\nwheel.exclude = ["**/_devtools.py"]\n'},
            {"src/shop/_devtools.py": _WHEEL},
        ),
        ({".gitignore": "*.pyc\n_devtools.py\n"}, {"src/shop/_devtools.py": _BOTH}),
        (
            # A negation puts nothing back.
            {"src/shop/.gitignore": "/devtools/\n!/devtools/dump.py\n"},
            {"src/shop/devtools/__init__.py": _BOTH, "src/shop/devtools/dump.py": _BOTH},
        ),
    ],
    ids=[
        "hatch-wheel-exclude",
        "hatch-exclude-directory",
        "hatch-sdist-exclude",
        "hatch-packages",
        "hatch-only-include",
        "hatch-include",
        "setuptools-find-exclude",
        "setup-cfg-find-exclude",
        "setuptools-package-list",
        "manifest-exclude",
        "manifest-prune",
        "poetry-exclude",
        "pdm-excludes",
        "uv-wheel-exclude",
        "flit-sdist-exclude",
        "scikit-build-exclude",
        "gitignore",
        "nested-gitignore",
    ],
)
def test_each_declared_exclusion_leaves_its_modules_out(
    tmp_path: Path, files: Mapping[str, str], expected: Mapping[str, FrozenSet[Artifact]]
) -> None:
    """What the sdist leaves out the wheel built from it lacks too."""
    assert _left_out(tmp_path, files) == expected


@pytest.mark.parametrize(
    "files",
    [
        {},
        {"pyproject.toml": '[tool.setuptools.exclude-package-data]\nshop = ["_devtools.py"]\n'},
        {"pyproject.toml": '[tool.hatch.build.targets.wheel]\nexclude = ["/_devtools.py"]\n'},
        {"MANIFEST.in": "include src/shop/*.py\ngraft src\n"},
    ],
    ids=["nothing", "exclude-package-data", "anchored-elsewhere", "only-includes"],
)
def test_what_no_exclusion_covers_ships(tmp_path: Path, files: Mapping[str, str]) -> None:
    """The control: ``exclude-package-data`` applies to data files; setuptools ships every module."""
    assert _left_out(tmp_path, files) == {}


def _model(root: Path) -> ImportModel:
    return build_import_model(root, installed=_standard_library_only)


def _spelled(model: ImportModel, importer: str, provider: str) -> Optional[str]:
    spelling = model.spelling(model.root / importer, model.root / provider)
    return None if spelling is None else spelling.module


def test_a_module_the_build_leaves_out_hosts_nothing_for_one_it_ships(tmp_path: Path) -> None:
    """The auditor's first shape, and the direction that stays sound: the kept module hosts."""
    root = _write(
        tmp_path,
        {
            "pyproject.toml": '[tool.hatch.build.targets.wheel]\npackages = ["src/shop"]\n'
            'exclude = ["src/shop/_devtools.py"]\n',
            "src/shop/__init__.py": "",
            "src/shop/stats.py": "",
            "src/shop/_devtools.py": "",
            "src/shop/other.py": "",
            "tests/test_shop.py": "import shop.stats\n",
        },
    )
    model = _model(root)
    assert _spelled(model, "src/shop/stats.py", "src/shop/_devtools.py") is None
    assert _spelled(model, "src/shop/_devtools.py", "src/shop/stats.py") == ".stats"
    assert _spelled(model, "src/shop/other.py", "src/shop/stats.py") == ".stats"


def test_a_directory_is_entered_only_on_evidence_that_runs_on_import(tmp_path: Path) -> None:
    """The second shape without its packaging: ``cli.main`` imports ``devtools`` only when called."""
    lazy = "def main(argv):\n    from .devtools.dump import dump\n    return dump(argv)\n"
    root = _write(
        tmp_path,
        {
            "src/shop/__init__.py": "",
            "src/shop/stats.py": "",
            "src/shop/cli.py": lazy,
            "src/shop/devtools/__init__.py": "",
            "src/shop/devtools/dump.py": "",
            "tests/test_shop.py": "import shop.stats\n",
        },
    )
    assert _spelled(_model(root), "src/shop/stats.py", "src/shop/devtools/dump.py") is None
    _write(root, {"src/shop/cli.py": "from .devtools.dump import dump\n"})
    assert (
        _spelled(_model(root), "src/shop/stats.py", "src/shop/devtools/dump.py") == ".devtools.dump"
    )
    # Declared out of the wheel, it hosts nothing for a module the wheel keeps, evidence or not.
    _write(
        root,
        {
            "pyproject.toml": '[tool.setuptools.packages.find]\nwhere = ["src"]\nexclude = ["shop.devtools*"]\n'
        },
    )
    assert _spelled(_model(root), "src/shop/stats.py", "src/shop/devtools/dump.py") is None


# -- The first shape, built and installed -------------------------------------------------------

requires_uv = pytest.mark.skipif(shutil.which("uv") is None, reason="building a wheel needs uv")


def _uv(*command: str) -> None:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME"}
    }
    subcommand = 2 if command[0] == "pip" else 1
    result: Optional[subprocess.CompletedProcess[str]] = None
    for offline in (["--offline"], []):
        arguments = [*command[:subcommand], *offline, *command[subcommand:]]
        result = subprocess.run(
            ["uv", *arguments], capture_output=True, text=True, env=environment, timeout=600
        )
        if result.returncode == 0:
            return
    assert result is not None
    raise AssertionError(f"uv {' '.join(command)} failed:\n{result.stdout}{result.stderr}")


_BLOCK = """
    count = len(values)
    biggest = max(values)
    smallest = min(values)
    return f"{prefix}{count} values, {smallest}..{biggest}"
"""


@requires_uv
def test_the_audited_exclusion_leaves_the_installed_wheel_working(tmp_path: Path) -> None:
    project = _write(
        tmp_path / "project",
        {
            "pyproject.toml": """
                [build-system]
                requires = ["hatchling"]
                build-backend = "hatchling.build"
                [project]
                name = "shop"
                version = "0.1"
                [tool.hatch.build.targets.wheel]
                packages = ["src/shop"]
                exclude = ["src/shop/_devtools.py"]
            """,
            "src/shop/__init__.py": "",
            "src/shop/stats.py": "def describe(values):\n    prefix = ''" + _BLOCK,
            "src/shop/_devtools.py": "def dump(values):\n    prefix = 'dev: '" + _BLOCK,
        },
    )
    ran = subprocess.run(
        [
            sys.executable,
            "-m",
            "towel.cli",
            "dry",
            str(project),
            str(project),
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
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "import" not in (project / "src/shop/stats.py").read_text()
    _uv("build", "--wheel", "-q", "-o", str(tmp_path / "dist"), str(project))
    (wheel,) = sorted((tmp_path / "dist").glob("*.whl"))
    environment = tmp_path / "environment"
    _uv("venv", "-q", "--python", sys.executable, str(environment))
    _uv(
        "pip",
        "install",
        "-q",
        "--no-deps",
        "--python",
        str(environment / "bin" / "python"),
        str(wheel),
    )
    installed = subprocess.run(
        [
            str(environment / "bin" / "python"),
            "-I",
            "-c",
            "from shop.stats import describe; print(describe([3, 1, 2]))",
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=120,
    )
    assert installed.stdout == "3 values, 1..3\n", installed.stdout + installed.stderr
