"""
Hygienic code extraction.

Generates extracted functions while ensuring:
- No shadowing of enclosing scope identifiers
- Proper handling of hygienically renamed identifiers
- Preservation of evaluation order
- Referential transparency
"""

import ast
import copy
from typing import List, Dict, Set, Tuple, Optional, TYPE_CHECKING, cast
from .unifier import Substitution

if TYPE_CHECKING:
    from .scope_analyzer import Scope


class HygienicExtractor:
    """
    Extract code into a function while maintaining hygiene and
    referential transparency.
    """

    def __init__(self) -> None:
        self.used_names: Set[str] = set()

    def extract_function(
        self,
        template_block: List[ast.AST],
        substitution: Substitution,
        free_variables: Set[str],
        enclosing_names: Set[str],
        is_value_producing: bool,
        return_variables: Optional[List[str]] = None,
        function_name: str = "extracted_function",
    ) -> Tuple[ast.FunctionDef, Dict[str, int]]:
        """
        Extract code into a function.

        Args:
            template_block: The code block to extract (from one of the blocks)
            substitution: Substitution mapping expressions to parameters
            free_variables: Free variables in the block
            enclosing_names: Names defined in enclosing scopes
            is_value_producing: Whether the block produces a value
            return_variables: Variables to return from the extracted function (for value-producing extraction)
            function_name: Name for the extracted function

        Returns:
            Tuple of (function AST node, parameter order dict)
        """
        if return_variables is None:
            return_variables = []
        # Ensure function name doesn't shadow
        function_name = self._ensure_unique_name(function_name, enclosing_names)

        # Determine parameters
        # 1. Parameters from unification (substituted expressions)
        # 2. Free variables (referenced but not bound in block)
        # IMPORTANT: Keep unified parameter names EXACT (e.g., '__param_0') to remain
        # consistent with Substitution lookups and replacements. Renaming these would
        # desynchronize the body substitutions from the function signature.
        param_names_unified = list(substitution.param_expressions.keys())

        # Mapping from renamed to original names (identity since we don't rename)
        rename_mapping = {name: name for name in param_names_unified}

        # Add free variables as parameters (they're already unique)
        param_names_free = sorted(free_variables)

        # Combine: unified parameters first (to preserve evaluation order),
        # then free variables
        all_param_names = param_names_unified + param_names_free

        # Create parameter order mapping
        param_order = {name: idx for idx, name in enumerate(all_param_names)}

        # Create function body by substituting unified parameters
        body_nodes = self._substitute_parameters(
            copy.deepcopy(template_block), substitution, param_names_unified, rename_mapping
        )
        # Substitute parameters returns generic AST nodes; for function body we expect statements
        body: List[ast.stmt] = [cast(ast.stmt, n) for n in body_nodes]

        # Detect parameters used as callees (in Call.func position) in the extracted body
        # so we can safely defer their evaluation at call sites via zero-arg lambdas.
        if param_names_unified:

            class _CalleeParamFinder(ast.NodeVisitor):
                def __init__(self, params: Set[str]) -> None:
                    self.params = params
                    self.found: Set[str] = set()

                def visit_Call(self, node: ast.Call) -> None:
                    # If the callee is a Name matching a unified parameter, record it
                    if isinstance(node.func, ast.Name) and node.func.id in self.params:
                        self.found.add(node.func.id)
                    # Continue traversal
                    self.generic_visit(node)

            finder = _CalleeParamFinder(set(param_names_unified))
            for stmt in body:
                finder.visit(stmt)
            # Record on substitution for use during call generation
            if hasattr(substitution, "params_used_as_callee"):
                substitution.params_used_as_callee.update(finder.found)

        # Add return statement for value-producing extraction
        if return_variables:
            # Prepare the return expression (expr type)
            return_value: ast.expr
            if len(return_variables) == 1:
                # Single return variable: return var
                return_value = ast.Name(id=return_variables[0], ctx=ast.Load())
            else:
                # Multiple return variables: return (var1, var2, ...)
                return_value = ast.Tuple(
                    elts=[ast.Name(id=var, ctx=ast.Load()) for var in return_variables],
                    ctx=ast.Load(),
                )

            return_stmt = ast.Return(value=return_value)
            body.append(return_stmt)

        # Create function arguments
        args = ast.arguments(
            posonlyargs=[],
            args=[ast.arg(arg=name) for name in all_param_names],
            kwonlyargs=[],
            kw_defaults=[],
            defaults=[],
        )

        # Create function definition
        func_def = ast.FunctionDef(
            name=function_name,
            args=args,
            body=body if body else [ast.Pass()],
            decorator_list=[],
            returns=None,
        )

        # Fix missing locations
        ast.fix_missing_locations(func_def)

        return func_def, param_order

    def generate_call(
        self,
        function_name: str,
        block_idx: int,
        substitution: Substitution,
        param_order: Dict[str, int],
        free_variables: Set[str],
        is_value_producing: bool,
        return_variables: Optional[List[str]] = None,
        hygienic_renames: Optional[List[Dict[str, str]]] = None,
    ) -> ast.stmt:
        """
        Generate a call to the extracted function.

        Args:
            function_name: Name of the function to call
            block_idx: Index of the block being replaced
            substitution: Substitution mapping
            param_order: Parameter order from extract_function
            free_variables: Free variables
            is_value_producing: Whether this produces a value
            return_variables: Variables that the extracted function returns
            hygienic_renames: Hygienic renaming mapping for each block (original → canonical)

        Returns:
            AST node representing the call (either Return, Assign, or Expr)
        """
        if return_variables is None:
            return_variables = []
        if hygienic_renames is None or not hygienic_renames:
            # Fallback: if the substitution carries hygienic renames, use them
            if hasattr(substitution, "hygienic_renames") and substitution.hygienic_renames:
                hygienic_renames = substitution.hygienic_renames
            else:
                hygienic_renames = []

        # Build inverse mapping: canonical name → original name for this block
        # hygienic_renames[block_idx] maps original → canonical, we need the reverse
        inverse_renames: Dict[str, str] = {}
        if block_idx < len(hygienic_renames):
            for original_name, canonical_name in hygienic_renames[block_idx].items():
                inverse_renames[canonical_name] = original_name

        # Build arguments in correct order
        # Build argument list (exprs); initialize as optional then cast when filled
        args_list: List[Optional[ast.expr]] = [None] * len(param_order)

        # Add unified parameters
        for param_name, param_idx in param_order.items():
            if param_name in substitution.param_expressions:
                # This is a unified parameter - find the expression for this block
                exprs = substitution.param_expressions[param_name]
                for expr_block_idx, expr in exprs:
                    if expr_block_idx == block_idx:
                        # Check if this is a function parameter
                        if substitution.is_function_param(param_name):
                            # Wrap expression in lambda with bound variables
                            bound_vars = substitution.get_function_param_vars(param_name)
                            # Create lambda: lambda var1, var2, ...: expr
                            lambda_node = ast.Lambda(
                                args=ast.arguments(
                                    posonlyargs=[],
                                    args=[ast.arg(arg=var) for var in bound_vars],
                                    kwonlyargs=[],
                                    kw_defaults=[],
                                    defaults=[],
                                ),
                                body=cast(ast.expr, expr),
                            )
                            args_list[param_idx] = lambda_node
                        elif (
                            hasattr(substitution, "params_used_as_callee")
                            and param_name in substitution.params_used_as_callee
                        ):
                            # Parameter is used as a callee in the extracted body (e.g., __param_0())
                            # Wrap it in a forwarding lambda that passes through any args/kwargs
                            # from the call site to the original callee expression.
                            # This avoids eager evaluation at the caller and preserves arity.
                            call_func = cast(ast.expr, expr if isinstance(expr, ast.expr) else expr)
                            call_body = ast.Call(
                                func=call_func,
                                args=[
                                    ast.Starred(
                                        value=ast.Name(id="args", ctx=ast.Load()), ctx=ast.Load()
                                    )
                                ],
                                keywords=[
                                    ast.keyword(
                                        arg=None, value=ast.Name(id="kwargs", ctx=ast.Load())
                                    )
                                ],
                            )

                            lambda_node = ast.Lambda(
                                args=ast.arguments(
                                    posonlyargs=[],
                                    args=[],
                                    vararg=ast.arg(arg="args"),
                                    kwonlyargs=[],
                                    kw_defaults=[],
                                    kwarg=ast.arg(arg="kwargs"),
                                    defaults=[],
                                ),
                                body=call_body,
                            )
                            args_list[param_idx] = lambda_node
                        else:
                            # Regular parameter - use expression as-is
                            args_list[param_idx] = cast(ast.expr, expr)
                        break
            else:
                # This is a free variable - use the correct name for this block
                # First check hygienic renames to find the original name for this block
                var_name = inverse_renames.get(param_name, param_name)

                # Also check if the name varies across blocks (augmented assignments)
                if (
                    hasattr(substitution, "aug_assign_mappings")
                    and param_name in substitution.aug_assign_mappings
                ):
                    mappings = substitution.aug_assign_mappings[param_name]
                    if block_idx in mappings:
                        var_name = mappings[block_idx]
                args_list[param_idx] = ast.Name(id=var_name, ctx=ast.Load())

        # Create function call
        call = ast.Call(
            func=ast.Name(id=function_name, ctx=ast.Load()),
            args=[cast(ast.expr, a) for a in args_list],
            keywords=[],
        )

        # Map return variables to this block's original names when needed
        mapped_return_vars: List[str] = []
        if return_variables:
            for var in return_variables:
                # inverse_renames is Dict[str, str], default is the original var (str)
                mapped_return_vars.append(inverse_renames.get(var, var))

        # Handle wrapping based on return variables and is_value_producing
        result_stmt: ast.stmt
        if mapped_return_vars:
            # Value-producing extraction with return variables
            # Create assignment statement: result = func(args) or result, other = func(args)
            if len(mapped_return_vars) == 1:
                # Single variable: result = func(args)
                assign_target: ast.expr = ast.Name(id=mapped_return_vars[0], ctx=ast.Store())
            else:
                # Multiple variables: result, other = func(args)
                assign_target = ast.Tuple(
                    elts=[ast.Name(id=var, ctx=ast.Store()) for var in mapped_return_vars],
                    ctx=ast.Store(),
                )
            result_stmt = ast.Assign(targets=[assign_target], value=call)
        elif is_value_producing:
            # Value-producing extraction without return variables (has explicit return statements)
            result_stmt = ast.Return(value=call)
        else:
            # Non-value-producing extraction
            result_stmt = ast.Expr(value=call)

        ast.fix_missing_locations(result_stmt)
        return result_stmt

    def _substitute_parameters(
        self,
        nodes: List[ast.AST],
        substitution: Substitution,
        param_names: List[str],
        rename_mapping: Dict[str, str],
    ) -> List[ast.AST]:
        """
        Substitute unified expressions with parameter names.

        Args:
            nodes: AST nodes to transform
            substitution: Substitution mapping
            param_names: Parameter names in order (renamed)
            rename_mapping: Mapping from renamed to original parameter names

        Returns:
            Transformed AST nodes
        """

        # Create a transformer that replaces expressions with parameter names
        class ParameterSubstituter(ast.NodeTransformer):
            def __init__(
                self, subst: Substitution, param_names: List[str], rename_mapping: Dict[str, str]
            ) -> None:
                self.subst = subst
                self.param_names = param_names
                self.rename_mapping = rename_mapping
                # Use block 0 as the template
                self.block_idx = 0
                # Track if we're inside a JoinedStr to avoid breaking f-string structure
                self.in_joinedstr = False
                # Track variables that are equivalent to parameters
                # Maps variable names to parameter names
                self.var_to_param: Dict[str, str] = {}

                # CRITICAL: Initialize var_to_param with variables that are parameterized
                # For each parameter, if its expression in block 0 is a simple variable name,
                # then that variable should be substituted with the parameter throughout
                for param_name in param_names:
                    # Get the original parameter name (before renaming)
                    original_param_name = rename_mapping.get(param_name, param_name)
                    if original_param_name in subst.param_expressions:
                        # This is a unified parameter - check if it's a simple variable reference
                        for block_idx, expr in subst.param_expressions[original_param_name]:
                            if block_idx == self.block_idx and isinstance(expr, ast.Name):
                                # This parameter represents a variable in our block
                                # Map the original variable name to the RENAMED parameter name
                                self.var_to_param[expr.id] = param_name
                                break

            def visit_JoinedStr(self, node: ast.JoinedStr) -> ast.JoinedStr:
                # JoinedStr (f-string) can only have Constant or FormattedValue as direct children
                # We must NEVER parameterize Constant nodes inside f-strings
                # But we CAN parameterize expressions inside FormattedValue nodes
                new_values: List[ast.expr] = []
                for value in node.values:
                    if isinstance(value, ast.Constant):
                        # String literal parts of f-string must stay as constants
                        new_values.append(value)
                    elif isinstance(value, ast.FormattedValue):
                        # For FormattedValue, recursively visit the value expression
                        new_formatted = ast.FormattedValue(
                            value=cast(ast.expr, self.visit(value.value)),
                            conversion=value.conversion,
                            format_spec=value.format_spec,
                        )
                        new_values.append(new_formatted)
                    else:
                        # Shouldn't happen, but handle gracefully
                        new_values.append(value)
                return ast.JoinedStr(values=new_values)

            def visit_For(self, node: ast.For) -> ast.For:
                """
                Special handling for For loops to avoid replacing binding occurrences.

                In 'for target in iter: body', the 'target' is a BINDING occurrence
                and should NOT be replaced with a parameter.
                """
                # Transform the iterator (can contain parameterized expressions)
                new_iter = cast(ast.expr, self.visit(node.iter))

                # Don't transform the target (loop variable) - it's a binding
                new_target = node.target

                # Transform the body
                new_body = [cast(ast.stmt, self.visit(stmt)) for stmt in node.body]
                new_orelse = (
                    [cast(ast.stmt, self.visit(stmt)) for stmt in node.orelse]
                    if node.orelse
                    else []
                )

                return ast.For(target=new_target, iter=new_iter, body=new_body, orelse=new_orelse)

            def visit_comprehension(self, node: ast.comprehension) -> ast.comprehension:
                """
                Special handling for comprehensions to avoid replacing binding occurrences.

                In 'for target in iter', the 'target' is a BINDING occurrence.
                """
                # Transform the iterator
                new_iter = cast(ast.expr, self.visit(node.iter))

                # Don't transform the target (comprehension variable) - it's a binding
                new_target = node.target

                # Transform the filters
                new_ifs = [cast(ast.expr, self.visit(cond)) for cond in node.ifs]

                return ast.comprehension(
                    target=new_target, iter=new_iter, ifs=new_ifs, is_async=node.is_async
                )

            def visit_Assign(self, node: ast.Assign) -> ast.Assign:
                """
                Special handling for assignments to handle both new bindings and reassignments.

                For 'var = expr':
                - If var is being assigned to a parameter (var = __param_N), track this mapping
                - If var is in var_to_param and being reassigned to the SAME parameter, substitute target
                - If var is in var_to_param but being reassigned to a DIFFERENT value, keep target as-is
                  and clear its mapping (creates new binding that shadows the parameter)
                - Otherwise, keep the target unchanged (new binding)
                """
                # Transform the value expression first
                new_value = cast(ast.expr, self.visit(node.value))

                # Transform targets
                new_targets: List[ast.expr] = []
                vars_to_delete = []  # Track which variables to remove from var_to_param

                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in self.var_to_param:
                        # This variable is currently equivalent to a parameter
                        param_name = self.var_to_param[target.id]

                        # Check if we're reassigning the parameter to itself (e.g., __param_0 = __param_0)
                        if isinstance(new_value, ast.Name) and new_value.id == param_name:
                            # Substitute the target with the parameter name
                            new_targets.append(ast.Name(id=param_name, ctx=ast.Store()))
                        else:
                            # We're assigning a DIFFERENT value, which creates a new binding
                            # Keep the original variable name and remove from var_to_param
                            new_targets.append(target)
                            vars_to_delete.append(target.id)
                    else:
                        # New binding or complex target (e.g., tuple unpacking) - keep as is
                        new_targets.append(target)

                # Now update var_to_param for new parameter bindings
                # Check if we're assigning a parameter to a variable (e.g., result = __param_0)
                if isinstance(new_value, ast.Name) and new_value.id in self.param_names:
                    # Record that these target variables are equivalent to this parameter
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            self.var_to_param[target.id] = new_value.id

                # Delete variables that are being reassigned to non-parameter values
                for var_name in vars_to_delete:
                    if var_name in self.var_to_param:
                        del self.var_to_param[var_name]

                return ast.Assign(targets=new_targets, value=new_value)

            def visit(self, node: ast.AST) -> ast.AST:
                # First, check if this is a variable that's equivalent to a parameter
                # (e.g., result is equivalent to __param_0)
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                    if node.id in self.var_to_param:
                        # This variable is equivalent to a parameter - substitute it
                        param_name = self.var_to_param[node.id]
                        return ast.Name(id=param_name, ctx=ast.Load())

                # Check if this expression should be replaced with a parameter
                maybe_param_name: Optional[str] = self.subst.get_param_for_expr(
                    self.block_idx, node
                )

                if maybe_param_name and maybe_param_name in self.param_names:
                    # CRITICAL: Never replace binding occurrences (Store/Del context)
                    # In 'for x in items:', the 'x' has Store context (binding)
                    # In 'result = x + 1', the 'x' has Load context (usage)
                    # We only parameterize USAGES, never BINDINGS
                    if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
                        # This is a binding occurrence (Store or Del context)
                        # Don't replace it - return as-is and don't visit children
                        return node

                    # Special handling for f-string components
                    # Don't replace FormattedValue nodes themselves, but do replace their value
                    if isinstance(node, ast.FormattedValue):
                        return self.generic_visit(node)

                    # For constants in f-strings, we need to be careful
                    # If we're inside a JoinedStr and this is a direct Constant child (literal part), skip
                    # But DO replace constants that are in expressions (like comparisons, assignments, etc.)
                    if self.in_joinedstr and isinstance(node, ast.Constant):
                        # This is a string literal component of f-string, don't replace
                        return self.generic_visit(node)

                    # Check if this is a function parameter
                    if self.subst.is_function_param(maybe_param_name):
                        # This parameter is a function - call it with bound variables
                        bound_vars = self.subst.get_function_param_vars(maybe_param_name)
                        # Create call: param_name(bound_var1, bound_var2, ...)
                        call = ast.Call(
                            func=ast.Name(id=maybe_param_name, ctx=ast.Load()),
                            args=[ast.Name(id=var, ctx=ast.Load()) for var in bound_vars],
                            keywords=[],
                        )
                        return call
                    else:
                        # Regular parameter - just replace with parameter name
                        return ast.Name(id=maybe_param_name, ctx=ast.Load())

                # Otherwise, recursively visit children
                return self.generic_visit(node)

        substituter = ParameterSubstituter(substitution, param_names, rename_mapping)
        return [substituter.visit(node) for node in nodes]

    def _ensure_unique_name(self, name: str, enclosing_names: Set[str]) -> str:
        """
        Ensure a name doesn't shadow enclosing scope names.

        Args:
            name: Proposed name
            enclosing_names: Names in enclosing scopes

        Returns:
            Unique name (possibly with numeric suffix)
        """
        if name not in enclosing_names and name not in self.used_names:
            self.used_names.add(name)
            return name

        # Add numeric suffix with __ prefix to avoid name collisions
        counter = 1
        while True:
            candidate = f"__{name}_{counter}"
            if candidate not in enclosing_names and candidate not in self.used_names:
                self.used_names.add(candidate)
                return candidate
            counter += 1


def contains_return(block: List[ast.stmt]) -> bool:
    """
    Check if a block contains any return statements (including nested ones).
    """

    class ReturnFinder(ast.NodeVisitor):
        def __init__(self) -> None:
            self.found_return: bool = False

        def visit_Return(self, node: ast.Return) -> None:
            self.found_return = True

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            # Don't visit nested function definitions
            pass

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            # Don't visit nested async function definitions
            pass

    finder = ReturnFinder()
    for stmt in block:
        finder.visit(stmt)
        if finder.found_return:
            return True
    return False


def is_value_producing(block: List[ast.stmt]) -> bool:
    """
    Check if a block of code produces a value.

    A block is value-producing if:
    - It contains a return statement (including nested)
    - It's a single expression
    """
    if not block:
        return False

    # Check if block contains any return statements
    if contains_return(block):
        return True

    # Single expression statement
    last_stmt = block[-1]
    if len(block) == 1 and isinstance(last_stmt, ast.Expr):
        return True

    return False


def has_complete_return_coverage(block: List[ast.stmt]) -> bool:
    """
    Check if a value-producing block has complete return coverage.

    This ensures that if a block contains conditional returns (like an IF
    with a return in the if-branch), it also has a return for the else case.

    Returns True if:
    - The last statement is a return, OR
    - The last statement is an IF/While/For with returns in ALL branches

    Args:
        block: List of AST statements

    Returns:
        True if the block has complete return coverage
    """
    if not block:
        return False

    last_stmt = block[-1]

    # If the last statement is a return, we have complete coverage
    if isinstance(last_stmt, ast.Return):
        return True

    # If the last statement is an IF
    if isinstance(last_stmt, ast.If):
        # Check if both branches have returns
        if_has_return = contains_return(last_stmt.body)

        # Check else branch
        if last_stmt.orelse:
            else_has_return = contains_return(last_stmt.orelse)
            # Complete coverage if both branches return
            return if_has_return and else_has_return
        else:
            # No else branch - incomplete coverage unless there's a return after
            return False

    # For other control structures (while, for, etc), incomplete coverage
    # unless there's a return after them
    return False


def get_enclosing_names(scope_tree: "Scope", current_scope: "Scope") -> Set[str]:
    """
    Get all names defined in scopes enclosing the current scope.

    Args:
        scope_tree: Root scope
        current_scope: Current scope

    Returns:
        Set of names in enclosing scopes
    """
    names: Set[str] = set()
    scope = current_scope.parent
    while scope is not None:
        names.update(scope.bindings.keys())
        scope = scope.parent
    return names
