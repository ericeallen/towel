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

"""Differential fuzzing over many generated cases: ``just fuzz [N] [SEED]``.

Usage::

    python -m tests.differential.fuzz [--count N] [--seed START] [--modes default,cross]
        [--typed-every K] [--jobs J] [--out DIR]

Seeds ``START`` to ``START + N - 1`` are generated (:mod:`tests.differential.grammar`)
and each case is refactored and compared (:mod:`tests.differential.runner`):
in the default mode, and, unless it is a single file, with ``--cross-module``
too. Every ``K``-th seed is also generated typed and run in the typed mode,
with the checker its project configures. With no ``--seed`` the start is
drawn at random and printed, so any run can be repeated exactly.

Each failure is written under ``DIR`` as a hostile fixture ready to commit,
named with ``--prefix``, with a note saying where it goes and what the
batteries must be told (:mod:`tests.differential.export`). Progress is printed as the run goes. The
exit status is 1 when any case failed, so the run cannot pass silently.
"""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import Future, ProcessPoolExecutor, as_completed
from dataclasses import dataclass
import multiprocessing
import os
from pathlib import Path
import random
import sys
import tempfile
import time
from typing import Dict, List, Optional, Sequence, Tuple

from tests.differential.export import write_fixture
from tests.differential.grammar import generate_case
from tests.differential.runner import CROSS_MODULE, DEFAULT, Mode, Outcome, run_case

MODES: Dict[str, Mode] = {"default": DEFAULT, "cross": CROSS_MODULE}
TYPED = Mode(types=True)


@dataclass(frozen=True)
class Job:
    """One case in one mode."""

    seed: int
    typed: bool
    mode: Mode


def jobs_for(seeds: Sequence[int], modes: Sequence[Mode], typed_every: int) -> List[Job]:
    """Every case the run covers: each seed in each mode, and every ``typed_every``-th seed typed."""
    jobs: List[Job] = []
    for seed in seeds:
        layout = generate_case(seed).layout
        jobs += [
            Job(seed, False, mode)
            for mode in modes
            if not (mode.cross_module and layout == "single")
        ]
        if typed_every > 0 and seed % typed_every == 0:
            jobs.append(Job(seed, True, TYPED))
    return jobs


def run_job(job: Job, call_seconds: float) -> Outcome:
    """Run ``job`` in a fresh temporary directory; the unit of work a worker process does."""
    with tempfile.TemporaryDirectory(prefix="towel-fuzz-") as directory:
        return run_case(
            generate_case(job.seed, typed=job.typed),
            job.mode,
            Path(directory),
            call_seconds=call_seconds,
        )


def _seed(text: str) -> Optional[int]:
    return None if text == "" else int(text)


def _modes(text: str) -> List[Mode]:
    names = text.split(",")
    unknown = [name for name in names if name not in MODES]
    if unknown:
        raise argparse.ArgumentTypeError(f"unknown mode(s) {unknown}; choose from {list(MODES)}")
    return [MODES[name] for name in names]


def _arguments(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m tests.differential.fuzz", description=__doc__)
    parser.add_argument("--count", type=int, default=1000, help="seeds to draw (default 1000)")
    parser.add_argument(
        "--seed", type=_seed, default=None, help="the first seed; drawn at random when omitted"
    )
    parser.add_argument(
        "--modes",
        type=_modes,
        default="default,cross",
        help="comma-separated: default, cross (default both)",
    )
    parser.add_argument(
        "--typed-every",
        type=int,
        default=20,
        help="also run every K-th seed typed, with its checker (default 20; 0 for none)",
    )
    parser.add_argument(
        "--typed", action="store_true", help="run the seeds typed only (to reproduce a typed case)"
    )
    parser.add_argument(
        "--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2), help="worker processes"
    )
    parser.add_argument("--out", type=Path, default=None, help="where failures are written")
    parser.add_argument(
        "--prefix",
        default="fz",
        help="fixtures are named r<PREFIX>_... and xf<PREFIX>_...; use your branch's (default fz)",
    )
    parser.add_argument(
        "--call-seconds", type=float, default=2.0, help="the limit on one probe call (default 2)"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    arguments = _arguments(argv)
    start = arguments.seed if arguments.seed is not None else random.SystemRandom().randrange(10**6)
    seeds = range(start, start + arguments.count)
    out: Path = (
        arguments.out or Path(tempfile.gettempdir()) / f"towel-fuzz-{start}-{arguments.count}"
    )
    if arguments.typed:
        jobs = [Job(seed, True, TYPED) for seed in seeds]
    else:
        jobs = jobs_for(seeds, arguments.modes, arguments.typed_every)
    print(
        f"Fuzzing seeds {start}..{start + arguments.count - 1}: {len(jobs)} runs"
        f" on {arguments.jobs} worker(s); failures go to {out}",
        flush=True,
    )
    counts: Counter[str] = Counter()
    failures: List[Tuple[Outcome, Path]] = []
    started = time.monotonic()
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(arguments.jobs, mp_context=context) as pool:
        pending: Dict[Future[Outcome], Job] = {
            pool.submit(run_job, job, arguments.call_seconds): job for job in jobs
        }
        for done, future in enumerate(as_completed(pending), 1):
            job = pending[future]
            try:
                outcome = future.result()
            except Exception as error:  # noqa: BLE001 - a harness fault ends the run loudly
                print(f"harness error on seed {job.seed} ({job.mode.label}): {error!r}", flush=True)
                pool.shutdown(cancel_futures=True)
                return 2
            counts[outcome.status] += 1
            if outcome.is_defect:
                fixture = write_fixture(outcome, out, prefix=arguments.prefix)
                failures.append((outcome, fixture.path))
                print(
                    f"FAIL {outcome.case.name} {outcome.mode.label or 'typed'}: {outcome.status};"
                    f" fixture {fixture.path}{'' if fixture.reproduced else ' (not reproduced)'}",
                    flush=True,
                )
            if done % 100 == 0 or done == len(jobs):
                rate = done / max(time.monotonic() - started, 1e-9)
                summary = ", ".join(f"{status} {n}" for status, n in sorted(counts.items()))
                print(f"[{done}/{len(jobs)}] {rate:.1f} runs/s: {summary}", flush=True)
    print(f"Done in {time.monotonic() - started:.0f}s: {len(failures)} failure(s).", flush=True)
    for outcome, path in failures:
        print(f"  {outcome.case.name} ({outcome.mode.label or 'typed'}): {path}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
