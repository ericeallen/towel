"""Integration tests for cross-file refactoring scenarios.

These tests verify end-to-end cross-file refactoring workflows beyond just
observational equivalence, focusing on:
- Proposal structure and metadata correctness
- Import generation for extracted functions
- Multi-file coordination
- Edge cases and error handling
"""

from __future__ import annotations

import contextlib
import io
import shutil
from pathlib import Path
from typing import List

import pytest

from towel.unification.models import RefactoringProposal
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.pipeline import run_pipeline

PROJECT_ROOT = Path(__file__).parent.parent
CROSSFILE_DIR = PROJECT_ROOT / "test_examples_crossfile"

# The one proposal analysis of the simple_crossfile project produces.
REUSE_ADMIN_EMAIL = (
    "Reuse validate_admin_email (admin_service.py) for duplicated code in validate_user_email"
)


def _participating_files(proposal) -> set[str]:
    """Files a proposal touches, counting a reused definition's module.

    When one duplicate is the whole body of an existing function, that
    function is left in place and the other file calls it, so the proposal
    spans both files even though only one receives an edit.
    """
    files = {r.file_path or proposal.file_path for r in proposal.replacements}
    if proposal.reused_function is not None:
        files.add(proposal.reused_function.file_path)
    return files


def get_crossfile_project_files(project_name: str) -> List[str]:
    """All Python files of a cross-file fixture project, which must exist.

    A missing or empty fixture is a broken test tree, not a reason to skip:
    renaming a fixture directory must fail every test that depends on it.
    """
    project_dir = CROSSFILE_DIR / project_name
    assert project_dir.is_dir(), f"cross-file fixture missing: {project_dir}"
    # Include files in nested directories to support complex project layouts
    files = sorted(str(f) for f in project_dir.rglob("*.py") if f.is_file())
    assert files, f"cross-file fixture has no Python files: {project_dir}"
    return files


def get_example3_files() -> List[str]:
    """The two example3 modules under ``test_examples``, which must exist."""
    files = [
        str(PROJECT_ROOT / "test_examples" / "example3_file1.py"),
        str(PROJECT_ROOT / "test_examples" / "example3_file2.py"),
    ]
    missing = [path for path in files if not Path(path).is_file()]
    assert not missing, f"example3 fixtures missing: {missing}"
    return files


class TestCrossFileProposalStructure:
    """Test that cross-file proposals have correct structure and metadata."""

    def test_simple_crossfile_produces_proposals(self):
        """Simple cross-file project should produce refactoring proposals."""
        files = get_crossfile_project_files("simple_crossfile")
        assert len(files) == 2, "simple_crossfile should have 2 files"

        engine = UnificationRefactorEngine()
        proposals = engine.analyze_files(files)

        # The one duplicate is the whole body of validate_admin_email, so the
        # user module is rewritten to call it.
        assert [p.description for p in proposals] == [REUSE_ADMIN_EMAIL]

    def test_crossfile_proposal_has_replacements_in_multiple_files(self):
        """Cross-file proposals should have replacements spanning multiple files."""
        files = get_crossfile_project_files("simple_crossfile")
        engine = UnificationRefactorEngine()
        proposals = engine.analyze_files(files)

        # Every proposal spans both modules: the reused definition lives in one
        # and the rewritten site in the other.
        assert [sorted(Path(f).name for f in _participating_files(p)) for p in proposals] == [
            ["admin_service.py", "user_service.py"]
        ]

    def test_crossfile_proposal_has_valid_file_paths(self):
        """All file paths in proposals should point to actual input files."""
        files = get_crossfile_project_files("simple_crossfile")
        engine = UnificationRefactorEngine()
        proposals = engine.analyze_files(files)

        file_set = set(files)
        for proposal in proposals:
            for replacement in proposal.replacements:
                assert (
                    replacement.file_path in file_set
                ), f"Replacement file {replacement.file_path} not in input files"

    def test_crossfile_proposal_has_consistent_parameters(self):
        """All replacements in a proposal should reference same parameter count."""
        files = get_crossfile_project_files("simple_crossfile")
        engine = UnificationRefactorEngine()
        proposals = engine.analyze_files(files)

        for proposal in proposals:
            if proposal.replacements:
                # All replacements should call with same number of args
                # (This is implicitly tested by checking parameters_count is consistent)
                assert proposal.parameters_count >= 0
                assert proposal.parameters_count <= 10  # Sanity check


class TestCrossFileImportGeneration:
    """Test that cross-file refactoring generates correct imports."""

    def test_crossfile_proposal_includes_file_path(self):
        """Cross-file proposals should include file_path for extraction location."""
        files = get_crossfile_project_files("simple_crossfile")
        engine = UnificationRefactorEngine()
        proposals = engine.analyze_files(files)

        # Every proposal is hosted in one of the analyzed files.
        assert proposals
        for proposal in proposals:
            assert proposal.file_path in set(files)

    def test_crossfile_extracted_function_name_is_valid(self):
        """Extracted function names should be valid Python identifiers."""
        files = get_crossfile_project_files("simple_crossfile")
        engine = UnificationRefactorEngine()
        proposals = engine.analyze_files(files)

        for proposal in proposals:
            func_name = proposal.extracted_function.name
            # Should be valid Python identifier
            assert func_name.isidentifier(), f"Invalid function name: {func_name}"
            # Should not be a Python keyword
            import keyword

            assert not keyword.iskeyword(func_name)


class TestCrossFileWithPipeline:
    """Test cross-file refactoring using the pipeline API."""

    def test_pipeline_handles_crossfile_correctly(self):
        """run_pipeline should work with cross-file scenarios."""
        files = get_crossfile_project_files("simple_crossfile")
        proposals = run_pipeline(files, engine=UnificationRefactorEngine(), progress="none")

        assert [p.description for p in proposals] == [REUSE_ADMIN_EMAIL]

    def test_pipeline_crossfile_matches_engine(self):
        """Pipeline and engine should produce same results for cross-file."""
        files = get_crossfile_project_files("simple_crossfile")

        engine = UnificationRefactorEngine()
        engine_proposals = engine.analyze_files(files)
        pipeline_proposals = run_pipeline(files, engine=engine, progress="none")

        def signatures(proposals: list[RefactoringProposal]) -> list[tuple[object, ...]]:
            return sorted(
                (
                    p.description,
                    p.parameters_count,
                    len(p.replacements),
                    p.insert_into_class,
                    p.insert_into_function,
                    p.method_kind,
                )
                for p in proposals
            )

        assert [p.description for p in pipeline_proposals] == [REUSE_ADMIN_EMAIL]
        assert signatures(engine_proposals) == signatures(pipeline_proposals)


class TestNestedStructureCrossFile:
    """Test cross-file refactoring with nested package structures."""

    def test_nested_structure_project_exists(self):
        """Nested structure test project should exist and have files."""
        files = get_crossfile_project_files("nested_structure")
        # The fixture nests packages, so files live below the project root.
        assert any(Path(f).parent != CROSSFILE_DIR / "nested_structure" for f in files)

    def test_nested_structure_analysis(self):
        """Nested package structure should be analyzable."""
        files = get_crossfile_project_files("nested_structure")

        engine = UnificationRefactorEngine()
        proposals = engine.analyze_files(files)

        # Should complete without errors
        assert isinstance(proposals, list)


class TestMultiLevelCrossFile:
    """Test cross-file refactoring with multi-level duplications."""

    def test_multi_level_project_exists(self):
        """Multi-level test project should exist."""
        files = get_crossfile_project_files("multi_level")
        assert len(files) >= 2, "multi_level must hold several modules to duplicate across"

    def test_multi_level_analysis(self):
        """Multi-level duplication scenarios should be analyzable."""
        files = get_crossfile_project_files("multi_level")

        engine = UnificationRefactorEngine()
        proposals = engine.analyze_files(files)

        # Should complete without errors
        assert isinstance(proposals, list)


class TestCrossFileErrorHandling:
    """Test error handling for cross-file scenarios."""

    def test_empty_file_list(self):
        """Empty file list should return no proposals."""
        engine = UnificationRefactorEngine()
        proposals = engine.analyze_files([])
        assert proposals == []

    def test_single_file_crossfile(self):
        """Single file should work (no cross-file opportunities)."""
        files = get_crossfile_project_files("simple_crossfile")

        # Just analyze first file
        engine = UnificationRefactorEngine()
        proposals = engine.analyze_files([files[0]])

        # Should complete without errors (may or may not have proposals)
        assert isinstance(proposals, list)

    def test_nonexistent_file_gracefully_handled(self):
        """Nonexistent files should be skipped gracefully."""
        files = [str(PROJECT_ROOT / "this_file_definitely_does_not_exist.py")]

        engine = UnificationRefactorEngine()
        proposals = engine.analyze_files(files)

        # Should return empty list without crashing
        assert proposals == []

    def test_mixed_valid_invalid_files(self):
        """Mix of valid and invalid files should process valid ones."""
        valid_files = get_crossfile_project_files("simple_crossfile")

        invalid = str(PROJECT_ROOT / "nonexistent.py")
        mixed = valid_files + [invalid]

        engine = UnificationRefactorEngine()
        proposals = engine.analyze_files(mixed)

        # Should process valid files
        assert isinstance(proposals, list)


class TestCrossFileProposalApplication:
    """Test applying cross-file refactoring proposals (non-destructive checks)."""

    def test_crossfile_replacement_has_valid_ranges(self):
        """Replacement ranges should be within file bounds."""
        files = get_crossfile_project_files("simple_crossfile")

        engine = UnificationRefactorEngine()
        proposals = engine.analyze_files(files)

        for proposal in proposals:
            for replacement in proposal.replacements:
                # Line range is a tuple (start_line, end_line)
                start_line, end_line = replacement.line_range

                # Line numbers should be positive
                assert start_line >= 1
                assert end_line >= start_line

                # Verify against actual file content
                if replacement.file_path:
                    file_path = Path(replacement.file_path)
                    if file_path.exists():
                        lines = file_path.read_text().splitlines()
                        # end_line should not exceed file length
                        assert end_line <= len(lines)

    def test_crossfile_replacement_node_is_valid(self):
        """Replacement AST nodes should be valid."""
        import ast as python_ast

        files = get_crossfile_project_files("simple_crossfile")

        engine = UnificationRefactorEngine()
        proposals = engine.analyze_files(files)

        for proposal in proposals:
            for replacement in proposal.replacements:
                # Replacement.node should be a valid AST node
                assert isinstance(replacement.node, python_ast.AST)

                # Try to unparse the node to verify it's valid
                try:
                    code = python_ast.unparse(replacement.node)
                    # Should be able to parse the unparsed code
                    python_ast.parse(code)
                except Exception as e:
                    pytest.fail(
                        f"Replacement node cannot be unparsed/reparsed: {e}\n"
                        f"Node type: {type(replacement.node)}"
                    )


class TestCrossFilePerformance:
    """Test performance characteristics of cross-file analysis."""

    def test_crossfile_analysis_evaluates_each_block_pair_once(self):
        """Cross-file analysis does bounded work: no block pair is evaluated twice.

        A wall-clock bound would measure the machine; the property behind it
        is that the pair enumeration is finite and free of repeats, which
        holds on any machine.
        """
        from unittest.mock import patch

        files = get_crossfile_project_files("simple_crossfile")
        engine = UnificationRefactorEngine()
        evaluated: list[tuple[str, tuple[int, int], str, tuple[int, int]]] = []
        original = UnificationRefactorEngine.process_block_pairs

        def record(self, block_pairs, *args, **kwargs):
            evaluated.extend(
                (p.file_path, p.block1_range, p.file_path2, p.block2_range) for p in block_pairs
            )
            return original(self, block_pairs, *args, **kwargs)

        with patch.object(UnificationRefactorEngine, "process_block_pairs", record):
            proposals = engine.analyze_files(files, progress="none")

        assert [p.description for p in proposals] == [REUSE_ADMIN_EMAIL]
        assert evaluated, "the project has duplicate blocks to pair"
        assert len(evaluated) == len(set(evaluated)), "a block pair was evaluated twice"

    def test_crossfile_with_progress_disabled(self):
        """Cross-file analysis with progress='none' should work."""
        files = get_crossfile_project_files("simple_crossfile")

        proposals = run_pipeline(files, engine=UnificationRefactorEngine(), progress="none")
        assert [p.description for p in proposals] == [REUSE_ADMIN_EMAIL]


EXAMPLE3_REUSE = (
    "Reuse calculate_discount_for_regular_customer (example3_file1.py) for duplicated code "
    "in calculate_discount_for_premium_customer"
)


class TestExampleThreeCrossFile:
    """The example3 pair: a duplicate that lives in two modules.

    Single-file mode finds nothing in either file (their goldens equal their
    inputs); analyzed together, the premium module's discount function is the
    whole body of the regular module's, so it is rewritten to import and call
    it. The directory run is what generates the import line.
    """

    def test_example3_finds_cross_file_duplication(self):
        file1, file2 = get_example3_files()

        engine = UnificationRefactorEngine()
        proposals = engine.analyze_files([file1, file2])

        assert [p.description for p in proposals] == [EXAMPLE3_REUSE]
        assert _participating_files(proposals[0]) == {file1, file2}
        assert engine.analyze_file(file1) == [] and engine.analyze_file(file2) == []

    def test_example3_directory_run_imports_the_reused_function(self, tmp_path: Path):
        source = tmp_path / "example3"
        source.mkdir()
        for path in get_example3_files():
            shutil.copy(path, source / Path(path).name)
        out = tmp_path / "out"

        with contextlib.redirect_stdout(io.StringIO()):
            results, termination = UnificationRefactorEngine().refactor_directory_to_fixed_point(
                str(source), str(out), progress="none"
            )

        assert termination == "fixed_point"
        # Both modules take part in the one proposal: the regular module hosts the
        # reused definition and the premium module is rewritten to call it.
        assert results == {
            str(out / "example3_file1.py"): (1, [EXAMPLE3_REUSE]),
            str(out / "example3_file2.py"): (1, [EXAMPLE3_REUSE]),
        }
        assert (out / "example3_file1.py").read_bytes() == (
            source / "example3_file1.py"
        ).read_bytes()
        premium = (out / "example3_file2.py").read_text()
        assert "from example3_file1 import calculate_discount_for_regular_customer\n" in premium
        assert (
            "def calculate_discount_for_premium_customer(price, customer):\n"
            '    """Calculate discount for premium customer."""\n'
            "    # Calculate discount (DUPLICATE across files!)\n"
            "    return calculate_discount_for_regular_customer(price, customer)\n"
        ) in premium
