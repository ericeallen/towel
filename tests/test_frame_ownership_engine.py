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

"""Actual proposals and fixed points preserve cleanup across helper frame boundaries."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from typing import Optional

import pytest

from towel.unification.function_index import FunctionIndex
from tests.test_cli_integration import invoke
from towel.unification.models import RefactoringProposal
from towel.unification.refactor_engine import UnificationRefactorEngine

FIXTURES = Path(__file__).parent / "hostile_cases"


def _run(path: Path) -> bytes:
    completed = subprocess.run([sys.executable, "-B", str(path)], capture_output=True, check=True)
    assert not completed.stderr
    return completed.stdout


def _source(tmp_path: Path, text: str) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname="frame-proof"\nversion="0.0.0"\nrequires-python=">=3.11"\n'
    )
    source = project / "input.py"
    source.write_text(text)
    return source


@pytest.mark.parametrize("case", ["external_exception_partial", "value_return_owner"])
def test_actual_proposals_and_fixed_point_preserve_original_finalizer_order(
    tmp_path: Path, case: str
) -> None:
    original = FIXTURES / ("r1792_" + case + ".py")
    source = _source(tmp_path, original.read_text())
    before_bytes, before_events = source.read_bytes(), _run(source)
    engine = UnificationRefactorEngine(annotate_helpers=False)
    proposals = engine.analyze_file(str(source))
    if case == "external_exception_partial":
        assert not proposals  # The asymmetric prefix forces only the unsafe partial shape.
    else:
        assert proposals and all(rep.argument_handoff for p in proposals for rep in p.replacements)
    for index, proposal in enumerate(proposals):
        output = tmp_path / f"proposal-{index}.py"
        output.write_text(engine.apply_refactoring(str(source), proposal))
        assert _run(output) == before_events
    output = tmp_path / "fixed.py"
    _, applied, _ = engine.refactor_to_fixed_point(
        str(source), output_path=str(output), progress="none"
    )
    assert _run(output) == before_events
    assert source.read_bytes() == before_bytes
    assert (applied == 0) if case == "external_exception_partial" else (applied > 0)


def test_partial_block_without_outside_ownership_stays_useful(tmp_path: Path) -> None:
    text = (FIXTURES / "r1792_external_exception_partial.py").read_text()
    text = text.replace("outer = Resource('outer:first')", "outer = 1").replace(
        "outer: Resource = Resource('outer:second')", "outer: int = 1"
    )
    source = _source(tmp_path, text)
    engine = UnificationRefactorEngine(annotate_helpers=False)
    proposals = engine.analyze_file(str(source))
    assert proposals and not any(rep.argument_handoff for p in proposals for rep in p.replacements)
    before = _run(source)
    output = tmp_path / "out.py"
    _, applied, _ = engine.refactor_to_fixed_point(
        str(source), output_path=str(output), progress="none"
    )
    assert applied > 0 and "__extracted_func_" in output.read_text()
    assert _run(output) == before


class _ReuseObservedEngine(UnificationRefactorEngine):
    reuse_calls: int = 0

    def _reusing_generated_helper(
        self, proposal: RefactoringProposal, functions: FunctionIndex
    ) -> Optional[RefactoringProposal]:
        self.reuse_calls += 1
        return super()._reusing_generated_helper(proposal, functions)


def test_handoff_keeps_fresh_helper_and_safe_no_new_owner_still_considers_reuse(
    tmp_path: Path,
) -> None:
    original = (FIXTURES / "r1792_value_return_owner.py").read_text()
    source = _source(tmp_path, original)
    handoff_engine = _ReuseObservedEngine(annotate_helpers=False)
    handoff = handoff_engine.analyze_file(str(source))
    assert handoff and handoff_engine.reuse_calls == 0
    assert all(p.reused_function is None for p in handoff)
    source.write_text(
        original.replace("inner = Resource('inner:first')", "x = 1\n    y = 2\n    inner = 3")
        .replace("inner = Resource('inner:second')", "x = 1\n    y = 2\n    inner = 3")
        .replace("return value", "return x * y + inner + value")
    )
    safe_engine = _ReuseObservedEngine(annotate_helpers=False)
    safe = safe_engine.analyze_file(str(source))
    assert safe and safe_engine.reuse_calls > 0
    assert not any(rep.argument_handoff for p in safe for rep in p.replacements)
    output = tmp_path / "safe.py"
    before = _run(source)
    _, applied, _ = safe_engine.refactor_to_fixed_point(
        str(source), output_path=str(output), progress="none"
    )
    assert applied > 0 and _run(output) == before


def test_default_typed_cli_preserves_whole_body_parameter_cleanup(tmp_path: Path) -> None:
    text = (FIXTURES / "r1792_value_return_owner.py").read_text()
    text = text.replace("events = []", "events: list[str] = []")
    text = text.replace("def __init__(self, name):", "def __init__(self, name: str) -> None:")
    text = text.replace("def __del__(self):", "def __del__(self) -> None:")
    text = text.replace("def fail():", "def fail() -> None:")
    text = text.replace("def first(unused):", "def first(unused: Resource) -> int:")
    text = text.replace("def second(unused):", "def second(unused: Resource) -> int:")
    source = _source(tmp_path, text)
    with (source.parent / "pyproject.toml").open("a") as settings:
        settings.write("\n[tool.mypy]\nstrict = true\n")
    before = _run(source)
    output = tmp_path / "typed.py"
    completed = invoke(["dry", str(source), str(output), "--no-interactive", "--progress", "none"])
    assert completed.status == 0, completed.stderr
    assert "__extracted_func_" in output.read_text()
    assert "del unused" in output.read_text()
    assert _run(output) == before
