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

"""A typed run's mypy checks build what the project's own mypy run checks, and no more (round 4, P2-4).

Where the configuration names no ``files``, ``packages`` or ``modules``, the
project's run is mypy over what Towel is pointed at, less what ``--exclude``
names; where it names them, it is those. Every check of the run, the baseline
and each candidate's, is built alike from that:

- sqlmodel's ``scripts/`` (no ``__init__.py``) holds a module its tests import
  as ``scripts.tool``. With ``--exclude scripts`` the baseline passed, and
  every candidate's check put ``scripts/tool.py`` back as a module of its own,
  was refused ("found twice under different module names"), and the run
  worked for minutes and exited 1 with every proposal not judged.
- Two documentation examples sharing a module name import the package; the
  consumer scan pulled both into the check of ``towel dry calc``, which was
  refused while ``mypy calc`` passes.
- cookiecutter's Jinja hook scripts under ``tests/**/hooks/`` do not parse and
  its mypy configuration excludes them; the baseline was given them anyway and
  the typed run was refused.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import textwrap
from typing import Mapping, Sequence

import pytest

from towel.type_inference import MypyInferrer
from towel.unification.refactor_engine import UnificationRefactorEngine

requires_mypy = pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")


def _write(root: Path, files: Mapping[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")


def _typed_run(target: Path, excluded: Sequence[str] = ()) -> list[str]:
    """The names of the files a typed run on ``target`` changed, in place."""
    oracle = MypyInferrer()
    engine = UnificationRefactorEngine(
        min_lines=3, type_oracle=oracle, excluded_directories=excluded
    )
    try:
        results, _ = engine.refactor_directory_to_fixed_point(
            str(target), str(target), progress="none"
        )
    finally:
        oracle.close()
    return sorted(Path(path).name for path, (applied, _) in results.items() if applied)


BUMP_TESTS = """\
    from scripts.tool import bump


    def test_bump_minor() -> None:
        result = bump("1.2")
        assert isinstance(result, str)
        assert result.startswith("1.")
        assert result == "1.3"


    def test_bump_zero() -> None:
        result = bump("0.9")
        assert isinstance(result, str)
        assert result.startswith("0.")
        assert result == "0.10"
    """

TAXES = """\
    def with_tax(prices: list[float], rate: float) -> float:
        subtotal = sum(prices)
        taxed = subtotal * (1 + rate)
        rounded = round(taxed, 2)
        return rounded


    def without_tax(prices: list[float], rate: float) -> float:
        subtotal = sum(prices)
        taxed = subtotal * (1 + rate)
        rounded = round(taxed, 2)
        return rounded - taxed + subtotal
    """


@requires_mypy
def test_r9my_the_baseline_and_a_candidate_are_built_from_the_same_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A candidate's check is given every module of the target, restated; the baseline, the analyzed ones."""
    from towel import _mypy_worker as worker

    _write(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "demo"\nversion = "0"\n',
            "tests/__init__.py": "",
            "tests/test_tool.py": BUMP_TESTS,
            "scripts/tool.py": "def bump(version: str) -> str:\n    return version\n",
            "docs/example.py": "import tests.test_tool\n",
        },
    )
    monkeypatch.chdir(tmp_path)
    options = worker._options(tmp_path, None, str(tmp_path / "cache"), probe=False).options
    run = worker._sources_of_the_projects_run(options, tmp_path)
    analyzed = {str(tmp_path / name) for name in ("tests/__init__.py", "tests/test_tool.py")}
    judged = worker._judged_by_the_project(
        options, tmp_path, run, {os.path.realpath(path) for path in analyzed}
    )
    texts = {str(path): path.read_text(encoding="utf-8") for path in tmp_path.rglob("*.py")}
    test = str(tmp_path / "tests" / "test_tool.py")
    consumer = [str(tmp_path / "docs" / "example.py")]

    def built(replacements: Mapping[str, str], given: Mapping[str, str]) -> list[str]:
        sources = worker._build_sources(
            replacements, given, options, tmp_path, True, {}, consumer, run=run, judged=judged
        )
        return sorted(str(source.path) for source in sources)

    baseline = built({path: texts[path] for path in analyzed}, {})
    changed = texts[test] + "\n\nEXTRA: int = 1\n"
    candidate = built({**texts, test: changed}, {test: changed})
    assert baseline == candidate == sorted(analyzed)


@requires_mypy
def test_r9my_candidates_are_checked_without_the_directory_exclude_leaves_out(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "demo"\nversion = "0"\n',
            "tests/__init__.py": "",
            "tests/test_tool.py": BUMP_TESTS,
            "scripts/tool.py": (
                "def bump(version: str) -> str:\n"
                '    major, minor = version.split(".")\n'
                '    return f"{major}.{int(minor) + 1}"\n'
            ),
        },
    )
    assert _typed_run(tmp_path, excluded=("scripts",)) == ["test_tool.py"]


@requires_mypy
def test_r9my_a_consumer_outside_the_target_is_not_built(tmp_path: Path) -> None:
    example = "from calc.core import with_tax\n\nprint(with_tax([1.0, 2.0], 0.1))\n"
    _write(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "calc"\nversion = "0"\n[tool.mypy]\nstrict = true\n',
            "calc/__init__.py": "",
            "calc/core.py": TAXES,
            "docs/one/example.py": example,
            "docs/two/example.py": example,
        },
    )
    assert _typed_run(tmp_path / "calc") == ["core.py"]


@requires_mypy
def test_r9my_a_file_the_configuration_excludes_is_not_built(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "pyproject.toml": (
                '[project]\nname = "calc"\nversion = "0"\n[tool.mypy]\nstrict = true\n'
                'files = ["calc", "tests"]\nexclude = "(?x)(/hooks/)"\n'
            ),
            "calc/__init__.py": "",
            "calc/core.py": TAXES,
            "tests/__init__.py": "",
            "tests/one/hooks/post_gen.py": (
                'import sys\n\n{% if cookiecutter.abort == "yes" %}\nsys.exit(5)\n{% endif %}\n'
            ),
        },
    )
    assert _typed_run(tmp_path) == ["core.py"]
