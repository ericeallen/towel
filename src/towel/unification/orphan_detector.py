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
from typing import List, Sequence, Set, Tuple, Union, cast

from .definite_assignment import definitely_bound_before_each
from .visitors import OwnScopeVisitor, visit_each


def _apply_visitor_to_nodes(
    result_set: Set[str], visitor: ast.NodeVisitor, nodes: Sequence[ast.AST]
) -> Set[str]:
    """
    Apply an AST visitor to a sequence of nodes and return the collected results.

    This helper function encapsulates the common pattern of visiting multiple AST nodes
    with a NodeVisitor and collecting results in a set.

    Args:
        result_set: The set where the visitor collects its results
        visitor: The NodeVisitor instance to apply to each node
        nodes: The AST nodes to visit

    Returns:
        The result_set after all nodes have been visited

    Note:
        This function was identified as a refactoring opportunity by Towel itself
        during dog-fooding testing (October 2025). The common visitor pattern in
        bound_names_in_block() and get_used_variables() was successfully extracted,
        validated with 100% test passage, and incorporated into the codebase.
    """
    visit_each(visitor, nodes)
    return result_set


class _BindingCollector(OwnScopeVisitor):
    def __init__(self) -> None:
        self.bindings: Set[str] = set()
        self.in_comprehension: bool = False

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._collect_names(target)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.target:
            self._collect_names(node.target)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._collect_names(node.target)
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self._collect_names(node.target)
        self.generic_visit(node)

    def _nested_function(self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef]) -> None:
        self.bindings.add(node.name)

    def _nested_class(self, node: ast.ClassDef) -> None:
        """A class binds its name here; what its body binds is the class's."""
        self.bindings.add(node.name)

    def _comprehension(
        self, node: Union[ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp]
    ) -> None:
        """Comprehension variables are local to it and are not bindings of this scope."""

    def _collect_names(self, node: ast.AST) -> None:
        """Collect all name nodes from a target."""
        if isinstance(node, ast.Name):
            self.bindings.add(node.id)
        elif isinstance(node, (ast.Tuple, ast.List)):
            for elt in node.elts:
                self._collect_names(elt)
        elif isinstance(node, ast.Starred):
            self._collect_names(node.value)
        # Ignore subscripts and attributes (they don't create bindings)


def bound_names_in_block(nodes: Sequence[ast.AST]) -> Set[str]:
    """
    Get all variables bound (assigned) in a block of code.

    This includes:
    - Assignment targets (x = ...)
    - For loop targets (for x in ...)
    - Function/class definitions
    - But NOT comprehension variables (they're local to the comprehension)
    """

    collector = _BindingCollector()
    return _apply_visitor_to_nodes(collector.bindings, collector, nodes)


class _UsageCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.uses: Set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.uses.add(node.id)
        self.generic_visit(node)


def get_used_variables(nodes: List[ast.AST]) -> Set[str]:
    """
    Get all variables used (referenced) in a block of code.
    """

    collector = _UsageCollector()
    return _apply_visitor_to_nodes(collector.uses, collector, nodes)


def orphaned_variables(
    function_body: Sequence[ast.AST], extracted_block_range: Tuple[int, int]
) -> Set[str]:
    """The names later code would read that extracting the block leaves unbound.

    Args:
        function_body: All statements in the function
        extracted_block_range: (start_index, end_index) of block to extract
            These are 0-based indices into function_body

    Returns:
        The orphaned names; empty when the extraction leaves every read bound.
    """
    start_idx, end_idx = extracted_block_range

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
    for statement, definite in zip(
        remaining_code, definitely_bound_before_each(cast(List[ast.stmt], remaining_code))
    ):
        if definite is None:
            break  # no path reaches this statement
        used = get_used_variables([statement])
        orphaned |= (bound_in_extracted & used) - definite
    return orphaned
