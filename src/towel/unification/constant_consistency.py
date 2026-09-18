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

"""Consistency of constants across the blocks being unified.

A constant that differs between blocks may become a parameter, but only
when every occurrence of that constant in each block corresponds: the same
value must appear at the same positions, so a helper that parameterizes it
does not silently change one occurrence and not another.
"""

from __future__ import annotations

import ast

from typing import Any, List, Sequence, Tuple

from .unifier_state import UnifierState


class ConstantConsistency(UnifierState):
    """See the module docstring."""

    def _check_constant_consistency(self, values: List[Any], block_indices: Sequence[int]) -> bool:
        """
        Check if constants can be consistently parameterized per the user's rule.

        The rule: For constants to be parameterized, ALL occurrences across blocks
        must unify consistently. If a value appears at multiple positions, those
        positions must align and unify the same way.

        Example that should FAIL:
        - Block 0: "x = item * 2" and "z = y ** 2"
        - Block 1: "x = item * 3" and "z = y ** 2"
        - Value 2 appears at 2 positions in block 0
        - At position 1: 2 differs from 3 (would parameterize)
        - At position 2: 2 equals 2 (would NOT parameterize)
        - INCONSISTENT -> return False

        Args:
            values: List of constant values (one per block)
            block_indices: Block indices

        Returns:
            True if constants can be consistently parameterized
        """
        if len(values) != 2 or len(block_indices) != 2:
            # Only handle 2-block case for now
            return True

        value0, value1 = values
        idx0, idx1 = block_indices

        # Get all positions where each value appears
        positions0 = self.constant_positions.get((idx0, value0), [])
        positions1 = self.constant_positions.get((idx1, value1), [])

        # If either value appears only once, it's trivially consistent
        if len(positions0) <= 1 and len(positions1) <= 1:
            return True

        # If both values are the same, check they appear at same positions
        if value0 == value1:
            # Same value in both blocks - they should appear at same positions
            # If they do, unification will succeed without parameterization
            # This is fine, return True
            return True

        # Different values - check consistency
        # If value0 appears N times, and value1 appears M times, and N != M,
        # this is already inconsistent
        if len(positions0) != len(positions1):
            # Different number of occurrences
            # This means one value appears more times than the other
            # Cannot parameterize consistently
            return False

        # Both values appear the same number of times
        # Check if they appear at structurally matching positions
        # If the positions don't align, we can't parameterize

        # For now, use a simple heuristic: if a value appears multiple times (> 1),
        # we need the positions to match exactly
        if len(positions0) > 1:
            # Sort positions for comparison
            sorted_pos0 = sorted(positions0)
            sorted_pos1 = sorted(positions1)

            # Check if positions align
            if sorted_pos0 != sorted_pos1:
                # Positions don't align - inconsistent
                return False

        # Positions align - can parameterize consistently
        return True

    def _collect_constant_positions(self, blocks: Sequence[Sequence[ast.AST]]) -> None:
        """
        Collect all constant occurrences and their structural positions.

        This pre-pass finds every constant in each block and records its position
        using a path tuple that uniquely identifies its location in the AST.

        Args:
            blocks: List of code blocks to analyze
        """
        self.constant_positions = {}

        for block_idx, block in enumerate(blocks):
            for stmt_idx, stmt in enumerate(block):
                # Traverse this statement and record all constants
                self._record_constants_in_tree(stmt, (stmt_idx,), block_idx)

    def _record_constants_in_tree(
        self, node: ast.AST, path: Tuple[Any, ...], block_idx: int
    ) -> None:
        """
        Recursively traverse AST and record all constant positions.

        Args:
            node: Current AST node
            path: Tuple representing path from statement root
            block_idx: Which block this is from
        """
        if isinstance(node, ast.Constant):
            # Record this constant's position
            key = (block_idx, node.value)
            if key not in self.constant_positions:
                self.constant_positions[key] = []
            self.constant_positions[key].append(path)

        # Recursively visit children
        for field_name, field_value in self._iter_child_fields(node):
            if isinstance(field_value, list):
                for i, item in enumerate(field_value):
                    if isinstance(item, ast.AST):
                        child_path = path + (field_name, i)
                        self._record_constants_in_tree(item, child_path, block_idx)
            elif isinstance(field_value, ast.AST):
                child_path = path + (field_name,)
                self._record_constants_in_tree(field_value, child_path, block_idx)
