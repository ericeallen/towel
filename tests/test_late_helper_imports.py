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
    size = values[0] + values[1]
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
            "55 105\n",
        ),
        (
            "import importlib\nsecond = importlib.import_module('pkg.b').second\n",
            "",
            "",
            "55 105\n",
        ),
        (
            "print('host')\n",
            "class Marker:\n    print('borrower class')\n",
            "",
            "borrower class\nhost\n55 105\n",
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
    package = _program(tmp_path, "print('host')\n", "import pkg.a\n", "")
    expected = "host\n55 105\n"
    assert _run(tmp_path) == expected
    engine = UnificationRefactorEngine(min_lines=3, cross_module_helpers=True)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        results, _ = engine.refactor_directory_to_fixed_point(
            str(package), str(package), progress="none"
        )
    assert results, "an import that already ran must not prevent safe sharing"
    assert _run(tmp_path) == expected


def test_a_future_directive_can_reenter_through_a_local_module(tmp_path: Path) -> None:
    package = _program(
        tmp_path,
        "from __future__ import annotations\n",
        "from pkg import a\n",
        "from pkg import a\n",
    )
    (tmp_path / "__future__.py").write_text(
        "import importlib\nimportlib.import_module('pkg.b')\nannotations = 0\n"
    )
    expected = "55 105\n"
    assert _run(tmp_path) == expected
    engine = UnificationRefactorEngine(min_lines=3, cross_module_helpers=True)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        results, _ = engine.refactor_directory_to_fixed_point(
            str(package), str(package), progress="none"
        )
    assert not results, "a shadowed future import cannot establish helper availability"
    assert _run(tmp_path) == expected


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("leading", ["", "import pkg\n", "from . import constants\n"])
def test_running_package_initializers_have_not_completed_their_later_imports(
    tmp_path: Path, nested: bool, leading: str
) -> None:
    """A dependency can import a child while its package initializer is still running."""
    package = tmp_path / "pkg"
    leaf = package / "inner" if nested else package
    leaf.mkdir(parents=True)
    module = "pkg.inner" if nested else "pkg"
    (tmp_path / "pyproject.toml").write_text('[project]\nname="init-order"\nversion="0"\n')
    if nested:
        (leaf / "__init__.py").write_text("")
    (package / "__init__.py").write_text(
        f"import importlib\nimportlib.import_module('{module}.b')\n"
        + ("from .inner import a\n" if nested else "from . import a\n")
    )
    (leaf / "constants.py").write_text("VALUE = 1\n")
    (leaf / "a.py").write_text(
        f"import importlib\nsecond = importlib.import_module('{module}.b').second\n"
        + _BODY.format(name="first", factor=5)
    )
    (leaf / "b.py").write_text(leading + _BODY.format(name="second", factor=7))
    (tmp_path / "run.py").write_text(
        f"import pkg\nprint({module}.a.first([1, 3]), {module}.b.second([2, 4]))\n"
    )
    expected = "55 105\n"
    assert _run(tmp_path) == expected
    engine = UnificationRefactorEngine(min_lines=3, cross_module_helpers=True)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        engine.refactor_directory_to_fixed_point(str(package), str(package), progress="none")
    assert _run(tmp_path) == expected
