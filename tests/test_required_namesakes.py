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

"""A directory named like a distribution the project requires is not taken for it.

The third audit's P1-2: ``app`` requires ``click>=8``, and the tree also holds
a ``click/`` directory sharing one module, ``utils.py``, with the library. Run
where the interpreter lacks click, as ``uvx`` runs it, Towel took ``click/`` for
the name, hosted a helper in ``click/utils.py`` and made ``app/core.py`` import
it; installed as it ships, with the real click, ``app.core`` raised
ImportError. A requirement the project declares, anywhere Towel reads one,
now puts the name in doubt (docs/DECISIONS.md, "An import problem refuses only
when it leaves a name in doubt"). These tests read small fragments of every
form, and one end-to-end run checks the auditor's layout against the program
as it is installed.
"""

from __future__ import annotations

import itertools
import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap
from typing import Mapping, Optional

import pytest

import towel
from towel.cli import (
    INSTALLED_COPY_REMEDY,
    LACKING_MODULE_REMEDY,
    REQUIRED_DISTRIBUTION_REMEDY,
    STRAY_COPY_REMEDY,
    _import_problem_remedies,
)
from towel.declared_requirements import (
    declared_requirements,
    installed_with_the_project,
    normalized_name,
)
from towel.import_model import (
    AmbiguousName,
    Doubt,
    FileUnderTwoNames,
    ImportSite,
    NameStatus,
    OutsideProvider,
    RelativeImportEscapes,
    TopLevelInsidePackage,
    UnresolvedImport,
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
    """This interpreter's answer for a standard name, and nothing installed for any other."""
    if name in sys.stdlib_module_names or name in sys.builtin_module_names:
        return installed_outside(name, root)
    return None


_NAMESAKE = {
    "zzapp/__init__.py": "",
    "zzapp/core.py": "from zz_click.utils import echo\n",
    "zz_click/__init__.py": "",
    "zz_click/utils.py": "def echo(message):\n    print(message)\n",
}
"""The auditor's layout under names no interpreter has: ``zz_click`` shares ``utils`` with a library."""


def _status(root: Path, files: Mapping[str, str]) -> tuple[NameStatus, Optional[str]]:
    _write(root, {**_NAMESAKE, **files})
    info = build_import_model(root, installed=_standard_library_only).names["zz_click"]
    return info.status, info.required


# -- Every form a requirement is declared in ------------------------------------

_PROJECT = '[project]\nname = "zzapp"\n'


@pytest.mark.parametrize(
    "files, source",
    [
        (
            {"pyproject.toml": _PROJECT + 'dependencies = ["ZZ-Click>=8"]\n'},
            "pyproject.toml [project].dependencies",
        ),
        (
            {
                "pyproject.toml": _PROJECT + "[project.optional-dependencies]\n"
                "cli = [\"zz.click[extra]; python_version > '3'\"]\n"
            },
            "pyproject.toml [project.optional-dependencies].cli",
        ),
        (
            {"pyproject.toml": '[dependency-groups]\ndev = [{include-group = "x"}, "zz_click"]\n'},
            "pyproject.toml [dependency-groups].dev",
        ),
        (
            {
                "pyproject.toml": '[tool.poetry]\nname = "zzapp"\n[tool.poetry.dependencies]\nzz-click = "^8"\n'
            },
            "pyproject.toml [tool.poetry.dependencies]",
        ),
        (
            {
                "pyproject.toml": '[tool.poetry.dependencies]\nzz-click = {version = "^8", optional = true}\n'
            },
            "pyproject.toml [tool.poetry.dependencies]",
        ),
        (
            {"pyproject.toml": '[tool.poetry.group.test.dependencies]\n"Zz.Click" = "*"\n'},
            "pyproject.toml [tool.poetry.group.test.dependencies]",
        ),
        (
            {"pyproject.toml": '[tool.poetry.dev-dependencies]\nzz-click = "*"\n'},
            "pyproject.toml [tool.poetry.dev-dependencies]",
        ),
        (
            {"setup.cfg": "[options]\ninstall_requires =\n    zz-click>=8  # the CLI\n"},
            "setup.cfg [options] install_requires",
        ),
        (
            {"setup.cfg": "[options.extras_require]\ncli =\n    zz-click\n"},
            "setup.cfg [options.extras_require] cli",
        ),
        (
            {"requirements.txt": "# pinned\nzz-click==8.1 \\\n    --hash=sha256:00\n"},
            "requirements.txt",
        ),
        (
            {
                "requirements-dev.txt": "-r requirements/base.in\n",
                "requirements/base.in": "zz-click\n",
            },
            "requirements/base.in",
        ),
        (
            {"requirements/test.txt": "-e git+https://example.invalid/zz.git#egg=zz-click\n"},
            "requirements/test.txt",
        ),
        ({"uv.lock": 'version = 1\n[[package]]\nname = "zz-click"\nversion = "8"\n'}, "uv.lock"),
        ({"poetry.lock": '[[package]]\nname = "zz-click"\nversion = "8"\n'}, "poetry.lock"),
        ({"pdm.lock": '[[package]]\nname = "zz-click"\nversion = "8"\n'}, "pdm.lock"),
        (
            {"Pipfile.lock": '{"default": {"zz-click": {"version": "==8"}}, "develop": {}}'},
            "Pipfile.lock",
        ),
        # Round-4 audit P1-3: declarations of an environment's tools, which were not read.
        (
            {"pyproject.toml": '[tool.hatch.envs.default]\ndependencies = ["zz-click>=8"]\n'},
            "pyproject.toml [tool.hatch.envs.default].dependencies",
        ),
        (
            {"hatch.toml": '[envs.test]\nextra-dependencies = ["zz-click"]\n'},
            "hatch.toml [envs.test].extra-dependencies",
        ),
        (
            {"pyproject.toml": '[tool.uv]\ndev-dependencies = ["zz-click>=8"]\n'},
            "pyproject.toml [tool.uv].dev-dependencies",
        ),
        (
            {"pyproject.toml": '[tool.pdm.dev-dependencies]\ntest = ["zz-click"]\n'},
            "pyproject.toml [tool.pdm.dev-dependencies].test",
        ),
        ({"Pipfile": '[dev-packages]\nzz-click = "*"\n'}, "Pipfile [dev-packages]"),
        ({"dev-requirements.txt": "zz-click>=8\npytest\n"}, "dev-requirements.txt"),
        ({"test_requirements.txt": "zz-click\n"}, "test_requirements.txt"),
        ({"requirements.in": "zz-click\n"}, "requirements.in"),
        ({"requirements/dev.in": "zz-click\n"}, "requirements/dev.in"),
    ],
    ids=[
        "pep621",
        "pep621-extra",
        "dependency-group",
        "poetry",
        "poetry-optional",
        "poetry-group",
        "poetry-dev",
        "setup-cfg",
        "setup-cfg-extra",
        "requirements",
        "requirements-include",
        "requirements-egg",
        "uv-lock",
        "poetry-lock",
        "pdm-lock",
        "pipfile-lock",
        "hatch-env",
        "hatch-toml",
        "uv-dev",
        "pdm-dev",
        "pipfile",
        "dev-requirements",
        "underscore-requirements",
        "requirements-in",
        "requirements-dir-in",
    ],
)
def test_a_requirement_in_any_form_puts_its_namesake_in_doubt(
    tmp_path: Path, files: Mapping[str, str], source: str
) -> None:
    status, required = _status(tmp_path, files)
    assert status is NameStatus.AMBIGUOUS
    assert required is not None and required.endswith(f" in {source}"), required


@pytest.mark.parametrize(
    "files",
    [
        {},
        {"pyproject.toml": _PROJECT + 'dependencies = ["zz-clicks", "zz", "zzclick"]\n'},
        # The project requiring itself with an extra requires nothing else.
        {
            "pyproject.toml": '[project]\nname = "ZZ.Click"\n'
            '[project.optional-dependencies]\nall = ["zz-click[a]"]\n'
        },
        {"setup.cfg": "[metadata]\nname = zzapp\n[options]\ninstall_requires = file: reqs.in\n"},
        {"requirements.txt": "https://example.invalid/zz_click-8.whl\n./vendor/zz_click\n-e .\n"},
    ],
    ids=["none", "other-names", "itself", "file-directive", "urls-and-paths"],
)
def test_without_a_requirement_of_its_name_the_directory_is_the_name(
    tmp_path: Path, files: Mapping[str, str]
) -> None:
    """The control: a directory genuinely the project's own is trusted as before."""
    assert _status(tmp_path, files) == (NameStatus.ATTESTED, None)


def test_a_namesake_holding_none_of_what_its_imports_need_stays_external(tmp_path: Path) -> None:
    """The namesake rule comes first: ``examples/celery/`` holding no module imported is no candidate."""
    _write(
        tmp_path,
        {
            "pyproject.toml": _PROJECT + 'dependencies = ["zz-click"]\n',
            "zzapp/__init__.py": "",
            "zzapp/core.py": "from zz_click.core import run\n",
            "zz_click/__init__.py": "",
            "zz_click/utils.py": "",
        },
    )
    model = build_import_model(tmp_path, installed=_standard_library_only)
    assert model.names["zz_click"].status is NameStatus.EXTERNAL
    assert model.problems == ()


def test_the_namesakes_unresolved_imports_are_the_librarys(tmp_path: Path) -> None:
    """Where the name is in doubt, an import its directory lacks is no missing module of the tree."""
    _write(
        tmp_path,
        {
            "pyproject.toml": _PROJECT + 'dependencies = ["zz-click"]\n',
            **_NAMESAKE,
            "zzapp/cli.py": "from zz_click.core import run\n",
        },
    )
    model = build_import_model(tmp_path, installed=_standard_library_only)
    assert [type(problem) for problem in model.problems] == [AmbiguousName]
    assert model.importers_of_missing_modules == frozenset()
    root = tmp_path.resolve()
    assert model.spelling(root / "zzapp/core.py", root / "zz_click/utils.py") is None
    assert model.module_name(root / "zz_click/utils.py") is None


def test_the_reader_keeps_what_every_installation_installs_apart(tmp_path: Path) -> None:
    """``new_import_requirement`` counts only what every installation has; the doubt counts all."""
    _write(
        tmp_path,
        {
            "pyproject.toml": """
                [project]
                name = "zzapp"
                dependencies = ["Always_Here>=1"]
                [project.optional-dependencies]
                extra = ["only-with-extra"]
                [dependency-groups]
                dev = ["only-in-dev"]
                [tool.poetry.dependencies]
                python = "^3.11"
                poetry-always = "^1"
                poetry-extra = {version = "^1", optional = true}
            """,
            "setup.cfg": "[options]\ninstall_requires =\n    cfg-always\n",
            "requirements.txt": "from-requirements\n",
            "uv.lock": '[[package]]\nname = "from-lock"\n[[package]]\nname = "zzapp"\n',
        },
    )
    assert installed_with_the_project(tmp_path) == {"always_here", "poetry_always", "cfg_always"}
    assert {requirement.name for requirement in declared_requirements(tmp_path)} == {
        "always_here",
        "only_with_extra",
        "only_in_dev",
        "poetry_always",
        "poetry_extra",
        "cfg_always",
        "from_requirements",
        "from_lock",
    }
    assert normalized_name("Typing.Extensions") == normalized_name("typing_extensions")


# -- What the user is told --------------------------------------------------------


def test_each_kind_of_doubt_gets_the_remedy_that_resolves_it(tmp_path: Path) -> None:
    """``--exclude`` resolves a stray copy, not an installed one, and a requirement needs telling apart."""
    root = tmp_path
    site = ImportSite(root / "a/b.py", 1, 3, "x", ("y",))
    stray = AmbiguousName("a", (root / "build/lib/a", root / "src/a"), None)
    installed = AmbiguousName("a", (root / "src/a",), "/venv/site-packages/a/__init__.py")
    required = AmbiguousName("a", (root / "a",), None, "a>=1 in pyproject.toml")
    assert stray.doubts == {Doubt.TREE}
    assert installed.doubts == {Doubt.INSTALLED}
    assert required.doubts == {Doubt.REQUIRED}
    for problem in (
        FileUnderTwoNames(root / "src/a", ("src.a", "a")),
        TopLevelInsidePackage("a", root / "src/a", root / "src"),
        RelativeImportEscapes(site),
    ):
        assert problem.doubts == {Doubt.TREE}
    assert UnresolvedImport(site, "a", root / "a", "a.gone").doubts == frozenset()
    assert _import_problem_remedies([stray]) == STRAY_COPY_REMEDY
    assert _import_problem_remedies([installed]) == INSTALLED_COPY_REMEDY
    assert _import_problem_remedies([required]) == REQUIRED_DISTRIBUTION_REMEDY
    assert _import_problem_remedies([required, stray, installed]) == "\n".join(
        [STRAY_COPY_REMEDY, INSTALLED_COPY_REMEDY, REQUIRED_DISTRIBUTION_REMEDY]
    )
    assert "--exclude cannot reach" in INSTALLED_COPY_REMEDY
    assert "pip install -e ." in INSTALLED_COPY_REMEDY
    assert "drop the requirement" in REQUIRED_DISTRIBUTION_REMEDY
    assert "rename" in REQUIRED_DISTRIBUTION_REMEDY
    described = required.describe(root)
    assert (
        described
        == "a could be any of: a; the distribution the project requires (a>=1 in pyproject.toml)"
    )


# -- The auditor's layout, run and installed ---------------------------------------


def _towel(root: Path, target: str, *flags: str) -> subprocess.CompletedProcess[str]:
    output = root.parent / f"{root.name}-out"
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "towel.cli",
            "dry",
            str(root / target),
            str(output),
            "--progress",
            "none",
            "--no-interactive",
            "--no-types",
            "--no-format",
            "--cross-module",
            *flags,
        ],
        capture_output=True,
        text=True,
        cwd=root.parent,
        env=dict(os.environ, PYTHONPATH=str(Path(towel.__file__).resolve().parents[1])),
        timeout=600,
    )


def _installed_behaviour(package: Path, site: Path, code: str, work: Path) -> str:
    """What ``code`` prints with only ``package``, shipped as ``zzapp``, and ``site`` on the path."""
    shipped = work / f"shipped-{next(_serial)}"
    shutil.copytree(package, shipped / "zzapp")
    prelude = f"import sys\nsys.path[:0] = [{str(shipped)!r}, {str(site)!r}]\n"
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", prelude + code],
        capture_output=True,
        text=True,
        env={"PATH": os.environ.get("PATH", "")},
        timeout=120,
    )
    return result.stdout + result.stderr


_serial = itertools.count()
_AUDITED = {
    "pyproject.toml": '[project]\nname = "zzapp"\nversion = "0"\ndependencies = ["zzclick>=8"]\n'
    '[tool.setuptools]\npackages = ["zzapp"]\n',
    "zzapp/__init__.py": "",
    "zzapp/core.py": """
        from zzclick.utils import echo


        def use(n):
            echo(n)
            print("app")
            total = n * 3
            extra = total + 7
            print("shared", total, extra)
            return extra + 1
    """,
    "zzclick/__init__.py": "",
    "zzclick/utils.py": """
        def echo(message):
            print("local echo", message)


        def shared(n):
            print("local")
            total = n * 3
            extra = total + 7
            print("shared", total, extra)
            return extra
    """,
}
_LIBRARY = {
    "zzclick/__init__.py": "",
    "zzclick/utils.py": "def echo(message):\n    print('library', message)\n",
}


@pytest.mark.parametrize(
    "target, refused", [(".", True), ("zzapp", False)], ids=["root", "package"]
)
def test_the_audited_namesake_leaves_the_installed_program_working(
    tmp_path: Path, target: str, refused: bool
) -> None:
    """Installed as it ships, with the real library beside it, ``zzapp.core.use(2)`` still returns 14.

    The root run refuses, naming the requirement and its remedy; a run on the
    package goes on and shares nothing with the namesake.
    """
    root = _write(tmp_path / "project", _AUDITED)
    site = _write(tmp_path / "site", _LIBRARY)
    probe = "import zzapp.core as c\nprint('use(2) ->', c.use(2))\n"
    expected = _installed_behaviour(root / "zzapp", site, probe, tmp_path)
    assert expected == "library 2\napp\nshared 6 13\nuse(2) -> 14\n", expected
    ran = _towel(root, target)
    output = tmp_path / "project-out"
    if refused:
        assert ran.returncode == 1, ran.stdout + ran.stderr
        assert (
            "zzclick could be any of: zzclick; the distribution the project requires"
            " (zzclick>=8 in pyproject.toml [project].dependencies)"
        ) in ran.stderr
        assert REQUIRED_DISTRIBUTION_REMEDY in ran.stderr
        assert STRAY_COPY_REMEDY not in ran.stderr and INSTALLED_COPY_REMEDY not in ran.stderr
        assert not output.exists()
        return
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "zzclick could be any of" in ran.stderr and REQUIRED_DISTRIBUTION_REMEDY in ran.stderr
    assert "import __extracted_func" not in (output / "core.py").read_text()
    assert _installed_behaviour(output, site, probe, tmp_path) == expected


# -- A directory lacking a module the program imports of its name (round-4 P1-3) --------

_LACKING = {
    "pyproject.toml": '[project]\nname = "zzapp"\n',
    "zzapp/__init__.py": "",
    "zzapp/core.py": "from zz_click.utils import echo\n",
    "third_party/zz_click/utils.py": "def echo(message):\n    print(message)\n",
}
"""The round-4 layout: ``third_party/zz_click`` shares ``utils`` with a library holding more."""


@pytest.mark.parametrize(
    "files",
    [
        {"zzapp/cli.py": "from zz_click.core import Command\n"},
        {
            "zzapp/cli.py": "from zz_click.core import Command\n",
            "third_party/zz_click/__init__.py": "",
        },
        {"zzapp/cli.py": "from zz_click import Command\n"},
        {"zzapp/cli.py": "def run():\n    import zz_click.core\n"},
        {
            "zzapp/cli.py": "from typing import TYPE_CHECKING\n\n"
            "if TYPE_CHECKING:\n    from zz_click.core import Command\n"
        },
    ],
    ids=["namespace", "regular", "unbound-name", "in-a-function", "type-only"],
)
def test_a_directory_lacking_a_module_the_program_imports_is_in_doubt(
    tmp_path: Path, files: Mapping[str, str]
) -> None:
    """The program reaches a ``zz_click`` holding ``core``, so the directory lacking it may not be that name."""
    _write(tmp_path, {**_LACKING, **files})
    model = build_import_model(tmp_path, installed=_standard_library_only)
    (problem,) = model.problems
    assert isinstance(problem, AmbiguousName) and problem.doubts == {Doubt.LACKING}
    assert problem.lacking is not None and problem.lacking.site.file == model.root / "zzapp/cli.py"
    assert "which third_party/zz_click lacks" in problem.describe(model.root)
    assert model.importers_of_missing_modules == frozenset()
    utils = model.root / "third_party/zz_click/utils.py"
    assert model.spelling(model.root / "zzapp/core.py", utils) is None
    assert model.module_name(utils) is None
    assert _import_problem_remedies([problem]) == LACKING_MODULE_REMEDY


@pytest.mark.parametrize(
    "files",
    [
        # The project is the distribution zz_click: sphinx's test data mocks a module of its own.
        {
            "pyproject.toml": '[project]\nname = "ZZ-Click"\n',
            "zzapp/cli.py": "import zz_click.core\n",
        },
        # The directory's own import of what it lacks is a module its build generates.
        {"third_party/zz_click/version.py": "from zz_click._version import version\n"},
        # Expecting the import may fail, or looking along another sys.path, shows nothing.
        {"zzapp/cli.py": "try:\n    import zz_click.core\nexcept ImportError:\n    pass\n"},
        {"zzapp/cli.py": "import sys\nsys.path.insert(0, 'vendor')\nimport zz_click.core\n"},
        # Nothing of the name in the tree: some other distribution's, and no problem.
        {"zzapp/cli.py": "import zz_nothing_here.core\n"},
    ],
    ids=["own-name", "from-inside", "guarded", "changes-sys-path", "wholly-missing"],
)
def test_an_import_that_lacks_says_nothing_of_the_name_here(
    tmp_path: Path, files: Mapping[str, str]
) -> None:
    """The controls: DECISIONS' missing modules still refuse nothing and leave the name trusted."""
    _write(tmp_path, {**_LACKING, **files})
    model = build_import_model(tmp_path, installed=_standard_library_only)
    assert model.names["zz_click"].trusted, model.problems
    assert all(isinstance(problem, UnresolvedImport) for problem in model.problems)
    spelling = model.spelling(
        model.root / "zzapp/core.py", model.root / "third_party/zz_click/utils.py"
    )
    assert spelling is not None and spelling.module == "zz_click.utils"


_LACKING_AUDITED = {
    "pyproject.toml": '[project]\nname = "zzapp"\nversion = "0"\ndependencies = ["zzblack"]\n'
    '[tool.setuptools]\npackages = ["zzapp"]\n',
    "zzapp/__init__.py": "",
    "zzapp/cli.py": _AUDITED["zzapp/core.py"],
    "zzapp/main.py": "from zzclick.core import Command\n\nfrom zzapp.cli import use\n",
    "third_party/zzclick/utils.py": _AUDITED["zzclick/utils.py"],
}
"""The round-4 reproducer: zzclick reaches the program only as a dependency's dependency."""


def test_the_lacking_namesake_refuses_the_root_run_and_its_remedies_clear_it(
    tmp_path: Path,
) -> None:
    """Installed as it ships, beside the real library, ``zzapp.cli.use(2)`` still returns 14."""
    root = _write(tmp_path / "project", _LACKING_AUDITED)
    library = {**_LIBRARY, "zzclick/core.py": "Command = object\n"}
    site = _write(tmp_path / "site", library)
    probe = "import zzapp.main, zzapp.cli as c\nprint('use(2) ->', c.use(2))\n"
    expected = _installed_behaviour(root / "zzapp", site, probe, tmp_path)
    assert expected == "library 2\napp\nshared 6 13\nuse(2) -> 14\n", expected
    refused = _towel(root, ".")
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert (
        "zzclick could be any of: third_party/zzclick; another zzclick, holding the zzclick.core"
        " that zzapp/main.py:1 imports (from zzclick.core import Command), which"
        " third_party/zzclick lacks"
    ) in refused.stderr, refused.stderr
    assert LACKING_MODULE_REMEDY in refused.stderr
    assert not (tmp_path / "project-out").exists()
    # The library's reading: leave the directory out.
    ran = _towel(root, ".", "--exclude", "zzclick")
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "zzclick could be any of" not in ran.stderr
    assert _installed_behaviour(tmp_path / "project-out/zzapp", site, probe, tmp_path) == expected


def test_where_the_directory_is_what_the_program_means_the_import_that_lacks_is_left_out(
    tmp_path: Path,
) -> None:
    """The other reading, and its remedy: the tree's ``zzclick`` ships, and a tool's import is stale."""
    files = {
        **_LACKING_AUDITED,
        "pyproject.toml": '[project]\nname = "zzapp"\nversion = "0"\n',
        "zzapp/main.py": "from zzapp.cli import use\n",
        "tools/stale.py": "from zzclick.core import Command\n",
    }
    root = _write(tmp_path / "project", files)
    probe = "import zzapp.cli as c\nprint('use(2) ->', c.use(2))\n"

    def run(tree: Path) -> str:
        prelude = f"import sys\nsys.path[:0] = [{str(tree)!r}, {str(tree / 'third_party')!r}]\n"
        command = [sys.executable, "-I", "-B", "-c", prelude + probe]
        ran = subprocess.run(command, capture_output=True, text=True, timeout=120)
        return ran.stdout + ran.stderr

    expected = run(root)
    assert expected == "local echo 2\napp\nshared 6 13\nuse(2) -> 14\n", expected
    refused = _towel(root, ".")
    assert refused.returncode == 1 and LACKING_MODULE_REMEDY in refused.stderr, refused.stderr
    ran = _towel(root, ".", "--exclude", "tools")
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "zzclick could be any of" not in ran.stderr
    assert "import __extracted_func" in (tmp_path / "project-out/zzapp/cli.py").read_text()
    assert run(tmp_path / "project-out") == expected
