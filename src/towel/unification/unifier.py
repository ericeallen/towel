"""
Unification algorithm for finding parameterizable differences in AST nodes.

This is based on Robinson's unification algorithm from automated theorem proving,
adapted for AST comparison.
"""

import ast
from typing import Dict, Optional, List, Tuple, Set
from dataclasses import dataclass, field


@dataclass
class Substitution:
    """
    Represents a substitution mapping from sub-expressions to parameter names.

    Each entry maps a (block_index, sub_expression) to a parameter name.
    """
    mappings: Dict[Tuple[int, str], str] = field(default_factory=dict)
    # Maps parameter names to the list of expressions they replace
    param_expressions: Dict[str, List[Tuple[int, ast.AST]]] = field(default_factory=dict)
    # Maps parameter names to list of bound variables they should take as function args
    # If a parameter is in this dict, it should be a function parameter
    function_params: Dict[str, List[str]] = field(default_factory=dict)

    def add_mapping(self, block_idx: int, expr: ast.AST, param_name: str, bound_vars: Optional[List[str]] = None):
        """
        Add a mapping from an expression to a parameter name.

        Args:
            block_idx: Block index
            expr: Expression being parameterized
            param_name: Name of the parameter
            bound_vars: List of bound variables the expression references (for function parameters)
        """
        expr_str = ast.unparse(expr)
        key = (block_idx, expr_str)
        self.mappings[key] = param_name

        if param_name not in self.param_expressions:
            self.param_expressions[param_name] = []
        self.param_expressions[param_name].append((block_idx, expr))

        # If this expression references bound variables, mark parameter as function
        if bound_vars:
            if param_name not in self.function_params:
                self.function_params[param_name] = bound_vars
            else:
                # Merge bound variables (should be same across all blocks)
                existing = set(self.function_params[param_name])
                new_vars = set(bound_vars)
                self.function_params[param_name] = sorted(existing | new_vars)

    def get_param_for_expr(self, block_idx: int, expr: ast.AST) -> Optional[str]:
        """Get the parameter name for an expression."""
        expr_str = ast.unparse(expr)
        return self.mappings.get((block_idx, expr_str))

    def is_function_param(self, param_name: str) -> bool:
        """Check if a parameter should be a function parameter."""
        return param_name in self.function_params

    def get_function_param_vars(self, param_name: str) -> List[str]:
        """Get the bound variables a function parameter should take."""
        return self.function_params.get(param_name, [])


def get_free_variables(expr: ast.AST) -> Set[str]:
    """
    Get all variables referenced in an expression (Load context only).

    Args:
        expr: Expression AST node

    Returns:
        Set of variable names referenced in the expression
    """
    class VarCollector(ast.NodeVisitor):
        def __init__(self):
            self.vars = set()

        def visit_Name(self, node):
            if isinstance(node.ctx, ast.Load):
                self.vars.add(node.id)
            self.generic_visit(node)

    collector = VarCollector()
    collector.visit(expr)
    return collector.vars


def get_bound_variables_in_context(node: ast.AST, target_expr: ast.AST) -> Set[str]:
    """
    Get variables that are bound in the context surrounding target_expr within node.

    This includes variables bound by:
    1. Enclosing for loops: 'for item in items: ... item ...'
    2. Enclosing comprehensions: '[item for item in items]'
    3. Enclosing lambdas: 'lambda x: x + 1'
    4. Assignments anywhere in the block: 'cleaned = x.strip(); ... cleaned ...'

    For assignments, we use a simpler rule: if a variable is assigned anywhere in the
    block containing the expression, it's considered bound. This matches Python's
    scoping rules where assignment anywhere in a function makes a variable local.

    Args:
        node: Root AST node to search in
        target_expr: Expression we're looking for

    Returns:
        Set of variables bound in context surrounding target_expr
    """
    # Find bound variables by traversing the AST with a stack
    class BindingContextFinder(ast.NodeVisitor):
        def __init__(self, target):
            self.target = target
            self.target_str = ast.unparse(target)
            self.bound_vars = set()
            self.found_target = False
            # Stack of currently bound variables (for control structures)
            self.binding_stack = []
            # Accumulated assignments (persist for rest of block)
            self.assignments = set()

        def visit_For(self, node):
            # Check if target is in this for loop
            if self._contains_target(node):
                # Extract loop variable(s)
                loop_vars = self._get_binding_vars(node.target)
                # Push binding context
                self.binding_stack.append(loop_vars)
                self.generic_visit(node)
                self.binding_stack.pop()
            else:
                self.generic_visit(node)

        def visit_comprehension(self, node):
            # comprehension node (part of generators list in ListComp, etc.)
            if self._contains_target(node):
                comp_vars = self._get_binding_vars(node.target)
                self.binding_stack.append(comp_vars)
                self.generic_visit(node)
                self.binding_stack.pop()
            else:
                self.generic_visit(node)

        def visit_ListComp(self, node):
            # CRITICAL: Comprehension variables must be bound when visiting elt
            # [r.get_value() for r in results] - 'r' must be bound before visiting r.get_value()
            if self._contains_target(node):
                # Collect all comprehension variables from generators
                for gen in node.generators:
                    comp_vars = self._get_binding_vars(gen.target)
                    self.binding_stack.append(comp_vars)

                # Visit generators (for iter and ifs)
                for gen in node.generators:
                    self.visit(gen.iter)
                    for if_clause in gen.ifs:
                        self.visit(if_clause)

                # Visit the element expression with comprehension vars bound
                self.visit(node.elt)

                # Pop bindings
                for _ in node.generators:
                    self.binding_stack.pop()
            else:
                self.generic_visit(node)

        def visit_SetComp(self, node):
            # CRITICAL: Comprehension variables must be bound when visiting elt
            if self._contains_target(node):
                for gen in node.generators:
                    comp_vars = self._get_binding_vars(gen.target)
                    self.binding_stack.append(comp_vars)

                for gen in node.generators:
                    self.visit(gen.iter)
                    for if_clause in gen.ifs:
                        self.visit(if_clause)

                self.visit(node.elt)

                for _ in node.generators:
                    self.binding_stack.pop()
            else:
                self.generic_visit(node)

        def visit_DictComp(self, node):
            # CRITICAL: Comprehension variables must be bound when visiting key and value
            if self._contains_target(node):
                for gen in node.generators:
                    comp_vars = self._get_binding_vars(gen.target)
                    self.binding_stack.append(comp_vars)

                for gen in node.generators:
                    self.visit(gen.iter)
                    for if_clause in gen.ifs:
                        self.visit(if_clause)

                self.visit(node.key)
                self.visit(node.value)

                for _ in node.generators:
                    self.binding_stack.pop()
            else:
                self.generic_visit(node)

        def visit_GeneratorExp(self, node):
            # CRITICAL: Comprehension variables must be bound when visiting elt
            if self._contains_target(node):
                for gen in node.generators:
                    comp_vars = self._get_binding_vars(gen.target)
                    self.binding_stack.append(comp_vars)

                for gen in node.generators:
                    self.visit(gen.iter)
                    for if_clause in gen.ifs:
                        self.visit(if_clause)

                self.visit(node.elt)

                for _ in node.generators:
                    self.binding_stack.pop()
            else:
                self.generic_visit(node)

        def visit_FunctionDef(self, node):
            # Function creates a new scope - save current assignments and start fresh
            if self._contains_target(node):
                # Save current assignments
                saved_assignments = self.assignments.copy()
                # Function name is bound in outer scope
                self.assignments.add(node.name)

                # Push function name and parameters for the function body
                self.binding_stack.append({node.name})
                params = set()
                for arg in node.args.args:
                    params.add(arg.arg)
                for arg in node.args.posonlyargs:
                    params.add(arg.arg)
                for arg in node.args.kwonlyargs:
                    params.add(arg.arg)
                if node.args.vararg:
                    params.add(node.args.vararg.arg)
                if node.args.kwarg:
                    params.add(node.args.kwarg.arg)
                if params:
                    self.binding_stack.append(params)

                # Clear assignments for function body (fresh scope)
                self.assignments = set()

                # Visit body
                for stmt in node.body:
                    self.visit(stmt)

                # Restore outer scope assignments
                self.assignments = saved_assignments

                if params:
                    self.binding_stack.pop()
                self.binding_stack.pop()
            else:
                # Target not in function - function name is bound in outer scope
                self.assignments.add(node.name)

        def visit_AsyncFunctionDef(self, node):
            # Same as FunctionDef
            self.visit_FunctionDef(node)

        def visit_ClassDef(self, node):
            # Class creates a new scope - save current assignments and start fresh
            if self._contains_target(node):
                # Save current assignments
                saved_assignments = self.assignments.copy()
                # Class name is bound in outer scope
                self.assignments.add(node.name)

                self.binding_stack.append({node.name})

                # Clear assignments for class body (fresh scope)
                self.assignments = set()

                # Visit body
                for stmt in node.body:
                    self.visit(stmt)

                # Restore outer scope assignments
                self.assignments = saved_assignments

                self.binding_stack.pop()
            else:
                # Target not in class - class name is bound in outer scope
                self.assignments.add(node.name)

        def visit_Lambda(self, node):
            # Lambda creates a new scope for its parameters
            if self._contains_target(node):
                lambda_vars = {arg.arg for arg in node.args.args}
                if lambda_vars:
                    self.binding_stack.append(lambda_vars)
                self.visit(node.body)
                if lambda_vars:
                    self.binding_stack.pop()

        def visit_Assign(self, node):
            # CRITICAL: Assignments create bindings that persist for the rest of the block
            # We accumulate ALL assignments as we traverse (not just those containing target)
            # Extract assigned variable(s)
            for target in node.targets:
                self.assignments.update(self._get_binding_vars(target))
            self.generic_visit(node)

        def visit_AugAssign(self, node):
            # Augmented assignments also create persistent bindings
            self.assignments.update(self._get_binding_vars(node.target))
            self.generic_visit(node)

        def visit_AnnAssign(self, node):
            # Annotated assignments create persistent bindings
            self.assignments.update(self._get_binding_vars(node.target))
            self.generic_visit(node)

        def visit_With(self, node):
            # Handle with statements: with open(f) as file: ...
            if self._contains_target(node):
                with_vars = set()
                for item in node.items:
                    if item.optional_vars:
                        with_vars.update(self._get_binding_vars(item.optional_vars))
                if with_vars:
                    self.binding_stack.append(with_vars)
                    self.generic_visit(node)
                    self.binding_stack.pop()
                else:
                    self.generic_visit(node)
            else:
                self.generic_visit(node)

        def visit_ExceptHandler(self, node):
            # Handle exception handlers: except Exception as e: ...
            if self._contains_target(node):
                if node.name:
                    # Exception variable is bound
                    self.binding_stack.append({node.name})
                    self.generic_visit(node)
                    self.binding_stack.pop()
                else:
                    self.generic_visit(node)
            else:
                self.generic_visit(node)

        def visit_NamedExpr(self, node):
            # Handle walrus operator: if (x := foo()): ...
            if self._contains_target(node):
                # The target of := is a binding
                named_vars = self._get_binding_vars(node.target)
                self.binding_stack.append(named_vars)
                self.generic_visit(node)
                self.binding_stack.pop()
            else:
                self.generic_visit(node)

        def generic_visit(self, node):
            # Check if this node matches target
            if ast.unparse(node) == self.target_str:
                self.found_target = True
                # Collect all currently bound variables
                # (from both control structures and assignments)
                for bound_set in self.binding_stack:
                    self.bound_vars.update(bound_set)
                self.bound_vars.update(self.assignments)
            super().generic_visit(node)

        def _contains_target(self, node) -> bool:
            """Check if node contains the target expression."""
            return self.target_str in ast.unparse(node)

        def _get_binding_vars(self, target) -> Set[str]:
            """Extract variable names from a binding target (Name, Tuple, etc.)."""
            if isinstance(target, ast.Name):
                return {target.id}
            elif isinstance(target, (ast.Tuple, ast.List)):
                vars = set()
                for elt in target.elts:
                    vars.update(self._get_binding_vars(elt))
                return vars
            else:
                return set()

    finder = BindingContextFinder(target_expr)
    finder.visit(node)

    # Filter to only include variables that are actually referenced in the target expression
    vars_in_expr = get_free_variables(target_expr)
    result = finder.bound_vars & vars_in_expr

    return result


class Unifier:
    """
    Unify AST blocks to find parameterizable differences.

    This finds sub-expressions that differ between blocks and can be
    factored out into function parameters.

    Implements alpha-renaming for bound variables (loop vars, etc.).
    """

    def __init__(self, max_parameters: int = 5, parameterize_constants: bool = True):
        """
        Initialize unifier.

        Args:
            max_parameters: Maximum number of parameters to extract
            parameterize_constants: Whether to parameterize differing constants
        """
        self.max_parameters = max_parameters
        self.parameterize_constants = parameterize_constants
        self.param_counter = 0
        # Track alpha-equivalence mappings for bound variables
        # Maps (block_idx, original_name) -> canonical_name
        self.alpha_renamings: Dict[Tuple[int, str], str] = {}
        # Store blocks being unified for context analysis
        self.current_blocks: Optional[List[List[ast.AST]]] = None
        # Track constant occurrences: (block_idx, value) -> [position_paths]
        # position_path is a tuple of (stmt_idx, field_name, ...) identifying location
        self.constant_positions: Dict[Tuple[int, any], List[Tuple]] = {}

    def unify_blocks(
        self,
        blocks: List[List[ast.AST]],
        hygienic_renames: List[Dict[str, str]],
        reassignments_list: Optional[List[Dict[int, bool]]] = None
    ) -> Optional[Substitution]:
        """
        Unify multiple code blocks.

        Args:
            blocks: List of code blocks (each is a list of AST statements)
            hygienic_renames: For each block, a mapping from original names
                             to hygienically renamed names
            reassignments_list: For each block, a dict mapping Assign node id() to
                              is_reassignment boolean. This helps distinguish fresh
                              bindings from reassignments.

        Returns:
            Substitution mapping expressions to parameters, or None if unification fails
        """
        if len(blocks) < 2:
            return None

        # Check all blocks have the same number of statements
        if not all(len(b) == len(blocks[0]) for b in blocks):
            return None

        # Store blocks for context analysis
        self.current_blocks = blocks

        # Store reassignments for use during unification
        self.reassignments_list = reassignments_list if reassignments_list else [{} for _ in blocks]

        # Collect all constant positions for consistency checking
        self._collect_constant_positions(blocks)

        # Initialize substitution
        subst = Substitution()

        # Unify statement by statement
        for stmt_idx in range(len(blocks[0])):
            stmts = [block[stmt_idx] for block in blocks]

            # Unify this statement across all blocks
            if not self._unify_nodes(stmts, subst, list(range(len(blocks)))):
                return None

        # Check we haven't exceeded max parameters
        if len(subst.param_expressions) > self.max_parameters:
            return None

        return subst

    def _unify_nodes(
        self,
        nodes: List[ast.AST],
        subst: Substitution,
        block_indices: List[int]
    ) -> bool:
        """
        Unify a list of AST nodes (one from each block).

        Args:
            nodes: List of nodes to unify
            subst: Current substitution
            block_indices: Block index for each node

        Returns:
            True if unification succeeded
        """
        # First check: do all nodes have the same type?
        node_types = [type(n) for n in nodes]
        if len(set(node_types)) != 1:
            # Different types - cannot unify at the statement level
            # Try to parameterize the entire expression
            return self._try_parameterize(nodes, subst, block_indices)

        first_node = nodes[0]

        # Special handling for different node types

        # Constants - check if they're identical, or parameterize if enabled
        if isinstance(first_node, ast.Constant):
            values = [n.value for n in nodes]
            if len(set(values)) == 1:
                return True  # All same constant

            # Different constants
            if self.parameterize_constants:
                # CRITICAL: Check consistency across all occurrences
                # Per the user's rule: if a constant value appears at multiple positions,
                # it must unify consistently at ALL positions.
                #
                # Example: "x = item * 2" vs "x = item * 3" AND "z = y ** 2" vs "z = y ** 2"
                # The constant 2 appears at 2 positions in block 0
                # Position 1: differs (2 vs 3)
                # Position 2: same (2 vs 2)
                # This is INCONSISTENT - we cannot parameterize just the constant 2
                #
                # Solution: Check if all occurrences of these values would unify consistently

                if not self._check_constant_consistency(values, block_indices):
                    # Constants appear at multiple positions with inconsistent unification
                    # Cannot parameterize the bare constant
                    return False

                # Parameterize differing constants
                return self._try_parameterize(nodes, subst, block_indices)
            else:
                # Cannot unify - constants must be identical
                return False

        # Names - if they differ, check alpha-renaming first
        if isinstance(first_node, ast.Name):
            # Apply alpha-renaming to get canonical names
            canonical_names = []
            for idx, (node, block_idx) in enumerate(zip(nodes, block_indices)):
                name = node.id
                # Check if this name has an alpha-renaming for this block
                renamed = self.alpha_renamings.get((block_idx, name), name)
                canonical_names.append(renamed)

            # Check if all canonical names are the same
            if len(set(canonical_names)) == 1:
                return True  # All same name (possibly after alpha-renaming)

            # Different names even after alpha-renaming - parameterize
            return self._try_parameterize(nodes, subst, block_indices)

        # For compound nodes, recursively unify all fields
        return self._unify_compound_node(nodes, subst, block_indices)

    def _unify_compound_node(
        self,
        nodes: List[ast.AST],
        subst: Substitution,
        block_indices: List[int]
    ) -> bool:
        """
        Unify compound AST nodes by recursively unifying their fields.

        Args:
            nodes: List of nodes (all same type)
            subst: Current substitution
            block_indices: Block indices

        Returns:
            True if unification succeeded
        """
        first_node = nodes[0]

        # Special handling for binding constructs
        # Loop variables, comprehension variables, etc. are BOUND by the construct
        # If they differ (like 'i' vs 'j'), they're alpha-equivalent, not parameterizable
        if isinstance(first_node, ast.For):
            # For loops: the target variable is bound
            # It can differ between blocks (like 'i' vs 'j') and that's OK
            # We just need to unify the structure, not the variable name
            return self._unify_for_loop(nodes, subst, block_indices)

        # Special handling for Lambda: parameters are bindings (alpha-renaming)
        # lambda x: x * 2 and lambda y: y * 2 are equivalent (alpha-equivalent)
        # The parameter names should NOT be parameterized
        if isinstance(first_node, ast.Lambda):
            return self._unify_lambda(nodes, subst, block_indices)

        # Special handling for FunctionDef: function names are bindings (alpha-renaming)
        # def helper(x): return x * 2 and def processor(x): return x * 2
        # are equivalent if they differ only in function name (alpha-equivalent)
        # The function name is in scope within the body (to support recursion)
        if isinstance(first_node, ast.FunctionDef):
            return self._unify_functiondef(nodes, subst, block_indices)

        # Special handling for f-strings (JoinedStr)
        # F-string literal parts (Constant nodes) must NEVER be parameterized
        # Only the expressions inside FormattedValue can be parameterized
        if isinstance(first_node, ast.JoinedStr):
            return self._unify_joined_str(nodes, subst, block_indices)

        # For each field in the node
        for field_name in first_node._fields:
            # Skip location fields
            if field_name in ('lineno', 'col_offset', 'end_lineno', 'end_col_offset'):
                continue

            # Get field values from all nodes
            field_values = [getattr(n, field_name, None) for n in nodes]

            first_value = field_values[0]

            # Handle different value types
            if first_value is None:
                # All None - OK
                if not all(v is None for v in field_values):
                    # Some None, some not - can't unify
                    return False
                continue

            elif isinstance(first_value, list):
                # Lists of AST nodes or primitives
                # Check all are lists
                if not all(isinstance(v, list) for v in field_values):
                    # Mixed list/non-list - can't unify
                    return False
                if not self._unify_lists(field_values, subst, block_indices):
                    return False

            elif isinstance(first_value, ast.AST):
                # Single AST node(s)
                # Check all are AST nodes
                if not all(isinstance(v, ast.AST) for v in field_values):
                    # Mixed AST/non-AST - can't unify
                    return False

                # Special handling for operator nodes (no fields - just type markers)
                # Operators like Add, Sub, Not, etc. define the operation semantics
                # and should NOT be parameterized - they must be identical
                if len(first_value._fields) == 0:
                    # Operator node - all must have same type
                    value_types = set(type(v) for v in field_values)
                    if len(value_types) > 1:
                        # Different operators - can't unify
                        return False
                    # Same operator type - continue
                    continue

                # Regular AST nodes - _unify_nodes handles type differences via parameterization
                if not self._unify_nodes(field_values, subst, block_indices):
                    return False

            else:
                # Primitive value (string, int, etc.)
                # Check all are non-AST, non-list primitives
                if any(isinstance(v, (ast.AST, list)) for v in field_values):
                    # Mixed primitive/AST or primitive/list - can't unify
                    return False
                # Must all be equal
                if not all(v == first_value for v in field_values):
                    # Try to parameterize the entire node if primitives differ
                    return self._try_parameterize(nodes, subst, block_indices)

        return True

    def _unify_for_loop(
        self,
        nodes: List[ast.For],
        subst: Substitution,
        block_indices: List[int]
    ) -> bool:
        """
        Unify For loops, treating loop variables as bound (alpha-equivalent).

        Args:
            nodes: List of For nodes
            subst: Current substitution
            block_indices: Block indices

        Returns:
            True if unification succeeded
        """
        # For loops have: target, iter, body, orelse
        # The 'target' is a bound variable - it can differ (i vs j) and that's OK

        # Get the loop variable names
        targets = [n.target for n in nodes]

        # Check if targets are simple Names or Tuples
        if not all(isinstance(t, ast.Name) for t in targets):
            # Check if all targets are tuples (for tuple unpacking support)
            if all(isinstance(t, ast.Tuple) for t in targets):
                # Delegate to tuple unpacking handler
                return self._unify_for_loop_with_tuple_targets(nodes, subst, block_indices)

            # Complex targets (nested structures, etc.) - fall back to default unification
            return self._unify_nodes(targets, subst, block_indices) and \
                   self._unify_nodes([n.iter for n in nodes], subst, block_indices) and \
                   self._unify_lists([n.body for n in nodes], subst, block_indices) and \
                   self._unify_lists([n.orelse for n in nodes], subst, block_indices)

        loop_var_names = [t.id for t in targets]

        # Check if all loop variables have the same name
        if len(set(loop_var_names)) == 1:
            # Same loop variable name - just unify normally
            return self._unify_nodes([n.iter for n in nodes], subst, block_indices) and \
                   self._unify_lists([n.body for n in nodes], subst, block_indices) and \
                   self._unify_lists([n.orelse for n in nodes], subst, block_indices)

        # Different loop variable names (i vs j) - establish alpha-equivalence
        # Use the first block's variable name as canonical
        canonical_var = loop_var_names[0]

        # Establish alpha-renaming mappings for all blocks
        # Save old mappings to restore later
        old_mappings = {}
        for idx, block_idx in enumerate(block_indices):
            var_name = loop_var_names[idx]
            key = (block_idx, var_name)
            if key in self.alpha_renamings:
                old_mappings[key] = self.alpha_renamings[key]
            # Map this block's loop var to the canonical name
            self.alpha_renamings[key] = canonical_var

        try:
            # Unify the iterator
            if not self._unify_nodes([n.iter for n in nodes], subst, block_indices):
                return False

            # Unify the body (with alpha-renaming in effect)
            if not self._unify_lists([n.body for n in nodes], subst, block_indices):
                return False

            # Unify orelse
            if not self._unify_lists([n.orelse for n in nodes], subst, block_indices):
                return False

            return True

        finally:
            # Restore old mappings or remove new ones
            for idx, block_idx in enumerate(block_indices):
                var_name = loop_var_names[idx]
                key = (block_idx, var_name)
                if key in old_mappings:
                    self.alpha_renamings[key] = old_mappings[key]
                else:
                    self.alpha_renamings.pop(key, None)

    def _unify_for_loop_with_tuple_targets(
        self,
        nodes: List[ast.For],
        subst: Substitution,
        block_indices: List[int]
    ) -> bool:
        """
        Unify For loops with tuple unpacking targets (e.g., for key, value in items).

        Handles flat tuple unpacking with alpha-renaming:
        - for key, value in pairs
        - for k, v in pairs

        Args:
            nodes: List of For nodes with Tuple targets
            subst: Current substitution
            block_indices: Block indices

        Returns:
            True if unification succeeded
        """
        targets = [n.target for n in nodes]

        # Verify all targets are tuples
        if not all(isinstance(t, ast.Tuple) for t in targets):
            return False

        # Check all tuples have the same number of elements
        tuple_lengths = [len(t.elts) for t in targets]
        if len(set(tuple_lengths)) != 1:
            return False

        # Check all tuple elements are simple Name nodes
        for target in targets:
            if not all(isinstance(elt, ast.Name) for elt in target.elts):
                return False

        # Extract variable names for each position
        # var_names[position_idx] = [name_in_block0, name_in_block1, ...]
        num_positions = len(targets[0].elts)
        var_names = []
        for pos in range(num_positions):
            names_at_pos = [target.elts[pos].id for target in targets]
            var_names.append(names_at_pos)

        # Check if all corresponding names are identical
        # If so, no alpha-renaming needed
        all_same = all(len(set(names)) == 1 for names in var_names)

        if all_same:
            # All tuple unpacking uses same variable names - just unify normally
            return self._unify_nodes([n.iter for n in nodes], subst, block_indices) and \
                   self._unify_lists([n.body for n in nodes], subst, block_indices) and \
                   self._unify_lists([n.orelse for n in nodes], subst, block_indices)

        # Different variable names - establish alpha-equivalence for each position
        # Use the first block's variable names as canonical
        canonical_vars = [names[0] for names in var_names]

        # Establish alpha-renaming mappings for all positions and blocks
        # Save old mappings to restore later
        old_mappings = {}
        for pos_idx, canonical_var in enumerate(canonical_vars):
            for idx, block_idx in enumerate(block_indices):
                var_name = var_names[pos_idx][idx]
                key = (block_idx, var_name)
                if key in self.alpha_renamings:
                    old_mappings[key] = self.alpha_renamings[key]
                # Map this block's variable to the canonical name
                self.alpha_renamings[key] = canonical_var

        try:
            # Unify the iterator
            if not self._unify_nodes([n.iter for n in nodes], subst, block_indices):
                return False

            # Unify the body (with alpha-renaming in effect)
            if not self._unify_lists([n.body for n in nodes], subst, block_indices):
                return False

            # Unify orelse
            if not self._unify_lists([n.orelse for n in nodes], subst, block_indices):
                return False

            return True

        finally:
            # Restore old mappings or remove new ones
            for pos_idx, canonical_var in enumerate(canonical_vars):
                for idx, block_idx in enumerate(block_indices):
                    var_name = var_names[pos_idx][idx]
                    key = (block_idx, var_name)
                    if key in old_mappings:
                        self.alpha_renamings[key] = old_mappings[key]
                    else:
                        self.alpha_renamings.pop(key, None)

    def _unify_lambda(
        self,
        nodes: List[ast.Lambda],
        subst: Substitution,
        block_indices: List[int]
    ) -> bool:
        """
        Unify lambda expressions with alpha-renaming support.

        Lambda parameters are bindings, similar to for loop variables.
        If they differ (like 'lambda x: ...' vs 'lambda y: ...'), they're
        alpha-equivalent, not parameterizable.

        Args:
            nodes: List of Lambda nodes
            subst: Current substitution
            block_indices: Block indices

        Returns:
            True if unification succeeded
        """
        # For now, only handle simple case: same number of regular positional args
        # Get parameter counts for each lambda
        param_counts = [len(n.args.args) for n in nodes]
        if len(set(param_counts)) > 1:
            # Different number of parameters - can't unify
            return False

        # Check that all other parameter types are empty (no *args, **kwargs, etc.)
        for node in nodes:
            if (node.args.posonlyargs or node.args.kwonlyargs or
                node.args.vararg or node.args.kwarg):
                # Complex lambda parameters - for now, don't unify
                # TODO: Add full support for all parameter types
                return False

        # Get parameter names from each lambda
        num_params = param_counts[0]
        if num_params == 0:
            # No parameters - just unify bodies directly
            return self._unify_nodes([n.body for n in nodes], subst, block_indices)

        # Create alpha-renaming mappings for lambda parameters
        # Use first lambda's parameter names as canonical
        canonical_params = [nodes[0].args.args[i].arg for i in range(num_params)]

        # Save old alpha-renaming mappings (in case of nested lambdas)
        old_mappings = {}
        try:
            # Set up alpha-renamings for each parameter position
            for param_idx in range(num_params):
                canonical_param = canonical_params[param_idx]
                for idx, node in enumerate(nodes):
                    block_idx = block_indices[idx]
                    actual_param = node.args.args[param_idx].arg
                    key = (block_idx, actual_param)

                    # Save old mapping if it exists
                    if key in self.alpha_renamings:
                        old_mappings[key] = self.alpha_renamings[key]

                    # Set new mapping: actual_param -> canonical_param
                    self.alpha_renamings[key] = canonical_param

            # Unify lambda bodies with alpha-renaming in effect
            return self._unify_nodes([n.body for n in nodes], subst, block_indices)

        finally:
            # Restore old mappings or remove new ones
            for param_idx in range(num_params):
                for idx, node in enumerate(nodes):
                    block_idx = block_indices[idx]
                    actual_param = node.args.args[param_idx].arg
                    key = (block_idx, actual_param)
                    if key in old_mappings:
                        self.alpha_renamings[key] = old_mappings[key]
                    else:
                        self.alpha_renamings.pop(key, None)

    def _unify_functiondef(
        self,
        nodes: List[ast.FunctionDef],
        subst: Substitution,
        block_indices: List[int]
    ) -> bool:
        """
        Unify FunctionDef nodes with alpha-renaming support for function names.

        Nested function definitions are binding constructs where the function name
        is bound in the enclosing scope. If they differ (like 'helper' vs 'processor'),
        they're alpha-equivalent, not parameterizable.

        The function name is also in scope within the function body (to support recursion).

        Args:
            nodes: List of FunctionDef nodes
            subst: Current substitution
            block_indices: Block indices

        Returns:
            True if unification succeeded
        """
        # Get function names
        func_names = [n.name for n in nodes]

        # Check if all function names are the same
        if len(set(func_names)) == 1:
            # Same function name - just unify normally
            # Unify decorators
            if not self._unify_lists([n.decorator_list for n in nodes], subst, block_indices):
                return False
            # Unify arguments (with parameter alpha-renaming)
            if not self._unify_arguments([n.args for n in nodes], subst, block_indices):
                return False
            # Unify body
            if not self._unify_lists([n.body for n in nodes], subst, block_indices):
                return False
            # Unify return type annotation if present
            return_annotations = [n.returns for n in nodes]
            if return_annotations[0] is not None:
                if not all(ra is not None for ra in return_annotations):
                    return False
                if not self._unify_nodes(return_annotations, subst, block_indices):
                    return False
            elif any(ra is not None for ra in return_annotations):
                return False
            return True

        # Different function names - establish alpha-equivalence
        # Use the first block's function name as canonical
        canonical_name = func_names[0]

        # Establish alpha-renaming mappings for all blocks
        # NOTE: We don't clean these up because function names are scoped
        # to all subsequent statements in the same block, not just the function body.
        # The alpha-renaming needs to persist so that calls to helper() vs processor()
        # in later statements are correctly recognized as equivalent.
        for idx, block_idx in enumerate(block_indices):
            func_name = func_names[idx]
            key = (block_idx, func_name)
            # Map this block's function name to the canonical name
            self.alpha_renamings[key] = canonical_name

        # Unify decorators
        if not self._unify_lists([n.decorator_list for n in nodes], subst, block_indices):
            return False

        # Unify arguments (with parameter alpha-renaming handled recursively)
        if not self._unify_arguments([n.args for n in nodes], subst, block_indices):
            return False

        # Unify body (with alpha-renaming in effect)
        if not self._unify_lists([n.body for n in nodes], subst, block_indices):
            return False

        # Unify return type annotation if present
        return_annotations = [n.returns for n in nodes]
        if return_annotations[0] is not None:
            if not all(ra is not None for ra in return_annotations):
                return False
            if not self._unify_nodes(return_annotations, subst, block_indices):
                return False
        elif any(ra is not None for ra in return_annotations):
            return False

        return True

    def _unify_arguments(
        self,
        args_nodes: List[ast.arguments],
        subst: Substitution,
        block_indices: List[int]
    ) -> bool:
        """
        Unify function arguments with alpha-renaming support for parameter names.

        Function parameters are bindings and can differ (like 'x' vs 'y').
        They're alpha-equivalent, not parameterizable.

        Args:
            args_nodes: List of ast.arguments nodes
            subst: Current substitution
            block_indices: Block indices

        Returns:
            True if unification succeeded
        """
        # Check all have the same number of each parameter type
        posonlyargs_counts = [len(a.posonlyargs) for a in args_nodes]
        args_counts = [len(a.args) for a in args_nodes]
        kwonlyargs_counts = [len(a.kwonlyargs) for a in args_nodes]
        has_vararg = [a.vararg is not None for a in args_nodes]
        has_kwarg = [a.kwarg is not None for a in args_nodes]

        if (len(set(posonlyargs_counts)) > 1 or
            len(set(args_counts)) > 1 or
            len(set(kwonlyargs_counts)) > 1 or
            len(set(has_vararg)) > 1 or
            len(set(has_kwarg)) > 1):
            # Different parameter structure - can't unify
            return False

        # Establish alpha-renaming for all parameters
        # Save old mappings
        old_mappings = {}

        try:
            # Handle positional-only args (Python 3.8+)
            for pos in range(posonlyargs_counts[0]):
                param_names = [args_nodes[i].posonlyargs[pos].arg for i in range(len(args_nodes))]
                canonical_name = param_names[0]
                for idx, block_idx in enumerate(block_indices):
                    param_name = param_names[idx]
                    key = (block_idx, param_name)
                    if key in self.alpha_renamings:
                        old_mappings[key] = self.alpha_renamings[key]
                    self.alpha_renamings[key] = canonical_name

            # Handle regular positional args
            for pos in range(args_counts[0]):
                param_names = [args_nodes[i].args[pos].arg for i in range(len(args_nodes))]
                canonical_name = param_names[0]
                for idx, block_idx in enumerate(block_indices):
                    param_name = param_names[idx]
                    key = (block_idx, param_name)
                    if key in self.alpha_renamings:
                        old_mappings[key] = self.alpha_renamings[key]
                    self.alpha_renamings[key] = canonical_name

            # Handle keyword-only args
            for pos in range(kwonlyargs_counts[0]):
                param_names = [args_nodes[i].kwonlyargs[pos].arg for i in range(len(args_nodes))]
                canonical_name = param_names[0]
                for idx, block_idx in enumerate(block_indices):
                    param_name = param_names[idx]
                    key = (block_idx, param_name)
                    if key in self.alpha_renamings:
                        old_mappings[key] = self.alpha_renamings[key]
                    self.alpha_renamings[key] = canonical_name

            # Handle *args
            if has_vararg[0]:
                vararg_names = [args_nodes[i].vararg.arg for i in range(len(args_nodes))]
                canonical_name = vararg_names[0]
                for idx, block_idx in enumerate(block_indices):
                    vararg_name = vararg_names[idx]
                    key = (block_idx, vararg_name)
                    if key in self.alpha_renamings:
                        old_mappings[key] = self.alpha_renamings[key]
                    self.alpha_renamings[key] = canonical_name

            # Handle **kwargs
            if has_kwarg[0]:
                kwarg_names = [args_nodes[i].kwarg.arg for i in range(len(args_nodes))]
                canonical_name = kwarg_names[0]
                for idx, block_idx in enumerate(block_indices):
                    kwarg_name = kwarg_names[idx]
                    key = (block_idx, kwarg_name)
                    if key in self.alpha_renamings:
                        old_mappings[key] = self.alpha_renamings[key]
                    self.alpha_renamings[key] = canonical_name

            # Unify default values for positional args
            for pos in range(len(args_nodes[0].defaults)):
                defaults_at_pos = [a.defaults[pos] for a in args_nodes]
                if defaults_at_pos[0] is None:
                    if not all(d is None for d in defaults_at_pos):
                        return False
                else:
                    if not all(d is not None for d in defaults_at_pos):
                        return False
                    if not self._unify_nodes(defaults_at_pos, subst, block_indices):
                        return False

            # Unify default values for keyword-only args
            for pos in range(len(args_nodes[0].kw_defaults)):
                kw_defaults_at_pos = [a.kw_defaults[pos] for a in args_nodes]
                if kw_defaults_at_pos[0] is None:
                    if not all(kd is None for kd in kw_defaults_at_pos):
                        return False
                else:
                    if not all(kd is not None for kd in kw_defaults_at_pos):
                        return False
                    if not self._unify_nodes(kw_defaults_at_pos, subst, block_indices):
                        return False

            # TODO: Unify type annotations if present
            # For now, we skip type annotation unification

            return True

        finally:
            # Restore old mappings
            for key in old_mappings:
                self.alpha_renamings[key] = old_mappings[key]
            # Remove new mappings
            for pos in range(posonlyargs_counts[0]):
                for idx, block_idx in enumerate(block_indices):
                    param_name = args_nodes[idx].posonlyargs[pos].arg
                    key = (block_idx, param_name)
                    if key not in old_mappings:
                        self.alpha_renamings.pop(key, None)
            for pos in range(args_counts[0]):
                for idx, block_idx in enumerate(block_indices):
                    param_name = args_nodes[idx].args[pos].arg
                    key = (block_idx, param_name)
                    if key not in old_mappings:
                        self.alpha_renamings.pop(key, None)
            for pos in range(kwonlyargs_counts[0]):
                for idx, block_idx in enumerate(block_indices):
                    param_name = args_nodes[idx].kwonlyargs[pos].arg
                    key = (block_idx, param_name)
                    if key not in old_mappings:
                        self.alpha_renamings.pop(key, None)
            if has_vararg[0]:
                for idx, block_idx in enumerate(block_indices):
                    vararg_name = args_nodes[idx].vararg.arg
                    key = (block_idx, vararg_name)
                    if key not in old_mappings:
                        self.alpha_renamings.pop(key, None)
            if has_kwarg[0]:
                for idx, block_idx in enumerate(block_indices):
                    kwarg_name = args_nodes[idx].kwarg.arg
                    key = (block_idx, kwarg_name)
                    if key not in old_mappings:
                        self.alpha_renamings.pop(key, None)

    def _unify_joined_str(
        self,
        nodes: List[ast.JoinedStr],
        subst: Substitution,
        block_indices: List[int]
    ) -> bool:
        """
        Unify f-strings (JoinedStr), never parameterizing Constant children.

        F-strings have strict structure requirements:
        - values list can only contain Constant or FormattedValue nodes
        - Constant nodes are string literals and must NEVER be parameterized
        - FormattedValue nodes contain expressions that CAN be unified/parameterized

        Args:
            nodes: List of JoinedStr nodes
            subst: Current substitution
            block_indices: Block indices

        Returns:
            True if unification succeeded
        """
        # Check all have the same number of values
        values_lists = [n.values for n in nodes]
        if not all(len(v) == len(values_lists[0]) for v in values_lists):
            # Different number of components - can't unify
            return False

        # Unify each component
        for i in range(len(values_lists[0])):
            components = [values[i] for values in values_lists]

            # Check all components are the same type
            component_types = [type(c) for c in components]
            if len(set(component_types)) != 1:
                # Different types at this position - can't unify
                return False

            first_component = components[0]

            if isinstance(first_component, ast.Constant):
                # String literal parts MUST be identical - NEVER parameterize
                values = [c.value for c in components]
                if not all(v == values[0] for v in values):
                    # Different string literals - can't unify f-strings with different text
                    return False

            elif isinstance(first_component, ast.FormattedValue):
                # FormattedValue contains an expression - unify it normally
                # Extract the value expressions
                value_exprs = [c.value for c in components]
                if not self._unify_nodes(value_exprs, subst, block_indices):
                    return False

                # Also check conversion and format_spec if present
                conversions = [c.conversion for c in components]
                if not all(conv == conversions[0] for conv in conversions):
                    return False

                # format_spec can be None or another JoinedStr
                format_specs = [c.format_spec for c in components]
                if format_specs[0] is not None:
                    if not all(fs is not None for fs in format_specs):
                        return False
                    if isinstance(format_specs[0], ast.JoinedStr):
                        if not self._unify_joined_str(format_specs, subst, block_indices):
                            return False

            else:
                # Unexpected component type
                return False

        return True

    def _unify_lists(
        self,
        lists: List[List],
        subst: Substitution,
        block_indices: List[int]
    ) -> bool:
        """
        Unify lists of values.

        Args:
            lists: List of lists to unify
            subst: Current substitution
            block_indices: Block indices

        Returns:
            True if unification succeeded
        """
        # All lists must have same length
        if not all(len(lst) == len(lists[0]) for lst in lists):
            return False

        # Unify element by element
        for i in range(len(lists[0])):
            elements = [lst[i] for lst in lists]

            # Check element types
            elem_types = set(type(e) for e in elements)
            if len(elem_types) > 1:
                return False

            first_elem = elements[0]

            if isinstance(first_elem, ast.AST):
                # AST nodes - unify recursively
                if not self._unify_nodes(elements, subst, block_indices):
                    return False
            elif isinstance(first_elem, list):
                # Nested lists
                if not self._unify_lists(elements, subst, block_indices):
                    return False
            else:
                # Primitive values - must be equal
                if not all(e == first_elem for e in elements):
                    return False

        return True

    def _check_constant_consistency(
        self,
        values: List[any],
        block_indices: List[int]
    ) -> bool:
        """
        Check if constants can be consistently parameterized per the user's rule.

        The rule: For constants to be parameterized, ALL occurrences across blocks
        must unify consistently. If a value appears at multiple positions, those
        positions must align and unify the same way.

        Example that should FAIL:
        - Block 0: "x = item * 2" and "z = y ** 2"
        - Block 1: "x = item * 3" and "z = y ** 2"
        - Value 2 appears at 2 positions in block 0
        - At position 1: 2 differs from 3 (would parameterize)
        - At position 2: 2 equals 2 (would NOT parameterize)
        - INCONSISTENT -> return False

        Args:
            values: List of constant values (one per block)
            block_indices: Block indices

        Returns:
            True if constants can be consistently parameterized
        """
        if len(values) != 2 or len(block_indices) != 2:
            # Only handle 2-block case for now
            return True

        value0, value1 = values
        idx0, idx1 = block_indices

        # Get all positions where each value appears
        positions0 = self.constant_positions.get((idx0, value0), [])
        positions1 = self.constant_positions.get((idx1, value1), [])

        # If either value appears only once, it's trivially consistent
        if len(positions0) <= 1 and len(positions1) <= 1:
            return True

        # If both values are the same, check they appear at same positions
        if value0 == value1:
            # Same value in both blocks - they should appear at same positions
            # If they do, unification will succeed without parameterization
            # This is fine, return True
            return True

        # Different values - check consistency
        # If value0 appears N times, and value1 appears M times, and N != M,
        # this is already inconsistent
        if len(positions0) != len(positions1):
            # Different number of occurrences
            # This means one value appears more times than the other
            # Cannot parameterize consistently
            return False

        # Both values appear the same number of times
        # Check if they appear at structurally matching positions
        # If the positions don't align, we can't parameterize

        # For now, use a simple heuristic: if a value appears multiple times (> 1),
        # we need the positions to match exactly
        if len(positions0) > 1:
            # Sort positions for comparison
            sorted_pos0 = sorted(positions0)
            sorted_pos1 = sorted(positions1)

            # Check if positions align
            if sorted_pos0 != sorted_pos1:
                # Positions don't align - inconsistent
                return False

        # Positions align - can parameterize consistently
        return True

    def _collect_constant_positions(self, blocks: List[List[ast.AST]]):
        """
        Collect all constant occurrences and their structural positions.

        This pre-pass finds every constant in each block and records its position
        using a path tuple that uniquely identifies its location in the AST.

        Args:
            blocks: List of code blocks to analyze
        """
        self.constant_positions = {}

        for block_idx, block in enumerate(blocks):
            for stmt_idx, stmt in enumerate(block):
                # Traverse this statement and record all constants
                self._record_constants_in_tree(stmt, (stmt_idx,), block_idx)

    def _record_constants_in_tree(self, node: ast.AST, path: Tuple, block_idx: int):
        """
        Recursively traverse AST and record all constant positions.

        Args:
            node: Current AST node
            path: Tuple representing path from statement root
            block_idx: Which block this is from
        """
        if isinstance(node, ast.Constant):
            # Record this constant's position
            key = (block_idx, node.value)
            if key not in self.constant_positions:
                self.constant_positions[key] = []
            self.constant_positions[key].append(path)

        # Recursively visit children
        for field_name in node._fields:
            if field_name in ('lineno', 'col_offset', 'end_lineno', 'end_col_offset'):
                continue

            field_value = getattr(node, field_name, None)

            if isinstance(field_value, list):
                for i, item in enumerate(field_value):
                    if isinstance(item, ast.AST):
                        child_path = path + (field_name, i)
                        self._record_constants_in_tree(item, child_path, block_idx)
            elif isinstance(field_value, ast.AST):
                child_path = path + (field_name,)
                self._record_constants_in_tree(field_value, child_path, block_idx)

    def _find_all_occurrences(self, value: any, block: List[ast.AST]) -> List[ast.AST]:
        """
        Find all AST nodes in a block that are constants with the given value.

        Args:
            value: The constant value to search for
            block: List of AST statements

        Returns:
            List of ast.Constant nodes with matching value
        """
        occurrences = []

        class OccurrenceFinder(ast.NodeVisitor):
            def visit_Constant(self, node):
                if node.value == value:
                    occurrences.append(node)
                self.generic_visit(node)

        for stmt in block:
            finder = OccurrenceFinder()
            finder.visit(stmt)

        return occurrences

    def _constant_appears_identically_elsewhere(
        self,
        values: List[any],
        block_indices: List[int]
    ) -> bool:
        """
        Check if any of the differing constant values also appears identically
        in both blocks at other positions.

        This implements the consistency rule: if a constant value appears at
        multiple positions, it must differ consistently at all positions or
        be identical at all positions. Mixed behavior means we can't parameterize
        just the constant - we'd need to parameterize a larger expression.

        Args:
            values: List of constant values (one per block)
            block_indices: Block indices

        Returns:
            True if any value appears identically elsewhere in both blocks
        """
        if not self.current_blocks or len(self.current_blocks) < 2:
            return False

        # Check each unique value
        unique_values = set(values)

        for value in unique_values:
            # Find all occurrences of this value in each block
            occurrences_per_block = []
            for idx in block_indices:
                if idx < len(self.current_blocks):
                    occs = self._find_all_occurrences(value, self.current_blocks[idx])
                    occurrences_per_block.append(len(occs))
                else:
                    occurrences_per_block.append(0)

            # If this value appears multiple times in ANY block, check consistency
            if any(count > 1 for count in occurrences_per_block):
                # Value appears multiple times - need to check if it's used consistently
                # For now, we use a conservative approach: if a value appears multiple
                # times, we don't parameterize the bare constant
                # This prevents the case where "2" appears as both "item * 2" (differs)
                # and "y ** 2" (same in both)
                return True

        return False

    def _try_parameterize(
        self,
        exprs: List[ast.AST],
        subst: Substitution,
        block_indices: List[int]
    ) -> bool:
        """
        Try to parameterize differing expressions.

        Args:
            exprs: List of expressions that differ
            subst: Current substitution
            block_indices: Block index for each expression

        Returns:
            True if parameterization succeeded
        """
        # CRITICAL: Cannot parameterize statement nodes (only expression nodes)
        # Statements (If, For, FunctionDef, etc.) must have the same type to unify
        # Only expressions (Name, Constant, Call, etc.) can be parameterized
        if any(isinstance(expr, ast.stmt) for expr in exprs):
            return False

        # Check if we've already parameterized these exact expressions
        expr_strs = [ast.unparse(e) for e in exprs]

        # Check if all expressions are already mapped to the same parameter
        existing_params = [
            subst.get_param_for_expr(idx, expr)
            for idx, expr in zip(block_indices, exprs)
        ]

        if all(p is not None for p in existing_params):
            # All mapped - check they map to the same parameter
            if len(set(existing_params)) == 1:
                return True
            else:
                # Mapped to different parameters - can't unify
                return False

        # Check we haven't exceeded max parameters
        if len(subst.param_expressions) >= self.max_parameters:
            return False

        # Analyze bound variables in each expression
        # For each expression, find which variables it references that are bound in context
        bound_vars_per_expr = []
        for idx, expr in zip(block_indices, exprs):
            if self.current_blocks and idx < len(self.current_blocks):
                # Get the full block as context
                block = self.current_blocks[idx]
                # Find which variables in the expression are bound in the block context
                bound_in_context = get_bound_variables_in_context(
                    ast.Module(body=block, type_ignores=[]),
                    expr
                )
                # Get variables referenced in the expression
                vars_in_expr = get_free_variables(expr)
                # Intersection: bound variables that are actually used in expression
                bound_vars = bound_in_context & vars_in_expr

                bound_vars_per_expr.append(bound_vars)
            else:
                bound_vars_per_expr.append(set())

        # Check if all expressions reference the same bound variables
        # If they do, this should be a function parameter
        all_bound_vars = [sorted(bv) for bv in bound_vars_per_expr]

        # Determine common bound variables (should be same across all expressions)
        if all_bound_vars and len(set(tuple(bv) for bv in all_bound_vars)) == 1:
            # All expressions reference the same set of bound variables
            common_bound_vars = all_bound_vars[0] if all_bound_vars[0] else None
        else:
            # Different bound variables - use None (not a function parameter)
            common_bound_vars = None

        # CRITICAL: Check if bound variables are accessible at call site
        # Comprehension variables (for r in results) are NOT accessible at call site
        # Only function-level free variables can be lambda-lifted
        if common_bound_vars:
            # Check if these bound variables are accessible at the function call site
            # (i.e., they're free variables of the block, not just comprehension variables)
            for idx, expr in zip(block_indices, exprs):
                if hasattr(self, 'current_blocks') and idx < len(self.current_blocks):
                    from .scope_analyzer import ScopeAnalyzer
                    analyzer = ScopeAnalyzer()
                    block_free_vars = analyzer.get_free_variables(self.current_blocks[idx])

                    # Check if all bound variables used in the expression are free variables
                    for var in common_bound_vars:
                        if var not in block_free_vars:
                            # Bound variable (like comprehension var) not accessible at call site
                            # Cannot lambda-lift - refuse to parameterize
                            return False

        # CRITICAL: If expressions are simple names without bound variables,
        # they must be validated - they need to exist at the call site
        # (Unless they're being lambda-lifted, in which case common_bound_vars is not None)
        if common_bound_vars is None:
            # Not using lambda lifting - check if expressions reference undefined variables
            for idx, expr in zip(block_indices, exprs):
                if isinstance(expr, ast.Name):
                    # Simple variable reference - needs to exist at call site
                    # Get free variables of the entire block to see what's available
                    if hasattr(self, 'current_blocks') and idx < len(self.current_blocks):
                        from .scope_analyzer import ScopeAnalyzer
                        analyzer = ScopeAnalyzer()
                        block_free_vars = analyzer.get_free_variables(self.current_blocks[idx])

                        # Check if this variable is available at call site
                        if expr.id not in block_free_vars:
                            # Variable not available at call site - can't parameterize
                            import logging
                            logger = logging.getLogger(__name__)
                            logger.debug(
                                f"Skipping refactoring: Variable '{expr.id}' is not accessible at function scope. "
                                f"It may be defined inside a nested function or be an unbound variable reference."
                            )
                            return False

        # Create a new parameter
        # Use __ prefix to avoid name collisions (Python convention)
        param_name = f"__param_{self.param_counter}"
        self.param_counter += 1

        # Add mappings for each block
        for idx, expr in zip(block_indices, exprs):
            subst.add_mapping(idx, expr, param_name, bound_vars=common_bound_vars)

        return True

    def reset(self):
        """Reset the parameter counter."""
        self.param_counter = 0
