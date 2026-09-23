"""A module run as a script by its path must not gain an import it cannot resolve there.

``python pkg/tool_b.py`` puts ``pkg/`` itself on ``sys.path``, not the
directory above it, so ``from pkg.tool_a import helper`` fails there with
``ModuleNotFoundError`` and ``from .tool_a import helper`` with an
``ImportError``, while ``python -m pkg.tool_b`` still works (audit case r08).
A module with a ``__main__`` guard may therefore gain an import only when
running it by path already needed what the import needs: when its leading
imports already import its own package absolutely, and the import it gains
is absolute too, or already run a relative import, which fails by path
anyway. Each case runs the project by path, by path with the package
importable, and with ``-m``, before and after the refactoring.
"""

from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap
from typing import Dict, List, Tuple

import pytest

from towel.unification.refactor_engine import UnificationRefactorEngine

TOOL = """
{imports}

def main(argv):
    print('pre', '{tag}')
    total = 0
    for a in argv:
        total += len(a) * 2
    print('mid', total)
    return total


if __name__ == '__main__':
    print(main(sys.argv[1:]))
"""


def _tool(tag: str, imports: str) -> str:
    return textwrap.dedent(TOOL.format(tag=tag, imports=imports)).lstrip("\n")


def _write(root: Path, files: Dict[str, str]) -> None:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _observe(root: Path) -> List[Tuple[str, int, str, List[str]]]:
    """Run each tool by path, by path with the project importable, and with ``-m``."""
    observed = []
    for tool in ("tool_a", "tool_b"):
        for label, command, extra_path in (
            ("path", [sys.executable, f"pkg/{tool}.py", "xx"], None),
            ("path+root", [sys.executable, f"pkg/{tool}.py", "xx"], str(root)),
            ("module", [sys.executable, "-m", f"pkg.{tool}", "xx"], None),
        ):
            environment = {"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"}
            if extra_path is not None:
                environment["PYTHONPATH"] = extra_path
            completed = subprocess.run(
                command, cwd=root, capture_output=True, text=True, env=environment, check=False
            )
            observed.append(
                (
                    f"{tool} {label}",
                    completed.returncode,
                    completed.stdout,
                    completed.stderr.strip().splitlines()[-1:],
                )
            )
    return observed


def _refactor(root: Path) -> int:
    engine = UnificationRefactorEngine(min_lines=3)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        results, _ = engine.refactor_directory_to_fixed_point(
            str(root / "pkg"), str(root / "pkg"), progress="none"
        )
    return sum(applied for applied, _ in results.values())


PYPROJECT = '[project]\nname = "pkg"\nversion = "0"\n'


@pytest.mark.parametrize(
    "metadata, imports_a, imports_b, transformed",
    [
        (True, "import sys", "import sys", False),
        (True, "import sys\nfrom pkg import util", "import sys\nfrom pkg import util", True),
        (True, "import sys\nfrom . import util", "import sys\nfrom . import util", True),
        # Without packaging metadata the helper's import would be relative,
        # which no run by path resolves: declined when the import is written.
        (False, "import sys\nfrom pkg import util", "import sys\nfrom pkg import util", False),
    ],
    ids=[
        "imports-nothing-of-its-package",
        "imports-its-package",
        "imports-relatively",
        "would-gain-a-relative-import",
    ],
)
def test_a_script_by_path_keeps_running_as_it_did(
    tmp_path: Path, metadata: bool, imports_a: str, imports_b: str, transformed: bool
) -> None:
    files = {
        **({"pyproject.toml": PYPROJECT} if metadata else {}),
        "pkg/__init__.py": "",
        "pkg/util.py": "VALUE = 1\n",
        "pkg/tool_a.py": _tool("a", imports_a),
        "pkg/tool_b.py": _tool("b", imports_b),
    }
    before, after = tmp_path / "before", tmp_path / "after"
    _write(before, files)
    _write(after, files)
    applied = _refactor(after)
    assert _observe(after) == _observe(before)
    assert (applied > 0) == transformed


def test_a_script_that_already_fails_by_path_may_borrow(tmp_path: Path) -> None:
    """Its own leading relative import already stops a run by path, at the same line."""
    _write(
        tmp_path,
        {
            "pyproject.toml": PYPROJECT,
            "pkg/__init__.py": "",
            "pkg/util.py": "VALUE = 1\n",
            "pkg/tool_a.py": _tool("a", "import sys\nfrom . import util"),
            "pkg/tool_b.py": _tool("b", "import sys\nfrom . import util"),
        },
    )
    shutil.copytree(tmp_path, tmp_path.parent / "reference", dirs_exist_ok=True)
    assert _refactor(tmp_path) > 0
    assert (
        "extracted_func"
        in (tmp_path / "pkg" / "tool_b.py").read_text()
        + (tmp_path / "pkg" / "tool_a.py").read_text()
    )
