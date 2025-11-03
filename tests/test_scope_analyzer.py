"""
Tests for scope analyzer edge cases.

Covers advanced Python features like position-only args, keyword-only args,
*args, **kwargs, async functions, and annotated assignments.
"""

import unittest
import ast
from towel.unification.scope_analyzer import ScopeAnalyzer


class TestScopeAnalyzerEdgeCases(unittest.TestCase):
    """Test scope analyzer edge cases."""

    def setUp(self):
        self.analyzer = ScopeAnalyzer()

    def test_position_only_args(self):
        """Test that position-only arguments are properly bound."""
        code = """
def foo(a, b, /, c):
    return a + b + c
"""
        tree = ast.parse(code)
        scope = self.analyzer.analyze(tree)

        func = tree.body[0]
        func_scope = self.analyzer.node_scopes.get(func)
        self.assertIsNotNone(func_scope)

        # Position-only args should be bound
        self.assertIn("a", func_scope.bindings)
        self.assertIn("b", func_scope.bindings)
        self.assertIn("c", func_scope.bindings)

    def test_keyword_only_args(self):
        """Test that keyword-only arguments are properly bound."""
        code = """
def foo(a, *, b, c=10):
    return a + b + c
"""
        tree = ast.parse(code)
        scope = self.analyzer.analyze(tree)

        func = tree.body[0]
        func_scope = self.analyzer.node_scopes.get(func)
        self.assertIsNotNone(func_scope)

        # Keyword-only args should be bound
        self.assertIn("a", func_scope.bindings)
        self.assertIn("b", func_scope.bindings)
        self.assertIn("c", func_scope.bindings)

    def test_vararg_and_kwarg(self):
        """Test that *args and **kwargs are properly bound."""
        code = """
def foo(a, *args, **kwargs):
    return a + len(args) + len(kwargs)
"""
        tree = ast.parse(code)
        scope = self.analyzer.analyze(tree)

        func = tree.body[0]
        func_scope = self.analyzer.node_scopes.get(func)
        self.assertIsNotNone(func_scope)

        # *args and **kwargs should be bound
        self.assertIn("a", func_scope.bindings)
        self.assertIn("args", func_scope.bindings)
        self.assertIn("kwargs", func_scope.bindings)

    def test_async_function(self):
        """Test that async functions are properly analyzed."""
        code = """
async def fetch(url):
    data = await get_data(url)
    return data
"""
        tree = ast.parse(code)
        scope = self.analyzer.analyze(tree)

        # Async function should be bound in module scope
        self.assertIn("fetch", scope.bindings)

        func = tree.body[0]
        func_scope = self.analyzer.node_scopes.get(func)
        self.assertIsNotNone(func_scope)

        # Parameters and local vars should be bound in function scope
        self.assertIn("url", func_scope.bindings)
        self.assertIn("data", func_scope.bindings)

    def test_annotated_assignment_with_value(self):
        """Test that annotated assignments with values are properly bound."""
        code = """
def foo():
    x: int = 10
    y: str = "hello"
    return x, y
"""
        tree = ast.parse(code)
        scope = self.analyzer.analyze(tree)

        func = tree.body[0]
        func_scope = self.analyzer.node_scopes.get(func)
        self.assertIsNotNone(func_scope)

        # Annotated variables should be bound
        self.assertIn("x", func_scope.bindings)
        self.assertIn("y", func_scope.bindings)

    def test_if_else_branches(self):
        """Test that else branches are properly visited."""
        code = """
def foo(x):
    if x > 10:
        y = x * 2
    else:
        z = x * 3
    return y if x > 10 else z
"""
        tree = ast.parse(code)
        scope = self.analyzer.analyze(tree)

        func = tree.body[0]
        func_scope = self.analyzer.node_scopes.get(func)
        self.assertIsNotNone(func_scope)

        # Variables in both branches should be bound
        self.assertIn("y", func_scope.bindings)
        self.assertIn("z", func_scope.bindings)

    def test_get_binding_for_name(self):
        """Test get_binding_for_name method."""
        code = """
x = 10
def foo():
    y = 20
    return x + y
"""
        tree = ast.parse(code)
        self.analyzer.analyze(tree)

        # Find the Name nodes
        func = tree.body[1]
        return_stmt = func.body[1]
        binop = return_stmt.value
        x_name = binop.left
        y_name = binop.right

        # Get bindings
        x_binding = self.analyzer.get_binding_for_name(x_name)
        y_binding = self.analyzer.get_binding_for_name(y_name)

        # Both should have bindings
        self.assertIsNotNone(x_binding)
        self.assertIsNotNone(y_binding)

    def test_comprehension_with_conditions(self):
        """Test that comprehensions with conditions are properly analyzed."""
        code = """
def foo(items):
    result = [x * 2 for x in items if x > 10]
    return result
"""
        tree = ast.parse(code)
        scope = self.analyzer.analyze(tree)

        func = tree.body[0]
        func_scope = self.analyzer.node_scopes.get(func)
        self.assertIsNotNone(func_scope)

        # 'result' should be bound, but not 'x' (it's local to comprehension)
        self.assertIn("result", func_scope.bindings)
        self.assertIn("items", func_scope.bindings)

    def test_all_arg_types_combined(self):
        """Test function with all argument types combined."""
        code = """
def complex_func(a, b, /, c, d=10, *args, e, f=20, **kwargs):
    return a + b + c + d + e + f + len(args) + len(kwargs)
"""
        tree = ast.parse(code)
        scope = self.analyzer.analyze(tree)

        func = tree.body[0]
        func_scope = self.analyzer.node_scopes.get(func)
        self.assertIsNotNone(func_scope)

        # All argument types should be bound
        for arg_name in ["a", "b", "c", "d", "args", "e", "f", "kwargs"]:
            self.assertIn(arg_name, func_scope.bindings, f"Argument '{arg_name}' should be bound")

    def test_for_else_clause(self):
        """Test that for-else clauses are properly visited."""
        code = """
def search(items, target):
    for item in items:
        if item == target:
            found = True
            break
    else:
        found = False
    return found
"""
        tree = ast.parse(code)
        scope = self.analyzer.analyze(tree)

        func = tree.body[0]
        func_scope = self.analyzer.node_scopes.get(func)
        self.assertIsNotNone(func_scope)

        # Variables in both for body and else clause should be bound
        self.assertIn("item", func_scope.bindings)
        self.assertIn("found", func_scope.bindings)


if __name__ == "__main__":
    unittest.main()
