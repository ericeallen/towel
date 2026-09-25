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


MAIN_GUARD = "if __name__ == '__main__':\n    print(main(sys.argv[1:]))\n"


def _tool(tag: str, imports: str, guard: str = MAIN_GUARD) -> str:
    """A tool whose ``main`` is the duplicate, run under ``guard`` in place of the usual one."""
    source = textwrap.dedent(TOOL.format(tag=tag, imports=imports)).lstrip("\n")
    assert source.endswith(MAIN_GUARD)
    return source[: -len(MAIN_GUARD)] + guard


def _write(root: Path, files: Dict[str, str]) -> None:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _observe(
    root: Path, tools: Tuple[str, ...] = ("tool_a", "tool_b")
) -> List[Tuple[str, int, str, List[str]]]:
    """Run each tool by path, by path with the project importable, and with ``-m``."""
    observed = []
    for tool in tools:
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
    engine = UnificationRefactorEngine(min_lines=3, cross_module_helpers=True)
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
        # Packaging metadata names nothing: the tools' own absolute imports of
        # their package are what the helper's import is spelled like, and a
        # run by path resolves it wherever it resolves theirs.
        (False, "import sys\nfrom pkg import util", "import sys\nfrom pkg import util", True),
    ],
    ids=[
        "imports-nothing-of-its-package",
        "imports-its-package",
        "imports-relatively",
        "imports-its-package-without-metadata",
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


def _observe_library(root: Path) -> Tuple[int, str, List[str]]:
    """Import the library the scripts share code with, and call it."""
    completed = subprocess.run(
        [sys.executable, "-c", "import pkg.util; print(pkg.util.scaled(['xx']))"],
        cwd=root,
        capture_output=True,
        text=True,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
    )
    return completed.returncode, completed.stdout, completed.stderr.strip().splitlines()[-1:]


LIBRARY = _tool("u", "", "").replace("def main(argv):", "def scaled(argv):")


@pytest.mark.parametrize(
    "shebang, guard",
    [
        ("", "if '__main__' == __name__:\n    print(main(sys.argv[1:]))\n"),
        ("", "if __name__ == '__main__' and sys.argv:\n    print(main(sys.argv[1:]))\n"),
        (
            "",
            "try:\n    if __name__ == '__main__':\n        print(main(sys.argv[1:]))\n"
            "except KeyboardInterrupt:\n    pass\n",
        ),
        ("#!/usr/bin/env python3\n", "print(main(sys.argv[1:]))\n"),
    ],
    ids=["reversed-guard", "guard-in-a-conjunction", "guard-inside-try", "interpreter-line"],
)
def test_every_way_of_writing_a_script_counts(tmp_path: Path, shebang: str, guard: str) -> None:
    """A guard either way round, opening a conjunction, or nested, or a ``#!`` line and none.

    Two such scripts share their code with each other and with a library
    module of their package, which neither imports; running them by path
    must still work.
    """
    files = {
        "pyproject.toml": PYPROJECT,
        "pkg/__init__.py": "",
        "pkg/util.py": LIBRARY,
        "pkg/tool_a.py": shebang + _tool("a", "import sys", guard),
        "pkg/tool_b.py": shebang + _tool("b", "import sys", guard),
    }
    before, after = tmp_path / "before", tmp_path / "after"
    _write(before, files)
    _write(after, files)
    _refactor(after)
    assert _observe(after) == _observe(before)
    assert _observe_library(after) == _observe_library(before)
