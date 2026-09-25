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

"""Apply one proposal to a small project under the real mypy, and say what the ladder did.

The typed ladder is tested end to end: a module is written into a project whose
``pyproject.toml`` configures mypy, the engine analyzes it, the one proposal
named is applied through a checker that counts the project checks it is asked
for, and the outcome records the helper as rendered, the module as it would be
written, or the refusal, with how many prospective checks it cost (the
baseline check of the original project is not counted).
"""

from __future__ import annotations

import ast
import importlib.util
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import List, Mapping, Optional, Sequence

import pytest

from towel.type_inference import CheckResult, MypyInferrer
from towel.unification.exceptions import RefactoringError
from towel.unification.refactor_engine import UnificationRefactorEngine

requires_mypy = pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")

STRICT = "[tool.mypy]\nstrict = true\n"


class CountingMypy(MypyInferrer):
    """The project's mypy, remembering every set of sources it was asked to check."""

    def __init__(self) -> None:
        super().__init__()
        self.checked: List[Mapping[str, str]] = []

    def check_project(
        self, sources: Mapping[str, str], *, excluded_paths: Sequence[str] = ()
    ) -> CheckResult:
        self.checked.append(dict(sources))
        return super().check_project(sources, excluded_paths=excluded_paths)


@dataclass(frozen=True)
class TypedOutcome:
    """What applying the proposal did: the rendered module or the refusal, and its cost."""

    module: Optional[str]
    error: Optional[RefactoringError]
    prospective_checks: int
    checked_helpers: List[str]
    """Every helper signature the checker was asked about, in order, as ``ast.unparse`` shows it."""

    def helper(self) -> ast.FunctionDef:
        """The helper the applied module defines."""
        assert self.module is not None, self.error
        found = [
            node
            for node in ast.walk(ast.parse(self.module))
            if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name
        ]
        assert len(found) == 1, self.module
        return found[0]

    def signature(self) -> str:
        """The helper's parameters and return, as written."""
        helper = self.helper()
        returns = f" -> {ast.unparse(helper.returns)}" if helper.returns is not None else ""
        return f"({ast.unparse(helper.args)}){returns}"


def _helper_signatures(sources: Sequence[Mapping[str, str]]) -> List[str]:
    return [
        ast.unparse(node.args) + (f" -> {ast.unparse(node.returns)}" if node.returns else "")
        for checked in sources
        for source in checked.values()
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name
    ]


def apply_one(
    tmp_path: Path,
    source: str,
    *,
    pick: str,
    config: str = STRICT,
    name: str = "module.py",
    oracle: Optional[MypyInferrer] = None,
) -> TypedOutcome:
    """Write ``source`` as ``name`` in a project configured by ``config`` and apply the proposal ``pick`` names.

    ``pick`` is a substring of the proposal's description. ``oracle`` replaces
    the counting mypy, for a test that needs to script what the checker says;
    it must count its own checks in a ``checked`` list as ``CountingMypy`` does.
    """
    (tmp_path / "pyproject.toml").write_text(config)
    path = tmp_path / name
    path.write_text(textwrap.dedent(source).lstrip())
    checker = oracle if oracle is not None else CountingMypy()
    try:
        engine = UnificationRefactorEngine(
            min_lines=2, reuse_existing_functions=False, type_oracle=checker
        )
        chosen = [p for p in engine.analyze_file(str(path)) if pick in p.description]
        assert len(chosen) == 1, [p.description for p in engine.analyze_file(str(path))]
        module: Optional[str] = None
        error: Optional[RefactoringError] = None
        try:
            module = engine.apply_refactoring(str(path), chosen[0])
        except RefactoringError as refused:
            error = refused
    finally:
        checker.close()
    checked: List[Mapping[str, str]] = getattr(checker, "checked")
    return TypedOutcome(module, error, len(checked) - 1, _helper_signatures(checked[1:]))
