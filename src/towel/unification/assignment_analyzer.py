# Copyright 2025 Eric Allen
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
from typing import Dict, List, Set, Tuple, Union


def analyze_assignments(func: Union[ast.FunctionDef, ast.AsyncFunctionDef]) -> Dict[int, bool]:
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

    def __init__(self) -> None:
        self.bound_vars: Set[str] = set()
        self.reassignments: Dict[int, bool] = {}  # node id -> is_reassignment

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
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
            _record_function_parameters(node.args, self.bound_vars)
            _record_kwonly_parameters(node.args, self.bound_vars)

            # Visit function body
            for stmt in node.body:
                self.visit(stmt)
        # else: Don't descend into nested functions (different scope)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Visit async function definition (same as FunctionDef)."""
        # Type ignore needed because mypy doesn't recognize structural compatibility
        self.visit_FunctionDef(node)  # type: ignore[arg-type]

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

    def visit_ListComp(self, node: ast.ListComp) -> None:
        """Don't descend into list comprehensions (own scope)."""
        pass

    def visit_DictComp(self, node: ast.DictComp) -> None:
        """Don't descend into dict comprehensions (own scope)."""
        pass

    def visit_SetComp(self, node: ast.SetComp) -> None:
        """Don't descend into set comprehensions (own scope)."""
        pass

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
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
        names: Set[str] = set()

        class NameCollector(ast.NodeVisitor):
            def visit_Name(self, node: ast.Name) -> None:
                if isinstance(node.ctx, ast.Store):
                    names.add(node.id)

        collector = NameCollector()
        collector.visit(target)
        return names


def has_reassignments_without_bindings(
    func: Union[ast.FunctionDef, ast.AsyncFunctionDef],
    block_nodes: List[ast.AST],
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

    class BindingCollector(ast.NodeVisitor):
        def visit_Assign(self, node: ast.Assign) -> None:
            is_reassignment = reassignments.get(id(node), False)

            for target in node.targets:
                if isinstance(target, ast.Name):
                    var_name = target.id
                    if is_reassignment:
                        reassigned_vars.add(var_name)
                    else:
                        bound_vars.add(var_name)

            self.generic_visit(node)

        def visit_AugAssign(self, node: ast.AugAssign) -> None:
            # Augmented assignments are always reassignments
            _add_augassign_target(node.target, reassigned_vars)
            self.generic_visit(node)

        def visit_For(self, node: ast.For) -> None:
            # For loop variables are initial bindings
            if isinstance(node.target, ast.Name):
                bound_vars.add(node.target.id)
            else:
                # Complex target
                class NameCollector(ast.NodeVisitor):
                    def visit_Name(self, n: ast.Name) -> None:
                        if isinstance(n.ctx, ast.Store):
                            bound_vars.add(n.id)

                collector = NameCollector()
                collector.visit(node.target)

            self.generic_visit(node)

        def visit_With(self, node: ast.With) -> None:
            # With statement 'as' clauses create bindings
            for item in node.items:
                if item.optional_vars:
                    if isinstance(item.optional_vars, ast.Name):
                        bound_vars.add(item.optional_vars.id)
            self.generic_visit(node)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            # Don't descend into nested functions
            pass

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            # Don't descend into nested async functions
            pass

    collector = BindingCollector()
    collector.visit(node)


def _collect_block_binding_stats(
    block_nodes: List[ast.AST], reassignments: Dict[int, bool]
) -> Tuple[Set[str], Set[str]]:
    """Return (bound_in_block, reassigned_in_block) for the given nodes."""
    bound_in_block: Set[str] = set()
    reassigned_in_block: Set[str] = set()
    for node in block_nodes:
        _collect_bindings_and_reassignments(
            node, reassignments, bound_in_block, reassigned_in_block
        )
    return bound_in_block, reassigned_in_block


def _record_function_parameters(args: ast.arguments, target: Set[str]) -> None:
    """Add positional, vararg, and kwarg parameters to ``target``."""
    for arg in args.args:
        target.add(arg.arg)
    if args.vararg:
        target.add(args.vararg.arg)
    if args.kwarg:
        target.add(args.kwarg.arg)


def _record_kwonly_parameters(args: ast.arguments, target: Set[str]) -> None:
    """Add positional-only and keyword-only parameters to ``target``."""
    for arg in args.posonlyargs:
        target.add(arg.arg)
    for arg in args.kwonlyargs:
        target.add(arg.arg)


def _add_augassign_target(target: ast.AST, reassigned_vars: Set[str]) -> None:
    """Track Name targets that appear on the LHS of an augmented assignment."""
    if isinstance(target, ast.Name):
        reassigned_vars.add(target.id)


def _visit_body_and_orelse(  # pragma: no cover - exercised via AssignmentAnalyzer
    visitor: ast.NodeVisitor, node: ast.AST
) -> None:
    for stmt in getattr(node, "body", []):
        visitor.visit(stmt)
    for stmt in getattr(node, "orelse", []):
        visitor.visit(stmt)
