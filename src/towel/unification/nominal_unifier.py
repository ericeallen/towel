#!/usr/bin/env python3
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
Nominal Unification for Python Code Blocks

This module implements nominal unification to handle α-equivalence: code blocks
that differ only in the names of bound variables.

Based on the Nominal Unification algorithm:
M. J. Gabbay. Urban, C., Pitts, A. M., & Gabbay, M. J. (2003).
    Nominal unification. In *Computer Science Logic* (pp. 513-527).
    Springer, Berlin, Heidelberg.
Calvès, C., & Fernández, M. (2008). A polynomial nominal unification algorithm.
    *Theoretical Computer Science*, *403*(2-3), 285-306.
Urban, C. (2010). Nominal Unification Revisited.
    Electronic Proceedings in Theoretical Computer Science, 42, 1–11.

Example of α-equivalent blocks:
    Block 1: user = get_user(); validate(user)
    Block 2: admin = get_admin(); validate(admin)

These blocks are structurally identical - 'user' and 'admin' play the same role.
Nominal unification tracks this correspondence so that extracted function calls
use the correct variable names:
    - Call in block 1: validate_func(user)
    - Call in block 2: validate_func(admin)  # NOT validate_func(user)!
"""

import ast
from typing import Dict, List, Set, Optional, Tuple, cast
from dataclasses import dataclass, field

from .binding_detector import Binding, detect_bindings


@dataclass
class VariableCorrespondence:
    """
    Tracks how variables correspond across unified code blocks.

    For α-equivalent blocks like:
        Block 0: user = ...; process(user)
        Block 1: admin = ...; process(admin)

    The correspondence tracks:
        canonical_name = 'user'  # We pick one as the canonical name
        block_to_original = {0: 'user', 1: 'admin'}  # Original names per block
        original_to_canonical = {('user', 0): 'user', ('admin', 1): 'user'}
    """

    canonical_name: str  # The canonical/template variable name
    block_to_original: Dict[int, str] = field(default_factory=dict)  # block_idx → original name
    original_to_canonical: Dict[Tuple[str, int], str] = field(
        default_factory=dict
    )  # (name, block_idx) → canonical


class NominalUnificationContext:
    """
    Context for nominal unification across multiple code blocks.

    Tracks:
    - Variable correspondences (which variables in different blocks correspond)
    - Binding sites (where each variable is bound)
    - Freshness constraints (which variables must not clash)
    """

    def __init__(self, num_blocks: int):
        self.num_blocks = num_blocks

        # Track variable correspondences across blocks
        # Maps canonical variable name → VariableCorrespondence
        self.correspondences: Dict[str, VariableCorrespondence] = {}

        # Track which variables are bound in each block
        # Maps block_idx → set of bound variable names
        self.bound_variables: List[Set[str]] = [set() for _ in range(num_blocks)]

        # Track binding sites for each variable in each block
        # Maps (block_idx, var_name) → list of AST nodes where variable is bound
        self.binding_sites: Dict[Tuple[int, str], List[ast.AST]] = {}

    def add_correspondence(self, canonical_name: str, block_idx: int, original_name: str) -> None:
        """
        Record that 'original_name' in block 'block_idx' corresponds to 'canonical_name'.

        Args:
            canonical_name: The canonical variable name (from the template)
            block_idx: Which code block this is
            original_name: The actual variable name in this block
        """
        if canonical_name not in self.correspondences:
            self.correspondences[canonical_name] = VariableCorrespondence(
                canonical_name=canonical_name
            )

        corr = self.correspondences[canonical_name]
        corr.block_to_original[block_idx] = original_name
        corr.original_to_canonical[(original_name, block_idx)] = canonical_name

    def get_original_name(self, canonical_name: str, block_idx: int) -> Optional[str]:
        """
        Get the original variable name in a specific block for a canonical variable.

        Args:
            canonical_name: The canonical variable name
            block_idx: Which block to look up

        Returns:
            The original variable name in that block, or None if not found
        """
        if canonical_name not in self.correspondences:
            return None
        return self.correspondences[canonical_name].block_to_original.get(block_idx)

    def get_canonical_name(self, original_name: str, block_idx: int) -> Optional[str]:
        """
        Get the canonical variable name for an original variable in a specific block.

        Args:
            original_name: The variable name in the block
            block_idx: Which block this is

        Returns:
            The canonical variable name, or None if not found
        """
        for corr in self.correspondences.values():
            if corr.original_to_canonical.get((original_name, block_idx)):
                return corr.canonical_name
        return None

    def detect_bindings_in_blocks(self, blocks: List[List[ast.AST]]) -> None:
        """
        Analyze all code blocks to detect bound variables.

        Args:
            blocks: List of code blocks (each block is a list of AST nodes)
        """
        for block_idx, block in enumerate(blocks):
            # Create a module to contain the block for analysis
            module = ast.Module(body=cast(List[ast.stmt], block), type_ignores=[])

            # Detect all bindings in this block
            bindings = detect_bindings(module)

            # Record bound variables
            for binding in bindings:
                self.bound_variables[block_idx].add(binding.name)

                # Record binding site
                _record_binding_sites(self.binding_sites, block_idx, binding)

    def is_bound_in_block(self, var_name: str, block_idx: int) -> bool:
        """Check if a variable is bound in a specific block."""
        return var_name in self.bound_variables[block_idx]

    def export_to_hygienic_renames(self) -> List[Dict[str, str]]:
        """
        Export correspondences to the hygienic_renames format.

        Returns:
            List of dictionaries, one per block, mapping original → canonical names
        """
        hygienic_renames: List[Dict[str, str]] = [{} for _ in range(self.num_blocks)]

        for corr in self.correspondences.values():
            for block_idx, original_name in corr.block_to_original.items():
                if original_name != corr.canonical_name:
                    hygienic_renames[block_idx][original_name] = corr.canonical_name

        return hygienic_renames


class NominalVariableMatcher:
    """
    Matches variables across blocks to identify α-equivalent patterns.

    This is the core of nominal unification: given two code blocks with different
    variable names, determine which variables correspond to each other.
    """

    def __init__(self, context: NominalUnificationContext):
        self.context = context

    def try_match_variables(self, var1: str, var2: str, block_idx1: int, block_idx2: int) -> bool:
        """
        Try to match two variables from different blocks.

        Args:
            var1: Variable name in block 1
            var2: Variable name in block 2
            block_idx1: Index of first block
            block_idx2: Index of second block

        Returns:
            True if the variables can be matched (or are already matched), False otherwise
        """
        # Check if both variables are bound in their respective blocks
        is_bound1 = self.context.is_bound_in_block(var1, block_idx1)
        is_bound2 = self.context.is_bound_in_block(var2, block_idx2)

        # If binding status differs, they can't match
        if is_bound1 != is_bound2:
            return False

        # If neither is bound, they're free variables - must have same name
        if not is_bound1 and not is_bound2:
            return var1 == var2

        # Both are bound - check if they're already in a correspondence
        canonical1 = self.context.get_canonical_name(var1, block_idx1)
        canonical2 = self.context.get_canonical_name(var2, block_idx2)

        if canonical1 and canonical2:
            # Both already have canonical names - must be the same
            return canonical1 == canonical2

        if canonical1:
            # var1 has a canonical name, var2 doesn't - add var2 to the same correspondence
            self.context.add_correspondence(canonical1, block_idx2, var2)
            return True

        if canonical2:
            # var2 has a canonical name, var1 doesn't - add var1 to the same correspondence
            self.context.add_correspondence(canonical2, block_idx1, var1)
            return True

        # Neither has a canonical name yet - create new correspondence
        # Use var1 as the canonical name
        canonical_name = var1
        self.context.add_correspondence(canonical_name, block_idx1, var1)
        self.context.add_correspondence(canonical_name, block_idx2, var2)
        return True

    def match_name_nodes(
        self, node1: ast.Name, node2: ast.Name, block_idx1: int, block_idx2: int
    ) -> bool:
        """
        Try to match two Name nodes from different blocks.

        Args:
            node1: Name node from block 1
            node2: Name node from block 2
            block_idx1: Index of first block
            block_idx2: Index of second block

        Returns:
            True if the names can be matched, False otherwise
        """
        return self.try_match_variables(node1.id, node2.id, block_idx1, block_idx2)


def analyze_nominal_patterns(blocks: List[List[ast.AST]]) -> NominalUnificationContext:
    """
    Analyze code blocks to identify nominal (α-equivalence) patterns.

    This is a simplified version that detects bound variables but doesn't
    do full structural matching. Full structural matching will be integrated
    into the existing Unifier class.

    Args:
        blocks: List of code blocks to analyze

    Returns:
        Context containing variable correspondences
    """
    context = NominalUnificationContext(num_blocks=len(blocks))

    # Detect all bindings in all blocks
    context.detect_bindings_in_blocks(blocks)

    return context


def build_hygienic_renames_from_unification(
    blocks: List[List[ast.AST]], canonical_block: List[ast.AST], canonical_idx: int = 0
) -> List[Dict[str, str]]:
    """
    Build hygienic_renames mapping by comparing blocks to a canonical template.

    This function is designed to be called after traditional unification has
    succeeded, to populate the hygienic_renames with variable correspondences.

    Args:
        blocks: All unified code blocks
        canonical_block: The canonical/template block (typically blocks[0])
        canonical_idx: Index of the canonical block (default 0)

    Returns:
        List of dicts mapping original names → canonical names for each block
    """
    context = NominalUnificationContext(num_blocks=len(blocks))
    context.detect_bindings_in_blocks(blocks)

    # Get bound variables from canonical block
    canonical_bound = context.bound_variables[canonical_idx]

    # For each other block, find corresponding variables
    for block_idx, block in enumerate(blocks):
        if block_idx == canonical_idx:
            # Canonical block: identity mapping
            for var in canonical_bound:
                context.add_correspondence(var, block_idx, var)
            continue

        # Match bound variables between canonical and this block
        block_bound = context.bound_variables[block_idx]

        # Simple heuristic: match variables in order of first occurrence
        # This works for our use case where blocks have parallel structure
        canonical_vars_ordered = _get_variables_in_order(canonical_block)
        block_vars_ordered = _get_variables_in_order(block)

        # Filter to bound variables only
        canonical_bound_ordered = [v for v in canonical_vars_ordered if v in canonical_bound]
        block_bound_ordered = [v for v in block_vars_ordered if v in block_bound]

        # Match in order
        for canonical_var, block_var in zip(canonical_bound_ordered, block_bound_ordered):
            context.add_correspondence(canonical_var, canonical_idx, canonical_var)
            context.add_correspondence(canonical_var, block_idx, block_var)

    return context.export_to_hygienic_renames()


def _get_variables_in_order(block: List[ast.AST]) -> List[str]:
    """
    Get all variable names from a block in order of first occurrence.

    Args:
        block: List of AST statements

    Returns:
        List of variable names in order of first use
    """
    seen = set()
    ordered = []

    class VariableCollector(ast.NodeVisitor):
        def visit_Name(self, node: ast.Name) -> None:
            if node.id not in seen:
                seen.add(node.id)
                ordered.append(node.id)
            self.generic_visit(node)

    collector = VariableCollector()
    return _gather_variables_in_order(block, collector, ordered)


def _record_binding_sites(
    table: Dict[Tuple[int, str], List[ast.AST]], block_idx: int, binding: Binding
) -> None:
    key = (block_idx, binding.name)
    table.setdefault(key, []).append(binding.node)


def _gather_variables_in_order(
    block: List[ast.AST], collector: ast.NodeVisitor, ordered: List[str]
) -> List[str]:
    for stmt in block:
        collector.visit(stmt)
    return ordered
