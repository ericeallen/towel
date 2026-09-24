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

"""The import model names a module only as the program's own imports do.

The model's answer proves nothing by itself: a name can look right and be a
module the installed package does not have. So each spelling case writes the
import the model proposes into the importing file, with a helper in the
providing one, and imports the importer the way the project is used -- in the
source tree with the ``sys.path`` its runner sets up, and for a representative
subset from a wheel built by ``uv build`` and installed into a fresh
environment. That import is the oracle.

The interpreter probe is replaced by one that knows only the standard
library, so that what this suite's own environment has installed (Towel's
``tests`` package among it) cannot decide a case; the real probe has tests of
its own.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import itertools
import os
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Mapping, Optional, Sequence

import pytest

import towel.import_model
from towel.consumers import ScanLimitExceeded
from towel.import_model import (
    AmbiguousName,
    FileUnderTwoNames,
    ImportModel,
    ImportSpelling,
    NameStatus,
    OutsideProvider,
    ProviderKind,
    RelativeImportEscapes,
    RelativeImportMissing,
    SpellingBasis,
    TopLevelInsidePackage,
    UnresolvedImport,
    build_import_model,
    installed_outside,
)

# -- Building projects and asking the model -----------------------------------


def _write(root: Path, files: Mapping[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
    return root


def _standard_library_only(name: str, root: Path) -> Optional[OutsideProvider]:
    """This interpreter's answer for a standard name, and nothing installed for any other."""
    if name in sys.stdlib_module_names or name in sys.builtin_module_names:
        return installed_outside(name, root)
    return None


def _model(root: Path, excluded: Sequence[Path] = ()) -> ImportModel:
    return build_import_model(root, excluded=excluded, installed=_standard_library_only)


def _spelled(model: ImportModel, importer: str, provider: str) -> Optional[str]:
    spelling = model.spelling(model.root / importer, model.root / provider)
    return None if spelling is None else spelling.module


# -- The oracle ----------------------------------------------------------------


@dataclass(frozen=True)
class _Probe:
    """A helper written into a provider, imported by an importer known as ``module``."""

    module: str
    helper: str
    marker: str


_serial = itertools.count()


def _adopt(
    root: Path, importer: str, provider: str, spelling: Optional[ImportSpelling], module: str
) -> _Probe:
    """Write the helper into ``provider`` and the model's import of it into ``importer``."""
    assert spelling is not None, f"the model found no import of {provider} from {importer}"
    helper = f"_towel_probe_{next(_serial)}"
    marker = f"{provider} for {importer}"
    with (root / provider).open("a", encoding="utf-8") as stream:
        stream.write(f"\n\ndef {helper}():\n    return {marker!r}\n")
    with (root / importer).open("a", encoding="utf-8") as stream:
        stream.write(f"\nfrom {spelling.module} import {helper}\n")
    return _Probe(module, helper, marker)


def _imports(python: str, sys_path: Sequence[Path], probes: Sequence[_Probe], cwd: Path) -> None:
    """Import each probe's module with only ``sys_path`` added, and call its borrowed helper.

    ``-I`` keeps the working directory, ``PYTHONPATH`` and user site out of
    the path, so the project is found only where ``sys_path`` says it is.
    """
    checks = "".join(
        f"assert importlib.import_module({probe.module!r}).{probe.helper}() == {probe.marker!r}\n"
        for probe in probes
    )
    code = f"import importlib, sys\nsys.path[:0] = {[str(path) for path in sys_path]!r}\n{checks}"
    result = subprocess.run(
        [python, "-I", "-B", "-c", code],
        capture_output=True,
        text=True,
        cwd=cwd,
        env={"PATH": os.environ.get("PATH", "")},
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _uv(*command: str) -> None:
    """Run uv, from its cache when it can, so the suite needs the network only once."""
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


def _installed(project: Path, scratch: Path) -> str:
    """The Python of a fresh environment holding only the wheel ``uv build`` makes of ``project``."""
    dist = scratch / "dist"
    _uv("build", "--wheel", "-q", "-o", str(dist), str(project))
    wheels = sorted(dist.glob("*.whl"))
    assert len(wheels) == 1, wheels
    environment = scratch / "environment"
    _uv("venv", "-q", "--python", sys.executable, str(environment))
    python = environment / "bin" / "python"
    _uv("pip", "install", "-q", "--python", str(python), str(wheels[0]))
    return str(python)


requires_uv = pytest.mark.skipif(shutil.which("uv") is None, reason="building a wheel needs uv")

_SETUPTOOLS = (
    '[build-system]\nrequires = ["setuptools>=61"]\nbuild-backend = "setuptools.build_meta"\n'
)


def _setuptools_project(options: str = "") -> str:
    return f'{_SETUPTOOLS}[project]\nname = "sample"\nversion = "0"\n{options}'


# -- Layouts -------------------------------------------------------------------


def _src_layout(root: Path) -> Path:
    return _write(
        root,
        {
            "pyproject.toml": _setuptools_project(
                '[tool.setuptools.packages.find]\nwhere = ["src"]\n'
            ),
            "src/alpha/__init__.py": "",
            "src/alpha/a.py": "from .b import VALUE\n",
            "src/alpha/b.py": "VALUE = 1\n",
            "tests/test_a.py": "from alpha.a import VALUE\n",
        },
    )


def test_a_src_layout_is_named_as_its_tests_import_it(tmp_path):
    project = _src_layout(tmp_path / "project")
    model = _model(project)
    assert model.names["alpha"].status is NameStatus.ATTESTED
    assert model.names["alpha"].location == project.resolve() / "src" / "alpha"
    assert model.problems == ()
    spelling = model.spelling(project / "tests/test_a.py", project / "src/alpha/b.py")
    assert spelling is not None and spelling.module == "alpha.b"
    assert spelling.basis is SpellingBasis.ATTESTED
    assert spelling.evidence is not None and spelling.evidence.line == 1
    within = model.spelling(project / "src/alpha/a.py", project / "src/alpha/b.py")
    assert within == ImportSpelling(".b", SpellingBasis.RELATIVE)
    probes = [
        _adopt(project, "tests/test_a.py", "src/alpha/b.py", spelling, "test_a"),
        _adopt(project, "src/alpha/a.py", "src/alpha/b.py", within, "alpha.a"),
    ]
    # pytest's default import mode puts a package-less test's own directory
    # first; an editable install puts ``src`` on the path.
    _imports(sys.executable, [project / "tests", project / "src"], probes, tmp_path)


@requires_uv
def test_a_src_layout_spelling_holds_in_its_wheel(tmp_path):
    project = _src_layout(tmp_path / "project")
    model = _model(project)
    probes = [
        _adopt(
            project,
            "tests/test_a.py",
            "src/alpha/b.py",
            model.spelling(project / "tests/test_a.py", project / "src/alpha/b.py"),
            "test_a",
        ),
        _adopt(
            project,
            "src/alpha/a.py",
            "src/alpha/b.py",
            model.spelling(project / "src/alpha/a.py", project / "src/alpha/b.py"),
            "alpha.a",
        ),
    ]
    python = _installed(project, tmp_path)
    _imports(python, [project / "tests"], probes, tmp_path)


def _setup_cfg_layout(root: Path) -> Path:
    return _write(
        root,
        {
            "pyproject.toml": _SETUPTOOLS,
            "setup.cfg": """
                [metadata]
                name = sample
                version = 0

                [options]
                package_dir =
                    =src
                packages = find:

                [options.packages.find]
                where = src
            """,
            "src/alpha/__init__.py": "",
            "src/alpha/a.py": "VALUE = 1\n",
            "src/alpha/b.py": "",
            "tests/test_a.py": "import alpha.a\n",
        },
    )


@requires_uv
def test_a_setup_cfg_src_layout_is_named_by_the_tests_imports(tmp_path):
    """No layout reader reads setup.cfg's ``package_dir``; the tests' ``import alpha.a`` says it."""
    project = _setup_cfg_layout(tmp_path / "project")
    model = _model(project)
    assert model.problems == ()
    spelling = model.spelling(project / "tests/test_a.py", project / "src/alpha/b.py")
    assert spelling is not None and spelling.module == "alpha.b"
    probe = _adopt(project, "tests/test_a.py", "src/alpha/b.py", spelling, "test_a")
    _imports(sys.executable, [project / "tests", project / "src"], [probe], tmp_path)
    python = _installed(project, tmp_path)
    _imports(python, [project / "tests"], [probe], tmp_path)


def _stray_src_init(root: Path) -> Path:
    return _write(
        root,
        {
            "pyproject.toml": _setuptools_project(
                '[tool.setuptools.packages.find]\nwhere = ["src"]\n'
            ),
            "src/__init__.py": "",
            "src/alpha/__init__.py": "",
            "src/alpha/a.py": "VALUE = 1\n",
            "src/alpha/b.py": "",
            "src/beta/__init__.py": "",
            "src/beta/c.py": "",
            "tests/test_a.py": "from alpha.a import VALUE\n",
        },
    )


@requires_uv
def test_a_stray_src_init_never_puts_src_in_a_name(tmp_path):
    """``src/__init__.py`` made ``src.alpha.a`` of a module the tests import as ``alpha.a``."""
    project = _stray_src_init(tmp_path / "project")
    model = _model(project)
    assert model.problems == ()
    assert model.names["alpha"].location == project.resolve() / "src" / "alpha"
    to_alpha = model.spelling(project / "tests/test_a.py", project / "src/alpha/b.py")
    assert to_alpha is not None and to_alpha.module == "alpha.b"
    # ``..beta.c`` would climb into src, which Python refuses once alpha and
    # beta are installed as the top-level packages they are.
    assert _spelled(model, "src/alpha/a.py", "src/beta/c.py") is None
    within = model.spelling(project / "src/alpha/a.py", project / "src/alpha/b.py")
    assert within is not None and within.module == ".b"
    probes = [
        _adopt(project, "tests/test_a.py", "src/alpha/b.py", to_alpha, "test_a"),
        _adopt(project, "src/alpha/a.py", "src/alpha/b.py", within, "alpha.a"),
    ]
    _imports(sys.executable, [project / "tests", project / "src"], probes, tmp_path)
    python = _installed(project, tmp_path)
    _imports(python, [project / "tests"], probes, tmp_path)


@requires_uv
def test_the_wheel_oracle_rejects_the_name_towel_used_to_write(tmp_path):
    """``src.alpha.b`` imports in the source tree from the project root and nowhere it is installed."""
    project = _stray_src_init(tmp_path / "project")
    wrong = ImportSpelling("src.alpha.b", SpellingBasis.ATTESTED)
    probe = _adopt(project, "tests/test_a.py", "src/alpha/b.py", wrong, "test_a")
    _imports(sys.executable, [project / "tests", project / "src", project], [probe], tmp_path)
    python = _installed(project, tmp_path)
    with pytest.raises(AssertionError, match="No module named 'src'"):
        _imports(python, [project / "tests"], [probe], tmp_path)


def test_a_project_directory_named_like_its_package_is_not_part_of_the_name(tmp_path):
    """From the project root, ``foo/src/foo/a.py`` was once ``foo.src.foo.a``."""
    project = _write(
        tmp_path / "foo",
        {
            "pyproject.toml": _setuptools_project(),
            "src/foo/__init__.py": "",
            "src/foo/a.py": "",
            "src/foo/b.py": "",
            "tests/test_foo.py": "import foo.a\n",
        },
    )
    model = _model(project)
    assert model.names["foo"].candidates == (project.resolve() / "src" / "foo",)
    spelling = model.spelling(project / "tests/test_foo.py", project / "src/foo/b.py")
    assert spelling is not None and spelling.module == "foo.b"
    probe = _adopt(project, "tests/test_foo.py", "src/foo/b.py", spelling, "test_foo")
    _imports(sys.executable, [project / "tests", project / "src"], [probe], tmp_path)


def _flat_layout(root: Path) -> Path:
    return _write(
        root,
        {
            "pyproject.toml": _setuptools_project('[tool.setuptools]\npackages = ["alpha"]\n'),
            "alpha/__init__.py": "",
            "alpha/a.py": "from alpha.b import VALUE\n",
            "alpha/b.py": "VALUE = 1\n",
            "tests/test_a.py": "from alpha import a\n",
        },
    )


@requires_uv
def test_a_flat_layout_is_named_from_the_project_root(tmp_path):
    project = _flat_layout(tmp_path / "project")
    model = _model(project)
    assert model.problems == ()
    spelling = model.spelling(project / "tests/test_a.py", project / "alpha/b.py")
    assert spelling is not None and spelling.module == "alpha.b"
    probe = _adopt(project, "tests/test_a.py", "alpha/b.py", spelling, "test_a")
    _imports(sys.executable, [project / "tests", project], [probe], tmp_path)
    python = _installed(project, tmp_path)
    _imports(python, [project / "tests"], [probe], tmp_path)


def test_tests_may_borrow_from_the_package_and_the_package_never_from_tests(tmp_path):
    project = _write(
        tmp_path / "project",
        {
            "alpha/__init__.py": "",
            "alpha/a.py": "",
            "alpha/b.py": "",
            "tests/__init__.py": "",
            "tests/helpers.py": "",
            "tests/test_a.py": "import alpha.a\nfrom tests.helpers import *\n",
        },
    )
    model = _model(project)
    assert model.names["tests"].trusted, "tests is a name the program uses, and it is sound"
    spelling = model.spelling(project / "tests/test_a.py", project / "alpha/b.py")
    assert spelling is not None and spelling.module == "alpha.b"
    assert _spelled(model, "alpha/a.py", "tests/helpers.py") is None
    probe = _adopt(project, "tests/test_a.py", "alpha/b.py", spelling, "tests.test_a")
    # With tests/__init__.py, pytest puts the directory above the package first.
    _imports(sys.executable, [project], [probe], tmp_path)


def test_sibling_packages_that_never_import_each_other_share_nothing(tmp_path):
    files = {
        "alpha/__init__.py": "",
        "alpha/a.py": "",
        "beta/__init__.py": "",
        "beta/b.py": "",
        "tests/test_both.py": "import alpha.a\nimport beta.b\n",
    }
    project = _write(tmp_path / "project", files)
    assert _spelled(_model(project), "alpha/a.py", "beta/b.py") is None
    # Once alpha imports beta somewhere, beta may be imported anywhere in alpha.
    _write(project, {"alpha/c.py": "import beta\n"})
    model = _model(project)
    spelling = model.spelling(project / "alpha/a.py", project / "beta/b.py")
    assert spelling is not None and spelling.module == "beta.b"
    assert spelling.evidence is not None and spelling.evidence.file.name == "c.py"
    assert _spelled(model, "beta/b.py", "alpha/a.py") is None
    probe = _adopt(project, "alpha/a.py", "beta/b.py", spelling, "alpha.a")
    _imports(sys.executable, [project], [probe], tmp_path)


def _namespace_layout(root: Path) -> Path:
    return _write(
        root,
        {
            "pyproject.toml": _setuptools_project(
                '[tool.setuptools.packages.find]\nwhere = ["src"]\n'
            ),
            "src/ns/pkg/__init__.py": "",
            "src/ns/pkg/x.py": "",
            "src/ns/pkg/y.py": "",
            "tests/test_ns.py": "from ns.pkg import x\n",
        },
    )


@requires_uv
def test_a_namespace_package_is_named_by_the_imports_that_use_it(tmp_path):
    project = _namespace_layout(tmp_path / "project")
    model = _model(project)
    assert model.problems == ()
    assert model.names["ns"].location == project.resolve() / "src" / "ns"
    spelling = model.spelling(project / "tests/test_ns.py", project / "src/ns/pkg/y.py")
    assert spelling is not None and spelling.module == "ns.pkg.y"
    within = model.spelling(project / "src/ns/pkg/x.py", project / "src/ns/pkg/y.py")
    assert within is not None and within.module == ".y"
    probes = [
        _adopt(project, "tests/test_ns.py", "src/ns/pkg/y.py", spelling, "test_ns"),
        _adopt(project, "src/ns/pkg/x.py", "src/ns/pkg/y.py", within, "ns.pkg.x"),
    ]
    _imports(sys.executable, [project / "tests", project / "src"], probes, tmp_path)
    python = _installed(project, tmp_path)
    _imports(python, [project / "tests"], probes, tmp_path)


def test_a_stale_build_copy_makes_its_name_ambiguous_until_excluded(tmp_path):
    project = _src_layout(tmp_path / "project")
    _write(project, {"build/lib/alpha/__init__.py": "", "build/lib/alpha/a.py": ""})
    model = _model(project)
    alpha = model.names["alpha"]
    assert alpha.status is NameStatus.AMBIGUOUS
    assert [path.relative_to(model.root).as_posix() for path in alpha.candidates] == [
        "build/lib/alpha",
        "src/alpha",
    ]
    assert model.problems == (AmbiguousName("alpha", alpha.candidates, None),)
    assert "build/lib/alpha" in model.problems[0].describe(model.root)
    assert _spelled(model, "tests/test_a.py", "src/alpha/b.py") is None
    assert _spelled(model, "src/alpha/a.py", "src/alpha/b.py") is None
    cleared = _model(project, excluded=[project / "build"])
    assert cleared.problems == ()
    assert _spelled(cleared, "tests/test_a.py", "src/alpha/b.py") == "alpha.b"
    by_name = build_import_model(
        project, excluded_names=["build"], installed=_standard_library_only
    )
    assert by_name.problems == ()


def test_a_vendored_duplicate_makes_its_name_ambiguous(tmp_path):
    project = _src_layout(tmp_path / "project")
    _write(
        project, {"tests/fixtures/copy/alpha/__init__.py": "", "tests/fixtures/copy/alpha/b.py": ""}
    )
    model = _model(project)
    assert model.names["alpha"].status is NameStatus.AMBIGUOUS
    assert _spelled(model, "tests/test_a.py", "src/alpha/b.py") is None


def test_an_installed_distribution_shadowing_a_project_directory(tmp_path):
    """The real probe: this interpreter imports ``packaging`` from its own site-packages."""
    installed = importlib.util.find_spec("packaging")
    assert installed is not None and installed.origin is not None
    project = _write(
        tmp_path / "project",
        {
            "packaging/__init__.py": "",
            "packaging/version.py": "",
            "packaging/tags.py": "",
            "tests/test_version.py": "import packaging.version\n",
        },
    )
    model = build_import_model(project)
    shadowed = model.names["packaging"]
    assert shadowed.status is NameStatus.AMBIGUOUS
    assert shadowed.installed == str(Path(installed.origin).resolve())
    assert isinstance(model.problems[0], AmbiguousName)
    assert _spelled(model, "tests/test_version.py", "packaging/tags.py") is None


def test_the_probe_reports_what_the_interpreter_finds_outside_the_project(tmp_path, monkeypatch):
    project = _write(tmp_path / "project", {"zz_towel_local/__init__.py": ""})
    json = installed_outside("json", project)
    assert json is not None and json.kind is ProviderKind.MODULE
    assert json.description.endswith("__init__.py")
    for unshadowable in ("sys", "abc"):  # built in, and frozen
        found = installed_outside(unshadowable, project)
        assert found is not None and found.kind is ProviderKind.UNSHADOWABLE, unshadowable
    assert installed_outside("zz_towel_nowhere", project) is None
    # The project on the path (a working directory, an editable install) is the project's own.
    monkeypatch.syspath_prepend(str(project))
    importlib.invalidate_caches()
    assert installed_outside("zz_towel_local", project) is None
    # A second copy behind it is not.
    elsewhere = _write(tmp_path / "elsewhere", {"zz_towel_local/__init__.py": ""})
    monkeypatch.setattr(sys, "path", [*sys.path, str(elsewhere)])
    importlib.invalidate_caches()
    behind = installed_outside("zz_towel_local", project)
    assert behind is not None and behind.kind is ProviderKind.MODULE
    assert behind.description.startswith(str(elsewhere.resolve()))


def test_the_probe_sets_aside_the_directory_towel_was_started_in(tmp_path, monkeypatch):
    """``python -m towel`` puts its working directory on the path; the project's runs do not."""
    project = _write(tmp_path / "project", {"zz_towel_here/__init__.py": ""})
    started = _write(tmp_path / "elsewhere", {"zz_towel_here/__init__.py": ""})
    monkeypatch.chdir(started)
    monkeypatch.setattr(sys, "path", ["", *sys.path])
    importlib.invalidate_caches()
    assert importlib.util.find_spec("zz_towel_here") is not None
    assert installed_outside("zz_towel_here", project) is None


def test_a_probe_that_fails_makes_the_name_ambiguous(tmp_path, monkeypatch):
    class Failing(importlib.machinery.PathFinder):
        @classmethod
        def find_spec(cls, fullname, path=None, target=None):
            raise RuntimeError("finder exploded")

    monkeypatch.setattr(sys, "meta_path", [Failing, *sys.meta_path])
    answer = installed_outside("zz_towel_anything", tmp_path)
    assert answer is not None and answer.kind is ProviderKind.UNKNOWN
    assert "finder exploded" in answer.description


def test_a_namespace_directory_never_displaces_a_regular_module(tmp_path):
    """Import takes a regular package or module anywhere on the path over a bare directory."""
    project = _src_layout(tmp_path / "project")
    _write(
        project,
        {
            # pytest's own layout: tests grouped in directories named like modules.
            "testing/logging/test_handlers.py": "import logging.handlers\n",
            "testing/io/test_saferepr.py": "import io\n",
            # A mirror of the package without __init__.py.
            "tests/alpha/test_core.py": "from alpha.b import VALUE\n",
            # A frozen module is found before any path entry is searched.
            "tests/data/abc.py": "import abc\n",
        },
    )
    model = _model(project)
    assert model.problems == ()
    for name in ("logging", "io", "abc"):
        assert model.names[name].status is NameStatus.EXTERNAL, name
    assert model.names["alpha"].candidates == (model.root / "src" / "alpha",)
    assert _spelled(model, "tests/alpha/test_core.py", "src/alpha/a.py") == "alpha.a"


def test_a_namespace_directory_the_imports_name_competes_with_a_regular_package(tmp_path):
    """chardet's tests import ``scripts.utils`` from ``scripts/``, beside a regular test-data copy."""
    project = _write(
        tmp_path / "project",
        {
            "scripts/utils.py": "",
            "tests/data/scripts/__init__.py": "",
            "tests/test_models.py": "from scripts.utils import collect\n",
        },
    )
    model = _model(project)
    scripts = model.names["scripts"]
    assert scripts.status is NameStatus.AMBIGUOUS
    assert [path.relative_to(model.root).as_posix() for path in scripts.candidates] == [
        "scripts",
        "tests/data/scripts",
    ]
    assert [type(problem) for problem in model.problems] == [AmbiguousName]


def test_a_project_module_named_like_a_standard_one_is_ambiguous(tmp_path):
    """``tools/json.py`` is the ``json`` a script in ``tools`` imports."""
    project = _write(tmp_path / "project", {"tools/json.py": "", "tools/run.py": "import json\n"})
    model = _model(project)
    json = model.names["json"]
    assert json.status is NameStatus.AMBIGUOUS and json.installed is not None
    assert _spelled(model, "tools/run.py", "tools/json.py") is None


def test_a_directory_named_like_a_library_it_uses_is_not_that_library(tmp_path):
    """flask's ``examples/celery`` holds none of ``celery.result``, so ``celery`` is the library."""
    project = _write(
        tmp_path / "project",
        {
            "examples/celery/src/task_app/__init__.py": "from celery import Celery\n",
            "examples/celery/src/task_app/views.py": "from celery.result import AsyncResult\n",
            "examples/celery/src/task_app/tasks.py": "",
            "examples/celery/tests/test_app.py": "from task_app import views\n",
        },
    )
    model = _model(project)
    assert model.problems == ()
    assert model.names["celery"].status is NameStatus.EXTERNAL
    assert model.names["task_app"].trusted
    assert (
        _spelled(
            model, "examples/celery/tests/test_app.py", "examples/celery/src/task_app/tasks.py"
        )
        == "task_app.tasks"
    )


def test_modules_a_package_registers_at_run_time_are_not_missing(tmp_path):
    """``six.moves`` and pytest's ``py.path`` exist in ``sys.modules``, not as files."""
    project = _write(
        tmp_path / "project",
        {
            "six.py": "import sys\nsys.modules[__name__ + '.moves'] = sys\n",
            "test_six.py": "import six\nfrom six.moves import urllib\n",
            "alpha/__init__.py": "import sys\nsys.meta_path.append(object())\n",
            "alpha/real.py": "",
            "tests/test_alpha.py": "import alpha.real\nimport alpha.virtual\n",
        },
    )
    model = _model(project)
    assert model.problems == ()
    assert _spelled(model, "test_six.py", "six.py") == "six"
    assert _spelled(model, "tests/test_alpha.py", "alpha/real.py") == "alpha.real"


def test_a_directory_imported_under_two_names_is_a_problem(tmp_path):
    project = _write(
        tmp_path / "project",
        {
            "src/alpha/__init__.py": "",
            "src/alpha/a.py": "",
            "src/alpha/b.py": "",
            "tests/test_one.py": "import src.alpha.a\n",
            "tests/test_two.py": "import alpha.a\n",
        },
    )
    model = _model(project)
    location = model.root / "src" / "alpha"
    assert FileUnderTwoNames(location, ("src.alpha", "alpha")) in model.problems
    assert model.names["alpha"].flagged and model.names["src"].flagged
    assert _spelled(model, "tests/test_two.py", "src/alpha/b.py") is None
    assert _spelled(model, "tests/test_one.py", "src/alpha/b.py") is None


def test_a_top_level_name_found_only_inside_a_used_package_is_a_problem(tmp_path):
    project = _write(
        tmp_path / "project",
        {
            "src/__init__.py": "",
            "src/alpha/__init__.py": "",
            "src/alpha/a.py": "",
            "src/alpha/b.py": "",
            "src/beta/__init__.py": "",
            "src/beta/c.py": "",
            "tools/report.py": "import src.beta.c\n",
            "tests/test_a.py": "from alpha.a import VALUE\n",
        },
    )
    model = _model(project)
    kinds = {type(problem) for problem in model.problems}
    assert TopLevelInsidePackage in kinds and FileUnderTwoNames in kinds
    assert _spelled(model, "tests/test_a.py", "src/alpha/b.py") is None
    # src/alpha may be imported as alpha, so nothing above it is its package;
    # and alpha is flagged, so nothing inside it is a provider either.
    assert _spelled(model, "src/alpha/a.py", "src/beta/c.py") is None
    assert _spelled(model, "src/alpha/a.py", "src/alpha/b.py") is None
    assert model.names["alpha"].flagged and not model.names["alpha"].trusted


def test_relative_imports_that_climb_out_or_name_nothing_are_problems(tmp_path):
    project = _write(
        tmp_path / "project",
        {
            "api/__init__.py": "",
            "api/c.py": "",
            "utils/__init__.py": "",
            "utils/v.py": "from ..api import c\n",
            "alpha/__init__.py": "",
            "alpha/a.py": "from .missing import x\n",
            "alpha/b.py": "try:\n    from ._speedups import fast\nexcept ImportError:\n    fast = None\n",
            "alpha/c.py": "",
            "alpha/_native.pyi": "",
            "alpha/d.py": "from ._native import handle\n",
            "tests/helpers.py": "",
            "tests/test_a.py": "import alpha.c\nimport utils.v\nfrom . import helpers\n",
        },
    )
    model = _model(project)
    escapes = [problem for problem in model.problems if isinstance(problem, RelativeImportEscapes)]
    missing = [problem for problem in model.problems if isinstance(problem, RelativeImportMissing)]
    assert [(problem.site.file.name, problem.site.line) for problem in escapes] == [("v.py", 1)]
    assert [(problem.site.file.name, problem.missing) for problem in missing] == [
        ("a.py", ".missing")
    ]
    # A guarded import expects to fail, a stub proves a compiled module, and a
    # relative import in a bare directory can hold under a namespace-importing runner.
    assert len(model.problems) == 2
    # Where the climb out of utils holds, v.py is part of a larger package,
    # so utils may not be its name. A module that does not exist says
    # nothing about where alpha is; only the file naming it is in doubt.
    assert model.names["utils"].flagged and model.names["alpha"].trusted
    assert _spelled(model, "tests/test_a.py", "alpha/c.py") == "alpha.c"
    assert _spelled(model, "tests/test_a.py", "alpha/a.py") is None
    assert _spelled(model, "tests/test_a.py", "utils/v.py") is None
    assert escapes[0].name == "utils" and escapes[0].names_in_doubt == {"utils"}
    assert missing[0].names_in_doubt == frozenset()


def test_an_import_its_package_does_not_hold_is_a_problem(tmp_path):
    project = _write(
        tmp_path / "project",
        {
            "alpha/__init__.py": "",
            "alpha/a.py": "",
            "tests/test_a.py": "import alpha.a\nfrom alpha.gone import thing\nfrom alpha import attribute\n",
        },
    )
    model = _model(project)
    (problem,) = model.problems
    assert isinstance(problem, UnresolvedImport)
    assert (problem.missing, problem.site.line) == ("alpha.gone", 2)
    assert "tests/test_a.py:2" in problem.describe(model.root)
    # It names a module, not a place: alpha is where its other imports say.
    assert model.names["alpha"].trusted
    assert _spelled(model, "tests/test_a.py", "alpha/a.py") == "alpha.a"
    assert problem.names_in_doubt == frozenset()


def test_the_file_making_an_unresolved_import_is_never_a_provider(tmp_path):
    project = _write(
        tmp_path / "project",
        {
            "alpha/__init__.py": "",
            "alpha/a.py": "",
            "alpha/c.py": "from alpha.gone import thing\n",
            "beta/__init__.py": "from beta._version import version\n",
            "beta/b.py": "",
            "tests/test_a.py": "import alpha.a\nimport beta.b\n",
        },
    )
    model = _model(project)
    assert all(isinstance(problem, UnresolvedImport) for problem in model.problems)
    assert model.names["alpha"].trusted and model.names["beta"].trusted
    assert _spelled(model, "tests/test_a.py", "alpha/c.py") is None
    assert _spelled(model, "tests/test_a.py", "alpha/a.py") == "alpha.a"
    # Every import of beta.b runs the initializer, whose import the tree lacks.
    assert _spelled(model, "tests/test_a.py", "beta/b.py") is None
    assert {path.name for problem in model.problems for path in problem.found_at} == {
        "c.py",
        "__init__.py",
    }


def test_what_is_missing_below_a_module_file_is_the_module_under_it(tmp_path):
    """``alpha/util.py`` holds no submodules: ``alpha.util.gone`` is missing, and ``alpha.util`` is not."""
    project = _write(
        tmp_path / "project",
        {
            "alpha/__init__.py": "",
            "alpha/util.py": "",
            "alpha/compat.py": "import sys\nsys.modules[__name__ + '.moves'] = sys\n",
            "alpha/a.py": "from .util.gone import thing\n",
            "tests/test_a.py": "import alpha.util\nimport alpha.util.gone\nimport alpha.compat.moves\n",
        },
    )
    model = _model(project)
    described = sorted(problem.describe(model.root) for problem in model.problems)
    assert described == [
        "alpha/a.py:1: from .util.gone import thing names .util.gone, which does not exist",
        "tests/test_a.py:2: import alpha.util.gone needs alpha.util.gone, which alpha does not hold",
    ]
    assert _spelled(model, "tests/test_a.py", "alpha/util.py") == "alpha.util"


def _sphinx_shape(root: Path) -> Path:
    """sphinx's shape: a package importing itself absolutely, and test data importing what it lacks."""
    return _write(
        root,
        {
            "pyproject.toml": _setuptools_project('[tool.setuptools]\npackages = ["sphx"]\n'),
            "sphx/__init__.py": "",
            "sphx/util.py": "VALUE = 1\n",
            "sphx/a.py": "from sphx.util import VALUE\n",
            "sphx/b.py": "from sphx.util import VALUE\n",
            "tests/test_a.py": "import sphx.a\n",
            "tests/roots/test-ext-autodoc/target/__init__.py": "",
            "tests/roots/test-ext-autodoc/target/need_mocks.py": (
                "import missing_module\n"
                "import sphx.missing_module4\n"
                "from sphx.missing_module4 import missing_name2\n"
            ),
        },
    )


def test_test_data_importing_what_its_package_lacks_leaves_the_package_trusted(tmp_path):
    """sphinx's tests mock ``sphinx.missing_module4``; every other import still places ``sphinx``."""
    project = _sphinx_shape(tmp_path / "project")
    model = _model(project)
    unresolved = [problem for problem in model.problems if isinstance(problem, UnresolvedImport)]
    assert len(unresolved) == len(model.problems) == 2
    assert {problem.missing for problem in unresolved} == {"sphx.missing_module4"}
    assert model.names["sphx"].trusted
    within = model.spelling(project / "sphx/b.py", project / "sphx/a.py")
    assert within is not None and within.module == "sphx.a"
    assert within.basis is SpellingBasis.OWN_PACKAGE
    across = model.spelling(project / "tests/test_a.py", project / "sphx/b.py")
    assert across is not None and across.module == "sphx.b"
    # The test data is never a provider, and no file is the missing module.
    data = "tests/roots/test-ext-autodoc/target"
    assert _spelled(model, f"{data}/__init__.py", f"{data}/need_mocks.py") is None
    named = {model.module_name(path) for path in project.rglob("*.py")} - {None}
    assert named == {"sphx", "sphx.util", "sphx.a", "sphx.b"}
    probes = [
        _adopt(project, "sphx/b.py", "sphx/a.py", within, "sphx.b"),
        _adopt(project, "tests/test_a.py", "sphx/b.py", across, "test_a"),
    ]
    _imports(sys.executable, [project / "tests", project], probes, tmp_path)


def test_a_script_directory_borrows_only_what_its_scripts_import(tmp_path):
    project = _write(
        tmp_path / "project",
        {"scripts/a.py": "import b\n", "scripts/b.py": "", "scripts/c.py": "import os\n"},
    )
    model = _model(project)
    spelling = model.spelling(project / "scripts/a.py", project / "scripts/b.py")
    assert spelling is not None and spelling.module == "b"
    assert _spelled(model, "scripts/c.py", "scripts/b.py") is None
    assert _spelled(model, "scripts/a.py", "scripts/c.py") is None
    probe = _adopt(project, "scripts/a.py", "scripts/b.py", spelling, "a")
    # ``python scripts/a.py`` puts the script's own directory first.
    _imports(sys.executable, [project / "scripts"], [probe], tmp_path)


def test_a_package_that_imports_itself_absolutely_keeps_that_spelling(tmp_path):
    project = _write(
        tmp_path / "project",
        {
            "sphx/__init__.py": "",
            "sphx/a.py": "from sphx.util import VALUE\n",
            "sphx/b.py": "",
            "sphx/util.py": "VALUE = 1\n",
            "sphx/mixed.py": "from sphx.util import VALUE\nfrom . import b\n",
        },
    )
    model = _model(project)
    spelling = model.spelling(project / "sphx/a.py", project / "sphx/b.py")
    assert spelling is not None and spelling.module == "sphx.b"
    assert spelling.basis is SpellingBasis.OWN_PACKAGE
    assert _spelled(model, "sphx/mixed.py", "sphx/util.py") == ".util"
    probe = _adopt(project, "sphx/a.py", "sphx/b.py", spelling, "sphx.a")
    _imports(sys.executable, [project], [probe], tmp_path)


def test_a_package_that_imports_itself_relatively_gets_relative_imports(tmp_path):
    project = _write(
        tmp_path / "project",
        {
            "alpha/__init__.py": "from .util import VALUE\nfrom .sub import x\n",
            "alpha/util.py": "VALUE = 1\n",
            "alpha/b.py": "",
            "alpha/sub/__init__.py": "",
            "alpha/sub/x.py": "from ..util import VALUE\n",
            "alpha/sub/y.py": "",
        },
    )
    model = _model(project)
    upward = model.spelling(project / "alpha/sub/x.py", project / "alpha/b.py")
    downward = model.spelling(project / "alpha/b.py", project / "alpha/sub/y.py")
    to_package = model.spelling(project / "alpha/sub/x.py", project / "alpha/__init__.py")
    assert upward == ImportSpelling("..b", SpellingBasis.RELATIVE)
    assert downward is not None and downward.module == ".sub.y"
    assert to_package is not None and to_package.module == ".."
    probes = [
        _adopt(project, "alpha/sub/x.py", "alpha/b.py", upward, "alpha.sub.x"),
        _adopt(project, "alpha/b.py", "alpha/sub/y.py", downward, "alpha.b"),
    ]
    _imports(sys.executable, [project], probes, tmp_path)


def _library_with_its_tests_inside(root: Path) -> Path:
    """beautifulsoup4's shape: ``bs4/tests`` is inside the package and outside the wheel."""
    return _write(
        root,
        {
            "pyproject.toml": (
                '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n'
                '[project]\nname = "sample"\nversion = "0"\n'
                '[tool.hatch.build.targets.wheel]\npackages = ["alpha"]\nexclude = ["alpha/tests/"]\n'
            ),
            "alpha/__init__.py": "from .core import VALUE\n",
            "alpha/core.py": "VALUE = 1\n",
            "alpha/other.py": "",
            "alpha/tests/__init__.py": "",
            "alpha/tests/helpers.py": "",
            "alpha/tests/test_core.py": "from ..core import VALUE\nfrom . import helpers\n",
        },
    )


def test_a_library_never_borrows_from_the_tests_inside_its_package(tmp_path):
    project = _library_with_its_tests_inside(tmp_path / "project")
    model = _model(project)
    assert _spelled(model, "alpha/other.py", "alpha/tests/helpers.py") is None
    assert _spelled(model, "alpha/__init__.py", "alpha/tests/test_core.py") is None
    assert _spelled(model, "alpha/tests/test_core.py", "alpha/tests/helpers.py") == ".helpers"
    to_library = model.spelling(project / "alpha/tests/test_core.py", project / "alpha/other.py")
    assert to_library is not None and to_library.module == "..other"
    # A script outside the package importing its tests shows nothing about what alpha ships.
    _write(project, {"docs/conf.py": "from alpha.tests import helpers\n"})
    assert _spelled(_model(project), "alpha/other.py", "alpha/tests/helpers.py") is None
    probe = _adopt(
        project, "alpha/tests/test_core.py", "alpha/other.py", to_library, "alpha.tests.test_core"
    )
    _imports(sys.executable, [project], [probe], tmp_path)


@requires_uv
def test_the_wheel_oracle_rejects_a_library_import_of_its_own_tests(tmp_path):
    """The relative import the model declines resolves in the tree and nowhere installed."""
    project = _library_with_its_tests_inside(tmp_path / "project")
    wrong = ImportSpelling(".tests.helpers", SpellingBasis.RELATIVE)
    probe = _adopt(project, "alpha/other.py", "alpha/tests/helpers.py", wrong, "alpha.other")
    _imports(sys.executable, [project], [probe], tmp_path)
    python = _installed(project, tmp_path)
    with pytest.raises(AssertionError, match="No module named 'alpha.tests'"):
        _imports(python, [], [probe], tmp_path)


def test_a_stray_tests_init_lets_modules_share_only_their_own_directory(tmp_path):
    """Nothing shows ``tests`` runs as a package above ``tests/unit``, so ``..helpers`` is unproven."""
    files = {
        "tests/__init__.py": "",
        "tests/helpers.py": "",
        "tests/test_top.py": "import os\n",
        "tests/unit/__init__.py": "",
        "tests/unit/test_x.py": "import os\n",
    }
    project = _write(tmp_path / "project", files)
    model = _model(project)
    assert _spelled(model, "tests/test_top.py", "tests/helpers.py") == ".helpers"
    assert _spelled(model, "tests/unit/test_x.py", "tests/helpers.py") is None
    _write(project, {"tests/unit/test_y.py": "from .. import helpers\n"})
    assert _spelled(_model(project), "tests/unit/test_x.py", "tests/helpers.py") == "..helpers"


def test_imports_that_need_not_run_attest_nothing(tmp_path):
    project = _write(
        tmp_path / "project",
        {
            "alpha/__init__.py": "",
            "alpha/checked.py": "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import beta\n",
            "alpha/guarded.py": "try:\n    import beta\nexcept ImportError:\n    beta = None\n",
            "beta/__init__.py": "",
            "beta/b.py": "",
            "tests/test_b.py": "import beta.b\n",
            "scripts/run.py": "import sys\nsys.path.insert(0, 'somewhere')\nimport beta\n",
        },
    )
    model = _model(project)
    assert model.attested_by(project / "alpha") == frozenset()
    assert _spelled(model, "alpha/checked.py", "beta/b.py") is None
    assert _spelled(model, "alpha/guarded.py", "beta/b.py") is None
    assert _spelled(model, "scripts/run.py", "beta/b.py") is None
    assert _spelled(model, "tests/test_b.py", "beta/b.py") is not None


def test_a_plain_directory_inside_a_package_is_never_a_provider(tmp_path):
    """setuptools' ``find_packages`` leaves ``alpha/data`` out of the wheel."""
    project = _write(
        tmp_path / "project",
        {
            "alpha/__init__.py": "",
            "alpha/a.py": "from . import data\n",
            "alpha/data/gen.py": "",
            "tests/test_a.py": "import alpha.a\n",
        },
    )
    model = _model(project)
    assert _spelled(model, "tests/test_a.py", "alpha/data/gen.py") is None
    assert _spelled(model, "alpha/a.py", "alpha/data/gen.py") is None
    assert model.module_name(project / "alpha/data/gen.py") is None
    assert model.module_name(project / "alpha/a.py") == "alpha.a"


def test_files_that_do_not_parse_neither_import_nor_are_imported(tmp_path):
    project = _write(
        tmp_path / "project",
        {
            "alpha/__init__.py": "",
            "alpha/broken.py": "def (:\n",
            "alpha/fine.py": "",
            "tests/test_a.py": "import alpha.fine\n",
        },
    )
    model = _model(project)
    assert _spelled(model, "tests/test_a.py", "alpha/broken.py") is None
    assert _spelled(model, "alpha/broken.py", "alpha/fine.py") is None
    assert model.imports_of(project / "alpha/broken.py") == ()
    assert _spelled(model, "tests/test_a.py", "alpha/fine.py") == "alpha.fine"


def test_what_an_import_reaches_and_what_a_qualified_name_denotes(tmp_path):
    project = _write(
        tmp_path / "project",
        {
            "alpha/__init__.py": "",
            "alpha/sub/__init__.py": "",
            "alpha/sub/m.py": "class Outer:\n    class Inner: ...\n",
            "tests/test_a.py": "import alpha.sub.m\nimport json\n",
        },
    )
    model = _model(project)
    root = model.root
    test = project / "tests/test_a.py"
    assert model.files_reached(test, 0, "alpha.sub.m") == {
        root / "alpha/__init__.py",
        root / "alpha/sub/__init__.py",
        root / "alpha/sub/m.py",
    }
    assert model.files_reached(test, 0, "json") == frozenset()
    assert model.files_reached(project / "alpha/sub/m.py", 2, None, ["sub"]) == {
        root / "alpha/__init__.py",
        root / "alpha/sub/__init__.py",
    }
    assert model.split_qualified("alpha.sub.m.Outer.Inner") == (
        root / "alpha/sub/m.py",
        "alpha.sub.m",
        "Outer.Inner",
    )
    assert model.split_qualified("json.JSONDecoder") is None
    assert model.module_name(project / "alpha/sub/__init__.py") == "alpha.sub"
    assert model.context_of(test) == root / "tests/test_a.py"
    assert model.context_of(project / "alpha/sub/m.py") == root / "alpha"
    assert model.attested_by(test) == {"alpha"}
    assert [site.module for site in model.imports_of(test)] == ["alpha.sub.m", "json"]


def test_environments_and_links_are_not_project_source(tmp_path):
    project = _src_layout(tmp_path / "project")
    _write(
        project,
        {
            "env/pyvenv.cfg": "home = /usr\n",
            "env/lib/python3.13/site-packages/alpha/__init__.py": "",
            ".tox/py/lib/site-packages/alpha/__init__.py": "",
            "conda/conda-meta/history": "",
            "conda/lib/site-packages/alpha/__init__.py": "",
        },
    )
    (project / "alpha").symlink_to(project / "src" / "alpha", target_is_directory=True)
    model = _model(project)
    assert model.names["alpha"].candidates == (project.resolve() / "src" / "alpha",)
    assert model.problems == ()
    outside = _write(tmp_path / "outside", {"alpha/__init__.py": ""})
    (project / "vendor").mkdir()
    (project / "vendor" / "alpha").symlink_to(outside / "alpha", target_is_directory=True)
    assert _model(project).names["alpha"].status is NameStatus.AMBIGUOUS


def test_the_scan_stops_rather_than_model_part_of_a_tree(tmp_path, monkeypatch):
    project = _write(tmp_path / "project", {"a.py": "", "b.py": "", "c.py": ""})
    monkeypatch.setattr(towel.import_model, "MAXIMUM_FILES", 2)
    with pytest.raises(ScanLimitExceeded):
        _model(project)


def test_future_and_main_are_never_project_modules(tmp_path):
    project = _write(
        tmp_path / "project",
        {
            "__future__.py": "",
            "__main__.py": "",
            "a.py": "from __future__ import annotations\nimport __main__\n",
        },
    )
    assert _model(project).names == {}


def _sites(source: str, tmp_path: Path) -> Iterator[tuple[str, bool, bool, bool]]:
    project = _write(tmp_path / "project", {"m.py": source})
    for site in _model(project).imports_of(project / "m.py"):
        yield site.statement(), site.runtime, site.guarded, site.deferred


def test_each_import_records_when_it_runs(tmp_path):
    source = """
        import a, b.c
        from contextlib import suppress
        import typing
        if typing.TYPE_CHECKING:
            from d import e
        else:
            import f
        if not TYPE_CHECKING:
            import g
        if TYPE_CHECKING and sys.version_info >= (3, 12):
            import m
        try:
            import h
        except (ValueError, ModuleNotFoundError):
            import i
        with suppress(ImportError):
            import j
        def later():
            from . import k
        class Body:
            import l
    """
    found: List[tuple[str, bool, bool, bool]] = list(_sites(source, tmp_path))
    assert found == [
        ("import a", True, False, False),
        ("import b.c", True, False, False),
        ("from contextlib import suppress", True, False, False),
        ("import typing", True, False, False),
        ("from d import e", False, False, False),
        ("import f", True, False, False),
        ("import g", True, False, False),
        ("import m", False, False, False),
        ("import h", True, True, False),
        ("import i", True, False, False),
        ("import j", True, True, False),
        ("from . import k", True, False, True),
        ("import l", True, False, False),
    ]
