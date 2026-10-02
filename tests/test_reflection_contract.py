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

"""Source readers and runtime rewriting do not impose extraction restrictions."""

from __future__ import annotations

import ast
import contextlib
import io
from pathlib import Path

import pytest

from towel.unification.models import is_generated_helper_name
from towel.unification.refactor_engine import UnificationRefactorEngine


@pytest.mark.parametrize(
    "imports, call",
    [
        ("from inline_snapshot import snapshot", "snapshot"),
        ("import inline_snapshot as snapshots", "snapshots.snapshot"),
        ("from inline_snapshot import snapshot as snap", "snap"),
        ("from inline_snapshot import external", "external"),
        ("from inline_snapshot import snapshot_arg", "snapshot_arg"),
    ],
)
def test_source_reading_calls_move_into_helpers_without_warning(
    tmp_path: Path, imports: str, call: str
) -> None:
    path = tmp_path / "checks.py"
    path.write_text(
        imports
        + "\n\n"
        + "\n".join(
            f"def {name}(value):\n"
            "    observed = str(value)\n"
            f"    expected = {call}('{name}')\n"
            "    assert observed == expected\n"
            "    return observed\n"
            for name in ("first", "second")
        )
    )
    engine = UnificationRefactorEngine(min_lines=3)
    stderr = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
        output, applied, _ = engine.refactor_to_fixed_point(str(path), progress="none")
    assert applied > 0
    compile(output, str(path), "exec")
    helpers = [
        node
        for node in ast.walk(ast.parse(output))
        if isinstance(node, ast.FunctionDef) and is_generated_helper_name(node.name)
    ]
    assert any(
        isinstance(node, ast.Call) and ast.unparse(node.func) == call
        for helper in helpers
        for node in ast.walk(helper)
    ), output
    assert not stderr.getvalue(), stderr.getvalue()


def test_asserts_can_be_shared_between_modules_pytest_rewrites_differently(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'asserts'\nversion = '0'\n")
    (tmp_path / "pytest.ini").write_text("[pytest]\npython_files = test_*.py\n")
    body = (
        "    total = value + 1\n"
        "    scaled = total * 2\n"
        "    assert scaled > 5\n"
        "    return scaled\n"
    )
    plain = tmp_path / "plain.py"
    rewritten = tmp_path / "test_values.py"
    plain.write_text("def first(value):\n" + body)
    rewritten.write_text("import plain\n\ndef second(value):\n" + body)
    engine = UnificationRefactorEngine(min_lines=3, cross_module_helpers=True)
    stderr = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
        results, _ = engine.refactor_directory_to_fixed_point(
            str(tmp_path), str(tmp_path), progress="none"
        )
    assert sum(applied for applied, _ in results.values()) > 0
    helpers: list[ast.FunctionDef] = []
    for path in (plain, rewritten):
        source = path.read_text()
        compile(source, str(path), "exec")
        helpers.extend(
            node
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.FunctionDef) and is_generated_helper_name(node.name)
        )
    assert any(isinstance(node, ast.Assert) for helper in helpers for node in ast.walk(helper))
    assert not stderr.getvalue(), stderr.getvalue()
