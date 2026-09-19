"""Tests for individual pipeline phase functions and edge cases."""

from __future__ import annotations

import ast


from tests.test_helpers import PROJECT_ROOT, example_paths
from towel.unification.pipeline import (
    parse_modules,
    analyze_scopes,
    collect_classes,
    collect_functions,
    pair_blocks,
    unify_blocks,
    filter_overlaps,
    run_pipeline,
)
from towel.unification.models import RawModule, RefactoringProposal
from towel.unification.refactor_engine import UnificationRefactorEngine

# The one proposal single-file analysis of example1_simple leaves after
# overlap filtering, and the one example2_classes adds to it.
USER_ADMIN = "Extract common code from process_user_data and process_admin_data"
ADMIN_GUEST = "Extract common code from process_admin_data and process_guest_data"
PROCESS_PROCESS = "Extract common code from process and process"


def descriptions(proposals: list[RefactoringProposal]) -> list[str]:
    return [p.description for p in proposals]


class TestParseModules:
    """Test parse_modules phase in isolation."""

    def test_parse_modules_single_file(self):
        """parse_modules should parse a single valid file."""
        files = example_paths(["example1_simple.py"])
        mods = parse_modules(files)

        assert len(mods) == 1
        assert isinstance(mods[0], RawModule)
        assert mods[0].file_path == files[0]
        assert isinstance(mods[0].tree, ast.Module)
        with open(files[0], encoding="utf-8") as handle:
            assert mods[0].source == handle.read()

    def test_parse_modules_multiple_files(self):
        """parse_modules should parse multiple valid files."""
        files = example_paths(["example1_simple.py", "example2_classes.py"])
        mods = parse_modules(files)

        assert len(mods) == 2
        assert all(isinstance(m, RawModule) for m in mods)
        assert mods[0].file_path == files[0]
        assert mods[1].file_path == files[1]

    def test_parse_modules_skips_invalid_file(self):
        """parse_modules should skip files that cannot be parsed."""
        valid = example_paths(["example1_simple.py"])
        invalid = [str(PROJECT_ROOT / "nonexistent_file.py")]
        mods = parse_modules(valid + invalid)

        # Should only parse the valid file
        assert len(mods) == 1
        assert mods[0].file_path == valid[0]

    def test_parse_modules_empty_list(self):
        """parse_modules should handle empty input gracefully."""
        mods = parse_modules([])
        assert mods == []


class TestAnalyzeScopes:
    """Test analyze_scopes phase in isolation."""

    def test_analyze_scopes_attaches_analyzer(self):
        """analyze_scopes should attach scope_analyzer to each module."""
        files = example_paths(["example1_simple.py"])
        mods = analyze_scopes(parse_modules(files))

        assert mods[0].scope_analyzer is not None
        assert mods[0].root_scope is not None

    def test_analyze_scopes_multiple_modules(self):
        """analyze_scopes should analyze all modules."""
        files = example_paths(["example1_simple.py", "example2_classes.py"])
        mods = analyze_scopes(parse_modules(files))

        for mod in mods:
            assert mod.scope_analyzer is not None
            assert mod.root_scope is not None

    def test_analyze_scopes_empty_list(self):
        """analyze_scopes on no modules is a no-op."""
        assert analyze_scopes([]) == []


class TestCollectClasses:
    """Test collect_classes phase in isolation."""

    def test_collect_classes_finds_simple_class(self):
        """collect_classes should find classes without bases."""
        files = example_paths(["example2_classes.py"])
        mods = parse_modules(files)
        classes = collect_classes(mods)

        assert {c.name for c in classes} == {
            "EmailProcessor",
            "PushNotificationProcessor",
            "SMSProcessor",
        }

    def test_collect_classes_handles_inheritance(self):
        """collect_classes should extract base class names."""
        # Create a temporary module with inheritance
        code = """
class Base:
    pass

class Child(Base):
    pass

class MultiChild(Base, object):
    pass
"""
        temp_path = PROJECT_ROOT / "mytmp" / "test_inherit.py"
        temp_path.parent.mkdir(exist_ok=True)
        temp_path.write_text(code)

        try:
            mods = parse_modules([str(temp_path)])
            classes = collect_classes(mods)

            # Find Child class
            child = next(c for c in classes if c.name == "Child")
            assert "Base" in child.bases

            # Find MultiChild
            multi = next(c for c in classes if c.name == "MultiChild")
            assert "Base" in multi.bases
            assert "object" in multi.bases
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def test_collect_classes_handles_attribute_bases(self):
        """collect_classes should handle module.Class base syntax."""
        code = """
import abc

class MyClass(abc.ABC):
    pass
"""
        temp_path = PROJECT_ROOT / "mytmp" / "test_attr_base.py"
        temp_path.parent.mkdir(exist_ok=True)
        temp_path.write_text(code)

        try:
            mods = parse_modules([str(temp_path)])
            classes = collect_classes(mods)

            my_class = next(c for c in classes if c.name == "MyClass")
            assert "abc.ABC" in my_class.bases
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def test_collect_classes_nested_classes(self):
        """collect_classes should handle nested class definitions."""
        code = """
class Outer:
    class Inner:
        pass
"""
        temp_path = PROJECT_ROOT / "mytmp" / "test_nested.py"
        temp_path.parent.mkdir(exist_ok=True)
        temp_path.write_text(code)

        try:
            mods = parse_modules([str(temp_path)])
            classes = collect_classes(mods)

            # Should find both Outer and Inner
            names = [c.name for c in classes]
            assert "Outer" in names
            assert "Inner" in names

            # Inner should have qualified name
            inner = next(c for c in classes if c.name == "Inner")
            assert inner.qualname == "Outer.Inner"
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def test_collect_classes_empty_modules(self):
        """collect_classes should handle modules without classes."""
        code = "x = 42\n"
        temp_path = PROJECT_ROOT / "mytmp" / "test_no_class.py"
        temp_path.parent.mkdir(exist_ok=True)
        temp_path.write_text(code)

        try:
            mods = parse_modules([str(temp_path)])
            classes = collect_classes(mods)
            assert classes == []
        finally:
            if temp_path.exists():
                temp_path.unlink()


class TestCollectFunctions:
    """Test collect_functions phase in isolation."""

    def test_collect_functions_finds_functions(self):
        """collect_functions should find function definitions."""
        files = example_paths(["example1_simple.py"])
        mods = analyze_scopes(parse_modules(files))
        funcs = collect_functions(mods)

        assert {f.node.name for f in funcs} == {
            "process_admin_data",
            "process_guest_data",
            "process_user_data",
        }

    def test_collect_functions_multiple_modules(self):
        """collect_functions should collect from all modules."""
        files = example_paths(["example1_simple.py", "example2_classes.py"])
        mods = analyze_scopes(parse_modules(files))
        funcs = collect_functions(mods)

        # Should have functions from both files
        file_paths = {f.file_path for f in funcs}
        assert len(file_paths) == 2


class TestPairBlocks:
    """Test pair_blocks phase in isolation."""

    def test_pair_blocks_without_progress(self):
        """pair_blocks should work with progress disabled."""
        files = example_paths(["example1_simple.py"])
        mods = analyze_scopes(parse_modules(files))
        funcs = collect_functions(mods)
        eng = UnificationRefactorEngine()

        pairs = pair_blocks(eng, funcs, progress="none")
        # The three functions of example1_simple pair into 48 candidate block
        # pairs, all within that one file.
        assert len(pairs) == 48
        assert {(p.file_path, p.file_path2) for p in pairs} == {(files[0], files[0])}

    def test_pair_blocks_with_auto_progress(self):
        """pair_blocks should work with progress='auto'."""
        files = example_paths(["example1_simple.py"])
        mods = analyze_scopes(parse_modules(files))
        funcs = collect_functions(mods)
        eng = UnificationRefactorEngine()

        # Should not raise even if tqdm is unavailable
        pairs = pair_blocks(eng, funcs, progress="auto")
        assert len(pairs) == 48

    def test_pair_blocks_with_tqdm_progress(self):
        """pair_blocks should work with progress='tqdm'."""
        files = example_paths(["example1_simple.py"])
        mods = analyze_scopes(parse_modules(files))
        funcs = collect_functions(mods)
        eng = UnificationRefactorEngine()

        # Should gracefully fall back if tqdm unavailable
        pairs = pair_blocks(eng, funcs, progress="tqdm")
        assert len(pairs) == 48


class TestUnifyBlocks:
    """Test unify_blocks phase in isolation."""

    def test_unify_blocks_produces_proposals(self):
        """unify_blocks should produce refactoring proposals."""
        files = example_paths(["example1_simple.py"])
        mods = analyze_scopes(parse_modules(files))
        funcs = collect_functions(mods)
        classes = collect_classes(mods)
        eng = UnificationRefactorEngine()
        pairs = pair_blocks(eng, funcs, progress="none")

        proposals = unify_blocks(eng, pairs, funcs, classes, progress="none")
        # Before overlap filtering, each pair of the three functions unifies
        # into six overlapping proposals, in pairing order; a proposal a later
        # pair repeats (the same helper over the same clustered sites) is
        # kept once, under the pair that found it first. The user/guest pairs
        # repeat the user/admin proposals; three admin/guest proposals order
        # the helper's parameters differently and are their own.
        assert descriptions(proposals) == [USER_ADMIN] * 6 + [ADMIN_GUEST] * 3


class TestFilterOverlaps:
    """Test filter_overlaps phase in isolation."""

    def test_filter_overlaps_maintains_list_type(self):
        """filter_overlaps should return a list."""
        files = example_paths(["example1_simple.py"])
        mods = analyze_scopes(parse_modules(files))
        funcs = collect_functions(mods)
        classes = collect_classes(mods)
        eng = UnificationRefactorEngine()
        pairs = pair_blocks(eng, funcs, progress="none")
        proposals = unify_blocks(eng, pairs, funcs, classes, progress="none")

        filtered = filter_overlaps(proposals)
        # The nine distinct overlapping proposals collapse to the one the engine reports.
        assert len(proposals) == 9
        assert descriptions(filtered) == [USER_ADMIN]


class TestCacheInvalidation:
    """Test run_pipeline cache invalidation logic."""

    def test_run_pipeline_with_invalidate_paths(self):
        """run_pipeline should invalidate specified paths from cache."""
        files = example_paths(["example1_simple.py"])

        # Run once to populate cache
        proposals1 = run_pipeline(files, engine=UnificationRefactorEngine(), progress="none")

        # Run again with invalidation
        proposals2 = run_pipeline(
            files, engine=UnificationRefactorEngine(), progress="none", invalidate_paths=files
        )

        # Should produce same results (cache invalidation shouldn't affect correctness)
        assert descriptions(proposals1) == descriptions(proposals2) == [USER_ADMIN]

    def test_run_pipeline_cache_reuse(self):
        """run_pipeline should reuse cached results for unchanged files."""
        files = example_paths(["example1_simple.py", "example2_classes.py"])

        # Run once
        run_pipeline(files, engine=UnificationRefactorEngine(), progress="none")

        # Run again - should use cache
        proposals = run_pipeline(files, engine=UnificationRefactorEngine(), progress="none")
        assert descriptions(proposals) == [USER_ADMIN, PROCESS_PROCESS]


class TestProgressBars:
    """Test progress bar display code paths."""

    def test_run_pipeline_with_auto_progress(self):
        """run_pipeline should handle progress='auto'."""
        files = example_paths(["example1_simple.py"])
        proposals = run_pipeline(files, engine=UnificationRefactorEngine(), progress="auto")
        assert descriptions(proposals) == [USER_ADMIN]

    def test_run_pipeline_with_tqdm_progress(self):
        """run_pipeline should handle progress='tqdm' (may fall back)."""
        files = example_paths(["example1_simple.py"])
        proposals = run_pipeline(files, engine=UnificationRefactorEngine(), progress="tqdm")
        assert descriptions(proposals) == [USER_ADMIN]

    def test_run_pipeline_with_none_progress(self):
        """run_pipeline should handle progress='none'."""
        files = example_paths(["example1_simple.py"])
        proposals = run_pipeline(files, engine=UnificationRefactorEngine(), progress="none")
        assert descriptions(proposals) == [USER_ADMIN]
