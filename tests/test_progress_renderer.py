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


"""Audit finding 7: transient progress must not become scrolling output."""

import contextlib
import io
import os
import pty
import select
import struct
import termios
import fcntl
from typing import Iterator

import pytest

from towel.unification import progress
from towel.unification.progress import ProgressRenderer


@contextlib.contextmanager
def terminal(columns: int = 40) -> Iterator[tuple[io.TextIOWrapper, int]]:
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, columns, 0, 0))
    with os.fdopen(slave, "w", buffering=1) as stream:
        try:
            yield stream, master
        finally:
            os.close(master)


def read_terminal(master: int) -> str:
    chunks = []
    while select.select([master], [], [], 0)[0]:
        chunks.append(os.read(master, 65536))
    return b"".join(chunks).decode()


@pytest.mark.parametrize("mode", ["auto", "tqdm", "none", "detail"])
def test_pipe_has_no_bar(mode: progress.ProgressMode) -> None:
    """Audit 7: redirected stderr never contains carriage-return updates."""
    output = io.StringIO()
    with contextlib.redirect_stderr(output):
        renderer = ProgressRenderer(mode, total=3, desc="analyze", unit="file")
        renderer.update()
        renderer.close()
    assert output.getvalue() == ""


def test_terminal_line_fits_and_finishes_without_scrolling(monkeypatch: pytest.MonkeyPatch) -> None:
    """Audit 7: three phases leave only the final result on a narrow terminal."""
    monkeypatch.setattr(progress, "load_tqdm", lambda: None)
    with terminal(32) as (stream, master), contextlib.redirect_stderr(stream):
        for name in ["analyze", "pair", "apply"]:
            renderer = ProgressRenderer("auto", total=3, desc=name, unit="file")
            renderer.set_postfix({"path": "long/" * 40})
            renderer.update(3)
            renderer.close()
        print("Result: 3 refactorings", file=stream, flush=True)
        output = read_terminal(master)
    assert output.count("\n") == 1
    assert output.endswith("\r\x1b[2KResult: 3 refactorings\r\n")
    for update in output.split("\r\x1b[2K")[:-1]:
        assert len(update) <= 31


def test_nested_phases_restore_outer_display(monkeypatch: pytest.MonkeyPatch) -> None:
    """Audit 7: local analysis and the apply heartbeat share one line."""
    monkeypatch.setattr(progress, "load_tqdm", lambda: None)
    with terminal() as (stream, master), contextlib.redirect_stderr(stream):
        outer = ProgressRenderer("auto", total=None, desc="apply", unit="it")
        inner = ProgressRenderer("auto", total=2, desc="analyze", unit="file")
        outer.update()
        inner.update()
        read_terminal(master)
        outer.refresh()
        assert read_terminal(master) == ""
        inner.close()
        assert "apply" in read_terminal(master)
        outer.close()
    assert not progress._ACTIVE


def test_fitting_uses_terminal_cells_and_removes_control_characters() -> None:
    """Audit 7: wide file names and embedded newlines cannot wrap a progress line."""
    assert progress._fit_line("界界界x", 5) == "界界"
    assert progress._fit_line("a\nb\x1bc", 5) == "a b c"


@pytest.mark.parametrize("use_tqdm", [False, True])
def test_note_preserves_progress_and_its_own_line(
    monkeypatch: pytest.MonkeyPatch,
    use_tqdm: bool,
) -> None:
    """Audit CLI-02: a formatter note cannot land in the middle of a bar."""
    if not use_tqdm:
        monkeypatch.setattr(progress, "load_tqdm", lambda: None)
    with terminal(80) as (stream, master), contextlib.redirect_stderr(stream):
        renderer = ProgressRenderer("auto", total=3, desc="apply", unit="it")
        renderer.update()
        read_terminal(master)
        renderer.note("formatter unavailable")
        output = read_terminal(master)
        assert "\r\x1b[2KNote: formatter unavailable\r\n" in output
        assert "apply" in output.split("\n")[-1]
        renderer.close()


def test_logger_warnings_have_a_prefix_and_clear_the_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit 7: logger warnings have the same placement and prefix as other diagnostics."""
    import logging
    from towel.diagnostics import _CurrentStderrHandler

    monkeypatch.setattr(progress, "load_tqdm", lambda: None)
    handler = _CurrentStderrHandler()
    with terminal(80) as (stream, master), contextlib.redirect_stderr(stream):
        renderer = ProgressRenderer("auto", total=3, desc="apply", unit="it")
        renderer.update()
        read_terminal(master)
        record = logging.LogRecord(
            "towel", logging.WARNING, __file__, 1, "Skipping %s", ("bad.py",), None
        )
        handler.handle(record)
        output = read_terminal(master)
        assert "\r\x1b[2KWarning: Skipping bad.py\r\n" in output
        assert "apply" in output.split("\n")[-1]
        assert record.getMessage() == "Skipping bad.py"
        renderer.close()


@pytest.mark.parametrize(
    "message",
    ["Skipping bad.py: cannot decode", "Dropped a proposal rendering (f and g): invalid AST"],
)
def test_warning_is_said_once_per_run(message: str) -> None:
    """Audit 7: repeated analysis passes and rehearings must not repeat a warning."""
    import logging
    from towel.diagnostics import _CurrentStderrHandler

    handler = _CurrentStderrHandler()
    output = io.StringIO()
    record = logging.LogRecord("towel", logging.WARNING, __file__, 1, message, (), None)
    with contextlib.redirect_stderr(output):
        for _ in range(2):
            with progress.progress_run():
                handler.handle(record)
                handler.handle(record)
    assert output.getvalue() == f"Warning: {message}\n" * 2


def test_a_run_resolves_its_backend_only_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Audit 7: analysis phases share one terminal/backend decision for the run."""
    calls = []

    def load() -> None:
        calls.append(True)

    monkeypatch.setattr(progress, "load_tqdm", load)
    with (
        terminal() as (stream, master),
        contextlib.redirect_stderr(stream),
        progress.progress_run(),
    ):
        for name in ["analyze", "pair", "apply"]:
            renderer = ProgressRenderer("auto", total=1, desc=name, unit="item")
            renderer.update()
            renderer.close()
        read_terminal(master)
    assert len(calls) == 1


def test_quiet_suppresses_progress_and_notes_but_keeps_diagnostics() -> None:
    """Audit 7: quiet keeps warnings/errors, and does not silence the next invocation."""
    import logging
    from towel.diagnostics import _CurrentStderrHandler

    output = io.StringIO()
    handler = _CurrentStderrHandler()
    with contextlib.redirect_stderr(output):
        with progress.progress_run(quiet=True):
            renderer = ProgressRenderer("tqdm", total=3, desc="apply", unit="it")
            renderer.update()
            renderer.note("quiet note")
            for level, message in [
                (logging.INFO, "quiet detail"),
                (logging.WARNING, "visible warning"),
                (logging.ERROR, "visible error"),
            ]:
                handler.handle(logging.LogRecord("towel", level, __file__, 1, message, (), None))
            renderer.close()
        with progress.progress_run():
            progress.write_note("normal note")
    assert (
        output.getvalue() == "Warning: visible warning\nError: visible error\nNote: normal note\n"
    )


def test_heartbeat_reports_absolute_counts_and_pauses_before_fork(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit 7/runtime hook: pool counts stay alive without leaving a thread at fork."""
    import threading

    monkeypatch.setattr(progress, "load_tqdm", lambda: None)
    with terminal(100) as (stream, master), contextlib.redirect_stderr(stream):
        renderer = ProgressRenderer("auto", total=10, desc="pairs judged", unit="pair")
        try:
            renderer.heartbeat(3, 10)
            renderer.heartbeat(7, 10, details={"rate": "2/s"})
            output = read_terminal(master)
            assert "pairs judged: 7/10 pair" in output
            assert "rate=2/s" in output
            assert any(thread.name == "towel-progress" for thread in threading.enumerate())
            with progress.display_threads_stopped(timeout=2):
                assert not any(thread.name == "towel-progress" for thread in threading.enumerate())
            assert any(thread.name == "towel-progress" for thread in threading.enumerate())
        finally:
            renderer.close()
    assert not any(thread.name == "towel-progress" for thread in threading.enumerate())


def test_run_cleanup_catches_a_heartbeat_before_its_first_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit 7: a heartbeat begun immediately before exit cannot outlive the run."""
    import threading

    monkeypatch.setattr(progress, "load_tqdm", lambda: None)
    with terminal() as (stream, master), contextlib.redirect_stderr(stream):
        with progress.progress_run():
            renderer = ProgressRenderer("auto", total=None, desc="apply", unit="it")
            renderer.start_heartbeat()
        assert renderer._closed
        assert not any(thread.name == "towel-progress" for thread in threading.enumerate())
        assert read_terminal(master).endswith("\r\x1b[2K")


def test_interrupted_heartbeat_start_does_not_break_run_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Integration 8: a time limit before the thread starts still closes the renderer."""
    import threading
    from towel.execution_budget import BudgetExpired

    def expire(thread: threading.Thread) -> None:
        raise BudgetExpired()

    monkeypatch.setattr(progress, "load_tqdm", lambda: None)
    monkeypatch.setattr(threading.Thread, "start", expire)
    with terminal() as (stream, master), contextlib.redirect_stderr(stream):
        renderer = ProgressRenderer("auto", total=3, desc="apply", unit="it")
        try:
            with pytest.raises(BudgetExpired), progress.progress_run():
                renderer.start_heartbeat()
            assert renderer not in progress._ACTIVE
        finally:
            renderer.close()


def test_timeout_leaves_tqdms_refresh_lock_available_to_the_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Alarm hang: tqdm must release its refresh lock before the deadline unwinds the draw."""
    import signal
    import threading
    from towel.execution_budget import BudgetExpired, run_with_budget

    lock = threading.Lock()

    def expire_while_drawing() -> None:
        assert lock.locked()
        signal.raise_signal(signal.SIGALRM)

    with terminal() as (stream, master), contextlib.redirect_stderr(stream):
        renderer = ProgressRenderer("tqdm", total=3, desc="apply", unit="it")
        try:
            renderer.refresh()
            bar = renderer._bar
            assert bar is not None
            # Give this bar a private lock so a regression cannot hang pytest at exit.
            with monkeypatch.context() as patch:
                patch.setattr(bar, "_lock", lock)
                patch.setattr(bar, "display", expire_while_drawing)
                with pytest.raises(BudgetExpired):
                    run_with_budget(3600, renderer.refresh)
            assert not lock.locked(), "the heartbeat would block on tqdm's abandoned lock"
        finally:
            renderer.close()
