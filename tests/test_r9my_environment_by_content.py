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

"""An environment is known by what it holds, never by its name (round 4, P2).

A project package called ``env`` or ``venv`` is refactored like any other
(README, "What is analyzed"). The checked copy of an output skipped both names
whatever they held, so once a change to ``env/x.py`` was written every later
check read the original's ``env/x.py``; the second change was accepted against
that stale view, and the cold confirmation, which read the real one, refused
the run (exit 1, nothing written). Every project scan also skipped ``venv`` by
name, so no consumer, namespace write or helper name inside such a package was
seen.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import textwrap
from typing import Dict, List, Mapping, Sequence

import pytest

from towel import consumers
from towel.consumers import scanned_directories
from towel.type_inference import CheckResult, MypyInferrer
from towel.unification.refactor_engine import UnificationRefactorEngine

requires_mypy = pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")


def _write(root: Path, files: Mapping[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")


def test_r9my_scans_enter_a_package_named_venv_and_skip_an_environment_by_its_marker(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        {
            "venv/__init__.py": "",
            "venv/mod.py": "import pkg\n",
            "env/x.py": "import pkg\n",
            "installed/pyvenv.cfg": "home = /usr\n",
            "installed/lib/site.py": "import pkg\n",
            "conda/x.py": "import pkg\n",
            ".venv/lib/site.py": "import pkg\n",
        },
    )
    (tmp_path / "conda" / "conda-meta").mkdir()
    assert scanned_directories(tmp_path, ["venv", "env", "installed", "conda", ".venv"]) == [
        "env",
        "venv",
    ]
    found = sorted(str(path.relative_to(tmp_path)) for path in consumers._python_files(tmp_path))
    assert found == ["env/x.py", "venv/__init__.py", "venv/mod.py"]


KEEPERS = """\
    def keep_int(x: int, n: int):
        pair = (x, n)
        note = repr(pair)
        kept = x
        print(note)
        return kept


    def keep_str(x: str, n: int):
        pair = (x, n)
        note = repr(pair)
        kept = x
        print(note)
        return kept
    """

USER = """\
    from env.x import keep_int


    def one(n: int, m: int):
        print("one")
        got = keep_int(n, m)
        shown = repr(got)
        print(shown)
        return got


    def two(n: int, m: int):
        print("two")
        got = keep_int(n, m)
        shown = repr(got)
        print(shown)
        return got
    """


class _RecordingMypy(MypyInferrer):
    """mypy, keeping every set of sources a project check was given."""

    def __init__(self) -> None:
        super().__init__()
        self.checked: List[Dict[str, str]] = []

    def check_project(
        self, sources: Mapping[str, str], *, excluded_paths: Sequence[str] = ()
    ) -> CheckResult:
        self.checked.append(dict(sources))
        return super().check_project(sources, excluded_paths=excluded_paths)


@requires_mypy
def test_r9my_a_typed_run_checks_a_package_named_env_as_it_now_stands(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "p"\nversion = "0"\n[tool.mypy]\n',
            "env/__init__.py": "",
            "env/x.py": KEEPERS,
            "pkg/__init__.py": "",
            "pkg/y.py": USER,
        },
    )
    oracle = _RecordingMypy()
    engine = UnificationRefactorEngine(min_lines=3, type_oracle=oracle)
    try:
        results, _ = engine.refactor_directory_to_fixed_point(
            str(tmp_path), str(tmp_path), progress="none"
        )
    finally:
        oracle.close()
    assert sorted(Path(path).name for path, (applied, _) in results.items() if applied) == [
        "x.py",
        "y.py",
    ]
    env_module = str((tmp_path / "env" / "x.py").resolve())
    later = [
        {str(Path(path).resolve()): text for path, text in sources.items()}
        for sources in oracle.checked[1:]
    ]
    assert later, "the run checked its candidates"
    assert all(
        env_module in sources for sources in later
    ), "every check of the stage restates env/x.py, as it restates every other module"
    written = (tmp_path / "env" / "x.py").read_text(encoding="utf-8")
    assert "__extracted_func" in written
    assert later[-1][env_module] == written, "the last check saw env/x.py as it was written"
