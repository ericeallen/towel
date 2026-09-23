"""A refactored file keeps its encoding, BOM and newline convention."""

from __future__ import annotations

import contextlib
import io
from pathlib import Path

import pytest

from towel.source_text import (
    UnencodableText,
    decode_source,
    dominant_newline,
    encode_like,
    read_source,
)
from towel.unification.refactor_engine import UnificationRefactorEngine

DUPLICATES = (
    "def f(a):\n    x = a + 1\n    y = x * 2\n    z = y + compute(a)\n    return z\n\n\n"
    "def g(b):\n    x = b + 1\n    y = x * 2\n    z = y + compute(b)\n    return z\n\n\n"
    "def compute(v):\n    return v\n"
)


def _dry(source_dir: Path, out: Path) -> int:
    engine = UnificationRefactorEngine(min_lines=3, reuse_existing_functions=False)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        results, _ = engine.refactor_directory_to_fixed_point(
            str(source_dir), str(out), max_iterations=0, progress="none"
        )
    return sum(count for count, _ in results.values())


@pytest.mark.parametrize(
    "data, expected",
    [
        (b"a\nb\n", b"\n"),
        (b"a\r\nb\r\n", b"\r\n"),
        (b"a\rb\r", b"\r"),
        (b"a\r\nb\nc\r\n", b"\r\n"),
        (b"", b"\n"),
    ],
)
def test_dominant_newline(data: bytes, expected: bytes) -> None:
    assert dominant_newline(data) == expected


def test_decode_and_encode_round_trip_bom_cookie_and_newlines() -> None:
    bom = "﻿".encode("utf-8") + b"x = 1\r\ny = 2\r\n"
    assert decode_source(bom) == "x = 1\ny = 2\n"
    assert encode_like(bom, "x = 1\ny = 3\n") == "﻿".encode("utf-8") + b"x = 1\r\ny = 3\r\n"
    latin = b"# -*- coding: latin-1 -*-\nname = '\xe9'\n"
    assert decode_source(latin) == "# -*- coding: latin-1 -*-\nname = 'é'\n"
    assert encode_like(latin, "# -*- coding: latin-1 -*-\nname = 'è'\n").endswith(b"'\xe8'\n")
    with pytest.raises(SyntaxError):
        decode_source(b"# -*- coding: no-such-codec -*-\nx = 1\n")


def test_crlf_file_keeps_its_line_endings(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "m.py").write_bytes(DUPLICATES.replace("\n", "\r\n").encode("utf-8"))
    assert _dry(source, tmp_path / "out") == 1
    data = (tmp_path / "out" / "m.py").read_bytes()
    assert b"\r\n" in data and b"\n" not in data.replace(b"\r\n", b"")
    assert "__extracted_func_0" in read_source(tmp_path / "out" / "m.py")


def test_bom_file_is_refactored_and_keeps_its_bom(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "m.py").write_bytes("﻿".encode("utf-8") + DUPLICATES.encode("utf-8"))
    assert _dry(source, tmp_path / "out") == 1
    data = (tmp_path / "out" / "m.py").read_bytes()
    assert data.startswith("﻿".encode("utf-8"))
    assert "__extracted_func_0" in decode_source(data)


def test_coding_cookie_file_is_written_back_in_its_encoding(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    latin = "# -*- coding: latin-1 -*-\n" + DUPLICATES.replace(
        "    return v\n", "    return v  # café\n"
    )
    (source / "m.py").write_bytes(latin.encode("latin-1"))
    (source / "other.py").write_text("plain = 1\n")
    assert _dry(source, tmp_path / "out") == 1
    data = (tmp_path / "out" / "m.py").read_bytes()
    assert b"caf\xe9" in data
    assert "__extracted_func_0" in decode_source(data)


# The first pair prints an escape the file's encoding cannot hold once
# rendering spells it as the character: the file itself is plain Latin-1, and
# only Towel's rendering ever produced the character. The second is ordinary.
UNENCODABLE = (
    "# -*- coding: latin-1 -*-\n\n\n"
    "def alpha(items):\n    total = 0\n    for item in items:\n        if item > 4:\n"
    "            total += item * 2\n        else:\n            total -= item\n"
    '    print("price \\u20ac", total)\n    return total + 1\n\n\n'
    "def beta(items):\n    total = 0\n    for item in items:\n        if item > 5:\n"
    "            total += item * 2\n        else:\n            total -= item\n"
    '    print("price \\u20ac", total)\n    return total + 2\n\n\n' + DUPLICATES
)


@pytest.mark.parametrize("single_file", [False, True])
def test_an_escape_the_rendering_spelled_as_a_character_is_written_back_escaped(
    tmp_path: Path, single_file: bool, caplog: pytest.LogCaptureFixture
) -> None:
    """The extraction is kept, its literal escaped again, and the program prints what it did."""
    source = tmp_path / "source"
    source.mkdir()
    module = source / "m.py"
    module.write_bytes(UNENCODABLE.encode("latin-1"))
    out = tmp_path / ("out.py" if single_file else "out")
    engine = UnificationRefactorEngine(min_lines=3, reuse_existing_functions=False)
    with contextlib.redirect_stdout(io.StringIO()):
        if single_file:
            _code, applied, _ = engine.refactor_to_fixed_point(
                str(module), progress="none", output_path=str(out)
            )
            written = out.read_bytes()
        else:
            results, _ = engine.refactor_directory_to_fixed_point(
                str(source), str(out), progress="none"
            )
            applied = sum(count for count, _ in results.values())
            written = (out / "m.py").read_bytes()
    assert applied == 2
    assert "cannot represent" not in caplog.text
    text = written.decode("latin-1")
    # The helper spells the escape as the source did, never the character.
    assert "\\u20ac" in text and "\u20ac" not in text

    def observed(program: str) -> str:
        namespace: dict[str, object] = {}
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            exec(compile(program, "m.py", "exec"), namespace)
            for name in ("alpha", "beta"):
                print(namespace[name]([1, 5, 9]))  # type: ignore[operator]
        return buffer.getvalue()

    assert observed(text) == observed(UNENCODABLE)


def test_an_unencodable_constant_is_escaped_in_every_kind_of_literal() -> None:
    """Plain, f-string and astral characters come back as escapes of the same value."""
    import ast

    original = b"# -*- coding: latin-1 -*-\nx = 1\n"
    text = "x = '€'\ny = f'{x}€\U0001f600'\n"
    written = encode_like(original, text).decode("latin-1")
    assert ast.dump(ast.parse(written)) == ast.dump(ast.parse(text))
    assert "€" not in written


def test_a_character_no_escape_can_spell_is_still_refused() -> None:
    """A comment has no escapes; declining is the only sound answer there."""
    with pytest.raises(UnencodableText):
        encode_like(b"# -*- coding: latin-1 -*-\n", "x = 1  # €\n")
