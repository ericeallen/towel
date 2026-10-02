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

"""Pure lexical eligibility for sharing CPython 3.11--3.14 AST parses.

Uncertain syntax uses the native parser. This module neither decodes source nor
changes warning filters: its only retained result is an immutable boolean.
"""

from __future__ import annotations

from dataclasses import dataclass
import io
import os
import re
import sys
import threading
import tokenize

from .unification.bounded_cache import BoundedCache, memoizing

_POSSIBLE_WARNING = re.compile(r"\\(?:[^\\'\"abfnrtv0-7x\r\n]|[4-7][0-7]{2})|[0-9]\.?[a-zA-Z_]")
# The C tokenizer itself warns for these f/t-string escapes, including its
# special handling of a carriage return. Reject them before tokenization.
_TOKENIZER_WARNING = re.compile(r"\\\r?[{}]")
_STRING_START = re.compile("(?i)^([rubft]*)(\"\"\"|'''|\"|')")
# Python 3.11 emits one token for a whole f-string. This intentionally also
# matches literal text and identifiers; it cannot miss a numeric suffix at a
# replacement-expression boundary, including hexadecimal and imaginary ones.
_LEGACY_NUMERIC_KEYWORD = re.compile(r"[0-9a-fA-FjJ]\.?(?:and|else|for|if|in|is|not|or)")
_MAX_SOURCE_CHARACTERS = 128 * 1024
_CACHE_ENTRIES = 2048
_CACHE_SOURCE_BYTES = 16 * 1024 * 1024


def _safe_escapes(text: str, *, is_bytes: bool = False) -> bool:
    position = 0
    while (position := text.find("\\", position)) >= 0:
        position += 1
        if position == len(text):
            return False
        escape = text[position]
        position += 1
        if escape in "\\'\"abfnrtv\r\n":
            continue
        if escape in "01234567":
            start = position - 1
            while position < min(start + 3, len(text)) and text[position] in "01234567":
                position += 1
            if position - start == 3 and escape in "4567":
                return False
            continue
        width = {"x": 2, "u": 4, "U": 8}.get(escape)
        if width is not None and (escape == "x" or not is_bytes):
            digits = text[position : position + width]
            if len(digits) != width or any(
                digit not in "0123456789abcdefABCDEF" for digit in digits
            ):
                return False
            position += width
            continue
        if escape == "N" and not is_bytes and text[position : position + 1] == "{":
            end = text.find("}", position + 1)
            if end <= position + 1:
                return False
            # Unknown Unicode names fail parsing; resolving names is not needed
            # to prove that this escape cannot emit a warning.
            position = end + 1
            continue
        return False
    return True


def _tokens_cannot_warn(source: str) -> bool:
    if _TOKENIZER_WARNING.search(source):
        return False
    formatted_raw: list[bool] = []
    previous: tokenize.TokenInfo | None = None
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            kind = tokenize.tok_name[token.type]
            if (
                previous is not None
                and previous.type == tokenize.NUMBER
                and token.type == tokenize.NAME
                and previous.end == token.start
            ):
                return False
            if kind in {"FSTRING_START", "TSTRING_START"}:
                start = _STRING_START.match(token.string)
                if start is None:
                    return False
                formatted_raw.append("r" in start[1].lower())
            elif kind in {"FSTRING_MIDDLE", "TSTRING_MIDDLE"}:
                # CPython 3.12 decodes even raw f-string format specifications.
                # MIDDLE also covers literal text; checking all of it avoids
                # duplicating the parser just to distinguish those positions.
                raw = formatted_raw and formatted_raw[-1] and sys.version_info[:2] != (3, 12)
                if not formatted_raw or (not raw and not _safe_escapes(token.string)):
                    return False
            elif kind in {"FSTRING_END", "TSTRING_END"}:
                if not formatted_raw:
                    return False
                formatted_raw.pop()
            elif token.type == tokenize.STRING:
                start = _STRING_START.match(token.string)
                if start is None:
                    return False
                prefix = start[1].lower()
                body = token.string[start.end() : -len(start[2])]
                if "f" in prefix and _LEGACY_NUMERIC_KEYWORD.search(body):
                    return False
                if "r" not in prefix and not _safe_escapes(body, is_bytes="b" in prefix):
                    return False
            elif token.type == tokenize.ERRORTOKEN:
                return False
            previous = token
    except (SyntaxError, UnicodeError, tokenize.TokenError, RecursionError):
        return False
    return not formatted_raw


@dataclass(frozen=True)
class _Eligibility:
    cannot_warn: bool
    source_bytes: int


class _LexicalCache:
    """Immutable answers with bounded source storage and synchronized bookkeeping."""

    def __init__(
        self,
        *,
        max_entries: int = _CACHE_ENTRIES,
        max_source_bytes: int = _CACHE_SOURCE_BYTES,
    ) -> None:
        self.pid = os.getpid()
        self.lock = threading.Lock()
        self._max_source_bytes = max_source_bytes
        self.entries: BoundedCache[str, _Eligibility] = BoundedCache(
            max_entries,
            weight=lambda answer: answer.source_bytes,
            weight_limit=max_source_bytes,
        )

    def cannot_warn(self, source: str) -> bool:
        with self.lock:
            known = self.entries.get(source)
        if known is not None:
            return known.cannot_warn
        # Tokenization is pure and can be repeated by simultaneous misses;
        # only shared table/weight bookkeeping needs serialization.
        # CPython may later cache a separate UTF-8 representation when the
        # native parser reads a non-ASCII string. Reserve its maximum size
        # now, including the terminator; ASCII shares its existing buffer.
        source_bytes = sys.getsizeof(source) + (0 if source.isascii() else 4 * len(source) + 1)
        answer = _Eligibility(_tokens_cannot_warn(source), source_bytes)
        if answer.source_bytes <= self._max_source_bytes:
            with self.lock:
                self.entries.put(source, answer)
        return answer.cannot_warn


_CACHE = _LexicalCache()


def _cached_tokens_cannot_warn(source: str) -> bool:
    global _CACHE
    cache = _CACHE
    if cache.pid != os.getpid():
        # A fork may inherit a lock held by a different parent thread. Start
        # fresh before acquiring it, without installing process-wide hooks.
        cache = _CACHE = _LexicalCache()
    return cache.cannot_warn(source)


def parsing_cannot_warn(source: str) -> bool:
    """Prove lexical warning freedom, retaining at most 16 MiB of source strings.

    Only the known CPython versions may call this predicate. Eligibility is
    independent of parser limits and warning policy. Counted string storage
    (plus a conservative UTF-8 reserve for non-ASCII text), rather than a
    worst-case size per entry, lets an ordinary source corpus remain cached.
    Entry and character caps also bound bookkeeping and each
    admitted source; larger sources are classified without retention. No AST
    is held here. The bounded cache is process-local and resets after fork.
    """
    if _POSSIBLE_WARNING.search(source) is None:
        return True
    if not memoizing() or len(source) > _MAX_SOURCE_CHARACTERS:
        return _tokens_cannot_warn(source)
    return _cached_tokens_cannot_warn(source)
