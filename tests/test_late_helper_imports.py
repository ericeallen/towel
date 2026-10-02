"""Sharing a helper must not advance imports past definitions they depend on."""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
import subprocess
import sys

import pytest

from towel.unification.refactor_engine import UnificationRefactorEngine

_BODY = """def {name}(values):
    size = len(values) + 1
    result = size * 2
    total = result + 3
    return total * {factor}
"""


def _program(root: Path, host_prefix: str, borrower_prefix: str, driver_prefix: str) -> Path:
    package = root / "pkg"
    package.mkdir()
    (root / "pyproject.toml").write_text('[project]\nname="import-order-probe"\nversion="0"\n')
    (package / "__init__.py").write_text("")
    (package / "a.py").write_text(host_prefix + _BODY.format(name="first", factor=5))
    (package / "b.py").write_text(
        borrower_prefix + _BODY.format(name="second", factor=7) + "\nfrom pkg import a\n"
    )
    (root / "run.py").write_text(
        driver_prefix + "from pkg import b\nprint(b.a.first([1, 3]), b.second([2, 4]))\n"
    )
    return package


def _run(root: Path) -> str:
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            f"import sys; sys.path.insert(0, {str(root)!r}); import run",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize(
    ("host_prefix", "borrower_prefix", "driver_prefix", "expected"),
    [
        (
            "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from .b import second\n",
            "",
            "import typing\ntyping.TYPE_CHECKING = True\n",
            "45 63\n",
        ),
        (
            "import importlib\nsecond = importlib.import_module('pkg.b').second\n",
            "",
            "",
            "45 63\n",
        ),
        (
            "print('host')\n",
            "class Marker:\n    print('borrower class')\n",
            "",
            "borrower class\nhost\n45 63\n",
        ),
    ],
    ids=["runtime-type-checking-flag", "dynamic-cycle", "class-body-order"],
)
def test_helper_import_does_not_advance_a_late_module_load(
    tmp_path: Path,
    host_prefix: str,
    borrower_prefix: str,
    driver_prefix: str,
    expected: str,
) -> None:
    package = _program(tmp_path, host_prefix, borrower_prefix, driver_prefix)
    assert _run(tmp_path) == expected
    engine = UnificationRefactorEngine(min_lines=3, cross_module_helpers=True)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        engine.refactor_directory_to_fixed_point(str(package), str(package), progress="none")
    assert _run(tmp_path) == expected


def test_an_already_loaded_host_still_shares_the_helper(tmp_path: Path) -> None:
    package = _program(tmp_path, "print('host')\n", "from pkg import a\n", "")
    expected = "host\n45 63\n"
    assert _run(tmp_path) == expected
    engine = UnificationRefactorEngine(min_lines=3, cross_module_helpers=True)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        results, _ = engine.refactor_directory_to_fixed_point(
            str(package), str(package), progress="none"
        )
    assert results, "an import that already ran must not prevent safe sharing"
    assert _run(tmp_path) == expected
