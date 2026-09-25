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


def _children_of(pid: int) -> list[int]:
    listing = subprocess.run(
        ["ps", "-axo", "pid=,ppid="], capture_output=True, text=True, check=True
    ).stdout
    return [int(row.split()[0]) for row in listing.splitlines() if int(row.split()[1]) == pid]


def _alive(pids: list[int]) -> list[int]:
    listing = subprocess.run(
        ["ps", "-axo", "pid="], capture_output=True, text=True, check=True
    ).stdout
    present = {int(row) for row in listing.split()}
    return [pid for pid in pids if pid in present]


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


@pytest.mark.parametrize("observer", ["children", "alive"])
def test_process_observer_failure_never_reports_an_empty_process_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, observer: str
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
    with pytest.raises(subprocess.CalledProcessError) as failure:
        if observer == "children":
            _children_of(os.getpid())
        else:
            _alive([os.getpid()])
    assert failure.value.returncode == 73
    assert failure.value.stdout == ""
    assert failure.value.stderr == "simulated process observer failure\n"
