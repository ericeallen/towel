"""Execute cross-file hostile fixtures before and after directory refactoring.

Each fixture directory holds a package whose modules duplicate a block that
reads a module-level name with a different meaning in each module: a local
function, a class, an import alias, or ``__file__``. The helper must receive
that name from its caller rather than resolve it in the helper's own module.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import pytest

from towel.unification.refactor_engine import UnificationRefactorEngine

CASES = Path(__file__).parent / "hostile_crossfile"


def _run(root: Path) -> tuple[int, str, str]:
    completed = subprocess.run(
        [sys.executable, "run.py"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": ""},
    )
    return completed.returncode, completed.stdout, completed.stderr.strip().splitlines()[-1:]


@pytest.mark.parametrize("case", sorted(path.name for path in CASES.iterdir() if path.is_dir()))
def test_directory_refactoring_preserves_program_output(case: str) -> None:
    with tempfile.TemporaryDirectory(prefix="towel-hostile-xf-") as directory:
        before = Path(directory) / "before"
        after = Path(directory) / "after"
        shutil.copytree(CASES / case, before)
        shutil.copytree(CASES / case, after)
        engine = UnificationRefactorEngine(min_lines=3)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            results, _ = engine.refactor_directory_to_fixed_point(
                str(after / "pkg"), str(after / "pkg"), progress="none"
            )
        assert results, "Each fixture must exercise a real cross-file extraction"
        assert _run(after) == _run(before)
