#!/usr/bin/env python3
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

"""Give every source file the sdist ships the Apache 2.0 header the package's modules carry.

That is the package, its tests and their fixtures, and the scripts. Left
alone are the files a header would change the meaning of (``LEFT_ALONE``),
and the example inputs and expected outputs the regression suite compares
byte for byte (``test_examples*``, which are not scanned). A shebang stays
the first line.

    python scripts/add_copyright_headers.py          # add what is missing
    python scripts/add_copyright_headers.py --check  # list it, exit 1 if any
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Dict, Iterator, List

LICENCE = """Copyright 2025-2026 Eric Allen

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

LEFT_ALONE: Dict[str, str] = {
    "tests/hostile_cases/r133_formfeed.py": (
        "a source-text fixture: where its form feeds fall relative to the lines is what it tests"
    ),
    "tests/hostile_cases/r135_formfeed_line_shifts_splice.py": (
        "a source-text fixture: its form feed on the second line, and the line numbers a splice"
        " computes around it, are what it tests"
    ),
    "tests/hostile_cases/r7fz_srctext_bom.py": (
        "a source-text fixture: its UTF-8 byte order mark must stay the file's first bytes"
    ),
    "tests/hostile_cases/r7fz_srctext_cp1252_quotes.py": (
        "a source-text fixture: its coding cookie must stay on its first two lines, and its"
        " bytes are cp1252, which a UTF-8 header would not be"
    ),
    "tests/hostile_cases/r7fz_srctext_crlf.py": (
        "a source-text fixture: its CRLF line endings are what it tests, and a header's LF"
        " lines would mix them"
    ),
    "tests/hostile_cases/r7fz_srctext_form_feed.py": (
        "a source-text fixture: where its form feed falls relative to the lines is what it tests"
    ),
    "tests/hostile_cases/r7fz_srctext_latin1_literals.py": (
        "a source-text fixture: its coding cookie must stay on its first two lines, and its"
        " bytes are latin-1, which a UTF-8 header would not be"
    ),
}
"""Files a header would change what they test, and why."""

REPOSITORY = Path(__file__).resolve().parents[1]


def _commented(prefix: str) -> str:
    """The licence as comment lines opened by ``prefix``, then a blank line."""
    lines = [f"{prefix} {line}".rstrip() for line in LICENCE.splitlines()]
    return "\n".join(lines) + "\n\n"


def _comment_prefix(path: Path) -> str:
    """How a comment begins in ``path``: ``//`` in JavaScript, ``#`` elsewhere."""
    return "//" if path.suffix in {".js", ".mjs"} else "#"


def has_copyright(content: str) -> bool:
    """Whether the file already begins with a copyright header."""
    return "Copyright" in content[:600] and "Apache License" in content[:600]


def _is_python_script(path: Path) -> bool:
    """An executable script without a suffix whose shebang names Python."""
    if path.suffix:
        return False
    with path.open("rb") as stream:
        first = stream.readline()
    return first.startswith(b"#!") and b"python" in first


def candidates(root: Path = REPOSITORY) -> Iterator[Path]:
    """Every file the policy covers, in a stable order."""
    for directory in ("src/towel", "tests"):
        yield from sorted((root / directory).rglob("*.py"))
    scripts = root / "scripts"
    for path in sorted(scripts.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        if path.suffix in {".py", ".mjs"} or path.name == "Dockerfile" or _is_python_script(path):
            yield path


def missing(root: Path = REPOSITORY) -> List[Path]:
    """The covered files without a header, less those ``LEFT_ALONE``."""
    return [
        path
        for path in candidates(root)
        if path.relative_to(root).as_posix() not in LEFT_ALONE
        and not has_copyright(path.read_text(encoding="utf-8"))
    ]


def add_header(path: Path) -> None:
    """Put the header at the top of ``path``, after its shebang if it has one."""
    content = path.read_text(encoding="utf-8")
    header = _commented(_comment_prefix(path))
    if not content.strip():
        # An empty module stays one: the header ends the file.
        path.write_text(header.rstrip("\n") + "\n", encoding="utf-8")
    elif content.startswith("#!"):
        shebang, _, rest = content.partition("\n")
        path.write_text(f"{shebang}\n{header}{rest}", encoding="utf-8")
    else:
        path.write_text(header + content, encoding="utf-8")


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="list files lacking it; change none")
    arguments = parser.parse_args(argv)
    lacking = missing()
    if arguments.check:
        for path in lacking:
            print(path.relative_to(REPOSITORY))
        return 1 if lacking else 0
    for path in lacking:
        add_header(path)
        print(f"ADDED {path.relative_to(REPOSITORY)}")
    print(f"{len(lacking)} file(s) given the header")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
