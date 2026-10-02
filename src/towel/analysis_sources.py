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

"""Read-only syntax shared by analyses of the same file and source version.

Readers supply current source, so filesystem access and change detection remain
outside the memoized computation. Trees belong to one bounded analysis context;
callers must copy them before rewriting. Different files never share node
identities, even when their text matches. This matters to node-keyed analyses
whose answers include a definition's module identity.
"""

from __future__ import annotations

import ast
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import os
from typing import Iterator, Optional, Tuple, Union
import warnings

from .source_text import decode_source
from .unification.bounded_cache import BoundedCache


def _field_value(value: object) -> str:
    if isinstance(value, ast.AST):
        return type(value).__name__
    if isinstance(value, list):
        return repr(tuple(_field_value(item) for item in value))
    if isinstance(value, int):
        # Keep bool distinct from int without decimal conversion of huge literals.
        return f"{type(value).__name__}:{hex(value)}"
    return repr(value)


def _fingerprint(tree: ast.Module) -> str:
    """Structure and source positions, without recursive dumping of deep trees."""
    digest = hashlib.sha256()
    for node in ast.walk(tree):
        fields = tuple(
            (name, _field_value(getattr(node, name, None)))
            for name in (*node._fields, *node._attributes)
        )
        digest.update(repr((type(node).__name__, fields)).encode("utf-8"))
    return digest.hexdigest()


@dataclass(frozen=True)
class _Parsed:
    tree: ast.Module
    source_bytes: int
    fingerprint: Optional[str]


class AnalysisSources:
    """Bounded, content-keyed syntax for one analysis; never caches file reads.

    The byte budget bounds retained source, not Python object-graph size. A
    source larger than the budget is parsed without retaining its tree.
    """

    def __init__(
        self,
        *,
        max_entries: int = 2048,
        max_source_bytes: int = 8 * 1024 * 1024,
        check_ast_immutable: bool = False,
    ) -> None:
        if max_entries < 0 or max_source_bytes < 0:
            raise ValueError("Analysis cache limits must be nonnegative")
        self._max_source_bytes = max_source_bytes
        self._check_immutable = check_ast_immutable
        self._parsed: BoundedCache[Tuple[str, str, str], _Parsed] = BoundedCache(
            max_entries, weight=lambda value: value.source_bytes, weight_limit=max_source_bytes
        )

    def parse(self, source: Union[str, bytes], filename: str) -> ast.Module:
        """Parse current source, preserving file identities and diagnostic filenames."""
        if isinstance(source, bytes):
            try:
                with warnings.catch_warnings(record=True) as decoding_warnings:
                    warnings.simplefilter("always")
                    text = decode_source(source)
            except (SyntaxError, UnicodeError, LookupError):
                # Let Python diagnose invalid encoded source exactly as a bare
                # ast.parse(bytes) would; failed parses are never cached.
                return ast.parse(source, filename=filename)
            if decoding_warnings:
                # Decoding for the cache key must neither add diagnostics nor
                # change whether the original parser raises under these filters.
                return ast.parse(source, filename=filename)
        else:
            text = source
        key = (os.path.abspath(filename), filename, text)
        known = self._parsed.get(key)
        if known is not None:
            if self._check_immutable and _fingerprint(known.tree) != known.fingerprint:
                raise RuntimeError(f"Analysis mutated the cached AST of {filename}")
            return known.tree
        # Warning-producing parses are not pure: warnings-as-errors can even
        # turn an invalid escape into SyntaxError. Only retain warning-free
        # syntax. Replay other parses under the caller's original filters.
        try:
            with warnings.catch_warnings(record=True) as emitted:
                warnings.simplefilter("always")
                tree = ast.parse(source, filename=filename)
        except SyntaxError:
            if not emitted:
                raise
            # A preceding warning can be the original error under the caller's
            # filters, or must still be emitted before the later syntax error.
            return ast.parse(source, filename=filename)
        if emitted:
            return ast.parse(source, filename=filename)
        size = len(text.encode("utf-8", errors="surrogatepass"))
        if size <= self._max_source_bytes:
            self._parsed.put(
                key, _Parsed(tree, size, _fingerprint(tree) if self._check_immutable else None)
            )
        return tree


_SOURCES: ContextVar[Optional[AnalysisSources]] = ContextVar("towel_analysis_sources", default=None)


@contextmanager
def sharing_analysis_sources(*, check_ast_immutable: bool = False) -> Iterator[AnalysisSources]:
    """Share syntax during this analysis, restoring an outer context on exit."""
    sources = AnalysisSources(check_ast_immutable=check_ast_immutable)
    token = _SOURCES.set(sources)
    try:
        yield sources
    finally:
        _SOURCES.reset(token)


def parse_analysis_source(source: Union[str, bytes], filename: str = "<unknown>") -> ast.Module:
    """Read-only AST of source, shared when an analysis owns the current context."""
    sources = _SOURCES.get()
    return (
        ast.parse(source, filename=filename) if sources is None else sources.parse(source, filename)
    )
