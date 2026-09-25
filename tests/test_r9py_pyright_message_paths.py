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

"""A pyright message never carries the path of the copy it was checked in.

``reportImportCycles`` lists the modules of the cycle, and pyright names them
where it read them: in Towel's private copy. The original check and the run
check different copies (the run's excludes its stage), and the language
server's messages were not put back as the command line's were, so the error
the project already had read as new after every change, and a change to an
unrelated module was declined for it. The command line gives such an error no
range at all, which was taken for a checker failure.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import List, Optional, Tuple

import pytest

from towel.checker_project import CheckerSnapshot
from towel.type_inference import CheckSuccess, PyrightOracle, Subtyping, TypeDiagnostic

requires_pyright = pytest.mark.skipif(
    importlib.util.find_spec("pyright") is None, reason="pyright absent"
)

_R9PY_CYCLE = {
    "pyrightconfig.json": '{"reportImportCycles": "error"}',
    "pkg/__init__.py": "",
    "pkg/a.py": "from pkg import b\n\n\ndef fa() -> int:\n    return b.fb() + 1\n",
    "pkg/b.py": "from pkg import a\n\n\ndef fb() -> int:\n    return 1\n\n\ndef use() -> int:\n"
    "    return a.fa()\n",
    "pkg/work.py": "def first(values: list[int]) -> int:\n    return sum(values)\n",
}


def _r9py_project(root: Path) -> Path:
    for name, text in _R9PY_CYCLE.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    return root.resolve()


def test_r9py_every_spelling_of_the_copy_is_put_back(tmp_path: Path) -> None:
    project = _r9py_project(tmp_path / "r9py_project")
    snapshot = CheckerSnapshot(project)
    try:
        named, resolved = snapshot.tree, Path(os.path.realpath(snapshot.tree))
        message = f"Cycle detected in import chain\n  {named}/pkg/a.py\n  {resolved}/pkg/b.py"
        assert snapshot.restore_paths(message) == (
            f"Cycle detected in import chain\n  {project}/pkg/a.py\n  {project}/pkg/b.py"
        )
        assert snapshot.original_of(f"{resolved}/pkg/a.py") == str(project / "pkg/a.py")
        assert snapshot.original_of(f"{named}/pkg/a.py") == str(project / "pkg/a.py")
    finally:
        snapshot.close()


def _errors(
    oracle: PyrightOracle, project: Path, excluded: Tuple[str, ...]
) -> List[Tuple[str, str, Optional[int]]]:
    sources = {
        str(project / name): text for name, text in _R9PY_CYCLE.items() if name[-3:] == ".py"
    }
    sources[
        str(project / "pkg/work.py")
    ] += "\n\ndef second(items: list[int]) -> int:\n    return 0\n"
    result = oracle.check_project(sources, excluded_paths=excluded)
    assert isinstance(result, CheckSuccess), result
    return sorted((error.path, error.message, error.line) for error in result.errors)


@requires_pyright
def test_r9py_an_error_naming_files_reads_alike_from_every_copy_and_both_paths(
    tmp_path: Path,
) -> None:
    project = _r9py_project(tmp_path / "r9py_project")
    elsewhere = tmp_path / "r9py_stage"
    elsewhere.mkdir()
    served, command_line = PyrightOracle(), PyrightOracle(language_server=False)
    try:
        before = _errors(served, project, ())
        during = _errors(served, project, (str(elsewhere),))
        cold = _errors(command_line, project, (str(elsewhere),))
    finally:
        served.close()
        command_line.close()
    expected = (
        f"pyright: reportImportCycles: Cycle detected in import chain\n"
        f"  {project}/pkg/a.py\n  {project}/pkg/b.py"
    )
    assert before == [(str(project / "pkg/a.py"), expected, 1)], before
    assert during == before, "the run's own copy reports the error the original check did"
    assert cold == before, "and so does the command line, which gives it no range"


def test_r9py_an_error_without_a_range_is_about_the_file_and_certifies_no_subtype(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """As pyright's command line reports an import cycle: exit 1 and no ``range``."""
    oracle = PyrightOracle.__new__(PyrightOracle)
    oracle._command = ["unused"]
    oracle._server = None
    oracle._warmed = {}
    oracle._probe_copies = {}
    oracle._interpreter = sys.executable
    oracle._search_path = ()
    oracle._owner_pid = os.getpid()
    path = tmp_path / "r9py_m.py"
    path.write_text("x = 1\n", encoding="utf-8")
    cycle = {
        "file": str(path),
        "severity": "error",
        "message": "Cycle",
        "rule": "reportImportCycles",
    }
    payload = json.dumps({"generalDiagnostics": [cycle]})
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=[], returncode=1, stdout=payload, stderr=""
        ),
    )
    try:
        assert oracle.check(str(path), "x = 1\n") == CheckSuccess(
            (TypeDiagnostic(str(path), "pyright: reportImportCycles: Cycle", 1),)
        ), "at the first line, where the language server publishes it"
        assert oracle.is_subtype(str(path), "x = 1\n", [("str", "int")]) == [Subtyping.UNKNOWN]
    finally:
        oracle.close()
