"""
Assignment analyzer for distinguishing initial bindings from reassignments.

This module analyzes assignment statements within a function to classify them as either:
- Initial bindings: First assignment to a variable (creates the variable)
- Reassignments: Subsequent assignments to an already-bound variable

This is critical for safe code extraction:
- Extracting code with an initial binding is safe
- Extracting code with a reassignment WITHOUT the initial binding is unsafe
"""

import ast
from typing import Dict, Set


def analyze_assignments(func: ast.FunctionDef) -> Dict[int, bool]:
    """
    Analyze assignments in a function to identify reassignments.

    Args:
        func: Function definition to analyze

    Returns:
        Dictionary mapping assignment node id to is_reassignment boolean
        - True: This assignment is a reassignment (variable was bound earlier)
        - False: This assignment is an initial binding (first assignment to variable)

    Example:
        def foo(x):
            result = x * 2      # Initial binding: id -> False
            result = result + 10  # Reassignment: id -> True
            return result
    """
    analyzer = AssignmentAnalyzer()
    analyzer.visit(func)
    return analyzer.reassignments


class AssignmentAnalyzer(ast.NodeVisitor):
    """
    Visitor that analyzes assignments to determine which are reassignments.

    Tracks which variables have been bound and marks assignments accordingly.
    Handles scoping correctly for nested functions, comprehensions, etc.
    """

    def __init__(self):
        self.bound_vars: Set[str] = set()
        self.reassignments: Dict[int, bool] = {}  # node id -> is_reassignment

    def visit_FunctionDef(self, node: ast.FunctionDef):
        """
        Visit function definition.

        For the top-level function being analyzed:
        - Parameters are considered bound variables
        - Visit the function body

        For nested functions:
        - Don't descend (they have their own scope)
        """
        # If this is the first function we're visiting, analyze it
        if not self.bound_vars:
            # Parameters are initially bound
            for arg in node.args.args:
                self.bound_vars.add(arg.arg)

            # Also handle keyword-only args, varargs, etc.
            if node.args.vararg:
                self.bound_vars.add(node.args.vararg.arg)
            if node.args.kwarg:
                self.bound_vars.add(node.args.kwarg.arg)
            for arg in node.args.posonlyargs:
                self.bound_vars.add(arg.arg)
            for arg in node.args.kwonlyargs:
                self.bound_vars.add(arg.arg)

            # Visit function body
            for stmt in node.body:
                self.visit(stmt)
        # else: Don't descend into nested functions (different scope)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        """Visit async function definition (same as FunctionDef)."""
        self.visit_FunctionDef(node)

    def visit_Assign(self, node: ast.Assign):
        """
        Visit assignment statement.

        For each target being assigned:
        - If already bound: mark as reassignment
        - If not bound: mark as initial binding and add to bound_vars
        """
        # Visit the RHS first (in case it has side effects on bound vars)
        self.visit(node.value)

        # Process each target
        for target in node.targets:
            if isinstance(target, ast.Name):
                # Simple variable assignment
                var_name = target.id

                # Check if this variable is already bound
                is_reassignment = var_name in self.bound_vars

                # Record the classification
                self.reassignments[id(node)] = is_reassignment

                # Mark variable as bound for future assignments
                self.bound_vars.add(var_name)
            else:
                # Complex target (tuple unpacking, subscript, attribute)
                # Collect any Name nodes being assigned to
                names = self._collect_assignment_names(target)

                # Check if ANY of the names are reassignments
                is_any_reassignment = any(name in self.bound_vars for name in names)
                self.reassignments[id(node)] = is_any_reassignment

                # Mark all names as bound
                self.bound_vars.update(names)

    def visit_AugAssign(self, node: ast.AugAssign):
        """
        Visit augmented assignment (+=, -=, etc.).

        Augmented assignments are ALWAYS reassignments because they read
        the variable before writing it.
        """
        # The target must already be bound (or it's a runtime error)
        # Mark as reassignment
        self.reassignments[id(node)] = True

        # Visit children
        self.visit(node.target)
        self.visit(node.value)

    def visit_For(self, node: ast.For):
        """
        Visit for loop.

        The loop variable is bound by the for statement.
        """
        # Visit the iterable first
        self.visit(node.iter)

        # The loop target creates bindings
        if isinstance(node.target, ast.Name):
            var_name = node.target.id
            # This is an initial binding (for loop creates the variable)
            # Note: We don't add a reassignments entry here because
            # for loop targets are handled specially
            self.bound_vars.add(var_name)
        else:
            # Complex target (tuple unpacking)
            names = self._collect_assignment_names(node.target)
            self.bound_vars.update(names)

        # Visit loop body
        for stmt in node.body:
            self.visit(stmt)

        # Visit else clause if present
        for stmt in node.orelse:
            self.visit(stmt)

    def visit_With(self, node: ast.With):
        """
        Visit with statement.

        The 'as' clause creates bindings.
        """
        # Visit context expressions
        for item in node.items:
            self.visit(item.context_expr)

            # The 'as' clause creates a binding
            if item.optional_vars:
                if isinstance(item.optional_vars, ast.Name):
                    self.bound_vars.add(item.optional_vars.id)
                else:
                    names = self._collect_assignment_names(item.optional_vars)
                    self.bound_vars.update(names)

        # Visit body
        for stmt in node.body:
            self.visit(stmt)

    def visit_comprehension(self, node: ast.comprehension):
        """
        Visit comprehension (in list/dict/set comprehension or generator).

        Don't descend - comprehensions have their own scope.
        """
        # Don't analyze comprehension targets as they create their own scope
        pass

    def visit_ListComp(self, node: ast.ListComp):
        """Don't descend into list comprehensions (own scope)."""
        pass

    def visit_DictComp(self, node: ast.DictComp):
        """Don't descend into dict comprehensions (own scope)."""
        pass

    def visit_SetComp(self, node: ast.SetComp):
        """Don't descend into set comprehensions (own scope)."""
        pass

    def visit_GeneratorExp(self, node: ast.GeneratorExp):
        """Don't descend into generator expressions (own scope)."""
        pass

    def _collect_assignment_names(self, target: ast.AST) -> Set[str]:
        """
        Collect all Name nodes being assigned to in a complex target.

        Examples:
        - (a, b) = ... -> {'a', 'b'}
        - [x, y, z] = ... -> {'x', 'y', 'z'}
        - obj.attr = ... -> set()  # Not a variable binding
        - lst[i] = ... -> set()  # Not a variable binding
        """
        names = set()

        class NameCollector(ast.NodeVisitor):
            def visit_Name(self, node):
                if isinstance(node.ctx, ast.Store):
                    names.add(node.id)

        collector = NameCollector()
        collector.visit(target)
        return names


def has_reassignments_without_bindings(
    func: ast.FunctionDef,
    block_nodes: list[ast.AST],
    reassignments: Dict[int, bool]
) -> tuple[bool, Set[str]]:
    """
    Check if a code block contains reassignments without initial bindings.

    This is the validation function for safe extraction. A block is unsafe
    to extract if it contains a reassignment to a variable that was initially
    bound outside the block.

    Args:
        func: The function containing the block
        block_nodes: The block being considered for extraction
        reassignments: Assignment classification from analyze_assignments()

    Returns:
        Tuple of (has_unsafe_reassignments, set of problematic variable names)
        - has_unsafe_reassignments: True if block is unsafe to extract
        - problematic variables: Names of variables with reassignments but no bindings in block

    Example:
        def foo(x):
            result = x * 2           # Line 2: initial binding
            if result > 10:          # Block starts here (line 3)
                return result
            result = result + 10     # Line 5: reassignment
            return result            # Block ends here

        If we try to extract lines 3-6:
        - Returns (True, {'result'}) because 'result' is reassigned on line 5
          but initially bound on line 2 (outside the block)
    """
    # Collect all variables bound within the block
    bound_in_block = set()

    # Collect all variables reassigned within the block
    reassigned_in_block = set()

    for node in block_nodes:
        _collect_bindings_and_reassignments(
            node,
            reassignments,
            bound_in_block,
            reassigned_in_block
        )

    # Find variables that are reassigned but not initially bound in the block
    problematic_vars = reassigned_in_block - bound_in_block

    return (len(problematic_vars) > 0, problematic_vars)


def _collect_bindings_and_reassignments(
    node: ast.AST,
    reassignments: Dict[int, bool],
    bound_vars: Set[str],
    reassigned_vars: Set[str]
):
    """
    Recursively collect variables bound and reassigned in a node.

    Args:
        node: AST node to analyze
        reassignments: Assignment classification mapping
        bound_vars: Set to add initially-bound variables to
        reassigned_vars: Set to add reassigned variables to
    """
    class BindingCollector(ast.NodeVisitor):
        def visit_Assign(self, node):
            is_reassignment = reassignments.get(id(node), False)

            for target in node.targets:
                if isinstance(target, ast.Name):
                    var_name = target.id
                    if is_reassignment:
                        reassigned_vars.add(var_name)
                    else:
                        bound_vars.add(var_name)

            self.generic_visit(node)

        def visit_AugAssign(self, node):
            # Augmented assignments are always reassignments
            if isinstance(node.target, ast.Name):
                reassigned_vars.add(node.target.id)
            self.generic_visit(node)

        def visit_For(self, node):
            # For loop variables are initial bindings
            if isinstance(node.target, ast.Name):
                bound_vars.add(node.target.id)
            else:
                # Complex target
                class NameCollector(ast.NodeVisitor):
                    def visit_Name(self, n):
                        if isinstance(n.ctx, ast.Store):
                            bound_vars.add(n.id)
                collector = NameCollector()
                collector.visit(node.target)

            self.generic_visit(node)

        def visit_With(self, node):
            # With statement 'as' clauses create bindings
            for item in node.items:
                if item.optional_vars:
                    if isinstance(item.optional_vars, ast.Name):
                        bound_vars.add(item.optional_vars.id)
            self.generic_visit(node)

        def visit_FunctionDef(self, node):
            # Don't descend into nested functions
            pass

        def visit_AsyncFunctionDef(self, node):
            # Don't descend into nested async functions
            pass

    collector = BindingCollector()
    collector.visit(node)
