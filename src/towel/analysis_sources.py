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
import functools
import hashlib
import os
import re
import sys
from typing import Callable, Concatenate, Iterator, Optional, ParamSpec, Tuple, TypeVar, Union, cast

from .source_text import decode_source
from .unification.bounded_cache import BoundedCache, memoizing


@dataclass(frozen=True)
class ParserConfiguration:
    """Mutable interpreter limits affecting source parsing and analysis."""

    integer_digits: int
    recursion_limit: int


def parser_configuration() -> ParserConfiguration:
    """The interpreter's current parsing limits, for syntax and analysis cache keys."""
    return ParserConfiguration(sys.get_int_max_str_digits(), sys.getrecursionlimit())


_KNOWN_WARNING_SITES = sys.implementation.name == "cpython" and (3, 11) <= sys.version_info[:2] <= (
    3,
    14,
)
"""Other parsers may warn about other syntax, so their parses are never shared."""

# CPython 3.11--3.14's AST parser warns for invalid string escapes, oversized
# octal escapes, and a numeric literal abutting a keyword. This deliberately
# overmatches comments, raw strings, identifiers and ordinary numeric suffixes.
# A false positive costs reuse; a false negative would hide native diagnostics.
# See CPython's Parser/string_parser.c and tokenizer.c (3.13+: lexer/lexer.c
# and tokenizer/helpers.c); AST-only parsing does not run code-generation warnings.
_POSSIBLE_WARNING = re.compile(r"\\(?:[^\\'\"abfnrtv0-7x\r\n]|[4-7][0-7]{2})|[0-9]\.?[a-zA-Z_]")
_CODING_COOKIE = re.compile(rb"coding[:=]\s*([-\w.]+)", re.ASCII)
_WARNING_FREE_ENCODINGS = frozenset(
    {b"utf-8", b"utf8", b"utf-8-sig", b"ascii", b"latin-1", b"iso-8859-1", b"iso-latin-1"}
)


def _cacheable_text(source: Union[str, bytes]) -> Optional[str]:
    """Text whose parse cannot warn, without changing process-global warning state.

    The native parser handles every uncertain source once under the caller's
    own filters. In particular, do not decode arbitrary codecs to form a key:
    decoding itself may warn, or call a user-registered codec.
    """
    if not _KNOWN_WARNING_SITES:
        return None
    if isinstance(source, bytes):
        for line in source.split(b"\n", 2)[:2]:
            cookie = _CODING_COOKIE.search(line)
            if (
                cookie is not None
                and cookie[1].lower().replace(b"_", b"-") not in _WARNING_FREE_ENCODINGS
            ):
                return None
        try:
            text = decode_source(source)
        except (SyntaxError, UnicodeError, LookupError):
            return None
    else:
        text = source
    return None if _POSSIBLE_WARNING.search(text) else text


def parsing_is_pure(source: Union[str, bytes]) -> bool:
    """Whether parsing this source cannot emit diagnostics under the caller's filters."""
    return _cacheable_text(source) is not None


_P = ParamSpec("_P")
_R = TypeVar("_R")


def memoized_source_analysis(
    maxsize: int,
) -> Callable[[Callable[Concatenate[str, _P], _R]], Callable[Concatenate[str, _P], _R]]:
    """Bound an immutable source analysis, including parser limits and native diagnostics.

    The analysis must depend only on source, its other arguments, and the
    parser configuration. Arguments must be hashable and its result read-only.
    Warning-capable sources always execute it so a cache hit cannot swallow
    a warning or its error.
    """

    def decorate(
        function: Callable[Concatenate[str, _P], _R],
    ) -> Callable[Concatenate[str, _P], _R]:
        def with_configuration(
            configuration: ParserConfiguration, source: str, *args: _P.args, **kwargs: _P.kwargs
        ) -> _R:
            return function(source, *args, **kwargs)

        # lru_cache preserves the call signature; its typing stub erases ParamSpec.
        cached = cast(
            Callable[Concatenate[ParserConfiguration, str, _P], _R],
            functools.lru_cache(maxsize=maxsize)(with_configuration),
        )

        @functools.wraps(function)
        def analyzed(source: str, /, *args: _P.args, **kwargs: _P.kwargs) -> _R:
            if not memoizing() or not parsing_is_pure(source):
                return function(source, *args, **kwargs)
            return cached(parser_configuration(), source, *args, **kwargs)

        return analyzed

    return decorate


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
        self._parsed: BoundedCache[Tuple[str, str, str, ParserConfiguration], _Parsed] = (
            BoundedCache(
                max_entries, weight=lambda value: value.source_bytes, weight_limit=max_source_bytes
            )
        )

    def parse(self, source: Union[str, bytes], filename: str) -> ast.Module:
        """Parse current source, preserving file identities and diagnostic filenames."""
        text = _cacheable_text(source)
        if text is None:
            return ast.parse(source, filename=filename)
        key = (os.path.abspath(filename), filename, text, parser_configuration())
        known = self._parsed.get(key)
        if known is not None:
            if self._check_immutable and _fingerprint(known.tree) != known.fingerprint:
                raise RuntimeError(f"Analysis mutated the cached AST of {filename}")
            return known.tree
        tree = ast.parse(source, filename=filename)
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
