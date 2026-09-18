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

"""Choosing a non-overlapping set of proposals.

Two proposals conflict when they would rewrite a line in common, or when one
would rewrite the definition another's calls now depend on. Within one file
the optimal non-overlapping set by covered lines is found by weighted
interval scheduling over each proposal's convex hull; proposals that span
files are then added greedily, largest first. The order is deterministic, so
a fixed-point run reproduces itself.
"""

from __future__ import annotations

from typing import Dict, List, Set, Tuple

from ..diagnostics import OVERLAP, debugging
from .models import RefactoringProposal


def line_ranges_intersect(left: Tuple[int, int], right: Tuple[int, int]) -> bool:
    """Whether two inclusive line ranges share at least one line."""
    return left[0] <= right[1] and right[0] <= left[1]


def get_affected_lines(proposal: RefactoringProposal) -> Set[Tuple[str, int]]:
    """
    Get all (file_path, line_number) tuples affected by a proposal.

    This is used for overlap detection - two proposals overlap if they
    affect any of the same lines in the same file.

    Args:
        proposal: A refactoring proposal

    Returns:
        Set of (file_path, line_number) tuples that would be modified
    """
    affected: Set[Tuple[str, int]] = set()
    for repl in proposal.replacements:
        file_path = repl.file_path or proposal.file_path
        start_line, end_line = repl.line_range
        for line_num in range(start_line, end_line + 1):
            affected.add((file_path, line_num))
    # The reused definition is not edited, but every call now depends on it
    # staying as it is, so a proposal that would rewrite it conflicts.
    if proposal.reused_function is not None:
        start_line, end_line = proposal.reused_function.line_range
        for line_num in range(start_line, end_line + 1):
            affected.add((proposal.reused_function.file_path, line_num))

    return affected


def filter_overlapping_proposals(proposals: List[RefactoringProposal]) -> List[RefactoringProposal]:
    """
    Filter proposals to remove overlaps, keeping the best ones.

    When multiple proposals affect overlapping lines, this function selects
    a subset of non-overlapping proposals. Larger proposals (affecting more
    lines) are preferred over smaller ones.

    Strategy:
    1. Sort proposals by size (total lines affected), largest first
    2. Greedily select proposals that don't overlap with already-selected ones

    Args:
        proposals: List of refactoring proposals

    Returns:
        List of non-overlapping proposals, sorted by size (largest first)

    Example:
        If proposals affect lines [1-7], [1-6], and [2-7], only [1-7]
        would be selected as it's the largest and the others overlap with it.
    """
    if not proposals:
        return []

    def proposal_size(p: RefactoringProposal) -> int:
        """Total lines a proposal covers, counting a reused definition as covered."""
        return len(get_affected_lines(p))

    # Optional debug diagnostics: env flag
    _debug_overlap = debugging(OVERLAP)

    # Helpers for deterministic ordering and interval extraction
    def first_span(p: RefactoringProposal) -> Tuple[str, int]:
        if not p.replacements:
            return (p.file_path, 0)
        starts = [r.line_range[0] for r in p.replacements]
        return (p.file_path, min(starts))

    # Map proposals to affected lines and per-file convex-hull intervals
    prop_affected: Dict[int, Set[Tuple[str, int]]] = {}
    prop_files: Dict[int, Set[str]] = {}
    per_file_interval: Dict[int, Dict[str, Tuple[int, int]]] = {}

    for idx, p in enumerate(proposals):
        affected = get_affected_lines(p)
        prop_affected[idx] = affected
        files: Set[str] = set(fp for (fp, _ln) in affected)
        prop_files[idx] = files
        per_file_interval[idx] = {}
        # Build convex hull interval per file for MWIS approximation
        by_file: Dict[str, List[int]] = {}
        for fp, ln in affected:
            by_file.setdefault(fp, []).append(ln)
        for fp, lines in by_file.items():
            per_file_interval[idx][fp] = (min(lines), max(lines))

    # Stage 1: Optimal non-overlapping selection within single-file proposals using MWIS
    selected_indices: Set[int] = set()
    used_lines: Set[Tuple[str, int]] = set()

    # Group single-file proposals by that file
    by_primary_file: Dict[str, List[int]] = {}
    for idx, files in prop_files.items():
        if len(files) == 1:
            fp = next(iter(files))
            by_primary_file.setdefault(fp, []).append(idx)

    def run_weighted_interval_scheduling(file_path: str, indices: List[int]) -> List[int]:
        # Build items: (start, end, weight, idx)
        items: List[Tuple[int, int, int, int, Tuple[str, int]]] = []
        for idx in indices:
            start, end = per_file_interval[idx][file_path]
            weight = proposal_size(proposals[idx])
            items.append((start, end, weight, idx, first_span(proposals[idx])))

        # Sort by end then tie-breaker to stabilize
        items.sort(key=lambda t: (t[1], t[0], -t[2], t[4]))

        n = len(items)
        if n == 0:
            return []

        # Precompute p[j]: rightmost non-overlapping interval index before j
        ends = [it[1] for it in items]
        starts = [it[0] for it in items]
        p = [-1] * n
        j = 0
        for j in range(n):
            # binary search for last i with ends[i] < starts[j]
            lo, hi = 0, j - 1
            last = -1
            while lo <= hi:
                mid = (lo + hi) // 2
                if ends[mid] < starts[j]:
                    last = mid
                    lo = mid + 1
                else:
                    hi = mid - 1
            p[j] = last

        # DP arrays
        dp = [0] * n
        take = [False] * n
        for j in range(n):
            wj = items[j][2]
            without = dp[j - 1] if j > 0 else 0
            withj = wj + (dp[p[j]] if p[j] != -1 else 0)
            if withj > without:
                dp[j] = withj
                take[j] = True
            elif withj == without:
                # Tie-breaker: prefer earlier ending interval set implicitly
                dp[j] = without
                take[j] = False
            else:
                dp[j] = without
                take[j] = False

        # Reconstruct
        sel: List[int] = []
        j = n - 1
        while j >= 0:
            if take[j]:
                sel.append(items[j][3])
                j = p[j]
            else:
                j -= 1
        sel.reverse()
        if _debug_overlap:
            OVERLAP.debug(
                "OVERLAP_OPTIMAL file=%s selected=%d total_weight=%s candidates=%d",
                file_path,
                len(sel),
                dp[n - 1],
                n,
            )
        return sel

    for fp, idxs in by_primary_file.items():
        chosen = run_weighted_interval_scheduling(fp, idxs)
        for idx in chosen:
            if idx not in selected_indices:
                selected_indices.add(idx)
                used_lines.update(prop_affected[idx])

    # Stage 2: Greedy add for remaining proposals (multi-file or leftover), respecting used_lines
    def sort_key(p: RefactoringProposal) -> Tuple[int, Tuple[str, int]]:
        return (-(proposal_size(p)), first_span(p))

    remaining = [i for i in range(len(proposals)) if i not in selected_indices]
    remaining_sorted = sorted(remaining, key=lambda i: sort_key(proposals[i]))

    selected: List[RefactoringProposal] = [proposals[i] for i in selected_indices]

    for idx in remaining_sorted:
        affected = prop_affected[idx]
        intersection = affected & used_lines
        if not intersection:
            selected.append(proposals[idx])
            used_lines.update(affected)
        elif _debug_overlap:
            by_file2: Dict[str, List[int]] = {}
            for fp, ln in intersection:
                by_file2.setdefault(fp, []).append(ln)
            parts = [
                f"{fp}:{min(lines)}-{max(lines)} ({len(lines)} lines)"
                for fp, lines in by_file2.items()
            ]
            OVERLAP.debug(
                "OVERLAP_DROP: size=%s first=%s because intersects %s",
                proposal_size(proposals[idx]),
                first_span(proposals[idx]),
                "; ".join(parts),
            )

    # Return selected sorted by size descending for external stability
    return sorted(selected, key=lambda p: (-(proposal_size(p)), first_span(p)))
