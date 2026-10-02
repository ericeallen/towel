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

import functools
import io
import re
import sys
import tokenize

from .unification.bounded_cache import memoizing

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
_CACHE_ENTRIES = 32


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


@functools.lru_cache(maxsize=_CACHE_ENTRIES)
def _cached_tokens_cannot_warn(source: str) -> bool:
    return _tokens_cannot_warn(source)


def parsing_cannot_warn(source: str) -> bool:
    """Prove lexical warning freedom, keeping at most 16 MiB of source payload.

    Only the known CPython versions may call this predicate. Eligibility is
    independent of parser limits and warning policy. The entry and character
    caps bound retained strings even at four bytes per Unicode character;
    larger sources are classified without retention. No AST is held here.
    """
    if _POSSIBLE_WARNING.search(source) is None:
        return True
    if not memoizing() or len(source) > _MAX_SOURCE_CHARACTERS:
        return _tokens_cannot_warn(source)
    return _cached_tokens_cannot_warn(source)
