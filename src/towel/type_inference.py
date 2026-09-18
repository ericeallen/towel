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

"""Ask mypy what type an expression has at a point in a module.

The engine copies annotations the call sites declare; for an argument that is
an expression rather than an annotated name, only a type checker knows. When
mypy is installed, :class:`MypyInferrer` builds a copy of the site's module in
memory with ``reveal_type(<expression>)`` inserted where the block begins,
so names resolve as they do at the call, and reads the revealed types back.
Nothing is written to disk except mypy's own cache, which lives for the
inferrer's lifetime so later proposals in the same run rebuild incrementally.

The engine accepts any ``TypeInferrer``; the answers are strings in mypy's
spelling, which ``towel.unification.annotations`` turns into annotations only
when every name in them resolves where the helper is defined.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import tempfile
from typing import TYPE_CHECKING, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

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
    ) -> Sequence[Optional[bool]]:
        """Whether each narrow type is assignable to its wide type, in the module's context."""
        raise NotImplementedError

    def check(self, file_path: str, source: str) -> Sequence[str]:
        """The checker's error messages for ``source`` as ``file_path``, without positions."""
        raise NotImplementedError


TypeInferrer = TypeOracle
"""Earlier name of the protocol, kept for callers that used it."""

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


class MypyInferrer:
    """A ``TypeInferrer`` backed by mypy's in-process build.

    Raises ``ImportError`` at construction when mypy is not installed; install
    the ``types`` extra (``pip install "code-towel[types]"``) to provide it.
    """

    def __init__(self, cache_dir: Optional[Path] = None) -> None:
        from mypy import build  # noqa: F401  (import error surfaces here)

        self._cache = (
            None if cache_dir is not None else tempfile.TemporaryDirectory(prefix="towel-mypy-")
        )
        self._cache_dir = cache_dir if cache_dir is not None else Path(self._cache.name)  # type: ignore[union-attr]

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
    ) -> Sequence[Optional[bool]]:
        """Whether each ``narrow`` is assignable to its ``wide``, judged in the module's context.

        One probe function per pair is appended to an in-memory copy of the
        module, ``def probe(v: narrow) -> wide: return v``. An error on the
        ``return`` line means not a subtype; an error on the signature line
        means a name the checker could not resolve, which is reported as None.
        """
        from mypy import build
        from mypy.build import BuildSource
        from mypy.errors import CompileError

        if not pairs:
            return []
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
        text = "".join(lines) + "".join(probe_lines)
        module, root = _module_name_and_root(Path(file_path))
        try:
            result = build.build(
                sources=[BuildSource(file_path, module, text)], options=self._options([str(root)])
            )
        except CompileError:
            return [None] * len(pairs)
        verdicts: List[Optional[bool]] = [True] * len(pairs)
        for message in result.errors:
            match = _ERROR.match(message)
            if match is None or match.group("path") != file_path:
                continue
            line = int(match.group("line"))
            if line in signature_line:
                verdicts[signature_line[line]] = None
            elif line in return_line and verdicts[return_line[line]] is not None:
                verdicts[return_line[line]] = False
        return verdicts

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
        except CompileError:
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
