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

"""Ask the project's type checker about expressions, subtypes, and generated code.

The engine copies annotations the call sites declare; for an argument that is
an expression rather than an annotated name, only a type checker knows. A
:class:`TypeOracle` answers three questions: the type of each probed
expression at a point in a module (``reveal``), whether one type is a
subtype of another (``is_subtype``), and whether a prospective project type-checks
(``check_project``). :class:`MypyInferrer` builds a copy of the site's module in
an owned worker process with ``reveal_type(<expression>)`` inserted where the block begins,
so names resolve as they do at the call, and asks subtyping through probe
functions ``def _probe(v: narrow) -> wide: return v`` appended to the copy,
so the relation is mypy's own; nothing is written to disk except mypy's
cache, which lives for the inferrer's lifetime so later proposals in the
same run rebuild incrementally. :class:`PyrightOracle` infers through
the pyright command on a temporary sibling file. Verification uses a complete
private project snapshot so changed hosts and unchanged consumers agree. :class:`CombinedOracle`
infers with one checker and verifies with several, and
:func:`type_oracle_for_project` picks them from the project's configuration.

The answers are strings in the checker's spelling, which
``towel.unification.annotations`` turns into annotations only when every
name in them resolves where the helper is defined.
"""

from __future__ import annotations

import configparser
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
import json
import atexit
import hashlib
import importlib.util
import select
import threading
import time
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from enum import Enum
from typing import (
    Dict,
    Final,
    FrozenSet,
    Iterable,
    Iterator,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
    TypedDict,
    cast,
)

from .diagnostics import LOG
from .project_tools import ToolChoice, python_tool_environment
from .checker_project import CheckerSnapshot, checker_snapshot
from .unification.bounded_cache import BoundedCache
from .pyright_session import Diagnostic, FileChange, PyrightSession, SessionFailure
from .source_text import read_source, source_lines
from .project_layout import find_project_root, load_pyproject, package_chain
from .consumers import consumers_of as consumers_of, module_prefixes, walked_package

from .source_files import PROBE_PREFIX as PROBE_PREFIX, is_probe_file as is_probe_file

__all__ = [
    "CheckFailure",
    "CheckResult",
    "CheckSuccess",
    "CombinedOracle",
    "MypyInferrer",
    "PROBE_PREFIX",
    "PyrightOracle",
    "RevealKey",
    "RevealRequest",
    "Subtyping",
    "TypeDiagnostic",
    "TypeOracle",
    "is_probe_file",
    "type_oracle_for_project",
    "relocate_oracle",
]

RevealKey = Tuple[str, int, int]
"""(file path, line the probe was inserted before, index of the expression)."""


@dataclass(frozen=True)
class RevealRequest:
    """Expressions whose types are wanted at the start of ``line`` in ``file_path``.

    ``source`` is the module text the line numbers refer to; ``indent`` is the
    indentation of that line, which the inserted probes share.
    """

    file_path: str
    source: str
    line: int
    indent: str
    expressions: Tuple[str, ...]


class Subtyping(Enum):
    """A checker's answer to "is this narrow type assignable to that wide one?"."""

    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"
    """The checker could not resolve a name in the question, so it could not judge."""


@dataclass(frozen=True)
class TypeDiagnostic:
    """A position-independent error at its original, absolute project path.

    ``line`` (one-based, when the checker gave one) locates the error in the
    checked text. It takes no part in equality, which stays position-independent.
    """

    path: str
    message: str
    line: Optional[int] = field(default=None, compare=False)


@dataclass(frozen=True)
class CheckSuccess:
    """A completed check, including its errors (which may be empty)."""

    errors: Tuple[TypeDiagnostic, ...] = ()


@dataclass(frozen=True)
class CheckFailure:
    """The checker could not finish; this is never evidence that code is valid."""

    reason: str


CheckResult = CheckSuccess | CheckFailure


class TypeOracle(Protocol):
    """Inference and coherent project verification, with explicit failure and ownership."""

    def reveal(self, requests: Sequence[RevealRequest]) -> Mapping[RevealKey, str]:
        """Return unambiguous expression types, omitting unavailable evidence.

        This flat protocol cannot correlate separate constrained-TypeVar
        instantiations. A checker reporting distinct types for the same probe
        does not establish any single one of those types for the expression.
        """
        raise NotImplementedError

    def is_subtype(
        self, file_path: str, source: str, pairs: Sequence[Tuple[str, str]]
    ) -> Sequence[Subtyping]:
        raise NotImplementedError

    def check(self, file_path: str, source: str) -> CheckResult:
        """Check the project with one prospective source replaced."""
        raise NotImplementedError

    def check_project(
        self, sources: Mapping[str, str], *, excluded_paths: Sequence[str] = ()
    ) -> CheckResult:
        """Check all project consumers against all prospective sources together."""
        raise NotImplementedError

    def close(self) -> None:
        """Release checker processes and owned temporary files."""
        raise NotImplementedError


_ERROR = re.compile(r"^(?P<path>.*?):(?P<line>\d+):(?:\d+:)? error: ")
_REVEALED = re.compile(
    r'^(?P<path>.*?):(?P<line>\d+):(?:\d+:)? note: Revealed type is "(?P<type>.*)"$'
)


def _same_file(reported: str, path: str) -> bool:
    """Whether a path a checker printed names ``path``.

    mypy prints a file under the working directory relative to it and any
    other file absolute; Towel passes whatever spelling the caller used. A
    plain string comparison therefore matched only when the working
    directory was outside the project, and every reveal and error was
    dropped otherwise, which switched inference off without a word.
    """
    return os.path.abspath(reported) == os.path.abspath(path)


def _module_name_and_root(path: Path) -> Tuple[str, Path]:
    """The dotted module name of ``path`` and the directory that must be on the search path.

    A package directory need not be a valid identifier: an out-of-place
    refactoring writes the package under an arbitrary output name, and mypy
    spells that name into every type it reveals. Such a component becomes a
    placeholder identifier, which the annotation writer then drops in favour
    of the name the host module binds.
    """
    packages = package_chain(path)
    parts = [path.stem] + [
        package.name if package.name.isidentifier() else "_towel_package" for package in packages
    ]
    if path.name == "__init__.py":
        parts = parts[1:]
    root = packages[-1].parent if packages else path.parent
    return ".".join(reversed(parts)), root


def _with_probes(request: RevealRequest) -> Tuple[str, List[int]]:
    """The module text with one ``reveal_type`` line per expression before ``line``.

    Returns the text and the line number each probe landed on.
    """
    lines = source_lines(request.source)
    probes = [f"{request.indent}reveal_type({expression})\n" for expression in request.expressions]
    index = request.line - 1
    lines[index:index] = probes
    return "".join(lines), [request.line + offset for offset in range(len(probes))]


def _subtype_probes(
    source: str, pairs: Sequence[Tuple[str, str]]
) -> Tuple[str, Dict[int, int], Dict[int, int]]:
    """The module with one probe function per pair appended, and which lines belong to which pair.

    ``def __towel_probe_i(__towel_value: narrow) -> wide: return __towel_value``:
    an error on the ``return`` line means not a subtype, an error on the
    signature line means a name the checker could not resolve.
    """
    lines = source_lines(source)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    base = len(lines)
    probe_lines: List[str] = ["\n"]
    signature_line: Dict[int, int] = {}
    return_line: Dict[int, int] = {}
    for index, (narrow, wide) in enumerate(pairs):
        signature_line[base + len(probe_lines) + 1] = index
        probe_lines.append(f"def __towel_probe_{index}(__towel_value: {narrow}) -> {wide}:\n")
        return_line[base + len(probe_lines) + 1] = index
        probe_lines.append("    return __towel_value\n")
        probe_lines.append("\n")
    return "".join(lines) + "".join(probe_lines), signature_line, return_line


def _verdicts_from_error_lines(
    count: int,
    error_lines: Iterable[int],
    signature_line: Mapping[int, int],
    return_line: Mapping[int, int],
) -> List[Subtyping]:
    """Read each probe's verdict off the lines the checker reported errors on."""
    verdicts = [Subtyping.YES] * count
    for line in error_lines:
        if line in signature_line:
            verdicts[signature_line[line]] = Subtyping.UNKNOWN
        elif line in return_line and verdicts[return_line[line]] is not Subtyping.UNKNOWN:
            verdicts[return_line[line]] = Subtyping.NO
    return verdicts


@dataclass(frozen=True)
class _BuildSource:
    path: str
    module: str
    text: str


@dataclass(frozen=True)
class _BuildMessages:
    messages: Tuple[str, ...]


def _checker_root(path: Path) -> Path:
    """Nearest checker or packaging root, independently of import-layout inference."""
    path = path.resolve()
    directory = path.parent if path.is_file() or path.suffix in {".py", ".pyi"} else path
    for root in (directory, *directory.parents):
        if any(
            (root / name).is_file()
            for name in (
                "mypy.ini",
                ".mypy.ini",
                "pyrightconfig.json",
                "pyproject.toml",
                "setup.cfg",
                "setup.py",
            )
        ):
            return root
    return find_project_root(directory)


def _mypy_config(root: Path) -> Optional[str]:
    for name in ("mypy.ini", ".mypy.ini", "pyproject.toml", "setup.cfg"):
        path = root / name
        if path.is_file() and (
            name in {"mypy.ini", ".mypy.ini"}
            or (name == "pyproject.toml" and _has_tool_section(root, "mypy"))
            or (name == "setup.cfg" and _has_ini_section(path, "mypy"))
        ):
            return str(path)
    return None


def _configured_root(path: Path, checker: str) -> Optional[Path]:
    """Checker discovery must not stop at an unrelated packaging configuration."""
    path = path.resolve()
    directory = path.parent if path.is_file() or path.suffix in {".py", ".pyi"} else path
    configured = project_configures_mypy if checker == "mypy" else project_configures_pyright
    for root in (directory, *directory.parents):
        if configured(root):
            return root
        if (root / ".git").exists() or (root / ".hg").exists():
            break
    return None


def _source_groups(sources: Mapping[str, str], checker: str) -> Dict[Path, Dict[str, str]]:
    groups: Dict[Path, Dict[str, str]] = {}
    for path, source in sources.items():
        absolute = str(Path(path).resolve())
        groups.setdefault(
            _configured_root(Path(absolute), checker) or _checker_root(Path(absolute)), {}
        )[absolute] = source
    return groups


class MypyInferrer:
    """A persistent, isolated mypy worker with an owned incremental cache.

    Builds run serially in an owned process. mypy's GC, imports and mutable
    globals cannot change this application's state or race between callers.
    Project checking options apply; project plugins and executables do not.
    """

    def __init__(self, cache_dir: Optional[Path] = None) -> None:
        # Cleanup must be valid even if dependency detection/construction fails.
        self._lock = threading.RLock()
        self._process: Optional[subprocess.Popen[bytes]] = None
        self._cache: Optional[tempfile.TemporaryDirectory[str]] = None
        self._closed = False
        self._owner_pid = os.getpid()
        if importlib.util.find_spec("mypy") is None:
            raise ImportError("mypy is not installed")
        if cache_dir is None:
            self._cache = tempfile.TemporaryDirectory(prefix="towel-mypy-")
            cache_dir = Path(self._cache.name)
        self._cache_dir = cache_dir.resolve()
        self._consumer_cache: Dict[Path, Tuple[FrozenSet[str], Sequence[str]]] = {}

    def __call__(self, requests: Sequence[RevealRequest]) -> Mapping[RevealKey, str]:
        return self.reveal(requests)

    def close(self) -> None:
        """Reap the owned worker and remove its cache; safe after partial construction."""
        if self._owner_pid != os.getpid():
            return  # A forked analysis worker never owns its parent's checker.
        with self._lock:
            self._closed = True
            self._stop_worker()
            if self._cache is not None:
                self._cache.cleanup()
                self._cache = None

    def _stop_worker(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass  # A failed worker may already have closed the receiving pipe.
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if process.stdout is not None:
            process.stdout.close()

    def __del__(self) -> None:
        self.close()

    def _consumers(self, root: Path, replacements: Mapping[str, str]) -> Sequence[str]:
        """The unchanged modules that import this change, found once and kept.

        A complete build walks the packages under refactoring, and mypy follows
        imports out of them. What imports *into* them is reached by neither, so
        it is scanned for here: a subclass in another package whose own method
        the new helper collides with is unchanged, unimported and broken by the
        change, and the check that never looked at it reported clean.

        The scan is one ``ast`` pass over the project, and it must stay one: a
        prospective check happens hundreds of times in a run, and each names
        only the few modules it is about. The answer is therefore kept against
        the *modules asked about so far*, not against the set of a single
        request, so those sparse checks reuse the scan the first complete one
        paid for. A request naming a module never seen before -- a second
        project under one oracle -- widens the set and scans once more.
        """

        def namer(path: Path) -> str:
            return _module_name_and_root(path)[0]

        wanted = module_prefixes(replacements, namer)
        remembered = self._consumer_cache.get(root)
        if remembered is not None and wanted <= remembered[0]:
            return remembered[1]
        if remembered is not None:
            wanted |= remembered[0]
        packages = {walked_package(Path(path)) or Path(path).resolve() for path in replacements}
        found = consumers_of(
            root,
            wanted,
            module_name=namer,
            exclude=[package for package in packages if package.is_dir()],
        )
        self._consumer_cache[root] = (frozenset(wanted), found)
        return found

    def _build_errors(
        self,
        sources: Sequence[_BuildSource],
        roots: Sequence[str],
        *,
        complete: bool = False,
        excluded_paths: Sequence[str] = (),
        consumers: Sequence[str] = (),
    ) -> _BuildMessages | CheckFailure:
        if self._owner_pid != os.getpid():
            return CheckFailure("Create a new mypy oracle after fork")
        with self._lock:
            if self._closed:
                return CheckFailure("mypy oracle is closed")
            if not sources:
                return _BuildMessages(())
            root = _configured_root(Path(sources[0].path), "mypy") or _checker_root(
                Path(sources[0].path)
            )
            request = {
                "root": str(root),
                "config": _mypy_config(root),
                "roots": list(roots),
                "sources": {str(Path(source.path).resolve()): source.text for source in sources},
                "complete": complete,
                "excluded_paths": list(excluded_paths),
                "consumers": list(consumers),
                "modules": {str(Path(source.path).resolve()): source.module for source in sources},
            }
            try:
                if self._process is None:
                    self._process = subprocess.Popen(
                        [
                            sys.executable,
                            "-I",
                            str(Path(__file__).with_name("_mypy_worker.py")),
                            str(self._cache_dir),
                        ],
                        bufsize=0,
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        cwd=tempfile.gettempdir(),
                        env=python_tool_environment(),
                    )
                process = self._process
                if process.stdin is None or process.stdout is None:
                    return CheckFailure("mypy worker has no protocol pipes")
                deadline = time.monotonic() + MYPY_TIMEOUT_SECONDS
                pending = (json.dumps(request) + "\n").encode("utf-8")
                written = 0
                response = bytearray()
                input_fd, output_fd = process.stdin.fileno(), process.stdout.fileno()
                os.set_blocking(input_fd, False)
                os.set_blocking(output_fd, False)
                while not response.endswith(b"\n"):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self._stop_worker()
                        return CheckFailure("mypy timed out")
                    readable, writable, _ = select.select(
                        [output_fd], [input_fd] if written < len(pending) else [], [], remaining
                    )
                    if writable:
                        try:
                            written += os.write(
                                input_fd, memoryview(pending)[written : written + 65536]
                            )
                        except BlockingIOError:
                            pass  # Another ready pipe can be consumed before trying again.
                    if readable:
                        try:
                            chunk = os.read(output_fd, 65536)
                        except BlockingIOError:
                            continue
                        if not chunk:
                            self._stop_worker()
                            return CheckFailure("mypy worker exited before returning diagnostics")
                        response.extend(chunk)
                payload = json.loads(response)
            except (OSError, ValueError) as error:
                self._stop_worker()
                return CheckFailure(f"mypy worker failed: {error}")
            if not isinstance(payload, dict):
                return CheckFailure("mypy worker returned an invalid response")
            failure, messages = payload.get("failure"), payload.get("messages")
            if isinstance(failure, str):
                return CheckFailure(failure)
            if failure is not None:
                return CheckFailure("mypy worker returned an invalid failure status")
            if not isinstance(messages, list) or not all(isinstance(m, str) for m in messages):
                return CheckFailure("mypy worker returned invalid diagnostics")
            return _BuildMessages(tuple(m for m in messages if isinstance(m, str)))

    def is_subtype(
        self, file_path: str, source: str, pairs: Sequence[Tuple[str, str]]
    ) -> Sequence[Subtyping]:
        """Whether each ``narrow`` is assignable to its ``wide``, judged in the module's context.

        One probe function per pair is appended to an in-memory copy of the
        module (see :func:`_subtype_probes`); mypy's error lines give the verdicts.
        """
        if not pairs:
            return []
        text, signature_line, return_line = _subtype_probes(source, pairs)
        module, root = _module_name_and_root(Path(file_path))
        result = self._build_errors([_BuildSource(file_path, module, text)], [str(root)])
        if isinstance(result, CheckFailure):
            LOG.warning("mypy subtype check failed: %s", result.reason)
            return [Subtyping.UNKNOWN] * len(pairs)
        errors = result.messages
        error_lines = [
            int(match.group("line"))
            for match in (_ERROR.match(message) for message in errors)
            if match is not None and _same_file(match.group("path"), file_path)
        ]
        return _verdicts_from_error_lines(len(pairs), error_lines, signature_line, return_line)

    def check(self, file_path: str, source: str) -> CheckResult:
        return self.check_project({file_path: source})

    def check_project(
        self, sources: Mapping[str, str], *, excluded_paths: Sequence[str] = ()
    ) -> CheckResult:
        errors: List[TypeDiagnostic] = []
        for root, replacements in _source_groups(sources, "mypy").items():
            builds = [
                _BuildSource(path, _module_name_and_root(Path(path))[0], source)
                for path, source in replacements.items()
            ]
            result = self._build_errors(
                builds,
                [str(root)],
                complete=True,
                excluded_paths=excluded_paths,
                consumers=self._consumers(root, replacements),
            )
            if isinstance(result, CheckFailure):
                return result
            for message in result.messages:
                match = _ERROR.match(message)
                if match is not None:
                    path = str((root / match.group("path")).resolve())
                    errors.append(
                        TypeDiagnostic(
                            path, message[match.end() :].strip(), int(match.group("line"))
                        )
                    )
        return CheckSuccess(tuple(errors))

    def reveal(self, requests: Sequence[RevealRequest]) -> Mapping[RevealKey, str]:
        """Reveal each request through one mypy build per module, probes appended in memory."""
        by_file: Dict[str, List[RevealRequest]] = {}
        for request in requests:
            by_file.setdefault(request.file_path, []).append(request)
        sources: List[_BuildSource] = []
        roots: List[str] = []
        probe_lines: Dict[Tuple[str, int], RevealKey] = {}
        for file_path, file_requests in by_file.items():
            text = file_requests[0].source
            # Insert from the bottom up so earlier line numbers stay valid,
            # then account for the shift each earlier insertion causes.
            ordered = sorted(file_requests, key=lambda request: request.line)
            shift = 0
            landed: List[Tuple[RevealRequest, List[int]]] = []
            for request in reversed(ordered):
                text, _ = _with_probes(
                    RevealRequest(
                        file_path, text, request.line, request.indent, request.expressions
                    )
                )
            for request in ordered:
                lines = [
                    request.line + shift + offset for offset in range(len(request.expressions))
                ]
                landed.append((request, lines))
                shift += len(request.expressions)
            for request, lines in landed:
                for index, line in enumerate(lines):
                    probe_lines[(os.path.abspath(file_path), line)] = (
                        file_path,
                        request.line,
                        index,
                    )
            module, root = _module_name_and_root(Path(file_path))
            sources.append(_BuildSource(file_path, module, text))
            if str(root) not in roots:
                roots.append(str(root))
        if not sources:
            return {}
        result = self._build_errors(sources, roots)
        if isinstance(result, CheckFailure):
            LOG.warning("mypy inference failed: %s", result.reason)
            return {}
        errors = result.messages
        revealed: Dict[RevealKey, str] = {}
        ambiguous: set[RevealKey] = set()
        for message in errors:
            match = _REVEALED.match(message)
            if match is None:
                continue
            key = probe_lines.get((os.path.abspath(match.group("path")), int(match.group("line"))))
            if key is not None and key not in ambiguous:
                kind = match.group("type")
                if key in revealed and revealed[key] != kind:
                    # mypy checks constrained generic bodies once per concrete
                    # specialization. Keep neither branch as the whole type.
                    ambiguous.add(key)
                    del revealed[key]
                else:
                    revealed[key] = kind
        return revealed


_PENDING_PROBES: "set[Path]" = set()
"""Probe files not yet removed; an interpreter exit removes them, a kill cannot."""


MYPY_TIMEOUT_SECONDS = 600.0


def _unlink_quietly(path: Path) -> None:
    """Remove ``path`` if it is still there; a probe that is already gone is fine."""
    try:
        path.unlink()
    except OSError:
        pass


def _remove_pending_probes() -> None:
    for probe in list(_PENDING_PROBES):
        _unlink_quietly(probe)
        _PENDING_PROBES.discard(probe)


atexit.register(_remove_pending_probes)


@contextmanager
def _probe_file(original: Path, text: str) -> Iterator[Path]:
    """A sibling of ``original`` holding ``text`` for the duration of the block.

    Pyright reads files, so the probed module must exist on disk in its own
    package for imports to resolve. The file is created exclusively with a
    unique name and owner-only permissions, so it never follows a symlink or
    collides with a concurrent run, and it is removed when the block ends or
    at interpreter exit, whichever comes first.
    """
    descriptor, name = tempfile.mkstemp(
        prefix=f"{PROBE_PREFIX}{original.stem}_", suffix=".py", dir=original.parent, text=True
    )
    probe = Path(name)
    _PENDING_PROBES.add(probe)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        yield probe
    finally:
        _unlink_quietly(probe)
        _PENDING_PROBES.discard(probe)


class _PyrightPosition(TypedDict, total=False):
    """A zero-based position in pyright's JSON output."""

    line: int
    character: int


class _PyrightRange(TypedDict, total=False):
    """The span of a pyright diagnostic."""

    start: _PyrightPosition
    end: _PyrightPosition


class _PyrightDiagnostic(TypedDict, total=False):
    """One entry of ``generalDiagnostics`` in ``pyright --outputjson``."""

    file: str
    severity: str
    message: str
    rule: str
    range: _PyrightRange


@dataclass(frozen=True)
class _PyrightDiagnostics:
    diagnostics: Tuple[_PyrightDiagnostic, ...]


class PyrightOracle:
    """A ``TypeOracle`` backed by the pyright command.

    Pyright reads files, so a probed copy of the module is written as a
    temporary sibling (same package, so its imports resolve) and removed
    afterwards. Raises ``ImportError`` at construction when pyright is not
    installed; it is part of the ``types`` extra.
    """

    def __init__(self, *, language_server: bool = True) -> None:
        command = _pyright_command()
        if command is None:
            raise ImportError("pyright is not installed")
        self._command: List[str] = command
        # The command line is the fallback and reaches the same verdicts, so it
        # stays available: a caller that must not keep a checker process alive,
        # and the tests covering that path, ask for it here.
        self._server: Optional[List[str]] = (
            _pyright_langserver_command() if language_server else None
        )
        self._warmed: Dict[Tuple[Path, Tuple[str, ...]], _WarmProject] = {}
        # Whether a session ever answered, which abandoning one does not undo.
        self.answered_from_a_session = False

    def close(self) -> None:
        """Stop every language server this oracle started and drop its copies."""
        for warm in self._warmed.values():
            warm.close()
        self._warmed.clear()

    def _warm(self, root: Path, excluded_paths: Sequence[str]) -> Optional[_WarmProject]:
        """The copy and live server for ``root``, made on first use.

        A server that cannot be started or that fails mid-run disables the whole
        fast path for this oracle: the command line reaches the same verdict,
        and silently checking some candidates one way and some the other would
        make a refusal impossible to reason about. Exclusions belong to the copy,
        so a call that excludes different paths gets a copy of its own.
        """
        if self._server is None:
            return None
        key = (root, tuple(sorted(excluded_paths)))
        existing = self._warmed.get(key)
        if existing is not None:
            return existing
        try:
            snapshot = CheckerSnapshot(root, excluded_paths=excluded_paths)
        except (OSError, ValueError, UnicodeError) as error:
            LOG.warning("could not copy %s for pyright (%s); using the command line", root, error)
            return None
        try:
            session = PyrightSession(
                self._server,
                snapshot.tree,
                sys.executable,
                environment=python_tool_environment(),
            )
        except SessionFailure as error:
            snapshot.close()
            LOG.warning("pyright language server unavailable (%s); using the command line", error)
            self._server = None
            return None
        warm = _WarmProject(snapshot, session)
        self._warmed[key] = warm
        self.answered_from_a_session = True
        return warm

    def stop_language_servers(self) -> None:
        """Close every warm project; this oracle answers from the command line after."""
        self._server = None
        for warm in self._warmed.values():
            warm.close()
        self._warmed.clear()

    def _abandon_sessions(self, error: SessionFailure) -> None:
        LOG.warning("pyright language server failed (%s); using the command line", error)
        self._server = None
        for warm in self._warmed.values():
            warm.close()
        self._warmed.clear()

    def _diagnostics(self, file_path: str, text: str) -> _PyrightDiagnostics | CheckFailure:
        original = Path(file_path).resolve()
        root = _configured_root(original, "pyright") or _checker_root(original)
        warm = self._warm(root, ())
        if warm is not None:
            try:
                published = warm.diagnostics({str(original): text})
            except SessionFailure as error:
                self._abandon_sessions(error)
            else:
                # Only this module's diagnostics: a probe's answer is read from
                # the line it landed on, and the command line saw this file alone.
                return _PyrightDiagnostics(
                    tuple(_as_entry(entry) for entry in published.get(str(original), ()))
                )
        try:
            with _probe_file(original, text) as probe:
                return self._run_diagnostics([str(probe)], original.parent)
        except OSError as error:
            return CheckFailure(f"Could not create a pyright probe: {error}")

    def _run_diagnostics(
        self, paths: Sequence[str], directory: Path, *, project: bool = False
    ) -> _PyrightDiagnostics | CheckFailure:
        project_arguments = ["--project", str(directory)] if project else []
        try:
            completed = subprocess.run(
                [
                    *self._command,
                    "--outputjson",
                    "--pythonpath",
                    sys.executable,
                    *project_arguments,
                    *paths,
                ],
                capture_output=True,
                text=True,
                cwd=str(directory),
                check=False,
                timeout=PYRIGHT_TIMEOUT_SECONDS,
                env=python_tool_environment(),
            )
        except (subprocess.TimeoutExpired, OSError) as error:
            reason = (
                f"pyright timed out after {error.timeout} s"
                if isinstance(error, subprocess.TimeoutExpired)
                else f"pyright failed: {error}"
            )
            LOG.warning("%s; checker result unavailable", reason)
            return CheckFailure(reason)
        output = completed.stdout
        start, end = output.find("{"), output.rfind("}")
        if start < 0 or end < 0:
            reason = f"pyright produced no JSON: {completed.stderr.strip()}"
            LOG.warning(reason)
            return CheckFailure(reason)
        try:
            data = json.loads(output[start : end + 1])
        except json.JSONDecodeError as error:
            reason = f"pyright output is not JSON: {error}"
            LOG.warning(reason)
            return CheckFailure(reason)
        diagnostics = data.get("generalDiagnostics") if isinstance(data, dict) else None
        if completed.returncode not in {0, 1} or not isinstance(diagnostics, list):
            reason = f"pyright failed or returned an unexpected shape (exit {completed.returncode})"
            LOG.warning(reason)
            return CheckFailure(reason)
        validated: List[_PyrightDiagnostic] = []
        for diagnostic in diagnostics:
            if not isinstance(diagnostic, dict) or not all(
                isinstance(diagnostic.get(key), str) for key in ("file", "severity", "message")
            ):
                return CheckFailure("pyright returned a malformed diagnostic")
            severity = diagnostic["severity"]
            if severity not in {"error", "warning", "information"}:
                return CheckFailure("pyright returned an invalid diagnostic severity")
            location = diagnostic.get("range")
            if severity == "error" and (
                not isinstance(location, dict)
                or not isinstance(location.get("start"), dict)
                or not isinstance(location["start"].get("line"), int)
                or isinstance(location["start"].get("line"), bool)
                or location["start"]["line"] < 0
            ):
                return CheckFailure("pyright error has no valid source position")
            if location is not None:
                if not isinstance(location, dict):
                    return CheckFailure("pyright returned an invalid diagnostic range")
                position = location.get("start")
                if position is not None and (
                    not isinstance(position, dict) or not isinstance(position.get("line"), int)
                ):
                    return CheckFailure("pyright returned an invalid diagnostic position")
            validated.append(cast(_PyrightDiagnostic, diagnostic))
        has_errors = any(item.get("severity") == "error" for item in validated)
        if (completed.returncode == 1) != has_errors:
            return CheckFailure("pyright exit status disagrees with its diagnostics")
        return _PyrightDiagnostics(tuple(validated))

    @staticmethod
    def _line(diagnostic: _PyrightDiagnostic) -> int:
        """The one-based line of a diagnostic, or 0 when it carries no position."""
        start = diagnostic.get("range", {}).get("start")
        return start.get("line", -1) + 1 if start is not None else 0

    def reveal(self, requests: Sequence[RevealRequest]) -> Mapping[RevealKey, str]:
        """Reveal each request by running pyright on a probe copy of its module."""
        revealed: Dict[RevealKey, str] = {}
        by_file: Dict[str, List[RevealRequest]] = {}
        for request in requests:
            by_file.setdefault(request.file_path, []).append(request)
        for file_path, file_requests in by_file.items():
            text = file_requests[0].source
            ordered = sorted(file_requests, key=lambda request: request.line)
            for request in reversed(ordered):
                text, _ = _with_probes(
                    RevealRequest(
                        file_path, text, request.line, request.indent, request.expressions
                    )
                )
            probe_lines: Dict[int, RevealKey] = {}
            shift = 0
            for request in ordered:
                for index in range(len(request.expressions)):
                    probe_lines[request.line + shift + index] = (file_path, request.line, index)
                shift += len(request.expressions)
            result = self._diagnostics(file_path, text)
            if isinstance(result, CheckFailure):
                continue
            for diagnostic in result.diagnostics:
                match = _PYRIGHT_REVEALED.match(str(diagnostic.get("message", "")))
                key = probe_lines.get(self._line(diagnostic))
                if match is not None and key is not None:
                    revealed[key] = match.group("type")
        return revealed

    def is_subtype(
        self, file_path: str, source: str, pairs: Sequence[Tuple[str, str]]
    ) -> Sequence[Subtyping]:
        """Pyright's verdict on each pair, read from the errors its probe functions raise."""
        if not pairs:
            return []
        text, signature_line, return_line = _subtype_probes(source, pairs)
        result = self._diagnostics(file_path, text)
        if isinstance(result, CheckFailure):
            return [Subtyping.UNKNOWN] * len(pairs)
        error_lines = [
            self._line(diagnostic)
            for diagnostic in result.diagnostics
            if diagnostic.get("severity") == "error"
        ]
        return _verdicts_from_error_lines(len(pairs), error_lines, signature_line, return_line)

    def check(self, file_path: str, source: str) -> CheckResult:
        return self.check_project({file_path: source})

    def check_project(
        self, sources: Mapping[str, str], *, excluded_paths: Sequence[str] = ()
    ) -> CheckResult:
        errors: List[TypeDiagnostic] = []
        for root, replacements in _source_groups(sources, "pyright").items():
            try:
                served = self._check_with_session(root, replacements, excluded_paths)
            except (OSError, ValueError, UnicodeError) as error:
                # The project is being read while it is being refactored, so a
                # file can go between listing it and reading it. The cold path
                # below has always reported that as a failed check rather than
                # letting it end the run; the warm one now does too.
                LOG.warning(
                    "pyright session could not read %s (%s); using the command line", root, error
                )
                self._abandon_sessions(SessionFailure(str(error)))
                served = None
            if served is not None:
                if isinstance(served, CheckFailure):
                    return served
                errors.extend(served.errors)
                continue
            try:
                with checker_snapshot(
                    root, replacements, excluded_paths=excluded_paths
                ) as snapshot:
                    # A positional directory overrides both configured include
                    # and exclude lists. Project mode preserves their scope.
                    result = self._run_diagnostics([], snapshot, project=True)
                    if isinstance(result, CheckFailure):
                        return result
                    for diagnostic in result.diagnostics:
                        if diagnostic.get("severity") != "error":
                            continue
                        path = Path(diagnostic["file"])
                        if path.is_relative_to(snapshot):
                            path = root / path.relative_to(snapshot)
                        message = (
                            f"pyright: {diagnostic.get('rule') or ''}: " f"{diagnostic['message']}"
                        )
                        start = diagnostic.get("range", {}).get("start", {}).get("line")
                        errors.append(
                            TypeDiagnostic(
                                str(path),
                                message.replace(str(snapshot), str(root)),
                                None if start is None else start + 1,
                            )
                        )
            except (OSError, ValueError, UnicodeError) as error:
                return CheckFailure(f"Could not snapshot the project for pyright: {error}")
        return CheckSuccess(tuple(errors))

    def _check_with_session(
        self, root: Path, replacements: Mapping[str, str], excluded_paths: Sequence[str]
    ) -> Optional[CheckResult]:
        """The project checked through its warm copy, or ``None`` to check it cold."""
        warm = self._warm(root, tuple(excluded_paths))
        if warm is None:
            return None
        try:
            published = warm.diagnostics(replacements)
        except SessionFailure as error:
            self._abandon_sessions(error)
            return None
        errors = [
            TypeDiagnostic(path, f"pyright: {entry.rule}: {entry.message}", entry.line + 1)
            for path, entries in published.items()
            for entry in entries
            if entry.severity == "error"
        ]
        return CheckSuccess(tuple(errors))


PYRIGHT_TIMEOUT_SECONDS = 600.0
"""How long one pyright run may take before Towel proceeds without its answer."""

_PYRIGHT_REVEALED = re.compile(r'^Type of ".*" is "(?P<type>.*)"$', re.DOTALL)


VERDICT_CACHE_ENTRIES = 256
"""How many distinct candidates a warm project remembers the verdict for."""


class _WarmProject:
    """A private copy of a project and the live checker watching it.

    A fixed point re-pairs the project after every applied change and so
    reconsiders proposals it has already weighed; remembering the verdicts
    spares the checker that work. A verdict is about a candidate over the project
    as it stood, so it is remembered under both: an in-place run changes the
    project with every refactoring it applies, and the copy follows it.
    """

    def __init__(self, snapshot: CheckerSnapshot, session: PyrightSession) -> None:
        self._snapshot = snapshot
        self._session = session
        self._verdicts: BoundedCache[str, Dict[str, List[Diagnostic]]] = BoundedCache(
            VERDICT_CACHE_ENTRIES
        )

    def diagnostics(self, replacements: Mapping[str, str]) -> Dict[str, List[Diagnostic]]:
        """Diagnostics for the project as ``replacements`` would leave it.

        Paths come back as the project's own, not the copy's, so a caller never
        sees where the check happened.
        """
        followed = self._snapshot.follow_project()
        key = f"{self._snapshot.revision}:{_candidate_key(replacements)}"
        remembered = self._verdicts.get(key)
        if remembered is not None:
            return {path: list(entries) for path, entries in remembered.items()}
        changed = self._snapshot.show(replacements, after=followed)
        published = self._session.diagnostics_after(
            {change.path: _FILE_CHANGES[change.kind] for change in changed},
            beside=[self._snapshot.path_of(name) for name in sorted(replacements)],
        )
        restored: Dict[str, List[Diagnostic]] = {}
        for path, entries in published.items():
            original = self._snapshot.original_of(path)
            restored[original] = [replace(entry, path=original) for entry in entries]
        self._verdicts[key] = restored
        return {path: list(entries) for path, entries in restored.items()}

    def close(self) -> None:
        self._session.close()
        self._snapshot.close()


_FILE_CHANGES: Final[Mapping[str, FileChange]] = {
    "created": FileChange.CREATED,
    "changed": FileChange.CHANGED,
    "deleted": FileChange.DELETED,
}


def _candidate_key(replacements: Mapping[str, str]) -> str:
    """A digest of exactly what a candidate proposes, and of nothing else."""
    digest = hashlib.sha256()
    for path in sorted(replacements):
        digest.update(path.encode("utf-8", "surrogatepass"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(replacements[path].encode("utf-8", "surrogatepass")).digest())
    return digest.hexdigest()


def _as_entry(diagnostic: Diagnostic) -> _PyrightDiagnostic:
    """One server diagnostic in the shape the command line's JSON produces."""
    return _PyrightDiagnostic(
        file=diagnostic.path,
        severity=diagnostic.severity,
        message=diagnostic.message,
        rule=diagnostic.rule,
        range=_PyrightRange(start=_PyrightPosition(line=diagnostic.line)),
    )


def _pyright_langserver_command() -> Optional[List[str]]:
    """The language server for this interpreter's pyright, or a PATH one."""
    if importlib.util.find_spec("pyright") is not None:
        return [sys.executable, "-I", "-m", "pyright.langserver"]
    executable = shutil.which("pyright-langserver")
    return [executable] if executable else None


def _pyright_command() -> Optional[List[str]]:
    """Run this interpreter's checker in isolation, else a configured PATH tool."""
    if importlib.util.find_spec("pyright") is None:
        executable = shutil.which("pyright")
        return [executable] if executable else None
    # -I also excludes inherited PYTHONPATH and user-site startup hooks. -P
    # alone only removes cwd and still lets source-owned sitecustomize execute.
    return [sys.executable, "-I", "-m", "pyright"]


def stop_language_servers(oracle: object) -> None:
    """Drop every warm session behind ``oracle``; later checks take the command line.

    The oracle keeps whatever else a run gave it -- where its output stands for
    its input, what it must not look at -- so a check made after this is the
    same question asked of a checker that starts from nothing.
    """
    if isinstance(oracle, CombinedOracle):
        for one in oracle.checkers:
            stop_language_servers(one)
    elif isinstance(oracle, _RelocatedOracle):
        stop_language_servers(oracle.inner)
    elif isinstance(oracle, PyrightOracle):
        oracle.stop_language_servers()


def served_by_a_language_server(oracle: object) -> bool:
    """Whether any checker behind ``oracle`` has ever answered from a warm session.

    Ever, not currently. A session that fails part way through a run is
    abandoned and the rest of the run is checked from the command line, which
    is exactly when the verdicts it already gave are most worth confirming.
    """
    if isinstance(oracle, CombinedOracle):
        return any(served_by_a_language_server(one) for one in oracle.checkers)
    if isinstance(oracle, _RelocatedOracle):
        return served_by_a_language_server(oracle.inner)
    return isinstance(oracle, PyrightOracle) and oracle.answered_from_a_session


class CombinedOracle:
    """Infers with one checker and verifies with every configured one."""

    def __init__(self, primary: TypeOracle, others: Sequence[TypeOracle]) -> None:
        self._primary = primary
        self._all = [primary, *others]

    @property
    def checkers(self) -> Sequence[TypeOracle]:
        """Every checker a candidate must satisfy."""
        return tuple(self._all)

    def reveal(self, requests: Sequence[RevealRequest]) -> Mapping[RevealKey, str]:
        """The primary oracle's revelations."""
        return self._primary.reveal(requests)

    def is_subtype(
        self, file_path: str, source: str, pairs: Sequence[Tuple[str, str]]
    ) -> Sequence[Subtyping]:
        """The primary oracle's verdicts."""
        return self._primary.is_subtype(file_path, source, pairs)

    def check(self, file_path: str, source: str) -> CheckResult:
        return self.check_project({file_path: source})

    def check_project(
        self, sources: Mapping[str, str], *, excluded_paths: Sequence[str] = ()
    ) -> CheckResult:
        errors: List[TypeDiagnostic] = []
        for oracle in self._all:
            result = oracle.check_project(sources, excluded_paths=excluded_paths)
            if isinstance(result, CheckFailure):
                return result
            errors.extend(result.errors)
            if errors:
                # Every configured checker must accept, so the first rejection
                # settles it. Asking the rest costs a whole-project check each
                # to reach a verdict already known, and a candidate nothing will
                # accept is exactly where a run spends its time.
                break
        return CheckSuccess(tuple(errors))

    def close(self) -> None:
        for oracle in self._all:
            oracle.close()


class _RelocatedOracle:
    """An output copy checked at its original project's logical module locations."""

    @property
    def inner(self) -> TypeOracle:
        """The checker this one relocates."""
        return self._oracle

    def __init__(self, oracle: TypeOracle, source: Path, destination: Path) -> None:
        self._oracle = oracle
        self._source = source
        self._destination = destination
        self._directory = source.is_dir()

    def _original(self, path: str) -> str:
        absolute = Path(path).resolve()
        if absolute == self._destination:
            return str(self._source)
        if self._directory and absolute.is_relative_to(self._destination):
            return str(self._source / absolute.relative_to(self._destination))
        return str(absolute)

    def _output(self, path: str) -> str:
        absolute = Path(path)
        if absolute == self._source:
            return str(self._destination)
        if self._directory and absolute.is_relative_to(self._source):
            return str(self._destination / absolute.relative_to(self._source))
        return path

    def reveal(self, requests: Sequence[RevealRequest]) -> Mapping[RevealKey, str]:
        originals = [
            RevealRequest(
                self._original(request.file_path),
                request.source,
                request.line,
                request.indent,
                request.expressions,
            )
            for request in requests
        ]
        return {
            (self._output(path), line, index): value
            for (path, line, index), value in self._oracle.reveal(originals).items()
        }

    def is_subtype(
        self, file_path: str, source: str, pairs: Sequence[Tuple[str, str]]
    ) -> Sequence[Subtyping]:
        return self._oracle.is_subtype(self._original(file_path), source, pairs)

    def check(self, file_path: str, source: str) -> CheckResult:
        return self.check_project({file_path: source})

    def check_project(
        self, sources: Mapping[str, str], *, excluded_paths: Sequence[str] = ()
    ) -> CheckResult:
        try:
            current: Dict[str, str] = {}
            if self._directory:
                for parent, directories, files in os.walk(self._destination):
                    directory = Path(parent)
                    directories[:] = [
                        name
                        for name in directories
                        if name
                        not in {
                            ".git",
                            ".hg",
                            ".svn",
                            ".mypy_cache",
                            ".pytest_cache",
                            ".ruff_cache",
                            "__pycache__",
                            "venv",
                            "env",
                            "node_modules",
                        }
                        and not (directory / name / "pyvenv.cfg").is_file()
                    ]
                    for name in files:
                        path = directory / name
                        if path.suffix in {".py", ".pyi"} and not is_probe_file(path):
                            current[self._original(str(path))] = read_source(path)
            else:
                current[str(self._source)] = read_source(self._destination)
            current.update({self._original(path): source for path, source in sources.items()})
        except (OSError, ValueError, UnicodeError, SyntaxError) as error:
            return CheckFailure(f"Could not read the complete output copy: {error}")
        result = self._oracle.check_project(
            current, excluded_paths=(*excluded_paths, str(self._destination))
        )
        if isinstance(result, CheckFailure):
            return result
        return CheckSuccess(
            tuple(
                TypeDiagnostic(self._output(error.path), error.message, error.line)
                for error in result.errors
            )
        )

    def close(self) -> None:
        self._oracle.close()


def relocate_oracle(oracle: TypeOracle, source: Path, destination: Path) -> TypeOracle:
    """Keep input configuration and consumers while checking a separate output copy."""
    source, destination = source.resolve(), destination.resolve()
    return oracle if source == destination else _RelocatedOracle(oracle, source, destination)


def project_configures_mypy(root: Path) -> bool:
    """Whether the project configures mypy (``mypy.ini``, ``[mypy]`` in setup.cfg, or ``[tool.mypy]``)."""
    if any((root / name).is_file() for name in ("mypy.ini", ".mypy.ini")):
        return True
    if _has_ini_section(root / "setup.cfg", "mypy"):
        return True
    return _has_tool_section(root, "mypy")


def project_configures_pyright(root: Path) -> bool:
    """Whether the project configures pyright (``pyrightconfig.json`` or ``[tool.pyright]``)."""
    return (root / "pyrightconfig.json").is_file() or _has_tool_section(root, "pyright")


def _has_tool_section(root: Path, name: str) -> bool:
    tool = load_pyproject(root).get("tool", {})
    return isinstance(tool, dict) and isinstance(tool.get(name), dict)


def _has_ini_section(path: Path, section: str) -> bool:
    if not path.is_file():
        return False
    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, OSError, UnicodeError):
        return False
    return parser.has_section(section)


def type_oracle_for_project(path: Path) -> ToolChoice[TypeOracle]:
    """The checker the project configures, and a note on what was chosen.

    A project that configures mypy gets mypy; one that configures pyright
    gets pyright; one that configures both infers with mypy and verifies
    with both, so its own check stays green; one that configures neither
    gets mypy when installed, else pyright. The note names any configured
    checker that is not installed.
    """
    wants_mypy = _configured_root(path, "mypy") is not None
    wants_pyright = _configured_root(path, "pyright") is not None
    mypy: Optional[TypeOracle] = None
    pyright: Optional[TypeOracle] = None
    notes: List[str] = []
    if wants_mypy or not wants_pyright:
        try:
            mypy = MypyInferrer()
        except ImportError:
            if wants_mypy:
                notes.append("mypy is configured but not installed")
    if wants_pyright or mypy is None:
        try:
            pyright = PyrightOracle()
        except ImportError:
            if wants_pyright:
                notes.append("pyright is configured but not installed")
    if mypy is not None and pyright is not None:
        return ToolChoice(
            CombinedOracle(mypy, [pyright]),
            "; ".join(["mypy for inference, mypy and pyright for verification", *notes]),
        )
    if mypy is not None:
        return ToolChoice(mypy, "; ".join(["mypy", *notes]))
    if pyright is not None:
        return ToolChoice(pyright, "; ".join(["pyright", *notes]))
    return ToolChoice(None, "; ".join(notes) if notes else "neither mypy nor pyright is installed")
