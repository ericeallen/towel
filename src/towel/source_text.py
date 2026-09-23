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

import ast
import io
from pathlib import Path
import tokenize
from typing import List, Optional, Tuple, Union


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
    """New text holds a character its file's own encoding cannot represent, even escaped.

    Rendering writes a string constant by value, so an escape such as
    ``"\\u20ac"`` in a Latin-1 file comes back as the character itself; that
    is written back as an escape (``_escaped_for``). What remains is a
    character no escape can spell, such as one in a raw string, and the
    refactoring that produced it cannot be written.
    """


def _escape(character: str) -> str:
    code = ord(character)
    if code < 0x100:
        return f"\\x{code:02x}"
    return f"\\u{code:04x}" if code < 0x10000 else f"\\U{code:08x}"


def _escaped_for(text: str, encoding: str) -> Optional[str]:
    """``text`` with each character ``encoding`` cannot hold escaped inside its string literal.

    A valid source file never holds a character its encoding cannot
    represent; such a character reached the new text only because rendering
    spelled a constant by value. Escaping it inside the literal restores the
    file's own kind of spelling, and the rewrite is kept only when the syntax
    tree is unchanged by it -- every constant the same value, nothing else
    touched. None when some character is outside an escapable literal (a raw
    string, a comment, a name) or the proof fails.
    """

    def encodable(character: str) -> bool:
        try:
            character.encode(encoding)
        except UnicodeEncodeError:
            return False
        return True

    literal_kinds = {tokenize.STRING}
    literal_kinds.update(
        getattr(tokenize, name) for name in ("FSTRING_MIDDLE",) if hasattr(tokenize, name)
    )
    lines = source_lines(text)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    edits: List[Tuple[int, int, str]] = []
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError):
        return None
    for token in tokens:
        if token.type not in literal_kinds or all(encodable(c) for c in token.string):
            continue
        prefix = token.string[: len(token.string) - len(token.string.lstrip("rRbBuUfF"))]
        if token.type == tokenize.STRING and ("r" in prefix.lower() or "b" in prefix.lower()):
            return None
        start = offsets[token.start[0] - 1] + token.start[1]
        end = offsets[token.end[0] - 1] + token.end[1]
        spelled = "".join(c if encodable(c) else _escape(c) for c in text[start:end])
        edits.append((start, end, spelled))
    rewritten = text
    for start, end, spelled in sorted(edits, reverse=True):
        rewritten = rewritten[:start] + spelled + rewritten[end:]
    try:
        rewritten.encode(encoding)
        if ast.dump(ast.parse(rewritten)) != ast.dump(ast.parse(text)):
            return None
    except (UnicodeEncodeError, SyntaxError, ValueError):
        return None
    return rewritten


def encode_like(original: bytes, text: str) -> bytes:
    """``text`` (LF) as bytes in ``original``'s encoding, BOM and newline convention.

    Raises ``UnencodableText`` when that encoding cannot represent ``text``.
    """
    encoding = source_encoding(original)
    try:
        encoded = text.encode(encoding)
    except UnicodeEncodeError as error:
        escaped = _escaped_for(text, encoding)
        if escaped is not None:
            return encode_like(original, escaped)
        character = error.object[error.start]
        line = error.object.count("\n", 0, error.start) + 1
        raise UnencodableText(
            f"the file's encoding, {encoding}, cannot represent {character!r}"
            f" (U+{ord(character):04X}) on line {line} of the new text"
        ) from error
    newline = dominant_newline(original)
    return encoded if newline == b"\n" else encoded.replace(b"\n", newline)
