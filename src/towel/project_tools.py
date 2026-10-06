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

"""The result of choosing an external tool from a project's configuration."""

from __future__ import annotations

from dataclasses import dataclass
import os
import json
from pathlib import Path
import select
import signal
import subprocess
import sys
import threading
import time
import weakref
from typing import Generic, Literal, Mapping, Optional, TypeVar, cast

T = TypeVar("T")


def python_tool_environment() -> dict[str, str]:
    """Keep child tools independent of caller-supplied Python import/startup settings.

    Isolated interpreter flags protect module launches; a tool discovered on
    PATH may instead be a Python console script whose shebang we cannot change.
    Neither path should load project code through PYTHONPATH or sitecustomize.
    """
    return {
        **{name: value for name, value in os.environ.items() if not name.startswith("PYTHON")},
        "PYTHONNOUSERSITE": "1",
        "PYTHONSAFEPATH": "1",
    }


@dataclass(frozen=True)
class ToolChoice(Generic[T]):
    """What ``*_for_project`` chose, and why.

    ``tool`` is None when nothing suitable is installed. ``note`` names the
    choice ("Black", "ruff import sorting", "mypy") and any configured tool
    that is missing, so the command line can tell the user what happened.
    """

    tool: Optional[T]
    note: str


class ToolFailure(ValueError):
    """An owned formatter could not return trustworthy text."""


def _stop_tool(process: subprocess.Popen[bytes], owner: int) -> None:
    """Close only this process's tool, including its children after a timeout."""
    if os.getpid() != owner:
        return
    if process.stdin is not None:
        process.stdin.close()
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
    if process.stdout is not None:
        process.stdout.close()


class IsolatedFormatTool:
    """One owned isolated tool, serialized across callers, with bounded text exchanges.

    Black and isort keep their imports across snippets without putting tool
    modules or their dependencies into the caller's import state. A forked
    caller cannot use or close the parent's tool. Closure disposal/exit closes
    it; a parent-PID watchdog ends the worker after an abrupt parent exit,
    even when a forked sibling inherited its stdin writer.
    """

    def __init__(self, name: Literal["black", "isort"], timeout: float = 120.0) -> None:
        self._owner = os.getpid()
        self._timeout = timeout
        self._lock = threading.RLock()
        self._closed = False
        self._process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-B",
                str(Path(__file__).with_name("_formatting_worker.py")),
                name,
                str(self._owner),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=python_tool_environment(),
            start_new_session=True,
        )
        self._removal = weakref.finalize(self, _stop_tool, self._process, self._owner)
        try:
            ready = self._exchange(None)
            unavailable = ready.get("unavailable")
            if isinstance(unavailable, str):
                raise ImportError(unavailable)
            if ready.get("ready") is not True:
                raise ToolFailure("Invalid formatter startup response")
        except BaseException:
            self.close()
            raise

    def _exchange(self, request: Optional[Mapping[str, object]]) -> Mapping[str, object]:
        from ._formatting_worker import MAX_FRAME_BYTES

        process = self._process
        if process.stdin is None or process.stdout is None:
            raise ToolFailure("Formatter protocol pipes are missing")
        raw = b"" if request is None else (json.dumps(request) + "\n").encode("utf-8")
        if len(raw) > MAX_FRAME_BYTES:
            raise ToolFailure("Formatter request exceeds the byte limit")
        input_fd, output_fd = process.stdin.fileno(), process.stdout.fileno()
        os.set_blocking(input_fd, False)
        os.set_blocking(output_fd, False)
        sent = 0
        response = bytearray()
        deadline = time.monotonic() + self._timeout
        try:
            while not response.endswith(b"\n"):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ToolFailure("Formatter timed out")
                readable, writable, _ = select.select(
                    [output_fd], [input_fd] if sent < len(raw) else [], [], remaining
                )
                if writable:
                    try:
                        sent += os.write(input_fd, memoryview(raw)[sent : sent + 65536])
                    except BlockingIOError:
                        pass
                if readable:
                    try:
                        chunk = os.read(output_fd, 65536)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        raise ToolFailure("Formatter exited before answering")
                    response.extend(chunk)
                    if len(response) > MAX_FRAME_BYTES:
                        raise ToolFailure("Formatter response exceeds the byte limit")
            value: object = json.loads(response)
        except (OSError, ValueError) as error:
            raise ToolFailure(f"Formatter protocol failed: {error}") from error
        if not isinstance(value, dict):
            raise ToolFailure("Formatter returned a non-object response")
        return cast(Mapping[str, object], value)

    def _answer(self, request: Mapping[str, object]) -> Mapping[str, object]:
        """One serialized response, or an explicit tool/protocol failure."""
        if os.getpid() != self._owner:
            raise ToolFailure("Create a new formatter after fork")
        with self._lock:
            if self._closed:
                raise ToolFailure("Formatter is closed")
            try:
                response = self._exchange(request)
            except ToolFailure:
                self.close()
                raise
            failure = response.get("failure")
            if isinstance(failure, str):
                raise ToolFailure(failure)
            return response

    def render(self, request: Mapping[str, object]) -> str:
        """The tool's text or an explicit refusal, never a fabricated unchanged result."""
        response = self._answer(request)
        source = response.get("source")
        if not isinstance(source, str):
            self.close()
            raise ToolFailure("Formatter returned invalid source text")
        return source

    def excludes(self, request: Mapping[str, object]) -> bool:
        """Black's own file-selection answer, not a parent import of Black's helpers."""
        response = self._answer(request)
        excluded = response.get("excluded")
        if type(excluded) is not bool:
            self.close()
            raise ToolFailure("Formatter returned an invalid exclusion answer")
        return excluded

    def close(self) -> None:
        if os.getpid() != self._owner:
            return
        with self._lock:
            if not self._closed:
                self._closed = True
                self._removal()
