from __future__ import annotations

from tests.test_helpers import PROJECT_ROOT, example_paths
from towel.unification.pipeline import run_pipeline
from towel.unification.refactor_engine import UnificationRefactorEngine


def test_run_pipeline_smoke_single_file():
    # Basic smoke test: ensure pipeline runs and produces at least one proposal
    files = example_paths(["example1_simple.py"])
    proposals = run_pipeline(files, engine=UnificationRefactorEngine())

    # example1_simple holds one duplicated validation block shared by two of
    # its three functions.
    assert [p.description for p in proposals] == [
        "Extract common code from process_user_data and process_admin_data"
    ]


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
    proposals = run_pipeline(
        bogus, engine=UnificationRefactorEngine(), verbose=True, progress="auto"
    )
    assert proposals == []
