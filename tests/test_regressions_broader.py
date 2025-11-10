"""
Broader regression tests covering:
- Overlap filtering behavior
- Cross-file proposal detection
- Trivial single-line return rejection
- Augmented assignment handling across blocks
"""

import ast
import textwrap
import unittest
from pathlib import Path

from towel.unification.refactor_engine import (
    UnificationRefactorEngine,
    RefactoringProposal,
    filter_overlapping_proposals,
)
from tests.test_helpers import temporary_test_directory


class TestOverlapFiltering(unittest.TestCase):
    def _dummy_funcdef(self, name="f"):
        return ast.parse(f"def {name}():\n    return 1\n").body[0]

    def _dummy_node(self):
        return ast.parse("pass\n").body[0]

    def test_keeps_largest_overlapping(self):
        f = self._dummy_funcdef()
        node = self._dummy_node()
        # Three proposals on the same file with overlapping ranges
        p1 = RefactoringProposal(
            file_path="/tmp/file.py",
            extracted_function=f,
            replacements=[((1, 7), node)],
            description="p1",
            parameters_count=0,
        )
        p2 = RefactoringProposal(
            file_path="/tmp/file.py",
            extracted_function=f,
            replacements=[((1, 6), node)],
            description="p2",
            parameters_count=0,
        )
        p3 = RefactoringProposal(
            file_path="/tmp/file.py",
            extracted_function=f,
            replacements=[((2, 7), node)],
            description="p3",
            parameters_count=0,
        )
        kept = filter_overlapping_proposals([p2, p3, p1])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].description, "p1")

    def test_non_overlapping_across_files(self):
        f = self._dummy_funcdef()
        node = self._dummy_node()
        a = RefactoringProposal(
            file_path="/tmp/a.py",
            extracted_function=f,
            replacements=[((10, 12), node)],
            description="A",
            parameters_count=0,
        )
        b = RefactoringProposal(
            file_path="/tmp/b.py",
            extracted_function=f,
            replacements=[((10, 12), node)],
            description="B",
            parameters_count=0,
        )
        kept = filter_overlapping_proposals([a, b])
        self.assertEqual(len(kept), 2)
        self.assertCountEqual([p.description for p in kept], ["A", "B"])


class TestCrossFileAndValidation(unittest.TestCase):
    def test_cross_file_proposal_detected(self):
        eng = UnificationRefactorEngine(max_parameters=5, min_lines=1, parameterize_constants=True)
        with temporary_test_directory() as tmp:
            file1 = Path(tmp) / "mod1.py"
            file2 = Path(tmp) / "mod2.py"
            file1.write_text(
                textwrap.dedent(
                    """
                    def fa():
                        x = 1
                        y = 2
                        return x + y
                    """
                ).strip()
            )
            file2.write_text(
                textwrap.dedent(
                    """
                    def fb():
                        x = 1
                        y = 2
                        return x + y
                    """
                ).strip()
            )
            props = eng.analyze_files([str(file1), str(file2)])
            # Should include a proposal that mentions both fa and fb
            self.assertTrue(any("fa" in p.description and "fb" in p.description for p in props))
            # And replacements should span both files
            has_both_files = any(
                len({(r.file_path or p.file_path) for r in p.replacements}) > 1 for p in props
            )
            self.assertTrue(has_both_files, "Expected replacements across both files")

    def test_trivial_single_line_return_rejected(self):
        # Two functions where only the single-line return matches; constants differ
        # With parameterize_constants=False, the larger two-line block won't unify
        # The engine should reject extracting just the trivial single-line return
        eng = UnificationRefactorEngine(max_parameters=5, min_lines=1, parameterize_constants=False)
        with temporary_test_directory() as tmp:
            path = Path(tmp) / "trivial.py"
            path.write_text(
                textwrap.dedent(
                    """
                    def a():
                        res = 1
                        return res

                    def b():
                        res = 2
                        return res
                    """
                ).strip()
            )
            props = eng.analyze_file(str(path))
            # No proposals should be returned (trivial single-line return rejected)
            self.assertEqual(len(props), 0)

    def test_augmented_assignment_reassignment_without_binding_rejected(self):
        # Reassignments (like "+=") to variables not initially bound in the block
        # should be rejected as unsafe extractions per validation rules.
        eng = UnificationRefactorEngine(max_parameters=5, min_lines=1, parameterize_constants=True)
        with temporary_test_directory() as tmp:
            path = Path(tmp) / "augassign.py"
            path.write_text(
                textwrap.dedent(
                    """
                    def a(v, total):
                        total += v
                        return total

                    def b(v, acc):
                        acc += v
                        return acc
                    """
                ).strip()
            )
            props = eng.analyze_file(str(path))
            self.assertEqual(
                len(props),
                0,
                "Unsafe augmented assignment without local binding should be rejected",
            )


if __name__ == "__main__":
    unittest.main()
