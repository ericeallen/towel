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
Detect orphaned variable references after code extraction.

An orphaned variable is one that is bound (assigned) in the extracted code
but referenced in code that remains after the extraction point.
"""

import ast
from typing import List, Set, Tuple


def _apply_visitor_to_nodes(
    result_set: Set[str], visitor: ast.NodeVisitor, nodes: List[ast.AST]
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
        get_bound_variables() and get_used_variables() was successfully extracted,
        validated with 100% test passage, and incorporated into the codebase.
    """
    for node in nodes:
        visitor.visit(node)
    return result_set


def get_bound_variables(nodes: List[ast.AST]) -> Set[str]:
    """
    Get all variables bound (assigned) in a block of code.

    This includes:
    - Assignment targets (x = ...)
    - For loop targets (for x in ...)
    - Function/class definitions
    - But NOT comprehension variables (they're local to the comprehension)
    """

    class BindingCollector(ast.NodeVisitor):
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

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.bindings.add(node.name)
            # Don't visit inside nested functions

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self.bindings.add(node.name)
            # Don't visit inside nested functions

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self.bindings.add(node.name)
            # Don't visit inside nested classes

        def visit_ListComp(self, node: ast.ListComp) -> None:
            # Comprehension variables are local, don't collect them
            pass

        def visit_SetComp(self, node: ast.SetComp) -> None:
            pass

        def visit_DictComp(self, node: ast.DictComp) -> None:
            pass

        def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
            pass

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

    collector = BindingCollector()
    return _apply_visitor_to_nodes(collector.bindings, collector, nodes)


def get_used_variables(nodes: List[ast.AST]) -> Set[str]:
    """
    Get all variables used (referenced) in a block of code.
    """

    class UsageCollector(ast.NodeVisitor):
        def __init__(self) -> None:
            self.uses: Set[str] = set()

        def visit_Name(self, node: ast.Name) -> None:
            if isinstance(node.ctx, ast.Load):
                self.uses.add(node.id)
            self.generic_visit(node)

    collector = UsageCollector()
    return _apply_visitor_to_nodes(collector.uses, collector, nodes)


def has_orphaned_variables(
    function_body: List[ast.AST], extracted_block_range: Tuple[int, int]
) -> Tuple[bool, Set[str]]:
    """
    Check if extracting a block would create orphaned variable references.

    Args:
        function_body: All statements in the function
        extracted_block_range: (start_index, end_index) of block to extract
            These are 0-based indices into function_body

    Returns:
        (has_orphans, orphaned_vars) where:
        - has_orphans: True if there are orphaned variables
        - orphaned_vars: Set of variable names that would be orphaned
    """
    start_idx, end_idx = extracted_block_range

    # Get the extracted block and remaining code
    extracted_block = function_body[start_idx : end_idx + 1]
    remaining_code = function_body[end_idx + 1 :]

    if not remaining_code:
        # Nothing after the extracted block, so no orphans possible
        return False, set()

    # Get variables bound in the extracted block
    bound_in_extracted = get_bound_variables(extracted_block)

    # Get variables used in the remaining code
    used_in_remaining = get_used_variables(remaining_code)

    # Get variables bound in the remaining code
    bound_in_remaining = get_bound_variables(remaining_code)

    # Orphaned variables are those that are:
    # 1. Bound in the extracted block
    # 2. Used in the remaining code
    # 3. NOT bound in the remaining code (before use)
    orphaned = bound_in_extracted & used_in_remaining - bound_in_remaining

    return len(orphaned) > 0, orphaned
