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

"""One long-lived pyright over a project, asked about prospective sources.

The command line reanalyzes the whole project on every invocation, so checking
one candidate signature against a project of a few hundred files costs seconds,
and a fixed point that tries several candidates per proposal costs hours. A
language server keeps its analysis warm between candidates and reanalyzes only
what the change reached, reaching the same verdict for a fraction of the work.

The server is told to analyze the whole workspace rather than open files alone,
so a change in one module is still judged by its consumers.

The server is pointed at a private copy of the project rather than at the user's
tree, and is told which copied files changed. Its in-memory overlays are not
usable here: it reanalyzes only the overlaid file, so a change that breaks a
consumer reads as clean through them. A watched-file notification reanalyzes
consumers exactly as an edit on disk would, which is what verification needs.

The protocol here is deliberately small: enough of LSP to open a workspace, set
its configuration, report changed files, and collect published diagnostics.
Anything unexpected from the server ends the session rather than being
interpreted, because a checker that cannot be trusted to have finished is not
evidence that code is valid.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
from typing import Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

from .diagnostics import LOG

START_TIMEOUT_SECONDS = 120.0
"""How long the server may take to accept the workspace before we give up."""

ANALYSIS_TIMEOUT_SECONDS = 600.0
"""How long one settle may take; past this the session is unusable."""

_QUIET_SECONDS = 0.35
"""Silence that ends a settle when the server reports no progress of its own.

Pyright brackets a substantial reanalysis with work-done progress, but answers
a small edit without any progress at all. Waiting only for progress would hang
on the small edits; waiting only for silence would read a large reanalysis half
finished. A settle therefore ends on whichever arrives: the end of a progress
run that began after the edit, or this much silence.
"""


@dataclass(frozen=True)
class Diagnostic:
    """One diagnostic at an absolute path, with its zero-based line."""

    path: str
    line: int
    severity: str
    message: str
    rule: str = ""


class SessionFailure(RuntimeError):
    """The server could not be started, could not finish, or spoke out of turn."""


_SEVERITIES = {1: "error", 2: "warning", 3: "information", 4: "hint"}


def _uri(path: Path) -> str:
    return path.as_uri()


def _path_of(uri: str) -> Optional[str]:
    if not uri.startswith("file://"):
        return None
    from urllib.parse import unquote, urlparse

    parsed = urlparse(uri)
    return unquote(parsed.path) or None


class PyrightSession:
    """A running language server over one project root.

    Not thread safe: one caller drives one session. ``close`` is idempotent and
    is safe to call after a failure.
    """

    def __init__(
        self,
        command: Sequence[str],
        root: Path,
        interpreter: str,
        *,
        environment: Optional[Mapping[str, str]] = None,
    ) -> None:
        self._root = root
        self._settings = {
            "python": {
                "pythonPath": interpreter,
                "analysis": {
                    # Open files alone would leave a helper's consumers
                    # unexamined, which is the whole point of the check.
                    "diagnosticMode": "workspace",
                },
            }
        }
        try:
            self._process = subprocess.Popen(
                [*command, "--stdio"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=str(root),
                env=dict(environment) if environment is not None else None,
            )
        except OSError as error:
            raise SessionFailure(f"could not start pyright: {error}") from error
        self._next_id = 0
        self._replies: "queue.Queue[Tuple[int, object]]" = queue.Queue()
        self._events: "queue.Queue[Tuple[str, object]]" = queue.Queue()
        # The server states a file's diagnostics once and stays silent about it
        # until they change, so the current picture has to be kept here. A
        # settle that returned only what was said during it would forget every
        # error found before the first candidate was ever checked.
        self._state: Dict[str, List[Diagnostic]] = {}
        self._closed = False
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._initialize()

    # -- protocol ---------------------------------------------------------

    def _write(self, payload: Mapping[str, object]) -> None:
        stream = self._process.stdin
        if stream is None or self._process.poll() is not None:
            raise SessionFailure("pyright exited")
        raw = json.dumps(payload).encode("utf-8")
        try:
            stream.write(b"Content-Length: %d\r\n\r\n%s" % (len(raw), raw))
            stream.flush()
        except (BrokenPipeError, OSError) as error:
            raise SessionFailure(f"pyright stopped reading: {error}") from error

    def _read_message(self) -> Optional[Mapping[str, object]]:
        stream = self._process.stdout
        if stream is None:
            return None
        header = b""
        while not header.endswith(b"\r\n\r\n"):
            chunk = stream.read(1)
            if not chunk:
                return None
            header += chunk
            if len(header) > 8192:
                return None
        length = 0
        for line in header.decode("ascii", "replace").split("\r\n"):
            if line.lower().startswith("content-length:"):
                try:
                    length = int(line.split(":", 1)[1])
                except ValueError:
                    return None
        if length <= 0 or length > 64 * 1024 * 1024:
            return None
        body = stream.read(length)
        if body is None or len(body) != length:
            return None
        try:
            message = json.loads(body)
        except (json.JSONDecodeError, UnicodeError):
            return None
        return message if isinstance(message, dict) else None

    def _read_loop(self) -> None:
        while True:
            message = self._read_message()
            if message is None:
                self._events.put(("", None))  # end of stream
                return
            method = message.get("method")
            if "id" in message and isinstance(method, str):
                self._answer(message, method)
            elif "id" in message:
                ident = message.get("id")
                if isinstance(ident, int):
                    self._replies.put((ident, message))
            elif isinstance(method, str):
                self._events.put((method, message.get("params")))

    def _answer(self, message: Mapping[str, object], method: str) -> None:
        """Reply to a server-to-client request.

        The server asks for configuration before it analyzes anything and waits
        for the answer, so an unanswered request is an immediate deadlock.
        """
        result: object = None
        if method == "workspace/configuration":
            params = message.get("params")
            items = params.get("items", []) if isinstance(params, dict) else []
            result = [self._setting(item) for item in items]
        try:
            self._write({"jsonrpc": "2.0", "id": message["id"], "result": result})
        except SessionFailure:
            pass

    def _setting(self, item: object) -> object:
        section = item.get("section") if isinstance(item, dict) else None
        node: object = self._settings
        for part in str(section or "").split("."):
            if not part:
                return node
            node = node.get(part, {}) if isinstance(node, dict) else {}
        return node

    def _request(self, method: str, params: object, timeout: float) -> object:
        self._next_id += 1
        ident = self._next_id
        self._write({"jsonrpc": "2.0", "id": ident, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        pending: List[Tuple[int, object]] = []
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SessionFailure(f"pyright did not answer {method} in {timeout:g}s")
                try:
                    reply = self._replies.get(timeout=min(remaining, 0.5))
                except queue.Empty:
                    if self._process.poll() is not None:
                        raise SessionFailure("pyright exited") from None
                    continue
                if reply[0] == ident:
                    return reply[1]
                pending.append(reply)
        finally:
            for item in pending:
                self._replies.put(item)

    def _notify(self, method: str, params: object) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _initialize(self) -> None:
        response = self._request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": _uri(self._root),
                "workspaceFolders": [{"uri": _uri(self._root), "name": self._root.name}],
                "capabilities": {
                    "window": {"workDoneProgress": True},
                    "workspace": {"configuration": True, "workspaceFolders": True},
                    "textDocument": {"publishDiagnostics": {"relatedInformation": False}},
                },
                "initializationOptions": self._settings,
            },
            START_TIMEOUT_SECONDS,
        )
        if not isinstance(response, dict) or "result" not in response:
            raise SessionFailure("pyright refused the workspace")
        self._notify("initialized", {})
        self._notify("workspace/didChangeConfiguration", {"settings": self._settings})
        self._settle(START_TIMEOUT_SECONDS)

    # -- analysis ---------------------------------------------------------

    def _settle(self, timeout: float) -> Dict[str, List[Diagnostic]]:
        """Absorb diagnostics until the server goes quiet; return the whole picture."""
        analyzing = False
        deadline = time.monotonic() + timeout
        last = time.monotonic()
        while True:
            if time.monotonic() > deadline:
                raise SessionFailure(f"pyright did not settle in {timeout:g}s")
            try:
                method, params = self._events.get(timeout=0.05)
            except queue.Empty:
                if self._process.poll() is not None:
                    raise SessionFailure("pyright exited") from None
                if not analyzing and time.monotonic() - last > _QUIET_SECONDS:
                    return dict(self._state)
                continue
            last = time.monotonic()
            if method == "":
                raise SessionFailure("pyright closed its output")
            if method == "textDocument/publishDiagnostics" and isinstance(params, dict):
                self._record(params)
            elif method == "$/progress" and isinstance(params, dict):
                value = params.get("value")
                kind = value.get("kind") if isinstance(value, dict) else None
                if kind == "begin":
                    analyzing = True
                elif kind == "end":
                    analyzing = False

    def diagnostics_after(self, changed: Sequence[Path]) -> Dict[str, List[Diagnostic]]:
        """Diagnostics once the server has taken ``changed`` into account.

        The caller has already written those paths inside the copied project.
        Passing no paths simply settles and reports what the server has to say
        about the project as it stands.
        """
        if self._closed:
            raise SessionFailure("session is closed")
        self._drain()
        if changed:
            self._notify(
                "workspace/didChangeWatchedFiles",
                {"changes": [{"uri": _uri(path), "type": 2} for path in changed]},
            )
        return self._settle(ANALYSIS_TIMEOUT_SECONDS)

    def _drain(self) -> None:
        """Absorb anything the server said while nobody was listening."""
        while True:
            try:
                method, params = self._events.get_nowait()
            except queue.Empty:
                return
            if method == "textDocument/publishDiagnostics" and isinstance(params, dict):
                self._record(params)

    def _record(self, params: Mapping[str, object]) -> None:
        """Take one published file's diagnostics as the current word on it."""
        path = _path_of(str(params.get("uri", "")))
        if path is None:
            return
        entries = list(_diagnostics_of(path, params))
        if entries:
            self._state[path] = entries
        else:
            self._state.pop(path, None)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._request("shutdown", None, 10.0)
            self._notify("exit", None)
        except SessionFailure:
            pass
        except Exception as error:  # pragma: no cover - defensive
            LOG.debug("pyright shutdown was not clean: %s", error)
        if self._process.stdin is not None:
            try:
                self._process.stdin.close()
            except OSError:
                pass
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                LOG.warning("pyright did not exit after being killed")
        for stream in (self._process.stdout, self._process.stdin):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass


def _diagnostics_of(path: str, params: Mapping[str, object]) -> Iterator[Diagnostic]:
    entries = params.get("diagnostics")
    if not isinstance(entries, list):
        return
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        message = entry.get("message")
        if not isinstance(message, str):
            continue
        span = entry.get("range")
        start = span.get("start") if isinstance(span, dict) else None
        line = start.get("line") if isinstance(start, dict) else None
        code = entry.get("code")
        yield Diagnostic(
            path=path,
            line=line if isinstance(line, int) else 0,
            severity=_SEVERITIES.get(entry.get("severity"), "error"),  # type: ignore[arg-type]
            message=message,
            rule=code if isinstance(code, str) else "",
        )
