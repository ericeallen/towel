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
Hygienic code extraction.

Generates extracted functions while ensuring:
- No shadowing of enclosing scope identifiers
- Proper handling of hygienically renamed identifiers
- Preservation of evaluation order
- Referential transparency
"""

import ast
import copy
from typing import List, Dict, Sequence, Set, Tuple, Optional, TYPE_CHECKING, Callable, Union, cast
from .substitution import Substitution
from .visitors import all_instances, visit_as
from .definite_assignment import definitely_bound_after
from .statement_facts import block_contains_return

if TYPE_CHECKING:
    from .scope_analyzer import Scope


class UnsupportedExtraction(ValueError):
    """A valid source construct cannot be represented by this extractor."""


class ParameterSubstituter(ast.NodeTransformer):
    """Replace the unified expressions of block 0 with the helper's parameter names.

    Binding occurrences (loop targets, comprehension targets, assignment
    targets) are never replaced; a name that shadows a parameter inside a
    nested scope is left alone there.
    """

    def __init__(
        self, subst: Substitution, param_names: List[str], rename_mapping: Dict[str, str]
    ) -> None:
        self.subst = subst
        self.param_names = param_names
        self.rename_mapping = rename_mapping
        self.block_idx = 0
        self.in_joinedstr = False
        self.var_to_param: Dict[str, str] = {}
        self.param_name_by_var: Dict[str, str] = {}
        self.shadowed_vars: Set[str] = set()

        # A parameter whose block-0 expression is a bare name stands for that
        # name wherever the template reads it.
        for param_name in param_names:
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

        maybe_param_name: Optional[str] = self.subst.get_param_for_expr(self.block_idx, node)
        if not maybe_param_name or maybe_param_name not in self.param_names:
            return None

        # Binding occurrences (Store/Del context) are never substituted.
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
                        value=visit_as(self, value.value),
                        conversion=value.conversion,
                        format_spec=value.format_spec,
                    )
                    new_values.append(new_formatted)
                else:
                    # Shouldn't happen, but handle gracefully
                    new_values.append(visit_as(self, value))
        finally:
            self.in_joinedstr = previous_state
        return ast.JoinedStr(values=new_values)

    def visit_For(self, node: ast.For) -> ast.For:
        """
        Special handling for For loops to avoid replacing binding occurrences.

        In 'for target in iter: body', the 'target' is a BINDING occurrence
        and should NOT be replaced with a parameter.
        """
        new_iter, new_body, new_orelse = self._loop_parts(node)
        return ast.For(target=node.target, iter=new_iter, body=new_body, orelse=new_orelse)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> ast.AsyncFor:
        new_iter, new_body, new_orelse = self._loop_parts(node)
        return ast.AsyncFor(target=node.target, iter=new_iter, body=new_body, orelse=new_orelse)

    def _loop_parts(
        self, node: Union[ast.For, ast.AsyncFor]
    ) -> Tuple[ast.expr, List[ast.stmt], List[ast.stmt]]:
        """The transformed iterator, body, and else of a loop; its target is a binding and stays."""
        new_iter = visit_as(self, node.iter)
        for var_name in self._variables_from_target(node.target):
            self._mark_shadowed(var_name)
        new_body = self._visit_branch_statements(node.body)
        new_orelse = self._visit_branch_statements(node.orelse) if node.orelse else []
        return new_iter, new_body, new_orelse

    def visit_comprehension(self, node: ast.comprehension) -> ast.comprehension:
        """
        Special handling for comprehensions to avoid replacing binding occurrences.

        In 'for target in iter', the 'target' is a BINDING occurrence.
        """
        # Transform the iterator
        new_iter = visit_as(self, node.iter)

        # Don't transform the target (comprehension variable) - it's a binding
        new_target = node.target
        for var_name in self._variables_from_target(node.target):
            self._mark_shadowed(var_name)

        # Transform the filters
        new_ifs = [visit_as(self, cond) for cond in node.ifs]

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
        new_value = visit_as(self, node.value)

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
        new_test = visit_as(self, node.test)
        new_body = self._visit_branch_statements(node.body)
        new_orelse = self._visit_branch_statements(node.orelse)
        return ast.If(test=new_test, body=new_body, orelse=new_orelse)

    def visit_AugAssign(self, node: ast.AugAssign) -> ast.AugAssign:
        new_value = visit_as(self, node.value)
        if isinstance(node.target, ast.Name):
            self._mark_shadowed(node.target.id)
            new_target: Union[ast.Name, ast.Attribute, ast.Subscript] = node.target
        else:
            new_target = cast(
                Union[ast.Attribute, ast.Subscript],
                self._transform_assignment_target(node.target),
            )
        return ast.AugAssign(target=new_target, op=node.op, value=new_value)

    def visit_With(self, node: ast.With) -> ast.With:
        new_items = [
            ast.withitem(
                context_expr=visit_as(self, item.context_expr),
                optional_vars=item.optional_vars,
            )
            for item in node.items
        ]
        new_body = self._visit_branch_statements(node.body)
        return ast.With(items=new_items, body=new_body)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> ast.AsyncWith:
        new_items = [
            ast.withitem(
                context_expr=visit_as(self, item.context_expr),
                optional_vars=item.optional_vars,
            )
            for item in node.items
        ]
        new_body = self._visit_branch_statements(node.body)
        return ast.AsyncWith(items=new_items, body=new_body)

    def visit_While(self, node: ast.While) -> ast.While:
        new_test = visit_as(self, node.test)
        new_body = self._visit_branch_statements(node.body)
        new_orelse = self._visit_branch_statements(node.orelse) if node.orelse else []
        return ast.While(test=new_test, body=new_body, orelse=new_orelse)

    def visit_Try(self, node: ast.Try) -> ast.Try:
        new_body = self._visit_branch_statements(node.body)
        new_handlers = []
        for handler in node.handlers:
            new_type = visit_as(self, handler.type) if handler.type else None
            new_handler_body = self._visit_branch_statements(handler.body)
            new_handlers.append(
                ast.ExceptHandler(type=new_type, name=handler.name, body=new_handler_body)
            )
        new_orelse = self._visit_branch_statements(node.orelse) if node.orelse else []
        new_finalbody = self._visit_branch_statements(node.finalbody) if node.finalbody else []
        return ast.Try(
            body=new_body,
            handlers=new_handlers,
            orelse=new_orelse,
            finalbody=new_finalbody,
        )

    def visit_AnnAssign(self, node: ast.AnnAssign) -> ast.AnnAssign:
        new_value = visit_as(self, node.value) if node.value else None
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
        if not isinstance(new_target, (ast.Name, ast.Attribute, ast.Subscript)):
            raise UnsupportedExtraction("Annotated assignment requires a single assignable target")
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
            return ast.copy_location(type(target)(elts=new_elts, ctx=target.ctx), target)
        if isinstance(target, ast.Attribute):
            new_value = visit_as(self, target.value)
            return ast.copy_location(
                ast.Attribute(value=new_value, attr=target.attr, ctx=target.ctx), target
            )
        if isinstance(target, ast.Subscript):
            new_value = visit_as(self, target.value)
            new_slice = visit_as(self, target.slice)
            return ast.copy_location(
                ast.Subscript(value=new_value, slice=new_slice, ctx=target.ctx), target
            )
        # Fallback: rely on generic_visit to transform child nodes
        return cast(ast.expr, super().generic_visit(target))

    def _visit_branch_statements(self, statements: List[ast.stmt]) -> List[ast.stmt]:
        snapshot = self.var_to_param.copy()
        shadow_snapshot = self.shadowed_vars.copy()
        try:
            result = [visit_as(self, stmt) for stmt in statements]
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


def _thunk(expr: ast.AST, bound_vars: List[str]) -> ast.Lambda:
    """``lambda v1, v2, ...: expr``: a function parameter's expression, evaluated lazily."""
    return ast.Lambda(
        args=ast.arguments(
            posonlyargs=[],
            args=[ast.arg(arg=var) for var in bound_vars],
            kwonlyargs=[],
            kw_defaults=[],
            defaults=[],
        ),
        body=cast(ast.expr, expr),
    )


def _forwarding_lambda(callee: ast.AST) -> ast.Lambda:
    """``lambda *args, **kwargs: callee(*args, **kwargs)``.

    A parameter the helper calls is passed as a callable of the same arity,
    so the call site evaluates nothing eagerly and the callee keeps its
    signature.
    """
    call_body = ast.Call(
        func=cast(ast.expr, callee),
        args=[ast.Starred(value=ast.Name(id="args", ctx=ast.Load()), ctx=ast.Load())],
        keywords=[ast.keyword(arg=None, value=ast.Name(id="kwargs", ctx=ast.Load()))],
    )
    return ast.Lambda(
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


def _unified_argument(
    substitution: Substitution, param_name: str, block_idx: int
) -> Optional[ast.expr]:
    """The argument ``block_idx`` passes for a parameter the unifier introduced.

    The block's own expression, wrapped as a thunk when the parameter is a
    function parameter and as a forwarding lambda when the helper calls it;
    None when the substitution records no expression for this block.
    """
    for expr_block_idx, expr in substitution.param_expressions[param_name]:
        if expr_block_idx != block_idx:
            continue
        if substitution.is_function_param(param_name):
            return _thunk(expr, substitution.get_function_param_vars(param_name))
        if param_name in substitution.params_used_as_callee:
            return _forwarding_lambda(expr)
        return cast(ast.expr, expr)
    return None


def _free_variable_argument(
    substitution: Substitution, param_name: str, block_idx: int, inverse_renames: Dict[str, str]
) -> ast.expr:
    """The argument ``block_idx`` passes for a free variable: its own spelling of the name.

    The spelling comes from the hygienic renames, then from an augmented
    assignment's per-block name; a parameter introduced by literal promotion
    passes the block's original expression, which need not be a name the
    call site binds.
    """
    var_name = inverse_renames.get(param_name, param_name)
    var_name = substitution.aug_assign_mappings.get(param_name, {}).get(block_idx, var_name)
    promoted = substitution.promoted_literal_args.get(param_name, {})
    if block_idx in promoted:
        return cast(ast.expr, promoted[block_idx])
    return ast.Name(id=var_name, ctx=ast.Load())


def _call_statement(call: ast.Call, return_vars: List[str], is_value_producing: bool) -> ast.stmt:
    """The statement that stands for the block: an assignment, a return, or a bare call."""
    if return_vars:
        assign_target: ast.expr
        if len(return_vars) == 1:
            assign_target = ast.Name(id=return_vars[0], ctx=ast.Store())
        else:
            assign_target = ast.Tuple(
                elts=[ast.Name(id=var, ctx=ast.Store()) for var in return_vars], ctx=ast.Store()
            )
        return ast.Assign(targets=[assign_target], value=call)
    if is_value_producing:
        return ast.Return(value=call)
    return ast.Expr(value=call)


def _declarations(
    global_decls: Optional[Set[str]], nonlocal_decls: Optional[Set[str]]
) -> List[ast.stmt]:
    """The ``global`` and ``nonlocal`` statements a helper body opens with, when it needs them."""
    preamble: List[ast.stmt] = []
    if global_decls:
        preamble.append(ast.Global(names=sorted(global_decls)))
    if nonlocal_decls:
        preamble.append(ast.Nonlocal(names=sorted(nonlocal_decls)))
    return preamble


def _names_tuple(names: List[str]) -> ast.expr:
    """``name`` for one name, ``(name1, name2, ...)`` for several, as a loaded expression."""
    if len(names) == 1:
        return ast.Name(id=names[0], ctx=ast.Load())
    return ast.Tuple(elts=[ast.Name(id=name, ctx=ast.Load()) for name in names], ctx=ast.Load())


class HygienicExtractor:
    """
    Extract code into a function while maintaining hygiene and
    referential transparency.
    """

    def __init__(self) -> None:
        self.used_names: Set[str] = set()

    def extract_function(
        self,
        *,
        template_block: Sequence[ast.stmt],
        substitution: Substitution,
        free_variables: Set[str],
        enclosing_names: Set[str],
        is_value_producing: bool,
        return_variables: Optional[List[str]] = None,
        global_decls: Optional[Set[str]] = None,
        nonlocal_decls: Optional[Set[str]] = None,
        function_name: str = "extracted_function",
    ) -> Tuple[ast.FunctionDef, Dict[str, int]]:
        """
        Extract code into a function.

        Args:
            template_block: The code block to extract (from one of the blocks).
            substitution: Substitution mapping expressions to parameters.
            free_variables: Free variables in the block.
            enclosing_names: Names defined in enclosing scopes, so the generated
                helper name and parameters do not shadow them.
            is_value_producing: Whether the block produces a value.
            return_variables: Variables the helper must return (for a
                value-producing extraction); empty for a statement block.
            global_decls: Names to declare ``global`` in the helper body, so an
                assignment to a module global keeps writing the global.
            nonlocal_decls: Names to declare ``nonlocal`` in the helper body, for
                the same reason across an enclosing function scope.
            function_name: Requested name for the helper; made unique against
                ``enclosing_names``.

        Returns:
            Tuple of (function AST node, parameter order dict).
        """
        # Reset name usage per extraction to keep function names stable across proposals
        # and avoid cross-proposal suffix inflation.
        self.used_names.clear()
        # Make the helper name unique against the enclosing scope so it never
        # shadows an existing binding.
        function_name = self._ensure_unique_name(function_name, enclosing_names)
        # Unified parameters keep their exact names (``__param_N``): the body
        # substitution and the substitution's lookups depend on them. They come
        # first, to preserve evaluation order, then the free variables.
        param_names_unified = list(substitution.param_expressions.keys())
        all_param_names = param_names_unified + sorted(free_variables)
        if len(set(all_param_names)) != len(all_param_names):
            raise UnsupportedExtraction("Generated parameter name collides with a free variable")
        param_order = {name: idx for idx, name in enumerate(all_param_names)}

        body_nodes = self._substitute_parameters(
            copy.deepcopy(list(template_block)),
            substitution,
            param_names_unified,
            {name: name for name in param_names_unified},
        )
        body: List[ast.stmt] = list(body_nodes)
        if param_names_unified:
            # Parameters the body calls are passed as thunks; record them.
            finder = _CalleeParamFinder(set(param_names_unified))
            for stmt in body:
                finder.visit(stmt)
            substitution.params_used_as_callee.update(finder.found)
        final_body = _declarations(global_decls, nonlocal_decls) + body
        if return_variables:
            final_body.append(ast.Return(value=_names_tuple(return_variables)))
        func_def = ast.FunctionDef(
            name=function_name,
            args=ast.arguments(
                posonlyargs=[],
                args=[ast.arg(arg=name) for name in all_param_names],
                kwonlyargs=[],
                kw_defaults=[],
                defaults=[],
            ),
            body=final_body if final_body else [ast.Pass()],
            decorator_list=[],
            returns=None,
        )
        ast.fix_missing_locations(func_def)
        return func_def, param_order

    def generate_call(
        self,
        *,
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
        if not hygienic_renames:
            # Fallback: the renames the substitution recorded during unification
            hygienic_renames = substitution.hygienic_renames
        # hygienic_renames[block_idx] maps original → canonical; the call needs the reverse
        inverse_renames: Dict[str, str] = {}
        if block_idx < len(hygienic_renames):
            for original_name, canonical_name in hygienic_renames[block_idx].items():
                inverse_renames[canonical_name] = original_name

        args_list: List[Optional[ast.expr]] = [None] * len(param_order)
        for param_name, param_idx in param_order.items():
            if param_name in substitution.param_expressions:
                args_list[param_idx] = _unified_argument(substitution, param_name, block_idx)
            else:
                args_list[param_idx] = _free_variable_argument(
                    substitution, param_name, block_idx, inverse_renames
                )
        if not all_instances(args_list, ast.expr):
            raise UnsupportedExtraction("a parameter has no argument at this site")
        arguments: List[ast.expr] = args_list
        call = ast.Call(
            func=ast.Name(id=function_name, ctx=ast.Load()),
            args=arguments,
            keywords=[],
        )
        mapped_return_vars = [inverse_renames.get(var, var) for var in return_variables or []]
        result_stmt = _call_statement(call, mapped_return_vars, is_value_producing)
        # Arguments may refer to expressions owned by the substitution. Detach
        # them before location repair, and before returning a mutable AST to a
        # caller that may subsequently edit it.
        result_stmt = copy.deepcopy(result_stmt)
        ast.fix_missing_locations(result_stmt)
        return result_stmt

    def _substitute_parameters(
        self,
        nodes: Sequence[ast.stmt],
        substitution: Substitution,
        param_names: List[str],
        rename_mapping: Dict[str, str],
    ) -> List[ast.stmt]:
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

        substituter = ParameterSubstituter(substitution, param_names, rename_mapping)
        return [visit_as(substituter, node) for node in nodes]

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

        counter = 1
        while True:
            candidate = f"__{name}_{counter}"
            if candidate not in enclosing_names and candidate not in self.used_names:
                self.used_names.add(candidate)
                return candidate
            counter += 1


class _CalleeParamFinder(ast.NodeVisitor):
    """Which of the given parameters are called (appear as a callee)."""

    def __init__(self, params: Set[str]) -> None:
        self.params = params
        self.found: Set[str] = set()

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in self.params:
            self.found.add(node.func.id)
        self.generic_visit(node)


def contains_return(block: Sequence[ast.stmt]) -> bool:
    """Whether the block returns anywhere in its own scope, nested statements included.

    A ``return`` inside a nested function is that function's, not the block's.
    Answered from facts memoized per statement, since block enumeration asks
    this of every contiguous sub-block of a body.
    """
    return block_contains_return(block)


def is_value_producing(block: Sequence[ast.stmt]) -> bool:
    """
    Check if a block of code produces a value.

    A block is value-producing if:
    - It contains a return statement (including nested)
    - It's a single expression
    """
    if not block:
        return False

    if contains_return(block):
        return True

    # Single expression statement
    last_stmt = block[-1]
    if len(block) == 1 and isinstance(last_stmt, ast.Expr):
        return True

    return False


def has_complete_return_coverage(block: Sequence[ast.stmt]) -> bool:
    """Whether a value-producing block returns on every path.

    Two conditions, both required. The shape the extractor renders: the last
    statement is a return, or an if/else whose branches both return. And the
    exact property: no path through the block falls through, decided by the
    definite-assignment analysis. The shape alone accepted networkx's
    ``if test == 'graph': if a != b: return False / elif ...: return False``
    followed by more checks, because each branch merely *contains* a return;
    called as ``return helper(...)``, the caller then returned None on the
    paths where neither branch returned.
    """
    if not block:
        return False
    last_stmt = block[-1]
    if isinstance(last_stmt, ast.Return):
        shaped = True
    elif isinstance(last_stmt, ast.If) and last_stmt.orelse:
        shaped = contains_return(last_stmt.body) and contains_return(last_stmt.orelse)
    else:
        shaped = False
    return shaped and definitely_bound_after(block) is None


def enclosing_names(scope_tree: "Scope", current_scope: "Scope") -> Set[str]:
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
