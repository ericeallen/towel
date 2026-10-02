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

"""Reflection is outside the preservation contract and triggers no scan or refusal."""

from __future__ import annotations

import ast
import contextlib
import io
import logging
from pathlib import Path
from typing import cast

import pytest

from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.scope_analyzer import ScopeAnalyzer
from towel.unification.semantic_safety import block_requires_original_frame


@pytest.mark.parametrize(
    "operation",
    [
        "locals()",
        "globals()",
        "vars()",
        "dir()",
        "eval('value')",
        "exec('value = 3')",
        "bi.locals()",
        "read_namespace()",
        "namespace_alias()",
        "warnings.warn('x')",
        "warn_alias('x', stacklevel=2)",
        "forward_warning('x', stacklevel=2)",
        "sys._getframe()",
        "inspect.currentframe()",
        "inspect.stack()",
        "frame_alias()",
        "reader()",
    ],
)
def test_reflective_calls_do_not_require_the_original_frame(operation: str) -> None:
    tree = ast.parse(
        "import builtins as bi\n"
        "import warnings\n"
        "from warnings import warn as warn_alias\n"
        "from builtins import locals as read_namespace\n"
        "import sys, inspect\n"
        "namespace_alias = read_namespace\n"
        "frame_alias = sys._getframe\n"
        "def reader():\n    return sys._getframe(1)\n"
        f"def subject():\n    {operation}\n"
    )
    analyzer = ScopeAnalyzer()
    analyzer.analyze(tree)
    function = cast(ast.FunctionDef, tree.body[-1])
    assert not block_requires_original_frame(analyzer, function, function.body)


@pytest.mark.parametrize("progress", ["tqdm", "none"])
def test_directory_refactor_does_not_warn_about_reflection(
    tmp_path: Path, caplog, progress
) -> None:
    project = tmp_path / "pkg"
    project.mkdir()
    (project / "__init__.py").write_text("")
    (project / "warns.py").write_text(
        "import warnings\n\n\ndef check(flag):\n"
        "    if flag:\n        warnings.warn('deprecated', stacklevel=2)\n"
    )
    (project / "plain.py").write_text("def add(a, b):\n    return a + b\n")
    engine = UnificationRefactorEngine(min_lines=3)
    with (
        caplog.at_level(logging.WARNING, logger="towel"),
        contextlib.redirect_stdout(io.StringIO()),
    ):
        engine.refactor_directory_to_fixed_point(str(project), str(project), progress=progress)
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "caller's frame" not in messages
    assert "warns.py" not in messages
