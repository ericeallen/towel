#!/usr/bin/env python3
"""
Comprehensive unit tests for the UnificationRefactorEngine class.

These tests isolate and test individual methods to ensure correct behavior
of block extraction, pair finding, and proposal generation.
"""

import unittest
import ast
import tempfile
import os
from src.towel.unification.refactor_engine import UnificationRefactorEngine


class TestBlockExtraction(unittest.TestCase):
    """Test _extract_code_blocks method."""

    def setUp(self):
        """Set up test fixtures."""
        self.engine = UnificationRefactorEngine(max_parameters=5, min_lines=4)

    def test_extract_blocks_from_simple_function(self):
        """Test extracting blocks from a simple function."""
        code = """
def simple_function():
    x = 1
    y = 2
    z = 3
    return x + y + z
"""
        tree = ast.parse(code)
        func = tree.body[0]

        blocks = self.engine._extract_code_blocks(func)

        # Should have at least the full body block
        self.assertGreater(len(blocks), 0, "Should extract at least one block")

        # Check that blocks are sorted by length (longest first)
        block_lengths = [len(block[1]) for block in blocks]
        self.assertEqual(
            block_lengths,
            sorted(block_lengths, reverse=True),
            "Blocks should be sorted by length (longest first)",
        )

    def test_extract_blocks_respects_min_lines(self):
        """Test that only blocks meeting min_lines are extracted."""
        code = """
def function_with_nested():
    def nested():
        return 1

    x = 1
    y = 2
    z = 3
    w = 4
    return nested() + x + y + z + w
"""
        tree = ast.parse(code)
        func = tree.body[0]

        blocks = self.engine._extract_code_blocks(func)

        # All blocks should have at least min_lines
        for block_range, block_stmts in blocks:
            start, end = block_range
            line_count = end - start + 1
            self.assertGreaterEqual(
                line_count,
                self.engine.min_lines,
                f"Block {start}-{end} has {line_count} lines, "
                f"should have at least {self.engine.min_lines}",
            )

    def test_extract_blocks_skips_docstring(self):
        """Test that docstrings are skipped when extracting blocks."""
        code = '''
def function_with_docstring():
    """This is a docstring."""
    x = 1
    y = 2
    z = 3
    return x + y + z
'''
        tree = ast.parse(code)
        func = tree.body[0]

        blocks = self.engine._extract_code_blocks(func)

        # The full body block should not include the docstring
        full_body = blocks[0][1]
        self.assertFalse(
            isinstance(full_body[0], ast.Expr), "First statement should not be docstring Expr node"
        )

    def test_extract_blocks_with_nested_functions(self):
        """Blocks that include nested defs should now be skipped entirely."""
        code = """
def outer():
    def inner1():
        return 1

    def inner2():
        return 2

    x = inner1()
    y = inner2()
    return x + y
"""
        tree = ast.parse(code)
        func = tree.body[0]

        engine = UnificationRefactorEngine(max_parameters=5, min_lines=1)
        blocks = engine._extract_code_blocks(func)

        self.assertGreater(len(blocks), 0, "Should still extract blocks after nested defs")

        for _, block in blocks:
            self.assertTrue(
                all(
                    not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                    for stmt in block
                ),
                "Extracted blocks must exclude nested function/class definitions",
            )


class TestStructuralSimilarity(unittest.TestCase):
    """Test _are_structurally_similar method."""

    def setUp(self):
        """Set up test fixtures."""
        self.engine = UnificationRefactorEngine(max_parameters=5, min_lines=4)

    def test_identical_blocks_are_similar(self):
        """Test that identical blocks are structurally similar."""
        code = "x = 1\ny = 2\nreturn x + y"
        tree1 = ast.parse(code)
        tree2 = ast.parse(code)

        result = self.engine._are_structurally_similar(tree1.body, tree2.body)

        self.assertTrue(result, "Identical blocks should be structurally similar")

    def test_different_statement_types_not_similar(self):
        """Test that blocks with different statement types are not similar."""
        code1 = "x = 1\nreturn x"
        code2 = "x = 1\nif True: pass"

        tree1 = ast.parse(code1)
        tree2 = ast.parse(code2)

        result = self.engine._are_structurally_similar(tree1.body, tree2.body)

        self.assertFalse(result, "Blocks with different statement types should not be similar")

    def test_different_lengths_not_similar(self):
        """Test that blocks of different lengths are not similar."""
        code1 = "x = 1\ny = 2"
        code2 = "x = 1"

        tree1 = ast.parse(code1)
        tree2 = ast.parse(code2)

        result = self.engine._are_structurally_similar(tree1.body, tree2.body)

        self.assertFalse(result, "Blocks with different lengths should not be similar")

    def test_same_structure_different_values_are_similar(self):
        """Test that blocks with same structure but different values are similar."""
        code1 = "x = 1\ny = 2\nreturn x + y"
        code2 = "x = 10\ny = 20\nreturn x + y"

        tree1 = ast.parse(code1)
        tree2 = ast.parse(code2)

        result = self.engine._are_structurally_similar(tree1.body, tree2.body)

        self.assertTrue(result, "Blocks with same structure should be similar")


class TestAnalyzeFile(unittest.TestCase):
    """Test analyze_file method."""

    def setUp(self):
        """Set up test fixtures."""
        self.engine = UnificationRefactorEngine(max_parameters=5, min_lines=4)

    def test_analyze_file_with_no_functions(self):
        """Test analyzing a file with no functions."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("x = 1\ny = 2\n")
            temp_path = f.name

        try:
            proposals = self.engine.analyze_file(temp_path)
            self.assertEqual(
                len(proposals), 0, "Should return empty list for file with no functions"
            )
        finally:
            os.unlink(temp_path)

    def test_analyze_file_with_single_function(self):
        """Test analyzing a file with only one function."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(
                """
def function1():
    x = 1
    y = 2
    z = 3
    return x + y + z
"""
            )
            temp_path = f.name

        try:
            proposals = self.engine.analyze_file(temp_path)
            self.assertEqual(len(proposals), 0, "Should return empty list with only one function")
        finally:
            os.unlink(temp_path)

    def test_analyze_file_with_identical_functions(self):
        """Test analyzing a file with two identical functions."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(
                """
def function1():
    x = 1
    y = 2
    z = 3
    return x + y + z

def function2():
    x = 1
    y = 2
    z = 3
    return x + y + z
"""
            )
            temp_path = f.name

        try:
            proposals = self.engine.analyze_file(temp_path)
            self.assertGreater(len(proposals), 0, "Should find proposals for identical functions")

            # Check that at least one proposal involves both functions
            found = False
            for prop in proposals:
                if "function1" in prop.description and "function2" in prop.description:
                    found = True
                    break
            self.assertTrue(found, "Should have proposal involving both functions")
        finally:
            os.unlink(temp_path)

    def test_analyze_file_with_syntax_error(self):
        """Test that files with syntax errors are handled gracefully."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def function1(:\n    pass\n")  # Syntax error
            temp_path = f.name

        try:
            proposals = self.engine.analyze_file(temp_path)
            self.assertEqual(
                len(proposals), 0, "Should return empty list for file with syntax error"
            )
        finally:
            os.unlink(temp_path)


class TestBlockPairFinding(unittest.TestCase):
    """Test _find_block_pairs_multi_file method."""

    def setUp(self):
        """Set up test fixtures."""
        self.engine = UnificationRefactorEngine(max_parameters=5, min_lines=4)

    def test_find_pairs_with_two_identical_functions(self):
        """Test finding pairs between two identical functions."""
        code = """
def func1():
    x = 1
    y = 2
    z = 3
    w = 4
    return x + y + z + w

def func2():
    x = 1
    y = 2
    z = 3
    w = 4
    return x + y + z + w
"""
        tree = ast.parse(code)
        from src.towel.unification.scope_analyzer import ScopeAnalyzer

        analyzer = ScopeAnalyzer()
        scope = analyzer.analyze(tree)

        func1, func2 = tree.body[0], tree.body[1]
        all_functions = [
            ("test.py", func1, code, analyzer, scope),
            ("test.py", func2, code, analyzer, scope),
        ]

        pairs = self.engine._find_block_pairs_multi_file(all_functions)

        self.assertGreater(len(pairs), 0, "Should find at least one pair")

        # Check that pairs have equal length blocks
        for pair in pairs:
            self.assertEqual(
                len(pair.block1_nodes),
                len(pair.block2_nodes),
                "Paired blocks should have equal number of statements",
            )

    def test_find_pairs_respects_min_lines(self):
        """Test that only blocks meeting min_lines threshold form pairs."""
        code = """
def func1():
    x = 1
    y = 2
    z = 3
    w = 4
    return x + y + z + w

def func2():
    x = 1
    y = 2
    z = 3
    w = 4
    return x + y + z + w
"""
        tree = ast.parse(code)
        from src.towel.unification.scope_analyzer import ScopeAnalyzer

        analyzer = ScopeAnalyzer()
        scope = analyzer.analyze(tree)

        func1, func2 = tree.body[0], tree.body[1]
        all_functions = [
            ("test.py", func1, code, analyzer, scope),
            ("test.py", func2, code, analyzer, scope),
        ]

        pairs = self.engine._find_block_pairs_multi_file(all_functions)

        # All pairs should meet min_lines requirement
        for pair in pairs:
            start1, end1 = pair.block1_range
            start2, end2 = pair.block2_range
            self.assertGreaterEqual(
                end1 - start1 + 1,
                self.engine.min_lines,
                f"Block 1 should have at least {self.engine.min_lines} lines",
            )
            self.assertGreaterEqual(
                end2 - start2 + 1,
                self.engine.min_lines,
                f"Block 2 should have at least {self.engine.min_lines} lines",
            )


class TestFullBodyWithNestedFunctions(unittest.TestCase):
    """Test that full body blocks with nested functions are handled correctly."""

    def setUp(self):
        """Set up test fixtures."""
        self.engine = UnificationRefactorEngine(max_parameters=5, min_lines=4)

    def test_identical_functions_with_two_nested_functions(self):
        """Test that identical functions with two nested functions produce correct proposal."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(
                """
def outer1(data, threshold):
    def make_validator(limit):
        return lambda x: x > limit

    def make_transformer(factor):
        return lambda x: x * factor + threshold

    validator = make_validator(5)
    transformer = make_transformer(2)
    filtered = list(filter(validator, data))
    transformed = list(map(transformer, filtered))
    return transformed

def outer2(data, threshold):
    def make_validator(limit):
        return lambda x: x > limit

    def make_transformer(factor):
        return lambda x: x * factor + threshold

    validator = make_validator(5)
    transformer = make_transformer(2)
    filtered = list(filter(validator, data))
    transformed = list(map(transformer, filtered))
    return transformed
"""
            )
            temp_path = f.name

        try:
            proposals = self.engine.analyze_file(temp_path)

            # Should have at least one proposal
            self.assertGreater(len(proposals), 0, "Should generate at least one proposal")

            # Find proposals involving both outer functions
            relevant_proposals = [
                p for p in proposals if "outer1" in p.description and "outer2" in p.description
            ]

            self.assertGreater(
                len(relevant_proposals), 0, "Should have proposals involving both outer functions"
            )

            # Extracted helpers should now omit nested function definitions entirely
            for prop in relevant_proposals:
                if isinstance(prop.extracted_function, ast.FunctionDef):
                    nested_defs = [
                        stmt
                        for stmt in prop.extracted_function.body
                        if isinstance(stmt, ast.FunctionDef)
                    ]
                    self.assertEqual(
                        nested_defs,
                        [],
                        "Extracted helper should not contain nested function definitions",
                    )

        finally:
            os.unlink(temp_path)


class TestEdgeCases(unittest.TestCase):
    """Test edge cases and boundary conditions."""

    def setUp(self):
        """Set up test fixtures."""
        self.engine = UnificationRefactorEngine(max_parameters=5, min_lines=4)

    def test_empty_function_bodies(self):
        """Test handling of empty function bodies."""
        code = """
def func1():
    pass

def func2():
    pass
"""
        tree = ast.parse(code)
        func1, func2 = tree.body[0], tree.body[1]

        blocks1 = self.engine._extract_code_blocks(func1)
        blocks2 = self.engine._extract_code_blocks(func2)

        # Empty functions should not produce any blocks (pass doesn't meet min_lines)
        self.assertEqual(len(blocks1), 0, "Empty function should have no extractable blocks")
        self.assertEqual(len(blocks2), 0, "Empty function should have no extractable blocks")

    def test_function_with_only_return(self):
        """Test function with only a return statement."""
        code = """
def func1():
    return 42

def func2():
    return 42
"""
        tree = ast.parse(code)
        func1, func2 = tree.body[0], tree.body[1]

        blocks1 = self.engine._extract_code_blocks(func1)
        blocks2 = self.engine._extract_code_blocks(func2)

        # Single return doesn't meet min_lines
        self.assertEqual(len(blocks1), 0, "Single statement doesn't meet min_lines")
        self.assertEqual(len(blocks2), 0, "Single statement doesn't meet min_lines")

    def test_overlapping_block_prevention(self):
        """Test that overlapping blocks are not included in final proposals."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(
                """
def func1():
    x = 1
    y = 2
    z = 3
    w = 4
    v = 5
    return x + y + z + w + v

def func2():
    x = 1
    y = 2
    z = 3
    w = 4
    v = 5
    return x + y + z + w + v
"""
            )
            temp_path = f.name

        try:
            proposals = self.engine.analyze_file(temp_path)

            # Proposals should not have overlapping ranges
            for i, prop1 in enumerate(proposals):
                for prop2 in proposals[i + 1 :]:
                    # Check that proposals don't overlap
                    ranges1 = set()
                    ranges2 = set()

                    for replacement in prop1.replacements:
                        start, end = replacement[0]
                        ranges1.add((start, end))

                    for replacement in prop2.replacements:
                        start, end = replacement[0]
                        ranges2.add((start, end))

                    # Ranges should not overlap
                    self.assertEqual(
                        ranges1 & ranges2, set(), "Proposals should not have overlapping ranges"
                    )

        finally:
            os.unlink(temp_path)


def main():
    """Run the tests."""
    unittest.main(verbosity=2)


if __name__ == "__main__":
    main()
