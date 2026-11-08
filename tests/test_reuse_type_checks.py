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

"""Whole-body duplicates keep their signatures while the helper they share is type-checked.

Two functions whose bodies are the same once had the second rewritten to call
the first; they now both call a new helper (see
``tests/test_functions_keep_their_own_bodies.py``), and under a strict
checker each keeps the signature it had while the helper is checked with
them.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys

import pytest

from towel.type_inference import CheckFailure, MypyInferrer
from towel.unification.exceptions import RefactoringError
from towel.unification.refactor_engine import UnificationRefactorEngine


def _source(second_type: str) -> str:
    body = (
        "    a = x + 1\n    b = a * 2\n    c = b - 3\n"
        "    d = c + 4\n    e = d * 3\n    f = e - 12\n"
        "    g = f // 3\n    h = g + 9\n    i = h * 2\n"
        "    j = i - 18\n    k = j // 2\n    return k\n"
    )
    return (
        "def first(x: int) -> int:\n" + body + "\n"
        f"def second(x: {second_type}) -> {second_type}:\n" + body
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
def test_default_cli_preserves_strict_types_of_whole_body_duplicates(
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
        timeout=60,
        check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    _assert_mypy_clean(output)
    transformed = ast.parse(output.read_text())
    signatures = {
        node.name: ast.dump(node.args) + ast.dump(node.returns or ast.Constant(None))
        for node in transformed.body
        if isinstance(node, ast.FunctionDef) and node.name in {"first", "second"}
    }
    assert signatures == {
        node.name: ast.dump(node.args) + ast.dump(node.returns or ast.Constant(None))
        for node in ast.parse(original).body
        if isinstance(node, ast.FunctionDef)
    }
    assert source.read_text() == original
    before: dict[str, object] = {}
    after: dict[str, object] = {}
    exec(original, before)
    exec(output.read_text(), after)
    for name in ("first", "second"):
        original_function, changed_function = before[name], after[name]
        assert callable(original_function) and callable(changed_function)
        arguments = (-3, 0, 2, 1.5) if name == "second" and second_type == "float" else (-3, 0, 2)
        assert [changed_function(x) for x in arguments] == [original_function(x) for x in arguments]
    if second_type == "int":
        assert "__extracted_func" in output.read_text(), "the shared body is extracted"
        assert "return first(x)" not in output.read_text()


def test_a_checker_failure_is_not_taken_for_acceptance(tmp_path: Path) -> None:
    pytest.importorskip("mypy")
    path = tmp_path / "source.py"
    original = _source("int")
    path.write_text(original)
    checker = MypyInferrer()
    checker.close()
    assert isinstance(checker.check(str(path), original), CheckFailure)
    engine = UnificationRefactorEngine(type_oracle=checker)
    proposal = engine.analyze_file(str(path))[0]
    assert proposal.reused_function is None
    with pytest.raises(RefactoringError, match="Original project type check failed"):
        engine.apply_refactoring(str(path), proposal)
    assert path.read_text() == original
    assert engine._change_log == []
