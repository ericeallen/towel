# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Verify the RSS instrument against touched child memory and native failures."""

import ctypes
import errno
import os
import select
import subprocess
import sys
from typing import IO

import pytest

from tests.differential import process_memory

ALLOCATION = 32 * 1024 * 1024


def _line(pipe: IO[str]) -> str:
    ready, _, _ = select.select([pipe], [], [], 10)
    assert ready, "The memory fixture did not report its next state"
    line = pipe.readline()
    assert line, "The memory fixture exited without reporting its next state"
    return line.strip()


@pytest.mark.skipif(sys.platform not in {"darwin", "linux"}, reason="supported RSS platforms")
def test_rss_tracks_touched_child_memory_and_an_exited_child() -> None:
    """An allocation and the child's independent peak counter corroborate live RSS."""
    script = (
        "import resource, sys\n"
        "print('ready', flush=True)\n"
        "sys.stdin.readline()\n"
        f"allocation = bytearray({ALLOCATION})\n"
        "for offset in range(0, len(allocation), 4096):\n"
        "    allocation[offset] = (offset // 4096) % 255 + 1\n"
        "peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss\n"
        "print(int(peak if sys.platform == 'darwin' else peak * 1024), flush=True)\n"
        "sys.stdin.readline()\n"
    )
    with subprocess.Popen(
        [sys.executable, "-I", "-u", "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as child:
        assert child.stdin is not None and child.stdout is not None
        try:
            assert _line(child.stdout) == "ready"
            before = process_memory.resident_bytes(child.pid)
            assert before > 0
            child.stdin.write("allocate\n")
            child.stdin.flush()
            peak = int(_line(child.stdout))
            allocated = process_memory.resident_bytes(child.pid)
            assert allocated >= before + ALLOCATION // 2, (before, allocated, peak)
            assert allocated <= peak + 8 * 1024 * 1024, (before, allocated, peak)
            child.stdin.write("exit\n")
            child.stdin.flush()
            assert child.wait(timeout=10) == 0
            assert process_memory.resident_bytes(child.pid) == 0
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS native task information")
def test_macos_measurement_does_not_launch_a_process(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("RSS measurement must not spawn ps or another helper")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert process_memory.resident_bytes(os.getpid()) > 0


@pytest.mark.parametrize("number", [errno.EPERM, errno.EACCES, errno.EINVAL, 0])
def test_native_failure_is_not_reported_as_zero_memory(
    monkeypatch: pytest.MonkeyPatch, number: int
) -> None:
    monkeypatch.setattr(process_memory, "_task_info", lambda pid, info: (0, number))
    with pytest.raises(OSError) as raised:
        process_memory._macos_resident_bytes(123)
    assert raised.value.errno == (number or errno.EIO)


def test_only_native_missing_process_is_zero_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_memory, "_task_info", lambda pid, info: (0, errno.ESRCH))
    assert process_memory._macos_resident_bytes(123) == 0


def test_partial_native_record_is_not_trusted(monkeypatch: pytest.MonkeyPatch) -> None:
    size = ctypes.sizeof(process_memory._TaskInfo)

    def partial(pid: int, info: process_memory._TaskInfo) -> tuple[int, int]:
        info.pti_resident_size = 42
        return size - 1, 0

    monkeypatch.setattr(process_memory, "_task_info", partial)
    with pytest.raises(OSError, match=f"returned {size - 1} of {size} bytes"):
        process_memory._macos_resident_bytes(123)
