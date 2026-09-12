#!/usr/bin/env python3
"""
Trace proposals applied during fixed-point refactoring for a given file.

Usage:
  python scripts/trace_proposals.py test_examples/hygienic_naming.py
  python scripts/trace_proposals.py test_examples/real_world_patterns.py

Environment flags (optional):
  DEBUG_SIGNATURE_FILTER=1            # print signature filter stats
  SIGNATURE_FILTER_VALIDATE=1        # validate filtered pairs via unify
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import List

from towel.unification.refactor_engine import UnificationRefactorEngine, RefactoringProposal


def _format_proposal(p: RefactoringProposal) -> str:
    parts: List[str] = []
    parts.append(p.description)
    parts.append(f"extracted_function={p.extracted_function}")
    parts.append(f"parameters_count={p.parameters_count}")
    if p.return_variables:
        parts.append(f"returns={sorted(set(p.return_variables))}")
    if p.insert_into_class:
        parts.append(f"insert_into_class={p.insert_into_class}")
    if p.insert_into_function:
        parts.append(f"insert_into_function={p.insert_into_function}")
    # Summarize replacement ranges
    ranges = []
    for item in p.replacements:
        if len(item) == 4:
            (start, end), _node, fpath, _cls = item
        elif len(item) == 3:
            (start, end), _node, fpath = item
        else:
            (start, end), _node = item
            fpath = p.file_path
        ranges.append((Path(fpath).name, start, end))
    parts.append(f"replacements={ranges}")
    return "; ".join(parts)


def trace_file(file_path: str, *, max_iterations: int = 10, min_lines: int | None = None) -> None:
    engine_kwargs = {}
    if min_lines is not None:
        engine_kwargs["min_lines"] = min_lines
    engine = UnificationRefactorEngine(**engine_kwargs)

    src = Path(file_path)
    if not src.exists():
        print(f"File not found: {file_path}", file=sys.stderr)
        sys.exit(2)

    with tempfile.NamedTemporaryFile(mode="w+", suffix=src.suffix, delete=False) as tmp:
        tmp.write(src.read_text())
        tmp.flush()
        tmp_path = Path(tmp.name)

    try:
        current_code = tmp_path.read_text()
        applied: List[str] = []

        for iteration in range(max_iterations):
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(current_code)

            proposals = engine.analyze_file(str(tmp_path))
            if not proposals:
                break
            # Take the first proposal, as in refactor_to_fixed_point
            proposal = proposals[0]
            applied.append(_format_proposal(proposal))
            current_code = engine.apply_refactoring(str(tmp_path), proposal)

        print(f"File: {src}")
        print(f"Applied {len(applied)} proposal(s):")
        for i, desc in enumerate(applied, 1):
            print(f"  [{i}] {desc}")

    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__.strip())
        sys.exit(1)
    for arg in sys.argv[1:]:
        trace_file(arg)
