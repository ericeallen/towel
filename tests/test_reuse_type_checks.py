"""Existing callees retain their signatures while new callers are type-checked."""

from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from towel.type_inference import CheckFailure, MypyInferrer
from towel.unification.exceptions import RefactoringError
from towel.unification.refactor_engine import UnificationRefactorEngine


def _source(second_type: str) -> str:
    return (
        "def first(x: int) -> int:\n"
        "    a = x + 1\n    b = a * 2\n    c = b - 3\n    return c\n\n"
        f"def second(x: {second_type}) -> {second_type}:\n"
        "    a = x + 1\n    b = a * 2\n    c = b - 3\n    return c\n"
    )


def _assert_mypy_clean(path: Path) -> None:
    run = subprocess.run(
        [
            sys.executable,
            "-P",
            "-m",
            "mypy",
            "--no-incremental",
            "--cache-dir",
            str(path.parent / "mypy-cache"),
            str(path),
        ],
        cwd=path.parent,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr


@pytest.mark.parametrize("second_type", ["int", "float"])
def test_default_cli_preserves_strict_types_when_reusing_functions(
    tmp_path: Path, second_type: str
) -> None:
    pytest.importorskip("mypy")
    (tmp_path / "mypy.ini").write_text("[mypy]\nstrict = true\n")
    source, output = tmp_path / "original.py", tmp_path / "refactored.py"
    original = _source(second_type)
    source.write_text(original)
    _assert_mypy_clean(source)
    run = subprocess.run(
        [
            sys.executable,
            "-c",
            "from towel.cli import main; main()",
            "dry",
            str(source),
            str(output),
            "--no-format",
            "--no-interactive",
            "--progress",
            "none",
        ],
        cwd=tmp_path,
        env={**os.environ, "TOWEL_WORKERS": "1"},
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    _assert_mypy_clean(output)
    transformed = output.read_text()
    assert ast.dump(ast.parse(transformed).body[0]) == ast.dump(ast.parse(original).body[0])
    assert source.read_text() == original
    if second_type == "int":
        assert "return first(x)" in transformed, "Compatible reuse must still apply"
    else:
        assert transformed == original, "An incompatible existing signature must not be widened"


def test_invalid_reuse_is_checked_once_without_annotation_fallback(tmp_path: Path) -> None:
    pytest.importorskip("mypy")
    (tmp_path / "mypy.ini").write_text("[mypy]\nstrict = true\n")
    path = tmp_path / "source.py"
    original = _source("float")
    path.write_text(original)
    checker = MypyInferrer()
    try:
        engine = UnificationRefactorEngine(type_oracle=checker)
        proposal = engine.analyze_file(str(path))[0]
        assert proposal.reused_function is not None
        with patch.object(checker, "check_project", wraps=checker.check_project) as check:
            with pytest.raises(RefactoringError, match="Reusing.*type errors"):
                engine.apply_refactoring(str(path), proposal)
        assert check.call_count == 2, "Check the baseline and the unchanged callee signature once"
        assert path.read_text() == original
        assert engine._change_log == []
    finally:
        checker.close()


def test_reuse_does_not_treat_checker_failure_as_acceptance(tmp_path: Path) -> None:
    pytest.importorskip("mypy")
    path = tmp_path / "source.py"
    original = _source("int")
    path.write_text(original)
    checker = MypyInferrer()
    checker.close()
    assert isinstance(checker.check(str(path), original), CheckFailure)
    engine = UnificationRefactorEngine(type_oracle=checker)
    proposal = engine.analyze_file(str(path))[0]
    assert proposal.reused_function is not None
    with pytest.raises(RefactoringError, match="Original project type check failed"):
        engine.apply_refactoring(str(path), proposal)
    assert path.read_text() == original
    assert engine._change_log == []
