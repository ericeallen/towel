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

"""mypy's jurisdiction is what the project's own mypy run checks (round 4, P2).

A file no configured checker covers gets only its sites' declared annotations,
completed with ``Any`` (DECISIONS, "An error is accounted for by the
original's error where it stood"). That was implemented for pyright only: mypy
was taken to report on every file it was given, and a probe build that names a
file makes it a source, so the probe answered there. Tests outside
``files = ["pkg"]``, and an implementation behind its own ``.pyi``, got helpers
annotated with inferred types (``-> int``) that no check of the project looks
at. And a module whose options set ``ignore_errors`` has its ``reveal_type``
notes suppressed with its errors, so the silence was read as code the checker
does not look at, and every change there was declined (graphene, referencing,
numbagg: nothing applied typed).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import textwrap
from typing import Mapping

import pytest

from towel.type_inference import MypyInferrer, TypeOracle, reports_by_each
from towel.unification.refactor_engine import UnificationRefactorEngine

requires_mypy = pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")
requires_pyright = pytest.mark.skipif(
    shutil.which("pyright") is None and importlib.util.find_spec("pyright") is None,
    reason="pyright absent",
)


def _write(root: Path, files: Mapping[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")


TWINS = """\
    def first(values: list[int], k: int) -> int:
        out = []
        for v in values:
            out.append(v + k)
        count = len(out)
        return count


    def second(items: list[str], j: str) -> int:
        out = []
        for v in items:
            out.append(v + j)
        count = len(out)
        return count * 2
    """

STUB = "def first(values: list[int], k: int) -> int: ...\ndef second(items: list[str], j: str) -> int: ...\n"


@requires_mypy
def test_r9my_mypy_reports_on_what_its_own_run_checks(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "pyproject.toml": (
                '[tool.mypy]\nfiles = ["pkg"]\n'
                '[[tool.mypy.overrides]]\nmodule = "pkg.quiet"\nignore_errors = true\n'
            ),
            "pkg/__init__.py": "",
            "pkg/core.py": "import helpers\n\nUSED: int = helpers.VALUE\n",
            "pkg/shadowed.py": TWINS,
            "pkg/shadowed.pyi": STUB,
            "pkg/quiet.py": TWINS,
            "helpers.py": "VALUE: int = 1\n",
            "tests/test_core.py": TWINS,
        },
    )
    names = ["pkg/core.py", "pkg/shadowed.py", "pkg/quiet.py", "helpers.py", "tests/test_core.py"]
    paths = [str(tmp_path / name) for name in names]
    oracle = MypyInferrer()
    try:
        oracle.begin_run(paths)
        oracle.check_project({path: Path(path).read_text(encoding="utf-8") for path in paths})
        (reported,) = reports_by_each(oracle, paths)
    finally:
        oracle.close()
    assert sorted(str(Path(path).relative_to(tmp_path)) for path in reported) == [
        "helpers.py",  # outside ``files``, but followed to from a file it names
        "pkg/core.py",
    ], "not tests/ (outside files), the implementation behind its stub, or pkg.quiet"


@requires_mypy
def test_r9my_a_file_no_check_has_seen_is_checked_to_find_out(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "pyproject.toml": "[tool.mypy]\nignore_errors = true\n",
            "pkg/__init__.py": "",
            "pkg/core.py": TWINS,
        },
    )
    path = str(tmp_path / "pkg" / "core.py")
    oracle = MypyInferrer()
    try:
        assert oracle.reports_on([path]) == frozenset(), "a global ignore_errors reports nowhere"
    finally:
        oracle.close()


def _helper_signature(root: Path, target: str, oracle: TypeOracle) -> str:
    engine = UnificationRefactorEngine(min_lines=3, type_oracle=oracle)
    try:
        results, _ = engine.refactor_directory_to_fixed_point(str(root), str(root), progress="none")
    finally:
        oracle.close()
    assert [Path(path).name for path, (applied, _) in results.items() if applied] == [
        Path(target).name
    ]
    text = (root / target).read_text(encoding="utf-8")
    start = text.index("def __extracted_func_0(")
    return " ".join(text[start : text.index(":\n", start)].split())


@requires_mypy
@requires_pyright
def test_r9my_outside_files_mypy_annotates_as_pyright_does_outside_its_include(
    tmp_path: Path,
) -> None:
    from towel.type_inference import PyrightOracle

    _write(
        tmp_path / "mypy",
        {
            "pyproject.toml": '[tool.mypy]\nstrict = true\nfiles = ["pkg"]\n',
            "pkg/__init__.py": "",
            "pkg/core.py": "def ok(x: int) -> int:\n    return x\n",
            "tests/test_core.py": TWINS,
        },
    )
    _write(
        tmp_path / "pyright",
        {
            "pyrightconfig.json": '{"exclude": ["pkg/legacy.py"]}',
            "pkg/__init__.py": "",
            "pkg/legacy.py": TWINS,
        },
    )
    by_mypy = _helper_signature(tmp_path / "mypy", "tests/test_core.py", MypyInferrer())
    by_pyright = _helper_signature(
        tmp_path / "pyright", "pkg/legacy.py", PyrightOracle(language_server=False)
    )
    assert by_mypy.replace('"', "'") == by_pyright.replace('"', "'")
    assert by_mypy.endswith("-> _typing.Any"), by_mypy


@requires_mypy
def test_r9my_an_implementation_behind_its_stub_takes_only_what_its_sites_declare(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        {
            "pyproject.toml": '[tool.mypy]\nstrict = true\nfiles = ["pkg"]\n',
            "pkg/__init__.py": "",
            "pkg/shadowed.py": TWINS,
            "pkg/shadowed.pyi": STUB,
        },
    )
    signature = _helper_signature(tmp_path, "pkg/shadowed.py", MypyInferrer())
    assert signature.endswith("-> _typing.Any"), signature


TAX_TESTS = """\
    from calc.core import total


    def test_total_with_tax() -> None:
        prices = [1.0, 2.0, 3.5]
        result = total(prices, 0.1)
        assert result > 0
        assert isinstance(result, float)
        assert result == 7.15


    def test_total_without_tax() -> None:
        prices = [4.0, 5.0, 6.5]
        result = total(prices, 0.0)
        assert result > 0
        assert isinstance(result, float)
        assert result == 15.5
    """


@requires_mypy
@pytest.mark.parametrize(
    "silenced",
    [
        "ignore_errors = true\n",
        '[[tool.mypy.overrides]]\nmodule = ["calc.tests.*"]\nignore_errors = true\n',
    ],
    ids=["global", "per-module"],
)
def test_r9my_a_module_under_ignore_errors_is_changed_as_one_no_checker_covers(
    tmp_path: Path, silenced: str
) -> None:
    _write(
        tmp_path,
        {
            "pyproject.toml": f"[tool.mypy]\nstrict = true\n{silenced}",
            "calc/__init__.py": "",
            "calc/core.py": (
                "def total(prices: list[float], rate: float) -> float:\n"
                "    return round(sum(prices) * (1 + rate), 2)\n"
            ),
            "calc/tests/__init__.py": "",
            "calc/tests/test_core.py": TAX_TESTS,
        },
    )
    signature = _helper_signature(tmp_path, "calc/tests/test_core.py", MypyInferrer())
    assert signature.endswith("-> None"), "what the sites declare"
