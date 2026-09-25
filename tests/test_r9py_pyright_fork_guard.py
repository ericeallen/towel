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

"""A pyright oracle's servers and private copies belong to the process that made it.

A forked child inherits the servers' pipes and the copies' paths but not the
servers, and ``Popen.poll`` there reports a process it did not start as gone.
A child that checked with the oracle, or only closed it, abandoned the parent's
sessions and removed the copies they watch, and the parent's next check of a
candidate that broke a consumer came back clean. ``MypyInferrer`` has always
refused in a child; the pyright oracle now does too, and removes nothing there.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from typing import Dict, List, Mapping, Sequence
import warnings

import pytest

import towel
from towel.checker_project import CheckerSnapshot
from towel.pyright_session import Diagnostic, FileChange, SessionFailure
from towel.type_inference import (
    CheckFailure,
    PyrightOracle,
    RevealRequest,
    _WarmProject,
    unanswered_files,
)

pytestmark = [
    pytest.mark.skipif(importlib.util.find_spec("pyright") is None, reason="pyright absent"),
    pytest.mark.skipif(not hasattr(os, "fork"), reason="no fork on this platform"),
]


class _R9pyDeadSession:
    """Stands in for a language server that a child would see as exited."""

    def diagnostics_after(
        self, changed: Mapping[Path, FileChange], *, beside: Sequence[Path] = ()
    ) -> Dict[str, List[Diagnostic]]:
        raise SessionFailure("pyright exited")

    def close(self) -> None:
        pass


def _r9py_project(root: Path) -> Path:
    (root / "pkg").mkdir(parents=True)
    (root / "pyrightconfig.json").write_text("{}\n", encoding="utf-8")
    (root / "pkg/__init__.py").write_text("", encoding="utf-8")
    (root / "pkg/a.py").write_text("def f(x: int) -> int:\n    return x\n", encoding="utf-8")
    return root.resolve()


def _in_a_child(oracle: PyrightOracle, module: Path) -> List[object]:
    """What a forked child is told when it checks, probes and closes, read back in the parent."""
    read, write = os.pipe()
    with warnings.catch_warnings():
        # A process with threads warns that a fork may deadlock; the child
        # below takes no lock and leaves through ``os._exit``.
        warnings.simplefilter("ignore", DeprecationWarning)
        pid = os.fork()
    if pid == 0:  # pragma: no cover - runs in the child, which reports through the pipe
        try:
            checked = oracle.check_project({str(module): "def f(x: int) -> str:\n    return ''\n"})
            subtypes = oracle.is_subtype(str(module), module.read_text(), [("int", "object")])
            text = module.read_text(encoding="utf-8")
            revealed = oracle.reveal([RevealRequest(str(module), text, 2, "    ", ("x",))])
            oracle.stop_language_servers()
            oracle.close()
            report = [
                checked.reason if isinstance(checked, CheckFailure) else repr(checked),
                [verdict.name for verdict in subtypes],
                sorted(unanswered_files(revealed).values()),
            ]
            os.write(write, json.dumps(report).encode("utf-8"))
        finally:
            os._exit(0)
    os.close(write)
    os.waitpid(pid, 0)
    with os.fdopen(read, "rb") as reader:
        return list(json.loads(reader.read()))


def test_r9py_a_forked_child_asks_nothing_and_removes_none_of_the_parents_copies(
    tmp_path: Path,
) -> None:
    project = _r9py_project(tmp_path / "r9py_project")
    module = project / "pkg/a.py"
    oracle = PyrightOracle(language_server=False)
    warm_copy = CheckerSnapshot(project)
    oracle._warmed[(project, ())] = _WarmProject(warm_copy, _R9pyDeadSession())  # type: ignore[arg-type]
    probe_copy = oracle._probe_copy(project)
    assert isinstance(probe_copy, CheckerSnapshot)
    try:
        report = _in_a_child(oracle, module)
        refused = "Create a new pyright oracle after fork"
        assert report == [refused, ["UNKNOWN"], [refused]]
        assert warm_copy.tree.is_dir(), "the watched copy is still there for the parent"
        assert probe_copy.tree.is_dir(), "so is the copy the command line probes in"
        assert oracle._warmed and oracle._probe_copies, "and the parent still knows them"
    finally:
        oracle.close()
    assert not warm_copy.tree.exists() and not probe_copy.tree.exists(), "the parent removes them"


_R9PY_EXITING_CHILD = textwrap.dedent("""
    import os, sys, warnings
    from pathlib import Path
    from towel.checker_project import CheckerSnapshot

    snapshot = CheckerSnapshot(Path(sys.argv[1]))
    warnings.simplefilter("ignore", DeprecationWarning)
    if os.fork() == 0:
        del snapshot  # collected in the child, and then the child exits as a program does
        sys.exit(0)
    os.wait()
    print(snapshot.tree.is_dir())
    snapshot.close()
    print(snapshot.tree.exists())
    """)


def test_r9py_a_forked_child_that_exits_leaves_the_parents_copy(tmp_path: Path) -> None:
    """A copy is removed when collected and at exit, and a child inherits both."""
    project = _r9py_project(tmp_path / "r9py_project")
    environment = {**os.environ, "PYTHONPATH": str(Path(towel.__file__).resolve().parents[1])}
    completed = subprocess.run(
        [sys.executable, "-c", _R9PY_EXITING_CHILD, str(project)],
        capture_output=True,
        text=True,
        check=True,
        env=environment,
        timeout=60,
    )
    assert completed.stdout.split() == ["True", "False"], completed
