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

"""An environment inside the project root is outside the project.

The third audit's D3: run from the project's own ``.venv``, as the docs
advise, the interpreter probe set aside every ``sys.path`` entry inside the
root, the ``.venv``'s site-packages among them. A library installed there was
invisible, so a project directory named like it was taken for it, and a helper
imported from it failed at run time; the project installed there without
``-e`` was invisible too, and its tests then imported helpers the installed
copy lacks. What an environment holds is a distribution, wherever it sits
(``towel.import_model._in_installation``). The unit tests build fake
environments under a temporary root; one end-to-end run uses a real ``.venv``.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import textwrap
from typing import Callable, Mapping

import pytest

import towel
from towel.cli import INSTALLED_COPY_REMEDY
from towel.import_model import (
    AmbiguousName,
    Doubt,
    NameStatus,
    ProviderKind,
    build_import_model,
    installed_outside,
)


def _write(root: Path, files: Mapping[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
    return root


PutFirst = Callable[..., None]


@pytest.fixture
def put_first(monkeypatch: pytest.MonkeyPatch) -> PutFirst:
    """Put directories first on ``sys.path``, as the interpreter Towel runs in would have them."""
    original = list(sys.path)

    def put(*entries: Path) -> None:
        monkeypatch.setattr(sys, "path", [str(entry) for entry in entries] + original)
        importlib.invalidate_caches()

    return put


def _site_packages(environment: Path) -> Path:
    return (
        environment
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )


@pytest.mark.parametrize("marker", ["pyvenv.cfg", "conda-meta"], ids=["venv", "conda"])
def test_what_an_environment_inside_the_root_holds_is_outside_the_project(
    tmp_path: Path, put_first: PutFirst, marker: str
) -> None:
    root = tmp_path / "project"
    environment = root / ".venv"
    site = _site_packages(environment)
    _write(site, {"zz_env_lib/__init__.py": ""})
    marked = environment / marker
    marked.mkdir(parents=True) if marker == "conda-meta" else marked.write_text("home = /x\n")
    put_first(site)
    found = installed_outside("zz_env_lib", root.resolve())
    assert found is not None and found.kind is ProviderKind.MODULE
    assert found.description == str((site / "zz_env_lib" / "__init__.py").resolve())


def test_a_site_packages_directory_inside_the_root_is_outside_the_project(
    tmp_path: Path, put_first: PutFirst
) -> None:
    """No marker file: the directory's name alone says what it is, as the tree walk reads it."""
    root = tmp_path / "project"
    site = _write(root / "vendor" / "site-packages", {"zz_vendored/__init__.py": ""})
    put_first(site)
    found = installed_outside("zz_vendored", root.resolve())
    assert found is not None and found.kind is ProviderKind.MODULE


def test_the_projects_own_source_on_the_path_is_still_the_project(
    tmp_path: Path, put_first: PutFirst
) -> None:
    """An editable install puts ``src`` on the path; that is the project, even in its own venv's root."""
    root = _write(tmp_path / "project", {"pyvenv.cfg": "home = /x\n", "src/zz_own/__init__.py": ""})
    site = _write(_site_packages(root), {"zz_env_only/__init__.py": ""})
    put_first(root / "src", site)
    assert installed_outside("zz_own", root.resolve()) is None
    # A project kept in its environment's own directory: what that environment installs is outside.
    found = installed_outside("zz_env_only", root.resolve())
    assert found is not None and found.kind is ProviderKind.MODULE


def test_a_library_in_the_projects_venv_is_not_the_projects_namesake(
    tmp_path: Path, put_first: PutFirst
) -> None:
    """The auditor's first shape: ``zz_shadow/`` holds only ``a.py``; the venv's library also has ``b``."""
    root = _write(
        tmp_path / "project",
        {
            "pyproject.toml": '[project]\nname = "app"\nversion = "0"\n',
            "zz_shadow/a.py": "def thing():\n    return 'project'\n",
            "app/__init__.py": "",
            "app/x.py": "from zz_shadow.b import other\n",
            "app/y.py": "from zz_shadow.a import thing\n",
            ".venv/pyvenv.cfg": "home = /x\n",
        },
    )
    site = _write(
        _site_packages(root / ".venv"),
        {"zz_shadow/__init__.py": "", "zz_shadow/a.py": "", "zz_shadow/b.py": ""},
    )
    put_first(site)
    model = build_import_model(root, installed=installed_outside)
    shadow = model.names["zz_shadow"]
    assert shadow.status is NameStatus.EXTERNAL
    assert shadow.installed == str((site / "zz_shadow" / "__init__.py").resolve())
    resolved = root.resolve()
    assert model.spelling(resolved / "app/y.py", resolved / "zz_shadow/a.py") is None


def test_the_project_installed_in_its_venv_makes_its_name_ambiguous(
    tmp_path: Path, put_first: PutFirst
) -> None:
    """The auditor's second shape: the project installed without ``-e`` in ``.venv``."""
    root = _write(
        tmp_path / "project",
        {
            "src/zz_shop/__init__.py": "",
            "src/zz_shop/util.py": "",
            "tests/test_shop.py": "from zz_shop.util import describe\n",
            ".venv/pyvenv.cfg": "home = /x\n",
        },
    )
    site = _write(
        _site_packages(root / ".venv"), {"zz_shop/__init__.py": "", "zz_shop/util.py": ""}
    )
    put_first(site)
    model = build_import_model(root, installed=installed_outside)
    assert model.names["zz_shop"].status is NameStatus.AMBIGUOUS
    (problem,) = model.problems
    assert isinstance(problem, AmbiguousName) and problem.doubts == {Doubt.INSTALLED}
    assert ".venv" in problem.describe(model.root)


# -- The auditor's run, from a real .venv inside the root -------------------------------------


def _venv_with_towel(environment: Path) -> Path:
    """A fresh venv whose interpreter imports the Towel under test, and nothing of this one's."""
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(environment)],
        check=True,
        capture_output=True,
        timeout=120,
    )
    source = Path(towel.__file__).resolve().parents[1]
    # The Towel under test, and where its metadata is: its source tree or this interpreter's site.
    places = [str(source), sysconfig.get_paths()["purelib"]]
    (_site_packages(environment) / "towel_under_test.pth").write_text("\n".join(places) + "\n")
    return environment / "bin" / "python"


def test_the_audited_run_from_the_projects_venv_leaves_the_program_working(tmp_path: Path) -> None:
    root = _write(
        tmp_path / "project",
        {
            "pyproject.toml": '[project]\nname = "app"\nversion = "0"\n',
            "main.py": "from app.y import report\nfrom app.x import show\nprint(report([1, 2, 3]))\nprint(show())\n",
            "app/__init__.py": "",
            "app/x.py": "from zz_shadow.b import other\n\n\ndef show():\n    return other()\n",
            "app/y.py": """
                from zz_shadow.a import thing


                def report(items):
                    total = 0
                    for value in items:
                        total += value * value
                    label = "sum of squares"
                    return f"{label}: {total} ({thing()})"
            """,
            "zz_shadow/a.py": """
                def thing():
                    return "project a.thing"


                def summarize(values):
                    total = 0
                    for value in values:
                        total += value * value
                    label = "sum of squares"
                    return f"{label}: {total}"
            """,
        },
    )
    python = _venv_with_towel(root / ".venv")
    _write(
        _site_packages(root / ".venv"),
        {
            "zz_shadow/__init__.py": "",
            "zz_shadow/a.py": "def thing():\n    return 'library a.thing'\n",
            "zz_shadow/b.py": "def other():\n    return 'library b.other'\n",
        },
    )
    clean = {key: value for key, value in os.environ.items() if not key.startswith("PYTHON")}

    def program() -> str:
        result = subprocess.run(
            [str(python), "-B", "main.py"], cwd=root, capture_output=True, text=True, env=clean
        )
        return result.stdout + result.stderr

    before = program()
    assert before == "sum of squares: 14 (library a.thing)\nlibrary b.other\n", before
    ran = subprocess.run(
        [
            str(python),
            "-m",
            "towel.cli",
            "dry",
            ".",
            ".",
            "--no-interactive",
            "--progress",
            "none",
            "--cross-module",
            "--no-types",
            "--no-format",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        env=dict(clean, PYTHONDONTWRITEBYTECODE="1"),
        timeout=600,
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "import __extracted_func" not in (root / "app/y.py").read_text()
    assert program() == before
    # The same venv holding the project itself, installed without -e, refuses the run.
    _write(_site_packages(root / ".venv"), {"app/__init__.py": "", "app/x.py": "", "app/y.py": ""})
    refused = subprocess.run(
        ran.args,
        cwd=root,
        capture_output=True,
        text=True,
        env=dict(clean, PYTHONDONTWRITEBYTECODE="1"),
        timeout=600,
    )
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "app could be any of: app; outside the project (" in refused.stderr
    assert INSTALLED_COPY_REMEDY in refused.stderr
