"""Ordinary protocol results keep caller ownership across extracted helpers."""

from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys

import pytest

from tests.test_cli_integration import invoke
from towel.unification.assignment_analyzer import analyze_assignments
from towel.unification.refactor_engine import UnificationRefactorEngine


@pytest.mark.parametrize(
    "case",
    [
        "property",
        "subscript",
        "operator",
        "context_target",
        "iteration_target",
        "nested_if",
        "caught_exception",
        "explicit_call_control",
    ],
)
def test_protocol_results_preserve_finalizer_order(tmp_path: Path, case: str) -> None:
    original = Path(__file__).parent / "hostile_cases" / f"r1792_protocol_{case}.py"
    source, output = tmp_path / "input.py", tmp_path / "output.py"
    text = original.read_text()
    source.write_text(text)
    engine = UnificationRefactorEngine(annotate_helpers=False)
    engine.refactor_to_fixed_point(str(source), output_path=str(output), progress="none")
    before = subprocess.run([sys.executable, "-B", str(source)], capture_output=True, check=True)
    after = subprocess.run([sys.executable, "-B", str(output)], capture_output=True, check=True)
    assert (after.stdout, after.stderr) == (before.stdout, before.stderr)
    assert source.read_text() == text
    if case in {"property", "subscript", "operator", "nested_if", "explicit_call_control"}:
        assert "def __extracted_func_" in output.read_text()


@pytest.mark.parametrize("case", ["property", "nested_if"])
def test_default_typed_cli_preserves_protocol_lifetime(tmp_path: Path, case: str) -> None:
    source, output = tmp_path / "input.py", tmp_path / "output.py"
    original = Path(__file__).parent / "hostile_cases" / f"r1792_protocol_{case}.py"
    source.write_bytes(original.read_bytes())
    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n")
    before = subprocess.run([sys.executable, "-B", str(source)], capture_output=True, check=True)
    result = invoke(["dry", str(source), str(output), "--no-interactive", "--progress", "none"])
    assert result.status == 0, result.stderr
    after = subprocess.run([sys.executable, "-B", str(output)], capture_output=True, check=True)
    assert (after.stdout, after.stderr) == (before.stdout, before.stderr)
    assert "def __extracted_func_" in output.read_text()


def test_nested_binding_positions_exclude_the_candidate_and_include_its_continuation() -> None:
    tree = ast.parse("""def f(provider):
    if provider:
        resource = provider.obtain()
        label = resource.label
        later = label.lower()
        return resource, later
""")
    function = tree.body[0]
    assert isinstance(function, ast.FunctionDef)
    branch = function.body[0]
    assert isinstance(branch, ast.If)
    block = branch.body[:2]
    engine = UnificationRefactorEngine()
    span = engine._block_line_span(block)
    assert span is not None
    snapshot = engine._compute_block_binding_snapshot(
        function, block, span, analyze_assignments(function)
    )
    assert snapshot.bound_before_block == {"provider"}
    assert snapshot.initially_bound == {"resource", "label"}
    assert snapshot.bound_after_block == {"later"}
    assert engine._find_return_variables(function, span, snapshot.initially_bound, block) == {
        "resource",
        "label",
    }


def test_semicolon_binding_positions_do_not_treat_siblings_as_preceding() -> None:
    function = ast.parse("def f():\n    first = 1; second = 2; third = 3\n").body[0]
    assert isinstance(function, ast.FunctionDef)
    block = function.body[1:2]
    engine = UnificationRefactorEngine()
    span = engine._block_line_span(block)
    assert span is not None
    snapshot = engine._compute_block_binding_snapshot(
        function, block, span, analyze_assignments(function)
    )
    assert snapshot.bound_before_block == {"first"}
    assert snapshot.initially_bound == {"second"}
    assert snapshot.bound_after_block == {"third"}
