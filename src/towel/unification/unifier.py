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
Unification algorithm for finding parameterizable differences in AST nodes.

This is based on Robinson's unification algorithm from automated theorem proving,
adapted for AST comparison.
"""

import ast
from typing import Callable, Dict, Optional, List, Tuple, Set, Any, cast, Sequence, Union, Iterator
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
    # Optional hygienic renames captured during unification (one mapping per block)
    hygienic_renames: Optional[List[Dict[str, str]]] = field(default_factory=list)
    # Parameters that, after substitution, are used in call position (as a callee)
    # within the extracted function body. These should be passed as thunks (lambdas)
    # that perform the call to avoid eager evaluation at the call site.
    params_used_as_callee: Set[str] = field(default_factory=set)

    # Optional: parameters introduced post-unification to promote literal arguments
    # of higher-order factory calls (e.g., make_validator(5)) into threaded parameters
    # of the extracted helper, even when those literals do not differ across blocks.
    #
    # Schema:
    #   promoted_literal_args[param_name][block_idx] = ast.AST (expression to pass)
    #
    # This allows call generation to supply the original literal (or expression)
    # per block for such promoted parameters. By default, this dict is empty and
    # has no effect on behavior until a promotion pass populates it.
    promoted_literal_args: Dict[str, Dict[int, ast.AST]] = field(default_factory=dict)

    def add_mapping(
        self, block_idx: int, expr: ast.AST, param_name: str, bound_vars: Optional[List[str]] = None
    ) -> None:
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
        def __init__(self) -> None:
            self.vars: Set[str] = set()

        def visit_Name(self, node: ast.Name) -> None:
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
        def __init__(self, target: ast.AST) -> None:
            self.target: ast.AST = target
            self.target_str: str = ast.unparse(target)
            self.bound_vars: Set[str] = set()
            self.found_target: bool = False
            # Stack of currently bound variables (for control structures)
            self.binding_stack: List[Set[str]] = []
            # Accumulated assignments (persist for rest of block)
            self.assignments: Set[str] = set()

        def _contains_target(self, node: ast.AST) -> bool:
            """Check if node contains the target expression."""
            return self.target_str in ast.unparse(node)

        def _get_binding_vars(self, target: ast.AST) -> Set[str]:
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

        def visit_For(self, node: ast.For) -> None:
            if self._contains_target(node):
                self._visit_binding_target(node, lambda: self.generic_visit(node))
            else:
                self.generic_visit(node)

        def visit_comprehension(self, node: ast.comprehension) -> None:
            # comprehension node (part of generators list in ListComp, etc.)
            if self._contains_target(node):
                self._visit_binding_target(node, lambda: self.generic_visit(node))
            else:
                self.generic_visit(node)

        def visit_ListComp(self, node: ast.ListComp) -> None:
            # CRITICAL: Comprehension variables must be bound when visiting elt
            # [r.get_value() for r in results] - 'r' must be bound before visiting r.get_value()
            if self._contains_target(node):
                self._visit_comprehension_node(node, lambda: self.visit(node.elt))
            else:
                self.generic_visit(node)

        def visit_SetComp(self, node: ast.SetComp) -> None:
            # CRITICAL: Comprehension variables must be bound when visiting elt
            if self._contains_target(node):
                self._visit_comprehension_node(node, lambda: self.visit(node.elt))
            else:
                self.generic_visit(node)

        def visit_DictComp(self, node: ast.DictComp) -> None:
            # CRITICAL: Comprehension variables must be bound when visiting key and value
            if self._contains_target(node):

                def visit_entries() -> None:
                    self.visit(node.key)
                    self.visit(node.value)

                self._visit_comprehension_node(node, visit_entries)
            else:
                self.generic_visit(node)

        def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
            # CRITICAL: Comprehension variables must be bound when visiting elt
            if self._contains_target(node):
                self._visit_comprehension_node(node, lambda: self.visit(node.elt))
            else:
                self.generic_visit(node)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            # Function creates a new scope - save current assignments and start fresh
            self._visit_function_like(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            # Same as FunctionDef
            # Async functions mirror FunctionDef handling but use AsyncFunctionDef fields
            self._visit_function_like(node)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            # Class creates a new scope - save current assignments and start fresh
            if self._contains_target(node):
                self._visit_class_scope(node)
            else:
                self.assignments.add(node.name)

        def visit_Lambda(self, node: ast.Lambda) -> None:
            # Lambda creates a new scope for its parameters
            if self._contains_target(node):
                lambda_vars = {arg.arg for arg in node.args.args}
                if lambda_vars:
                    self.binding_stack.append(lambda_vars)
                self.visit(node.body)
                if lambda_vars:
                    self.binding_stack.pop()

        def visit_Assign(self, node: ast.Assign) -> None:
            # CRITICAL: Assignments create bindings that persist for the rest of the block
            # We accumulate ALL assignments as we traverse (not just those containing target)
            # Extract assigned variable(s)
            for target in node.targets:
                self.assignments.update(self._get_binding_vars(target))
            self.generic_visit(node)

        def visit_AugAssign(self, node: ast.AugAssign) -> None:
            # Augmented assignments also create persistent bindings
            self.assignments.update(self._get_binding_vars(node.target))
            self.generic_visit(node)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
            # Annotated assignments create persistent bindings
            self.assignments.update(self._get_binding_vars(node.target))
            self.generic_visit(node)

        def visit_With(self, node: ast.With) -> None:
            # Handle with statements: with open(f) as file: ...
            if self._contains_target(node):
                with_vars: Set[str] = set()
                for item in node.items:
                    if item.optional_vars:
                        with_vars.update(self._get_binding_vars(item.optional_vars))
                if with_vars:
                    self._with_binding(with_vars, lambda: self.generic_visit(node))
                else:
                    self.generic_visit(node)
            else:
                self.generic_visit(node)

        def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
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

        def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
            # Handle walrus operator: if (x := foo()): ...
            if self._contains_target(node):
                # The target of := is a binding
                named_vars = self._get_binding_vars(node.target)
                self._with_binding(named_vars, lambda: self.generic_visit(node))
            else:
                self.generic_visit(node)

        def generic_visit(self, node: ast.AST) -> None:
            # Check if this node matches target
            if ast.unparse(node) == self.target_str:
                self.found_target = True
                # Collect all currently bound variables
                # (from both control structures and assignments)
                for bound_set in self.binding_stack:
                    self.bound_vars.update(bound_set)
                self.bound_vars.update(self.assignments)
            ast.NodeVisitor.generic_visit(self, node)

        def _with_binding(self, names: Set[str], visit: Callable[[], None]) -> None:
            if not names:
                visit()
                return
            self.binding_stack.append(names)
            try:
                visit()
            finally:
                self.binding_stack.pop()

        def _visit_binding_target(
            self, node: Union[ast.For, ast.comprehension], visit: Callable[[], None]
        ) -> None:
            loop_vars = self._get_binding_vars(node.target)
            self._with_binding(loop_vars, visit)

        def _visit_comprehension_node(
            self,
            node: Union[ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp],
            visit_expression: Callable[[], None],
        ) -> None:
            def _visit_generators() -> None:
                for gen in node.generators:
                    self.visit(gen.iter)
                    for if_clause in gen.ifs:
                        self.visit(if_clause)

            for gen in node.generators:
                comp_vars = self._get_binding_vars(gen.target)
                self.binding_stack.append(comp_vars)

            try:
                _visit_generators()
                visit_expression()
            finally:
                for _ in node.generators:
                    self.binding_stack.pop()

        def _collect_param_names(self, args: ast.arguments) -> Set[str]:
            names: Set[str] = set()
            for arg in args.args:
                names.add(arg.arg)
            for arg in args.posonlyargs:
                names.add(arg.arg)
            for arg in args.kwonlyargs:
                names.add(arg.arg)
            if args.vararg:
                names.add(args.vararg.arg)
            if args.kwarg:
                names.add(args.kwarg.arg)
            return names

        def _visit_function_like(self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef]) -> None:
            if not self._contains_target(node):
                self.assignments.add(node.name)
                return

            saved_assignments = self.assignments.copy()
            self.assignments.add(node.name)
            self.binding_stack.append({node.name})
            try:
                param_names = self._collect_param_names(node.args)
                if param_names:
                    self._with_binding(
                        param_names,
                        lambda: self._visit_function_body(node, saved_assignments),
                    )
                else:
                    self._visit_function_body(node, saved_assignments)
            finally:
                self.binding_stack.pop()

        def _visit_function_body(
            self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef], saved_assignments: Set[str]
        ) -> None:
            self.assignments = set()
            try:
                for stmt in node.body:
                    self.visit(stmt)
            finally:
                self.assignments = saved_assignments

        def _visit_class_scope(self, node: ast.ClassDef) -> None:
            saved_assignments = self.assignments.copy()
            self.assignments.add(node.name)
            self.binding_stack.append({node.name})
            try:
                self.assignments = set()
                for stmt in node.body:
                    self.visit(stmt)
            finally:
                self.assignments = saved_assignments
                self.binding_stack.pop()

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

    # Track constant occurrences: (block_idx, value) -> [position_paths]
    # position_path is a tuple of (stmt_idx, field_name, ...) identifying location
    constant_positions: Dict[Tuple[int, Any], List[Tuple[Any, ...]]]

    def __init__(
        self,
        max_parameters: int = 5,
        parameterize_constants: bool = True,
        *,
        promote_equal_hof_literals: bool = False,
    ):
        """
        Initialize unifier.

        Args:
            max_parameters: Maximum number of parameters to extract
            parameterize_constants: Whether to parameterize differing constants
        """
        self.max_parameters = max_parameters
        # Feature flag: when True, enable Option B promotion of equal literals in
        # higher-order factory calls (thread as parameters even when equal).
        self._set_feature_flags(parameterize_constants, promote_equal_hof_literals)
        self.param_counter = 0
        # Track alpha-equivalence mappings for bound variables
        # Maps (block_idx, original_name) -> canonical_name
        self.alpha_renamings: Dict[Tuple[int, str], str] = {}
        # Store blocks being unified for context analysis
        self.current_blocks: Optional[List[List[ast.AST]]] = None
        # Track constant occurrences: (block_idx, value) -> [position_paths]
        # position_path is a tuple of (stmt_idx, field_name, ...) identifying location
        self.constant_positions = {}

    def unify_blocks(
        self, blocks: List[List[ast.AST]], hygienic_renames: List[Dict[str, str]]
    ) -> Optional[Substitution]:
        """
        Unify multiple code blocks.

        Args:
            blocks: List of code blocks (each is a list of AST statements)
            hygienic_renames: For each block, a mapping from original names
                             to hygienically renamed names

        Returns:
            Substitution mapping expressions to parameters, or None if unification fails
        """
        if len(blocks) < 2:
            return None

        # Check all blocks have the same number of statements
        if not all(len(b) == len(blocks[0]) for b in blocks):
            return None

        # Reset per-unification state to avoid cross-pair contamination
        # Alpha-renamings and parameter counters must start fresh for each call
        self.alpha_renamings = {}
        self._reset_unification_state(blocks)

        # Collect all constant positions for consistency checking
        self._collect_constant_positions(blocks)

        # Detect and map block-level bound variables for hygienic renaming
        # This allows unification of blocks with structurally identical code but different variable names
        self._setup_bound_variable_alpha_renamings(blocks)

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

        # Copy alpha-renamings to output hygienic_renames parameter
        # This allows callers to access the renames that were applied
        for (block_idx, var_name), canonical_name in self.alpha_renamings.items():
            if block_idx < len(hygienic_renames):
                hygienic_renames[block_idx][var_name] = canonical_name

        # Also attach hygienic_renames to the substitution for downstream consumers
        # so they don't need to thread the mapping through every call.
        try:
            subst.hygienic_renames = hygienic_renames
        except Exception:
            # Best-effort; continue even if attribute assignment is blocked
            pass

        # After successful unification, optionally promote literal arguments in
        # higher-order factory calls (Option B policy): even if literals are
        # equal across blocks, expose them as parameters and thread through calls.
        if self.promote_equal_hof_literals:
            try:
                self._promote_hof_literals(blocks, subst)
            except Exception:
                # Non-fatal; promotion is best-effort
                pass

        return subst

    def _promote_hof_literals(self, blocks: List[List[ast.AST]], subst: Substitution) -> None:
        """
        Promote literal arguments in higher-order factory calls into parameters,
        even when equal across blocks (Option B).

        Heuristic:
        - Look for assignments of the form: name = Call(...)
        - If 'name' is later used in Call position (i.e., as a callee), treat the
          assignment as constructing a higher-order function.
        - For each Constant argument to that Call, promote it to a parameter by
          mapping the corresponding per-block expression to a fresh parameter name.

        Implementation notes:
        - Uses block 0 as the structural template and assumes unified blocks share
          the same AST shape for corresponding statements.
        - Skips any expression already parameterized by the unifier.
        - Generates fresh parameter names via self.param_counter to avoid collisions
          with existing unified parameters (names like __param_N).
        """

        if not blocks or len(blocks) < 2:
            return

        num_blocks = len(blocks)

        # Utility: collect whether a variable is used later in a Call context (as a callee or as an argument)
        def is_used_as_callable_or_value_later(
            block: List[ast.AST], start_stmt_idx: int, var_name: str
        ) -> bool:
            class CallContextFinder(ast.NodeVisitor):
                def __init__(self) -> None:
                    self.found = False

                def visit_Call(self, node: ast.Call) -> None:
                    # Used as a callee
                    if isinstance(node.func, ast.Name) and node.func.id == var_name:
                        self.found = True
                    # Used as an argument to a call (higher-order usage)
                    for arg in node.args:
                        if isinstance(arg, ast.Name) and arg.id == var_name:
                            self.found = True
                    for kw in node.keywords:
                        if (
                            kw.arg is not None
                            and isinstance(kw.value, ast.Name)
                            and kw.value.id == var_name
                        ):
                            self.found = True
                    self.generic_visit(node)

                def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                    # Do not descend into nested function scopes
                    pass

                def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                    pass

                def visit_ClassDef(self, node: ast.ClassDef) -> None:
                    pass

            finder = CallContextFinder()
            for sidx in range(start_stmt_idx + 1, len(block)):
                finder.visit(block[sidx])
                if finder.found:
                    return True
            return False

        # Utility: yield all (path, call_node) pairs within a statement in block 0
        def iter_calls_with_paths(stmt: ast.AST) -> List[Tuple[Tuple[Any, ...], ast.Call]]:
            result: List[Tuple[Tuple[Any, ...], ast.Call]] = []

            def walk(node: ast.AST, path: Tuple[Any, ...]) -> None:
                if isinstance(node, ast.Call):
                    result.append((path, node))
                for field_name, value in self._iter_child_fields(node):
                    if isinstance(value, list):
                        for i, item in enumerate(value):
                            if isinstance(item, ast.AST):
                                walk(item, path + (field_name, i))
                    elif isinstance(value, ast.AST):
                        walk(value, path + (field_name,))

            walk(stmt, ("$root",))
            return result

        # Utility: follow a path within a statement to retrieve the corresponding node
        def get_node_by_path(stmt: ast.AST, path: Tuple[Any, ...]) -> Optional[ast.AST]:
            node: ast.AST = stmt
            # path starts with ("$root",), skip first marker
            for p in path[1:]:
                if isinstance(p, str):
                    if not hasattr(node, p):
                        return None
                    node = getattr(node, p)
                elif isinstance(p, int):
                    # indexing into a list; prior element must have been a list field
                    # find the previous step to access the list; handled by caller structure
                    return None  # we only use (field, index) pairs, so int alone shouldn't appear
                else:
                    # we expect (field_name, index) pairs encoded sequentially
                    return None
            return node

        # Utility: get child by (field, index) sequence from current node
        def get_node_by_field_index_path(stmt: ast.AST, path: Tuple[Any, ...]) -> Optional[ast.AST]:
            node: ast.AST = stmt
            # Skip "$root"
            idx = 1
            while idx < len(path):
                part = path[idx]
                if not isinstance(part, str):
                    return None
                field = part
                idx += 1
                if idx < len(path) and isinstance(path[idx], int):
                    list_index = path[idx]
                    idx += 1
                else:
                    list_index = None

                value = getattr(node, field, None)
                if list_index is None:
                    if not isinstance(value, ast.AST):
                        return None
                    node = value
                else:
                    if not isinstance(value, list) or list_index >= len(value):
                        return None
                    next_node = value[list_index]
                    if not isinstance(next_node, ast.AST):
                        return None
                    node = next_node
            return node

        # Iterate over statements in block 0 and attempt promotions
        for stmt_idx, stmt0 in enumerate(blocks[0]):
            # Consider only simple assignments to a single Name
            if not isinstance(stmt0, ast.Assign) or len(stmt0.targets) != 1:
                continue
            target0 = stmt0.targets[0]
            if not isinstance(target0, ast.Name):
                continue
            if not isinstance(stmt0.value, ast.Call):
                continue

            target_name = target0.id
            # Only promote when the assigned variable is used later in a call context
            if not is_used_as_callable_or_value_later(blocks[0], stmt_idx, target_name):
                continue

            # For each arg in the call in block 0, if Constant, attempt to promote
            call_paths = iter_calls_with_paths(stmt0)
            # Find the specific call path corresponding to stmt0.value
            # Since stmt0.value is a Call, find its path (should exist)
            call_path = None
            for path, call_node in call_paths:
                if call_node is stmt0.value:
                    call_path = path
                    break
            if call_path is None:
                continue

            call0 = stmt0.value
            for arg_pos, arg0 in enumerate(call0.args):
                if not isinstance(arg0, ast.AST):
                    continue
                # Only consider literal constants for now
                if not isinstance(arg0, ast.Constant):
                    continue

                # Build path to this arg: call_path + ("args", arg_pos)
                arg_path = call_path + ("args", arg_pos)

                # Collect per-block corresponding arg expressions
                per_block_exprs: List[Optional[ast.AST]] = []
                missing = False
                for bidx in range(num_blocks):
                    stmt_b = blocks[bidx][stmt_idx] if stmt_idx < len(blocks[bidx]) else None
                    if not isinstance(stmt_b, ast.Assign):
                        missing = True
                        break
                    val_b = stmt_b.value
                    if not isinstance(val_b, ast.AST):
                        missing = True
                        break
                    # Retrieve the node at arg_path within this statement
                    node_b = get_node_by_field_index_path(stmt_b, arg_path)
                    if node_b is None or not isinstance(node_b, ast.expr):
                        missing = True
                        break
                    per_block_exprs.append(node_b)

                if missing or len(per_block_exprs) != num_blocks:
                    continue

                # At this point, all per_block_exprs are non-None (validated above)
                valid_exprs = cast(List[ast.AST], per_block_exprs)

                # Skip if any of these expressions are already parameterized
                already_param = False
                for bidx, expr_b in enumerate(valid_exprs):
                    if subst.get_param_for_expr(bidx, expr_b) is not None:
                        already_param = True
                        break
                if already_param:
                    continue

                # Create fresh parameter name and record mappings for all blocks
                param_name = f"__param_{self.param_counter}"
                self.param_counter += 1

                for bidx, expr_b in enumerate(valid_exprs):
                    subst.add_mapping(bidx, expr_b, param_name, bound_vars=None)

                # Also store per-block expr under promoted_literal_args for clarity
                if hasattr(subst, "promoted_literal_args"):
                    if param_name not in subst.promoted_literal_args:
                        subst.promoted_literal_args[param_name] = {}
                    for bidx, expr_b in enumerate(valid_exprs):
                        subst.promoted_literal_args[param_name][bidx] = expr_b

    def _unify_nodes(
        self, nodes: Sequence[ast.AST], subst: Substitution, block_indices: Sequence[int]
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
            values = [cast(ast.Constant, n).value for n in nodes]
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
                name = cast(ast.Name, node).id
                # Check if this name has an alpha-renaming for this block
                renamed = self.alpha_renamings.get((block_idx, name), name)
                canonical_names.append(renamed)

            # Check if all canonical names are the same
            if len(set(canonical_names)) == 1:
                return True  # All same name (possibly after alpha-renaming)

            # Different names even after alpha-renaming - track correspondence before parameterizing
            # Use first ORIGINAL name (not canonical) for free variable correspondence
            # This is important: we want to map admin→user, not admin→__temp_0
            original_names = [cast(ast.Name, n).id for n in nodes]
            first_original_name = original_names[0]

            for node, block_idx, original_name in zip(nodes, block_indices, original_names):
                if original_name != first_original_name:
                    # Record that this block's name maps to the first block's original name
                    # This handles free variables with different names across blocks
                    self.alpha_renamings[(block_idx, original_name)] = first_original_name

            # Now parameterize
            return self._try_parameterize(nodes, subst, block_indices)

        # For compound nodes, recursively unify all fields
        return self._unify_compound_node(nodes, subst, block_indices)

    def _unify_compound_node(
        self, nodes: Sequence[ast.AST], subst: Substitution, block_indices: Sequence[int]
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
            return self._unify_for_loop(
                cast(List[ast.For], nodes), subst, cast(List[int], list(block_indices))
            )

        # Special handling for Lambda: parameters are bindings (alpha-renaming)
        # lambda x: x * 2 and lambda y: y * 2 are equivalent (alpha-equivalent)
        # The parameter names should NOT be parameterized
        if isinstance(first_node, ast.Lambda):
            return self._unify_lambda(
                cast(List[ast.Lambda], nodes), subst, cast(List[int], list(block_indices))
            )

        # Special handling for f-strings (JoinedStr)
        # F-string literal parts (Constant nodes) must NEVER be parameterized
        # Only the expressions inside FormattedValue can be parameterized
        if isinstance(first_node, ast.JoinedStr):
            return self._unify_joined_str(
                cast(List[ast.JoinedStr], nodes), subst, cast(List[int], list(block_indices))
            )

        # Special handling for comprehensions: targets are bindings and may differ
        # Treat generator targets as alpha-equivalent like for-loop variables
        if isinstance(first_node, ast.ListComp):
            return self._unify_list_comp(
                cast(List[ast.ListComp], nodes), subst, cast(List[int], list(block_indices))
            )
        if isinstance(first_node, ast.SetComp):
            return self._unify_set_comp(
                cast(List[ast.SetComp], nodes), subst, cast(List[int], list(block_indices))
            )
        if isinstance(first_node, ast.DictComp):
            return self._unify_dict_comp(
                cast(List[ast.DictComp], nodes), subst, cast(List[int], list(block_indices))
            )
        if isinstance(first_node, ast.GeneratorExp):
            return self._unify_generator_exp(
                cast(List[ast.GeneratorExp], nodes), subst, cast(List[int], list(block_indices))
            )

        # Special handling for with-statements: optional_vars are bindings
        if isinstance(first_node, ast.With):
            return self._unify_with(
                cast(List[ast.With], nodes), subst, cast(List[int], list(block_indices))
            )

        # Special handling for except handlers: name is a binding identifier
        if isinstance(first_node, ast.ExceptHandler):
            return self._unify_except_handler(
                cast(List[ast.ExceptHandler], nodes), subst, cast(List[int], list(block_indices))
            )

        # Special handling for walrus operator: target is a binding (alpha-equivalent)
        if isinstance(first_node, ast.NamedExpr):
            return self._unify_named_expr(
                cast(List[ast.NamedExpr], nodes), subst, cast(List[int], list(block_indices))
            )

        # For each field in the node
        for field_name in first_node._fields:
            # Skip location fields
            if field_name in ("lineno", "col_offset", "end_lineno", "end_col_offset"):
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
                if not self._unify_lists(
                    cast(Sequence[Sequence[Any]], field_values), subst, block_indices
                ):
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
                if not self._unify_nodes(
                    cast(Sequence[ast.AST], field_values), subst, block_indices
                ):
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

    def _unify_list_comp(
        self, nodes: List[ast.ListComp], subst: Substitution, block_indices: List[int]
    ) -> bool:
        """
        Unify ListComp nodes with alpha-renaming of generator targets.

        For a list comprehension, variables bound in each generator's target are
        bindings (like for-loop variables) and can differ across blocks. We
        establish temporary alpha-renamings using the first block as canonical,
        unify all generators under those mappings, then unify the element.
        """
        if not nodes:
            return False

        return self._unify_comprehension_core(
            nodes,
            subst,
            block_indices,
            lambda: self._unify_nodes([n.elt for n in nodes], subst, block_indices),
        )

    def _unify_set_comp(
        self, nodes: List[ast.SetComp], subst: Substitution, block_indices: List[int]
    ) -> bool:
        if not nodes:
            return False

        return self._unify_comprehension_core(
            nodes,
            subst,
            block_indices,
            lambda: self._unify_nodes([n.elt for n in nodes], subst, block_indices),
        )

    def _unify_comprehension_core(
        self,
        nodes: Sequence[Union[ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp]],
        subst: Substitution,
        block_indices: List[int],
        finalize: Callable[[], bool],
    ) -> bool:
        gen_lists = [n.generators for n in nodes]
        if not gen_lists or not all(len(g) == len(gen_lists[0]) for g in gen_lists):
            return False

        saved_alpha = dict(self.alpha_renamings)
        try:
            num_gens = len(gen_lists[0])
            for gen_idx in range(num_gens):
                comps = [g[gen_idx] for g in gen_lists]
                if not self._unify_single_comprehension(comps, subst, block_indices):
                    return False
            return finalize()
        finally:
            self.alpha_renamings = saved_alpha

    def _unify_dict_comp(
        self, nodes: List[ast.DictComp], subst: Substitution, block_indices: List[int]
    ) -> bool:
        if not nodes:
            return False

        return self._unify_comprehension_core(
            nodes,
            subst,
            block_indices,
            lambda: self._unify_nodes([n.key for n in nodes], subst, block_indices)
            and self._unify_nodes([n.value for n in nodes], subst, block_indices),
        )

    def _unify_generator_exp(
        self, nodes: List[ast.GeneratorExp], subst: Substitution, block_indices: List[int]
    ) -> bool:
        if not nodes:
            return False

        return self._unify_comprehension_core(
            nodes,
            subst,
            block_indices,
            lambda: self._unify_nodes([n.elt for n in nodes], subst, block_indices),
        )

    def _unify_with(
        self, nodes: List[ast.With], subst: Substitution, block_indices: List[int]
    ) -> bool:
        """
        Unify With nodes, treating optional_vars as bound (alpha-equivalent).

        - Unify items' context_expr.
        - Establish temporary alpha-renamings for optional_vars targets.
        - Unify bodies under those mappings.
        """
        # Same number of items
        items_lists = [n.items for n in nodes]
        if not all(len(lst) == len(items_lists[0]) for lst in items_lists):
            return False

        # Save mappings to restore after
        saved_alpha = dict(self.alpha_renamings)
        try:
            # Unify each item
            num_items = len(items_lists[0])
            for i in range(num_items):
                items_i = [lst[i] for lst in items_lists]
                # Unify context_expr
                if not self._unify_nodes([it.context_expr for it in items_i], subst, block_indices):
                    return False

                # Handle optional_vars as bindings
                optional_vars_raw = [it.optional_vars for it in items_i]
                if all(ov is None for ov in optional_vars_raw):
                    pass  # nothing to do
                else:
                    # All must be AST; if any None while others not, fail
                    if any(ov is None for ov in optional_vars_raw):
                        return False
                    # Establish alpha-renaming for targets
                    targets = cast(List[ast.expr], optional_vars_raw)
                    # Support simple Name or Tuple[List] of Names
                    # Collect names positionally

                    def flatten_names(t: ast.AST) -> List[str]:
                        if isinstance(t, ast.Name):
                            return [t.id]
                        if isinstance(t, (ast.Tuple, ast.List)):
                            names: List[str] = []
                            for e in t.elts:
                                names.extend(flatten_names(e))
                            return names
                        return []

                    names_per_block = [flatten_names(t) for t in targets]
                    # Ensure all have same arity
                    arities = [len(nl) for nl in names_per_block]
                    if len(set(arities)) != 1:
                        return False
                    # Use first block's names as canonical, map positionally
                    for pos in range(arities[0]):
                        canonical = names_per_block[0][pos]
                        for idx, block_idx in enumerate(block_indices):
                            actual = names_per_block[idx][pos]
                            self.alpha_renamings[(block_idx, actual)] = canonical

            # Unify bodies
            if not self._unify_lists([n.body for n in nodes], subst, block_indices):
                return False

            # type_comment (if present) must match
            comments = [getattr(n, "type_comment", None) for n in nodes]
            if not all(c == comments[0] for c in comments):
                return False

            return True
        finally:
            self.alpha_renamings = saved_alpha

    def _unify_except_handler(
        self, nodes: List[ast.ExceptHandler], subst: Substitution, block_indices: List[int]
    ) -> bool:
        """
        Unify ExceptHandler nodes, treating the 'name' as a bound identifier.
        """
        # Unify exception types (may be None)
        types_list: List[Optional[ast.expr]] = [n.type for n in nodes]
        if all(t is None for t in types_list):
            pass
        else:
            if any(t is None for t in types_list):
                return False
            if not self._unify_nodes(cast(List[ast.AST], types_list), subst, block_indices):
                return False

        # Establish temporary alpha-renamings for the handler variable names (strings)
        saved_alpha = dict(self.alpha_renamings)
        try:
            names: List[Optional[str]] = [n.name for n in nodes]
            # If all None, fine; if some None and others not, fail
            if all(nm is None for nm in names):
                pass
            else:
                if not all((nm is None) == (names[0] is None) for nm in names):
                    return False
                if names[0] is not None:
                    names_str: List[str] = cast(List[str], names)
                    canonical = names_str[0]
                    for idx, block_idx in enumerate(block_indices):
                        actual = names_str[idx]
                        self.alpha_renamings[(block_idx, actual)] = canonical

            # Unify body
            if not self._unify_lists([n.body for n in nodes], subst, block_indices):
                return False
            return True
        finally:
            self.alpha_renamings = saved_alpha

    def _unify_named_expr(
        self, nodes: List[ast.NamedExpr], subst: Substitution, block_indices: List[int]
    ) -> bool:
        """
        Unify walrus (NamedExpr) treating the target as a binding.
        Only simple Name targets are recognized for alpha-renaming.
        """
        # Save and set alpha mappings for targets
        saved_alpha = dict(self.alpha_renamings)
        try:
            targets = [n.target for n in nodes]
            name_targets: List[ast.Name] = [t for t in targets if isinstance(t, ast.Name)]
            if len(name_targets) == len(targets):
                names = [t.id for t in name_targets]
                canonical = names[0]
                for idx, block_idx in enumerate(block_indices):
                    self.alpha_renamings[(block_idx, names[idx])] = canonical

            # Unify values under established alpha-renamings
            if not self._unify_nodes([n.value for n in nodes], subst, block_indices):
                return False
            # Do not require exact match for target identifiers (treated as bindings)
            return True
        finally:
            self.alpha_renamings = saved_alpha

    def _unify_single_comprehension(
        self, comps: List[ast.comprehension], subst: Substitution, block_indices: List[int]
    ) -> bool:
        """
        Unify a single 'comprehension' node across blocks, establishing
        alpha-renamings for its target (Name or Tuple of Names), then unifying
        its iterator and if-clauses under those mappings.
        """
        # All must be comprehension nodes
        if not all(isinstance(c, ast.comprehension) for c in comps):
            return False

        # is_async flags must match
        async_flags = [c.is_async for c in comps]
        if len(set(async_flags)) != 1:
            return False

        # Handle targets as bindings
        targets = [c.target for c in comps]

        # Simple Name targets
        if all(isinstance(t, ast.Name) for t in targets):
            name_targets = cast(List[ast.Name], targets)
            names = [t.id for t in name_targets]
            canonical = names[0]
            # Establish alpha-renaming for the duration of the entire comprehension
            for idx, block_idx in enumerate(block_indices):
                key = (block_idx, names[idx])
                self.alpha_renamings[key] = canonical

            # Unify iterator and ifs under alpha-renaming
            if not self._unify_nodes([c.iter for c in comps], subst, block_indices):
                return False
            if not self._unify_lists([c.ifs for c in comps], subst, block_indices):
                return False
            return True

        # Tuple targets with simple names
        if all(isinstance(t, ast.Tuple) for t in targets):
            # All tuples must be flat and same length with Name elts
            tuple_targets = cast(List[ast.Tuple], targets)
            lengths = [len(t.elts) for t in tuple_targets]
            if len(set(lengths)) != 1:
                return False
            if not all(all(isinstance(e, ast.Name) for e in t.elts) for t in tuple_targets):
                return False

            tuple_names: List[List[str]] = []  # per position names
            for pos in range(lengths[0]):
                # We verified above that all elements are ast.Name, so cast for type checker
                tuple_names.append([cast(ast.Name, t.elts[pos]).id for t in tuple_targets])

            # Establish alpha-renaming per position to the first block's names
            for pos, names_at_pos in enumerate(tuple_names):
                canonical = names_at_pos[0]
                for idx, block_idx in enumerate(block_indices):
                    key = (block_idx, names_at_pos[idx])
                    self.alpha_renamings[key] = canonical

            # Unify iterator and ifs
            if not self._unify_nodes([c.iter for c in comps], subst, block_indices):
                return False
            if not self._unify_lists([c.ifs for c in comps], subst, block_indices):
                return False
            return True

        # Fallback: complex targets - unify structurally without alpha-renaming
        return (
            self._unify_nodes([c.target for c in comps], subst, block_indices)
            and self._unify_nodes([c.iter for c in comps], subst, block_indices)
            and self._unify_lists([c.ifs for c in comps], subst, block_indices)
        )

    def _unify_loop_components(
        self,
        nodes: Sequence[ast.For],
        subst: Substitution,
        block_indices: Sequence[int],
    ) -> bool:
        return (
            self._unify_nodes([n.iter for n in nodes], subst, block_indices)
            and self._unify_lists([n.body for n in nodes], subst, block_indices)
            and self._unify_lists([n.orelse for n in nodes], subst, block_indices)
        )

    def _unify_for_loop(
        self, nodes: List[ast.For], subst: Substitution, block_indices: List[int]
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
            return (
                self._unify_nodes(targets, subst, block_indices)
                and self._unify_nodes([n.iter for n in nodes], subst, block_indices)
                and self._unify_lists([n.body for n in nodes], subst, block_indices)
                and self._unify_lists([n.orelse for n in nodes], subst, block_indices)
            )

        name_targets = cast(List[ast.Name], targets)
        loop_var_names = [t.id for t in name_targets]

        # Check if all loop variables have the same name
        if len(set(loop_var_names)) == 1:
            # Same loop variable name - just unify normally
            return self._unify_loop_components(nodes, subst, block_indices)

        # Different loop variable names (i vs j) - establish alpha-equivalence
        # Use the first block's variable name as canonical
        canonical_var = loop_var_names[0]

        # Establish alpha-renaming mappings for all blocks
        # Save old mappings to restore later
        old_mappings: Dict[Tuple[int, str], str] = {}
        for idx, block_idx in enumerate(block_indices):
            var_name = loop_var_names[idx]
            key = (block_idx, var_name)
            self._assign_alpha_mapping(key, canonical_var, old_mappings)

        try:
            # Unify the iterator
            return self._unify_loop_components(nodes, subst, block_indices)

        finally:
            # Restore old mappings or remove new ones
            for idx, block_idx in enumerate(block_indices):
                var_name = loop_var_names[idx]
                key = (block_idx, var_name)
                self._restore_alpha_mapping(key, old_mappings)

    def _unify_for_loop_with_tuple_targets(
        self, nodes: List[ast.For], subst: Substitution, block_indices: List[int]
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
        tuple_targets = cast(List[ast.Tuple], targets)
        tuple_lengths = [len(t.elts) for t in tuple_targets]
        if len(set(tuple_lengths)) != 1:
            return False

        # Check all tuple elements are simple Name nodes
        for target in tuple_targets:
            if not all(isinstance(elt, ast.Name) for elt in target.elts):
                return False

        # Extract variable names for each position
        # var_names[position_idx] = [name_in_block0, name_in_block1, ...]
        num_positions = len(tuple_targets[0].elts)
        var_names = []
        for pos in range(num_positions):
            # We verified elements are ast.Name above; cast to satisfy type checker
            names_at_pos = [cast(ast.Name, target.elts[pos]).id for target in tuple_targets]
            var_names.append(names_at_pos)

        # Check if all corresponding names are identical
        # If so, no alpha-renaming needed
        all_same = all(len(set(names)) == 1 for names in var_names)

        if all_same:
            # All tuple unpacking uses same variable names - just unify normally
            return self._unify_loop_components(nodes, subst, block_indices)

        # Different variable names - establish alpha-equivalence for each position
        # Use the first block's variable names as canonical
        canonical_vars = [names[0] for names in var_names]

        # Establish alpha-renaming mappings for all positions and blocks
        # Save old mappings to restore later
        old_mappings: Dict[Tuple[int, str], str] = {}
        for pos_idx, canonical_var in enumerate(canonical_vars):
            for idx, block_idx in enumerate(block_indices):
                var_name = var_names[pos_idx][idx]
                key = (block_idx, var_name)
                self._assign_alpha_mapping(key, canonical_var, old_mappings)

        try:
            # Unify the iterator
            return self._unify_loop_components(nodes, subst, block_indices)

        finally:
            # Restore old mappings or remove new ones
            for pos_idx, canonical_var in enumerate(canonical_vars):
                for idx, block_idx in enumerate(block_indices):
                    var_name = var_names[pos_idx][idx]
                    key = (block_idx, var_name)
                    self._restore_alpha_mapping(key, old_mappings)

    def _unify_lambda(
        self, nodes: List[ast.Lambda], subst: Substitution, block_indices: List[int]
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
            if node.args.posonlyargs or node.args.kwonlyargs or node.args.vararg or node.args.kwarg:
                # Complex lambda parameters - not currently supported
                # See docs/KNOWN_LIMITATIONS.md for details
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
                    self._assign_alpha_mapping(key, canonical_param, old_mappings)

            # Unify lambda bodies with alpha-renaming in effect
            return self._unify_nodes([n.body for n in nodes], subst, block_indices)

        finally:
            # Restore old mappings or remove new ones
            for param_idx in range(num_params):
                for idx, node in enumerate(nodes):
                    block_idx = block_indices[idx]
                    actual_param = node.args.args[param_idx].arg
                    key = (block_idx, actual_param)
                    self._restore_alpha_mapping(key, old_mappings)

    def _unify_joined_str(
        self, nodes: List[ast.JoinedStr], subst: Substitution, block_indices: List[int]
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
                const_components = cast(List[ast.Constant], components)
                values = [c.value for c in const_components]
                if not all(v == values[0] for v in values):
                    # Different string literals - can't unify f-strings with different text
                    return False

            elif isinstance(first_component, ast.FormattedValue):
                # FormattedValue contains an expression - unify it normally
                # Extract the value expressions
                fmt_components = cast(List[ast.FormattedValue], components)
                value_exprs = [c.value for c in fmt_components]
                if not self._unify_nodes(value_exprs, subst, block_indices):
                    return False

                # Also check conversion and format_spec if present
                conversions = [c.conversion for c in fmt_components]
                if not all(conv == conversions[0] for conv in conversions):
                    return False

                # format_spec can be None or another JoinedStr
                format_specs = [c.format_spec for c in fmt_components]
                if format_specs[0] is not None:
                    if not all(fs is not None for fs in format_specs):
                        return False
                    if isinstance(format_specs[0], ast.JoinedStr):
                        joined_specs = cast(List[ast.JoinedStr], format_specs)
                        if not self._unify_joined_str(joined_specs, subst, block_indices):
                            return False

            else:
                # Unexpected component type
                return False

        return True

    def _unify_lists(
        self, lists: Sequence[Sequence[Any]], subst: Substitution, block_indices: Sequence[int]
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

    def _check_constant_consistency(self, values: List[Any], block_indices: Sequence[int]) -> bool:
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

    def _collect_constant_positions(self, blocks: List[List[ast.AST]]) -> None:
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

    def _record_constants_in_tree(
        self, node: ast.AST, path: Tuple[Any, ...], block_idx: int
    ) -> None:
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
        for field_name, field_value in self._iter_child_fields(node):
            if isinstance(field_value, list):
                for i, item in enumerate(field_value):
                    if isinstance(item, ast.AST):
                        child_path = path + (field_name, i)
                        self._record_constants_in_tree(item, child_path, block_idx)
            elif isinstance(field_value, ast.AST):
                child_path = path + (field_name,)
                self._record_constants_in_tree(field_value, child_path, block_idx)

    @staticmethod
    def _iter_child_fields(node: ast.AST) -> Iterator[Tuple[str, Any]]:
        """Yield (field_name, value) pairs skipping location metadata fields."""
        for field_name in getattr(node, "_fields", ()):  # pragma: no branch - simple iteration
            if field_name in ("lineno", "col_offset", "end_lineno", "end_col_offset"):
                continue
            yield field_name, getattr(node, field_name, None)

    def _find_all_occurrences(self, value: Any, block: List[ast.AST]) -> List[ast.AST]:
        """
        Find all AST nodes in a block that are constants with the given value.

        Args:
            value: The constant value to search for
            block: List of AST statements

        Returns:
            List of ast.Constant nodes with matching value
        """
        occurrences: List[ast.AST] = []

        class OccurrenceFinder(ast.NodeVisitor):
            def visit_Constant(self, node: ast.Constant) -> None:
                if node.value == value:
                    occurrences.append(node)
                self.generic_visit(node)

        for stmt in block:
            finder = OccurrenceFinder()
            finder.visit(stmt)

        return occurrences

    def _constant_appears_identically_elsewhere(
        self, values: List[Any], block_indices: List[int]
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
        self, exprs: Sequence[ast.AST], subst: Substitution, block_indices: Sequence[int]
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
        # F-strings (JoinedStr) must not be parameterized as a whole; only their
        # internal expressions (FormattedValue.value) are eligible. Prevent turning
        # entire f-strings into a single parameter to preserve structure.
        if any(isinstance(expr, ast.JoinedStr) for expr in exprs):
            return False

        # CRITICAL: Cannot parameterize statement nodes (only expression nodes)
        # Statements (If, For, FunctionDef, etc.) must have the same type to unify
        # Only expressions (Name, Constant, Call, etc.) can be parameterized
        if any(isinstance(expr, ast.stmt) for expr in exprs):
            return False

            # Check if we've already parameterized these exact expressions

        # Check if all expressions are already mapped to the same parameter
        existing_params = [
            subst.get_param_for_expr(idx, expr) for idx, expr in zip(block_indices, exprs)
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
                # mypy: ast.Module expects list[ast.stmt]
                typed_block = cast(List[ast.stmt], block)
                bound_in_context = get_bound_variables_in_context(
                    ast.Module(body=typed_block, type_ignores=[]), expr
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
                if self.current_blocks is not None and idx < len(self.current_blocks):
                    from .scope_analyzer import ScopeAnalyzer
                    from typing import Any as _Any, cast as _cast

                    analyzer1 = _cast(_Any, ScopeAnalyzer)()
                    block_free_vars = analyzer1.get_free_variables(self.current_blocks[idx])

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
                    if self.current_blocks is not None and idx < len(self.current_blocks):
                        from .scope_analyzer import ScopeAnalyzer
                        from typing import Any as _Any, cast as _cast

                        analyzer2 = _cast(_Any, ScopeAnalyzer)()
                        block_free_vars = analyzer2.get_free_variables(self.current_blocks[idx])

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

    def _setup_bound_variable_alpha_renamings(self, blocks: List[List[ast.AST]]) -> None:
        """
        Setup alpha-renamings for block-level bound variables.

        This allows unification of blocks with structurally identical code but different
        bound variable names (e.g., 'result' vs 'output').

        Strategy:
        1. For each block, collect variables that are first bound (assigned) in that block
        2. Match variables across blocks by their structural position (where they're first assigned)
        3. Add alpha-renamings to map corresponding variables to canonical names

        Example:
            Block1: result = x + 1; result = result + 2; return result
            Block2: output = y + 1; output = output + 2; return output

            -> result and output both bound at position (0, 0)
            -> Add mapping: result → temp, output → temp
            -> Existing Name node handling will use these mappings
        """
        # Collect binding information for each block
        binding_info = []
        for block_idx, block in enumerate(blocks):
            bindings = {}  # var_name → (stmt_idx, target_idx)
            seen_vars = set()

            for stmt_idx, stmt in enumerate(block):
                # Get all assignment targets in this statement
                targets = self._get_assignment_targets(stmt)

                for target_idx, target in enumerate(targets):
                    if isinstance(target, ast.Name) and isinstance(target.ctx, ast.Store):
                        var_name = target.id
                        # Track first binding position
                        if var_name not in seen_vars:
                            bindings[var_name] = (stmt_idx, target_idx)
                            seen_vars.add(var_name)

            binding_info.append(bindings)

        # Match variables across blocks by structural position
        # position_to_vars maps (stmt_idx, target_idx) → list of (block_idx, var_name) pairs
        position_to_vars: Dict[Tuple[int, int], List[Tuple[int, str]]] = {}

        for block_idx, bindings in enumerate(binding_info):
            for var_name, position in bindings.items():
                if position not in position_to_vars:
                    position_to_vars[position] = []
                position_to_vars[position].append((block_idx, var_name))

        # Generate alpha-renamings for variables at same position
        canonical_counter = 0
        used_canonical_names = set()

        for position, var_list in position_to_vars.items():
            # Only create alpha-renaming if multiple blocks have a variable at this position
            if len(var_list) < 2:
                continue

            # Check if variables at this position have different names
            var_names = [var_name for _, var_name in var_list]
            if len(set(var_names)) <= 1:
                # All same name - no renaming needed
                continue

            # Generate canonical name
            canonical_name = f"__temp_{canonical_counter}"
            while canonical_name in used_canonical_names:
                canonical_counter += 1
                canonical_name = f"__temp_{canonical_counter}"
            used_canonical_names.add(canonical_name)
            canonical_counter += 1

            # Add alpha-renamings for all blocks
            for block_idx, var_name in var_list:
                key = (block_idx, var_name)
                self.alpha_renamings[key] = canonical_name

    def _get_assignment_targets(self, stmt: ast.AST) -> List[ast.AST]:
        """
        Extract assignment targets from a statement.

        Returns list of target AST nodes (may be Name, Tuple, List, etc.)
        """
        targets = []

        if isinstance(stmt, ast.Assign):
            # Regular assignment: x = 1 or x, y = 1, 2
            for target in stmt.targets:
                targets.extend(self._flatten_assignment_target(target))
        elif isinstance(stmt, ast.AugAssign):
            # Augmented assignment: x += 1
            targets.append(stmt.target)
        elif isinstance(stmt, ast.AnnAssign):
            # Annotated assignment: x: int = 1
            if stmt.target:
                targets.append(stmt.target)
        elif isinstance(stmt, ast.With):
            # With bindings: with expr as target
            for item in stmt.items:
                if item.optional_vars is not None:
                    targets.extend(self._flatten_assignment_target(item.optional_vars))
        elif isinstance(stmt, ast.Try):
            # Except handler names are bindings
            for handler in stmt.handlers:
                if handler.name:
                    targets.append(ast.Name(id=handler.name, ctx=ast.Store()))

        return targets

    def _flatten_assignment_target(self, target: ast.AST) -> List[ast.AST]:
        """
        Flatten assignment target to individual names.

        Examples:
            x → [x]
            (x, y) → [x, y]
            [x, (y, z)] → [x, y, z]
        """
        if isinstance(target, ast.Name):
            return [target]
        elif isinstance(target, (ast.Tuple, ast.List)):
            result = []
            for elt in target.elts:
                result.extend(self._flatten_assignment_target(elt))
            return result
        else:
            # Other complex targets (subscript, attribute, etc.) - don't extract names
            return []

    def reset(self) -> None:
        """Reset the parameter counter."""
        self.param_counter = 0

    def _set_feature_flags(
        self, parameterize_constants: bool, promote_equal_hof_literals: bool
    ) -> None:
        self.parameterize_constants = parameterize_constants
        self.promote_equal_hof_literals = promote_equal_hof_literals

    def _reset_unification_state(self, blocks: List[List[ast.AST]]) -> None:
        self.param_counter = 0
        self.current_blocks = blocks

    def _assign_alpha_mapping(
        self,
        key: Tuple[int, str],
        canonical: str,
        old_mappings: Dict[Tuple[int, str], str],
    ) -> None:
        if key in self.alpha_renamings:
            old_mappings[key] = self.alpha_renamings[key]
        self.alpha_renamings[key] = canonical

    def _restore_alpha_mapping(
        self,
        key: Tuple[int, str],
        old_mappings: Dict[Tuple[int, str], str],
    ) -> None:
        if key in old_mappings:
            self.alpha_renamings[key] = old_mappings[key]
        else:
            self.alpha_renamings.pop(key, None)
