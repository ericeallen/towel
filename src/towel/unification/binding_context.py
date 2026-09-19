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

"""Which names an expression reads, and which of them the enclosing block binds.

``statement_facts.loaded_names`` lists the names an expression reads; ``_BindingContextFinder``
walks the block that contains it, tracking loop, comprehension, lambda, and
function binders, so ``bound_variables_in_context`` can say which of those
names the block itself binds at the point of the expression.
``bound_variables_in_block`` memoizes that answer per block and target text.
"""

from __future__ import annotations

import ast
from weakref import WeakKeyDictionary
from typing import Callable, Dict, FrozenSet, List, Sequence, Set, Tuple, Union

from .parameters import parameter_names
from .statement_facts import loaded_names
from .models import FunctionNode
from .visitors import visit_comprehension_generators

_UNPARSED: "WeakKeyDictionary[ast.AST, str]" = WeakKeyDictionary()


def _unparse_cached(node: ast.AST) -> str:
    """``ast.unparse`` once per node for as long as the node lives.

    A module wrapper built around a block for one query is fresh each time,
    so its text is assembled from its statements' cached text instead.
    """
    cached = _UNPARSED.get(node)
    if cached is not None:
        return cached
    if isinstance(node, ast.Module):
        text = "\n".join(_unparse_cached(statement) for statement in node.body)
    else:
        text = ast.unparse(node)
    try:
        _UNPARSED[node] = text
    except TypeError:  # a node type that cannot be weakly referenced
        pass
    return text


class _BindingContextFinder(ast.NodeVisitor):
    """Bindings in effect around every textual occurrence of a target expression.

    Occurrences and containment are decided on the nodes' source text, as the
    original did; that text is computed once per node and cached for the life
    of the node, since this runs once per differing sub-expression per pair.
    """

    def __init__(self, target: ast.AST) -> None:
        self.target: ast.AST = target
        self.target_str: str = _unparse_cached(target)
        self.bound_vars: Set[str] = set()
        self.found_target: bool = False
        # Stack of currently bound variables (for control structures)
        self.binding_stack: List[Set[str]] = []
        # Accumulated assignments (persist for rest of block)
        self.assignments: Set[str] = set()

    def _contains_target(self, node: ast.AST) -> bool:
        """Check if node contains the target expression."""
        return self.target_str in _unparse_cached(node)

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

    def visit_For(self, node: Union[ast.For, ast.comprehension]) -> None:
        """A loop or a generator binds its target around the part that holds the target expression."""
        if self._contains_target(node):
            self._visit_binding_target(node, lambda: self.generic_visit(node))
        else:
            self.generic_visit(node)

    visit_comprehension = visit_For

    def visit_ListComp(self, node: Union[ast.ListComp, ast.SetComp, ast.GeneratorExp]) -> None:
        # Comprehension variables must be bound when visiting the element:
        # in [r.get_value() for r in results], 'r' is bound before r.get_value().
        if self._contains_target(node):
            self._visit_comprehension_node(node, lambda: self.visit(node.elt))
        else:
            self.generic_visit(node)

    visit_SetComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp

    def visit_DictComp(self, node: ast.DictComp) -> None:
        # Comprehension variables are bound while the key and value are visited.
        if self._contains_target(node):

            def visit_entries() -> None:
                self.visit(node.key)
                self.visit(node.value)

            self._visit_comprehension_node(node, visit_entries)
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
        # An assignment binds for the rest of the block, whether or not it
        # contains the target, so every one is accumulated.
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
        if self._contains_target(node):
            # The target of := is a binding
            named_vars = self._get_binding_vars(node.target)
            self._with_binding(named_vars, lambda: self.generic_visit(node))
        else:
            self.generic_visit(node)

    def generic_visit(self, node: ast.AST) -> None:
        if _unparse_cached(node) == self.target_str:
            self.found_target = True
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
        for gen in node.generators:
            comp_vars = self._get_binding_vars(gen.target)
            self.binding_stack.append(comp_vars)

        try:
            visit_comprehension_generators(self, node)
            visit_expression()
        finally:
            for _ in node.generators:
                self.binding_stack.pop()

    def _visit_function_like(self, node: FunctionNode) -> None:
        if not self._contains_target(node):
            self.assignments.add(node.name)
            return

        saved_assignments = self.assignments.copy()
        self.assignments.add(node.name)
        self.binding_stack.append({node.name})
        try:
            param_names = set(parameter_names(node.args))
            if param_names:
                self._with_binding(
                    param_names,
                    lambda: self._visit_function_body(node, saved_assignments),
                )
            else:
                self._visit_function_body(node, saved_assignments)
        finally:
            self.binding_stack.pop()

    def _visit_function_body(self, node: FunctionNode, saved_assignments: Set[str]) -> None:
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


def bound_variables_in_context(node: ast.AST, target_expr: ast.AST) -> Set[str]:
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

    finder = _BindingContextFinder(target_expr)
    finder.visit(node)

    # Filter to only include variables that are actually referenced in the target expression
    vars_in_expr = loaded_names(target_expr)
    result = finder.bound_vars & vars_in_expr

    return result


_BOUND_IN_BLOCK: "WeakKeyDictionary[ast.AST, Dict[Tuple[int, str], FrozenSet[str]]]" = (
    WeakKeyDictionary()
)
"""Per block (first statement, then length) and target text, what the finder collected."""


def bound_variables_in_block(block: Sequence[ast.AST], target_expr: ast.AST) -> Set[str]:
    """``bound_variables_in_context`` over a block, memoized per block and target text.

    The finder's answer depends only on the block's statements and the
    target's text, and the unifier asks the same question of the same block
    for every pair the block forms (on Towel's own source, 23,000 queries with
    5,200 distinct answers). The memo is keyed by the block's first statement
    and length, weakly, like the engine's other per-block caches: contiguous
    blocks that start at one statement and have one length are one block.
    """
    if not block:
        return bound_variables_in_context(ast.Module(body=[], type_ignores=[]), target_expr)
    by_target = _BOUND_IN_BLOCK.get(block[0])
    if by_target is None:
        by_target = {}
        try:
            _BOUND_IN_BLOCK[block[0]] = by_target
        except TypeError:  # a node type that cannot be weakly referenced
            pass
    key = (len(block), _unparse_cached(target_expr))
    bound = by_target.get(key)
    if bound is None:
        finder = _BindingContextFinder(target_expr)
        finder.visit(
            ast.Module(body=[node for node in block if isinstance(node, ast.stmt)], type_ignores=[])
        )
        bound = frozenset(finder.bound_vars)
        by_target[key] = bound
    return set(bound & loaded_names(target_expr))
