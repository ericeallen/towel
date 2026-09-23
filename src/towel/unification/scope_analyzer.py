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

"""Lexical scopes of a module: each binding's scope, each name node's
resolution, and the external-binding hazards (``global``/``nonlocal``
rebinding, namespace reflection) later guards consult.
"""

import ast
from typing import Dict, FrozenSet, Iterator, List, Literal, Optional, Sequence, Set, Tuple, Union
from dataclasses import dataclass, field
from .builtins import filter_builtins
from .models import FunctionNode
from .statement_facts import import_binding_names, pattern_capture_names  # noqa: F401
from .parameters import parameter_names, parameter_nodes
from .visitors import (
    ScopeVisitor,
    annotation_expressions,
    evaluated_before_definition,
    type_parameter_expressions,
)


def type_parameter_names(node: ast.AST) -> FrozenSet[str]:
    """Names bound by a PEP 695 definition's annotation scope.

    These are separate from the function's arguments and a class's attributes.
    Attribute inspection keeps the analyzer importable on Python 3.11, whose
    AST has neither ``type_params`` nor the type-parameter node classes.
    """
    parameters: object = getattr(node, "type_params", ())
    if not isinstance(parameters, list):
        return frozenset()
    names: Set[str] = set()
    for parameter in parameters:
        name: object = getattr(parameter, "name", None)
        if isinstance(name, str):
            names.add(name)
    return frozenset(names)


def pattern_expressions(pattern: ast.AST) -> Iterator[ast.expr]:
    """The expressions a match pattern evaluates as it matches, at any depth.

    A value pattern evaluates its literal or dotted name, a class pattern its
    class, and a mapping pattern its keys; ``case kind():`` reads ``kind``
    as surely as ``kind()`` would. Captures bind names and read none.
    """
    for node in ast.walk(pattern):
        if isinstance(node, ast.MatchValue):
            yield node.value
        elif isinstance(node, ast.MatchClass):
            yield node.cls
        elif isinstance(node, ast.MatchMapping):
            yield from node.keys


@dataclass(frozen=True)
class ExternalBindingHazards:
    """Module-wide binding hazards, immutable after lexical analysis completes."""

    rebound: FrozenSet[Tuple[int, str]]
    unresolved_nonlocal: bool
    reflective: bool


@dataclass
class ScopeBinding:
    """Represents a binding of an identifier to a value."""

    name: str
    scope_id: int  # Unique identifier for the scope
    node: ast.AST  # The AST node that creates this binding


@dataclass
class Scope:
    """Represents a lexical scope."""

    scope_id: int
    parent: Optional["Scope"]
    bindings: Dict[str, ScopeBinding] = field(default_factory=dict)
    children: List["Scope"] = field(default_factory=list)

    def lookup(self, name: str) -> Optional[ScopeBinding]:
        """Lookup a binding in this scope or parent scopes."""
        if name in self.bindings:
            return self.bindings[name]
        if self.parent:
            return self.parent.lookup(name)
        return None

    def add_binding(self, name: str, node: ast.AST) -> None:
        """Add a binding to this scope."""
        self.bindings[name] = ScopeBinding(name, self.scope_id, node)


_ScopeKind = Literal["block", "function", "comprehension", "class", "annotation"]


@dataclass
class _WalkedScope:
    """A scope the walker is inside, with the reads it has yet to resolve.

    ``plain`` reads were made in the scope itself, ``nested`` ones came out of
    a function, lambda, comprehension or class body within it. A class body's
    own names shadow only the plain ones: what is defined inside a class
    looks names up past it. An annotation scope (a generic definition's
    type parameters, a ``type`` statement's value) sees its class's names,
    and keeps the distinction for what it encloses, since the function body
    of a generic method still looks past the class.
    """

    kind: _ScopeKind
    assigned_before: Set[str]
    bindings: Set[str] = field(default_factory=set)
    plain: Set[str] = field(default_factory=set)
    nested: Set[str] = field(default_factory=set)
    pending_name: Optional[str] = None
    """A generic definition's own name, bound in the enclosing scope once its annotation scope ends."""


_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


class _ScopeRespectingWalker(ScopeVisitor):
    """Collect the names a block reads and binds, following Python's scopes.

    ``uses`` are the names read from the block's own scope, ``bindings`` the
    names bound in it, and ``used_before_assigned`` the names read before the
    block binds them, which Python would treat as locals of the whole
    function. Nested scopes are entered: what a nested function, lambda,
    comprehension or class reads and does not bind itself is read from the
    block's scope too. Everything a definition evaluates where it stands
    counts as read there: decorators, defaults, annotations (unless the
    module postpones them), class bases and keywords, and the lazily
    evaluated bounds of type parameters and values of ``type`` statements.
    """

    def __init__(self, *, postponed_annotations: bool = False) -> None:
        self.postponed_annotations = postponed_annotations
        self._scopes: List[_WalkedScope] = [_WalkedScope("block", set())]
        self._annotation_scopes: Dict[ast.AST, _WalkedScope] = {}
        self.used_before_assigned: Set[str] = set()  # Variables used before assignment
        self.assigned_so_far: Set[str] = set()  # Variables assigned so far in traversal
        self.global_vars: Set[str] = set()  # Variables declared global
        self.nonlocal_vars: Set[str] = set()  # Variables declared nonlocal

    @property
    def uses(self) -> Set[str]:
        """The names read from the block's own scope, by it or by the scopes nested in it."""
        block = self._scopes[0]
        return block.plain | block.nested

    @property
    def bindings(self) -> Set[str]:
        """The names bound in the block's own scope."""
        return self._scopes[0].bindings

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

    # -- scopes ------------------------------------------------------------

    def _enter_scope(self, node: ast.AST) -> None:
        """Begin a nested scope: bindings made inside will not leak out."""
        kind: _ScopeKind = (
            "class"
            if isinstance(node, ast.ClassDef)
            else "comprehension" if isinstance(node, _COMPREHENSIONS) else "function"
        )
        self._push(kind)

    def _leave_scope(self, node: ast.AST) -> None:
        """End the nested scope, keeping only the reads it left free."""
        self._pop()

    def _push(self, kind: _ScopeKind) -> _WalkedScope:
        scope = _WalkedScope(kind, set(self.assigned_so_far))
        self._scopes.append(scope)
        return scope

    def _pop(self) -> None:
        scope = self._scopes.pop()
        self.assigned_so_far = scope.assigned_before
        enclosing = self._scopes[-1]
        if scope.kind == "annotation":
            enclosing.plain |= scope.plain - scope.bindings
            enclosing.nested |= scope.nested - scope.bindings
        elif scope.kind == "class":
            enclosing.nested |= (scope.plain - scope.bindings) | scope.nested
        else:
            enclosing.nested |= (scope.plain | scope.nested) - scope.bindings

    # -- what a definition evaluates where it stands -----------------------

    def _visit_definition_head(
        self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda]
    ) -> None:
        """Decorators and defaults here; type parameters, annotations, bases in the annotation scope.

        A generic definition's annotations (and a generic class's bases and
        keywords) are evaluated in its annotation scope, where its type
        parameters are bound; that scope stays open around the definition's
        own scope and is closed in ``_visit_definition_tail``.
        """
        for expression in evaluated_before_definition(node):
            self.visit(expression)
        if isinstance(node, ast.Lambda):
            return
        parameters = type_parameter_names(node)
        if parameters:
            scope = self._push("annotation")
            self._annotation_scopes[node] = scope
            self._add_current_scope_bindings(set(parameters))
            for expression in type_parameter_expressions(node):
                self.visit(expression)
        if isinstance(node, ast.ClassDef):
            for expression in [*node.bases, *(keyword.value for keyword in node.keywords)]:
                self.visit(expression)
        elif not self.postponed_annotations:
            for expression in annotation_expressions(node):
                self.visit(expression)

    def _bind_definition_name(
        self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef]
    ) -> None:
        scope = self._annotation_scopes.get(node)
        if scope is None:
            self._add_current_scope_bindings({node.name})
            return
        # Bound in the enclosing scope once the annotation scope ends; until
        # then the name is assigned, so the body may call itself.
        scope.pending_name = node.name
        self.assigned_so_far.add(node.name)

    def _visit_definition_tail(
        self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef]
    ) -> None:
        scope = self._annotation_scopes.pop(node, None)
        if scope is None:
            return
        self._pop()
        if scope.pending_name is not None:
            self._add_current_scope_bindings({scope.pending_name})

    def visit_TypeAlias(self, node: ast.AST) -> None:
        """``type X[T] = value``: X binds here; the value is read lazily, in an annotation scope."""
        name = getattr(node, "name", None)
        if isinstance(name, ast.AST):
            self.visit(name)
        self._push("annotation")
        self._add_current_scope_bindings(set(type_parameter_names(node)))
        for expression in type_parameter_expressions(node):
            self.visit(expression)
        value = getattr(node, "value", None)
        if isinstance(value, ast.AST):
            self.visit(value)
        self._pop()

    # -- bindings ----------------------------------------------------------

    def _bind_parameters(self, args: ast.arguments) -> None:
        self._bind_callable_parameters(args)

    def _bind_target(self, target: ast.AST) -> None:
        self._add_current_scope_bindings(self._extract_binding_names(target))

    def _add_current_scope_bindings(self, new_bindings: Set[str]) -> None:
        """Add bindings to the current scope (they persist)."""
        self._scopes[-1].bindings.update(new_bindings)
        self.assigned_so_far.update(new_bindings)

    def _read(self, name: str) -> None:
        """Record a read in the current scope."""
        self._scopes[-1].plain.add(name)

    def _bind_callable_parameters(self, args: ast.arguments) -> None:
        """Bind a callable's parameters as locals of the current scope."""
        for name in parameter_names(args):
            self._add_current_scope_bindings({name})

    def _visit_loop_with_bindings(self, node: Union[ast.For, ast.AsyncFor]) -> None:
        """Visit a for/async-for loop, binding its target in the current scope."""
        self.visit(node.iter)
        loop_vars = self._extract_binding_names(node.target)
        self._add_current_scope_bindings(loop_vars)
        for stmt in node.body:
            self.visit(stmt)
        for stmt in node.orelse:
            self.visit(stmt)

    def _visit_with_like_block(self, node: Union[ast.With, ast.AsyncWith]) -> None:
        """Visit a with/async-with block, binding each ``as`` target in the current scope."""
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars:
                with_vars = self._extract_binding_names(item.optional_vars)
                self._add_current_scope_bindings(with_vars)
        for stmt in node.body:
            self.visit(stmt)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self._read(node.id)
            # If variable used before any assignment, mark it
            if node.id not in self.assigned_so_far:
                self.used_before_assigned.add(node.id)
        elif isinstance(node.ctx, ast.Store):
            if node.id in self.global_vars or node.id in self.nonlocal_vars:
                # Global/nonlocal assignments are uses, not local bindings
                self._read(node.id)
            else:
                # Normal local binding
                self._add_current_scope_bindings({node.id})
        # Continue visiting (though Name has no children)
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        """Track global declarations."""
        self.global_vars.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        """Track nonlocal declarations."""
        self.nonlocal_vars.update(node.names)

    def visit_Assign(self, node: ast.Assign) -> None:
        # The right-hand side is evaluated before the targets bind, so a name
        # read there counts as used before assigned.
        # In "x = y + 1", we must see the use of 'y' before marking 'x' as assigned
        self.visit(node.value)  # Visit RHS first
        for target in node.targets:
            self.visit(target)  # Then visit LHS targets

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        # Inside a function body no annotation is evaluated, whatever the
        # target, so names that appear only in one are not free variables
        # (astroid: a class imported under TYPE_CHECKING). A class body
        # evaluates its annotations unless the module postpones them.
        if self._scopes[-1].kind == "class" and not self.postponed_annotations:
            self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.value)
            self.visit(node.target)
        elif not isinstance(node.target, ast.Name):
            # ``obj.attr: T`` evaluates ``obj``; nothing is assigned.
            self.visit(node.target)
        # A bare ``x: T`` assigns nothing: x keeps whatever it was bound to,
        # so a later read of x is still the caller's x.

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        # An augmented assignment reads its target, which must already be
        # bound, so the target is a use, not a binding.
        if isinstance(node.target, ast.Name):
            self._read(node.target.id)
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
        # Walrus operator: visit the value first, then bind the target in the
        # nearest scope that is not a comprehension, as Python does.
        self.visit(node.value)
        if isinstance(node.target, ast.Name):
            for scope in reversed(self._scopes):
                if scope.kind != "comprehension":
                    scope.bindings.add(node.target.id)
                    break
            self.assigned_so_far.add(node.target.id)

    def visit_Match(self, node: ast.Match) -> None:
        # What a pattern evaluates is read before its captures bind, in the
        # enclosing function scope, where the captures bind too.
        self.visit(node.subject)
        for case in node.cases:
            for expression in pattern_expressions(case.pattern):
                self.visit(expression)
            self._add_current_scope_bindings(pattern_capture_names(case.pattern))
            if case.guard:
                self.visit(case.guard)
            for stmt in case.body:
                self.visit(stmt)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        # Exception variable binds in current scope
        # In: except ValueError as e:
        #     The variable 'e' is bound here
        if node.name:
            self._add_current_scope_bindings({node.name})
        # Visit the rest (type, body)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        # ``import a.b`` binds ``a``, ``import a.b as c`` binds ``c``.
        self._add_current_scope_bindings(set(import_binding_names(node)))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        # ``from m import *`` binds nothing nameable.
        self._add_current_scope_bindings(set(import_binding_names(node)))


def _postpones_annotations(tree: Optional[ast.AST]) -> bool:
    """Whether ``tree`` is a module written under ``from __future__ import annotations``."""
    return isinstance(tree, ast.Module) and any(
        isinstance(statement, ast.ImportFrom)
        and statement.module == "__future__"
        and any(alias.name == "annotations" for alias in statement.names)
        for statement in tree.body
    )


class ScopeAnalyzer(ScopeVisitor):
    """
    Analyze scopes and identifier bindings in an AST.

    This builds a scope tree and tracks which identifiers refer to which
    bindings in the enclosing environment.
    """

    def __init__(self) -> None:
        self._external_binding_hazards: Optional[ExternalBindingHazards] = None
        self.analyzed_tree: Optional[ast.AST] = None
        self.scope_counter = 0
        self.root_scope: Scope = self._create_scope(None)
        self.current_scope: Scope = self.root_scope

        self.node_scopes: Dict[ast.AST, Scope] = {}
        self.scope_nodes: Dict[int, ast.AST] = {}

        self.identifier_bindings: Dict[ast.Name, Optional[ScopeBinding]] = {}

        # Maps scope_id -> set of variable names
        self.global_vars: Dict[int, Set[str]] = {}
        self.nonlocal_vars: Dict[int, Set[str]] = {}

        # Cache for free-variable analysis (keyed by node identity tuple)
        self._free_var_cache: Dict[Tuple[ast.AST, ...], Set[str]] = {}

    def analyze(self, tree: ast.AST) -> Scope:
        """Analyze an AST and return the root scope."""
        self._external_binding_hazards = None
        self.node_scopes.clear()
        self.identifier_bindings.clear()
        self.global_vars.clear()
        self.nonlocal_vars.clear()
        self._free_var_cache.clear()
        if self.analyzed_tree is not None:
            # A second analysis gets a root of its own; the first uses the
            # one built at construction, so its scope ids start at zero.
            self.root_scope = self._create_scope(None)
        self.analyzed_tree = tree
        self.current_scope = self.root_scope
        self.visit(tree)
        self._external_binding_hazards = self._summarize_external_binding_hazards(tree)
        return self.root_scope

    @property
    def external_binding_hazards(self) -> Optional[ExternalBindingHazards]:
        return self._external_binding_hazards

    def _summarize_external_binding_hazards(self, tree: ast.AST) -> ExternalBindingHazards:
        root = self.root_scope
        scopes = {root.scope_id: root}
        scopes.update((item.scope_id, item) for item in self.node_scopes.values())
        rebound: Set[Tuple[int, str]] = set()
        unresolved = False
        for names in self.global_vars.values():
            rebound.update((root.scope_id, name) for name in names)
        for scope_id, names in self.nonlocal_vars.items():
            declaring = scopes.get(scope_id)
            for name in names:
                binding = declaring.parent.lookup(name) if declaring and declaring.parent else None
                if binding is None:
                    unresolved = True
                else:
                    rebound.add((binding.scope_id, name))

        # Include top-level statements, not just declarations retained as bindings.
        reflective = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id in {"globals", "locals", "exec", "eval"}:
                reflective = True
            elif node.func.id in {"getattr", "setattr", "delattr"} and node.args:
                target = node.args[0]
                target_binding = (
                    self.identifier_bindings.get(target) if isinstance(target, ast.Name) else None
                )
                if target_binding is None or isinstance(target_binding.node, ast.Import):
                    reflective = True
        return ExternalBindingHazards(frozenset(rebound), unresolved, reflective)

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
        self.scope_nodes[new_scope.scope_id] = node
        self.current_scope = new_scope
        return new_scope

    def is_method(self, function: FunctionNode) -> bool:
        """Whether ``function`` is defined directly in a class body.

        A function nested inside a method shares the method's lexical class for
        name mangling but has no receiver: its first parameter is an ordinary
        argument, so it must not be dispatched as a method.
        """
        scope = self.node_scopes.get(function)
        if scope is None or scope.parent is None:
            return False
        return isinstance(self.scope_nodes.get(scope.parent.scope_id), ast.ClassDef)

    def _exit_scope(self) -> None:
        """Exit the current scope."""
        if self.current_scope and self.current_scope.parent:
            self.current_scope = self.current_scope.parent

    # The traversal of definitions and comprehensions is ScopeVisitor's; the
    # analyzer supplies the scope objects and the bindings.

    def _bind_definition_name(
        self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef]
    ) -> None:
        self.current_scope.add_binding(node.name, node)

    def _leave_scope(self, node: ast.AST) -> None:
        self._exit_scope()

    def _bind_parameters(self, args: ast.arguments) -> None:
        for arg in parameter_nodes(args):
            self.current_scope.add_binding(arg.arg, arg)

    def _bind_target(self, target: ast.AST) -> None:
        self._add_assignment_bindings(target)

    def _lambda(self, node: ast.Lambda) -> None:
        """A lambda gets no scope of its own here.

        The analyzer's scopes are those of functions and classes, the ones
        helpers are placed in and methods dispatched from; a lambda's body is
        read as part of the scope it appears in, its parameters unbound, as
        it always was. ``free_variables`` handles lambdas by their own
        scope through ``_ScopeRespectingWalker``.
        """
        self.generic_visit(node)

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

    def visit_Assign(self, node: ast.Assign) -> None:
        """Visit an assignment."""
        # Visit RHS first
        self.visit(node.value)

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
            self.current_scope.add_binding(node.target.id, node.target)

    def visit_Global(self, node: ast.Global) -> None:
        """Visit a global statement."""
        self._record_scope_declaration(self.global_vars, node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        """Visit a nonlocal statement."""
        self._record_scope_declaration(self.nonlocal_vars, node.names)

    def visit_Import(self, node: ast.Import) -> None:
        for name in import_binding_names(node):
            self.current_scope.add_binding(name, node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for name in import_binding_names(node):
            self.current_scope.add_binding(name, node)

    def visit_Match(self, node: ast.Match) -> None:
        """Visit a match statement; capture patterns bind in the current scope."""
        self.visit(node.subject)
        for case in node.cases:
            for name in sorted(pattern_capture_names(case.pattern)):
                self.current_scope.add_binding(name, case.pattern)
            for expression in pattern_expressions(case.pattern):
                self.visit(expression)
            if case.guard:
                self.visit(case.guard)
            for stmt in case.body:
                self.visit(stmt)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type:
            self.visit(node.type)
        if node.name:
            self.current_scope.add_binding(node.name, node)
        for stmt in node.body:
            self.visit(stmt)

    def visit_Name(self, node: ast.Name) -> None:
        """Visit a name reference."""
        binding = self.current_scope.lookup(node.id)
        self.identifier_bindings[node] = binding

    def _add_assignment_bindings(self, target: ast.AST) -> None:
        """Add bindings created by an assignment target."""
        if isinstance(target, ast.Name):
            scope_id = self.current_scope.scope_id if self.current_scope else -1
            is_global = scope_id in self.global_vars and target.id in self.global_vars[scope_id]
            is_nonlocal = (
                scope_id in self.nonlocal_vars and target.id in self.nonlocal_vars[scope_id]
            )

            # Only add as local binding if not global/nonlocal
            if not is_global and not is_nonlocal:
                self.current_scope.add_binding(target.id, target)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                self._add_assignment_bindings(elt)
        # Other cases (subscript, attribute) don't create bindings

    def free_variables(self, nodes: Sequence[ast.AST]) -> Set[str]:
        """
        Get free variables in a block of code.

        Free variables are identifiers that are referenced but not bound
        within the block.

        Nested functions, lambdas, comprehensions and classes are scopes of
        their own: what they read and do not bind themselves is read from
        the block, and so is everything a definition evaluates where it
        stands (see ``_ScopeRespectingWalker``).

        Excludes Python builtins.
        """

        # Keyed by the nodes themselves: they hash by identity, and holding them
        # keeps an id from being reused by a later node within one analysis.
        cache_key = tuple(nodes)
        if cache_key:
            cached = self._free_var_cache.get(cache_key)
            if cached is not None:
                return set(cached)

        walker = _ScopeRespectingWalker(
            postponed_annotations=_postpones_annotations(self.analyzed_tree)
        )
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

        # A builtin spelling can be rebound in any lexical scope. Retain names
        # with a known binding; over-approximating shadows is safe because an
        # unshadowed builtin can also be passed explicitly to the helper.
        bound_names: Set[str] = set()
        scopes: List[Scope] = [self.root_scope]
        while scopes:
            scope = scopes.pop()
            bound_names.update(scope.bindings)
            scope_node = self.scope_nodes.get(scope.scope_id)
            if scope_node is not None:
                bound_names.update(type_parameter_names(scope_node))
            scopes.extend(scope.children)
        free_vars = filter_builtins(free_vars) | (free_vars & bound_names)

        if cache_key:
            self._free_var_cache[cache_key] = set(free_vars)
        return free_vars

    def get_binding_for_name(self, name_node: ast.Name) -> Optional[ScopeBinding]:
        """Get the binding for a name node."""
        return self.identifier_bindings.get(name_node)
