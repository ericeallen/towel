"""A refactored file keeps its encoding, BOM and newline convention."""

from __future__ import annotations

import contextlib
import io
from pathlib import Path

import pytest

from towel.source_text import decode_source, dominant_newline, encode_like, read_source
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
# rendering spells it as the character; the second pair is ordinary.
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
def test_a_rendering_the_encoding_cannot_hold_is_declined_and_the_run_goes_on(
    tmp_path: Path, single_file: bool, caplog: pytest.LogCaptureFixture
) -> None:
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
    assert applied == 1
    assert "cannot represent '€' (U+20AC)" in caplog.text
    text = written.decode("latin-1")
    assert text.count('print("price \\u20ac", total)') == 2
    assert "__extracted_func_0(b)" in text
