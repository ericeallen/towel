#!/usr/bin/env python3
"""
Report proposals that would be skipped under stricter net-benefit thresholds.

This script:
- Disables the net-benefit heuristic to enumerate all proposals
- Recomputes each proposal's net LOC delta using the engine's estimator
- Shows which proposals pass at threshold=3 (current default) but would be skipped
  at thresholds 2, 1, or 0.

Usage:
  python scripts/report_net_benefit_diff.py

Environment:
  Looks for test_examples/*.py and compares proposals without applying changes.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List, Tuple

from towel.unification.refactor_engine import UnificationRefactorEngine, RefactoringProposal

ROOT = Path(__file__).parent.parent
TEST_EXAMPLES = ROOT / "test_examples"

# Thresholds to compare against current default (3)
THRESHOLDS = [2, 1, 0]


def estimate_net_delta(
    engine: UnificationRefactorEngine, proposal: RefactoringProposal
) -> Tuple[int, int, int]:
    """Return (added_lines, removed_effective, net_delta) for the proposal."""
    added_lines = engine._estimate_extracted_function_loc(proposal.extracted_function)
    # Approximate one extra spacer line
    added_lines += 1
    removed_effective = 0
    for item in proposal.replacements:
        if len(item) == 4:
            (start_line, end_line), _node, _file_path, _cls = item
        elif len(item) == 3:
            (start_line, end_line), _node, _file_path = item
        else:
            (start_line, end_line), _node = item
        length = max(0, end_line - start_line + 1)
        removed_effective += max(0, length - 1)  # collapse to single call line
    net_delta = added_lines - removed_effective
    return added_lines, removed_effective, net_delta


def main() -> int:
    # Ensure heuristic is disabled to enumerate all proposals
    os.environ["DISABLE_NET_BENEFIT_HEURISTIC"] = "1"

    engine = UnificationRefactorEngine(min_lines=3)

    if not TEST_EXAMPLES.exists():
        print(f"Test examples not found at {TEST_EXAMPLES}", file=sys.stderr)
        return 2

    rows: List[Tuple[str, str, int, int, int, List[int]]] = []
    # (file, description, added_lines, removed_eff, net_delta, thresholds_skipped)

    py_files = sorted(p for p in TEST_EXAMPLES.glob("*.py") if p.is_file())

    for path in py_files:
        try:
            proposals = engine.analyze_file(str(path))
        except Exception as e:
            print(f"[WARN] analyze failed for {path.name}: {e}", file=sys.stderr)
            continue

        for p in proposals:
            added, removed_eff, net = estimate_net_delta(engine, p)
            # Current default keeps if net <= 3
            if net <= 3:
                skipped = [t for t in THRESHOLDS if net > t]
                if skipped:
                    rows.append((path.name, p.description, added, removed_eff, net, skipped))

    # Print report
    if not rows:
        print("No proposals would be skipped under stricter thresholds (0-2).")
        return 0

    print("Proposals kept at threshold=3 but skipped at stricter thresholds:")
    for file_name, desc, added, removed_eff, net, skipped in rows:
        print(
            f"- {file_name}: net_delta={net} (added={added}, removed_eff={removed_eff}) "
            f"| would skip at thresholds={skipped} | {desc}"
        )

    # Summary by file
    from collections import defaultdict
    from typing import DefaultDict

    by_file: DefaultDict[str, int] = defaultdict(int)
    for file_name, *_ in rows:
        by_file[file_name] += 1

    print("\nSummary:")
    total = 0
    for fname in sorted(by_file):
        print(f"  {fname}: {by_file[fname]} proposal(s)")
        total += by_file[fname]
    print(f"  Total: {total} proposal(s)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
