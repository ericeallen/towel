#!/usr/bin/env python3
"""
Comprehensive unit tests for the ScopeAnalyzer class.

These tests isolate and test individual methods to ensure correct behavior
of scope analysis, binding tracking, and free variable detection.
"""

import unittest
import ast
from src.towel.unification.scope_analyzer import ScopeAnalyzer, Scope, Binding


class TestScopeBasics(unittest.TestCase):
    """Test basic scope creation and lookup functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.analyzer = ScopeAnalyzer()

    def test_create_root_scope(self):
        """Test creating a root scope."""
        code = "x = 1"
        tree = ast.parse(code)

        scope = self.analyzer.analyze(tree)

        self.assertIsNotNone(scope, "Should create a root scope")
        self.assertEqual(scope.scope_id, 0, "Root scope should have ID 0")
        self.assertIsNone(scope.parent, "Root scope should have no parent")

    def test_scope_add_binding(self):
        """Test adding bindings to a scope."""
        scope = Scope(scope_id=0, parent=None)
        node = ast.Name(id="x", ctx=ast.Store())

        scope.add_binding("x", node)

        self.assertIn("x", scope.bindings, "Binding should be added to scope")
        binding = scope.bindings["x"]
        self.assertEqual(binding.name, "x")
        self.assertEqual(binding.scope_id, 0)
        self.assertEqual(binding.node, node)

    def test_scope_lookup_local(self):
        """Test looking up a binding in the local scope."""
        scope = Scope(scope_id=0, parent=None)
        node = ast.Name(id="x", ctx=ast.Store())
        scope.add_binding("x", node)

        binding = scope.lookup("x")

        self.assertIsNotNone(binding, "Should find local binding")
        self.assertEqual(binding.name, "x")

    def test_scope_lookup_parent(self):
        """Test looking up a binding in parent scope."""
        parent_scope = Scope(scope_id=0, parent=None)
        child_scope = Scope(scope_id=1, parent=parent_scope)
        node = ast.Name(id="x", ctx=ast.Store())
        parent_scope.add_binding("x", node)

        binding = child_scope.lookup("x")

        self.assertIsNotNone(binding, "Should find binding in parent scope")
        self.assertEqual(binding.name, "x")
        self.assertEqual(binding.scope_id, 0, "Should be from parent scope")

    def test_scope_lookup_shadowing(self):
        """Test that local bindings shadow parent bindings."""
        parent_scope = Scope(scope_id=0, parent=None)
        child_scope = Scope(scope_id=1, parent=parent_scope)
        parent_node = ast.Name(id="x", ctx=ast.Store())
        child_node = ast.Name(id="x", ctx=ast.Store())

        parent_scope.add_binding("x", parent_node)
        child_scope.add_binding("x", child_node)

        binding = child_scope.lookup("x")

        self.assertIsNotNone(binding, "Should find binding")
        self.assertEqual(binding.scope_id, 1, "Should find child's binding, not parent's")

    def test_scope_lookup_not_found(self):
        """Test looking up a non-existent binding."""
        scope = Scope(scope_id=0, parent=None)

        binding = scope.lookup("nonexistent")

        self.assertIsNone(binding, "Should return None for non-existent binding")


class TestSimpleStatements(unittest.TestCase):
    """Test analyzing simple statements."""

    def setUp(self):
        """Set up test fixtures."""
        self.analyzer = ScopeAnalyzer()

    def test_simple_assignment(self):
        """Test analyzing a simple assignment."""
        code = "x = 1"
        tree = ast.parse(code)

        scope = self.analyzer.analyze(tree)

        self.assertIn("x", scope.bindings, "Should have binding for x")

    def test_multiple_assignments(self):
        """Test analyzing multiple assignments."""
        code = "x = 1\ny = 2\nz = 3"
        tree = ast.parse(code)

        scope = self.analyzer.analyze(tree)

        self.assertIn("x", scope.bindings)
        self.assertIn("y", scope.bindings)
        self.assertIn("z", scope.bindings)

    def test_tuple_unpacking(self):
        """Test analyzing tuple unpacking."""
        code = "x, y = (1, 2)"
        tree = ast.parse(code)

        scope = self.analyzer.analyze(tree)

        self.assertIn("x", scope.bindings, "Should have binding for x")
        self.assertIn("y", scope.bindings, "Should have binding for y")

    def test_augmented_assignment(self):
        """Test analyzing augmented assignment."""
        code = "x = 1\nx += 1"
        tree = ast.parse(code)

        scope = self.analyzer.analyze(tree)

        # Augmented assignment doesn't create a new binding, just modifies
        self.assertIn("x", scope.bindings)

    def test_annotated_assignment(self):
        """Test analyzing annotated assignment."""
        code = "x: int = 1"
        tree = ast.parse(code)

        scope = self.analyzer.analyze(tree)

        self.assertIn("x", scope.bindings)


class TestFunctionScopes(unittest.TestCase):
    """Test analyzing function definitions and scopes."""

    def setUp(self):
        """Set up test fixtures."""
        self.analyzer = ScopeAnalyzer()

    def test_function_definition(self):
        """Test that function name is bound in outer scope."""
        code = "def foo(): pass"
        tree = ast.parse(code)

        scope = self.analyzer.analyze(tree)

        self.assertIn("foo", scope.bindings, "Function name should be in outer scope")

    def test_function_parameters(self):
        """Test that function parameters are bound in function scope."""
        code = "def foo(x, y): return x + y"
        tree = ast.parse(code)
        func_def = tree.body[0]

        scope = self.analyzer.analyze(tree)

        # Get the function's scope
        func_scope = self.analyzer.node_scopes[func_def]
        self.assertIn("x", func_scope.bindings, "Parameter x should be in function scope")
        self.assertIn("y", func_scope.bindings, "Parameter y should be in function scope")

    def test_function_with_defaults(self):
        """Test function with default parameter values."""
        code = "def foo(x, y=10): return x + y"
        tree = ast.parse(code)
        func_def = tree.body[0]

        scope = self.analyzer.analyze(tree)

        func_scope = self.analyzer.node_scopes[func_def]
        self.assertIn("x", func_scope.bindings)
        self.assertIn("y", func_scope.bindings)

    def test_function_with_varargs(self):
        """Test function with *args."""
        code = "def foo(x, *args): pass"
        tree = ast.parse(code)
        func_def = tree.body[0]

        scope = self.analyzer.analyze(tree)

        func_scope = self.analyzer.node_scopes[func_def]
        self.assertIn("x", func_scope.bindings)
        self.assertIn("args", func_scope.bindings, "Vararg should be bound in function scope")

    def test_function_with_kwargs(self):
        """Test function with **kwargs."""
        code = "def foo(x, **kwargs): pass"
        tree = ast.parse(code)
        func_def = tree.body[0]

        scope = self.analyzer.analyze(tree)

        func_scope = self.analyzer.node_scopes[func_def]
        self.assertIn("x", func_scope.bindings)
        self.assertIn("kwargs", func_scope.bindings, "Kwarg should be bound in function scope")

    def test_nested_functions(self):
        """Test analyzing nested function definitions."""
        code = """
def outer(x):
    def inner(y):
        return x + y
    return inner
"""
        tree = ast.parse(code)
        outer_def = tree.body[0]
        inner_def = outer_def.body[0]

        scope = self.analyzer.analyze(tree)

        # Check outer function scope
        outer_scope = self.analyzer.node_scopes[outer_def]
        self.assertIn("x", outer_scope.bindings)
        self.assertIn("inner", outer_scope.bindings, "Inner function name should be in outer scope")

        # Check inner function scope
        inner_scope = self.analyzer.node_scopes[inner_def]
        self.assertIn("y", inner_scope.bindings)


class TestClassScopes(unittest.TestCase):
    """Test analyzing class definitions and scopes."""

    def setUp(self):
        """Set up test fixtures."""
        self.analyzer = ScopeAnalyzer()

    def test_class_definition(self):
        """Test that class name is bound in outer scope."""
        code = "class Foo: pass"
        tree = ast.parse(code)

        scope = self.analyzer.analyze(tree)

        self.assertIn("Foo", scope.bindings, "Class name should be in outer scope")

    def test_class_with_methods(self):
        """Test class with method definitions."""
        code = """
class Foo:
    def method(self):
        pass
"""
        tree = ast.parse(code)
        class_def = tree.body[0]

        scope = self.analyzer.analyze(tree)

        # Check class scope
        class_scope = self.analyzer.node_scopes[class_def]
        self.assertIn("method", class_scope.bindings, "Method should be in class scope")

    def test_class_with_attributes(self):
        """Test class with class attributes."""
        code = """
class Foo:
    x = 1
    y = 2
"""
        tree = ast.parse(code)
        class_def = tree.body[0]

        scope = self.analyzer.analyze(tree)

        class_scope = self.analyzer.node_scopes[class_def]
        self.assertIn("x", class_scope.bindings)
        self.assertIn("y", class_scope.bindings)


class TestControlFlow(unittest.TestCase):
    """Test analyzing control flow statements."""

    def setUp(self):
        """Set up test fixtures."""
        self.analyzer = ScopeAnalyzer()

    def test_for_loop(self):
        """Test that for loop variable is bound."""
        code = "for i in range(10): pass"
        tree = ast.parse(code)

        scope = self.analyzer.analyze(tree)

        self.assertIn("i", scope.bindings, "Loop variable should be bound")

    def test_for_loop_tuple_unpacking(self):
        """Test for loop with tuple unpacking."""
        code = "for x, y in [(1, 2), (3, 4)]: pass"
        tree = ast.parse(code)

        scope = self.analyzer.analyze(tree)

        self.assertIn("x", scope.bindings)
        self.assertIn("y", scope.bindings)

    def test_with_statement(self):
        """Test with statement binding."""
        code = "with open('file') as f: pass"
        tree = ast.parse(code)

        scope = self.analyzer.analyze(tree)

        self.assertIn("f", scope.bindings, "With variable should be bound")

    def test_with_statement_no_as(self):
        """Test with statement without 'as' clause."""
        code = "with open('file'): pass"
        tree = ast.parse(code)

        scope = self.analyzer.analyze(tree)

        # No bindings should be created
        self.assertEqual(len(scope.bindings), 0)


class TestGlobalNonlocal(unittest.TestCase):
    """Test global and nonlocal declarations."""

    def setUp(self):
        """Set up test fixtures."""
        self.analyzer = ScopeAnalyzer()

    def test_global_declaration(self):
        """Test global variable declaration."""
        code = """
x = 1
def foo():
    global x
    x = 2
"""
        tree = ast.parse(code)
        func_def = tree.body[1]

        scope = self.analyzer.analyze(tree)

        func_scope = self.analyzer.node_scopes[func_def]
        # Global variables should be tracked
        self.assertIn(func_scope.scope_id, self.analyzer.global_vars)
        self.assertIn("x", self.analyzer.global_vars[func_scope.scope_id])

    def test_nonlocal_declaration(self):
        """Test nonlocal variable declaration."""
        code = """
def outer():
    x = 1
    def inner():
        nonlocal x
        x = 2
"""
        tree = ast.parse(code)
        outer_def = tree.body[0]
        inner_def = outer_def.body[1]

        scope = self.analyzer.analyze(tree)

        inner_scope = self.analyzer.node_scopes[inner_def]
        # Nonlocal variables should be tracked
        self.assertIn(inner_scope.scope_id, self.analyzer.nonlocal_vars)
        self.assertIn("x", self.analyzer.nonlocal_vars[inner_scope.scope_id])


class TestFreeVariables(unittest.TestCase):
    """Test get_free_variables method."""

    def setUp(self):
        """Set up test fixtures."""
        self.analyzer = ScopeAnalyzer()

    def test_no_free_variables(self):
        """Test code with no free variables."""
        code = "x = 1\ny = x + 1"
        tree = ast.parse(code)

        self.analyzer.analyze(tree)
        free_vars = self.analyzer.get_free_variables(tree.body)

        self.assertEqual(len(free_vars), 0, "Should have no free variables")

    def test_simple_free_variable(self):
        """Test code with a simple free variable."""
        code = "y = x + 1"
        tree = ast.parse(code)

        self.analyzer.analyze(tree)
        free_vars = self.analyzer.get_free_variables(tree.body)

        self.assertIn("x", free_vars, "x should be a free variable")

    def test_used_before_assigned(self):
        """Test variable used before it's assigned."""
        code = "y = x\nx = 1"
        tree = ast.parse(code)

        self.analyzer.analyze(tree)
        free_vars = self.analyzer.get_free_variables(tree.body)

        # x is used before assigned, so it should be a free variable
        self.assertIn("x", free_vars, "x should be free (used before assigned)")

    def test_augmented_assignment_is_use(self):
        """Test that augmented assignment counts as a use."""
        code = "x += 1"
        tree = ast.parse(code)

        self.analyzer.analyze(tree)
        free_vars = self.analyzer.get_free_variables(tree.body)

        self.assertIn("x", free_vars, "x should be free (augmented assignment is a use)")

    def test_free_variables_with_function(self):
        """Test free variables in function body."""
        code = """
def foo(x):
    y = 1
    return x + y + z
"""
        tree = ast.parse(code)
        func_def = tree.body[0]

        self.analyzer.analyze(tree)
        free_vars = self.analyzer.get_free_variables(func_def.body)

        # z is free (not defined anywhere)
        # When calling get_free_variables on just the body statements without context,
        # x appears as a use before it's assigned in those statements
        self.assertIn("z", free_vars, "z should be a free variable")
        self.assertNotIn("y", free_vars, "y should not be free (it's assigned locally)")

    def test_free_variables_nested_function(self):
        """Test that nested function definitions don't leak their bindings."""
        code = """
def helper(x):
    return x * 2
result = helper(5)
"""
        tree = ast.parse(code)

        self.analyzer.analyze(tree)
        free_vars = self.analyzer.get_free_variables(tree.body)

        # helper is defined locally, no free variables
        self.assertEqual(len(free_vars), 0, "Should have no free variables")

    def test_free_variables_with_global(self):
        """Test free variables with global declaration."""
        code = """
global x
x = 1
"""
        tree = ast.parse(code)

        self.analyzer.analyze(tree)
        free_vars = self.analyzer.get_free_variables(tree.body)

        # Global assignments are uses, not bindings
        self.assertIn("x", free_vars, "Global variable should be treated as free")

    def test_free_variables_builtin_filtered(self):
        """Test that Python builtins are filtered from free variables."""
        code = "result = len([1, 2, 3])"
        tree = ast.parse(code)

        self.analyzer.analyze(tree)
        free_vars = self.analyzer.get_free_variables(tree.body)

        self.assertNotIn("len", free_vars, "Builtins should be filtered out")


class TestComprehensions(unittest.TestCase):
    """Test analyzing comprehensions."""

    def setUp(self):
        """Set up test fixtures."""
        self.analyzer = ScopeAnalyzer()

    def test_list_comprehension_scope(self):
        """Test that list comprehension variables are tracked by analyzer."""
        code = "result = [x * 2 for x in range(10)]"
        tree = ast.parse(code)

        scope = self.analyzer.analyze(tree)

        # NOTE: The ScopeAnalyzer walks the entire AST and tracks bindings.
        # Comprehensions create separate scopes in get_free_variables but not in analyze()
        self.assertIn("result", scope.bindings, "Result variable should be bound")

    def test_list_comprehension_free_variables(self):
        """Test free variables in list comprehensions."""
        code = "result = [x * factor for x in range(10)]"
        tree = ast.parse(code)

        self.analyzer.analyze(tree)
        free_vars = self.analyzer.get_free_variables(tree.body)

        # factor is used but not defined, so it's free
        self.assertIn("factor", free_vars, "factor should be a free variable")


class TestImports(unittest.TestCase):
    """Test analyzing import statements."""

    def setUp(self):
        """Set up test fixtures."""
        self.analyzer = ScopeAnalyzer()

    def test_import_statement(self):
        """Test simple import statement."""
        code = "import os"
        tree = ast.parse(code)

        self.analyzer.analyze(tree)
        free_vars = self.analyzer.get_free_variables(tree.body)

        # os should be bound, not free
        self.assertEqual(len(free_vars), 0, "Import should bind the module name")

    def test_import_with_alias(self):
        """Test import with alias."""
        code = "import os as operating_system"
        tree = ast.parse(code)

        self.analyzer.analyze(tree)
        free_vars = self.analyzer.get_free_variables(tree.body)

        # operating_system should be bound
        self.assertEqual(len(free_vars), 0, "Import alias should be bound")

    def test_from_import(self):
        """Test from ... import statement."""
        code = "from os import path"
        tree = ast.parse(code)

        self.analyzer.analyze(tree)
        free_vars = self.analyzer.get_free_variables(tree.body)

        # path should be bound
        self.assertEqual(len(free_vars), 0, "From import should bind the name")


class TestWalrusOperator(unittest.TestCase):
    """Test analyzing walrus operator (named expressions)."""

    def setUp(self):
        """Set up test fixtures."""
        self.analyzer = ScopeAnalyzer()

    def test_walrus_operator(self):
        """Test walrus operator creates binding."""
        code = "if (n := len(data)) > 10: pass"
        tree = ast.parse(code)

        scope = self.analyzer.analyze(tree)

        # Walrus creates binding in current scope
        self.assertIn("n", scope.bindings, "Walrus operator should create binding")


class TestEdgeCases(unittest.TestCase):
    """Test edge cases and boundary conditions."""

    def setUp(self):
        """Set up test fixtures."""
        self.analyzer = ScopeAnalyzer()

    def test_empty_code(self):
        """Test analyzing empty code."""
        code = ""
        tree = ast.parse(code)

        scope = self.analyzer.analyze(tree)

        self.assertIsNotNone(scope, "Should create scope even for empty code")
        self.assertEqual(len(scope.bindings), 0, "Empty code should have no bindings")

    def test_async_function(self):
        """Test analyzing async function."""
        code = "async def foo(): pass"
        tree = ast.parse(code)

        scope = self.analyzer.analyze(tree)

        self.assertIn("foo", scope.bindings, "Async function name should be bound")

    def test_async_for(self):
        """Test analyzing async for loop."""
        code = """
async def foo():
    async for item in async_iter:
        pass
"""
        tree = ast.parse(code)
        func_def = tree.body[0]

        scope = self.analyzer.analyze(tree)

        func_scope = self.analyzer.node_scopes[func_def]
        self.assertIn("item", func_scope.bindings, "Async for variable should be bound")

    def test_async_with(self):
        """Test analyzing async with statement."""
        code = """
async def foo():
    async with async_ctx() as ctx:
        pass
"""
        tree = ast.parse(code)
        func_def = tree.body[0]

        scope = self.analyzer.analyze(tree)

        func_scope = self.analyzer.node_scopes[func_def]
        self.assertIn("ctx", func_scope.bindings, "Async with variable should be bound")

    def test_exception_handler(self):
        """Test exception handler binding."""
        code = """
try:
    risky()
except ValueError as e:
    handle(e)
"""
        tree = ast.parse(code)

        self.analyzer.analyze(tree)
        free_vars = self.analyzer.get_free_variables(tree.body)

        # e is bound, risky and handle are free
        self.assertIn("risky", free_vars)
        self.assertIn("handle", free_vars)
        self.assertNotIn("e", free_vars, "Exception variable should be bound")


def main():
    """Run the tests."""
    unittest.main(verbosity=2)


if __name__ == "__main__":
    main()
