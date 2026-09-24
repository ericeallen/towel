"""Behavioral regressions for formatter execution, import effects and conflicts."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from typing import Literal

import pytest

from towel.changes import ChangeConflict
from towel import formatting
from towel.formatting import (
    formatter_for_project,
    import_sorter_for_project,
    imports_permuted_only,
)
from towel.unification.models import RefactoringProposal
from towel.unification.refactor_engine import UnificationRefactorEngine

DUPLICATES = """def one(value):
    x = value + 1
    y = x * 2
    z = y - 3
    return z

def two(value):
    x = value + 1
    y = x * 2
    z = y - 3
    return z + 7
"""


def _cli(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", "from towel.cli import main; main()", *arguments],
        capture_output=True,
        text=True,
        env={**os.environ, "TOWEL_WORKERS": "1"},
        timeout=10,
        check=False,
    )


def _behavior(path: Path) -> tuple[int, str, str]:
    run = subprocess.run(
        [sys.executable, str(path)], capture_output=True, text=True, timeout=5, check=False
    )
    return run.returncode, run.stdout, run.stderr


@pytest.mark.parametrize("mode", ["format", "sort"])
@pytest.mark.parametrize("shadow", ["ruff.py", "subprocess.py"])
def test_ruff_never_imports_modules_from_the_project(
    tmp_path: Path, mode: Literal["format", "sort"], shadow: str
) -> None:
    pytest.importorskip("ruff")
    (tmp_path / "pyproject.toml").write_text('[tool.ruff.lint]\nselect = ["I"]\n')
    marker = tmp_path / "executed.txt"
    (tmp_path / shadow).write_text(
        "from pathlib import Path\nimport sys\n"
        'Path("executed.txt").write_text("project code ran")\n'
        "sys.stdout.write(sys.stdin.read())\n"
    )
    module = tmp_path / "source.py"
    module.write_text("import sys\n")
    if mode == "format":
        formatter = formatter_for_project(module).tool
        assert formatter is not None
        assert formatter("value='x'") == 'value = "x"'
    else:
        sorter = import_sorter_for_project(module).tool
        assert sorter is not None
        assert sorter(str(module), "import sys\nimport os\n").startswith("import os\nimport sys\n")
    assert not marker.exists(), f"Ruff executed project-owned {shadow}"


@pytest.mark.parametrize("mode", ["format", "sort"])
@pytest.mark.parametrize("fallback", [False, True], ids=["installed-module", "path-script"])
def test_ruff_ignores_inherited_project_pythonpath(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: Literal["format", "sort"],
    fallback: bool,
) -> None:
    pytest.importorskip("ruff")
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text('[tool.ruff.lint]\nselect = ["I"]\n')
    marker = project / "executed.txt"
    (project / "sitecustomize.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('project startup code ran')\n"
    )
    # Set this after the test interpreter has started. Only the tool child
    # would consult it; the parent has never imported the project hook.
    monkeypatch.setenv("PYTHONPATH", str(project))
    if fallback:
        wrapper = tmp_path / "trusted_ruff.py"
        wrapper.write_text("import sys\nsys.stdout.write(sys.stdin.read())\n")
        monkeypatch.setattr(formatting, "_ruff_executable", lambda: [sys.executable, str(wrapper)])
    module = project / "source.py"
    source = "import os\nimport sys\n"
    module.write_text(source)
    if mode == "format":
        formatter = formatter_for_project(module).tool
        assert formatter is not None
        assert formatter("value = 1") == "value = 1"
    else:
        sorter = import_sorter_for_project(module).tool
        assert sorter is not None
        assert sorter(str(module), source).strip() == source.strip()
    assert not marker.exists(), "The Ruff child executed inherited PYTHONPATH/sitecustomize"


def test_dry_with_types_disabled_never_executes_project_ruff(tmp_path: Path) -> None:
    pytest.importorskip("ruff")
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 88\n")
    (tmp_path / "ruff.py").write_text(
        "from pathlib import Path\nimport sys\n"
        'Path("executed.txt").write_text("project code ran")\n'
        "sys.stdout.write(sys.stdin.read())\n"
    )
    source = tmp_path / "input.py"
    source.write_text(DUPLICATES)
    output = tmp_path / "output.py"
    run = _cli(
        [
            "dry",
            str(source),
            str(output),
            "--no-types",
            "--no-interactive",
            "--progress",
            "none",
        ]
    )
    assert not (tmp_path / "executed.txt").exists()
    assert run.returncode == 0, run.stderr
    assert output.read_text().count("y = x * 2") == 1


@pytest.mark.parametrize(
    "configuration",
    ['[tool.isort]\nprofile = "black"\n', '[tool.ruff.lint]\nselect = ["I"]\n'],
    ids=["isort", "ruff"],
)
def test_dry_import_sorting_preserves_last_imported_binding(
    tmp_path: Path, configuration: str
) -> None:
    pytest.importorskip("isort" if "isort" in configuration else "ruff")
    (tmp_path / "pyproject.toml").write_text(configuration)
    source = tmp_path / "input.py"
    source.write_text(
        "import math as numeric\nimport cmath as numeric\n\n"
        + DUPLICATES
        + "\nprint(numeric.sqrt(-1), one(3), two(4))\n"
    )
    expected = _behavior(source)
    assert expected == (0, "1j 5 14\n", "")
    output = tmp_path / "output.py"
    run = _cli(
        [
            "dry",
            str(source),
            str(output),
            "--no-types",
            "--no-interactive",
            "--progress",
            "none",
        ]
    )
    assert run.returncode == 0, run.stderr
    assert output.read_text().count("y = x * 2") == 1
    assert _behavior(output) == expected


@pytest.mark.parametrize(
    "original,finished",
    [
        (
            "import math as value\nimport cmath as value\n",
            "import cmath as value\nimport math as value\n",
        ),
        (
            "from math import sqrt\nfrom cmath import sqrt\n",
            "from cmath import sqrt\nfrom math import sqrt\n",
        ),
        (
            "import sys\nprint(sys.version)\nimport os\n",
            "import os\nimport sys\nprint(sys.version)\n",
        ),
        (
            "if flag:\n    import os\nelse:\n    import sys\n",
            "if flag:\n    import sys\nelse:\n    import os\n",
        ),
        (
            "def a():\n    import os\ndef b():\n    import sys\n",
            "def a():\n    import sys\ndef b():\n    import os\n",
        ),
        (
            "try:\n    import os\nexcept ImportError:\n    import sys\n",
            "try:\n    import sys\nexcept ImportError:\n    import os\n",
        ),
        (
            "from math import *\nfrom cmath import sqrt\n",
            "from cmath import sqrt\nfrom math import *\n",
        ),
        (
            "from __future__ import annotations\nimport os\n",
            "import os\nfrom __future__ import annotations\n",
        ),
        (
            "from module import value\nfrom module import value\n",
            "from module import value\n",
        ),
    ],
    ids=[
        "import-alias-order",
        "from-import-order",
        "statement-boundary",
        "conditional-branch",
        "function-scope",
        "exception-handler",
        "wildcard-boundary",
        "future-boundary",
        "repeated-import-effects",
    ],
)
def test_import_finisher_refuses_binding_or_control_flow_changes(
    original: str, finished: str
) -> None:
    finisher = imports_permuted_only(lambda path, source: finished)
    assert finisher("source.py", original) == original


def test_import_finisher_keeps_merging_and_sorting_within_nested_groups() -> None:
    original = (
        "if flag:\n"
        "    from module import zebra\n"
        "    from module import alpha\n"
        "    import sys\n"
        "    import os\n"
        "    print(alpha, zebra, os, sys)\n"
    )
    finished = (
        "if flag:\n"
        "    import os\n"
        "    import sys\n"
        "    from module import alpha, zebra\n"
        "    print(alpha, zebra, os, sys)\n"
    )
    finisher = imports_permuted_only(lambda path, source: finished)
    assert finisher("source.py", original) == finished


def test_directory_hardlink_conflict_terminates(tmp_path: Path) -> None:
    target = tmp_path / "project"
    target.mkdir()
    source = target / "program.py"
    source.write_text(DUPLICATES)
    outside = tmp_path / "outside-link.py"
    os.link(source, outside)
    run = _cli(
        [
            "dry",
            str(target),
            str(target),
            "--no-format",
            "--no-types",
            "--no-interactive",
            "--max-refactorings",
            "1",
            "--progress",
            "none",
        ]
    )
    assert run.returncode != 0, (run.stdout, run.stderr)
    assert "regular file with one link" in run.stderr
    assert source.read_text() == outside.read_text() == DUPLICATES


def test_directory_does_not_retry_a_permanent_change_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "program.py"
    source.write_text(DUPLICATES)
    attempts = 0

    def refuse(self: UnificationRefactorEngine, proposal: RefactoringProposal) -> dict[str, str]:
        nonlocal attempts
        attempts += 1
        if attempts > 1:
            raise AssertionError("A permanent conflict was retried")
        raise ChangeConflict("A transaction cannot span filesystems")

    monkeypatch.setattr(UnificationRefactorEngine, "apply_refactoring_multi_file", refuse)
    with pytest.raises(ChangeConflict, match="cannot span filesystems"):
        UnificationRefactorEngine().refactor_directory_to_fixed_point(
            str(tmp_path), str(tmp_path), progress="none"
        )
    assert attempts == 1
    assert source.read_text() == DUPLICATES
