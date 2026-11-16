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

"""Original usability-audit examples exercise unification and observable CLI behavior."""

from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys

import pytest
import towel

from towel.unification.scope_analyzer import ScopeAnalyzer
from towel.unification.semantic_safety import (
    created_object_escapes,
    nested_scopes_cross_block_boundary,
)
from towel.unification.unifier import Unifier

FIXTURES = Path(__file__).parent / "fixtures" / "unify_audit"
CASES = (
    "02_literal_medium",
    "03_literal_two",
    "05_varname_medium",
    "08_attr",
    "10_block_begin",
    "38_cross_literal",
    "43_typed_generic",
)


def _module(case: str, filename: str) -> ast.Module:
    return ast.parse((FIXTURES / case / "src" / f"{filename}.py.txt").read_text())


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    return next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name
    )


@pytest.mark.parametrize(
    "case,left_file,left_name,right_file,right_name",
    [
        ("02_literal_medium", "logsummary", "summarize_errors", "logsummary", "summarize_warnings"),
        ("03_literal_two", "client", "get_user", "client", "get_invoice"),
        ("38_cross_literal", "daily", "build_report", "weekly", "build_report"),
        ("43_typed_generic", "lookup", "find_user", "lookup", "find_group"),
        ("p2_fstring", "m", "summarize_errors", "m", "summarize_warnings"),
    ],
)
def test_audit_d1_whole_twins_parameterize_fstring_text(
    case: str, left_file: str, left_name: str, right_file: str, right_name: str
) -> None:
    """Audit D1: differing f-string text must not hide whole-function twins."""
    left = _function(_module(case, left_file), left_name)
    right = _function(_module(case, right_file), right_name)
    substitution = Unifier().unify_blocks([left.body, right.body], [{}, {}])
    assert substitution is not None, case
    assert substitution.fstring_affixes, case


@pytest.mark.parametrize(
    "case,filename",
    [
        ("05_varname_medium", "dedupe"),
        ("p1c", "m"),
        ("p1r_email", "dedupe"),
        ("p1r_normalized", "dedupe"),
        ("p1r_result", "dedupe"),
        ("p1r_seen", "dedupe"),
    ],
)
def test_audit_d3_renamed_locals_unify_whole_twins(case: str, filename: str) -> None:
    """Audit D3: local assignments inside loops participate in alpha-renaming."""
    tree = _module(case, filename)
    left = _function(tree, "unique_emails")
    right = _function(tree, "unique_tags")
    renames: list[dict[str, str]] = [{}, {}]
    substitution = Unifier().unify_blocks([left.body, right.body], renames)
    assert substitution is not None, case
    if case in {"05_varname_medium", "p1r_normalized"}:
        assert renames[0]["normalized"] == renames[1]["cleaned"]


def test_audit_d1_concatenation_control_still_unifies() -> None:
    """Audit D1's concatenation control remains equivalent to the f-string example."""
    tree = _module("p2_concat", "m")
    bodies = [_function(tree, name).body for name in ("summarize_errors", "summarize_warnings")]
    assert Unifier().unify_blocks(bodies, [{}, {}]) is not None


@pytest.mark.parametrize("strict", [False, True])
def test_audit_d4_shared_customer_sort_does_not_capture_its_parameter(strict: bool) -> None:
    """Audit D4: a closed sort callback can move without changing its keyword interface."""
    tree = _module("08_attr", "customers")
    left = _function(tree, "group_by_city")
    right = _function(tree, "group_by_plan")
    # The differing loop-bound attribute in the first loop belongs to audit D2.
    # The shared suffix contains the callback whose escaping guard belongs here.
    assert Unifier().unify_blocks([left.body[2:], right.body[2:]], [{}, {}]) is not None
    analyzer = ScopeAnalyzer()
    analyzer.analyze(tree)
    assert created_object_escapes(analyzer, left, left.body[2:], strict=strict) is strict


@pytest.mark.parametrize("strict", [False, True])
def test_audit_d4_d5_ledger_prefix_has_no_captured_transaction_parameter(strict: bool) -> None:
    """Audit D4/D5: the sort lambda's tx is its own parameter, not the loop's tx."""
    tree = _module("10_block_begin", "ledger")
    left = _function(tree, "monthly_report")
    right = _function(tree, "category_breakdown")
    # These reports intentionally diverge after the shared cleanup and sort.
    assert Unifier().unify_blocks([left.body[:3], right.body[:3]], [{}, {}]) is not None
    analyzer = ScopeAnalyzer()
    analyzer.analyze(tree)
    for function in (left, right):
        assert not nested_scopes_cross_block_boundary(function, function.body[:3])
        assert (
            created_object_escapes(analyzer, function, function.body[:3], strict=strict) is strict
        )


def _run_driver(driver: Path, source: Path) -> str:
    result = subprocess.run(
        [sys.executable, str(driver), str(source)],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not result.stderr, result.stderr
    return result.stdout


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("strict", [False, True])
def test_audit_cli_preserves_original_driver_observations(
    tmp_path: Path, case: str, strict: bool
) -> None:
    """Audit D1/D3/D4/D5: each original scenario driver survives the complete CLI."""
    source = tmp_path / "src"
    source.mkdir()
    fixture = FIXTURES / case
    for file in sorted((fixture / "src").glob("*.py.txt")):
        (source / file.name.removesuffix(".txt")).write_bytes(file.read_bytes())
    driver = tmp_path / "driver.py"
    driver.write_bytes((fixture / "driver.py.txt").read_bytes())
    before = _run_driver(driver, source)
    output = tmp_path / "out"
    options = ["--cross-module"] if case == "38_cross_literal" else []
    transformed = subprocess.run(
        [
            sys.executable,
            "-m",
            "towel.cli",
            "dry",
            str(source),
            str(output),
            "--no-interactive",
            "--no-types",
            "--no-format",
            "--progress",
            "none",
            *options,
            *(["--strict"] if strict else []),
        ],
        capture_output=True,
        text=True,
        timeout=180,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(towel.__file__).resolve().parents[1]),
            "TOWEL_WORKERS": "2",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )
    assert transformed.returncode == 0, transformed.stdout + transformed.stderr
    assert output.is_dir(), transformed.stdout + transformed.stderr
    assert _run_driver(driver, output) == before

    if case in {"02_literal_medium", "05_varname_medium", "43_typed_generic"}:
        assert any("def __extracted_func_" in path.read_text() for path in output.glob("*.py"))
