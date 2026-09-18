"""Forked pair workers end when the process that forked them is killed."""

from __future__ import annotations

import os
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
    from towel.unification.parallel import _start_parent_watchdog

    def idle(seconds):
        time.sleep(seconds)

    if __name__ == "__main__":
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
    listing = subprocess.run(["ps", "-axo", "pid=,ppid="], capture_output=True, text=True).stdout
    return [int(row.split()[0]) for row in listing.splitlines() if int(row.split()[1]) == pid]


def _alive(pids: list[int]) -> list[int]:
    listing = subprocess.run(["ps", "-axo", "pid="], capture_output=True, text=True).stdout
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
        deadline = time.monotonic() + 20
        workers: list[int] = []
        while time.monotonic() < deadline and len(workers) < 3:
            time.sleep(0.2)
            workers = _children_of(driver.pid)
        assert len(workers) == 3, f"expected three forked workers, saw {workers}"
    finally:
        driver.kill()
        driver.wait()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and _alive(workers):
        time.sleep(0.2)
    survivors = _alive(workers)
    for pid in survivors:
        os.kill(pid, signal.SIGKILL)
    assert not survivors, f"workers outlived their parent: {survivors}"
