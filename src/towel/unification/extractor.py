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
Hygienic code extraction.

Generates extracted functions while ensuring:
- No shadowing of enclosing scope identifiers
- Proper handling of hygienically renamed identifiers
- Preservation of evaluation order
- Referential transparency
"""

import ast
import copy
from typing import List, Dict, Set, Tuple, Optional, TYPE_CHECKING, Callable, cast
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
        *,
        global_decls: Optional[Set[str]] = None,
        nonlocal_decls: Optional[Set[str]] = None,
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
        # Reset name usage per extraction to keep function names stable across proposals
        # and avoid cross-proposal suffix inflation.
        self.used_names.clear()

        if return_variables is None:
            return_variables = []
        # Ensure function name doesn't shadow
        # Force double-underscore prefix for hygiene (avoid collisions with user code).
        # If caller specified a different name explicitly, respect it; otherwise use the default.
        if function_name == "__extracted_func":
            function_name = self._ensure_unique_name(function_name, enclosing_names)
        else:
            # Still ensure uniqueness if a custom name was provided.
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

        # Optionally inject global/nonlocal declarations at the top of the extracted function
        injected_preamble: List[ast.stmt] = []
        if global_decls:
            injected_preamble.append(ast.Global(names=sorted(global_decls)))
        if nonlocal_decls:
            injected_preamble.append(ast.Nonlocal(names=sorted(nonlocal_decls)))

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
        # Prepend any injected declarations before the transformed body
        final_body: List[ast.stmt] = (injected_preamble + body) if injected_preamble else body

        func_def = ast.FunctionDef(
            name=function_name,
            args=args,
            body=final_body if final_body else [ast.Pass()],
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
                # If this parameter was introduced via higher-order literal promotion,
                # prefer passing the original per-block expression rather than a free variable
                # reference (which likely doesn't exist at the call site).
                try:
                    if (
                        hasattr(substitution, "promoted_literal_args")
                        and substitution.promoted_literal_args
                    ):
                        promoted = substitution.promoted_literal_args.get(param_name, {})
                        if block_idx in promoted:
                            args_list[param_idx] = cast(ast.expr, promoted[block_idx])
                except Exception:
                    # Best-effort; fall back to name reference on any issue
                    pass

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
                # Track canonical parameter assigned to a variable name (even if shadowed later)
                self.param_name_by_var: Dict[str, str] = {}
                # Track parameterized variables that have been rebound to local values
                self.shadowed_vars: Set[str] = set()

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
                                self.param_name_by_var[expr.id] = param_name
                                break

            def _alias_variable(self, var_name: str, param_name: str) -> None:
                self.var_to_param[var_name] = param_name
                self.param_name_by_var[var_name] = param_name
                self.shadowed_vars.discard(var_name)

            def _mark_shadowed(self, var_name: str) -> None:
                if var_name in self.param_name_by_var:
                    self.var_to_param.pop(var_name, None)
                    self.shadowed_vars.add(var_name)

            def _variables_from_target(self, target: ast.AST) -> List[str]:
                names: List[str] = []

                def _collect(node: ast.AST) -> None:
                    if isinstance(node, ast.Name):
                        names.append(node.id)
                    elif isinstance(node, (ast.Tuple, ast.List)):
                        for elt in node.elts:
                            _collect(elt)

                _collect(target)
                return names

            def _maybe_replace_node(self, node: ast.AST) -> Optional[ast.AST]:
                # Only expressions participate in substitution mappings
                if not isinstance(node, ast.expr):
                    return None

                maybe_param_name: Optional[str] = self.subst.get_param_for_expr(
                    self.block_idx, node
                )
                if not maybe_param_name or maybe_param_name not in self.param_names:
                    return None

                # CRITICAL: Never replace binding occurrences (Store/Del context)
                if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
                    return node

                if isinstance(node, ast.Name) and node.id in self.shadowed_vars:
                    return node

                # Inside an f-string literal component, keep constants intact
                if self.in_joinedstr and isinstance(node, ast.Constant):
                    return node

                # Don't replace FormattedValue nodes themselves; recurse into their value instead
                if isinstance(node, ast.FormattedValue):
                    return None

                if self.subst.is_function_param(maybe_param_name):
                    bound_vars = self.subst.get_function_param_vars(maybe_param_name)
                    call = ast.Call(
                        func=ast.Name(id=maybe_param_name, ctx=ast.Load()),
                        args=[ast.Name(id=var, ctx=ast.Load()) for var in bound_vars],
                        keywords=[],
                    )
                    return ast.copy_location(call, node)

                # Regular parameter - just replace with parameter name
                return ast.copy_location(ast.Name(id=maybe_param_name, ctx=ast.Load()), node)

            def visit_JoinedStr(self, node: ast.JoinedStr) -> ast.JoinedStr:
                # JoinedStr (f-string) can only have Constant or FormattedValue as direct children
                # We must NEVER parameterize Constant nodes inside f-strings
                # But we CAN parameterize expressions inside FormattedValue nodes
                new_values: List[ast.expr] = []
                previous_state = self.in_joinedstr
                self.in_joinedstr = True
                try:
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
                            new_values.append(cast(ast.expr, self.visit(value)))
                finally:
                    self.in_joinedstr = previous_state
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
                for var_name in self._variables_from_target(node.target):
                    self._mark_shadowed(var_name)

                # Transform the body
                new_body = self._visit_branch_statements(node.body)
                new_orelse = self._visit_branch_statements(node.orelse) if node.orelse else []

                return ast.For(target=new_target, iter=new_iter, body=new_body, orelse=new_orelse)

            def visit_AsyncFor(self, node: ast.AsyncFor) -> ast.AsyncFor:
                new_iter = cast(ast.expr, self.visit(node.iter))
                new_target = node.target
                for var_name in self._variables_from_target(node.target):
                    self._mark_shadowed(var_name)
                new_body = self._visit_branch_statements(node.body)
                new_orelse = self._visit_branch_statements(node.orelse) if node.orelse else []
                return ast.AsyncFor(
                    target=new_target, iter=new_iter, body=new_body, orelse=new_orelse
                )

            def visit_comprehension(self, node: ast.comprehension) -> ast.comprehension:
                """
                Special handling for comprehensions to avoid replacing binding occurrences.

                In 'for target in iter', the 'target' is a BINDING occurrence.
                """
                # Transform the iterator
                new_iter = cast(ast.expr, self.visit(node.iter))

                # Don't transform the target (comprehension variable) - it's a binding
                new_target = node.target
                for var_name in self._variables_from_target(node.target):
                    self._mark_shadowed(var_name)

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

                # Transform targets while preserving binding semantics
                new_targets: List[ast.expr] = []
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        new_targets.append(target)
                    else:
                        new_targets.append(self._transform_assignment_target(target))

                assigns_param = isinstance(new_value, ast.Name) and new_value.id in self.param_names
                for target in node.targets:
                    for var_name in self._variables_from_target(target):
                        if assigns_param:
                            self._alias_variable(var_name, cast(ast.Name, new_value).id)
                        else:
                            self._mark_shadowed(var_name)

                return ast.Assign(targets=new_targets, value=new_value)

            def visit_If(self, node: ast.If) -> ast.If:
                new_test = cast(ast.expr, self.visit(node.test))
                new_body = self._visit_branch_statements(node.body)
                new_orelse = self._visit_branch_statements(node.orelse)
                return ast.If(test=new_test, body=new_body, orelse=new_orelse)

            def visit_AugAssign(self, node: ast.AugAssign) -> ast.AugAssign:
                new_value = cast(ast.expr, self.visit(node.value))
                if isinstance(node.target, ast.Name):
                    self._mark_shadowed(node.target.id)
                    new_target: ast.Name | ast.Attribute | ast.Subscript = node.target
                else:
                    new_target = cast(
                        ast.Attribute | ast.Subscript,
                        self._transform_assignment_target(node.target),
                    )
                return ast.AugAssign(target=new_target, op=node.op, value=new_value)

            def visit_With(self, node: ast.With) -> ast.With:
                new_items = [
                    ast.withitem(
                        context_expr=cast(ast.expr, self.visit(item.context_expr)),
                        optional_vars=item.optional_vars,
                    )
                    for item in node.items
                ]
                new_body = self._visit_branch_statements(node.body)
                return ast.With(items=new_items, body=new_body)

            def visit_AsyncWith(self, node: ast.AsyncWith) -> ast.AsyncWith:
                new_items = [
                    ast.withitem(
                        context_expr=cast(ast.expr, self.visit(item.context_expr)),
                        optional_vars=item.optional_vars,
                    )
                    for item in node.items
                ]
                new_body = self._visit_branch_statements(node.body)
                return ast.AsyncWith(items=new_items, body=new_body)

            def visit_While(self, node: ast.While) -> ast.While:
                new_test = cast(ast.expr, self.visit(node.test))
                new_body = self._visit_branch_statements(node.body)
                new_orelse = self._visit_branch_statements(node.orelse) if node.orelse else []
                return ast.While(test=new_test, body=new_body, orelse=new_orelse)

            def visit_Try(self, node: ast.Try) -> ast.Try:
                new_body = self._visit_branch_statements(node.body)
                new_handlers = []
                for handler in node.handlers:
                    new_type = cast(ast.expr, self.visit(handler.type)) if handler.type else None
                    new_handler_body = self._visit_branch_statements(handler.body)
                    new_handlers.append(
                        ast.ExceptHandler(type=new_type, name=handler.name, body=new_handler_body)
                    )
                new_orelse = self._visit_branch_statements(node.orelse) if node.orelse else []
                new_finalbody = (
                    self._visit_branch_statements(node.finalbody) if node.finalbody else []
                )
                return ast.Try(
                    body=new_body,
                    handlers=new_handlers,
                    orelse=new_orelse,
                    finalbody=new_finalbody,
                )

            def visit_AnnAssign(self, node: ast.AnnAssign) -> ast.AnnAssign:
                new_value = cast(ast.expr, self.visit(node.value)) if node.value else None
                if isinstance(node.target, (ast.Tuple, ast.List, ast.Attribute, ast.Subscript)):
                    new_target = self._transform_assignment_target(node.target)
                else:
                    new_target = node.target
                if isinstance(node.target, ast.Name):
                    if (
                        isinstance(new_value, ast.Name)
                        and new_value is not None
                        and new_value.id in self.param_names
                    ):
                        self._alias_variable(node.target.id, new_value.id)
                    else:
                        self._mark_shadowed(node.target.id)
                return ast.AnnAssign(
                    target=new_target,
                    annotation=node.annotation,
                    value=new_value,
                    simple=node.simple,
                )

            def _transform_assignment_target(self, target: ast.expr) -> ast.expr:
                """Recursively transform assignment targets while preserving binding semantics."""
                if isinstance(target, ast.Name):
                    return target
                if isinstance(target, (ast.Tuple, ast.List)):
                    new_elts = [self._transform_assignment_target(elt) for elt in target.elts]
                    return cast(
                        ast.expr,
                        ast.copy_location(type(target)(elts=new_elts, ctx=target.ctx), target),
                    )
                if isinstance(target, ast.Attribute):
                    new_value = cast(ast.expr, self.visit(target.value))
                    return cast(
                        ast.expr,
                        ast.copy_location(
                            ast.Attribute(value=new_value, attr=target.attr, ctx=target.ctx), target
                        ),
                    )
                if isinstance(target, ast.Subscript):
                    new_value = cast(ast.expr, self.visit(target.value))
                    new_slice = cast(ast.expr, self.visit(target.slice))
                    return cast(
                        ast.expr,
                        ast.copy_location(
                            ast.Subscript(value=new_value, slice=new_slice, ctx=target.ctx), target
                        ),
                    )
                # Fallback: rely on generic_visit to transform child nodes
                return cast(ast.expr, super().generic_visit(target))

            def _visit_branch_statements(self, statements: List[ast.stmt]) -> List[ast.stmt]:
                snapshot = self.var_to_param.copy()
                shadow_snapshot = self.shadowed_vars.copy()
                try:
                    result = [cast(ast.stmt, self.visit(stmt)) for stmt in statements]
                    current_state = self.var_to_param.copy()
                finally:
                    current_state = locals().get("current_state", self.var_to_param.copy())
                    restored = snapshot.copy()
                    for var_name, param_name in list(snapshot.items()):
                        if var_name not in current_state:
                            restored.pop(var_name, None)
                        elif current_state[var_name] != param_name:
                            restored.pop(var_name, None)
                    self.var_to_param = restored
                    current_shadowed = self.shadowed_vars.copy()
                    self.shadowed_vars = shadow_snapshot | current_shadowed
                return result

            def visit(self, node: ast.AST) -> ast.AST:
                replacement = self._maybe_replace_node(node)
                if replacement is not None:
                    return replacement

                method_name = f"visit_{node.__class__.__name__}"
                visitor = getattr(self, method_name, None)
                if visitor is None:
                    generic_result = super().generic_visit(node)
                    return generic_result
                visit_callable = cast(Callable[[ast.AST], ast.AST], visitor)
                return visit_callable(node)

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
