"""Where the module docstring ends and the imports stop, line by line."""

from __future__ import annotations

from typing import List, Tuple

import pytest

from towel.unification.insertion import InsertionPoints


@pytest.mark.parametrize(
    "lines, expected",
    [
        ([], (0, 0)),
        (["x = 1\n"], (0, 0)),
        (['"""Doc."""\n', "x = 1\n"], (0, 1)),
        (["'''Doc.'''\n", "import os\n"], (2, 1)),
        (['"""Doc\n', "more\n", '"""\n', "import os\n", "x = 1\n"], (4, 3)),
        (["import os\n", "\n", "x = 1\n"], (1, 0)),
        (["import a\n", "# between\n", "import b\n", "x = 1\n"], (3, 0)),
        (["from a import (\n", "    b,\n", "    c,\n", ")\n", "x = 1\n"], (4, 0)),
        (["import os\n", "x = 1\n", "import late\n"], (1, 0)),
    ],
    ids=[
        "empty",
        "no-docstring-no-imports",
        "docstring-only",
        "single-quoted-docstring-then-import",
        "multiline-docstring-then-import",
        "import-then-blank-then-code",
        "comment-between-imports",
        "parenthesized-import",
        "imports-after-code-are-not-counted",
    ],
)
def test_scan_reports_last_import_and_docstring_end(
    lines: List[str], expected: Tuple[int, int]
) -> None:
    assert InsertionPoints._scan_module_docstring_and_imports(lines) == expected
