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
Detect orphaned variable references after code extraction.

An orphaned variable is one that is bound (assigned) in the extracted code
but referenced in code that remains after the extraction point.
"""

import ast
from typing import FrozenSet, Sequence, Set, Tuple

from .bounded_cache import BoundedCache
from .definite_assignment import definitely_bound_before_each
from .statement_facts import bindings_of, loaded_names
from .structural_memo import structural_id

_ORPHANS: BoundedCache[Tuple[str, int, int], FrozenSet[str]] = BoundedCache(65_536)
"""Orphaned names by (structural id of the body, block start, block end).

A block takes part in every pair it forms and each asks this of the same
body and indices; the answer is a function of the body's structure alone,
so the same function re-parsed after a rewrite hits too. Per process; the
workers fork after parsing and each keeps its own copy.
"""


def bound_names_in_block(nodes: Sequence[ast.stmt]) -> Set[str]:
    """The names a block binds in its function's own scope (see ``bindings_of``)."""
    names: Set[str] = set()
    for node in nodes:
        names |= bindings_of(node, into_nested_scopes=False)
    return names


def used_names(nodes: Sequence[ast.AST]) -> Set[str]:
    """Every name a block reads, nested scopes included."""
    names: Set[str] = set()
    for node in nodes:
        names |= loaded_names(node)
    return names


def orphaned_variables(
    function_body: Sequence[ast.stmt], extracted_block_range: Tuple[int, int]
) -> Set[str]:
    """The names later code would read that extracting the block leaves unbound.

    Args:
        function_body: All statements in the function
        extracted_block_range: (start_index, end_index) of block to extract
            These are 0-based indices into function_body

    Returns:
        The orphaned names; empty when the extraction leaves every read bound.
        A fresh set: callers subtract from it.
    """
    start_idx, end_idx = extracted_block_range
    key = (structural_id(function_body), start_idx, end_idx)
    cached = _ORPHANS.get(key)
    if cached is None:
        cached = _ORPHANS.put(
            key, frozenset(_orphaned_variables(function_body, start_idx, end_idx))
        )
    return set(cached)


def _orphaned_variables(
    function_body: Sequence[ast.stmt], start_idx: int, end_idx: int
) -> Set[str]:
    """See ``orphaned_variables``; this computes it."""
    # Get the extracted block and remaining code
    extracted_block = function_body[start_idx : end_idx + 1]
    remaining_code = function_body[end_idx + 1 :]

    if not remaining_code:
        # Nothing after the extracted block, so no orphans possible
        return set()

    # Get variables bound in the extracted block
    bound_in_extracted = bound_names_in_block(extracted_block)

    # A later read is safe only when every path from the block's end to that
    # read rebinds the name first. Subtracting every name rebound anywhere
    # afterwards let networkx's ``if multigraph_key is not None: edge_id =
    # multigraph_key`` hide the read of ``edge_id`` that follows it.
    orphaned: Set[str] = set()
    for statement, definite in zip(remaining_code, definitely_bound_before_each(remaining_code)):
        if definite is None:
            break  # no path reaches this statement
        used = used_names([statement])
        orphaned |= (bound_in_extracted & used) - definite
    return orphaned
