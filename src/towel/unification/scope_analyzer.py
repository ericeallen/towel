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
Analyze identifier bindings and scopes in Python code.
"""

import ast
from typing import Callable, Dict, Set, List, Optional, Tuple, Iterable, Union
from dataclasses import dataclass, field
from .builtins import filter_builtins


@dataclass
class Binding:
    """Represents a binding of an identifier to a value."""

    name: str
    scope_id: int  # Unique identifier for the scope
    node: ast.AST  # The AST node that creates this binding


@dataclass
class Scope:
    """Represents a lexical scope."""

    scope_id: int
    parent: Optional["Scope"]
    bindings: Dict[str, Binding] = field(default_factory=dict)
    children: List["Scope"] = field(default_factory=list)

    def lookup(self, name: str) -> Optional[Binding]:
        """Lookup a binding in this scope or parent scopes."""
        if name in self.bindings:
            return self.bindings[name]
        if self.parent:
            return self.parent.lookup(name)
        return None

    def add_binding(self, name: str, node: ast.AST) -> None:
        """Add a binding to this scope."""
        self.bindings[name] = Binding(name, self.scope_id, node)


def _iter_argument_nodes(args: ast.arguments) -> Iterable[ast.arg]:
    """Yield every ast.arg (positional, keyword, var/kw) from an arguments block."""
    for arg in args.posonlyargs:
        yield arg
    for arg in args.args:
        yield arg
    for arg in args.kwonlyargs:
        yield arg
    if args.vararg:
        yield args.vararg
    if args.kwarg:
        yield args.kwarg


def _iter_argument_names(args: ast.arguments) -> Iterable[str]:
    for arg in _iter_argument_nodes(args):
        yield arg.arg


def _visit_node_and_iterables(
    visitor: ast.NodeVisitor, first: ast.AST, rest: Iterable[ast.AST]
) -> None:
    visitor.visit(first)
    for expr in rest:
        visitor.visit(expr)


def _visit_comprehension_generators(
    visitor: ast.NodeVisitor, generators: List[ast.comprehension]
) -> None:
    for gen in generators:
        visitor.visit(gen)


class ScopeAnalyzer(ast.NodeVisitor):
    """
    Analyze scopes and identifier bindings in an AST.

    This builds a scope tree and tracks which identifiers refer to which
    bindings in the enclosing environment.
    """

    def __init__(self) -> None:
        self.scope_counter = 0
        self.current_scope: Optional[Scope] = None
        self.root_scope: Optional[Scope] = None

        # Map AST nodes to their scopes
        self.node_scopes: Dict[ast.AST, Scope] = {}

        # Map identifier uses to their bindings
        self.identifier_bindings: Dict[ast.Name, Optional[Binding]] = {}

        # Track global and nonlocal declarations per scope
        # Maps scope_id -> set of variable names
        self.global_vars: Dict[int, Set[str]] = {}
        self.nonlocal_vars: Dict[int, Set[str]] = {}

        # Cache for free-variable analysis (keyed by node identity tuple)
        self._free_var_cache: Dict[Tuple[int, ...], Set[str]] = {}

    def analyze(self, tree: ast.AST) -> Scope:
        """Analyze an AST and return the root scope."""
        self.root_scope = self._create_scope(None)
        self.current_scope = self.root_scope
        self.visit(tree)
        return self.root_scope

    def _create_scope(self, parent: Optional[Scope]) -> Scope:
        """Create a new scope."""
        scope = Scope(self.scope_counter, parent)
        self.scope_counter += 1
        if parent:
            parent.children.append(scope)
        return scope

    def _enter_scope(self, node: ast.AST) -> Scope:
        """Enter a new scope."""
        new_scope = self._create_scope(self.current_scope)
        self.node_scopes[node] = new_scope
        self.current_scope = new_scope
        return new_scope

    def _exit_scope(self) -> None:
        """Exit the current scope."""
        if self.current_scope and self.current_scope.parent:
            self.current_scope = self.current_scope.parent

    def _enter_named_scope(
        self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef]
    ) -> None:
        assert self.current_scope is not None
        self.current_scope.add_binding(node.name, node)
        self._enter_scope(node)

    def _visit_body_and_exit(self, body: List[ast.stmt]) -> None:
        for stmt in body:
            self.visit(stmt)
        self._exit_scope()

    def _bind_function_parameters(self, args: ast.arguments) -> None:
        assert self.current_scope is not None
        for arg in _iter_argument_nodes(args):
            self.current_scope.add_binding(arg.arg, arg)

    def _visit_loop(self, node: Union[ast.For, ast.AsyncFor]) -> None:
        self.visit(node.iter)
        self._add_assignment_bindings(node.target)
        for stmt in node.body:
            self.visit(stmt)
        for stmt in node.orelse:
            self.visit(stmt)

    def _visit_with_statement(self, node: Union[ast.With, ast.AsyncWith]) -> None:
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars:
                self._add_assignment_bindings(item.optional_vars)
        for stmt in node.body:
            self.visit(stmt)

    def _record_scope_declaration(self, table: Dict[int, Set[str]], names: List[str]) -> None:
        if not self.current_scope:
            return
        scope_id = self.current_scope.scope_id
        table.setdefault(scope_id, set()).update(names)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Visit a function definition."""
        self._enter_named_scope(node)
        self._bind_function_parameters(node.args)
        self._visit_body_and_exit(node.body)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Visit an async function definition."""
        # Type ignore needed because mypy doesn't recognize structural compatibility
        self.visit_FunctionDef(node)  # type: ignore[arg-type]

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Visit a class definition."""
        self._enter_named_scope(node)
        self._visit_body_and_exit(node.body)

    def visit_Assign(self, node: ast.Assign) -> None:
        """Visit an assignment."""
        # Visit RHS first
        self.visit(node.value)

        # Add bindings for LHS targets
        for target in node.targets:
            self._add_assignment_bindings(target)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        """Visit an annotated assignment."""
        if node.value:
            self.visit(node.value)
        self._add_assignment_bindings(node.target)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        """Visit an augmented assignment."""
        self.visit(node.value)
        # Aug assigns don't create new bindings, they modify existing ones
        self.visit(node.target)

    def visit_For(self, node: ast.For) -> None:
        """Visit a for loop."""
        self._visit_loop(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        """Visit an async for loop."""
        self._visit_loop(node)

    def visit_With(self, node: ast.With) -> None:
        """Visit a with statement."""
        self._visit_with_statement(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        """Visit an async with statement."""
        self._visit_with_statement(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        """Visit a named expression (walrus operator :=)."""
        # Visit RHS first
        self.visit(node.value)
        # Target creates binding (and it leaks to enclosing scope)
        if isinstance(node.target, ast.Name):
            assert self.current_scope is not None
            self.current_scope.add_binding(node.target.id, node.target)

    def visit_Global(self, node: ast.Global) -> None:
        """Visit a global statement."""
        self._record_scope_declaration(self.global_vars, node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        """Visit a nonlocal statement."""
        self._record_scope_declaration(self.nonlocal_vars, node.names)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        """Visit a comprehension."""
        # Target creates bindings (in comprehension scope)
        self._add_assignment_bindings(node.target)
        _visit_node_and_iterables(self, node.iter, node.ifs)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type:
            self.visit(node.type)
        if node.name:
            assert self.current_scope is not None
            self.current_scope.add_binding(node.name, node)
        for stmt in node.body:
            self.visit(stmt)

    def visit_Name(self, node: ast.Name) -> None:
        """Visit a name reference."""
        # Record which binding this identifier refers to
        assert self.current_scope is not None
        binding = self.current_scope.lookup(node.id)
        self.identifier_bindings[node] = binding

    def _add_assignment_bindings(self, target: ast.AST) -> None:
        """Add bindings created by an assignment target."""
        if isinstance(target, ast.Name):
            # Check if this variable is declared global or nonlocal
            scope_id = self.current_scope.scope_id if self.current_scope else -1
            is_global = scope_id in self.global_vars and target.id in self.global_vars[scope_id]
            is_nonlocal = (
                scope_id in self.nonlocal_vars and target.id in self.nonlocal_vars[scope_id]
            )

            # Only add as local binding if not global/nonlocal
            if not is_global and not is_nonlocal:
                assert self.current_scope is not None
                self.current_scope.add_binding(target.id, target)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                self._add_assignment_bindings(elt)
        # Other cases (subscript, attribute) don't create bindings

    def get_free_variables(self, nodes: List[ast.AST]) -> Set[str]:
        """
        Get free variables in a block of code.

        Free variables are identifiers that are referenced but not bound
        within the block.

        CRITICAL: Does NOT descend into nested function/class definitions,
        since those have separate scopes.

        Excludes Python builtins.
        """

        # Custom visitor that doesn't descend into nested functions
        class ScopeRespectingWalker(ast.NodeVisitor):
            def __init__(self) -> None:
                self.uses: Set[str] = set()
                self.bindings: Set[str] = set()
                self.used_before_assigned: Set[str] = set()  # Variables used before assignment
                self.assigned_so_far: Set[str] = set()  # Variables assigned so far in traversal
                self.global_vars: Set[str] = set()  # Variables declared global
                self.nonlocal_vars: Set[str] = set()  # Variables declared nonlocal

            def _extract_binding_names(self, target: ast.AST) -> Set[str]:
                """Extract variable names from an assignment target."""
                names: Set[str] = set()
                if isinstance(target, ast.Name):
                    names.add(target.id)
                elif isinstance(target, (ast.Tuple, ast.List)):
                    for elt in ast.walk(target):
                        if isinstance(elt, ast.Name) and isinstance(elt.ctx, ast.Store):
                            names.add(elt.id)
                return names

            def _with_new_scope(
                self, new_bindings: Set[str], body_visitor: Callable[[], None]
            ) -> None:
                """
                Execute body_visitor with new bindings in a nested scope.
                Bindings don't leak out, but uses of free variables are captured.
                """
                saved_bindings = self.bindings.copy()
                saved_assigned = self.assigned_so_far.copy()

                self.bindings.update(new_bindings)
                self.assigned_so_far.update(new_bindings)

                body_visitor()

                # Remove ALL variables bound in this scope from uses
                # This includes both new_bindings and any added during body_visitor
                scope_bindings = self.bindings - saved_bindings
                self.uses -= scope_bindings

                # Restore bindings (they don't leak out of nested scope)
                self.bindings = saved_bindings
                self.assigned_so_far = saved_assigned

            def _add_current_scope_bindings(self, new_bindings: Set[str]) -> None:
                """Add bindings to the current scope (they persist)."""
                self.bindings.update(new_bindings)
                self.assigned_so_far.update(new_bindings)

            def _bind_callable_parameters(self, args: ast.arguments) -> None:
                """Reused parameter binding logic from the DRY run (docs/DRY_RUN_2025-11-28.md)."""
                for name in _iter_argument_names(args):
                    self._add_current_scope_bindings({name})

            def _visit_loop_with_bindings(self, node: Union[ast.For, ast.AsyncFor]) -> None:
                """Shared traversal for sync/async loops (see docs/DRY_RUN_2025-11-28.md)."""
                self.visit(node.iter)
                loop_vars = self._extract_binding_names(node.target)
                self._add_current_scope_bindings(loop_vars)
                for stmt in node.body:
                    self.visit(stmt)
                for stmt in node.orelse:
                    self.visit(stmt)

            def _visit_with_like_block(self, node: Union[ast.With, ast.AsyncWith]) -> None:
                """Centralizes binding + visitation for with/async-with blocks (DRY run helper)."""
                for item in node.items:
                    self.visit(item.context_expr)
                    if item.optional_vars:
                        with_vars = self._extract_binding_names(item.optional_vars)
                        self._add_current_scope_bindings(with_vars)
                for stmt in node.body:
                    self.visit(stmt)

            def visit_Name(self, node: ast.Name) -> None:
                if isinstance(node.ctx, ast.Load):
                    self.uses.add(node.id)
                    # If variable used before any assignment, mark it
                    if node.id not in self.assigned_so_far:
                        self.used_before_assigned.add(node.id)
                elif isinstance(node.ctx, ast.Store):
                    # Check if this variable is global or nonlocal
                    if node.id in self.global_vars or node.id in self.nonlocal_vars:
                        # Global/nonlocal assignments are uses, not local bindings
                        self.uses.add(node.id)
                    else:
                        # Normal local binding
                        self.bindings.add(node.id)
                        self.assigned_so_far.add(node.id)
                # Continue visiting (though Name has no children)
                self.generic_visit(node)

            def visit_Global(self, node: ast.Global) -> None:
                """Track global declarations."""
                self.global_vars.update(node.names)

            def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
                """Track nonlocal declarations."""
                self.nonlocal_vars.update(node.names)

            def visit_Assign(self, node: ast.Assign) -> None:
                # CRITICAL: Visit RHS before LHS to correctly track used-before-assigned
                # In "x = y + 1", we must see the use of 'y' before marking 'x' as assigned
                self.visit(node.value)  # Visit RHS first
                for target in node.targets:
                    self.visit(target)  # Then visit LHS targets

            def visit_AugAssign(self, node: ast.AugAssign) -> None:
                # CRITICAL: Augmented assignments (x += 1) READ the variable
                # They don't DEFINE it, so target should be in uses, NOT bindings
                # (The variable must already exist for the augmented assignment to work)
                if isinstance(node.target, ast.Name):
                    self.uses.add(node.target.id)
                else:
                    # For subscripts (metrics["count"] += 1) or attributes (obj.x += 1),
                    # visit the target to capture the base variable
                    self.visit(node.target)
                # Visit the RHS value
                self.visit(node.value)

            def visit_For(self, node: ast.For) -> None:
                # Visit iterable first (before loop variable is bound)
                self._visit_loop_with_bindings(node)

            def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
                # Same as regular for loop
                self._visit_loop_with_bindings(node)

            def visit_With(self, node: ast.With) -> None:
                # Visit context expressions first
                self._visit_with_like_block(node)

            def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
                # Same as regular with
                self._visit_with_like_block(node)

            def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
                # Walrus operator: Visit RHS first, then add binding
                self.visit(node.value)
                if isinstance(node.target, ast.Name):
                    # Walrus bindings leak into the current scope
                    self.bindings.add(node.target.id)
                    self.assigned_so_far.add(node.target.id)

            def visit_comprehension(self, node: ast.comprehension) -> None:
                # Comprehension variable binds within comprehension scope
                # This is called from _visit_comprehension_node which handles scoping
                comp_vars = self._extract_binding_names(node.target)
                self._add_current_scope_bindings(comp_vars)
                # Visit the rest (iter, ifs)
                self.generic_visit(node)

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                # Function name binds in outer scope
                self._add_current_scope_bindings({node.name})

                # Visit function body in a nested scope to capture free variables
                # that the function closes over. This is important when the function
                # definition itself is part of the extracted code.
                def visit_func_body() -> None:
                    # Add function parameters as bindings in the nested scope
                    self._bind_callable_parameters(node.args)

                    # Visit function body
                    for stmt in node.body:
                        self.visit(stmt)

                self._with_new_scope(set(), visit_func_body)

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                # Same as FunctionDef
                # Type ignore needed because mypy doesn't recognize structural compatibility
                self.visit_FunctionDef(node)  # type: ignore[arg-type]

            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                # Class name binds in outer scope
                self._add_current_scope_bindings({node.name})
                # DON'T visit body - it's a different scope!

            def visit_Lambda(self, node: ast.Lambda) -> None:
                # Lambda creates a nested scope
                # Parameters bind within the lambda, but don't leak out
                # Free variables used in body are captured from outer scope
                def visit_body() -> None:
                    # Add lambda parameters as bindings
                    self._bind_callable_parameters(node.args)

                    # Visit lambda body
                    self.visit(node.body)

                self._with_new_scope(set(), visit_body)

            def visit_ListComp(self, node: ast.ListComp) -> None:
                # List comprehensions have their own scope (Python 3+)
                def visit_body() -> None:
                    _visit_comprehension_generators(self, node.generators)
                    self.visit(node.elt)

                self._with_new_scope(set(), visit_body)

            def visit_DictComp(self, node: ast.DictComp) -> None:
                # Dict comprehensions have their own scope
                def visit_body() -> None:
                    _visit_comprehension_generators(self, node.generators)
                    self.visit(node.key)
                    self.visit(node.value)

                self._with_new_scope(set(), visit_body)

            def visit_SetComp(self, node: ast.SetComp) -> None:
                # Set comprehensions have their own scope
                def visit_body() -> None:
                    _visit_comprehension_generators(self, node.generators)
                    self.visit(node.elt)

                self._with_new_scope(set(), visit_body)

            def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
                # Generator expressions have their own scope
                def visit_body() -> None:
                    _visit_comprehension_generators(self, node.generators)
                    self.visit(node.elt)

                self._with_new_scope(set(), visit_body)

            def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
                # Exception variable binds in current scope
                # In: except ValueError as e:
                #     The variable 'e' is bound here
                if node.name:
                    self._add_current_scope_bindings({node.name})
                # Visit the rest (type, body)
                self.generic_visit(node)

            def visit_Import(self, node: ast.Import) -> None:
                # import foo, bar as baz
                # Binds: foo, baz
                imports: Set[str] = set()
                for alias in node.names:
                    name = alias.asname if alias.asname else alias.name
                    imports.add(name)
                self._add_current_scope_bindings(imports)

            def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
                # from module import foo, bar as baz
                # Binds: foo, baz
                imports: Set[str] = set()
                for alias in node.names:
                    if alias.name == "*":
                        # from module import * - skip, can't determine bindings
                        continue
                    name = alias.asname if alias.asname else alias.name
                    imports.add(name)
                self._add_current_scope_bindings(imports)

        cache_key = tuple(id(node) for node in nodes)
        if cache_key:
            cached = self._free_var_cache.get(cache_key)
            if cached is not None:
                return set(cached)

        # Collect uses and bindings
        walker = ScopeRespectingWalker()
        for node in nodes:
            walker.visit(node)

        uses = walker.uses
        bindings = walker.bindings
        used_before_assigned = walker.used_before_assigned

        # Free variables are:
        # 1. Variables used but not bound (classic free variables)
        # 2. Variables used before they're assigned (shadowing cases)
        #
        # For case 2: If a variable is both used and assigned, Python treats it
        # as a local variable for the ENTIRE scope. If it's used before it's
        # assigned, we get UnboundLocalError unless it's passed as a parameter.
        # So we must include such variables in free_vars.
        free_vars = (uses - bindings) | (used_before_assigned & bindings)

        # Filter out Python builtins
        free_vars = filter_builtins(free_vars)

        if cache_key:
            self._free_var_cache[cache_key] = set(free_vars)
        return free_vars

    def get_binding_for_name(self, name_node: ast.Name) -> Optional[Binding]:
        """Get the binding for a name node."""
        return self.identifier_bindings.get(name_node)
