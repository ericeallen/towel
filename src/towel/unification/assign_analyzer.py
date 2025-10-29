"""
Assignment Analyzer - Determine which Assign nodes are reassignments vs fresh bindings.

This module analyzes Assign nodes to determine whether they represent:
- Fresh bindings: `x = 5` when `x` is not in scope
- Reassignments: `x = x + 1` when `x` is already in scope

This distinction is crucial for unification because reassignments mutate existing
variables and should not be unified in the same way as fresh bindings.
"""

import ast
from typing import Dict, Set
from .visitor_utils import make_defensive_generic_visit


class AssignmentAnalyzer(ast.NodeVisitor):
    """
    Analyze Assign nodes to determine if they are reassignments.

    Returns a dict mapping Assign node IDs to booleans:
    - True: This assign is a reassignment (variable already in scope)
    - False: This assign is a fresh binding (variable not in scope)
    """

    generic_visit = make_defensive_generic_visit('AssignmentAnalyzer')

    def __init__(self):
        self.scopes: list[Set[str]] = [set()]  # Stack of scopes
        self.reassignments: Dict[int, bool] = {}  # Map node id() to is_reassignment

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Enter a new scope for function definitions."""
        # Add function parameters to scope
        new_scope = set()
        for arg in node.args.args:
            new_scope.add(arg.arg)

        self.scopes.append(new_scope)

        # Visit the body
        for stmt in node.body:
            self.visit(stmt)

        # Exit scope
        self.scopes.pop()

    def visit_Assign(self, node: ast.Assign) -> None:
        """
        Analyze an assignment to determine if it's a reassignment.

        An assignment is considered a reassignment if:
        - It has a single target
        - The target is a simple Name node
        - The target variable is already in scope
        """
        # Visit the value first (RHS)
        self.visit(node.value)

        # Now analyze the targets (LHS)
        for target in node.targets:
            if isinstance(target, ast.Name):
                var_name = target.id
                is_reassignment = self._is_in_scope(var_name)

                # Record whether this is a reassignment
                self.reassignments[id(node)] = is_reassignment

                # Add to current scope (for future assignments)
                self.scopes[-1].add(var_name)
            else:
                # Complex target (subscript, attribute, tuple, etc.)
                # Not a simple reassignment
                self.reassignments[id(node)] = False
                self.visit(target)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        """
        Augmented assignments are always reassignments.
        (Python enforces that the variable must already exist)
        """
        if isinstance(node.target, ast.Name):
            self.scopes[-1].add(node.target.id)

        self.reassignments[id(node)] = True
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        """Track loop variables."""
        if isinstance(node.target, ast.Name):
            self.scopes[-1].add(node.target.id)
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        """Track context manager variables."""
        for item in node.items:
            if item.optional_vars and isinstance(item.optional_vars, ast.Name):
                self.scopes[-1].add(item.optional_vars.id)
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        """Track exception handler variables."""
        if node.name:
            self.scopes[-1].add(node.name)
        self.generic_visit(node)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        """Track comprehension variables."""
        if isinstance(node.target, ast.Name):
            self.scopes[-1].add(node.target.id)
        self.generic_visit(node)

    def _is_in_scope(self, name: str) -> bool:
        """Check if a variable is in any of the current scopes."""
        for scope in self.scopes:
            if name in scope:
                return True
        return False


def analyze_assignments(tree: ast.AST) -> Dict[int, bool]:
    """
    Analyze all assignments in an AST to determine which are reassignments.

    Args:
        tree: The AST to analyze

    Returns:
        A dict mapping Assign node id() to is_reassignment boolean
    """
    analyzer = AssignmentAnalyzer()
    analyzer.visit(tree)
    return analyzer.reassignments


def is_reassignment(node: ast.Assign, reassignments: Dict[int, bool]) -> bool:
    """
    Check if an Assign node is a reassignment.

    Args:
        node: The Assign node to check
        reassignments: The dict returned by analyze_assignments()

    Returns:
        True if this is a reassignment, False if it's a fresh binding
    """
    return reassignments.get(id(node), False)
