# Copyright 2025-2026 Eric Allen
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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
from typing import Dict, Sequence, Set, Tuple, Union

from .scope_analyzer import pattern_capture_names
from .statement_facts import import_binding_names
from .models import FunctionNode
from .parameters import parameter_names
from .visitors import OwnScopeVisitor


def analyze_assignments(func: FunctionNode) -> Dict[int, bool]:
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
    analyzer = _AssignmentAnalyzer()
    analyzer.visit(func)
    return analyzer.reassignments


class _AssignmentAnalyzer(OwnScopeVisitor):
    """
    Visitor that analyzes assignments to determine which are reassignments.

    Tracks which variables have been bound and marks assignments accordingly.
    Handles scoping correctly for nested functions, comprehensions, etc.
    """

    def __init__(self) -> None:
        self.bound_vars: Set[str] = set()
        self.reassignments: Dict[int, bool] = {}  # node id -> is_reassignment

    def _nested_function(self, node: FunctionNode) -> None:
        """The first function seen is the one analyzed: its parameters are bound, its body visited.

        A function nested inside it is another scope and is not entered.
        """
        if not self.bound_vars:
            self.bound_vars.update(parameter_names(node.args))
            for stmt in node.body:
                self.visit(stmt)

    def visit_Assign(self, node: ast.Assign) -> None:
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

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        """An annotated assignment with a value binds its target like ``Assign``."""
        if node.value is None:
            return
        self.visit(node.value)
        if isinstance(node.target, ast.Name):
            self.reassignments[id(node)] = node.target.id in self.bound_vars
            self.bound_vars.add(node.target.id)

    def visit_Import(self, node: ast.Import) -> None:
        """An import binds each alias, or the first component of a dotted name."""
        self._bind_import_aliases(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self._bind_import_aliases(node)

    def _bind_import_aliases(self, node: Union[ast.Import, ast.ImportFrom]) -> None:
        names = import_binding_names(node)
        self.reassignments[id(node)] = any(name in self.bound_vars for name in names)
        self.bound_vars.update(names)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        """An assignment expression binds its target in the enclosing function."""
        self.visit(node.value)
        self.reassignments[id(node)] = node.target.id in self.bound_vars
        self.bound_vars.add(node.target.id)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
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

    def visit_For(self, node: ast.For) -> None:
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
        _visit_body_and_orelse(self, node)

    def visit_With(self, node: ast.With) -> None:
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

    def visit_comprehension(self, node: ast.comprehension) -> None:
        """
        Visit comprehension (in list/dict/set comprehension or generator).

        Don't descend - comprehensions have their own scope.
        """
        # Don't analyze comprehension targets as they create their own scope
        pass

    def _comprehension(
        self, node: Union[ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp]
    ) -> None:
        """A comprehension's targets are its own; nothing in it is an assignment of this scope."""

    def _collect_assignment_names(self, target: ast.AST) -> Set[str]:
        """
        Collect all Name nodes being assigned to in a complex target.

        Examples:
        - (a, b) = ... -> {'a', 'b'}
        - [x, y, z] = ... -> {'x', 'y', 'z'}
        - obj.attr = ... -> set()  # Not a variable binding
        - lst[i] = ... -> set()  # Not a variable binding
        """
        return stored_names(target)


def has_reassignments_without_bindings(
    func: FunctionNode,
    block_nodes: Sequence[ast.stmt],
    reassignments: Dict[int, bool],
) -> Tuple[bool, Set[str]]:
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
    bound_in_block, reassigned_in_block = _collect_block_binding_stats(block_nodes, reassignments)

    # Find variables that are reassigned but not initially bound in the block
    problematic_vars = reassigned_in_block - bound_in_block

    # Relaxation: allow reassignments to names declared global/nonlocal in the enclosing function
    declared_global: Set[str] = set()
    declared_nonlocal: Set[str] = set()

    for stmt in func.body:
        if isinstance(stmt, ast.Global):
            declared_global.update(stmt.names)
        elif isinstance(stmt, ast.Nonlocal):
            declared_nonlocal.update(stmt.names)

    allowed = declared_global | declared_nonlocal
    remaining = problematic_vars - allowed

    return (len(remaining) > 0, remaining)


def _collect_bindings_and_reassignments(
    node: ast.AST, reassignments: Dict[int, bool], bound_vars: Set[str], reassigned_vars: Set[str]
) -> None:
    """
    Recursively collect variables bound and reassigned in a node.

    Args:
        node: AST node to analyze
        reassignments: Assignment classification mapping
        bound_vars: Set to add initially-bound variables to
        reassigned_vars: Set to add reassigned variables to
    """
    _BindingCollector(reassignments, bound_vars, reassigned_vars).visit(node)


class _BindingCollector(OwnScopeVisitor):
    """Adds the names a node binds, and the ones it reassigns, to the caller's sets."""

    def __init__(
        self, reassignments: Dict[int, bool], bound_vars: Set[str], reassigned_vars: Set[str]
    ) -> None:
        self.reassignments = reassignments
        self.bound_vars = bound_vars
        self.reassigned_vars = reassigned_vars

    def _destination(self, node: ast.AST) -> Set[str]:
        return self.reassigned_vars if self.reassignments.get(id(node), False) else self.bound_vars

    def visit_Assign(self, node: ast.Assign) -> None:
        # A tuple or list target binds every name inside it (astroid:
        # ``frame, stmts = self.lookup(name)`` read after the block).
        destination = self._destination(node)
        for target in node.targets:
            destination.update(stored_names(target))
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        # An annotated assignment with a value binds its target; without a
        # value it only declares the annotation and binds nothing.
        if node.value is not None and isinstance(node.target, ast.Name):
            self._destination(node).add(node.target.id)
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self._destination(node).add(node.target.id)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        self._bind_import_aliases(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self._bind_import_aliases(node)

    def _bind_import_aliases(self, node: Union[ast.Import, ast.ImportFrom]) -> None:
        self._destination(node).update(import_binding_names(node))

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        # Augmented assignments are always reassignments
        _add_augassign_target(node.target, self.reassigned_vars)
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        # For loop variables are initial bindings
        self.bound_vars.update(stored_names(node.target))
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        # With statement 'as' clauses create bindings, including unpacked ones
        for item in node.items:
            if item.optional_vars:
                self.bound_vars.update(stored_names(item.optional_vars))
        self.generic_visit(node)

    def visit_Match(self, node: ast.Match) -> None:
        # Capture patterns are initial bindings of the enclosing function
        for case in node.cases:
            self.bound_vars.update(pattern_capture_names(case.pattern))
        self.generic_visit(node)


def stored_names(target: ast.AST) -> Set[str]:
    """Names an assignment target binds: a name, or every name inside a tuple, list or star."""
    return {
        node.id
        for node in ast.walk(target)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    }


def _collect_block_binding_stats(
    block_nodes: Sequence[ast.stmt], reassignments: Dict[int, bool]
) -> Tuple[Set[str], Set[str]]:
    """Return (bound_in_block, reassigned_in_block) for the given nodes."""
    bound_in_block: Set[str] = set()
    reassigned_in_block: Set[str] = set()
    for node in block_nodes:
        _collect_bindings_and_reassignments(
            node, reassignments, bound_in_block, reassigned_in_block
        )
    return bound_in_block, reassigned_in_block


def _add_augassign_target(target: ast.AST, reassigned_vars: Set[str]) -> None:
    """Track Name targets that appear on the LHS of an augmented assignment."""
    if isinstance(target, ast.Name):
        reassigned_vars.add(target.id)


def _visit_body_and_orelse(visitor: ast.NodeVisitor, node: ast.AST) -> None:
    for stmt in getattr(node, "body", []):
        visitor.visit(stmt)
    for stmt in getattr(node, "orelse", []):
        visitor.visit(stmt)
