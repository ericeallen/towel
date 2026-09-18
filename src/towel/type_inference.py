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
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

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


TypeInferrer = Callable[[Sequence[RevealRequest]], Mapping[RevealKey, str]]
"""Maps each requested expression to mypy's spelling of its type, when known."""

_REVEALED = re.compile(
    r'^(?P<path>.*?):(?P<line>\d+):(?:\d+:)? note: Revealed type is "(?P<type>.*)"$'
)


def _module_name_and_root(path: Path) -> Tuple[str, Path]:
    """The dotted module name of ``path`` and the directory that must be on the search path."""
    parts = [path.stem]
    directory = path.parent
    while (directory / "__init__.py").is_file():
        parts.append(directory.name)
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
        from mypy import build
        from mypy.build import BuildSource
        from mypy.errors import CompileError
        from mypy.options import Options

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
        options = Options()
        options.ignore_missing_imports = True
        options.follow_imports = "silent"
        options.incremental = True
        options.cache_dir = str(self._cache_dir)
        options.check_untyped_defs = True
        options.explicit_package_bases = True
        options.mypy_path = roots
        options.hide_error_codes = True
        try:
            result = build.build(sources=sources, options=options)
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
