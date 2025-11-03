"""
Tests for overlap filtering functionality.

The overlap filtering prevents applying multiple overlapping refactoring proposals
which would break the code by modifying the same lines multiple times.
"""

import unittest
import ast
from towel.unification.refactor_engine import (
    RefactoringProposal,
    get_affected_lines,
    filter_overlapping_proposals,
)


class TestGetAffectedLines(unittest.TestCase):
    """Tests for get_affected_lines function."""

    def test_single_replacement_same_file(self):
        """Test affected lines for a single replacement in the same file."""
        # Create a minimal proposal
        func = ast.FunctionDef(
            name="extracted_func",
            args=ast.arguments(args=[], posonlyargs=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
            body=[ast.Pass()],
            decorator_list=[],
        )

        proposal = RefactoringProposal(
            file_path="test.py",
            extracted_function=func,
            replacements=[((10, 15), ast.Pass())],  # Lines 10-15 in test.py
            description="Test proposal",
            parameters_count=0,
        )

        affected = get_affected_lines(proposal)

        # Should include lines 10, 11, 12, 13, 14, 15
        expected = {("test.py", i) for i in range(10, 16)}
        self.assertEqual(affected, expected)

    def test_single_replacement_cross_file(self):
        """Test affected lines for a cross-file replacement."""
        func = ast.FunctionDef(
            name="extracted_func",
            args=ast.arguments(args=[], posonlyargs=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
            body=[ast.Pass()],
            decorator_list=[],
        )

        proposal = RefactoringProposal(
            file_path="file1.py",
            extracted_function=func,
            replacements=[((10, 15), ast.Pass(), "file2.py")],  # Lines 10-15 in file2.py
            description="Test proposal",
            parameters_count=0,
        )

        affected = get_affected_lines(proposal)

        # Should include lines 10-15 in file2.py, not file1.py
        expected = {("file2.py", i) for i in range(10, 16)}
        self.assertEqual(affected, expected)

    def test_multiple_replacements_same_file(self):
        """Test affected lines for multiple replacements in the same file."""
        func = ast.FunctionDef(
            name="extracted_func",
            args=ast.arguments(args=[], posonlyargs=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
            body=[ast.Pass()],
            decorator_list=[],
        )

        proposal = RefactoringProposal(
            file_path="test.py",
            extracted_function=func,
            replacements=[
                ((10, 12), ast.Pass()),  # Lines 10-12
                ((20, 22), ast.Pass()),  # Lines 20-22
            ],
            description="Test proposal",
            parameters_count=0,
        )

        affected = get_affected_lines(proposal)

        # Should include both ranges
        expected = {("test.py", i) for i in range(10, 13)} | {("test.py", i) for i in range(20, 23)}
        self.assertEqual(affected, expected)

    def test_multiple_replacements_different_files(self):
        """Test affected lines for replacements across multiple files."""
        func = ast.FunctionDef(
            name="extracted_func",
            args=ast.arguments(args=[], posonlyargs=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
            body=[ast.Pass()],
            decorator_list=[],
        )

        proposal = RefactoringProposal(
            file_path="file1.py",
            extracted_function=func,
            replacements=[
                ((10, 12), ast.Pass()),  # Lines 10-12 in file1.py
                ((20, 22), ast.Pass(), "file2.py"),  # Lines 20-22 in file2.py
            ],
            description="Test proposal",
            parameters_count=0,
        )

        affected = get_affected_lines(proposal)

        # Should include both files
        expected = {("file1.py", i) for i in range(10, 13)} | {
            ("file2.py", i) for i in range(20, 23)
        }
        self.assertEqual(affected, expected)

    def test_single_line_replacement(self):
        """Test affected lines for a single-line replacement."""
        func = ast.FunctionDef(
            name="extracted_func",
            args=ast.arguments(args=[], posonlyargs=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
            body=[ast.Pass()],
            decorator_list=[],
        )

        proposal = RefactoringProposal(
            file_path="test.py",
            extracted_function=func,
            replacements=[((10, 10), ast.Pass())],  # Single line 10
            description="Test proposal",
            parameters_count=0,
        )

        affected = get_affected_lines(proposal)

        # Should include only line 10
        expected = {("test.py", 10)}
        self.assertEqual(affected, expected)


class TestFilterOverlappingProposals(unittest.TestCase):
    """Tests for filter_overlapping_proposals function."""

    def _create_proposal(self, file_path, line_ranges, description="Test"):
        """Helper to create a proposal with given line ranges."""
        func = ast.FunctionDef(
            name="extracted_func",
            args=ast.arguments(args=[], posonlyargs=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
            body=[ast.Pass()],
            decorator_list=[],
        )

        replacements = [((start, end), ast.Pass()) for start, end in line_ranges]

        return RefactoringProposal(
            file_path=file_path,
            extracted_function=func,
            replacements=replacements,
            description=description,
            parameters_count=0,
        )

    def test_empty_proposals(self):
        """Test filtering empty list returns empty list."""
        result = filter_overlapping_proposals([])
        self.assertEqual(result, [])

    def test_single_proposal(self):
        """Test filtering single proposal returns that proposal."""
        proposals = [self._create_proposal("test.py", [(10, 15)])]

        result = filter_overlapping_proposals(proposals)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], proposals[0])

    def test_non_overlapping_proposals(self):
        """Test that non-overlapping proposals are all kept."""
        proposals = [
            self._create_proposal("test.py", [(10, 15)], "First"),
            self._create_proposal("test.py", [(20, 25)], "Second"),
            self._create_proposal("test.py", [(30, 35)], "Third"),
        ]

        result = filter_overlapping_proposals(proposals)

        # All should be kept since none overlap
        self.assertEqual(len(result), 3)
        # Check that all descriptions are present
        result_descriptions = {p.description for p in result}
        expected_descriptions = {"First", "Second", "Third"}
        self.assertEqual(result_descriptions, expected_descriptions)

    def test_completely_overlapping_proposals(self):
        """Test that when proposals completely overlap, largest is kept."""
        proposals = [
            self._create_proposal("test.py", [(10, 16)], "Full (7 lines)"),  # Lines 10-16 (7 lines)
            self._create_proposal(
                "test.py", [(10, 15)], "Partial1 (6 lines)"
            ),  # Lines 10-15 (6 lines)
            self._create_proposal(
                "test.py", [(11, 16)], "Partial2 (6 lines)"
            ),  # Lines 11-16 (6 lines)
        ]

        result = filter_overlapping_proposals(proposals)

        # Only the largest (first one) should be kept
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].description, "Full (7 lines)")

    def test_partially_overlapping_proposals(self):
        """Test that partially overlapping proposals are filtered correctly."""
        proposals = [
            self._create_proposal("test.py", [(10, 20)], "Large (11 lines)"),  # Lines 10-20
            self._create_proposal(
                "test.py", [(15, 25)], "Overlap (11 lines)"
            ),  # Lines 15-25 (overlaps)
            self._create_proposal(
                "test.py", [(30, 40)], "Separate (11 lines)"
            ),  # Lines 30-40 (no overlap)
        ]

        result = filter_overlapping_proposals(proposals)

        # First and third should be kept (no overlap), second rejected (overlaps with first)
        self.assertEqual(len(result), 2)
        descriptions = {p.description for p in result}
        self.assertIn("Large (11 lines)", descriptions)
        self.assertIn("Separate (11 lines)", descriptions)
        self.assertNotIn("Overlap (11 lines)", descriptions)

    def test_size_priority(self):
        """Test that larger proposals are prioritized over smaller ones."""
        proposals = [
            self._create_proposal(
                "test.py", [(10, 12)], "Small (3 lines)"
            ),  # Lines 10-12 (3 lines)
            self._create_proposal(
                "test.py", [(10, 20)], "Large (11 lines)"
            ),  # Lines 10-20 (11 lines, overlaps)
        ]

        result = filter_overlapping_proposals(proposals)

        # Larger one should be selected
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].description, "Large (11 lines)")

    def test_different_files_no_overlap(self):
        """Test that proposals in different files don't overlap."""
        proposals = [
            self._create_proposal("file1.py", [(10, 20)], "File1"),
            self._create_proposal("file2.py", [(10, 20)], "File2"),
        ]

        result = filter_overlapping_proposals(proposals)

        # Both should be kept (different files)
        self.assertEqual(len(result), 2)

    def test_same_lines_different_files(self):
        """Test that same line numbers in different files are treated as separate."""
        proposals = [
            self._create_proposal("file1.py", [(10, 20)], "File1 lines 10-20"),
            self._create_proposal("file2.py", [(10, 20)], "File2 lines 10-20"),
            self._create_proposal("file1.py", [(15, 25)], "File1 lines 15-25 (overlaps)"),
        ]

        result = filter_overlapping_proposals(proposals)

        # First two should be kept (different files), third rejected (overlaps with first)
        self.assertEqual(len(result), 2)
        descriptions = {p.description for p in result}
        self.assertIn("File1 lines 10-20", descriptions)
        self.assertIn("File2 lines 10-20", descriptions)

    def test_complex_overlap_scenario(self):
        """Test complex scenario with multiple overlapping and non-overlapping proposals."""
        proposals = [
            self._create_proposal("test.py", [(10, 20)], "Block1 (11 lines)"),  # Lines 10-20
            self._create_proposal(
                "test.py", [(10, 15)], "Block1a (6 lines)"
            ),  # Overlaps with Block1
            self._create_proposal(
                "test.py", [(16, 20)], "Block1b (5 lines)"
            ),  # Overlaps with Block1
            self._create_proposal("test.py", [(30, 40)], "Block2 (11 lines)"),  # No overlap
            self._create_proposal(
                "test.py", [(35, 45)], "Block2a (11 lines)"
            ),  # Overlaps with Block2
            self._create_proposal("test.py", [(50, 55)], "Block3 (6 lines)"),  # No overlap
        ]

        result = filter_overlapping_proposals(proposals)

        # Should keep Block1 (largest of first group), Block2 (first of tied group), Block3 (no overlap)
        self.assertEqual(len(result), 3)
        descriptions = {p.description for p in result}
        self.assertIn("Block1 (11 lines)", descriptions)
        self.assertIn("Block2 (11 lines)", descriptions)
        self.assertIn("Block3 (6 lines)", descriptions)

    def test_multiple_replacements_per_proposal(self):
        """Test proposals with multiple replacements (e.g., two duplicate blocks)."""
        func = ast.FunctionDef(
            name="extracted_func",
            args=ast.arguments(args=[], posonlyargs=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
            body=[ast.Pass()],
            decorator_list=[],
        )

        # Proposal that refactors lines 10-15 and 20-25 (total 12 lines)
        proposal1 = RefactoringProposal(
            file_path="test.py",
            extracted_function=func,
            replacements=[((10, 15), ast.Pass()), ((20, 25), ast.Pass())],
            description="Two blocks (12 lines total)",
            parameters_count=0,
        )

        # Proposal that overlaps with first block (lines 10-12, only 3 lines)
        proposal2 = RefactoringProposal(
            file_path="test.py",
            extracted_function=func,
            replacements=[((10, 12), ast.Pass())],
            description="Overlaps first (3 lines)",
            parameters_count=0,
        )

        # Proposal that overlaps with second block (lines 20-22, only 3 lines)
        proposal3 = RefactoringProposal(
            file_path="test.py",
            extracted_function=func,
            replacements=[((20, 22), ast.Pass())],
            description="Overlaps second (3 lines)",
            parameters_count=0,
        )

        proposals = [proposal1, proposal2, proposal3]
        result = filter_overlapping_proposals(proposals)

        # Only proposal1 should be kept (largest and others overlap with it)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].description, "Two blocks (12 lines total)")

    def test_adjacent_non_overlapping_proposals(self):
        """Test that adjacent but non-overlapping proposals are both kept."""
        proposals = [
            self._create_proposal("test.py", [(10, 15)], "First"),  # Lines 10-15
            self._create_proposal(
                "test.py", [(16, 20)], "Second"
            ),  # Lines 16-20 (adjacent, not overlapping)
        ]

        result = filter_overlapping_proposals(proposals)

        # Both should be kept (adjacent but not overlapping)
        self.assertEqual(len(result), 2)


if __name__ == "__main__":
    unittest.main()
