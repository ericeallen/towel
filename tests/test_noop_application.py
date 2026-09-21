"""A proposal that renders the bytes a file already holds must not count as applied.

The plan drops files whose bytes would not change, so such an application
writes nothing. Counting it would advance the run's revision without changing
the project, and the next analysis would find the same proposal, render the
same bytes, and the run would never reach a fixed point.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Dict, List
from unittest.mock import patch

from towel.unification.models import RefactoringProposal, Replacement
from towel.unification.refactor_engine import UnificationRefactorEngine

SOURCE = "VALUE = 0\n"


def _proposal(path: Path) -> RefactoringProposal:
    helper = ast.parse("def _extracted_func_0() -> None:\n    pass\n").body[0]
    assert isinstance(helper, ast.FunctionDef)
    return RefactoringProposal(
        file_path=str(path),
        extracted_function=helper,
        replacements=[Replacement(line_range=(1, 1), node=ast.parse("VALUE = 0").body[0])],
        description="a proposal that renders what is already there",
        parameters_count=0,
    )


def _run(tmp_path: Path, renders: str) -> tuple[Dict[str, tuple[int, List[str]]], str, List[str]]:
    path = tmp_path / "a.py"
    path.write_text(SOURCE, encoding="utf-8")
    proposal = _proposal(path)
    engine = UnificationRefactorEngine()
    analyses: List[str] = []

    def analyze(*_args: object, **kwargs: object) -> List[RefactoringProposal]:
        analyses.append("pass")
        # A driver that loops would call this without bound; fail fast instead.
        assert len(analyses) <= 8, "the run did not terminate"
        # A real analysis stops offering a proposal once its site is gone.
        return [proposal] if path.read_text(encoding="utf-8") == SOURCE else []

    with (
        patch.object(engine, "analyze_directory", analyze),
        patch.object(engine, "analyze_files", return_value=[]),
        patch.object(engine, "apply_refactoring_multi_file", return_value={str(path): renders}),
    ):
        results, reason = engine.refactor_directory_to_fixed_point(
            str(tmp_path), str(tmp_path), max_iterations=0, progress="none"
        )
    return results, reason, analyses


def test_a_proposal_that_changes_nothing_ends_the_run(tmp_path: Path) -> None:
    results, reason, analyses = _run(tmp_path, SOURCE)
    assert reason == "fixed_point"
    assert results == {}, "a no-op must not be counted as an applied refactoring"
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == SOURCE
    assert len(analyses) <= 3, analyses


def test_a_proposal_that_changes_something_is_still_applied(tmp_path: Path) -> None:
    """The control: the same driver path must keep working for a real change."""
    results, reason, _ = _run(tmp_path, "VALUE = 1\n")
    assert reason == "fixed_point"
    assert sum(count for count, _ in results.values()) >= 1
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "VALUE = 1\n"
