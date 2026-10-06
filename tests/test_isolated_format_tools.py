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

"""Fresh entries and persistent tool calls cannot execute project-owned imports."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import gc
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import types
import weakref

import pytest

from towel.formatting import BlackSettings, black_formatter
from towel.project_tools import IsolatedFormatTool, ToolFailure

DUPLICATES = """def one(value):
    x = value + 1
    y = x * 2
    z = y - 3
    return x, y, z

def two(value):
    x = value + 1
    y = x * 2
    z = y - 3
    return x, y, z + 7
"""


@pytest.mark.parametrize("entry", ["module", "console"])
@pytest.mark.parametrize("configuration", ["ruff", "black", "isort"])
def test_fresh_entry_ignores_project_tools_and_their_dependencies(
    tmp_path: Path, entry: str, configuration: str
) -> None:
    """Cwd and PYTHONPATH expose benign traps only to the parent, never to tool discovery/calls."""
    pytest.importorskip(configuration)
    project = tmp_path / "project"
    project.mkdir()
    settings = {
        "ruff": "[tool.ruff]\nline-length = 88\n",
        "black": "[tool.black]\n",
        "isort": '[tool.isort]\nprofile = "black"\n',
    }
    (project / "pyproject.toml").write_text(settings[configuration])
    sentinel = project / "executed.txt"
    for name in ("ruff", "black", "isort", "click", "blackd"):
        (project / (name + ".py")).write_text(
            "from pathlib import Path\n"
            f"Path({str(sentinel)!r}).write_text('project code executed')\n"
            "raise RuntimeError('project tool shadow')\n"
        )
    source = project / "input.py"
    source.write_text(DUPLICATES)
    output = project / "output.py"
    launch = (
        [sys.executable, "-m", "towel.cli"]
        if entry == "module"
        else [str(Path(sys.executable).with_name("towel"))]
    )
    package = Path(__file__).resolve().parents[1] / "src"
    completed = subprocess.run(
        [
            *launch,
            "dry",
            str(source),
            str(output),
            "--no-types",
            "--no-interactive",
            "--progress",
            "none",
        ],
        cwd=project,
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join((str(package), str(project))),
            "TOWEL_WORKERS": "1",
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert not sentinel.exists()
    assert "__extracted_func_" in output.read_text()
    assert source.read_text() == DUPLICATES
    before: dict[str, object] = {}
    after: dict[str, object] = {}
    exec(DUPLICATES, before)
    exec(output.read_text(), after)
    for name in ("one", "two"):
        original, refactored = before[name], after[name]
        assert callable(original) and callable(refactored)
        assert original(5) == refactored(5)


def test_preloaded_rogue_tools_do_not_change_or_leak_to_concurrent_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even lazy/transitive imports remain isolated; no caller module/path mutation is needed."""
    pytest.importorskip("black")
    originals = {}
    for name in ("black", "click", "isort", "black.ranges"):
        module = types.ModuleType(name)
        module.__file__ = str(tmp_path / (name + ".py"))
        originals[name] = module
        monkeypatch.setitem(sys.modules, name, module)
    paths = sys.path
    formatter = black_formatter(BlackSettings(line_length=60))
    inputs = [f"value={{'x': {index}, 'y': lambda x: x+1}}" for index in range(12)]
    with ThreadPoolExecutor(max_workers=4) as threads:
        outputs = list(threads.map(formatter, inputs))
    assert all('"x"' in text and "lambda x: x + 1" in text for text in outputs)
    assert sys.path is paths
    assert all(sys.modules[name] is module for name, module in originals.items())


def test_tool_failure_does_not_poison_the_next_valid_operation() -> None:
    tool = IsolatedFormatTool("black")
    try:
        with pytest.raises(ToolFailure):
            tool.render({"source": "invalid = (:", "line_length": 88, "string_normalization": True})
        assert (
            tool.render({"source": "value=1", "line_length": 88, "string_normalization": True})
            == "value = 1\n"
        )
    finally:
        tool.close()
    assert tool._process.poll() is not None


def test_disposal_closes_the_owned_process() -> None:
    tool = IsolatedFormatTool("black")
    process = tool._process
    reference = weakref.ref(tool)
    del tool
    gc.collect()
    assert reference() is None
    assert process.wait(timeout=3) == 0


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork")
def test_forked_caller_cannot_use_or_stop_the_parents_tool() -> None:
    tool = IsolatedFormatTool("black")
    pid = os.fork()
    if pid == 0:
        try:
            tool.close()
            try:
                tool.render({"source": "value=1", "line_length": 88, "string_normalization": True})
            except ToolFailure:
                os._exit(0)
            os._exit(1)
        except BaseException:
            os._exit(2)
    try:
        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0
        assert tool._process.poll() is None
        assert (
            tool.render({"source": "value=1", "line_length": 88, "string_normalization": True})
            == "value = 1\n"
        )
    finally:
        tool.close()


def test_eof_and_timeout_cannot_return_unchanged_text() -> None:
    tool = IsolatedFormatTool("black", timeout=0.2)
    tool._process.kill()
    tool._process.wait()
    with pytest.raises(ToolFailure):
        tool.render({"source": "value=1", "line_length": 88, "string_normalization": True})
    assert tool._closed
    stalled = IsolatedFormatTool("black", timeout=0.2)
    os.kill(stalled._process.pid, signal.SIGSTOP)
    with pytest.raises(ToolFailure, match="timed out"):
        stalled.render({"source": "value=1", "line_length": 88, "string_normalization": True})
    assert stalled._closed and stalled._process.poll() is not None


def test_oversized_request_is_rejected_and_closes_the_worker() -> None:
    from towel._formatting_worker import MAX_FRAME_BYTES

    tool = IsolatedFormatTool("black")
    with pytest.raises(ToolFailure, match="byte limit"):
        tool.render(
            {"source": "x" * MAX_FRAME_BYTES, "line_length": 88, "string_normalization": True}
        )
    assert tool._closed and tool._process.poll() is not None


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork")
def test_abrupt_parent_death_closes_tool_with_an_inherited_writer() -> None:
    script = """
import os, time
from towel.project_tools import IsolatedFormatTool
tool = IsolatedFormatTool("black")
child = os.fork()
if child == 0:
    os.close(1)
    time.sleep(3)
    os._exit(0)
print(tool._process.pid, child, flush=True)
os._exit(0)
"""
    owner = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    assert owner.stdout is not None
    worker, holder = map(int, owner.stdout.readline().split())
    assert owner.wait(timeout=3) == 0
    owner.stdout.close()
    try:
        deadline = time.monotonic() + 2
        while True:
            try:
                os.kill(worker, 0)
            except ProcessLookupError:
                break
            assert (
                time.monotonic() < deadline
            ), "tool survived its parent while its pipe stayed open"
            time.sleep(0.05)
        os.kill(holder, 0)  # The inherited stdin writer is still alive.
    finally:
        try:
            os.kill(holder, signal.SIGTERM)
        except ProcessLookupError:
            pass
