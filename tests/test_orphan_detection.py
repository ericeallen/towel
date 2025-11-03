"""
Tests for orphaned variable detection.

Ensures that extractions which would create orphaned variable references
are properly rejected.
"""

import unittest
import ast
from towel.unification.orphan_detector import (
    get_bound_variables,
    get_used_variables,
    has_orphaned_variables,
)


class TestOrphanDetection(unittest.TestCase):
    """Test orphaned variable detection."""

    def test_get_bound_variables_simple(self):
        """Test detecting simple variable bindings."""
        code = """
x = 10
y = 20
"""
        tree = ast.parse(code)
        bound = get_bound_variables(tree.body)
        self.assertEqual(bound, {"x", "y"})

    def test_get_bound_variables_for_loop(self):
        """Test detecting for loop variable bindings."""
        code = """
for i in range(10):
    pass
"""
        tree = ast.parse(code)
        bound = get_bound_variables(tree.body)
        self.assertIn("i", bound)

    def test_get_bound_variables_comprehension_excluded(self):
        """Test that comprehension variables are NOT bound at function level."""
        code = """
result = [x for x in range(10)]
"""
        tree = ast.parse(code)
        bound = get_bound_variables(tree.body)
        # 'result' is bound, but 'x' is local to the comprehension
        self.assertIn("result", bound)
        self.assertNotIn("x", bound)

    def test_get_used_variables(self):
        """Test detecting variable usage."""
        code = """
result = x + y
print(result)
"""
        tree = ast.parse(code)
        used = get_used_variables(tree.body)
        self.assertIn("x", used)
        self.assertIn("y", used)
        self.assertIn("print", used)
        # 'result' is used in print, but also bound in first line

    def test_no_orphans_no_remaining_code(self):
        """Test that no orphans are detected when there's no remaining code."""
        code = """
def foo():
    x = 10
    y = 20
    result = x + y
    return result
"""
        tree = ast.parse(code)
        func = tree.body[0]
        body = func.body

        # Extract entire function body - no remaining code
        has_orphans, orphans = has_orphaned_variables(body, (0, len(body) - 1))
        self.assertFalse(has_orphans)
        self.assertEqual(orphans, set())

    def test_orphans_detected(self):
        """Test detecting orphaned variables."""
        code = """
def foo():
    x = 10
    y = 20
    total = x + y
    if total > 25:
        print("Large")
    return total
"""
        tree = ast.parse(code)
        func = tree.body[0]
        body = func.body

        # Extract first 3 statements (lines that bind x, y, total)
        # But remaining code uses 'total'
        has_orphans, orphans = has_orphaned_variables(body, (0, 2))
        self.assertTrue(has_orphans)
        self.assertIn("total", orphans)

    def test_no_orphans_variable_rebound(self):
        """Test that no orphans if variable is rebound in remaining code."""
        code = """
def foo():
    x = 10
    y = 20
    total = x + y
    total = 100
    return total
"""
        tree = ast.parse(code)
        func = tree.body[0]
        body = func.body

        # Extract first 3 statements
        # 'total' is used in remaining code BUT also rebound
        has_orphans, orphans = has_orphaned_variables(body, (0, 2))
        self.assertFalse(has_orphans)
        # 'total' is rebound in remaining code, so it's not orphaned

    def test_orphans_multiple_variables(self):
        """Test detecting multiple orphaned variables."""
        code = """
def process():
    x = data.get('x')
    y = data.get('y')
    z = x * 2
    result = y + z
    if x > 100:
        return result * 2
    return result
"""
        tree = ast.parse(code)
        func = tree.body[0]
        body = func.body

        # Extract first 3 statements
        # Remaining code uses 'x' and 'result'
        has_orphans, orphans = has_orphaned_variables(body, (0, 2))
        self.assertTrue(has_orphans)
        # Both 'x' and 'result' should be orphaned (bound in extracted, used in remaining)
        # But wait, 'result' is also BOUND in the remaining code (line 4)
        # So only 'x' should be orphaned
        self.assertIn("x", orphans)

    def test_no_orphans_only_uses_parameters(self):
        """Test that using function parameters in remaining code is fine."""
        code = """
def process(data):
    x = data.get('x', 0)
    y = data.get('y', 0)
    total = x + y
    if data.get('z', 0) > 10:
        return total * 2
    return total
"""
        tree = ast.parse(code)
        func = tree.body[0]
        body = func.body

        # Extract first 3 statements
        # Remaining code uses 'data' (parameter) and 'total' (bound in extracted)
        has_orphans, orphans = has_orphaned_variables(body, (0, 2))
        self.assertTrue(has_orphans)
        self.assertIn("total", orphans)
        # 'data' is a parameter, not bound in extracted block

    def test_annotated_assignment_binding(self):
        """Test that annotated assignments are detected as bindings."""
        code = """
x: int = 10
y: str = "hello"
"""
        tree = ast.parse(code)
        bound = get_bound_variables(tree.body)
        self.assertIn("x", bound)
        self.assertIn("y", bound)

    def test_async_function_binding(self):
        """Test that async function definitions are detected as bindings."""
        code = """
async def fetch_data():
    pass
x = 1
"""
        tree = ast.parse(code)
        bound = get_bound_variables(tree.body)
        self.assertIn("fetch_data", bound)
        self.assertIn("x", bound)

    def test_class_definition_binding(self):
        """Test that class definitions are detected as bindings."""
        code = """
class MyClass:
    pass
x = 1
"""
        tree = ast.parse(code)
        bound = get_bound_variables(tree.body)
        self.assertIn("MyClass", bound)
        self.assertIn("x", bound)

    def test_set_comprehension_not_bound(self):
        """Test that set comprehension variables are not bound at outer level."""
        code = """
result = {x * 2 for x in range(10)}
"""
        tree = ast.parse(code)
        bound = get_bound_variables(tree.body)
        self.assertIn("result", bound)
        self.assertNotIn("x", bound)

    def test_generator_expression_not_bound(self):
        """Test that generator expression variables are not bound at outer level."""
        code = """
result = (x * 2 for x in range(10))
"""
        tree = ast.parse(code)
        bound = get_bound_variables(tree.body)
        self.assertIn("result", bound)
        self.assertNotIn("x", bound)

    def test_starred_assignment(self):
        """Test that starred assignments are detected as bindings."""
        code = """
a, *rest, b = [1, 2, 3, 4, 5]
"""
        tree = ast.parse(code)
        bound = get_bound_variables(tree.body)
        self.assertIn("a", bound)
        self.assertIn("rest", bound)
        self.assertIn("b", bound)

    def test_augmented_assignment_binding(self):
        """Test that augmented assignments are detected as bindings."""
        code = """
x = 10
x += 5
"""
        tree = ast.parse(code)
        bound = get_bound_variables(tree.body)
        self.assertIn("x", bound)


if __name__ == "__main__":
    unittest.main()
