"""How the hostile batteries execute a fixture before and after refactoring.

Both batteries compare the same observation of a program: its exit status,
everything it printed to stdout, and the last line of stderr, which names
the exception when it died. Earlier stderr lines hold the traceback, whose
file paths and line numbers legitimately differ after an extraction. The
program runs in an emptied environment so nothing on the developer's PATH
or in their shell variables can reach it.
"""

from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys

import pytest

ISOLATED_ENV = {"PYTHONDONTWRITEBYTECODE": "1", "PATH": ""}


def observe(script: str, cwd: Path) -> tuple[int, str, list[str]]:
    """Run ``script`` (a path relative to ``cwd``) and return what the batteries compare."""
    completed = subprocess.run(
        [sys.executable, script],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env=ISOLATED_ENV,
    )
    return completed.returncode, completed.stdout, completed.stderr.strip().splitlines()[-1:]


def parsed_or_skipped(path: Path) -> ast.Module:
    """``path`` parsed, or the calling test skipped where this Python lacks the fixture's syntax.

    A fixture may be written in syntax newer than the oldest supported Python:
    ``type Alias = ...`` and ``class Box[T]:`` need 3.12. Every test that reads
    the fixtures skips such a one there, alike.
    """
    try:
        return ast.parse(path.read_bytes(), filename=str(path))
    except SyntaxError:
        pytest.skip("the fixture is written in syntax this Python does not have")
