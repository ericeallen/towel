#!/usr/bin/env python3
"""
Quick timing harness to compare incremental vs naive refactoring loops.

- Incremental: engine.refactor_directory_to_fixed_point (proposal queue; re-analyze only when queue is empty)
- Naive: Re-run full analysis after every applied proposal

Usage:
  python scripts/bench_refactor.py --dir test_examples --max-iters 50

Outputs wall-clock timings, iterations applied, and result paths.
"""
from __future__ import annotations

import argparse
import shutil
import tempfile
import time
from pathlib import Path
from typing import Tuple

from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.refactor_engine import filter_overlapping_proposals


def copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.rglob("*"):
        rel = item.relative_to(src)
        out = dst / rel
        if item.is_dir():
            out.mkdir(parents=True, exist_ok=True)
        elif item.is_file():
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, out)


def run_incremental(
    engine: UnificationRefactorEngine, input_dir: Path, max_iters: int
) -> Tuple[float, int, Path]:
    tmp_root = Path(tempfile.mkdtemp(prefix="towel_bench_inc_"))
    out_dir = tmp_root / "out"
    # Let engine copy files from input -> out
    t0 = time.perf_counter()
    results, termination_reason = engine.refactor_directory_to_fixed_point(
        str(input_dir), str(out_dir), max_iterations=max_iters
    )
    dt = time.perf_counter() - t0
    total_applied = sum(v[0] for v in results.values())
    return dt, total_applied, out_dir


def run_naive(
    engine: UnificationRefactorEngine, input_dir: Path, max_iters: int
) -> Tuple[float, int, Path]:
    tmp_root = Path(tempfile.mkdtemp(prefix="towel_bench_naive_"))
    work_dir = tmp_root / "work"
    copy_tree(input_dir, work_dir)

    applied = 0
    t0 = time.perf_counter()
    for _ in range(max_iters):
        props = engine.analyze_directory(str(work_dir), recursive=True, verbose=False)
        if not props:
            break
        # Optionally de-overlap before choosing one, to mimic selection stability
        props = filter_overlapping_proposals(props)
        if not props:
            break
        proposal = props[0]
        result = engine.apply_refactoring_multi_file(proposal)
        if isinstance(result, tuple):
            modified_files, _changed_paths = result
        else:
            modified_files = result
        # Write back all modified files
        for f, content in modified_files.items():
            Path(f).write_text(content, encoding="utf-8")
        applied += 1
    dt = time.perf_counter() - t0
    return dt, applied, work_dir


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="test_examples", help="Input directory to refactor")
    ap.add_argument("--max-iters", type=int, default=50, help="Maximum iterations")
    args = ap.parse_args()

    input_dir = Path(args.dir).resolve()
    if not input_dir.exists():
        raise SystemExit(f"Input directory not found: {input_dir}")

    engine = UnificationRefactorEngine()

    print(f"Benchmarking on {input_dir} (max iters={args.max_iters})\n")

    inc_dt, inc_applied, inc_out = run_incremental(engine, input_dir, args.max_iters)
    print("Incremental:")
    print(f"  time: {inc_dt:.3f}s  applied: {inc_applied}  out: {inc_out}")

    naive_dt, naive_applied, naive_out = run_naive(engine, input_dir, args.max_iters)
    print("Naive (reprocess-after-each):")
    print(f"  time: {naive_dt:.3f}s  applied: {naive_applied}  out: {naive_out}")

    # Short summary
    speedup = (naive_dt / inc_dt) if inc_dt > 0 else float("inf")
    print("\nSummary:")
    print(f"  speedup (naive/inc): {speedup:.2f}x")
    if inc_applied != naive_applied:
        print(f"  NOTE: applied counts differ (inc={inc_applied}, naive={naive_applied})")


if __name__ == "__main__":
    main()
