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

"""The run's import model, asked in the paths a run works on, sees what it cannot read.

A run leaves a directory out with ``--exclude``, and the model reads nothing
there. What an import that enters it executes is then unknown, not empty:
pip's ``_vendor`` left out of a run is still what ``import pip._vendor.rich``
runs, and a borrower made to import a host that loads a vendored module
would start running that module's import-time code.
"""

from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from typing import Mapping

from towel.unification.import_graph import (
    ImportChange,
    ImportGraphCache,
    import_change,
    would_create_import_cycle,
)
from towel.unification.program_imports import program_imports
from towel.unification.refactor_engine import UnificationRefactorEngine

_BLOCK = """
def {name}(values):
    total = 0
    for value in values:
        if value > 1:
            total += value * 2
        else:
            total -= value
    total = total + {offset}
    return total
"""


def _write(root: Path, files: Mapping[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
    return root


def _vendoring(root: Path) -> Path:
    """A package whose first module loads a vendored module that prints as it is imported.

    ``a_host`` sorts first, so it is the pair's own file and the host tried first.
    """
    return _write(
        root,
        {
            "pkg/__init__.py": "",
            "pkg/_vendor/__init__.py": "",
            "pkg/_vendor/noisy.py": 'print("loading noisy")\n',
            "pkg/a_host.py": "from pkg._vendor import noisy\n\n"
            + _BLOCK.format(name="fa", offset=1),
            "pkg/b_borrower.py": _BLOCK.format(name="fb", offset=2),
            "tests/test_pkg.py": "import pkg.a_host\nimport pkg.b_borrower\n",
        },
    )


def test_an_import_into_an_excluded_directory_is_unknown_not_empty(tmp_path: Path) -> None:
    root = _vendoring(tmp_path / "project")
    host, borrower = str(root / "pkg/a_host.py"), str(root / "pkg/b_borrower.py")
    assert import_change(host, borrower, ImportGraphCache()) is ImportChange.RUNS_CODE
    excluded = ImportGraphCache(excluded_names=["_vendor"])
    assert import_change(host, borrower, excluded) is ImportChange.UNKNOWN
    assert would_create_import_cycle(host, {borrower}, excluded)
    # The other way round nothing enters the vendored directory.
    assert import_change(borrower, host, excluded) is None
    assert not would_create_import_cycle(borrower, {host}, excluded)


def test_excluding_a_vendored_directory_does_not_hide_what_a_host_runs(tmp_path: Path) -> None:
    root = _vendoring(tmp_path / "project")

    def imported() -> str:
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                f"import sys\nsys.path.insert(0, {str(root)!r})\nimport pkg.b_borrower\n",
            ],
            capture_output=True,
            text=True,
            cwd=root,
            env={"PATH": os.environ.get("PATH", "")},
            timeout=60,
        )
        assert completed.returncode == 0, completed.stderr
        return completed.stdout

    before = imported()
    engine = UnificationRefactorEngine(
        min_lines=3, cross_module_helpers=True, excluded_directories=["_vendor"]
    )
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        results, _ = engine.refactor_directory_to_fixed_point(
            str(root / "pkg"), str(root / "pkg"), progress="none"
        )
    assert results, "the duplicate must still be shared, from the module that loads nothing"
    assert "def __extracted_func_0(" in (root / "pkg/b_borrower.py").read_text()
    assert imported() == before == ""


def test_a_stage_is_answered_from_the_project_it_copies(tmp_path: Path) -> None:
    root = _write(
        tmp_path / "project",
        {
            "alpha/__init__.py": "",
            "alpha/a.py": "",
            "alpha/b.py": "",
            "tests/t.py": "import alpha.a\n",
        },
    )
    stage = tmp_path / "stage" / "project"
    _write(stage, {"alpha/__init__.py": "", "alpha/a.py": "", "alpha/b.py": ""})
    program = program_imports(root, stage_root=stage)
    assert program.origin(stage / "alpha" / "a.py") == root.resolve() / "alpha" / "a.py"
    assert program.origin(root / "alpha" / "a.py") == root.resolve() / "alpha" / "a.py"
    # A file the stage copied is read there; one it did not is read where it is.
    assert program.in_run(root.resolve() / "alpha" / "b.py") == stage.resolve() / "alpha" / "b.py"
    assert program.in_run(root.resolve() / "tests" / "t.py") == root.resolve() / "tests" / "t.py"
    spelled = program.spelling(stage / "alpha" / "b.py", stage / "alpha" / "a.py")
    assert spelled is not None and spelled.module == ".a"
    assert program.module_name(stage / "alpha" / "b.py") == "alpha.b"
