#!/usr/bin/env python3
"""
Binding Detector for Nominal Unification

This module identifies all Python constructs that bind variables, which is
essential for implementing nominal unification as described in:
https://arxiv.org/pdf/1012.4890

Nominal unification handles α-equivalence: code that differs only in the
names of bound variables. For example:
    Block 1: user = {...}; validate(user)
    Block 2: admin = {...}; validate(admin)

These blocks are α-equivalent if 'user' and 'admin' play the same structural
role (both are bound variables with identical usage patterns).
"""

import ast
from typing import Set, Dict, List, Tuple
from dataclasses import dataclass
from enum import Enum


class BindingKind(Enum):
    """Types of variable bindings in Python."""
    ASSIGNMENT = "assignment"           # x = 1
    AUG_ASSIGNMENT = "aug_assignment"   # x += 1 (requires x to exist)
    FOR_LOOP = "for_loop"              # for x in iterable:
    COMPREHENSION = "comprehension"     # [x for x in ...]
    EXCEPTION = "exception"             # except E as e:
    WITH_STMT = "with"                 # with ... as x:
    FUNCTION_PARAM = "function_param"   # def f(x): or lambda x:
    NAMED_EXPR = "named_expr"          # if (x := foo()):
    MATCH_CASE = "match_case"          # case pattern as x:
    IMPORT = "import"                   # import x, from y import x
    FUNCTION_DEF = "function_def"       # def foo():
    CLASS_DEF = "class_def"            # class Foo:


@dataclass
class Binding:
    """Represents a variable binding."""
    name: str                    # Variable name
    kind: BindingKind           # Type of binding
    node: ast.AST               # AST node where binding occurs
    scope_node: ast.AST         # AST node defining the scope (function, class, module)
    line_number: int            # Line number for debugging


class BindingDetector(ast.NodeVisitor):
    """
    Detects all variable bindings in a Python AST.

    This visitor traverses the AST and identifies every construct that
    introduces a new variable binding, tracking:
    - The variable name
    - The kind of binding (assignment, loop, exception, etc.)
    - The AST node where the binding occurs
    - The scope in which the binding is visible
    """

    def __init__(self):
        self.bindings: List[Binding] = []
        self.scope_stack: List[ast.AST] = []  # Track nested scopes

    def _current_scope(self) -> ast.AST:
        """Get the current scope node (function, class, or module)."""
        return self.scope_stack[-1] if self.scope_stack else None

    def _add_binding(self, name: str, kind: BindingKind, node: ast.AST):
        """Record a variable binding."""
        self.bindings.append(Binding(
            name=name,
            kind=kind,
            node=node,
            scope_node=self._current_scope(),
            line_number=getattr(node, 'lineno', -1)
        ))

    def _extract_names_from_target(self, target: ast.AST, kind: BindingKind):
        """
        Extract all variable names from an assignment target.

        Handles complex targets like:
        - Simple names: x
        - Tuples: x, y = ...
        - Lists: [x, y] = ...
        - Starred: x, *rest = ...
        - Nested: (x, (y, z)) = ...

        Does NOT extract attribute assignments (x.y) or subscripts (x[i])
        as these don't create new bindings.
        """
        if isinstance(target, ast.Name):
            self._add_binding(target.id, kind, target)

        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                self._extract_names_from_target(elt, kind)

        elif isinstance(target, ast.Starred):
            self._extract_names_from_target(target.value, kind)

        # Ignore ast.Subscript (x[i] = ...) and ast.Attribute (x.y = ...)
        # as these modify existing objects rather than creating new bindings

    # Assignment statements

    def visit_Assign(self, node: ast.Assign):
        """Handle simple assignments: x = 1, x = y = 1, etc."""
        for target in node.targets:
            self._extract_names_from_target(target, BindingKind.ASSIGNMENT)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign):
        """Handle augmented assignments: x += 1, x *= 2, etc."""
        # Note: Augmented assignment requires the variable to already exist,
        # but it's still considered a binding for our purposes
        self._extract_names_from_target(node.target, BindingKind.AUG_ASSIGNMENT)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign):
        """Handle annotated assignments: x: int = 1"""
        if node.value is not None:  # x: int (without assignment) doesn't bind
            self._extract_names_from_target(node.target, BindingKind.ASSIGNMENT)
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr):
        """Handle walrus operator: if (x := foo()):"""
        self._extract_names_from_target(node.target, BindingKind.NAMED_EXPR)
        self.generic_visit(node)

    # Loop constructs

    def visit_For(self, node: ast.For):
        """Handle for loops: for x in iterable:"""
        self._extract_names_from_target(node.target, BindingKind.FOR_LOOP)
        self.generic_visit(node)

    def visit_comprehension(self, node: ast.comprehension):
        """
        Handle comprehension variables: [x for x in ...], {k: v for k, v in ...}

        Note: This is called for list/dict/set comprehensions and generators.
        """
        self._extract_names_from_target(node.target, BindingKind.COMPREHENSION)
        self.generic_visit(node)

    # Exception handling

    def visit_ExceptHandler(self, node: ast.ExceptHandler):
        """Handle exception binding: except Exception as e:"""
        if node.name:
            # In Python 3, node.name is a string, not an ast.Name
            self._add_binding(node.name, BindingKind.EXCEPTION, node)
        self.generic_visit(node)

    # Context managers

    def visit_With(self, node: ast.With):
        """Handle with statements: with open() as f:"""
        for item in node.items:
            if item.optional_vars:
                self._extract_names_from_target(item.optional_vars, BindingKind.WITH_STMT)
        self.generic_visit(node)

    # Function and class definitions

    def visit_FunctionDef(self, node: ast.FunctionDef):
        """Handle function definitions: def foo(x, y):"""
        # The function name itself is a binding in the enclosing scope
        self._add_binding(node.name, BindingKind.FUNCTION_DEF, node)

        # Enter the function scope for processing parameters and body
        self.scope_stack.append(node)

        # Function parameters are bindings in the function scope
        for arg in node.args.args:
            self._add_binding(arg.arg, BindingKind.FUNCTION_PARAM, arg)

        # Handle vararg (*args) and kwarg (**kwargs)
        if node.args.vararg:
            self._add_binding(node.args.vararg.arg, BindingKind.FUNCTION_PARAM, node.args.vararg)
        if node.args.kwarg:
            self._add_binding(node.args.kwarg.arg, BindingKind.FUNCTION_PARAM, node.args.kwarg)

        # Handle keyword-only arguments
        for arg in node.args.kwonlyargs:
            self._add_binding(arg.arg, BindingKind.FUNCTION_PARAM, arg)

        # Handle positional-only arguments (Python 3.8+)
        for arg in node.args.posonlyargs:
            self._add_binding(arg.arg, BindingKind.FUNCTION_PARAM, arg)

        # Visit function body
        for stmt in node.body:
            self.visit(stmt)

        # Exit function scope
        self.scope_stack.pop()

        # Visit decorators (in the enclosing scope)
        for decorator in node.decorator_list:
            self.visit(decorator)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        """Handle async function definitions: async def foo():"""
        # Treat the same as regular function definitions
        self.visit_FunctionDef(node)

    def visit_Lambda(self, node: ast.Lambda):
        """Handle lambda expressions: lambda x, y: x + y"""
        # Enter lambda scope
        self.scope_stack.append(node)

        # Lambda parameters
        for arg in node.args.args:
            self._add_binding(arg.arg, BindingKind.FUNCTION_PARAM, arg)

        if node.args.vararg:
            self._add_binding(node.args.vararg.arg, BindingKind.FUNCTION_PARAM, node.args.vararg)
        if node.args.kwarg:
            self._add_binding(node.args.kwarg.arg, BindingKind.FUNCTION_PARAM, node.args.kwarg)

        for arg in node.args.kwonlyargs:
            self._add_binding(arg.arg, BindingKind.FUNCTION_PARAM, arg)

        for arg in node.args.posonlyargs:
            self._add_binding(arg.arg, BindingKind.FUNCTION_PARAM, arg)

        # Visit lambda body
        self.visit(node.body)

        # Exit lambda scope
        self.scope_stack.pop()

    def visit_ClassDef(self, node: ast.ClassDef):
        """Handle class definitions: class Foo:"""
        # The class name is a binding in the enclosing scope
        self._add_binding(node.name, BindingKind.CLASS_DEF, node)

        # Enter class scope
        self.scope_stack.append(node)

        # Visit class body
        for stmt in node.body:
            self.visit(stmt)

        # Exit class scope
        self.scope_stack.pop()

        # Visit decorators and bases (in enclosing scope)
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)

    # Import statements

    def visit_Import(self, node: ast.Import):
        """Handle import statements: import x, import y as z"""
        for alias in node.names:
            # Use the alias if provided, otherwise the module name
            name = alias.asname if alias.asname else alias.name
            self._add_binding(name, BindingKind.IMPORT, node)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        """Handle from-import statements: from x import y, from x import y as z"""
        for alias in node.names:
            if alias.name == '*':
                # from x import * - skip, as it's non-specific
                continue
            name = alias.asname if alias.asname else alias.name
            self._add_binding(name, BindingKind.IMPORT, node)
        self.generic_visit(node)

    # Match statements (Python 3.10+)

    def visit_Match(self, node: ast.Match):
        """Handle match statements: match x: case pattern:"""
        # Visit the subject
        self.visit(node.subject)

        # Visit each case
        for case in node.cases:
            # Extract bindings from the pattern
            self._extract_pattern_bindings(case.pattern)

            # Visit guard and body
            if case.guard:
                self.visit(case.guard)
            for stmt in case.body:
                self.visit(stmt)

    def _extract_pattern_bindings(self, pattern: ast.AST):
        """Extract variable bindings from match patterns."""
        if isinstance(pattern, ast.MatchAs):
            # case pattern as x:
            if pattern.name:
                # Create a pseudo-node for the binding
                self._add_binding(pattern.name, BindingKind.MATCH_CASE, pattern)
            if pattern.pattern:
                self._extract_pattern_bindings(pattern.pattern)

        elif isinstance(pattern, ast.MatchOr):
            # case pattern1 | pattern2:
            for subpattern in pattern.patterns:
                self._extract_pattern_bindings(subpattern)

        elif isinstance(pattern, ast.MatchSequence):
            # case [x, y]:
            for subpattern in pattern.patterns:
                self._extract_pattern_bindings(subpattern)

        elif isinstance(pattern, ast.MatchMapping):
            # case {"key": value}:
            for subpattern in pattern.patterns:
                self._extract_pattern_bindings(subpattern)
            if pattern.rest:
                self._add_binding(pattern.rest, BindingKind.MATCH_CASE, pattern)

        elif isinstance(pattern, ast.MatchClass):
            # case ClassName(x, y):
            for subpattern in pattern.patterns:
                self._extract_pattern_bindings(subpattern)
            for subpattern in pattern.kwd_patterns:
                self._extract_pattern_bindings(subpattern)

        elif isinstance(pattern, ast.MatchStar):
            # case [*rest]:
            if pattern.name:
                self._add_binding(pattern.name, BindingKind.MATCH_CASE, pattern)

        # Other patterns (MatchValue, MatchSingleton) don't bind variables


def detect_bindings(tree: ast.AST) -> List[Binding]:
    """
    Detect all variable bindings in an AST.

    Args:
        tree: The AST to analyze

    Returns:
        List of all variable bindings found
    """
    detector = BindingDetector()
    detector.visit(tree)
    return detector.bindings


def get_bound_variables(tree: ast.AST, scope_node: ast.AST = None) -> Set[str]:
    """
    Get the set of all variable names bound in an AST.

    Args:
        tree: The AST to analyze
        scope_node: If provided, only return bindings in this scope

    Returns:
        Set of variable names
    """
    bindings = detect_bindings(tree)

    if scope_node is not None:
        bindings = [b for b in bindings if b.scope_node == scope_node]

    return {b.name for b in bindings}


def get_bindings_by_kind(tree: ast.AST, kind: BindingKind) -> List[Binding]:
    """
    Get all bindings of a specific kind.

    Args:
        tree: The AST to analyze
        kind: The kind of binding to filter for

    Returns:
        List of bindings of the specified kind
    """
    bindings = detect_bindings(tree)
    return [b for b in bindings if b.kind == kind]
