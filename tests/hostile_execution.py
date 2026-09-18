"""How the hostile batteries execute a fixture before and after refactoring.

Both batteries compare the same observation of a program: its exit status,
everything it printed to stdout, and the last line of stderr, which names
the exception when it died. Earlier stderr lines hold the traceback, whose
file paths and line numbers legitimately differ after an extraction. The
program runs in an emptied environment so nothing on the developer's PATH
or in their shell variables can reach it.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

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
