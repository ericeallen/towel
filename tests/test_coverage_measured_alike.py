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

"""Under ``--cross-module``, code moves only between files coverage.py measures alike.

Round 4 found a block of ``pkg/a.py``, which the project's ``.coveragerc``
omits, moving into a helper in ``pkg/b.py``, which coverage.py measures: the
moved code became measured, and ``coverage report`` counted a missed line more
(4 to 5). A pair is now declined (``coverage_measurement_differs``) when its
modules differ in whether coverage.py measures them, or in whether it reports
them and so counts them toward ``fail_under``, as the project's ``[run]``
``omit``, ``include``, ``source``, ``source_pkgs`` and ``source_dirs`` and its
``[report]`` ``include`` and ``omit`` decide.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
from typing import Dict, Mapping, Tuple

import pytest

from towel.unification.refactor_engine import UnificationRefactorEngine

_A = """
from pkg import b


def f1(rows):
    n = len(rows) + 1
    total = sum(rows) * n
    print("t", n, total)
    return total
"""
_B = """
def f2(rows):
    n = len(rows) + 1
    total = sum(rows) * n
    print("t", n, total)
    return total


def used(rows):
    return rows
"""


def _refactor(root: Path, configuration: Mapping[str, str]) -> Tuple[int, Mapping[str, int]]:
    files: Dict[str, str] = {
        "pyproject.toml": '[project]\nname = "proj"\nversion = "0"\n',
        "pkg/__init__.py": "",
        "pkg/a.py": _A,
        "pkg/b.py": _B,
        **configuration,
    }
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    engine = UnificationRefactorEngine(min_lines=3, cross_module_helpers=True)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        results, _ = engine.refactor_directory_to_fixed_point(
            str(root / "pkg"), str(root / "pkg"), progress="none"
        )
    return sum(applied for applied, _ in results.values()), engine.declined_pairs


@pytest.mark.parametrize(
    "configuration",
    [
        {".coveragerc": "[run]\nomit =\n    pkg/a.py\n"},
        {"pyproject.toml": '[project]\nname = "proj"\n[tool.coverage.run]\nomit = ["*/a.py"]\n'},
        {".coveragerc": "[run]\ninclude = */b.py\n"},
        {"setup.cfg": "[coverage:run]\nsource_pkgs = pkg.b\n"},
        # Measured alike, but only one counts toward fail_under.
        {".coveragerc": "[report]\nomit = pkg/b.py\n"},
    ],
    ids=["omit", "pyproject omit", "include", "source_pkgs", "report omit"],
)
def test_code_does_not_move_between_files_coverage_treats_differently(
    tmp_path: Path, configuration: Mapping[str, str]
) -> None:
    applied, declined = _refactor(tmp_path, configuration)
    assert applied == 0
    assert "coverage_measurement_differs" in declined


@pytest.mark.parametrize(
    "configuration",
    [
        {},
        {".coveragerc": "[run]\nomit = pkg/*\n"},
        {".coveragerc": "[run]\nsource = pkg\n"},
    ],
    ids=["no configuration", "both omitted", "both in the source"],
)
def test_code_moves_between_files_coverage_treats_alike(
    tmp_path: Path, configuration: Mapping[str, str]
) -> None:
    applied, declined = _refactor(tmp_path, configuration)
    assert applied > 0
    assert "coverage_measurement_differs" not in declined
