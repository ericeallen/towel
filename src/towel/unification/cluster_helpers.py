# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0.
"""Helper construction shared by cluster scans within one run.

A site unifies afresh and computes its current scope and value policy before
asking for a helper. Construction consumes that substitution and revises its
callee and thunk flags. Keep the resulting signature and flag changes only:
the next site generates its own call from its current expression nodes.
"""

from __future__ import annotations

import ast
from typing import NamedTuple, TYPE_CHECKING
from weakref import WeakKeyDictionary

from .bounded_cache import BoundedCache
from .function_scope import identifiers
from .statement_facts import memoized_per_node
from .structural_memo import StoredSubstitution, store_substitution, structural_id

if TYPE_CHECKING:
    from ..declared_python import PythonVersion
    from .models import HelperTemplate
    from .substitution import Substitution
    from .thunk_inlining import ValuePolicy


class BuiltClusterHelper(NamedTuple):
    """Immutable result of substituting, ordering parameters and inlining thunks."""

    body: str
    order: tuple[tuple[str, int], ...]
    callees: frozenset[str]
    inlined: frozenset[str]

    def restore_flags(self, substitution: Substitution) -> None:
        substitution.params_used_as_callee = set(self.callees)
        for parameter in self.inlined:
            substitution.function_params.pop(parameter, None)
        substitution.inlined_parameters.update(self.inlined)


# Substitution can replace any expression with a name carrying its location.
# The extractor orders parameters by their first textual use, so equal structure
# alone cannot identify the signature (for example, arguments of a dict display).
_Positions = tuple[tuple[int, int] | None, ...]


def _positions(statement: ast.AST) -> _Positions:
    start = getattr(statement, "lineno", 0)
    return tuple(
        (
            (node.lineno - start, node.col_offset)
            if isinstance(node, (ast.stmt, ast.expr)) and hasattr(node, "lineno")
            else None
        )
        for node in ast.walk(statement)
    )


class _Construction(NamedTuple):
    template_block: str
    positions: tuple[tuple[int, _Positions], ...]
    candidate_block: str
    substitution: StoredSubstitution
    source_parameters: tuple[str, ...]
    free_variables: frozenset[str]
    helper_name: str
    enclosing_names: frozenset[str]
    returns: tuple[str, ...]
    globals: frozenset[str]
    nonlocals: frozenset[str]
    value_producing: bool
    oldest_python: PythonVersion
    any_result: bool
    plain_values: bool
    available: tuple[frozenset[str], ...]
    certain_names: frozenset[str]


class ClusterHelperMemo:
    def __init__(self) -> None:
        self.built: BoundedCache[_Construction, BuiltClusterHelper] = BoundedCache(8192)
        self._positions: WeakKeyDictionary[ast.AST, _Positions] = WeakKeyDictionary()

    def key(
        self,
        template: HelperTemplate,
        candidate: list[ast.stmt],
        substitution: Substitution,
        policy: ValuePolicy,
        oldest_python: PythonVersion,
    ) -> _Construction:
        nodes = template.pair.block1_nodes
        start = nodes[0].lineno
        name = template.func_def.name
        # Enclosing names serve only to avoid a collision with the requested
        # name. The common collision-free case survives unrelated new bindings.
        enclosing = (
            frozenset(template.enclosing_names) if name in template.enclosing_names else frozenset()
        )
        # The policy looks up names in the two blocks' expressions and in the
        # helper assembled from the template, its declarations and parameters.
        names = (
            identifiers((*nodes, *candidate))
            | template.free_vars
            | set(template.return_variables)
            | template.globals_to_declare
            | template.nonlocals_to_declare
            | substitution.param_expressions.keys()
            | {v for variables in substitution.function_params.values() for v in variables}
        )
        return _Construction(
            structural_id(nodes),
            tuple(
                (stmt.lineno - start, memoized_per_node(self._positions, stmt, _positions))
                for stmt in nodes
            ),
            structural_id(candidate),
            store_substitution(substitution, (nodes, candidate), substitution.hygienic_renames),
            template.source_parameters,
            frozenset(template.free_vars),
            name,
            enclosing,
            template.return_variables,
            frozenset(template.globals_to_declare),
            frozenset(template.nonlocals_to_declare),
            template.is_value_producing,
            oldest_python,
            policy.any_result,
            policy.plain_values,
            tuple(frozenset(site & names) for site in policy.available),
            policy.certain_names & names,
        )
