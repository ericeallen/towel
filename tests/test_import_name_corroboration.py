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

The layout readers reimplemented five build backends, and checked against
built wheels they named 102 files of seven corpus projects wrongly
(``src.foo.a`` for a ``setup.cfg`` src layout they never read) and 937 more
when a project directory is named like its package (``foo.src.foo.a``);
requiring two derivations to agree then declined exactly those helpers. Towel
now spells every import as the program's own imports show it works
(docs/DECISIONS.md, "Import names come from the program"). Each case runs
``towel dry --cross-module`` over a layout that broke before, checks that the
cross-module helpers it exists for were written, and then runs the result
two ways: in the source tree with the ``sys.path`` its runner sets up, and
from a wheel ``uv build`` makes of the refactored project, installed into a
fresh environment, with only the test directory beside it. Both must print
what the original printed.
"""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap
from typing import Mapping, Sequence, Tuple

import pytest

import towel
from tests.test_import_model import _installed, requires_uv

_WITHIN = """
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
"""A block two modules of one package share: their helper is imported relatively."""

_ACROSS = """
def {name}(words):
    print({tag!r})
    seen = []
    for word in words:
        cleaned = word.strip().lower()
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
    return ", ".join(seen)
"""
"""A block a test module shares with the package it tests: the test borrows it."""

_SETUPTOOLS = (
    '[build-system]\nrequires = ["setuptools>=61"]\nbuild-backend = "setuptools.build_meta"\n'
)
_HATCH = '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n'


def _write(root: Path, files: Mapping[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
    return root


@dataclass(frozen=True)
class _Layout:
    """A project, the directory Towel is pointed at, and how its tests find its modules."""

    files: Mapping[str, str]
    target: str
    package: str
    """The directory holding the package, which a source-tree run puts on ``sys.path``."""
    tests: str
    calls: str
    """Statements that import the modules and print what their functions return."""
    imports: Tuple[Tuple[str, str], ...]
    """Each generated import that must appear, and the file it must appear in."""
    shipped_beside: Tuple[str, ...] = ("tests/test_a.py",)
    """What of the tree an installed run still has beside the wheel: the tests, not the package."""


def _package_modules(prefix: str, package: str) -> Mapping[str, str]:
    """``a.py`` and ``b.py`` sharing ``_WITHIN``, and ``a.py`` holding ``_ACROSS`` too."""
    return {
        f"{prefix}/__init__.py": "",
        f"{prefix}/a.py": _WITHIN.format(name="fa", tag=f"{package} a")
        + "\n"
        + _ACROSS.format(name="ga", tag=f"{package} ga"),
        f"{prefix}/b.py": _WITHIN.format(name="fb", tag=f"{package} b"),
    }


def _test_module(imports: str) -> str:
    return f"{imports}\n\n" + _ACROSS.format(name="gt", tag="test gt")


def _calls(package: str, test_module: str) -> str:
    return (
        f"import {test_module}\n"
        f"from {package}.a import fa, ga\n"
        f"from {package}.b import fb\n"
        "print(fa([0, 2, 3]), fb([1, 5]), ga([' A', 'b ', 'a']), "
        f"{test_module}.gt(['x', ' Y ', 'y']))\n"
    )


def _src_layout(metadata: Mapping[str, str], package: str = "alpha") -> _Layout:
    return _Layout(
        files={
            **metadata,
            **_package_modules(f"src/{package}", package),
            "tests/test_a.py": _test_module(f"import {package}.a"),
        },
        target=".",
        package="src",
        tests="tests",
        calls=_calls(package, "test_a"),
        imports=(
            ("from .a import __extracted_func", f"src/{package}/b.py"),
            (f"from {package}.a import __extracted_func", "tests/test_a.py"),
        ),
    )


def _setup_cfg() -> _Layout:
    return _src_layout(
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
        }
    )


def _setup_py() -> _Layout:
    return _src_layout(
        {
            "pyproject.toml": _SETUPTOOLS,
            "setup.py": """
                from setuptools import find_packages, setup

                setup(
                    name="sample",
                    version="0",
                    package_dir={"": "src"},
                    packages=find_packages("src"),
                )
            """,
        }
    )


def _stray_src_init() -> _Layout:
    layout = _src_layout(
        {
            "pyproject.toml": _SETUPTOOLS
            + '[project]\nname = "sample"\nversion = "0"\n'
            + '[tool.setuptools.packages.find]\nwhere = ["src"]\n'
        }
    )
    return dataclasses.replace(layout, files={**layout.files, "src/__init__.py": ""})


def _named_like_its_package() -> _Layout:
    """The project directory is ``alpha``, and so is the package under ``src``."""
    layout = _src_layout(
        {"pyproject.toml": _SETUPTOOLS + '[project]\nname = "alpha"\nversion = "0"\n'}
    )
    return _Layout(
        {f"alpha/{name}": text for name, text in layout.files.items()},
        "alpha",
        "alpha/src",
        "alpha/tests",
        layout.calls,
        tuple((text, f"alpha/{path}") for text, path in layout.imports),
        ("alpha/tests/test_a.py",),
    )


def _hatch_include() -> _Layout:
    """soupsieve's shape: a flat package that ``include`` selects, with no ``sources``."""
    return _Layout(
        files={
            "pyproject.toml": _HATCH
            + '[project]\nname = "sample"\nversion = "0"\n'
            + '[tool.hatch.build.targets.wheel]\ninclude = ["/alpha"]\n',
            **_package_modules("alpha", "alpha"),
            "tests/test_a.py": _test_module("import alpha.a"),
        },
        target=".",
        package=".",
        tests="tests",
        calls=_calls("alpha", "test_a"),
        imports=(
            ("from .a import __extracted_func", "alpha/b.py"),
            ("from alpha.a import __extracted_func", "tests/test_a.py"),
        ),
    )


def _namespace() -> _Layout:
    """``ns`` has no ``__init__.py``; ``ns.pkg`` is a regular package inside it."""
    files = {
        "pyproject.toml": _SETUPTOOLS
        + '[project]\nname = "sample"\nversion = "0"\n'
        + '[tool.setuptools.packages.find]\nwhere = ["src"]\n',
        **_package_modules("src/ns/pkg", "ns.pkg"),
        "tests/test_a.py": _test_module("import ns.pkg.a"),
    }
    return _Layout(
        files,
        ".",
        "src",
        "tests",
        _calls("ns.pkg", "test_a"),
        (
            ("from .a import __extracted_func", "src/ns/pkg/b.py"),
            ("from ns.pkg.a import __extracted_func", "tests/test_a.py"),
        ),
    )


def _tests_borrow_never_lend() -> _Layout:
    """The tests sort first, so the pair's own file is the test module, which may not host."""
    files = {
        "pyproject.toml": _SETUPTOOLS
        + '[project]\nname = "zeta"\nversion = "0"\n'
        + '[tool.setuptools]\npackages = ["zeta"]\n',
        **_package_modules("zeta", "zeta"),
        "tests/__init__.py": "",
        "tests/test_a.py": _test_module("import zeta.a"),
    }
    return _Layout(
        files,
        ".",
        ".",
        ".",
        _calls("zeta", "tests.test_a"),
        (
            ("from .a import __extracted_func", "zeta/b.py"),
            ("from zeta.a import __extracted_func", "tests/test_a.py"),
        ),
        ("tests",),
    )


def _tests_inside_the_package() -> _Layout:
    """beautifulsoup4's shape: ``alpha/tests`` is inside the package and left out of its wheel.

    ``alpha/tests/test_zcore.py`` sorts before ``alpha/zcore.py``, so the
    pair's own file is the test module. It imports another module of the
    package, which shows it runs as part of ``alpha``, but not ``zcore``, so
    no cycle through it stops ``zcore`` importing it: only the rule that an
    import may enter a directory solely where its own side already imports
    from it does. The helper goes to the module that ships.
    """
    files = {
        "pyproject.toml": _HATCH
        + '[project]\nname = "sample"\nversion = "0"\n'
        + '[tool.hatch.build.targets.wheel]\npackages = ["alpha"]\nexclude = ["alpha/tests/"]\n',
        "alpha/__init__.py": "",
        "alpha/zcore.py": _ACROSS.format(name="ga", tag="alpha ga"),
        "alpha/other.py": "VALUE = 1\n",
        "alpha/tests/__init__.py": "",
        "alpha/tests/test_zcore.py": _test_module("from .. import other"),
    }
    calls = (
        "from alpha.zcore import ga\n"
        "print(ga([' A', 'b ', 'a']))\n"
        "try:\n"
        "    from alpha.tests import test_zcore\n"
        "except ImportError:\n"
        "    print('no tests shipped')\n"
        "else:\n"
        "    print(test_zcore.gt(['x', ' Y ', 'y']))\n"
    )
    return _Layout(
        files,
        ".",
        ".",
        ".",
        calls,
        (("from ..zcore import __extracted_func", "alpha/tests/test_zcore.py"),),
        (),
    )


LAYOUTS = {
    "setup-cfg-src": _setup_cfg,
    "setup-py-src": _setup_py,
    "stray-src-init": _stray_src_init,
    "project-named-like-its-package": _named_like_its_package,
    "hatch-include": _hatch_include,
    "namespace-package": _namespace,
    "tests-borrow-never-lend": _tests_borrow_never_lend,
    "tests-inside-the-package": _tests_inside_the_package,
}


def _dry(project: Path, target: str) -> str:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "towel.cli",
            "dry",
            target,
            target,
            "--no-interactive",
            "--no-types",
            "--no-format",
            "--cross-module",
            "--progress",
            "none",
            "--min-lines",
            "3",
        ],
        capture_output=True,
        text=True,
        cwd=project,
        # The Towel under test, wherever the subprocess runs from.
        env=dict(os.environ, PYTHONPATH=str(Path(towel.__file__).resolve().parents[1])),
        timeout=600,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout + result.stderr


def _run(python: str, sys_path: Sequence[Path], calls: str, cwd: Path) -> str:
    """Run ``calls`` with only ``sys_path`` added: no working directory, ``PYTHONPATH`` or user site."""
    code = f"import sys\nsys.path[:0] = {[str(path) for path in sys_path]!r}\n{calls}"
    result = subprocess.run(
        [python, "-I", "-B", "-c", code],
        capture_output=True,
        text=True,
        cwd=cwd,
        env={"PATH": os.environ.get("PATH", "")},
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def _lent_by_tests(root: Path) -> list[str]:
    """Test modules that define a helper: a test may borrow one, and never lend one."""
    return sorted(
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if "tests" in path.relative_to(root).parts
        and "def __extracted_func" in path.read_text(encoding="utf-8")
    )


@pytest.mark.parametrize("layout", sorted(LAYOUTS))
def test_the_helpers_import_where_the_project_runs(tmp_path: Path, layout: str) -> None:
    case = LAYOUTS[layout]()
    original = _write(tmp_path / "original", case.files)
    refactored = _write(tmp_path / "refactored", case.files)
    expected = _run(
        sys.executable, [original / case.tests, original / case.package], case.calls, tmp_path
    )
    _dry(refactored, case.target)
    for text, path in case.imports:
        assert text in (refactored / path).read_text(encoding="utf-8"), path
    assert _lent_by_tests(refactored) == []
    ran = _run(
        sys.executable, [refactored / case.tests, refactored / case.package], case.calls, tmp_path
    )
    assert ran == expected


def _beside(tree: Path, case: _Layout, directory: Path) -> Path:
    """A directory holding only what ``case`` keeps beside an installed wheel, copied from ``tree``."""
    directory.mkdir()
    for relative in case.shipped_beside:
        source = tree / relative
        if source.is_dir():
            shutil.copytree(source, directory / source.name)
        else:
            shutil.copy2(source, directory / source.name)
    return directory


@requires_uv
@pytest.mark.parametrize("layout", sorted(LAYOUTS))
def test_the_helpers_import_where_the_project_is_installed(tmp_path: Path, layout: str) -> None:
    case = LAYOUTS[layout]()
    original = _write(tmp_path / "original", case.files)
    refactored = _write(tmp_path / "refactored", case.files)
    _dry(refactored, case.target)
    expected = _run(
        _installed(original / case.target, tmp_path / "original-wheel"),
        [_beside(original, case, tmp_path / "original-beside")],
        case.calls,
        tmp_path,
    )
    assert expected.strip(), "the installed original printed nothing"
    ran = _run(
        _installed(refactored / case.target, tmp_path / "refactored-wheel"),
        [_beside(refactored, case, tmp_path / "refactored-beside")],
        case.calls,
        tmp_path,
    )
    assert ran == expected


def test_sibling_top_level_packages_are_not_reached_by_climbing_above_them(
    tmp_path: Path,
) -> None:
    """``..api.checkout`` from ``utils/v.py`` climbs out of ``utils``, which Python refuses."""
    project = _write(
        tmp_path / "project",
        {
            "api/__init__.py": "",
            "api/checkout.py": _WITHIN.format(name="checkout", tag="checkout"),
            "utils/__init__.py": "",
            # A package borrows only from one it already imports.
            "utils/validators.py": "import api.checkout\n\n\n"
            + _WITHIN.format(name="validate", tag="validate"),
        },
    )
    calls = (
        "from api.checkout import checkout\nfrom utils.validators import validate\n"
        "print(checkout([0, 2, 3]), validate([1, 5]))\n"
    )
    expected = _run(sys.executable, [project], calls, tmp_path)
    _dry(project, ".")
    written = (project / "utils" / "validators.py").read_text(encoding="utf-8")
    assert "from api.checkout import __extracted_func" in written, written
    assert "from .." not in written
    assert _run(sys.executable, [project], calls, tmp_path) == expected
