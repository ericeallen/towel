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

"""Evaluating candidate pairs in parallel by forking.

A large cold analysis forks worker processes after parsing; each inherits
the engine, its parsed modules, and its caches copy-on-write and returns
only accepted proposals, so nothing is pickled in and only results travel
back. Forking is decided by a timed serial probe over a strided sample of
the pairs, never by pair count alone. Each worker runs a watchdog thread
that ends it within a second of its parent's death, and the worker count is
capped by the parent's resident size against physical memory.
TOWEL_WORKERS=1 keeps evaluation serial; any other value caps the workers.
"""

from __future__ import annotations

import multiprocessing
import os
import resource
import sys
import threading
import time

from typing import Dict, List, Optional, Sequence, Tuple
from .models import ClassInfo, CodeBlockPair, FunctionArtifact, RefactoringProposal
from concurrent.futures import ProcessPoolExecutor
from ..diagnostics import LOG
from concurrent.futures.process import BrokenProcessPool

from .engine_state import EngineState

_worker_engine: Optional["ParallelEvaluation"] = None


_worker_functions: Optional[Sequence[FunctionArtifact]] = None


_worker_class_infos: Optional[List[ClassInfo]] = None


_worker_pairs: Optional[List[CodeBlockPair]] = None


PARENT_WATCH_INTERVAL_SECONDS = 1.0


def _exit_when_parent_dies(parent: int, interval: float) -> None:
    """Poll the parent pid and end this worker as soon as it is reparented."""
    while os.getppid() == parent:
        time.sleep(interval)
    os._exit(1)


def _start_parent_watchdog() -> None:
    """Run in each forked worker: a killed parent must not leave workers behind.

    A worker checks nothing itself: between chunks it blocks on the pool's
    call queue, whose write end every sibling inherited, so it would wait
    there forever once the parent is gone (fourteen such orphans from a
    killed run once filled a 128 GB machine's swap). A daemon thread that
    polls the parent pid ends the worker within one interval wherever the
    main thread happens to be, mid-pair or idle.
    """
    threading.Thread(
        target=_exit_when_parent_dies,
        args=(os.getppid(), PARENT_WATCH_INTERVAL_SECONDS),
        name="towel-parent-watchdog",
        daemon=True,
    ).start()


def _evaluate_pair_chunk(bounds: Tuple[int, int]) -> List[Tuple[int, RefactoringProposal]]:
    """Evaluate ``_worker_pairs[start:end]`` in a forked worker.

    The worker inherited the parent's engine, function context, pairs and
    caches copy-on-write at fork time, so nothing is pickled in; only the
    accepted proposals travel back.
    """
    if _worker_engine is None or _worker_functions is None or _worker_class_infos is None:
        raise RuntimeError("Worker not initialized for pair processing")
    if _worker_pairs is None:
        raise RuntimeError("Worker has no pairs to evaluate")
    start, end = bounds
    accepted: List[Tuple[int, RefactoringProposal]] = []
    for index in range(start, end):
        proposal = _worker_engine._try_refactor_pair_multi_file(
            _worker_pairs[index], _worker_functions, _worker_class_infos
        )
        if proposal is not None:
            accepted.append((index, proposal))
    return accepted


class ParallelEvaluation(EngineState):
    """ParallelEvaluation methods of the engine; see the module docstring."""

    #: Fewer cold pairs than this never fork; above it, a serial prefix is
    #: timed and the rest forks only when the projected serial time exceeds
    #: PARALLEL_MIN_PROJECTED_SECONDS. Pair counts alone misjudge cost: pygments'
    #: lexer modules form tens of thousands of cheap pairs per iteration, and
    #: forking a pool for each iteration made the run three times slower,
    #: while pyflakes' test modules form a few thousand expensive pairs that
    #: a pool halves. The probe, not the count, tells the two apart.
    PARALLEL_PAIR_THRESHOLD = 2000
    PARALLEL_PROBE_PAIRS = 400
    PARALLEL_MIN_PROJECTED_SECONDS = 12.0
    #: Forked workers may keep at most this share of physical memory between
    #: them, estimated from this process's resident size: refcount updates
    #: copy the pages a worker touches, so a large analysis graph costs about
    #: one process size per worker.
    PARALLEL_MEMORY_SHARE = 0.35

    def _parallel_workers(self) -> int:
        """Worker processes to use, or 1 when pair evaluation must stay serial."""
        if self._settings.workers is not None:
            return self._settings.workers
        if "fork" not in multiprocessing.get_all_start_methods():
            return 1
        workers = max(1, os.cpu_count() or 1)
        return max(1, min(workers, self._workers_that_fit_in_memory()))

    @staticmethod
    def _workers_that_fit_in_memory() -> int:
        """How many copies of this process's resident set fit in the allowed memory share."""
        try:
            physical = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
            resident = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            if sys.platform != "darwin":
                resident *= 1024  # Linux reports kilobytes
        except (ValueError, OSError, AttributeError):
            return os.cpu_count() or 1
        if resident <= 0:
            return os.cpu_count() or 1
        return int(physical * ParallelEvaluation.PARALLEL_MEMORY_SHARE // resident)

    def _should_use_parallel(self, pair_count: int) -> bool:
        """Whether pair evaluation should fork workers.

        Workers are forked after parsing, so they inherit the ASTs, the
        function context, and every cache copy-on-write; nothing is pickled
        in, and only accepted proposals are pickled out. That is what made the
        earlier pool, which pickled ASTs per task, slower than serial. Pairs
        already memoized in this engine are served serially from the cache.
        """
        return pair_count >= self.PARALLEL_PAIR_THRESHOLD and self._parallel_workers() > 1

    def _evaluate_pairs_serial(
        self,
        block_pairs: List[CodeBlockPair],
        all_functions: Sequence[FunctionArtifact],
        class_infos: List[ClassInfo],
        *,
        verbose: bool,
        progress: str,
    ) -> List[RefactoringProposal]:
        proposals: List[RefactoringProposal] = []

        progress_mode, tqdm_cls, use_tqdm = self._resolve_progress_backend(progress)
        tqdm_iter = None
        # Always show progress for pair evaluation when progress is enabled, even if verbose=False
        if use_tqdm and tqdm_cls is not None:
            tqdm_iter = tqdm_cls(
                block_pairs,
                total=len(block_pairs),
                desc="unify",
                unit="pair",
                leave=False,
            )

        if use_tqdm and tqdm_iter is not None:
            for pair in tqdm_iter:
                proposal = self._try_refactor_pair_multi_file(pair, all_functions, class_infos)
                if proposal:
                    proposals.append(proposal)
            return proposals

        use_inline_bar = progress_mode in ("auto", "tqdm") and len(block_pairs) > 0 and not use_tqdm
        last_pct = -1
        self._start_inline_status("Analyzing pairs (unify):", use_inline_bar)

        for idx, pair in enumerate(block_pairs, 1):
            proposal = self._try_refactor_pair_multi_file(pair, all_functions, class_infos)
            if proposal:
                proposals.append(proposal)
            if use_inline_bar:
                pct = int(100 * idx / len(block_pairs))
                if pct != last_pct:
                    last_pct = pct
                    self._update_inline_status("Analyzing pairs (unify):", pct)

        self._finish_inline_status(use_inline_bar)

        return proposals

    def _evaluate_pairs_parallel(
        self,
        block_pairs: List[CodeBlockPair],
        all_functions: Sequence[FunctionArtifact],
        class_infos: List[ClassInfo],
        *,
        verbose: bool,
        progress: str,
    ) -> List[RefactoringProposal]:
        """Evaluate pairs in forked workers; results are ordered as the serial path orders them.

        A serial probe over the first pairs measures the real per-pair cost;
        the rest is split into contiguous chunks so pairs sharing a template
        block land in one worker and hit that worker's own caches.
        """
        global _worker_engine, _worker_functions, _worker_class_infos, _worker_pairs

        # Every pair costs something even when its analyses are cached, and
        # most pairs are rejected before unification, so the probe samples the
        # whole list rather than the pairs the unification cache has not seen.
        cold = list(range(len(block_pairs)))
        workers = min(self._parallel_workers(), max(1, len(cold) // 64))
        if workers <= 1 or len(cold) < self.PARALLEL_PAIR_THRESHOLD:
            return self._evaluate_pairs_serial(
                block_pairs, all_functions, class_infos, verbose=verbose, progress=progress
            )
        # Probe: evaluate a strided sample of the pairs here and project the
        # rest. Pairs are ordered by function, and in a fixed-point iteration
        # the early functions are the untouched ones whose analyses are
        # cached, so a prefix would understate the cost; a stride samples
        # cached and cold regions alike.
        results: Dict[int, RefactoringProposal] = {}
        stride = max(1, len(cold) // self.PARALLEL_PROBE_PAIRS)
        probe = cold[::stride][: self.PARALLEL_PROBE_PAIRS]
        started = time.monotonic()
        for index in probe:
            probed = self._try_refactor_pair_multi_file(
                block_pairs[index], all_functions, class_infos
            )
            if probed is not None:
                results[index] = probed
        per_pair = (time.monotonic() - started) / max(1, len(probe))
        probed_indices = set(probe)
        cold = [index for index in cold if index not in probed_indices]
        if per_pair * len(cold) < self.PARALLEL_MIN_PROJECTED_SECONDS:
            for index in cold:
                serial_proposal = self._try_refactor_pair_multi_file(
                    block_pairs[index], all_functions, class_infos
                )
                if serial_proposal is not None:
                    results[index] = serial_proposal
            return [results[index] for index in sorted(results)]
        chunk_count = workers * 4
        chunk_size = max(1, -(-len(cold) // chunk_count))
        chunks = [
            (cold[offset], cold[min(offset + chunk_size, len(cold)) - 1] + 1)
            for offset in range(0, len(cold), chunk_size)
        ]
        # Chunks are index ranges over ``block_pairs``; cold indices are
        # increasing, so a range may include warm or probed pairs, which the
        # worker then also evaluates cheaply. That keeps chunks contiguous.
        _worker_engine, _worker_functions, _worker_class_infos, _worker_pairs = (
            self,
            all_functions,
            class_infos,
            block_pairs,
        )
        try:
            context = multiprocessing.get_context("fork")
            with ProcessPoolExecutor(
                max_workers=workers, mp_context=context, initializer=_start_parent_watchdog
            ) as executor:
                for _bounds, accepted in zip(chunks, executor.map(_evaluate_pair_chunk, chunks)):
                    for index, proposal in accepted:
                        results[index] = proposal
        except (BrokenProcessPool, OSError, RuntimeError) as error:
            LOG.warning("Parallel pair evaluation unavailable (%s); evaluating serially", error)
            return self._evaluate_pairs_serial(
                block_pairs, all_functions, class_infos, verbose=verbose, progress=progress
            )
        finally:
            _worker_engine = _worker_functions = _worker_class_infos = _worker_pairs = None
        return [results[index] for index in sorted(results)]
