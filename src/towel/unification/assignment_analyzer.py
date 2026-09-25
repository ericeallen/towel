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
Assignment analyzer for distinguishing initial bindings from reassignments.

Every binding a function's own scope makes is classified as either:
- an initial binding: the first binding of the name (it creates the variable), or
- a reassignment: the name was already bound, by a parameter or an earlier binding.

This is critical for safe code extraction:
- Extracting code with an initial binding is safe
- Extracting code with a reassignment WITHOUT the initial binding is unsafe

A binding is any construct that stores a name in the scope, whatever its
spelling: an assignment, augmented or annotated assignment, walrus (also
inside a comprehension, where it binds in the function), ``for`` and
``with`` targets, an import, an ``except ... as`` name, a ``match`` capture,
a nested ``def`` or ``class``, and a ``type`` alias. What the scope's
nested functions, lambdas, comprehensions and class bodies bind is theirs,
not the function's; what those definitions evaluate where they stand
(decorators, defaults, annotations, bases) runs in the function and may bind
there.
"""

import ast
from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterable, List, Sequence, Set, Tuple, Union
from weakref import WeakKeyDictionary

from .statement_facts import imported_binding_name, memoized_per_node
from .models import FunctionNode
from .parameters import parameter_names
from .visitors import (
    OwnScopeVisitor,
    annotation_expressions,
    evaluated_before_definition,
    visit_comprehension_result,
    visit_each,
)


@dataclass(frozen=True)
class Binding:
    """One place a scope's own code binds one name.

    ``node_id`` is the ``id`` of the node that performs the binding (a
    ``Name`` in store context, an import alias, an except handler, a match
    capture pattern, or a ``def``/``class`` statement); the node itself is
    not held, so a memoized list of bindings cannot keep its statement
    alive. ``reads_first`` marks an augmented assignment, which reads the
    name before it rebinds it and so always needs an earlier binding;
    ``deleted_on_exit`` an ``except ... as`` name, which the clause deletes
    as it ends.
    """

    node_id: int
    name: str
    reads_first: bool = False
    deleted_on_exit: bool = False


class _OwnScopeBindings(OwnScopeVisitor):
    """Collect the bindings one scope's own code makes, in evaluation order."""

    def __init__(self) -> None:
        self.found: List[Binding] = []

    def _bind(
        self, node: ast.AST, name: str, *, reads_first: bool = False, deleted_on_exit: bool = False
    ) -> None:
        self.found.append(Binding(id(node), name, reads_first, deleted_on_exit))

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store):
            self._bind(node, node.id)

    def visit_Assign(self, node: ast.Assign) -> None:
        # The value is evaluated before any target is bound.
        self.visit(node.value)
        visit_each(self, node.targets)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if isinstance(node.target, ast.Name):
            self.visit(node.value)
            self._bind(node.target, node.target.id, reads_first=True)
        else:
            self.visit(node.target)
            self.visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        # An annotation in a function body is never evaluated. Without a value
        # nothing is bound: ``x: int`` leaves ``x`` as it was, and
        # ``obj.attr: int`` only evaluates ``obj``.
        if node.value is None:
            if not isinstance(node.target, ast.Name):
                self.visit(node.target)
            return
        self.visit(node.value)
        self.visit(node.target)

    def visit_For(self, node: Union[ast.For, ast.AsyncFor]) -> None:
        self.visit(node.iter)
        self.visit(node.target)
        visit_each(self, node.body)
        visit_each(self, node.orelse)

    visit_AsyncFor = visit_For

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        self.visit(node.target)

    def visit_Import(self, node: Union[ast.Import, ast.ImportFrom]) -> None:
        for alias in node.names:
            name = imported_binding_name(alias)
            if name is not None:
                self._bind(alias, name)

    visit_ImportFrom = visit_Import

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is not None:
            self.visit(node.type)
        if node.name:
            self._bind(node, node.name, deleted_on_exit=True)
        visit_each(self, node.body)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.pattern is not None:
            self.visit(node.pattern)
        if node.name:
            self._bind(node, node.name)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name:
            self._bind(node, node.name)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        visit_each(self, node.keys)
        visit_each(self, node.patterns)
        if node.rest:
            self._bind(node, node.rest)

    def _nested_function(self, node: FunctionNode) -> None:
        # Decorators, defaults and annotations run here; the body is another scope.
        visit_each(self, evaluated_before_definition(node))
        visit_each(self, annotation_expressions(node))
        self._bind(node, node.name)

    def _nested_class(self, node: ast.ClassDef) -> None:
        # The class body binds the class's attributes, not the function's names.
        visit_each(self, evaluated_before_definition(node))
        visit_each(self, node.bases)
        visit_each(self, [keyword.value for keyword in node.keywords])
        self._bind(node, node.name)

    def _lambda(self, node: ast.Lambda) -> None:
        # Only the defaults run here; the body is the lambda's own scope.
        visit_each(self, evaluated_before_definition(node))

    def _comprehension(
        self, node: Union[ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp]
    ) -> None:
        # The targets are the comprehension's own; a walrus anywhere else in
        # it binds in the enclosing function.
        for generator in node.generators:
            self.visit(generator.iter)
            visit_each(self, generator.ifs)
        visit_comprehension_result(self, node)


_BINDINGS: "WeakKeyDictionary[ast.AST, Tuple[Binding, ...]]" = WeakKeyDictionary()


def _statement_bindings(statement: ast.AST) -> Tuple[Binding, ...]:
    collector = _OwnScopeBindings()
    collector.visit(statement)
    return tuple(collector.found)


def own_scope_bindings(nodes: Iterable[ast.AST]) -> List[Binding]:
    """The bindings ``nodes`` make in their own scope, in evaluation order.

    Computed once per node: the result depends on the node's structure alone.
    """
    return [
        binding
        for node in nodes
        for binding in memoized_per_node(_BINDINGS, node, _statement_bindings)
    ]


def scope_declarations(function: FunctionNode) -> FrozenSet[str]:
    """The names ``function``'s own scope declares ``global`` or ``nonlocal``, at any depth.

    A declaration anywhere in the function's own code (inside an ``if``, a
    loop, a handler) covers the whole scope; one in a nested function or
    class is that scope's.
    """
    declared: Set[str] = set()
    pending: List[ast.AST] = list(function.body)
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            declared.update(node.names)
        elif not isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
        ):
            pending.extend(ast.iter_child_nodes(node))
    return frozenset(declared)


def analyze_assignments(func: FunctionNode) -> Dict[int, bool]:
    """
    Classify every binding of ``func``'s own scope as initial or a reassignment.

    Args:
        func: Function definition to analyze

    Returns:
        Dictionary mapping the ``Binding.node_id`` of each binding (see
        ``own_scope_bindings``) to is_reassignment:
        - True: the name was already bound, by a parameter or an earlier binding,
          or the binding is an augmented assignment, which reads the name first
        - False: the first binding of the name

    Example:
        def foo(x):
            result = x * 2        # Initial binding: False
            for result in x:      # Reassignment: True
                pass
            return result
    """
    bound: Set[str] = set(parameter_names(func.args))
    reassignments: Dict[int, bool] = {}
    for binding in own_scope_bindings(func.body):
        reassignments[binding.node_id] = binding.reads_first or binding.name in bound
        bound.add(binding.name)
    return reassignments


def has_reassignments_without_bindings(
    func: FunctionNode,
    block_nodes: Sequence[ast.stmt],
    reassignments: Dict[int, bool],
) -> Tuple[bool, Set[str]]:
    """
    Check if a code block contains reassignments without initial bindings.

    This is the validation function for safe extraction. A block is unsafe
    to extract if it rebinds, by any binding construct, a variable that was
    initially bound outside the block: the helper would bind a local of its
    own, so a read after a rebinding that did not happen (an empty loop, an
    unmatched case, an untaken branch) or after the block would not see the
    caller's binding.

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

    problematic_vars = reassigned_in_block - bound_in_block

    # Relaxation: a name declared global or nonlocal is not a local of the
    # function; the helper re-declares it and writes the same variable.
    remaining = problematic_vars - scope_declarations(func)

    return (len(remaining) > 0, remaining)


def _collect_bindings_and_reassignments(
    node: ast.AST, reassignments: Dict[int, bool], bound_vars: Set[str], reassigned_vars: Set[str]
) -> None:
    """
    Add the variables ``node`` binds in its own scope, and keeps bound, to the caller's sets.

    An ``except ... as`` name is left out: the clause deletes it as it ends,
    so it is bound neither after the statement nor, by it, before a later
    block. Rebinding a name bound before the block that way deletes that
    binding, which ``unbinds_external_name`` declines.

    Args:
        node: AST node to analyze
        reassignments: Assignment classification mapping from analyze_assignments()
        bound_vars: Set to add initially-bound variables to
        reassigned_vars: Set to add reassigned variables to (augmented
            assignments always count as reassignments)
    """
    for binding in own_scope_bindings([node]):
        if binding.deleted_on_exit:
            continue
        reassigned = binding.reads_first or reassignments.get(binding.node_id, False)
        (reassigned_vars if reassigned else bound_vars).add(binding.name)


def stored_names(target: ast.AST) -> Set[str]:
    """Names an assignment target binds: a name, or every name inside a tuple, list or star."""
    return {
        node.id
        for node in ast.walk(target)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    }


def _collect_block_binding_stats(
    block_nodes: Sequence[ast.stmt], reassignments: Dict[int, bool]
) -> Tuple[Set[str], Set[str]]:
    """Return (bound_in_block, reassigned_in_block) for the given nodes."""
    bound_in_block: Set[str] = set()
    reassigned_in_block: Set[str] = set()
    for node in block_nodes:
        _collect_bindings_and_reassignments(
            node, reassignments, bound_in_block, reassigned_in_block
        )
    return bound_in_block, reassigned_in_block
