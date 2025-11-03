#!/usr/bin/env python3
"""
Comprehensive unit tests for the visitor_utils module.

These tests isolate and test the make_defensive_generic_visit function
to ensure correct behavior in debug and production modes.
"""

import unittest
import ast
import os
from src.towel.unification.visitor_utils import make_defensive_generic_visit


class TestMakeDefensiveGenericVisit(unittest.TestCase):
    """Test make_defensive_generic_visit function."""

    def tearDown(self):
        """Clean up environment variables after each test."""
        if "DEBUG_AST_COVERAGE" in os.environ:
            del os.environ["DEBUG_AST_COVERAGE"]

    def test_returns_callable(self):
        """Test that make_defensive_generic_visit returns a callable."""
        result = make_defensive_generic_visit("TestVisitor")

        self.assertTrue(callable(result), "Should return a callable function")

    def test_function_signature(self):
        """Test that returned function has correct signature."""
        generic_visit = make_defensive_generic_visit("TestVisitor")

        # Should accept self and node parameters
        import inspect

        sig = inspect.signature(generic_visit)
        params = list(sig.parameters.keys())

        self.assertEqual(len(params), 2, "Should have 2 parameters (self, node)")
        self.assertEqual(params[0], "self", "First parameter should be 'self'")
        self.assertEqual(params[1], "node", "Second parameter should be 'node'")


class TestProductionModeBehavior(unittest.TestCase):
    """Test behavior when DEBUG_AST_COVERAGE is not set."""

    def tearDown(self):
        """Clean up environment variables."""
        if "DEBUG_AST_COVERAGE" in os.environ:
            del os.environ["DEBUG_AST_COVERAGE"]

    def test_delegates_to_parent_visitor(self):
        """Test that production mode delegates to parent NodeVisitor."""

        class TestVisitor(ast.NodeVisitor):
            generic_visit = make_defensive_generic_visit("TestVisitor")

            def __init__(self):
                self.visited = []

            def visit_Name(self, node):
                self.visited.append(("Name", node.id))
                return node

        code = "x = y + z"
        tree = ast.parse(code)
        visitor = TestVisitor()

        # Should not raise even though we don't handle all node types
        visitor.visit(tree)

        # Should have visited Name nodes
        self.assertGreater(len(visitor.visited), 0, "Should visit some nodes")

    def test_delegates_to_parent_transformer(self):
        """Test that production mode delegates to parent NodeTransformer."""

        class TestTransformer(ast.NodeTransformer):
            generic_visit = make_defensive_generic_visit("TestTransformer")

            def visit_Constant(self, node):
                # Double constant values
                if isinstance(node.value, int):
                    return ast.Constant(value=node.value * 2)
                return node

        code = "x = 1"
        tree = ast.parse(code)
        transformer = TestTransformer()

        # Should not raise
        result = transformer.visit(tree)

        # Tree should be modified
        self.assertIsNotNone(result, "Should return modified tree")


class TestDebugModeBehavior(unittest.TestCase):
    """Test behavior when DEBUG_AST_COVERAGE is set."""

    def setUp(self):
        """Enable debug mode."""
        os.environ["DEBUG_AST_COVERAGE"] = "1"

    def tearDown(self):
        """Clean up environment."""
        if "DEBUG_AST_COVERAGE" in os.environ:
            del os.environ["DEBUG_AST_COVERAGE"]

    def test_raises_for_unhandled_node(self):
        """Test that debug mode raises NotImplementedError for unhandled nodes."""

        class IncompleteVisitor(ast.NodeVisitor):
            generic_visit = make_defensive_generic_visit("IncompleteVisitor")

            def visit_Module(self, node):
                # Handle Module but not its children
                for child in node.body:
                    self.visit(child)

        code = "x = 1"
        tree = ast.parse(code)
        visitor = IncompleteVisitor()

        # Should raise NotImplementedError for Assign node
        with self.assertRaises(NotImplementedError) as ctx:
            visitor.visit(tree)

        error_message = str(ctx.exception)
        self.assertIn("IncompleteVisitor", error_message, "Error should mention visitor class")
        self.assertIn("Assign", error_message, "Error should mention missing node type")
        self.assertIn("visit_Assign", error_message, "Error should suggest visitor method")

    def test_does_not_raise_for_handled_node(self):
        """Test that debug mode doesn't raise for handled nodes."""

        class CompleteVisitor(ast.NodeVisitor):
            generic_visit = make_defensive_generic_visit("CompleteVisitor")

            def __init__(self):
                self.visited = []

            def visit_Module(self, node):
                self.visited.append("Module")
                self.generic_visit(node)

            def visit_Assign(self, node):
                self.visited.append("Assign")
                self.generic_visit(node)

            def visit_Name(self, node):
                self.visited.append("Name")
                self.generic_visit(node)

            def visit_Constant(self, node):
                self.visited.append("Constant")
                self.generic_visit(node)

            def visit_Store(self, node):
                self.visited.append("Store")

            def visit_Load(self, node):
                self.visited.append("Load")

        code = "x = 1"
        tree = ast.parse(code)
        visitor = CompleteVisitor()

        # Should not raise
        visitor.visit(tree)

        # Should have visited nodes
        self.assertGreater(len(visitor.visited), 0, "Should visit nodes")

    def test_error_message_includes_visitor_name(self):
        """Test that error message includes the visitor class name."""

        class MySpecialVisitor(ast.NodeVisitor):
            generic_visit = make_defensive_generic_visit("MySpecialVisitor")

        code = "x = 1"
        tree = ast.parse(code)
        visitor = MySpecialVisitor()

        with self.assertRaises(NotImplementedError) as ctx:
            visitor.visit(tree)

        self.assertIn(
            "MySpecialVisitor", str(ctx.exception), "Error should mention custom visitor name"
        )


class TestNodeTypeChecking(unittest.TestCase):
    """Test that only AST node types trigger errors."""

    def setUp(self):
        """Enable debug mode."""
        os.environ["DEBUG_AST_COVERAGE"] = "1"

    def tearDown(self):
        """Clean up environment."""
        if "DEBUG_AST_COVERAGE" in os.environ:
            del os.environ["DEBUG_AST_COVERAGE"]

    def test_only_checks_ast_nodes(self):
        """Test that only real AST nodes (in ast module) trigger errors."""

        class TestVisitor(ast.NodeVisitor):
            generic_visit = make_defensive_generic_visit("TestVisitor")

            def visit_Module(self, node):
                # Visit all children
                for child in ast.walk(node):
                    if child is not node:
                        self.visit(child)

        code = "x = 1"
        tree = ast.parse(code)
        visitor = TestVisitor()

        # Should raise for real AST nodes like Assign
        with self.assertRaises(NotImplementedError) as ctx:
            visitor.visit(tree)

        # Error should be about a real AST node
        error_msg = str(ctx.exception)
        # Should mention an actual AST node type
        ast_types = ["Assign", "Name", "Constant", "Store", "Load"]
        self.assertTrue(
            any(t in error_msg for t in ast_types),
            f"Error should mention an AST node type: {error_msg}",
        )


class TestVisitorVsTransformer(unittest.TestCase):
    """Test behavior with both NodeVisitor and NodeTransformer."""

    def tearDown(self):
        """Clean up environment."""
        if "DEBUG_AST_COVERAGE" in os.environ:
            del os.environ["DEBUG_AST_COVERAGE"]

    def test_works_with_node_visitor(self):
        """Test that it works correctly with NodeVisitor."""

        class TestVisitor(ast.NodeVisitor):
            generic_visit = make_defensive_generic_visit("TestVisitor")

            def visit_Module(self, node):
                self.generic_visit(node)

        code = "x = 1"
        tree = ast.parse(code)
        visitor = TestVisitor()

        # Should work without errors in production mode
        visitor.visit(tree)

    def test_works_with_node_transformer(self):
        """Test that it works correctly with NodeTransformer."""

        class TestTransformer(ast.NodeTransformer):
            generic_visit = make_defensive_generic_visit("TestTransformer")

            def visit_Module(self, node):
                return self.generic_visit(node)

        code = "x = 1"
        tree = ast.parse(code)
        transformer = TestTransformer()

        # Should work and return transformed tree
        result = transformer.visit(tree)
        self.assertIsNotNone(result, "Transformer should return result")

    def test_transformer_returns_node(self):
        """Test that transformer version returns nodes correctly."""

        class TestTransformer(ast.NodeTransformer):
            generic_visit = make_defensive_generic_visit("TestTransformer")

            def visit_Constant(self, node):
                # Modify constants
                if isinstance(node.value, int):
                    node.value = node.value + 1
                return node

        code = "x = 5"
        tree = ast.parse(code)
        transformer = TestTransformer()

        result = transformer.visit(tree)

        # Should have modified the constant
        assign = result.body[0]
        self.assertEqual(assign.value.value, 6, "Constant should be modified")


class TestIntegration(unittest.TestCase):
    """Integration tests using realistic visitors."""

    def tearDown(self):
        """Clean up environment."""
        if "DEBUG_AST_COVERAGE" in os.environ:
            del os.environ["DEBUG_AST_COVERAGE"]

    def test_realistic_visitor_production(self):
        """Test realistic visitor in production mode."""

        class NameCollector(ast.NodeVisitor):
            generic_visit = make_defensive_generic_visit("NameCollector")

            def __init__(self):
                self.names = []

            def visit_Name(self, node):
                self.names.append(node.id)
                self.generic_visit(node)

        code = """
def foo(x, y):
    z = x + y
    return z
"""
        tree = ast.parse(code)
        collector = NameCollector()

        collector.visit(tree)

        # Should have collected variable names
        self.assertIn("x", collector.names, "Should collect parameter name")
        self.assertIn("y", collector.names, "Should collect parameter name")
        self.assertIn("z", collector.names, "Should collect local variable name")

    def test_realistic_transformer_production(self):
        """Test realistic transformer in production mode."""

        class ConstantDoubler(ast.NodeTransformer):
            generic_visit = make_defensive_generic_visit("ConstantDoubler")

            def visit_Constant(self, node):
                if isinstance(node.value, int):
                    node.value = node.value * 2
                return node

        code = "x = 5\ny = 10"
        tree = ast.parse(code)
        doubler = ConstantDoubler()

        result = doubler.visit(tree)

        # Values should be doubled
        self.assertEqual(result.body[0].value.value, 10, "First constant should be doubled")
        self.assertEqual(result.body[1].value.value, 20, "Second constant should be doubled")


def main():
    """Run the tests."""
    unittest.main(verbosity=2)


if __name__ == "__main__":
    main()
