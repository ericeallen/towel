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

"""Built-in repeatability is explicit; wrappers never certify custom callbacks."""

from pathlib import Path
from typing import Sequence
import gc
import subprocess
import weakref

import pytest

from towel import formatting


def test_actual_ruff_formatter_and_composite_finisher_are_registered(tmp_path: Path) -> None:
    pytest.importorskip("ruff")
    path = tmp_path / "m.py"
    path.write_text("VALUE = 0\n")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="fixture"\nversion="0"\n[tool.ruff.lint]\nselect=["F","I"]\n'
    )
    snippet = formatting.ruff_formatter(path)
    finisher = formatting.file_finisher_for_project(path).tool
    assert snippet("x=1") == "x = 1"
    assert finisher is not None and finisher(str(path), "VALUE = 0\n") == "VALUE = 0\n"
    capability = formatting.formatting_repeatability(snippet)
    assert capability is not None and capability.roots == (tmp_path,)
    assert formatting.formatting_repeatability(finisher) is not None


def test_black_worker_close_invalidates_and_registry_does_not_own_callback() -> None:
    pytest.importorskip("black")
    snippet = formatting.black_formatter(formatting.BlackSettings())
    assert snippet("x=1") == "x = 1"
    capability = formatting.formatting_repeatability(snippet)
    assert capability is not None and capability.workers
    tool = capability.workers[0]()
    assert tool is not None
    tool.close()
    assert formatting.formatting_repeatability(snippet) is None
    reference = weakref.ref(snippet)
    del snippet
    gc.collect()
    assert reference() is None


def test_checked_and_sorted_wrappers_do_not_certify_custom_functions() -> None:
    assert formatting.formatting_repeatability(formatting.checked(lambda source: source)) is None
    custom = formatting.sorted_where_already_sorted(lambda path, source: source, "custom")
    assert formatting.formatting_repeatability(custom) is None


def test_sorter_tool_failure_invalidates_all_composite_capabilities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("ruff")
    path = tmp_path / "m.py"
    path.write_text("VALUE = 0\n")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="fixture"\nversion="0"\n[tool.ruff.lint]\nselect=["F","I"]\n'
    )
    finisher = formatting.file_finisher_for_project(path).tool
    assert finisher is not None and formatting.formatting_repeatability(finisher) is not None

    def run(argv: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[Sequence[str]]:
        # The sort fails; the subsequent configured lint check completes cleanly.
        return subprocess.CompletedProcess(
            argv, 1 if "--select" in argv else 0, "" if "--select" in argv else "[]", ""
        )

    monkeypatch.setattr(subprocess, "run", run)
    assert finisher(str(path), path.read_text()) == path.read_text()
    assert formatting.formatting_repeatability(finisher) is None


def test_sorter_owns_its_command_selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    command = ["original-ruff"]
    sort = formatting._ruff_sorter(command, tmp_path)
    command[0] = "changed-command"
    actual: list[str] = []

    def run(argv: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[Sequence[str]]:
        actual.extend(argv)
        source = kwargs["input"]
        assert isinstance(source, str)
        return subprocess.CompletedProcess(argv, 0, source, "")

    monkeypatch.setattr(subprocess, "run", run)
    assert sort(str(tmp_path / "m.py"), "VALUE = 0\n") == "VALUE = 0\n"
    assert actual[0] == "original-ruff"
    capability = formatting.formatting_repeatability(sort)
    assert capability is not None and "original-ruff" in capability.selection
