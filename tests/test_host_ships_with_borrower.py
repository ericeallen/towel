"""A borrower may import a helper only from a top-level package it already imports.

An installed distribution ships the packages its metadata names, not the
repository: a ``tests`` or ``examples`` package beside ``zeta`` stays behind.
A helper hosted in ``tests/test_b.py`` that ``zeta/a.py`` imports made the
installed ``zeta.a`` raise ``ModuleNotFoundError`` (audit cases L20, L21).
What a module already imports is known to be present wherever it runs, its
own top-level package included; anything else is a new requirement, and the
host is refused. Each case here imports every module of the refactored
project with only the shipped packages on the path.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap
from typing import Dict, Sequence

import pytest

from towel.unification.import_graph import ImportGraphCache, import_runs_new_code
from towel.unification.refactor_engine import UnificationRefactorEngine

BLOCK = """
    print("begin {tag}")
    total = 0
    for item in items:
        if item > 0:
            total += item * scale
        else:
            total -= item
    count = len(items)
    print("{tag}", total, count)
    return total + count + {offset}
"""


def _function(name: str, tag: str, offset: int) -> str:
    return (
        f"def {name}(items, scale):\n"
        + textwrap.indent(textwrap.dedent(BLOCK.format(tag=tag, offset=offset)).strip("\n"), "    ")
        + "\n"
    )


def _write(root: Path, files: Dict[str, str]) -> None:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _refactor(root: Path, target: str) -> bool:
    engine = UnificationRefactorEngine(min_lines=3, cross_module_helpers=True)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        results, _ = engine.refactor_directory_to_fixed_point(
            str(root / target), str(root / target), progress="none"
        )
    return sum(applied for applied, _ in results.values()) > 0


def _import_shipped(root: Path, packages: Sequence[str], modules: Sequence[str], work: Path) -> str:
    """Import ``modules`` with only ``packages`` on the path, as an installed wheel holds them."""
    site = work / "site"
    shutil.rmtree(site, ignore_errors=True)
    site.mkdir(parents=True)
    for package in packages:
        shutil.copytree(root / package, site / Path(package).name)
    probe = "import importlib\n" + "".join(
        f"try:\n    importlib.import_module({module!r}); print({module!r}, 'ok')\n"
        f"except Exception as error:\n    print({module!r}, type(error).__name__)\n"
        for module in modules
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {str(site)!r})\n" + probe],
        capture_output=True,
        text=True,
        cwd=work,
        check=True,
    )
    return completed.stdout


FLAT_PYPROJECT = (
    '[build-system]\nrequires = ["setuptools>=61"]\nbuild-backend = "setuptools.build_meta"\n\n'
    '[project]\nname = "zeta"\nversion = "0.1"\n\n[tool.setuptools]\npackages = ["zeta"]\n'
)
SRC_PYPROJECT = (
    '[build-system]\nrequires = ["setuptools>=61"]\nbuild-backend = "setuptools.build_meta"\n\n'
    '[project]\nname = "alpha"\nversion = "0.1"\n'
)


@pytest.mark.parametrize(
    "files, target, shipped, modules",
    [
        (
            {
                "pyproject.toml": FLAT_PYPROJECT,
                "zeta/__init__.py": "",
                "zeta/a.py": _function("fa", "a", 1),
                "tests/__init__.py": "",
                "tests/test_b.py": _function("test_fb", "bb", 2),
            },
            ".",
            ["zeta"],
            ["zeta", "zeta.a"],
        ),
        (
            {
                "pyproject.toml": SRC_PYPROJECT,
                "src/alpha/__init__.py": "",
                "src/alpha/a.py": _function("fa", "a", 1),
                "examples/__init__.py": "",
                "examples/demo.py": _function("fb", "bb", 2),
            },
            ".",
            ["src/alpha"],
            ["alpha", "alpha.a"],
        ),
    ],
    ids=["flat-with-tests", "src-with-examples"],
)
def test_the_shipped_package_never_imports_one_that_stays_behind(
    tmp_path: Path,
    files: Dict[str, str],
    target: str,
    shipped: Sequence[str],
    modules: Sequence[str],
) -> None:
    before, after = tmp_path / "before", tmp_path / "after"
    _write(before, files)
    _write(after, files)
    _refactor(after, target)
    assert _import_shipped(after, shipped, modules, tmp_path) == _import_shipped(
        before, shipped, modules, tmp_path
    )


def test_a_test_module_that_imports_the_package_may_borrow_from_it(tmp_path: Path) -> None:
    files = {
        "pyproject.toml": FLAT_PYPROJECT,
        "zeta/__init__.py": "",
        "zeta/a.py": _function("fa", "a", 1),
        "tests/__init__.py": "",
        "tests/test_b.py": "import zeta\n\n\n" + _function("test_fb", "bb", 2),
    }
    _write(tmp_path, files)
    assert _refactor(tmp_path, ".")
    assert "from tests" not in (tmp_path / "zeta" / "a.py").read_text()
    assert "extracted_func" in (tmp_path / "tests" / "test_b.py").read_text()
    assert _import_shipped(tmp_path, ["zeta"], ["zeta", "zeta.a"], tmp_path).split() == [
        "zeta",
        "ok",
        "zeta.a",
        "ok",
    ]


def test_a_borrower_gains_only_top_level_packages_it_already_imports(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "pyproject.toml": FLAT_PYPROJECT,
            "zeta/__init__.py": "",
            "zeta/a.py": "def fa():\n    return 1\n",
            "zeta/b.py": "def fb():\n    return 2\n",
            "zeta/uses_tests.py": "import tests.util\ndef fc():\n    return 3\n",
            "zeta/lazy.py": "def fl():\n    import tests.util\n    return 4\n",
            "tests/__init__.py": "",
            "tests/util.py": "def helper():\n    return 5\n",
            "tests/test_b.py": "import zeta.a\ndef test_b():\n    return 6\n",
            "tests/test_c.py": "def test_c():\n    return 7\n",
        },
    )
    cache = ImportGraphCache()

    def refused(host: str, borrower: str) -> bool:
        return import_runs_new_code(str(tmp_path / host), str(tmp_path / borrower), cache)

    # Its own top-level package is always there; another only when imported.
    assert not refused("zeta/b.py", "zeta/a.py")
    assert refused("tests/util.py", "zeta/a.py")
    assert not refused("zeta/b.py", "tests/test_b.py")
    assert refused("zeta/b.py", "tests/test_c.py")
    # A host that imports tests makes its borrower need tests too.
    assert refused("zeta/uses_tests.py", "zeta/a.py")
    # An import inside a function runs only when it is called: importing
    # zeta.lazy does not require tests, and does not make them present.
    assert not refused("zeta/lazy.py", "zeta/a.py")
    assert refused("tests/util.py", "zeta/lazy.py")


def test_a_borrower_that_imports_its_host_only_in_a_function_does_not_already_run_it(
    tmp_path: Path,
) -> None:
    package = tmp_path / "pkg"
    _write(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/host.py": 'print("loading host")\ndef helper():\n    return 1\n',
            "pkg/lazy.py": "def use():\n    from pkg import host\n    return host.helper()\n",
            "pkg/eager.py": "from pkg import host\ndef use():\n    return 2\n",
        },
    )
    cache = ImportGraphCache()
    assert import_runs_new_code(str(package / "host.py"), str(package / "lazy.py"), cache)
    assert not import_runs_new_code(str(package / "host.py"), str(package / "eager.py"), cache)
