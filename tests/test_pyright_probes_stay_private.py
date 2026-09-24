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

"""Pyright's command-line probes are written only into Towel's private copy of the project.

When the language server cannot be started, or fails, a revealed type or a
subtype question is answered by running pyright on a probed copy of the
module. That copy was written beside the module in the user's tree, as
``_towel_probe_<name>_<random>.py``, and removed afterwards: an out-of-place
run, which promises to only read its input, wrote into it, and a kill between
writing and removing left the file there. The probe now goes into a private
copy of the project that follows it, the same copy a language server watches.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
from typing import Any, List

import pytest

from towel import type_inference
from towel.type_inference import PyrightOracle, RevealRequest, Subtyping
from towel.unification.refactor_engine import UnificationRefactorEngine

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("pyright") is None, reason="pyright absent"
)

SOURCE = (
    "def first(value: int) -> int:\n"
    "    print('first')\n    total = value + 1\n    doubled = total * 2\n"
    "    answer = doubled - 3\n    return answer\n\n\n"
    "def second(value: int) -> int:\n"
    "    print('second')\n    total = value + 1\n    doubled = total * 2\n"
    "    answer = doubled - 4\n    return answer\n"
)


def _project(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[tool.pyright]\ntypeCheckingMode = "basic"\n', encoding="utf-8"
    )
    path = root / "m.py"
    path.write_text(SOURCE, encoding="utf-8")
    return path


def _probes_under(root: Path) -> List[str]:
    return sorted(str(path) for path in root.rglob("_towel_probe_*"))


def _watch_pyright_runs(
    monkeypatch: pytest.MonkeyPatch, project: Path, seen: List[List[str]]
) -> None:
    """Record, at the moment each pyright command runs, what probes sit in the project."""
    real_run = subprocess.run

    def watching(command: List[str], **kwargs: Any) -> "subprocess.CompletedProcess[str]":
        seen.append(_probes_under(project))
        return real_run(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", watching)


def test_command_line_reveals_and_subtypes_write_nothing_into_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _project(tmp_path / "project")
    seen: List[List[str]] = []
    _watch_pyright_runs(monkeypatch, path.parent, seen)
    oracle = PyrightOracle(language_server=False)
    try:
        request = RevealRequest(str(path), SOURCE, 3, "    ", ("value + 1",))
        revealed = oracle.reveal([request])
        verdicts = oracle.is_subtype(str(path), SOURCE, [("bool", "int"), ("str", "int")])
    finally:
        oracle.close()
    assert revealed == {(str(path), 3, 0): "int"}
    assert list(verdicts) == [Subtyping.YES, Subtyping.NO]
    assert seen and all(probes == [] for probes in seen), seen
    assert sorted(p.name for p in path.parent.iterdir()) == ["m.py", "pyproject.toml"]


def test_an_out_of_place_run_without_a_language_server_leaves_its_input_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _project(tmp_path / "project")
    monkeypatch.setattr(type_inference, "_pyright_langserver_command", lambda: None)
    seen: List[List[str]] = []
    _watch_pyright_runs(monkeypatch, path.parent, seen)
    oracle = PyrightOracle()
    output = tmp_path / "out"
    try:
        engine = UnificationRefactorEngine(
            min_lines=3, reuse_existing_functions=False, type_oracle=oracle
        )
        with contextlib.redirect_stdout(io.StringIO()):
            results, _ = engine.refactor_directory_to_fixed_point(
                str(path.parent), str(output), progress="none"
            )
    finally:
        oracle.close()
    assert sum(count for count, _ in results.values()) == 1
    assert seen and all(probes == [] for probes in seen), seen
    assert path.read_text(encoding="utf-8") == SOURCE
