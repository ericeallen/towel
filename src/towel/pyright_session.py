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
so a change in one module is still judged by its consumers, and is configured
setting by setting as the command line configures itself (see
``server_settings``), so the two reach one verdict on the same files.

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

from contextlib import contextmanager
from dataclasses import dataclass
from enum import IntEnum
import json
import os
from pathlib import Path, PurePosixPath
import queue
import re
import secrets
import select
import subprocess
import threading
import time
import tomllib
import weakref
from typing import Dict, Iterator, List, Literal, Mapping, Optional, Sequence, Set, Tuple, TypedDict

from .checker_project import UnusableConfiguration, _pyright_config_inputs, _read_json_config
from .diagnostics import LOG
from .source_files import PROBE_PREFIX

START_TIMEOUT_SECONDS = 120.0
"""How long the server may take to accept the workspace before we give up."""

ANALYSIS_TIMEOUT_SECONDS = 600.0
"""How long one settle may take; past this the session is unusable."""

_QUIET_SECONDS = 0.35
"""Silence that ends a settle, once the server has answered for this change.

Pyright brackets a substantial reanalysis with work-done progress, but answers
a small edit without any progress at all. Waiting only for progress would hang
on the small edits; waiting only for silence would read a large reanalysis half
finished. A settle therefore ends on whichever arrives: the end of a progress
run that began after the edit, or this much silence.

Silence alone is not evidence. A server that has not started on the change is
as silent as one that has finished, and a machine under load makes that
moment long: a candidate that broke its consumer read as clean whenever the
server took longer than this to begin. So silence counts only after the
server has published the marker written with the change (see ``_Marker``),
and only while nothing is waiting in its pipe or half read.
"""

UNSEEN_MARKER_TIMEOUT_SECONDS = 60.0
"""How long a directory's first marker may go unanswered before the server is
taken not to analyze that directory at all, which no later wait would cure."""

_MARKER_NAME = f"{PROBE_PREFIX}settled.py"


@dataclass(frozen=True)
class Diagnostic:
    """One diagnostic at an absolute path, with its zero-based line."""

    path: str
    line: int
    severity: str
    message: str
    rule: str = ""


class FileChange(IntEnum):
    """What happened to a watched file, in the protocol's own numbering."""

    CREATED = 1
    CHANGED = 2
    DELETED = 3


class SessionFailure(RuntimeError):
    """The server could not be started, could not finish, or spoke out of turn."""


_SEVERITIES = {1: "error", 2: "warning", 3: "information", 4: "hint"}


class AnalysisSettings(TypedDict):
    """``python.analysis``: each setting sent, at the only value that matches the command line."""

    diagnosticMode: Literal["workspace"]
    autoSearchPaths: Literal[True]
    typeCheckingMode: Literal["standard"]
    useLibraryCodeForTypes: Literal[True]


class PythonSettings(TypedDict):
    """The ``python`` section: the interpreter, and the analysis settings under it."""

    pythonPath: str
    analysis: AnalysisSettings


class ServerSettings(TypedDict):
    """Everything the server is told; a section it asks for and this lacks is answered empty."""

    python: PythonSettings


def server_settings(interpreter: str) -> ServerSettings:
    """The configuration under which the server reaches the command line's verdict.

    The language server and ``pyright`` share one analyzer and configure it
    differently wherever the client is silent. Every setting the server reads
    that can change a diagnostic is below -- under ``python.analysis`` unless
    named in full -- with its default in pyright 1.1.414's server once the
    client sends a ``python.analysis`` section, the command line's, and what
    Towel sends:

    ===========================  ================  ===============  ============
    setting                      server default    command line     sent
    ===========================  ================  ===============  ============
    autoSearchPaths              false             true, always     true
    diagnosticMode               open files only   every file       workspace
    typeCheckingMode             standard [1]      standard [1]     standard
    useLibraryCodeForTypes       true [1]          true [1]         true
    extraPaths                   none [1]          none             unset
    include, exclude, ignore     none [1]          none [2]         unset
    diagnosticSeverityOverrides  none [1]          none             unset
    stubPath                     typings [1]       typings          unset
    typeshedPaths                bundled [1]       bundled          unset
    python.pythonPath            python on PATH    --pythonpath     the oracle's
    python.venvPath              none              none             unset
    pyright.*, over the above    none              none             unset
    ===========================  ================  ===============  ============

    [1] Applied by the server only to a project without a pyright
    configuration. With one, the server takes these from the configuration
    alone, as the command line does, which has no flag for them. [2] With
    ``--project`` and no files named, as Towel runs it.

    ``autoSearchPaths`` is the one that decides verdicts. The server turns it
    on only for a client that sends no ``python.analysis`` section at all, and
    Towel must send one for ``diagnosticMode``, so it was off. In a ``src``
    layout the package was then analyzed as ``src.<package>``, and ``import
    <package>`` resolved to the copy installed in the environment -- with an
    editable install, the user's own tree, not the copy under check. A
    consumer outside ``src`` was judged against the unchanged project, so a
    candidate that broke it read as clean, and the two copies' types clashed
    in errors the project does not have (``"src.pkg.Task" is not assignable
    to "pkg.Task"``). The command line adds ``src`` to the search paths
    whenever it exists without an ``__init__.py``; so does the server now.

    What is left unset is unset on the command line too, so the analyzer's
    own defaults apply to both; any value sent for it could only differ.
    Settings that change what an editor shows but no diagnostic (completion,
    indexing, logging) keep the server's defaults. The interpreter is the
    oracle's for both paths, and is where the search paths, the Python
    version and the platform come from.
    """
    return ServerSettings(
        python=PythonSettings(
            pythonPath=interpreter,
            analysis=AnalysisSettings(
                # Open files alone would leave a helper's consumers
                # unexamined, which is the whole point of the check.
                diagnosticMode="workspace",
                autoSearchPaths=True,
                typeCheckingMode="standard",
                useLibraryCodeForTypes=True,
            ),
        )
    )


_DEFAULT_EXCLUDE = ("**/node_modules", "**/__pycache__", "**/.*")
"""What pyright leaves out of a project whose configuration names no ``exclude``."""


def _wildcard(base: Path, spec: str) -> "re.Pattern[str]":
    """A path, or a directory and all it holds, that pyright's file spec ``spec`` names.

    As pyright reads a spec: relative to the configuration that states it,
    ``**`` for any number of directories, ``*`` and ``?`` within one path
    component.
    """
    pattern = ""
    for component in PurePosixPath(os.path.normpath(str(base / spec))).parts[1:]:
        if component == "**":
            pattern += "(/[^/]+)*?"
            continue
        pattern += "/" + "".join(
            "[^/]*" if char == "*" else "[^/]" if char == "?" else re.escape(char)
            for char in component
        )
    return re.compile(f"^{pattern}($|/)")


@dataclass(frozen=True)
class PyrightScope:
    """The files a project's pyright configuration has pyright report on.

    Those its ``include`` names, less those its ``exclude`` or ``ignore``
    names. pyright analyzes an excluded module another imports, but reports
    nothing in it, and nothing in an ignored one, so its silence there says
    nothing about the code: the file is outside what the project's pyright
    checks, not code it takes to be unreachable. A language server asked to
    answer there never does, and a run waited a minute for it and then gave up
    the server for the rest of the run.
    """

    include: Tuple["re.Pattern[str]", ...]
    exclude: Tuple["re.Pattern[str]", ...] = ()
    ignore: Tuple["re.Pattern[str]", ...] = ()

    def reports_on(self, path: Path) -> bool:
        """Whether pyright, configured so, reports what it finds in the file at ``path``."""
        where = Path(os.path.realpath(path)).as_posix()
        return (
            any(spec.match(where) for spec in self.include)
            and not any(spec.match(where) for spec in self.exclude)
            and not any(spec.match(where) for spec in self.ignore)
        )


EVERYWHERE = PyrightScope((re.compile("^/"),))
"""The scope of a configuration that cannot be read, where the check itself says why."""


def _settings_chain(root: Path) -> List[Tuple[Path, Mapping[str, object]]]:
    """Each configuration pyright reads for ``root``, its own first, with the directory it is in."""
    chain: List[Tuple[Path, Mapping[str, object]]] = []
    for config in _pyright_config_inputs(root):
        if config.name == "pyproject.toml":
            with config.open("rb") as handle:
                tool = tomllib.load(handle).get("tool", {})
            section = tool.get("pyright", {}) if isinstance(tool, dict) else {}
            chain.append((config.parent, section if isinstance(section, dict) else {}))
        else:
            chain.append((config.parent, _read_json_config(config)))
    return chain


def pyright_scope(root: Path) -> PyrightScope:
    """What ``root``'s pyright configuration has pyright report on, read without running pyright.

    Of each of ``include``, ``exclude`` and ``ignore``, the first
    configuration in its ``extends`` chain that states it decides it, as
    pyright reads the chain, each relative to the file that states it. With
    no ``include`` pyright checks the directory of its configuration; with no
    ``exclude`` it leaves out ``node_modules``, ``__pycache__`` and hidden
    directories. A configuration that cannot be read has no scope here: the
    check refuses it, saying why.
    """
    root = Path(os.path.realpath(root))
    try:
        chain = _settings_chain(root)
    except (OSError, ValueError, UnusableConfiguration, tomllib.TOMLDecodeError):
        return EVERYWHERE
    specs: Dict[str, Tuple["re.Pattern[str]", ...]] = {}
    defaults = {"include": (".",), "exclude": _DEFAULT_EXCLUDE, "ignore": ()}
    for key, default in defaults.items():
        stated = next(((base, chosen[key]) for base, chosen in chain if key in chosen), None)
        base, value = stated if stated is not None else (root, default)
        values = value if isinstance(value, (list, tuple)) else ()
        specs[key] = tuple(
            _wildcard(Path(os.path.realpath(base)), spec)
            for spec in values
            if isinstance(spec, str)
        )
    return PyrightScope(specs["include"], specs["exclude"], specs["ignore"])


def _included_directories(root: Path) -> List[Path]:
    """The directories ``root``'s pyright configuration includes by name, without wildcards.

    Each is spelled under ``root`` as given, not resolved. The server knows its
    workspace by the spelling it was given, and a file written under another
    spelling of the same directory is a change outside it that the server never
    analyzes. The configuration is read resolved, so a marker placed in an
    included directory went to ``/private/var/...`` for a copy made under
    macOS's ``/var/...``, and a run whose changes all lay outside ``include``
    waited out ``UNSEEN_MARKER_TIMEOUT_SECONDS`` and gave the server up.
    """
    real = Path(os.path.realpath(root))
    try:
        chain = _settings_chain(real)
    except (OSError, ValueError, UnusableConfiguration, tomllib.TOMLDecodeError):
        return []
    for base, chosen in chain:
        specs = chosen.get("include")
        if specs is None:
            continue
        named = specs if isinstance(specs, list) else []
        return [
            _spelled_under(root, real, base / spec)
            for spec in named
            if isinstance(spec, str) and not set("*?[") & set(spec) and (base / spec).is_dir()
        ]
    return []


def _spelled_under(root: Path, real: Path, path: Path) -> Path:
    """``path``, which lies under ``real``, the resolved ``root``, spelled under ``root``."""
    normal = Path(os.path.normpath(path))
    return root / normal.relative_to(real) if normal.is_relative_to(real) else normal


def _first_reported_directory(root: Path, scope: PyrightScope) -> Optional[Path]:
    """The first directory under ``root``, walked in order, where pyright reports on a new file.

    For a configuration whose ``include`` names no directory outright
    (``src/*``): the marker must stand where the server analyzes it.
    """
    for parent, directories, _ in os.walk(root):
        directories.sort()
        if scope.reports_on(Path(parent) / _MARKER_NAME):
            return Path(parent)
    return None


def _uri(path: Path) -> str:
    return path.as_uri()


def _path_of(uri: str) -> Optional[str]:
    if not uri.startswith("file://"):
        return None
    from urllib.parse import unquote, urlparse

    parsed = urlparse(uri)
    return unquote(parsed.path) or None


class _Marker:
    """A private module whose only diagnostic names the change it was written with.

    The server learns of changes from one notification and from nothing else (it
    does not watch the copy itself), so every file named in that notification is
    invalidated together. The marker is named in it too, and its text reveals a
    literal that is new each time. Its diagnostic can therefore only come from
    an analysis that began after the server took the whole change in. Revealing
    a type is reported whatever rules the project turns off.
    """

    def __init__(self) -> None:
        self.token = ""
        self.directory: Optional[Path] = None
        self._placed: set[Path] = set()

    def place(self, directory: Path) -> Tuple[Path, FileChange, bool]:
        """Write a fresh marker in ``directory``; its path, how to report it, and if new there."""
        self.token = f"towel-{secrets.token_hex(8)}"
        self.directory = directory
        path = directory / _MARKER_NAME
        path.write_text(f'reveal_type("{self.token}")\n', encoding="utf-8")
        first = path not in self._placed
        self._placed.add(path)
        return path, (FileChange.CREATED if first else FileChange.CHANGED), first

    def answered_by(self, path: str, entries: Sequence[Diagnostic]) -> bool:
        return Path(path).name == _MARKER_NAME and any(
            self.token and self.token in entry.message for entry in entries
        )


_OPEN_SESSIONS: "weakref.WeakSet[PyrightSession]" = weakref.WeakSet()
"""Every session not yet closed, for :func:`readers_stopped`."""

_STOP_CHECK_SECONDS = 0.05
"""How often a reader waiting for the server looks whether it has been asked to end."""


@contextmanager
def readers_stopped(timeout: float) -> Iterator[None]:
    """End every open session's reader thread for the block, and start each again after.

    For a fork: a child keeps only the thread that forked it and every lock as
    it stood, so a fork made while a reader runs could leave the child a lock
    no thread of its own will ever release. A reader ends only between two
    messages, so none is split between it and the next; one still mid-message
    after ``timeout`` seconds is left running, still to be seen among the
    process's threads. The server waits meanwhile, as it does whenever its
    client is slow, and nothing it says is lost: it stays in the pipe.
    """
    asked = [
        (session, reader)
        for session in list(_OPEN_SESSIONS)
        for reader in (session._ask_reader_to_end(),)
        if reader is not None
    ]
    deadline = time.monotonic() + timeout
    for _, reader in asked:
        reader.join(max(0.0, deadline - time.monotonic()))
    try:
        yield
    finally:
        for session, _ in asked:
            session._resume_reading()


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
        self._settings = server_settings(interpreter)
        self._scope = pyright_scope(root)
        self._included = _included_directories(root)
        try:
            self._process = subprocess.Popen(
                [*command, "--stdio"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=str(root),
                env=dict(environment) if environment is not None else None,
                # Unbuffered, so what the server has said is either still in the
                # pipe or in the reader's hands, and a settle can ask about both.
                bufsize=0,
            )
        except OSError as error:
            raise SessionFailure(f"could not start pyright: {error}") from error
        self._next_id = 0
        self._writing = threading.Lock()
        self._replies: "queue.Queue[Tuple[int, object]]" = queue.Queue()
        self._events: "queue.Queue[Tuple[str, object]]" = queue.Queue()
        # The server states a file's diagnostics once and stays silent about it
        # until they change, so the current picture has to be kept here. A
        # settle that returned only what was said during it would forget every
        # error found before the first candidate was ever checked.
        self._state: Dict[str, List[Diagnostic]] = {}
        self._marker = _Marker()
        self._marker_answered = False
        self._reading = False
        self._closed = False
        # A request that the reader end at its next message boundary, and
        # whether the reader took it (see ``_server_speaks_before_a_stop``).
        self._stopping = threading.Lock()
        self._stop_asked = False
        self._stop_taken = False
        self._reader = self._new_reader()
        self._reader.start()
        _OPEN_SESSIONS.add(self)
        try:
            self._initialize()
        except BaseException:
            # No object is bound when a constructor raises, so nobody can be
            # asked to close this one. A server left running would go on
            # analysing a directory its caller is about to delete.
            self.close()
            raise

    # -- protocol ---------------------------------------------------------

    def _write(self, payload: Mapping[str, object]) -> None:
        """Send one message whole.

        The reader thread answers the server's own requests, so two threads
        write here. A frame longer than a pipe's atomic unit can interleave
        with another and leave the protocol unreadable, and a raw stream may
        write fewer bytes than it was given, which strands the server waiting
        for a body that never arrives. The lock keeps frames apart and the
        loop finishes one before returning.
        """
        raw = json.dumps(payload).encode("utf-8")
        frame = b"Content-Length: %d\r\n\r\n%s" % (len(raw), raw)
        with self._writing:
            stream = self._process.stdin
            if stream is None or self._process.poll() is not None:
                raise SessionFailure("pyright exited")
            try:
                sent = 0
                while sent < len(frame):
                    written = stream.write(frame[sent:])
                    if not written:
                        raise SessionFailure("pyright accepted none of a message")
                    sent += written
                stream.flush()
            except (BrokenPipeError, OSError) as error:
                raise SessionFailure(f"pyright stopped reading: {error}") from error

    def _read_exactly(self, count: int) -> Optional[bytes]:
        stream = self._process.stdout
        if stream is None:
            return None
        data = b""
        while len(data) < count:
            chunk = stream.read(count - len(data))
            if not chunk:
                return None
            data += chunk
            self._reading = True
        return data

    def _read_message(self) -> Optional[Mapping[str, object]]:
        header = b""
        while not header.endswith(b"\r\n\r\n"):
            chunk = self._read_exactly(1)
            if chunk is None:
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
        body = self._read_exactly(length)
        if body is None:
            return None
        try:
            message = json.loads(body)
        except (json.JSONDecodeError, UnicodeError):
            return None
        return message if isinstance(message, dict) else None

    def _new_reader(self) -> threading.Thread:
        return threading.Thread(target=self._read_loop, name="towel-pyright-reader", daemon=True)

    def _read_loop(self) -> None:
        try:
            self._read_messages()
        except (OSError, ValueError):
            # ``close`` shut the pipe between two of this reader's reads, which
            # it waits on between messages; that ends the stream as surely as
            # the server's exit does.
            if not self._closed:
                raise

    def _read_messages(self) -> None:
        while self._server_speaks_before_a_stop():
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
            # Only now is the message out of this thread's hands; see ``_is_silent``.
            self._reading = False

    def _server_speaks_before_a_stop(self) -> bool:
        """Wait for the server's next message; False when this reader is asked to end first.

        A request to end is either taken here, and the reader ends, or
        withdrawn by ``_resume_reading`` before the reader saw it; the lock
        makes it exactly one of the two.
        """
        stream = self._process.stdout
        if stream is None:
            return True  # The read that follows reports the end of the stream.
        while True:
            with self._stopping:
                if self._stop_asked:
                    self._stop_asked, self._stop_taken = False, True
                    return False
            ready, _, _ = select.select([stream], [], [], _STOP_CHECK_SECONDS)
            if ready:
                return True

    def _ask_reader_to_end(self) -> Optional[threading.Thread]:
        """Ask the reader to end at its next message boundary; the thread, for the caller to join."""
        if self._closed or not self._reader.is_alive():
            return None
        with self._stopping:
            self._stop_asked, self._stop_taken = True, False
        return self._reader

    def _resume_reading(self) -> None:
        """After ``_ask_reader_to_end``: a new reader if the old one ended, else the old one carries on."""
        with self._stopping:
            taken = self._stop_taken
            self._stop_asked = self._stop_taken = False
        if taken:
            # The reader took the request, so it has ended or is about to.
            self._reader.join()
            if not self._closed:
                self._reader = self._new_reader()
                self._reader.start()

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
            # A server that has gone cannot be answered. The caller's own next
            # write or settle meets the same dead process and reports it there,
            # on the thread that can act on it.
            pass

    def _setting(self, item: object) -> object:
        """The section of ``server_settings`` that ``item`` asks for, empty where it holds none.

        An empty section leaves the server at its defaults, which for every
        section not held there are the command line's.
        """
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

    def _is_silent(self) -> bool:
        """Whether nothing the server has said is still on its way to ``_events``.

        Asked in the order a message travels (pipe, reader, queue), so one that
        moves on between the questions is met again further along.
        """
        stream = self._process.stdout
        if stream is None:
            return True
        waiting, _, _ = select.select([stream], [], [], 0)
        return not waiting and not self._reading and self._events.empty()

    def _settle(
        self, timeout: float, *, marker_timeout: Optional[float] = None
    ) -> Dict[str, List[Diagnostic]]:
        """Absorb diagnostics until the server has answered and gone quiet.

        With ``marker_timeout``, quiet counts only once the marker written with
        the change has been published, which must happen within that long:
        before it, silence says nothing.
        """
        analyzing = False
        running: Set[str] = set()
        self._marker_answered = marker_timeout is None
        started = time.monotonic()
        deadline = started + timeout
        last = started
        while True:
            now = time.monotonic()
            if not self._marker_answered and now > started + (marker_timeout or 0.0):
                raise SessionFailure(f"pyright did not answer for a change in {marker_timeout:g}s")
            if now > deadline:
                raise SessionFailure(f"pyright did not settle in {timeout:g}s")
            try:
                method, params = self._events.get(timeout=0.05)
            except queue.Empty:
                if self._process.poll() is not None:
                    raise SessionFailure("pyright exited") from None
                if not self._is_silent() or not self._marker_answered:
                    last = time.monotonic()
                elif not analyzing and time.monotonic() - last > _QUIET_SECONDS:
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
                token = repr(params.get("token"))
                # The server runs more than one progress at a time, indexing
                # beside analysis. Ending whichever finishes first would say
                # the work was over while the rest was still queued, so a
                # settle waits for every run it saw begin.
                if kind == "begin":
                    running.add(token)
                elif kind == "end":
                    running.discard(token)
                analyzing = bool(running)

    def diagnostics_after(
        self, changed: Mapping[Path, FileChange], *, beside: Sequence[Path] = ()
    ) -> Dict[str, List[Diagnostic]]:
        """Diagnostics once the server has taken ``changed`` into account.

        The caller has already made those changes inside the copied project.
        ``beside`` names files the question is about; the marker goes next to the
        first, so the server analyzes it if it analyzes them. Even with nothing
        changed the marker is rewritten, so the answer never rests on a settle
        that ended before the server had begun.
        """
        if self._closed:
            raise SessionFailure("session is closed")
        self._drain()
        marker, how, first = self._marker.place(self._marker_directory(beside))
        self._notify(
            "workspace/didChangeWatchedFiles",
            {
                "changes": [
                    {"uri": _uri(path), "type": int(kind)}
                    for path, kind in {**changed, marker: how}.items()
                ]
            },
        )
        try:
            return self._settle(
                ANALYSIS_TIMEOUT_SECONDS,
                marker_timeout=(
                    UNSEEN_MARKER_TIMEOUT_SECONDS if first else ANALYSIS_TIMEOUT_SECONDS
                ),
            )
        except SessionFailure:
            if first and not self._marker_answered:
                LOG.warning("pyright gave no sign of analyzing %s", marker.parent)
            raise

    def _marker_directory(self, beside: Sequence[Path]) -> Path:
        """Where the marker goes: beside the first of ``beside`` that the server reports on.

        A marker in a directory the configuration excludes or ignores, or
        leaves out of its ``include``, is never answered, however long the
        wait. With nothing to stand beside, where the server last answered is
        the best place known, then the root, then a directory the
        configuration includes; the root may lie outside what the project
        includes. Failing all of those, the first directory of the project
        that the configuration reports on.
        """
        candidates = [path.parent for path in beside]
        if self._marker.directory is not None:
            candidates.append(self._marker.directory)
        candidates += [self._root, *self._included]
        chosen = next(
            (
                directory
                for directory in candidates
                if self._scope.reports_on(directory / _MARKER_NAME)
            ),
            None,
        )
        if chosen is None:
            chosen = _first_reported_directory(self._root, self._scope)
        return candidates[0] if chosen is None else chosen

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
        if Path(path).name == _MARKER_NAME:
            # The marker is this session's own and no part of the project's picture.
            self._marker_answered |= self._marker.answered_by(path, entries)
            return
        if entries:
            self._state[path] = entries
        else:
            self._state.pop(path, None)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        _OPEN_SESSIONS.discard(self)
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
