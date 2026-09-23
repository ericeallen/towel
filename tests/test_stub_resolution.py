"""A module with a stub beside it is checked as the project's own mypy resolves it.

mypy's walk of a directory keeps ``a.pyi`` and never reads ``a.py``, and an
import of the module finds the stub first, so every importer sees the stub.
Only a configuration that names ``a.py`` itself makes mypy check the
implementation. The worker collapsed the pair to the implementation, the file
Towel was changing, so an importer of a name the implementation gained and the
stub lacks was checked against the implementation: clean for Towel, an
``attr-defined`` error for the project (the release audit's k30_selfstub).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

import pytest

pytest.importorskip("mypy")

from towel.type_inference import CheckSuccess, MypyInferrer  # noqa: E402

STUB = "def fa(x: int) -> int: ...\n"
IMPLEMENTATION = "def fa(x: int) -> int:\n    return x + 1\n"
CHANGED_IMPLEMENTATION = "def helper(x: int) -> int:\n    return x + 1\n\n\ndef fa(x: int) -> int:\n    return helper(x)\n"
IMPORTER = "def fb(x: int) -> int:\n    return x * 2\n"
CHANGED_IMPORTER = "from alpha.a import helper\n\n\ndef fb(x: int) -> int:\n    return helper(x)\n"


def _project(root: Path, files: Optional[str]) -> Dict[str, Path]:
    package = root / "src" / "alpha"
    package.mkdir(parents=True)
    configuration = "[tool.mypy]\nstrict = true\n"
    if files is not None:
        configuration += f"files = {files}\n"
    (root / "pyproject.toml").write_text(configuration, encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "py.typed").write_text("", encoding="utf-8")
    (package / "a.pyi").write_text(STUB, encoding="utf-8")
    (package / "a.py").write_text(IMPLEMENTATION, encoding="utf-8")
    (package / "b.py").write_text(IMPORTER, encoding="utf-8")
    return {"a": package / "a.py", "b": package / "b.py"}


def _projects_own_mypy(root: Path, arguments: List[str]) -> List[str]:
    environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    completed = subprocess.run(
        [sys.executable, "-m", "mypy", "--no-incremental", "--cache-dir=/dev/null", *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    return sorted(
        line.split(": error: ")[1].split("  [")[0]
        for line in completed.stdout.splitlines()
        if ": error: " in line
    )


@pytest.mark.parametrize(
    "files, run_as, stub_wins",
    [
        (None, ["src"], True),
        ('["src"]', [], True),
        ('["src/alpha/a.py", "src/alpha/b.py", "src/alpha/__init__.py"]', [], False),
    ],
    ids=["walked-directory", "configured-directory", "configured-implementation"],
)
def test_an_importer_sees_the_module_the_projects_mypy_would_show_it(
    tmp_path: Path, files: Optional[str], run_as: List[str], stub_wins: bool
) -> None:
    root = tmp_path / "project"
    paths = _project(root, files)
    prospective = {str(paths["a"]): CHANGED_IMPLEMENTATION, str(paths["b"]): CHANGED_IMPORTER}
    checker = MypyInferrer()
    try:
        assert (
            checker.check_project(
                {str(path): path.read_text(encoding="utf-8") for path in paths.values()}
            )
            == CheckSuccess()
        ), "the original project is clean"
        result = checker.check_project(prospective)
    finally:
        checker.close()
    for path, text in prospective.items():
        Path(path).write_text(text, encoding="utf-8")
    truth = _projects_own_mypy(root, run_as)
    assert isinstance(result, CheckSuccess), result
    found = sorted(error.message for error in result.errors)
    assert found == truth, (found, truth)
    assert bool(truth) is stub_wins, truth
