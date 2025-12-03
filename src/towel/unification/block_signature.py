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

"""Conservative block signature utilities used for cheap pre-filtering.

This rollback version intentionally omits aggressive heuristics and telemetry.
Only very low-risk structural gates are applied:
  1. Same statement count
  2. try/with presence alignment
  3. Matching first and last statement types

Anything passing these gates is considered for full unification. All other
behavior (ratio/histogram/simhash) has been removed to restore baseline
proposal counts and prevent false negatives introduced by over‑filtering.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import List, Tuple, Dict, Any


IDENT_COUNT_TOLERANCE = 2


@dataclass(frozen=True)
class BlockSignature:
    """Structural fingerprint of a code block for fast similarity pre-filtering.

    Contains conservative metrics (statement count, control flow presence, name/call counts)
    used to quickly reject obviously dissimilar blocks before expensive unification.
    """

    stmt_count: int
    stmt_seq: Tuple[str, ...]
    has_with: bool
    has_try: bool
    name_load_count: int
    name_store_count: int
    call_count: int


def extract_block_signature(block: List[ast.AST]) -> BlockSignature:
    stmt_seq = tuple(type(s).__name__ for s in block)
    has_with = False
    has_try = False
    name_load_count = 0
    name_store_count = 0
    call_count = 0

    skip_types = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)

    for stmt in block:
        stack = [stmt]
        while stack:
            node = stack.pop()

            if isinstance(node, skip_types):
                continue

            if not has_with and isinstance(node, ast.With):
                has_with = True
            if not has_try and isinstance(node, ast.Try):
                has_try = True
            if isinstance(node, ast.Name):
                if isinstance(node.ctx, ast.Load):
                    name_load_count += 1
                elif isinstance(node.ctx, ast.Store):
                    name_store_count += 1
            elif isinstance(node, ast.Call):
                call_count += 1

            stack.extend(ast.iter_child_nodes(node))

    return BlockSignature(
        stmt_count=len(block),
        stmt_seq=stmt_seq,
        has_with=has_with,
        has_try=has_try,
        name_load_count=name_load_count,
        name_store_count=name_store_count,
        call_count=call_count,
    )


def quick_filter(sig1: BlockSignature, sig2: BlockSignature) -> bool:
    """Return True if the pair should be considered for unification.

    Extremely simple structural checks matching the original conservative
    behavior prior to aggressive experimentation.
    """
    if sig1.stmt_count != sig2.stmt_count:
        return False
    if sig1.has_with != sig2.has_with or sig1.has_try != sig2.has_try:
        return False
    if abs(sig1.name_load_count - sig2.name_load_count) > IDENT_COUNT_TOLERANCE:
        return False
    if abs(sig1.name_store_count - sig2.name_store_count) > IDENT_COUNT_TOLERANCE:
        return False
    if abs(sig1.call_count - sig2.call_count) > IDENT_COUNT_TOLERANCE:
        return False
    if sig1.stmt_seq and sig2.stmt_seq:
        if sig1.stmt_seq[0] != sig2.stmt_seq[0] or sig1.stmt_seq[-1] != sig2.stmt_seq[-1]:
            return False
    return True


def evaluate_signature(sig1: BlockSignature, sig2: BlockSignature) -> Tuple[bool, Dict[str, Any]]:
    """Minimal compatibility wrapper retained so refactor_engine telemetry calls
    do not break. Returns (decision, empty_info)."""
    return quick_filter(sig1, sig2), {}
