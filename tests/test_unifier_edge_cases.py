"""
Tests for unifier edge cases and error paths.

Covers edge cases like mismatched block lengths, exceeding parameter limits,
and various AST node type combinations.
"""

import unittest
import ast
from dry_detector.unification.unifier import Unifier


class TestUnifierEdgeCases(unittest.TestCase):
    """Test unifier edge cases and error paths."""

    def setUp(self):
        self.unifier = Unifier(max_parameters=5, parameterize_constants=True)

    def test_single_block_returns_none(self):
        """Test that unifying a single block returns None."""
        code = """
x = 10
y = 20
"""
        tree = ast.parse(code)
        blocks = [tree.body]

        result = self.unifier.unify_blocks(blocks, [{}])
        self.assertIsNone(result)

    def test_mismatched_block_lengths_returns_none(self):
        """Test that blocks with different lengths don't unify."""
        code1 = """
x = 10
y = 20
"""
        code2 = """
x = 10
y = 20
z = 30
"""
        tree1 = ast.parse(code1)
        tree2 = ast.parse(code2)
        blocks = [tree1.body, tree2.body]

        result = self.unifier.unify_blocks(blocks, [{}, {}])
        self.assertIsNone(result)

    def test_exceeding_max_parameters_returns_none(self):
        """Test that exceeding max_parameters returns None."""
        # Create code with many different constants
        code1 = """
a = 1
b = 2
c = 3
d = 4
e = 5
f = 6
"""
        code2 = """
a = 10
b = 20
c = 30
d = 40
e = 50
f = 60
"""
        tree1 = ast.parse(code1)
        tree2 = ast.parse(code2)
        blocks = [tree1.body, tree2.body]

        # max_parameters is 5, but we have 6 different constants
        result = self.unifier.unify_blocks(blocks, [{}, {}])
        self.assertIsNone(result)

    def test_different_statement_types_dont_unify(self):
        """Test that different statement types don't unify.

        Statements cannot be parameterized as whole units because it would
        create invalid AST structures. Only expressions can be parameterized.
        """
        code1 = """
x = 10
"""
        code2 = """
for i in range(10):
    pass
"""
        tree1 = ast.parse(code1)
        tree2 = ast.parse(code2)
        blocks = [tree1.body, tree2.body]

        result = self.unifier.unify_blocks(blocks, [{}, {}])
        # Different statement types should NOT unify
        self.assertIsNone(result)

    def test_different_operators_dont_unify(self):
        """Test that different operators don't unify."""
        code1 = """
result = x + y
"""
        code2 = """
result = x - y
"""
        tree1 = ast.parse(code1)
        tree2 = ast.parse(code2)
        blocks = [tree1.body, tree2.body]

        result = self.unifier.unify_blocks(blocks, [{}, {}])
        self.assertIsNone(result)

    def test_different_comparison_ops_dont_unify(self):
        """Test that different comparison operators don't unify."""
        code1 = """
if x > 10:
    pass
"""
        code2 = """
if x < 10:
    pass
"""
        tree1 = ast.parse(code1)
        tree2 = ast.parse(code2)
        blocks = [tree1.body, tree2.body]

        result = self.unifier.unify_blocks(blocks, [{}, {}])
        self.assertIsNone(result)

    def test_different_boolops_dont_unify(self):
        """Test that different boolean operators don't unify."""
        code1 = """
if x and y:
    pass
"""
        code2 = """
if x or y:
    pass
"""
        tree1 = ast.parse(code1)
        tree2 = ast.parse(code2)
        blocks = [tree1.body, tree2.body]

        result = self.unifier.unify_blocks(blocks, [{}, {}])
        self.assertIsNone(result)

    def test_different_unary_ops_dont_unify(self):
        """Test that different unary operators don't unify."""
        code1 = """
result = not x
"""
        code2 = """
result = -x
"""
        tree1 = ast.parse(code1)
        tree2 = ast.parse(code2)
        blocks = [tree1.body, tree2.body]

        result = self.unifier.unify_blocks(blocks, [{}, {}])
        self.assertIsNone(result)

    def test_with_statements_unify(self):
        """Test that with statements can unify."""
        code1 = """
with open('file1.txt') as f:
    data = f.read()
"""
        code2 = """
with open('file2.txt') as f:
    data = f.read()
"""
        tree1 = ast.parse(code1)
        tree2 = ast.parse(code2)
        blocks = [tree1.body, tree2.body]

        result = self.unifier.unify_blocks(blocks, [{}, {}])
        self.assertIsNotNone(result)
        self.assertEqual(len(result.param_expressions), 1)

    def test_try_except_statements(self):
        """Test that try-except statements can unify."""
        code1 = """
try:
    result = risky_op(x)
except ValueError:
    result = default1
"""
        code2 = """
try:
    result = risky_op(y)
except ValueError:
    result = default2
"""
        tree1 = ast.parse(code1)
        tree2 = ast.parse(code2)
        blocks = [tree1.body, tree2.body]

        result = self.unifier.unify_blocks(blocks, [{}, {}])
        self.assertIsNotNone(result)

    def test_while_loops_unify(self):
        """Test that while loops can unify."""
        code1 = """
while x > 0:
    x -= 1
"""
        code2 = """
while y > 0:
    y -= 1
"""
        tree1 = ast.parse(code1)
        tree2 = ast.parse(code2)
        blocks = [tree1.body, tree2.body]

        result = self.unifier.unify_blocks(blocks, [{}, {}])
        self.assertIsNotNone(result)

    def test_attribute_access_unifies(self):
        """Test that attribute access can unify."""
        code1 = """
result = obj.method(arg1)
"""
        code2 = """
result = obj.method(arg2)
"""
        tree1 = ast.parse(code1)
        tree2 = ast.parse(code2)
        blocks = [tree1.body, tree2.body]

        result = self.unifier.unify_blocks(blocks, [{}, {}])
        self.assertIsNotNone(result)
        self.assertEqual(len(result.param_expressions), 1)

    def test_subscript_unifies(self):
        """Test that subscript operations can unify."""
        code1 = """
value = data[0]
"""
        code2 = """
value = data[1]
"""
        tree1 = ast.parse(code1)
        tree2 = ast.parse(code2)
        blocks = [tree1.body, tree2.body]

        result = self.unifier.unify_blocks(blocks, [{}, {}])
        self.assertIsNotNone(result)
        self.assertEqual(len(result.param_expressions), 1)

    def test_slice_operations(self):
        """Test that slice operations can unify."""
        code1 = """
subset = data[1:5]
"""
        code2 = """
subset = data[2:6]
"""
        tree1 = ast.parse(code1)
        tree2 = ast.parse(code2)
        blocks = [tree1.body, tree2.body]

        result = self.unifier.unify_blocks(blocks, [{}, {}])
        self.assertIsNotNone(result)

    def test_empty_blocks_return_substitution(self):
        """Test that empty blocks return an empty substitution."""
        blocks = [[], []]

        result = self.unifier.unify_blocks(blocks, [{}, {}])
        self.assertIsNotNone(result)
        self.assertEqual(len(result.param_expressions), 0)


if __name__ == '__main__':
    unittest.main()
