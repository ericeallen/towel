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

from typing import Dict, List, Set, Tuple, NamedTuple

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


def _proposal_size(proposal: RefactoringProposal) -> int:
    """Total lines a proposal covers, counting a reused definition as covered."""
    return len(get_affected_lines(proposal))


def _first_span(proposal: RefactoringProposal) -> Tuple[str, int]:
    """Where a proposal starts, for deterministic ordering among equals."""
    if not proposal.replacements:
        return (proposal.file_path, 0)
    return (proposal.file_path, min(r.line_range[0] for r in proposal.replacements))


class _Interval(NamedTuple):
    """One single-file proposal as an interval for weighted interval scheduling."""

    start: int
    end: int
    weight: int
    position: int
    tiebreak: Tuple[str, int]


def _weighted_interval_schedule(intervals: List[_Interval]) -> Tuple[List[int], int]:
    """The maximum-weight set of non-overlapping intervals, as proposal indices, and its weight.

    Classic weighted interval scheduling: intervals sorted by end, ``p[j]`` the
    rightmost interval ending before ``j`` starts, a table over prefixes, and a
    reconstruction; ties keep the earlier-ending set.
    """
    items = sorted(intervals, key=lambda t: (t.end, t.start, -t.weight, t.tiebreak))
    n = len(items)
    if n == 0:
        return [], 0
    ends = [it.end for it in items]
    starts = [it.start for it in items]
    p = [-1] * n
    for j in range(n):
        lo, hi, last = 0, j - 1, -1
        while lo <= hi:
            mid = (lo + hi) // 2
            if ends[mid] < starts[j]:
                last = mid
                lo = mid + 1
            else:
                hi = mid - 1
        p[j] = last
    dp = [0] * n
    take = [False] * n
    for j in range(n):
        without = dp[j - 1] if j > 0 else 0
        withj = items[j].weight + (dp[p[j]] if p[j] != -1 else 0)
        take[j] = withj > without
        dp[j] = withj if take[j] else without
    chosen: List[int] = []
    j = n - 1
    while j >= 0:
        if take[j]:
            chosen.append(items[j].position)
            j = p[j]
        else:
            j -= 1
    chosen.reverse()
    return chosen, dp[n - 1]


def filter_overlapping_proposals(proposals: List[RefactoringProposal]) -> List[RefactoringProposal]:
    """
    Filter proposals to remove overlaps, keeping the best ones.

    When multiple proposals affect overlapping lines, this function selects
    a subset of non-overlapping proposals. Larger proposals (affecting more
    lines) are preferred over smaller ones.

    Strategy:
    1. Within each file, single-file proposals are chosen optimally by
       weighted interval scheduling over their line hulls, weight = size.
    2. The remaining proposals (multi-file or displaced) are added greedily,
       largest first, when they touch no line already taken.

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
    debug_overlap = debugging(OVERLAP)

    # Affected lines, files, and per-file convex hulls, per proposal
    prop_affected: Dict[int, Set[Tuple[str, int]]] = {}
    per_file_interval: Dict[int, Dict[str, Tuple[int, int]]] = {}
    by_primary_file: Dict[str, List[int]] = {}
    for idx, proposal in enumerate(proposals):
        affected = get_affected_lines(proposal)
        prop_affected[idx] = affected
        by_file: Dict[str, List[int]] = {}
        for fp, ln in affected:
            by_file.setdefault(fp, []).append(ln)
        per_file_interval[idx] = {fp: (min(lines), max(lines)) for fp, lines in by_file.items()}
        if len(by_file) == 1:
            by_primary_file.setdefault(next(iter(by_file)), []).append(idx)

    # Stage 1: optimal selection among single-file proposals, per file
    selected_indices: Set[int] = set()
    used_lines: Set[Tuple[str, int]] = set()
    for fp, idxs in by_primary_file.items():
        intervals = [
            _Interval(
                *per_file_interval[idx][fp],
                _proposal_size(proposals[idx]),
                idx,
                _first_span(proposals[idx]),
            )
            for idx in idxs
        ]
        chosen, total_weight = _weighted_interval_schedule(intervals)
        if debug_overlap:
            OVERLAP.debug(
                "OVERLAP_OPTIMAL file=%s selected=%d total_weight=%s candidates=%d",
                fp,
                len(chosen),
                total_weight,
                len(intervals),
            )
        for idx in chosen:
            if idx not in selected_indices:
                selected_indices.add(idx)
                used_lines.update(prop_affected[idx])

    # Stage 2: greedy add for the rest (multi-file or displaced), largest first
    remaining = [i for i in range(len(proposals)) if i not in selected_indices]
    remaining.sort(key=lambda i: (-_proposal_size(proposals[i]), _first_span(proposals[i])))
    selected: List[RefactoringProposal] = [proposals[i] for i in selected_indices]
    for idx in remaining:
        affected = prop_affected[idx]
        intersection = affected & used_lines
        if not intersection:
            selected.append(proposals[idx])
            used_lines.update(affected)
        elif debug_overlap:
            by_file2: Dict[str, List[int]] = {}
            for fp, ln in intersection:
                by_file2.setdefault(fp, []).append(ln)
            parts = [
                f"{fp}:{min(lines)}-{max(lines)} ({len(lines)} lines)"
                for fp, lines in by_file2.items()
            ]
            OVERLAP.debug(
                "OVERLAP_DROP: size=%s first=%s because intersects %s",
                _proposal_size(proposals[idx]),
                _first_span(proposals[idx]),
                "; ".join(parts),
            )

    # Return selected sorted by size descending for external stability
    return sorted(selected, key=lambda p: (-_proposal_size(p), _first_span(p)))
