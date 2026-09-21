"""Directory fixed points retry declined proposals only after project progress."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import FrozenSet, Optional
from unittest.mock import patch

import pytest

from towel.type_inference import CheckFailure, MypyInferrer
from towel.unification.exceptions import RefactoringError
from towel.unification.models import RefactoringProposal, Replacement, source_digest_of
from towel.unification.progress import ProgressMode
from towel.unification.refactor_engine import UnificationRefactorEngine


def _proposal(path: Path) -> RefactoringProposal:
    helper = ast.parse("def helper():\n    pass\n").body[0]
    assert isinstance(helper, ast.FunctionDef)
    return RefactoringProposal(
        file_path=str(path),
        extracted_function=helper,
        replacements=[Replacement((1, 1), ast.parse("VALUE = 1").body[0])],
        description=path.name,
        parameters_count=0,
        source_digests=((str(path), source_digest_of(path.read_text())),),
    )


@pytest.mark.parametrize("failure", [RefactoringError, SyntaxError])
@pytest.mark.parametrize("incremental", [False, True])
def test_permanent_refusal_finishes_without_repeating_analysis(
    tmp_path: Path,
    failure: type[Exception],
    incremental: bool,
) -> None:
    path = tmp_path / "a.py"
    path.write_text("VALUE = 0\n")
    proposal = _proposal(path)
    engine = UnificationRefactorEngine(incremental_global_passes=incremental)
    # Exhausting the side effect fails immediately if the driver regresses,
    # instead of hanging the suite while an unbounded loop retries.
    with (
        patch.object(engine, "analyze_directory", side_effect=[[proposal]]) as analyze,
        patch.object(
            engine, "apply_refactoring_multi_file", side_effect=failure("cannot render")
        ) as apply,
    ):
        results, reason = engine.refactor_directory_to_fixed_point(
            str(tmp_path), str(tmp_path), max_iterations=0, progress="none"
        )
    assert results == {} and reason == "fixed_point"
    assert analyze.call_count == apply.call_count == 1
    assert path.read_text() == "VALUE = 0\n"


@pytest.mark.parametrize("incremental", [False, True])
@pytest.mark.parametrize("eventually_succeeds", [False, True])
def test_other_proposals_continue_and_changed_context_retries_a_refusal(
    tmp_path: Path,
    incremental: bool,
    eventually_succeeds: bool,
) -> None:
    first, second = tmp_path / "a.py", tmp_path / "b.py"
    for path in (first, second):
        path.write_text("VALUE = 0\n")
    proposals = [_proposal(first), _proposal(second)]
    restrictions: list[Optional[FrozenSet[str]]] = []
    attempted: list[str] = []
    engine = UnificationRefactorEngine(incremental_global_passes=incremental)

    def analyze(
        directory: str,
        recursive: bool,
        progress: ProgressMode,
        changed_files: Optional[FrozenSet[str]],
    ) -> list[RefactoringProposal]:
        restrictions.append(changed_files)
        # Three passes find work; the fourth is the rehearing, which looks at
        # an unchanged project on purpose to hear what was declined earlier.
        # Any more than that and the run is circling.
        assert len(restrictions) <= 4, "An unchanged project was reanalyzed more than once"
        return [
            proposal
            for proposal in proposals
            if Path(proposal.file_path).read_text() == "VALUE = 0\n"
            and (changed_files is None or proposal.file_path in changed_files)
        ]

    def apply(proposal: RefactoringProposal) -> dict[str, str]:
        attempted.append(Path(proposal.file_path).name)
        if proposal.file_path == str(first) and (
            not eventually_succeeds or second.read_text() == "VALUE = 0\n"
        ):
            raise RefactoringError("The current project context refuses this proposal")
        return {proposal.file_path: "VALUE = 1\n"}

    with (
        patch.object(engine, "analyze_directory", analyze),
        patch.object(engine, "analyze_files", return_value=[]),
        patch.object(engine, "apply_refactoring_multi_file", apply),
    ):
        results, reason = engine.refactor_directory_to_fixed_point(
            str(tmp_path), str(tmp_path), max_iterations=0, progress="none"
        )
    assert attempted == ["a.py", "b.py", "a.py"]
    assert reason == "fixed_point"
    assert sum(count for count, _ in results.values()) == (2 if eventually_succeeds else 1)
    assert second.read_text() == "VALUE = 1\n"
    assert first.read_text() == ("VALUE = 1\n" if eventually_succeeds else "VALUE = 0\n")
    if incremental:
        assert restrictions[1] == frozenset({str(first), str(second)})
        # An ordinary pass looks only at what changed; the rehearing looks at
        # the whole project, so that a proposal declined anywhere is heard.
        assert None in restrictions[1:], restrictions


def test_real_checker_failure_terminates_directory_run(tmp_path: Path) -> None:
    pytest.importorskip("mypy")
    (tmp_path / "mypy.ini").write_text("[mypy]\nfiles = missing_configured_module.py\n")
    path = tmp_path / "program.py"
    original = (
        "def first(value: int) -> int:\n"
        "    total = value + 1\n    print(total)\n    return total * 2\n\n"
        "def second(value: int) -> int:\n"
        "    total = value + 1\n    print(total)\n    return total * 3\n"
    )
    path.write_text(original)
    checker = MypyInferrer()
    try:
        assert isinstance(checker.check(str(path), original), CheckFailure)
        engine = UnificationRefactorEngine(
            min_lines=2, reuse_existing_functions=False, type_oracle=checker
        )
        proposals = engine.analyze_directory(str(tmp_path), progress="none")
        assert proposals, "The failing checker must encounter a real extraction"
        with patch.object(engine, "analyze_directory", side_effect=[proposals]) as analyze:
            with pytest.raises(RefactoringError, match="Original project type check failed"):
                engine.refactor_directory_to_fixed_point(
                    str(tmp_path), str(tmp_path), max_iterations=0, progress="none"
                )
        assert analyze.call_count == 0, "A failed baseline must stop before proposal analysis"
        assert path.read_text() == original
    finally:
        checker.close()
