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
subtype of another (``is_subtype``), and whether a file type-checks
(``check``). :class:`MypyInferrer` builds a copy of the site's module in
memory with ``reveal_type(<expression>)`` inserted where the block begins,
so names resolve as they do at the call, and asks subtyping through probe
functions ``def _probe(v: narrow) -> wide: return v`` appended to the copy,
so the relation is mypy's own; nothing is written to disk except mypy's
cache, which lives for the inferrer's lifetime so later proposals in the
same run rebuild incrementally. :class:`PyrightOracle` does the same through
the pyright command on a temporary sibling file. :class:`CombinedOracle`
infers with one checker and verifies with several, and
:func:`type_oracle_for_project` picks them from the project's configuration.

The answers are strings in the checker's spelling, which
``towel.unification.annotations`` turns into annotations only when every
name in them resolves where the helper is defined.
"""

from __future__ import annotations

import configparser
from contextlib import contextmanager
from dataclasses import dataclass
import json
import atexit
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
    Iterable,
    Iterator,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    TYPE_CHECKING,
    Tuple,
    TypedDict,
    cast,
)

from .diagnostics import LOG
from .project_tools import ToolChoice

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mypy.options import Options

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


class TypeOracle(Protocol):
    """What the annotation writer asks a type checker.

    ``reveal`` maps each requested expression to the checker's spelling of
    its type, when known. ``is_subtype`` answers, for each ``(narrow, wide)``
    pair spelled as annotations in the given module, whether ``narrow`` is
    assignable to ``wide``: True, False, or None when the checker could not
    judge (a name it cannot resolve).
    """

    def reveal(self, requests: Sequence[RevealRequest]) -> Mapping[RevealKey, str]:
        """The checker's spelling of each requested expression's type."""
        raise NotImplementedError

    def is_subtype(
        self, file_path: str, source: str, pairs: Sequence[Tuple[str, str]]
    ) -> Sequence[Subtyping]:
        """Whether each narrow type is assignable to its wide type, in the module's context."""
        raise NotImplementedError

    def check(self, file_path: str, source: str) -> Sequence[str]:
        """The checker's error messages for ``source`` as ``file_path``, without positions."""
        raise NotImplementedError


_ERROR = re.compile(r"^(?P<path>.*?):(?P<line>\d+):(?:\d+:)? error: ")
_REVEALED = re.compile(
    r'^(?P<path>.*?):(?P<line>\d+):(?:\d+:)? note: Revealed type is "(?P<type>.*)"$'
)


def _module_name_and_root(path: Path) -> Tuple[str, Path]:
    """The dotted module name of ``path`` and the directory that must be on the search path.

    A package directory need not be a valid identifier: an out-of-place
    refactoring writes the package under an arbitrary output name, and mypy
    spells that name into every type it reveals. Such a component becomes a
    placeholder identifier, which the annotation writer then drops in favour
    of the name the host module binds.
    """
    parts = [path.stem]
    directory = path.parent
    while (directory / "__init__.py").is_file():
        parts.append(directory.name if directory.name.isidentifier() else "_towel_package")
        parent = directory.parent
        if parent == directory:
            break
        directory = parent
    if path.name == "__init__.py":
        parts = parts[1:]
    return ".".join(reversed(parts)), directory


def _with_probes(request: RevealRequest) -> Tuple[str, List[int]]:
    """The module text with one ``reveal_type`` line per expression before ``line``.

    Returns the text and the line number each probe landed on.
    """
    lines = request.source.splitlines(keepends=True)
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
    lines = source.splitlines(keepends=True)
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


class MypyInferrer:
    """A ``TypeOracle`` backed by mypy's in-process build.

    Raises ``ImportError`` at construction when mypy is not installed; install
    the ``types`` extra (``pip install "code-towel[types]"``) to provide it.
    """

    def __init__(self, cache_dir: Optional[Path] = None) -> None:
        from mypy import build  # noqa: F401  (import error surfaces here)

        self._cache: Optional[tempfile.TemporaryDirectory[str]] = None
        if cache_dir is None:
            self._cache = tempfile.TemporaryDirectory(prefix="towel-mypy-")
            cache_dir = Path(self._cache.name)
        self._cache_dir = cache_dir

    def __call__(self, requests: Sequence[RevealRequest]) -> Mapping[RevealKey, str]:
        return self.reveal(requests)

    def _options(self, roots: Sequence[str]) -> "Options":
        from mypy.options import Options

        options = Options()
        options.ignore_missing_imports = True
        options.follow_imports = "silent"
        options.incremental = True
        options.cache_dir = str(self._cache_dir)
        options.check_untyped_defs = True
        options.explicit_package_bases = True
        options.mypy_path = list(roots)
        options.hide_error_codes = True
        return options

    def is_subtype(
        self, file_path: str, source: str, pairs: Sequence[Tuple[str, str]]
    ) -> Sequence[Subtyping]:
        """Whether each ``narrow`` is assignable to its ``wide``, judged in the module's context.

        One probe function per pair is appended to an in-memory copy of the
        module (see :func:`_subtype_probes`); mypy's error lines give the verdicts.
        """
        from mypy import build
        from mypy.build import BuildSource
        from mypy.errors import CompileError

        if not pairs:
            return []
        text, signature_line, return_line = _subtype_probes(source, pairs)
        module, root = _module_name_and_root(Path(file_path))
        try:
            result = build.build(
                sources=[BuildSource(file_path, module, text)], options=self._options([str(root)])
            )
        except CompileError:
            return [Subtyping.UNKNOWN] * len(pairs)
        error_lines = [
            int(match.group("line"))
            for match in (_ERROR.match(message) for message in result.errors)
            if match is not None and match.group("path") == file_path
        ]
        return _verdicts_from_error_lines(len(pairs), error_lines, signature_line, return_line)

    def check(self, file_path: str, source: str) -> Sequence[str]:
        """Error messages mypy reports for ``source`` in place of ``file_path``.

        Positions are stripped so two versions of a file can be compared for
        new errors regardless of where lines moved.
        """
        from mypy import build
        from mypy.build import BuildSource
        from mypy.errors import CompileError

        module, root = _module_name_and_root(Path(file_path))
        try:
            result = build.build(
                sources=[BuildSource(file_path, module, source)], options=self._options([str(root)])
            )
        except CompileError as error:
            return [line for line in error.messages if "error:" in line]
        messages: List[str] = []
        for message in result.errors:
            match = _ERROR.match(message)
            if match is not None and match.group("path") == file_path:
                messages.append(message[match.end() :].strip())
        return messages

    def reveal(self, requests: Sequence[RevealRequest]) -> Mapping[RevealKey, str]:
        """Reveal each request through one mypy build per module, probes appended in memory."""
        from mypy import build
        from mypy.build import BuildSource
        from mypy.errors import CompileError

        by_file: Dict[str, List[RevealRequest]] = {}
        for request in requests:
            by_file.setdefault(request.file_path, []).append(request)
        sources: List[BuildSource] = []
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
                    probe_lines[(file_path, line)] = (file_path, request.line, index)
            module, root = _module_name_and_root(Path(file_path))
            sources.append(BuildSource(file_path, module, text))
            if str(root) not in roots:
                roots.append(str(root))
        if not sources:
            return {}
        try:
            result = build.build(sources=sources, options=self._options(roots))
        except CompileError as error:
            LOG.warning("mypy could not build %s; no types inferred there: %s", roots, error)
            return {}
        revealed: Dict[RevealKey, str] = {}
        for message in result.errors:
            match = _REVEALED.match(message)
            if match is None:
                continue
            key = probe_lines.get((match.group("path"), int(match.group("line"))))
            if key is not None:
                revealed[key] = match.group("type")
        return revealed


_PENDING_PROBES: "set[Path]" = set()
"""Probe files not yet removed; an interpreter exit removes them, a kill cannot."""


def _remove_pending_probes() -> None:
    for probe in list(_PENDING_PROBES):
        try:
            probe.unlink()
        except OSError:
            pass
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
        prefix=f"_towel_probe_{original.stem}_", suffix=".py", dir=original.parent, text=True
    )
    probe = Path(name)
    _PENDING_PROBES.add(probe)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        yield probe
    finally:
        try:
            probe.unlink()
        except OSError:
            pass
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

    severity: str
    message: str
    rule: str
    range: _PyrightRange


class PyrightOracle:
    """A ``TypeOracle`` backed by the pyright command.

    Pyright reads files, so a probed copy of the module is written as a
    temporary sibling (same package, so its imports resolve) and removed
    afterwards. Raises ``ImportError`` at construction when pyright is not
    installed; it is part of the ``types`` extra.
    """

    def __init__(self) -> None:
        command = _pyright_command()
        if command is None:
            raise ImportError("pyright is not installed")
        self._command: List[str] = command

    def _diagnostics(self, file_path: str, text: str) -> List[_PyrightDiagnostic]:
        """Pyright's diagnostics for ``text`` standing in for ``file_path``."""
        original = Path(file_path)
        with _probe_file(original, text) as probe:
            # The project's pyright configuration applies (its rules are what
            # the generated code must satisfy), but its interpreter is never
            # run: --pythonpath names this process's interpreter, so a venv
            # setting in that configuration cannot execute the project.
            completed = subprocess.run(
                [*self._command, "--outputjson", "--pythonpath", sys.executable, str(probe)],
                capture_output=True,
                text=True,
                cwd=str(original.parent),
                check=False,
            )
        output = completed.stdout
        start, end = output.find("{"), output.rfind("}")
        if start < 0 or end < 0:
            LOG.warning("pyright produced no JSON for %s; no types inferred there", file_path)
            return []
        try:
            data = json.loads(output[start : end + 1])
        except json.JSONDecodeError as error:
            LOG.warning("pyright output for %s is not JSON: %s", file_path, error)
            return []
        diagnostics = data.get("generalDiagnostics", [])
        # pyright's JSON is trusted to have this shape; the reads below use .get.
        return [cast(_PyrightDiagnostic, d) for d in diagnostics if isinstance(d, dict)]

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
            for diagnostic in self._diagnostics(file_path, text):
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
        error_lines = [
            self._line(diagnostic)
            for diagnostic in self._diagnostics(file_path, text)
            if diagnostic.get("severity") == "error"
        ]
        return _verdicts_from_error_lines(len(pairs), error_lines, signature_line, return_line)

    def check(self, file_path: str, source: str) -> Sequence[str]:
        """Pyright's errors for ``source`` standing at ``file_path``, one message each."""
        return [
            f"pyright: {diagnostic.get('rule') or ''}: {diagnostic.get('message', '')}"
            for diagnostic in self._diagnostics(file_path, source)
            if diagnostic.get("severity") == "error"
        ]


_PYRIGHT_REVEALED = re.compile(r'^Type of ".*" is "(?P<type>.*)"$', re.DOTALL)


def _pyright_command() -> Optional[List[str]]:
    """How to run pyright: this interpreter's copy first, then one on PATH."""
    try:
        import pyright  # noqa: F401
    except ImportError:
        executable = shutil.which("pyright")
        return [executable] if executable else None
    return [sys.executable, "-m", "pyright"]


class CombinedOracle:
    """Infers with one checker and verifies with every configured one."""

    def __init__(self, primary: TypeOracle, others: Sequence[TypeOracle]) -> None:
        self._primary = primary
        self._all = [primary, *others]

    def reveal(self, requests: Sequence[RevealRequest]) -> Mapping[RevealKey, str]:
        """The primary oracle's revelations."""
        return self._primary.reveal(requests)

    def is_subtype(
        self, file_path: str, source: str, pairs: Sequence[Tuple[str, str]]
    ) -> Sequence[Subtyping]:
        """The primary oracle's verdicts."""
        return self._primary.is_subtype(file_path, source, pairs)

    def check(self, file_path: str, source: str) -> Sequence[str]:
        """Every oracle's errors, the primary's first, so each configured checker stays green."""
        messages: List[str] = []
        for oracle in self._all:
            messages.extend(oracle.check(file_path, source))
        return messages


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
    from .unification.project_layout import load_pyproject

    tool = load_pyproject(root).get("tool", {})
    return isinstance(tool, dict) and bool(tool.get(name))


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
    from .unification.project_layout import find_project_root

    root = find_project_root(path)
    wants_mypy = project_configures_mypy(root)
    wants_pyright = project_configures_pyright(root)
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
