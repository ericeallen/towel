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

"""Where an inserted import goes: never above a line whose place matters, never before an effect.

One position function serves every import Towel writes: a borrower's
``from .a import __extracted_func_0``, a typed run's ``import typing as
_typing`` and its ``TYPE_CHECKING`` guard. The table below holds it to each
kind of line a module can open with, and the end-to-end cases run the
written files the way their first lines say they run.
"""

from __future__ import annotations

import ast
import contextlib
import io
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import List

import pytest

from towel.source_text import EncodingNotKept, decode_source, encode_like, source_lines
from towel.unification.insertion import import_line
from towel.unification.refactor_engine import UnificationRefactorEngine

_DEF = "def f():\n    return 1\n"

# (case, source, the 1-based line the import is written after; 0 for line 1)
_TABLE = [
    ("nothing above the first definition", _DEF, 0),
    ("a shebang", "#!/usr/bin/env python3\n" + _DEF, 1),
    ("a shebang, then a blank line", "#!/usr/bin/env python3\n\n" + _DEF, 1),
    ("a coding declaration on line 1", "# -*- coding: latin-1 -*-\n\n" + _DEF, 1),
    ("a coding declaration on line 2", "\n# vim: set fileencoding=latin-1 :\n\n" + _DEF, 2),
    ("a shebang and a coding declaration", "#!/usr/bin/python\n# coding=latin-1\n" + _DEF, 2),
    ("a leading comment block", "# Copyright 2026\n# Licensed under MIT\n\n" + _DEF, 2),
    ("a comment block touching the definition", "# helper for f\n" + _DEF, 1),
    ("a file-wide type: ignore below a blank", "# Copyright\n\n# type: ignore\n\n" + _DEF, 3),
    ("comments above a decorated definition", "# head\n\n@staticmethod\n" + _DEF, 1),
    ("a docstring", '"""Doc."""\n' + _DEF, 1),
    ("a shebang and a docstring", '#!/usr/bin/env python3\n"""Doc\n\nmore."""\n' + _DEF, 4),
    ("a __future__ import", "from __future__ import annotations\n" + _DEF, 1),
    (
        "a docstring, __future__ and imports",
        '"""Doc."""\nfrom __future__ import annotations\nimport os\nimport sys\n' + _DEF,
        4,
    ),
    ("a quiet constant after the imports", "import os\nLIMIT = 3\n" + _DEF, 1),
    ("a patch after the imports", "import time\ntime.sleep = print\n" + _DEF, 2),
    (
        "an environment change before an import",
        "import os\nos.environ['X'] = '1'\nimport m\n" + _DEF,
        3,
    ),
    ("a call before the imports", "print('loading')\nimport os\n" + _DEF, 2),
    ("a path change after the imports", "import sys\nsys.path.insert(0, 'lib')\nX = 1\n" + _DEF, 2),
    ("effects after the first definition", _DEF + "print('late')\n", 0),
    ("only comments", "# nothing\n# here\n", 2),
    ("an empty module", "", 0),
]


@pytest.mark.parametrize("source, after", [c[1:] for c in _TABLE], ids=[c[0] for c in _TABLE])
def test_the_import_goes_after_every_line_whose_place_matters(source: str, after: int) -> None:
    lines = source_lines(source)
    assert import_line(lines, ast.parse(source), source) == after
    written: List[str] = [*lines]
    written.insert(after, "import zz_inserted\n")
    text = "".join(written)
    tree = ast.parse(text)  # still a module, with the import where it was put
    assert any(isinstance(n, ast.Import) and n.names[0].name == "zz_inserted" for n in tree.body)


def test_a_bom_file_keeps_its_bom_and_its_first_line() -> None:
    data = "\ufeff# -*- coding: utf-8 -*-\n".encode("utf-8") + _DEF.encode("utf-8")
    text = decode_source(data)
    lines = source_lines(text)
    at = import_line(lines, ast.parse(text), text)
    lines.insert(at, "import zz_inserted\n")
    written = encode_like(data, "".join(lines))
    assert written.startswith(b"\xef\xbb\xbf# -*- coding: utf-8 -*-\nimport zz_inserted\n")


def test_a_write_that_would_move_the_encoding_declaration_is_refused() -> None:
    original = b"# -*- coding: latin-1 -*-\nx = '\xc3\xa9'\n"
    with pytest.raises(EncodingNotKept):
        encode_like(original, "import os\n# -*- coding: latin-1 -*-\nx = '\xc3\xa9'\n")
    kept = encode_like(original, "# -*- coding: latin-1 -*-\nimport os\nx = '\xc3\xa9'\n")
    assert kept.endswith(b"x = '\xc3\xa9'\n")


def _duplicates(first_lines: str, literal: str = "v") -> str:
    return (
        f"{first_lines}\n\ndef shout(k: int) -> str:\n    base = k * 2\n    extra = tick(base)\n"
        f"    label = '{literal} {{}} {{}}'.format(base, extra)\n    print(label)\n    return label\n\n\n"
        f"def whisper(k: int) -> str:\n    base = k * 2\n    extra = tock(base)\n"
        f"    label = '{literal} {{}} {{}}'.format(base, extra)\n    print(label)\n    return label\n\n\n"
        "def tick(v: int) -> int:\n    return v + 1\n\n\ndef tock(v: int) -> int:\n    return v - 1\n"
    )


def _refactor(directory: Path, **options: object) -> int:
    engine = UnificationRefactorEngine(min_lines=3, **options)  # type: ignore[arg-type]
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        results, _ = engine.refactor_directory_to_fixed_point(
            str(directory), str(directory), progress="none"
        )
    return sum(count for count, _ in results.values())


@pytest.mark.skipif(os.name == "nt", reason="runs the script through its #! line")
def test_r9xh_a_script_still_runs_through_its_shebang(tmp_path: Path) -> None:
    script = tmp_path / "tool.py"
    script.write_text(
        _duplicates(f"#!{sys.executable}")
        + "\n\nif __name__ == '__main__':\n    shout(1)\n    whisper(2)\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    before = subprocess.run([str(script)], capture_output=True, text=True, check=True).stdout
    assert _refactor(tmp_path) == 1
    text = script.read_text()
    assert text.startswith(f"#!{sys.executable}\n") and "__extracted_func_0" in text
    after = subprocess.run([str(script)], capture_output=True, text=True, check=True).stdout
    assert after == before == "v 2 3\nv 4 3\n"


def test_r9xh_a_latin1_borrower_keeps_its_declaration(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'pkg'\nversion = '0'\n")
    (package / "__init__.py").write_text("")
    body = (
        "def f{n}(values, offset):\n    total = sum(values) + offset\n    doubled = total * 2\n"
        "    label = '\u00c3\u00a9 {{}} {{}}'.format(total, doubled)\n    print(label)\n"
        "    return label\n"
    )
    for name in ("a", "b"):
        (package / f"{name}.py").write_bytes(
            f"# -*- coding: latin-1 -*-\n\n\n{body.format(n=name)}".encode("latin-1")
        )
    with (package / "b.py").open("ab") as handle:
        handle.write("\n\ndef stays():\n    return '\u00c3\u00a9 stays'\n".encode("latin-1"))
    (package / "c.py").write_text("from pkg import a, b\n")
    assert _refactor(package, cross_module_helpers=True) == 2
    borrower = (package / "b.py").read_bytes()
    assert borrower.startswith(b"# -*- coding: latin-1 -*-\nfrom .a import __extracted_func_0\n")
    probe = "from pkg import b; print(ascii(b.stays()))"
    ran = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(tmp_path)},
    )
    assert ran.stdout == "'\\xc3\\xa9 stays'\n"
