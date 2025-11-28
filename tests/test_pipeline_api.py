from __future__ import annotations

from pathlib import Path
from typing import List

from towel.unification.pipeline import run_pipeline
from towel.unification.refactor_engine import UnificationRefactorEngine


PROJECT_ROOT = Path(__file__).parent.parent
EXAMPLES_DIR = PROJECT_ROOT / "test_examples"


def example_paths(names: List[str]) -> List[str]:
    return [str(EXAMPLES_DIR / n) for n in names]


def test_run_pipeline_smoke_single_file():
    # Basic smoke test: ensure pipeline runs and produces at least one proposal
    files = example_paths(["example1_simple.py"])
    proposals = run_pipeline(files)

    # Contract: returns a list of RefactoringProposal, possibly non-empty for this example
    assert isinstance(proposals, list)
    # example1_simple is known to produce at least one proposal in the existing suite
    assert len(proposals) >= 1


def test_run_pipeline_matches_engine_counts_multi_file():
    # Compare proposal counts between new pipeline and legacy engine API
    files = example_paths(["example1_simple.py", "example2_classes.py"])
    eng = UnificationRefactorEngine()

    pipeline_props = run_pipeline(files, engine=eng)
    engine_props = eng.analyze_files(files)

    # We don't compare object identity (AST nodes differ), but counts should match
    assert len(pipeline_props) == len(engine_props)

    # Additionally, compare a few stable attributes across proposals when available
    def sigs(props):
        return sorted(
            (
                p.description,
                p.parameters_count,
                len(p.replacements),
                p.insert_into_class,
                p.insert_into_function,
                p.method_kind,
            )
            for p in props
        )

    assert sigs(pipeline_props) == sigs(engine_props)


def test_run_pipeline_handles_missing_or_invalid_files_gracefully():
    # Nonexistent or invalid files should be skipped without raising exceptions
    bogus = [str(PROJECT_ROOT / "this_file_does_not_exist.py")]
    proposals = run_pipeline(bogus, verbose=True, progress="auto")
    assert proposals == []
