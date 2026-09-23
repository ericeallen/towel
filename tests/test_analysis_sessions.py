"""Analysis reuse must not share mutable syntax or leak across analysis owners."""

import logging
import ast
from pathlib import Path
from unittest.mock import patch

import pytest

from towel.unification.pipeline import AnalysisSession, SourceFileError, run_pipeline
from towel.unification.refactor_engine import UnificationRefactorEngine

SOURCE = "def value(x):\n    local = x + 1\n    return local\n"


def write_module(directory: Path, name: str = "module.py", source: str = SOURCE) -> str:
    path = directory / name
    path.write_text(source)
    return str(path)


def test_reuses_parse_and_scope_work_without_copying(tmp_path):
    path = write_module(tmp_path)
    session = AnalysisSession()
    first = session.analyze_module(path)
    with patch("towel.unification.pipeline.ast.parse", side_effect=AssertionError("reparsed")):
        second = session.analyze_module(path)
    # Reuse hands back the cached graph itself, so an engine's node-identity
    # caches stay valid for the file across fixed-point iterations.
    assert second is first
    assert isinstance(second.module.tree, ast.Module)
    assert second.functions[0].node is second.module.tree.body[0]
    assert second.functions[0].scope_analyzer is second.module.scope_analyzer
    assert second.functions[0].root_scope is second.module.root_scope
    analyzer = second.module.scope_analyzer
    assert second.functions[0].node in analyzer.node_scopes
    names = [node for node in ast.walk(second.module.tree) if isinstance(node, ast.Name)]
    assert any(node in analyzer.identifier_bindings for node in names)
    assert session.reusable(path)


def test_reuse_detects_a_caller_that_mutates_the_cached_graph(tmp_path, monkeypatch):
    monkeypatch.setenv("TOWEL_CHECK_AST_IMMUTABLE", "1")
    path = write_module(tmp_path)
    session = AnalysisSession()
    first = session.analyze_module(path)
    first.functions[0].node.name = "poisoned"
    with pytest.raises(RuntimeError, match="mutated the cached AST"):
        session.analyze_module(path)


def test_changed_content_invalidates_even_with_same_size_and_mtime(tmp_path):
    path = write_module(tmp_path)
    session = AnalysisSession()
    session.analyze_module(path)
    import os

    before = Path(path).stat()
    Path(path).write_text(SOURCE.replace("x + 1", "x - 1"))
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    updated = session.analyze_module(path)
    assert any(isinstance(node, ast.Sub) for node in ast.walk(updated.module.tree))
    assert session.entry_count == 1


def test_lru_eviction_and_source_budget(tmp_path):
    paths = [write_module(tmp_path, f"module{index}.py") for index in range(3)]
    session = AnalysisSession(max_entries=2, max_source_bytes=2 * len(SOURCE.encode()))
    session.analyze_module(paths[0])
    session.analyze_module(paths[1])
    session.analyze_module(paths[0])  # refresh first entry, evict second
    session.analyze_module(paths[2])
    assert session.entry_count == 2
    assert session.source_bytes == 2 * len(SOURCE.encode())
    with patch("towel.unification.pipeline.ast.parse", wraps=ast.parse) as parse:
        session.analyze_module(paths[0])
        assert parse.call_count == 0
        session.analyze_module(paths[1])
        assert parse.call_count == 1
    small = AnalysisSession(max_source_bytes=len(SOURCE.encode()) - 1)
    small.analyze_module(paths[0])
    assert small.entry_count == 0
    assert small.source_bytes == 0
    session.clear()
    assert session.entry_count == session.source_bytes == 0


def test_relative_path_resolution_is_per_call_and_preserves_spelling(tmp_path, monkeypatch):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    write_module(first)
    write_module(second, source=SOURCE.replace("value", "other"))
    session = AnalysisSession()
    monkeypatch.chdir(first)
    assert session.analyze_module("module.py").functions[0].node.name == "value"
    monkeypatch.chdir(second)
    analysis = session.analyze_module("module.py")
    assert analysis.functions[0].node.name == "other"
    assert analysis.module.file_path == "module.py"
    monkeypatch.chdir(first)
    assert session.analyze_module("module.py").functions[0].node.name == "value"
    session.invalidate([str(first / "module.py")])
    assert session.entry_count == 1


def test_session_owners_and_standalone_runs_are_independent(tmp_path):
    path = write_module(tmp_path)
    first, second = AnalysisSession(), AnalysisSession()
    first.analyze_module(path)
    second.analyze_module(path)
    first.invalidate([path])
    assert first.entry_count == 0
    assert second.entry_count == 1
    with patch("towel.unification.pipeline.ast.parse", wraps=ast.parse) as parse:
        run_pipeline([path], engine=UnificationRefactorEngine(), progress="none")
        run_pipeline([path], engine=UnificationRefactorEngine(), progress="none")
        assert parse.call_count == 2


@pytest.mark.parametrize("bad_source", ["def :", "\x00"])
def test_invalid_changed_source_never_reuses_old_analysis(tmp_path, bad_source):
    path = write_module(tmp_path)
    session = AnalysisSession()
    session.analyze_module(path)
    Path(path).write_text(bad_source)
    with pytest.raises(SourceFileError):
        session.analyze_module(path)
    assert session.entry_count == session.source_bytes == 0


def test_missing_file_is_reported_and_cached_snapshot_is_discarded(tmp_path, caplog):
    path = write_module(tmp_path)
    session = AnalysisSession()
    session.analyze_module(path)
    Path(path).rename(tmp_path / "moved.py")
    with caplog.at_level(logging.WARNING, logger="towel"):
        assert (
            run_pipeline(
                [path], engine=UnificationRefactorEngine(), session=session, progress="none"
            )
            == []
        )
    assert any("Skipping" in record.getMessage() for record in caplog.records)
    assert session.entry_count == 0


@pytest.mark.parametrize("error", [RuntimeError("defect"), OSError("analysis defect")])
def test_unexpected_analysis_errors_propagate_and_do_not_cache(tmp_path, error):
    path = write_module(tmp_path)
    session = AnalysisSession()
    with patch("towel.unification.pipeline.analyze_scopes", side_effect=error):
        with pytest.raises(type(error), match="defect"):
            run_pipeline(
                [path], engine=UnificationRefactorEngine(), session=session, progress="none"
            )
    assert session.entry_count == 0


def test_disabled_and_invalid_limits(tmp_path):
    path = write_module(tmp_path)
    session = AnalysisSession(max_entries=0)
    session.analyze_module(path)
    assert session.entry_count == 0
    with pytest.raises(ValueError):
        AnalysisSession(max_entries=-1)
    with pytest.raises(ValueError):
        AnalysisSession(max_source_bytes=-1)


def test_engines_own_distinct_sessions_and_reuse_only_their_own_work(tmp_path):
    from towel.unification.refactor_engine import UnificationRefactorEngine

    path = write_module(tmp_path)
    first = UnificationRefactorEngine()
    second = UnificationRefactorEngine()
    assert first.analysis_session is not second.analysis_session
    first.analyze_files([path], progress="none")
    assert first.analysis_session.entry_count == 1
    assert second.analysis_session.entry_count == 0
    with patch("towel.unification.pipeline.ast.parse", wraps=ast.parse) as parse:
        first.analyze_files([path], progress="none")
        assert parse.call_count == 0
        second.analyze_files([path], progress="none")
        assert parse.call_count == 1
        first.invalidate_paths([path])
        assert second.analysis_session.entry_count == 1
        second.analyze_files([path], progress="none")
        assert parse.call_count == 1
        first.analyze_files([path], progress="none")
        assert parse.call_count == 2


def test_mutating_returned_engine_proposal_does_not_change_reanalysis(tmp_path):
    from towel.unification.refactor_engine import UnificationRefactorEngine

    source = (
        "def first(x):\n    y = x + 1\n    z = y * 2\n    return z\n\n"
        "def second(x):\n    y = x + 1\n    z = y * 2\n    return z\n"
    )
    path = write_module(tmp_path, source=source)
    engine = UnificationRefactorEngine(min_lines=2)
    proposals = engine.analyze_files([path], progress="none")
    assert [p.description for p in proposals] == ["Extract common code from first and second"]
    expected = [ast.dump(proposal.extracted_function) for proposal in proposals]
    proposals[0].extracted_function.body.clear()
    proposals[0].replacements.clear()
    repeated = engine.analyze_files([path], progress="none")
    assert [ast.dump(proposal.extracted_function) for proposal in repeated] == expected
    assert repeated[0].replacements


DUPLICATED_PAIR = (
    "def alpha(items):\n"
    "    total = 0\n"
    "    for item in items:\n"
    "        total += item * 2\n"
    "    return total\n"
    "\n"
    "\n"
    "def beta(items):\n"
    "    total = 0\n"
    "    for item in items:\n"
    "        total += item * 2\n"
    "    return total\n"
)


def test_engine_invalidate_paths_drops_the_snapshot_and_reanalyzes_new_content(tmp_path):
    """After ``invalidate_paths`` a rewritten file yields proposals for the new code only."""
    from towel.unification.refactor_engine import UnificationRefactorEngine

    path = write_module(tmp_path, source=DUPLICATED_PAIR)
    engine = UnificationRefactorEngine(min_lines=3)

    first = engine.analyze_files([path], progress="none")
    assert [p.description for p in first] == ["Extract common code from alpha and beta"]
    assert engine.analysis_session.entry_count == 1
    assert engine.analysis_session.reusable(path)

    engine.invalidate_paths([path])
    assert engine.analysis_session.entry_count == 0
    assert not engine.analysis_session.reusable(path)

    Path(path).write_text(DUPLICATED_PAIR.replace("alpha", "gamma").replace("beta", "delta"))
    second = engine.analyze_files([path], progress="none")
    assert [p.description for p in second] == ["Extract common code from gamma and delta"]
    assert engine.analysis_session.reusable(path)


def test_engine_invalidate_paths_leaves_other_files_cached(tmp_path):
    from towel.unification.refactor_engine import UnificationRefactorEngine

    kept = write_module(tmp_path, "kept.py", DUPLICATED_PAIR)
    dropped = write_module(tmp_path, "dropped.py", DUPLICATED_PAIR)
    engine = UnificationRefactorEngine(min_lines=3)
    engine.analyze_files([kept, dropped], progress="none")
    assert engine.analysis_session.entry_count == 2

    engine.invalidate_paths([dropped])
    assert engine.analysis_session.entry_count == 1
    assert engine.analysis_session.reusable(kept)
    assert not engine.analysis_session.reusable(dropped)
