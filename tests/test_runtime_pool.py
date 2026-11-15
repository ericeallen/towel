# Copyright 2026 Eric Allen
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

"""R1/R2: real pools unwind promptly, including a worker killed mid-chunk."""

import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

DRIVER = r"""
import os, sys, time
from pathlib import Path
from towel import cli
from towel.unification import parallel
from towel.unification.parallel import ParallelEvaluation
marker = Path(sys.argv[1])
mode = sys.argv[2]
source = sys.argv[3]
original = parallel._evaluate_pair_chunk

def chunk(bounds):
    (marker / str(os.getpid())).touch()
    if mode == 'death':
        os.kill(os.getpid(), 9)
    time.sleep(30)
    return original(bounds)
parallel._evaluate_pair_chunk = chunk
ParallelEvaluation.PARALLEL_PAIR_THRESHOLD = 2
ParallelEvaluation.PARALLEL_PROBE_PAIRS = 1
ParallelEvaluation.PARALLEL_MIN_PROJECTED_SECONDS = 0
sys.argv = ['towel', 'preview', source, '--no-types', '--no-format', '--summary', '--progress', 'none']
if mode == 'deadline':
    sys.argv += ['--time-limit', '1']
cli.main()
"""


@pytest.mark.parametrize("mode,status", [("deadline", 124), ("int", 130), ("term", 143)])
def test_pool_cancellation_reaps_workers(tmp_path: Path, mode: str, status: int) -> None:
    """R1: deadline/SIGINT/SIGTERM inside a chunk exits within two seconds and reaps children."""
    run, pids, elapsed, output = _run(tmp_path, mode)
    assert run == status, output
    assert pids, "the cancellation must reach the worker phase"
    assert elapsed < (3 if mode == "deadline" else 2), output
    assert all(not _alive(pid) for pid in pids), (pids, output)
    if mode == "int":
        assert "Interrupted." in output


@pytest.mark.parametrize("repeat", range(5))
def test_dead_worker_never_hangs(tmp_path: Path, repeat: int) -> None:
    """R2: five independent killed-worker runs finish visibly, without an orphan."""
    status, pids, elapsed, output = _run(tmp_path, "death")
    assert status == 0, output
    assert pids
    assert "finishing serially" in output
    assert all(not _alive(pid) for pid in pids), (pids, output)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _run(tmp_path: Path, mode: str) -> tuple[int, list[int], float, str]:
    source = tmp_path / "input.py"
    source.write_text(
        "\n".join(
            f"def f{i}(x):\n    a = x + {i}\n    b = a * 2\n    c = b - 3\n    return c\n"
            for i in range(16)
        )
    )
    markers = tmp_path / "workers"
    markers.mkdir()
    started = time.monotonic()
    process = subprocess.Popen(
        [sys.executable, "-c", DRIVER, str(markers), mode, str(source)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env={**os.environ, "TOWEL_WORKERS": "2"},
        start_new_session=True,
    )
    try:
        if mode in {"int", "term"}:
            end = started + 5
            while not list(markers.iterdir()) and process.poll() is None and time.monotonic() < end:
                time.sleep(0.01)
            started = time.monotonic()
            process.send_signal(signal.SIGINT if mode == "int" else signal.SIGTERM)
        try:
            output, _ = process.communicate(timeout=4 if mode != "death" else 20)
        except subprocess.TimeoutExpired:
            pytest.fail(f"{mode}: pool did not exit after cancellation")
        return (
            process.returncode,
            [int(p.name) for p in markers.iterdir()],
            time.monotonic() - started,
            output,
        )
    finally:
        # This group contains only the subprocess and workers started by this test.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate(timeout=5)
