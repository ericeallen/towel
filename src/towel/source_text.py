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

"""Source files as text, and text back as the bytes the file was written in.

Python decodes a source file by its BOM or coding cookie (PEP 263), else
as UTF-8, and accepts any newline convention. Towel works on LF text in
memory and, when it writes a file back, restores the file's own encoding,
BOM and dominant newline, so the only bytes that change are the ones the
refactoring changed.
"""

from __future__ import annotations

import io
from pathlib import Path
import tokenize
from typing import List, Optional, Union


def source_encoding(data: bytes) -> str:
    """The encoding Python would decode ``data`` with; ``utf-8-sig`` when it carries a BOM.

    Raises ``SyntaxError`` for a coding cookie naming an unknown encoding,
    as the interpreter does.
    """
    encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
    return encoding


def decode_source(data: bytes) -> str:
    """``data`` as text with LF newlines, decoded as the interpreter would."""
    text = data.decode(source_encoding(data))
    return text.replace("\r\n", "\n").replace("\r", "\n")


def try_read_source(path: Union[str, Path]) -> Optional[str]:
    """``read_source``, or None when the file cannot be read, decoded, or has no valid cookie.

    The one policy for a file the analysis merely consults: a module that
    cannot be read contributes nothing, and the caller says so.
    """
    try:
        return read_source(path)
    except (OSError, UnicodeError, SyntaxError):
        return None


def source_lines(text: str) -> List[str]:
    """``text`` split into lines the way the tokenizer counts them, ends kept.

    ``str.splitlines`` also splits on form feeds, ``\\x1c``-``\\x1e``, ``\\x85``
    and the Unicode line and paragraph separators, none of which end a line
    for the parser, so a table built from it disagrees with the tree's line
    numbers from the first such character on. Decoded source has LF newlines
    only (``decode_source``), so this splits on LF alone.
    """
    lines = text.split("\n")
    result = [line + "\n" for line in lines[:-1]]
    if lines[-1]:
        result.append(lines[-1])
    return result


def read_source(path: Union[str, Path]) -> str:
    """The file at ``path`` as LF text, decoded as the interpreter would."""
    return decode_source(Path(path).read_bytes())


def dominant_newline(data: bytes) -> bytes:
    """The newline sequence ``data`` uses most; LF when it has none."""
    crlf = data.count(b"\r\n")
    lf = data.count(b"\n") - crlf
    cr = data.count(b"\r") - crlf
    best = max((lf, b"\n"), (crlf, b"\r\n"), (cr, b"\r"), key=lambda pair: pair[0])
    return best[1] if best[0] else b"\n"


class UnencodableText(ValueError):
    """New text holds a character its file's own encoding cannot represent.

    Rendering writes a string constant by value, so an escape such as
    ``"\\u20ac"`` in a Latin-1 file comes back as the character itself. The
    file cannot hold that text, and the refactoring that produced it cannot
    be written; nothing else about the run is wrong.
    """


def encode_like(original: bytes, text: str) -> bytes:
    """``text`` (LF) as bytes in ``original``'s encoding, BOM and newline convention.

    Raises ``UnencodableText`` when that encoding cannot represent ``text``.
    """
    encoding = source_encoding(original)
    try:
        encoded = text.encode(encoding)
    except UnicodeEncodeError as error:
        character = error.object[error.start]
        line = error.object.count("\n", 0, error.start) + 1
        raise UnencodableText(
            f"the file's encoding, {encoding}, cannot represent {character!r}"
            f" (U+{ord(character):04X}) on line {line} of the new text"
        ) from error
    newline = dominant_newline(original)
    return encoded if newline == b"\n" else encoded.replace(b"\n", newline)
