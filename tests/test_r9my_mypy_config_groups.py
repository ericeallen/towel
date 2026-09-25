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

"""Each mypy configuration group checks what its own run checks, with the whole candidate (round 4, P2).

The mypy worker kept one record of supplied text per cache, and one cache per
oracle, shared by every configuration group. A sub-project's candidate texts
were then restored into the root group's later builds and checked under the
root's configuration, which the baseline had never done:

- an unrelated root-configuration error in the sub-project declined every
  change there, while the same project without a root-group file applied it;
- a test of the root group importing the sub-project did not resolve at the
  baseline and resolved later, so the cold confirmation refused the run as
  "a defect in Towel's verification" (exit 1), though the root's own
  ``mypy .`` is clean.

Now each group builds in a cache of its own, over every file its own run
checks (a configuration covers the directories below it, a nested
configuration's included, as ``mypy`` run from it does), and reads another
group's changed file with the candidate's text.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import textwrap
from typing import Mapping

import pytest

from towel.type_inference import CheckFailure, MypyInferrer
from towel.unification.refactor_engine import UnificationRefactorEngine

requires_mypy = pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")


def _write(root: Path, files: Mapping[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")


def _typed_run(root: Path) -> UnificationRefactorEngine:
    oracle = MypyInferrer()
    engine = UnificationRefactorEngine(min_lines=3, type_oracle=oracle)
    try:
        engine.refactor_directory_to_fixed_point(str(root), str(root), progress="none")
    finally:
        oracle.close()
    return engine


SUB_MODULE = """\
    def legacy(x):
        return x


    def first(values: list[int], scale: int) -> str:
        total = 0
        for v in values:
            total += v * scale
        label = f"sum={total}"
        return label.upper()


    def second(items: list[int], factor: int) -> str:
        total = 0
        for v in items:
            total += v * factor
        label = f"sum={total}"
        return label.lower()
    """


@requires_mypy
def test_r9my_a_root_configuration_error_does_not_decline_a_sub_project_change(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "root"\nversion = "0.1"\n'
            "[tool.mypy]\ndisallow_untyped_defs = true\n",
            "app/__init__.py": "",
            "app/core.py": "def ok(x: int) -> int:\n    return x + 1\n",
            "sub/pyproject.toml": '[project]\nname = "sub"\nversion = "0.1"\n'
            "[tool.mypy]\nwarn_unused_configs = true\n",
            "sub/subpkg/__init__.py": "",
            "sub/subpkg/mod.py": SUB_MODULE,
        },
    )
    engine = _typed_run(tmp_path)
    assert engine.run_report.declined_proposals == {}
    assert "__extracted_func_0" in (tmp_path / "sub" / "subpkg" / "mod.py").read_text()


HOLDER = """\
    from subpkg.helpers import make_od


    class Holder:
        def log(self, n: int) -> None:
            print("count", n)

        def first(self, n: int) -> None:
            od = make_od(n)
            print("first")
            self.data = od
            self.count = len(od)
            self.log(self.count)

        def second(self, n: int) -> None:
            od = make_od(n + 1)
            print("second")
            self.data = od
            self.count = len(od)
            self.log(self.count)
    """

CONSUMER = """\
    import collections

    from subpkg.mod import Holder


    def data_of(h: Holder) -> "collections.OrderedDict[str, int]":
        return h.data


    def test_data() -> None:
        h = Holder()
        h.first(3)
        assert data_of(h)["a"] == 3
    """


@requires_mypy
def test_r9my_a_cross_group_import_leaves_the_cold_confirmation_nothing_to_refuse(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "root"\nversion = "0.1"\n[tool.mypy]\nstrict = true\n',
            "tests/test_mod.py": CONSUMER,
            "sub/pyproject.toml": '[project]\nname = "subpkg"\nversion = "0.1"\n'
            "[tool.mypy]\nstrict = true\n",
            "sub/subpkg/__init__.py": "",
            "sub/subpkg/helpers.py": (
                "import collections\n\n\n"
                'def make_od(n: int) -> "collections.OrderedDict[str, int]":\n'
                "    return collections.OrderedDict(a=n)\n"
            ),
            "sub/subpkg/mod.py": HOLDER,
        },
    )
    engine = _typed_run(tmp_path)  # raised: the finished project reports 1 type error(s)
    # The root's own ``mypy .`` checks the test against the change and rejects
    # it (``h.data`` would be ``Any``); the run's checks now see that too.
    assert engine.run_report.declined_proposals == {"refused by the type checker": 1}


@requires_mypy
def test_r9my_a_group_reads_another_groups_change_with_the_candidates_text(
    tmp_path: Path,
) -> None:
    """The sub-project reaches the root's ``app`` through its ``mypy_path``; the root leaves ``sub`` out.

    Only the sub-project's check can see its module broken, and only by reading
    ``app/core.py`` as the candidate writes it: its own build read the file on
    disk, and called a change that breaks ``TOTAL`` clean.
    """
    _write(
        tmp_path,
        {
            "pyproject.toml": '[tool.mypy]\nexclude = ["^sub/"]\n',
            "app/__init__.py": "",
            "app/core.py": "def size() -> int:\n    return 1\n",
            "sub/pyproject.toml": '[tool.mypy]\nmypy_path = ".."\n',
            "sub/subpkg/__init__.py": "",
            "sub/subpkg/mod.py": "from app.core import size\n\nTOTAL: int = size()\n",
        },
    )
    core, mod = tmp_path / "app" / "core.py", tmp_path / "sub" / "subpkg" / "mod.py"
    oracle = MypyInferrer()
    try:
        before = oracle.check_project({str(core): core.read_text(), str(mod): mod.read_text()})
        after = oracle.check_project(
            {str(core): "def size() -> str:\n    return 'x'\n", str(mod): mod.read_text()}
        )
    finally:
        oracle.close()
    assert not isinstance(before, CheckFailure) and before.errors == (), before
    assert not isinstance(after, CheckFailure), after
    assert [(Path(error.path).name, error.line) for error in after.errors] == [("mod.py", 3)]
