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

"""Forked pair workers end when the process that forked them is killed."""

from __future__ import annotations

import os
import ctypes
import errno
from functools import lru_cache
from pathlib import Path
import signal
import subprocess
import sys
import textwrap
import time

import multiprocessing

import pytest

DRIVER = textwrap.dedent("""
    import multiprocessing, sys, time
    from concurrent.futures import ProcessPoolExecutor
    from towel.unification import parallel
    from towel.unification.parallel import _start_parent_watchdog

    def idle(seconds):
        time.sleep(seconds)

    if __name__ == "__main__":
        # Workers inherit the module at fork time, so they poll ten times a
        # second here instead of once; the production interval is not under test.
        parallel.PARENT_WATCH_INTERVAL_SECONDS = 0.1
        context = multiprocessing.get_context("fork")
        with ProcessPoolExecutor(
            max_workers=3, mp_context=context, initializer=_start_parent_watchdog
        ) as pool:
            # One worker sleeps as if on a long pair; the others wait on the queue.
            pool.submit(idle, 120)
            print("ready", flush=True)
            time.sleep(120)
    """)


@lru_cache(maxsize=1)
def _libproc() -> ctypes.CDLL:
    library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    library.proc_listchildpids.argtypes = (ctypes.c_int, ctypes.c_void_p, ctypes.c_int)
    library.proc_listchildpids.restype = ctypes.c_int
    return library


def _list_children(pid: int, buffer: ctypes.Array[ctypes.c_int]) -> tuple[int, int]:
    # Unlike proc_listpids, proc_listchildpids returns PID count, not byte count.
    function = _libproc().proc_listchildpids
    ctypes.set_errno(0)
    count = int(function(pid, buffer, ctypes.sizeof(buffer)))
    return count, ctypes.get_errno()


def _macos_children_of(pid: int) -> list[int]:
    capacity = 64
    while capacity <= 65536:
        buffer = (ctypes.c_int * capacity)()
        count, error = _list_children(pid, buffer)
        if count < 0 or (count == 0 and error):
            number = error or errno.EIO
            raise OSError(number, f"Cannot enumerate children of {pid}: {os.strerror(number)}")
        if count < capacity:
            return [child for child in buffer[:count] if child > 0]
        capacity *= 2
    raise RuntimeError(f"Cannot enumerate children of {pid}: more than 65535 child PIDs")


def _children_of(pid: int) -> list[int]:
    if sys.platform == "darwin":
        # libproc uses the parent PID directly and needs no setuid ps executable.
        return _macos_children_of(pid)
    else:
        listing = subprocess.run(
            ["ps", "-axo", "pid=,ppid="], capture_output=True, text=True, check=True
        ).stdout
        return [int(row.split()[0]) for row in listing.splitlines() if int(row.split()[1]) == pid]


def _alive(pids: list[int]) -> list[int]:
    present: list[int] = []
    for pid in pids:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        present.append(pid)
    return present


@pytest.mark.skipif(
    "fork" not in multiprocessing.get_all_start_methods(), reason="needs the fork start method"
)
def test_workers_exit_after_the_parent_is_killed() -> None:
    driver = subprocess.Popen(
        [sys.executable, "-c", DRIVER], stdout=subprocess.PIPE, text=True, cwd=os.getcwd()
    )
    try:
        assert driver.stdout is not None
        assert driver.stdout.readline().strip() == "ready"
        # Deadlines bound a failure, not a success: polling stops as soon as
        # the condition holds, so a fast machine spends milliseconds here.
        deadline = time.monotonic() + 10
        workers: list[int] = []
        while time.monotonic() < deadline and len(workers) < 3:
            time.sleep(0.05)
            workers = _children_of(driver.pid)
        assert len(workers) == 3, f"expected three forked workers, saw {workers}"
    finally:
        driver.kill()
        driver.wait()
    # The watchdog polls every 0.1 s; a worker outliving its parent by two
    # seconds has failed regardless of machine speed.
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and _alive(workers):
        time.sleep(0.05)
    survivors = _alive(workers)
    for pid in survivors:
        os.kill(pid, signal.SIGKILL)
    assert not survivors, f"workers outlived their parent: {survivors}"


def test_native_enumeration_failure_never_reports_an_empty_process_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed(pid: int, buffer: ctypes.Array[ctypes.c_int]) -> tuple[int, int]:
        return 0, errno.EPERM

    monkeypatch.setattr(sys.modules[__name__], "_list_children", failed)
    with pytest.raises(PermissionError, match="Cannot enumerate children"):
        _macos_children_of(os.getpid())


def test_native_enumeration_retries_a_full_buffer_and_refuses_its_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sizes: list[int] = []

    def full(pid: int, buffer: ctypes.Array[ctypes.c_int]) -> tuple[int, int]:
        sizes.append(len(buffer))
        return len(buffer), 0

    monkeypatch.setattr(sys.modules[__name__], "_list_children", full)
    with pytest.raises(RuntimeError, match="more than 65535"):
        _macos_children_of(os.getpid())
    assert sizes == [2**power for power in range(6, 17)]


def test_alive_observer_failure_never_reports_an_empty_process_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed(pid: int, sig: int) -> None:
        raise PermissionError(errno.EPERM, "simulated process observer failure")

    monkeypatch.setattr(os, "kill", failed)
    with pytest.raises(PermissionError, match="simulated process observer failure"):
        _alive([os.getpid()])


def test_portable_enumeration_failure_never_reports_an_empty_process_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Run an owned executable, never the machine's process listing. An empty
    # stdout from a failed observer says nothing about whether workers exist.
    executable = tmp_path / "ps"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        'sys.stderr.write("simulated process observer failure\\n")\n'
        "sys.exit(73)\n"
    )
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(subprocess.CalledProcessError) as failure:
        _children_of(os.getpid())
    assert failure.value.returncode == 73
    assert failure.value.stdout == ""
    assert failure.value.stderr == "simulated process observer failure\n"
