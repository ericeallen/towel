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

"""Pair workers fork only while no other thread runs.

A forked child keeps only the thread that forked it, and every lock in the
state it was in. A default run forked its workers with tqdm's monitor alive
(tqdm starts it with the first bar and never ends it), a directory run with
the progress heartbeat as well, and a typed run with each warm pyright
session's reader. Any of them could be holding a lock at that instant -- above
all the one inside ``sys.stderr``, which every worker takes when it flushes on
exit -- and the worker then waited on it for ever, and the run on the worker.
Now every thread Towel runs is ended for the fork and started again after it,
and a thread that cannot be ended keeps evaluation in the process.

The first test holds stderr's lock across the fork on purpose, in a process of
its own so that a hang is a timeout rather than a suite that never ends.
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import threading
import warnings
from typing import Callable, Iterator, List, Optional

import pytest

import towel
from towel.diagnostics import Settings
from towel.pyright_session import PyrightSession, readers_stopped
from towel.unification import parallel
from towel.unification.parallel import ParallelEvaluation
from towel.unification.progress import Heartbeat, display_threads_stopped
from towel.unification.refactor_engine import UnificationRefactorEngine

pytestmark = pytest.mark.skipif(
    "fork" not in multiprocessing.get_all_start_methods(), reason="needs the fork start method"
)

SHAPES = (
    "def {name}(x):\n    a = x + {index}\n    b = a * 2\n    c = b - 3\n    return c\n",
    "def {name}(items):\n    total = 0\n    for item in items:\n        total += item * {index}\n    return total\n",
)


def _module(path: Path) -> Path:
    """Enough near-identical functions for the pool's ``len // 64`` cap to allow two workers."""
    path.write_text(
        "\n\n".join(
            shape.format(name=f"f{shape_index}_{index}", index=index)
            for shape_index, shape in enumerate(SHAPES)
            for index in range(8)
        )
    )
    return path


def _descriptions(path: Path, workers: int) -> List[str]:
    settings = Settings(
        workers=workers,
        check_ast_immutable=False,
        debug_rejections=False,
        debug_validation=False,
        debug_overlap=False,
        debug_types=False,
    )
    engine = UnificationRefactorEngine(min_lines=3, settings=settings)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return [p.description for p in engine.analyze_files([str(path)], progress="none")]


def _lower_thresholds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ParallelEvaluation, "PARALLEL_PAIR_THRESHOLD", 2)
    monkeypatch.setattr(ParallelEvaluation, "PARALLEL_PROBE_PAIRS", 1)
    monkeypatch.setattr(ParallelEvaluation, "PARALLEL_MIN_PROJECTED_SECONDS", 0.0)


_AT_FORK: List[Callable[[], None]] = []
"""What runs just before each fork of this process while a test has put it here."""

os.register_at_fork(before=lambda: [hook() for hook in list(_AT_FORK)])


@contextlib.contextmanager
def _threads_at_each_fork() -> Iterator[List[List[str]]]:
    """The names of the live threads at each fork made inside the block."""
    seen: List[List[str]] = []

    def record() -> None:
        seen.append(sorted(thread.name for thread in threading.enumerate()))

    _AT_FORK.append(record)
    try:
        yield seen
    finally:
        _AT_FORK.remove(record)


# A language server that answers every request with an empty result and says
# nothing else: all a session needs to start, and to show its reader works.
# Asked for ``towel/split``, it sends half its answer, pauses, then the rest.
_QUIET_SERVER = r"""
import json, sys, time
stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
while True:
    length = 0
    while True:
        line = stdin.readline()
        if not line:
            sys.exit(0)
        if line.strip() == b"":
            break
        name, _, value = line.decode().partition(":")
        if name.lower() == "content-length":
            length = int(value)
    message = json.loads(stdin.read(length))
    if "id" in message and "method" in message:
        body = json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": {}}).encode()
        frame = b"Content-Length: %d\r\n\r\n%s" % (len(body), body)
        if message["method"] == "towel/split":
            # Half a message, then a pause: a reader must not stop inside it.
            stdout.write(frame[: len(frame) // 2])
            stdout.flush()
            time.sleep(float(message["params"]["seconds"]))
            frame = frame[len(frame) // 2 :]
        stdout.write(frame)
        stdout.flush()
    if message.get("method") == "exit":
        sys.exit(0)
"""


@contextlib.contextmanager
def _quiet_session(root: Path) -> Iterator[PyrightSession]:
    server = root / "quiet_server.py"
    server.write_text(_QUIET_SERVER)
    session = PyrightSession([sys.executable, str(server)], root, sys.executable)
    try:
        yield session
    finally:
        session.close()


def _answers(session: PyrightSession, method: str = "towel/ping", **params: object) -> bool:
    reply = session._request(method, params, 5.0)
    return isinstance(reply, dict) and "result" in reply


@contextlib.contextmanager
def _threads_of_a_typed_directory_run(root: Path) -> Iterator[PyrightSession]:
    """What such a run has alive when it reaches pair evaluation for the second time.

    The heartbeat, tqdm's monitor (with an open bar, as the apply bar is) and a
    warm pyright session's reader.
    """
    tqdm = pytest.importorskip("tqdm.auto")
    heartbeat = Heartbeat(lambda: None, period=0.01)
    heartbeat.start()
    bar = tqdm.tqdm(total=1, file=io.StringIO())
    try:
        with _quiet_session(root) as session:
            yield session
    finally:
        bar.close()
        heartbeat.stop()


def _names() -> List[str]:
    return sorted(thread.name for thread in threading.enumerate())


DRIVER = r"""
import io, json, logging, os, sys, threading
from pathlib import Path

from towel.diagnostics import LOG, Settings
from towel.unification.parallel import ParallelEvaluation
from towel.unification.progress import Heartbeat
from towel.unification.refactor_engine import UnificationRefactorEngine


class Gate:
    def __init__(self):
        self.inside = threading.Event()
        self.opened = threading.Event()


class GatedStream(io.RawIOBase):
    '''Standard error's raw stream: with a gate set, a write holds until it opens.

    The buffered writer above it holds its lock for the whole write, so a
    thread held here holds the lock every write and flush of stderr needs.
    '''

    gate = None

    def writable(self):
        return True

    def write(self, data):
        gate = self.gate
        if gate is not None:
            gate.inside.set()
            gate.opened.wait()
        return len(data)


raw = GatedStream()
sys.stderr = io.TextIOWrapper(io.BufferedWriter(raw))
notes = []


class Keep(logging.Handler):
    def emit(self, record):
        notes.append(record.getMessage())


LOG.addHandler(Keep())
LOG.propagate = False


def analyze(workers):
    settings = Settings(workers=workers, check_ast_immutable=False, debug_rejections=False,
                        debug_validation=False, debug_overlap=False, debug_types=False)
    engine = UnificationRefactorEngine(min_lines=3, settings=settings)
    return [p.description for p in engine.analyze_files([sys.argv[1]], progress="none")]


serial = analyze(1)
ParallelEvaluation.PARALLEL_PAIR_THRESHOLD = 2
ParallelEvaluation.PARALLEL_PROBE_PAIRS = 1
ParallelEvaluation.PARALLEL_MIN_PROJECTED_SECONDS = 0.0

forks = []
held_at_fork = []


def before_fork():
    forks.append(sorted(thread.name for thread in threading.enumerate()))
    # Whenever the heartbeat is alive, make it hold stderr's lock across the
    # fork, as a redraw caught mid-write does.
    if any(thread.name == "towel-progress" for thread in threading.enumerate()):
        gate = raw.gate = Gate()
        held_at_fork.append(gate.inside.wait(5))


def after_fork_in_parent():
    gate, raw.gate = raw.gate, None
    if gate is not None:
        gate.opened.set()


os.register_at_fork(before=before_fork, after_in_parent=after_fork_in_parent)

beats = []


def redraw():
    beats.append(1)
    sys.stderr.write(".")
    sys.stderr.flush()


heartbeat = Heartbeat(redraw, period=0.005)
heartbeat.start()
with_heartbeat = analyze(2)
beats_before = len(beats)
threading.Event().wait(0.2)
beats_after = len(beats) - beats_before
heartbeat.stop()
forks_with_heartbeat, forks[:] = list(forks), []

# A thread Towel does not own holds stderr's lock for the whole evaluation.
gate = raw.gate = Gate()
holder = threading.Thread(
    target=lambda: (sys.stderr.write("held"), sys.stderr.flush()), name="r7f-stderr-holder"
)
holder.start()
assert gate.inside.wait(5)
with_holder = analyze(2)
raw.gate = None
gate.opened.set()
holder.join(5)

print(json.dumps({
    "serial": serial,
    "with_heartbeat": with_heartbeat,
    "forks_with_heartbeat": forks_with_heartbeat,
    "held_at_fork": held_at_fork,
    "beats_after": beats_after,
    "with_holder": with_holder,
    "forks_with_holder": forks,
    "notes": notes,
}), file=sys.__stdout__, flush=True)
"""


def test_no_worker_inherits_stderr_held_by_another_thread(tmp_path: Path) -> None:
    """The heartbeat is made to hold stderr's lock at every fork it is alive for.

    Before the fix it was alive for every fork, each worker inherited the lock
    held, and the run hung when the workers flushed on exit. Now it has ended
    before the workers fork and beats again after. A thread that is not
    Towel's and holds the lock throughout keeps evaluation in the process,
    where it waits for nothing, and is named once.
    """
    path = _module(tmp_path / "m.py")
    source_root = str(Path(towel.__file__).resolve().parents[1])
    environment = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([source_root, *filter(None, [os.environ.get("PYTHONPATH")])]),
    }
    run: Optional[subprocess.CompletedProcess[str]] = None
    try:
        run = subprocess.run(
            [sys.executable, "-c", DRIVER, str(path)],
            capture_output=True,
            text=True,
            timeout=60,
            env=environment,
        )
    except subprocess.TimeoutExpired:
        pass
    if run is None:
        pytest.fail("a run whose stderr lock was held at a fork hung")
    assert run.returncode == 0, run.stderr
    report = json.loads(run.stdout.splitlines()[-1])

    assert report["with_heartbeat"] == report["serial"]
    assert report["forks_with_heartbeat"] == [["MainThread"], ["MainThread"]]
    assert report["held_at_fork"] == [], "stderr's lock was held at a fork"
    assert report["beats_after"] > 0, "the heartbeat did not beat again after the fork"

    assert report["forks_with_holder"] == [], "workers forked while stderr's lock was held"
    assert report["with_holder"] == report["serial"]
    notes = [note for note in report["notes"] if "forked workers" in note]
    assert len(notes) == 1 and "r7f-stderr-holder" in notes[0], report["notes"]


def test_workers_fork_while_this_is_the_only_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Heartbeat, tqdm's monitor and a pyright reader are gone at the fork and back after."""
    path = _module(tmp_path / "m.py")
    serial = _descriptions(path, workers=1)
    _lower_thresholds(monkeypatch)
    with _threads_of_a_typed_directory_run(tmp_path) as session:
        before = _names()
        with _threads_at_each_fork() as at_fork:
            forked = _descriptions(path, workers=2)
        after = _names()
        assert _answers(session), "the session's reader did not come back"
    assert at_fork == [[threading.current_thread().name]] * 2, at_fork
    assert {"towel-progress", "tqdm_monitor", "towel-pyright-reader"} <= set(before), before
    assert after == before
    assert forked == serial


@pytest.mark.skipif(
    sys.version_info < (3, 12), reason="CPython warns about forking with threads from 3.12"
)
def test_a_parallel_run_draws_no_warning_about_forking_threads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The warning 3.12 added for forking a process with other threads, recorded rather than raised.

    ``-W error::DeprecationWarning`` cannot see this one: CPython issues it
    in the parent after the fork and discards the exception an error filter
    raises, so under ``error`` the fork passes silently. Recorded under
    ``always``, every such fork leaves a warning to find.
    """
    path = _module(tmp_path / "m.py")
    _lower_thresholds(monkeypatch)
    with _threads_of_a_typed_directory_run(tmp_path):
        with _threads_at_each_fork() as at_fork, warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _descriptions(path, workers=2)
    assert len(at_fork) == 2, "the pool never forked, so this proved nothing"
    forking = [str(w.message) for w in caught if "multi-threaded" in str(w.message)]
    assert forking == []


def test_a_display_thread_that_will_not_stop_keeps_evaluation_here(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A redraw blocked past the wait -- a write to a full pipe -- is waited out, not forked past."""
    path = _module(tmp_path / "m.py")
    serial = _descriptions(path, workers=1)
    _lower_thresholds(monkeypatch)
    # Long enough for the readers of sessions earlier tests left open to stop.
    monkeypatch.setattr(parallel, "THREAD_STOP_SECONDS", 0.5)
    monkeypatch.setattr(parallel, "_SERIAL_NOTED", set())
    released = threading.Event()
    entered = threading.Event()

    def stuck() -> None:
        entered.set()
        released.wait(10)

    heartbeat = Heartbeat(stuck, period=0.001)
    heartbeat.start()
    try:
        assert entered.wait(5)
        with _threads_at_each_fork() as at_fork, caplog.at_level(logging.WARNING, "towel"):
            first = _descriptions(path, workers=2)
            second = _descriptions(path, workers=2)
    finally:
        released.set()
        heartbeat.stop()
    assert at_fork == []
    assert first == second == serial
    notes = [r.getMessage() for r in caplog.records if "forked workers" in r.getMessage()]
    assert len(notes) == 1 and "towel-progress" in notes[0], notes


def test_display_threads_end_for_the_block_and_start_again_after() -> None:
    tqdm = pytest.importorskip("tqdm.auto")
    beats = threading.Semaphore(0)
    heartbeat = Heartbeat(beats.release, period=0.01)
    heartbeat.start()
    bar = tqdm.tqdm(total=1, file=io.StringIO())
    try:
        monitors = [t for t in threading.enumerate() if t.name == "tqdm_monitor"]
        assert monitors, "tqdm started no monitor, so this proves nothing"
        with display_threads_stopped(5.0):
            inside = _names()
        assert "towel-progress" not in inside and "tqdm_monitor" not in inside, inside
        assert not any(monitor.is_alive() for monitor in monitors)
        assert "tqdm_monitor" in _names()
        assert beats.acquire(timeout=5), "the heartbeat did not beat again"
    finally:
        bar.close()
        heartbeat.stop()


def test_a_reader_stops_only_between_messages(tmp_path: Path) -> None:
    """Asked to stop mid-message, the reader finishes it, and the session keeps one reader."""
    with _quiet_session(tmp_path) as session:
        first = session._reader
        with readers_stopped(5.0):
            assert not first.is_alive(), "a reader between messages did not stop"
        assert session._reader is not first and _answers(session)

        replies: List[Optional[bool]] = [None]

        def ask() -> None:
            replies[0] = _answers(session, "towel/split", seconds=0.5)

        pause = threading.Event()
        # The reader has finished the last reply, so the next message it is
        # inside is the one the server splits.
        for _ in range(500):
            if not session._reading:
                break
            pause.wait(0.01)
        asking = threading.Thread(target=ask)
        asking.start()
        try:
            for _ in range(500):
                if session._reading:
                    break
                pause.wait(0.01)
            assert session._reading, "the reader never began the split message"
            reading = session._reader
            with readers_stopped(0.05):
                assert reading.is_alive(), "the reader stopped inside a message"
        finally:
            asking.join(10)
        assert replies == [True]
        # The request was withdrawn, so the reader that finished the message
        # carries on, and no second one was started beside it.
        assert session._reader is reading and reading.is_alive()
        assert _answers(session)
