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

Workers fork only while no other thread runs here. A forked child keeps only
the thread that forked it, but every lock in whatever state it was in: one
held by another thread at that instant -- tqdm's, a language server reader's,
the one inside ``sys.stderr`` that a half-written line holds -- stays held in
the child for good. A child that needs it waits for ever, as every worker
needs the stream locks when it flushes on exit, and the run waits on the
worker. So every thread Towel runs is ended for the fork and
started again after it (see ``_alone_at_fork``), and evaluation stays in this
process, saying why, while any thread that did not end is still running.
"""

from __future__ import annotations

import multiprocessing
import os
import resource
import sys
import threading
import time

from contextlib import ExitStack, contextmanager
from typing import (
    Dict,
    FrozenSet,
    Hashable,
    Iterable,
    Iterator,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
)
from .models import (
    ClassInfo,
    CodeBlockPair,
    FunctionArtifact,
    PairVerdict,
    RefactoringProposal,
    proposal_identity,
)
from concurrent.futures import ProcessPoolExecutor
from ..diagnostics import LOG
from ..pyright_session import readers_stopped
from .progress import (
    ProgressMode,
    display_threads_stopped,
    finish_inline_status,
    start_inline_status,
    update_inline_status,
    wants_bar,
)
from concurrent.futures.process import BrokenProcessPool

from .fixed_point import FixedPointDrivers
from .pair_evaluation import PairEvaluation

_worker_engine: Optional["ParallelEvaluation"] = None


_worker_functions: Optional[Sequence[FunctionArtifact]] = None


_worker_class_infos: Optional[List[ClassInfo]] = None


_worker_pairs: Optional[List[CodeBlockPair]] = None


_worker_probed: FrozenSet[int] = frozenset()
"""Pairs the parent's probe already judged; a worker whose chunk spans one leaves it alone."""


PARENT_WATCH_INTERVAL_SECONDS = 1.0


THREAD_STOP_SECONDS = 2.0
"""How long each kind of thread Towel runs gets to end before workers fork.

Each ends within milliseconds unless it is blocked, in a write to a full
pipe or mid-message from a stalled language server; past this, it is taken
to be one of those and evaluation stays in this process.
"""


_SERIAL_NOTED: Set[Tuple[str, ...]] = set()
"""The sets of threads that have already been named for keeping evaluation serial."""


@contextmanager
def _alone_at_fork() -> Iterator[Tuple[str, ...]]:
    """End every thread Towel runs for the block; the names of the threads still running.

    Those are the progress displays -- the heartbeat and tqdm's monitor,
    which tqdm starts with the first bar and never ends -- and the reader of
    each warm pyright session, which typed runs keep across fixed-point
    iterations. Each is started again when the block ends. Workers may fork
    inside the block only when the names are empty: the process is then down
    to this thread, so no lock can be held by anyone else, and nothing but
    this thread can start another before the fork.
    """
    with display_threads_stopped(THREAD_STOP_SECONDS), readers_stopped(THREAD_STOP_SECONDS):
        this = threading.current_thread()
        yield tuple(sorted({thread.name for thread in threading.enumerate() if thread is not this}))


def _note_serial_evaluation(running: Tuple[str, ...]) -> None:
    """Say once for each set of threads that they kept pair evaluation in this process."""
    if running in _SERIAL_NOTED:
        return
    _SERIAL_NOTED.add(running)
    LOG.warning(
        "Evaluating pairs in this process rather than in forked workers: a worker "
        "forked while another thread runs can wait for ever on a lock that thread "
        "held, and %s did not stop",
        ", ".join(running),
    )


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


def _evaluate_pair_chunk(bounds: Tuple[int, int]) -> List[Tuple[int, PairVerdict]]:
    """Judge ``_worker_pairs[start:end]`` in a forked worker; the verdicts, by pair index.

    The worker inherited the parent's engine, function context, pairs and
    caches copy-on-write at fork time, so nothing is pickled in; only the
    verdicts travel back, and the parent settles them in pair order, where
    each is traced and counted once. A pair the parent's probe already
    judged is not judged again, since chunks are contiguous: judging it here
    too wrote its trace a second time.
    """
    if _worker_engine is None or _worker_functions is None or _worker_class_infos is None:
        raise RuntimeError("Worker not initialized for pair processing")
    if _worker_pairs is None:
        raise RuntimeError("Worker has no pairs to evaluate")
    start, end = bounds
    # The parent's probe left identities behind at fork time; which pair
    # repeats which is settled by the parent, so the worker starts from none.
    _worker_engine._seen_proposals.clear()
    return [
        (
            index,
            _worker_engine._pair_verdict(
                _worker_pairs[index], _worker_functions, _worker_class_infos
            ),
        )
        for index in range(start, end)
        if index not in _worker_probed
    ]


class _DistinctProposals:
    """Proposals in pair order, the first of each identity kept.

    Holding every pair's proposal until the overlap filter is what took a
    file of sixty similar functions to 33 GB: nearly every pair proposed
    the same few helpers over the same sites. Indices are the pairs'
    positions; a duplicate that arrives with a lower index replaces the one
    held, so the result is the same as filtering after the fact.
    """

    def __init__(self) -> None:
        self._by_index: Dict[int, RefactoringProposal] = {}
        self._index_of: Dict[Hashable, int] = {}

    def add(self, index: int, proposal: RefactoringProposal) -> None:
        identity = proposal_identity(proposal)
        held = self._index_of.get(identity)
        if held is not None:
            if held <= index:
                return
            del self._by_index[held]
        self._index_of[identity] = index
        self._by_index[index] = proposal

    def in_order(self) -> List[RefactoringProposal]:
        return [self._by_index[index] for index in sorted(self._by_index)]


class ParallelEvaluation(FixedPointDrivers, PairEvaluation):
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
        if "fork" not in multiprocessing.get_all_start_methods():
            return 1
        if self._settings.workers is not None:
            return self._settings.workers
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
            # os.sysconf is absent on some platforms (AttributeError) and its
            # names vary (ValueError); without a reading, one worker per core.
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
        progress: ProgressMode,
    ) -> List[RefactoringProposal]:
        proposals = _DistinctProposals()

        progress_mode, tqdm_cls = self._resolve_progress_backend(progress)
        use_tqdm = tqdm_cls is not None
        # Pair evaluation shows progress whenever progress is enabled.
        if tqdm_cls is not None:
            bar = tqdm_cls(
                total=len(block_pairs), desc="unify", unit="pair", dynamic_ncols=True, leave=False
            )
            try:
                for index, pair in enumerate(block_pairs):
                    proposal = self._judge_pair(pair, all_functions, class_infos)
                    if proposal:
                        proposals.add(index, proposal)
                    bar.update()
            finally:
                bar.close()
            return proposals.in_order()

        use_inline_bar = wants_bar(progress_mode) and len(block_pairs) > 0 and not use_tqdm
        last_pct = -1
        start_inline_status("Analyzing pairs (unify):", use_inline_bar)

        for idx, pair in enumerate(block_pairs, 1):
            proposal = self._judge_pair(pair, all_functions, class_infos)
            if proposal:
                proposals.add(idx - 1, proposal)
            if use_inline_bar:
                pct = int(100 * idx / len(block_pairs))
                if pct != last_pct:
                    last_pct = pct
                    update_inline_status("Analyzing pairs (unify):", pct)

        finish_inline_status(use_inline_bar)

        return proposals.in_order()

    def _evaluate_pairs_parallel(
        self,
        block_pairs: List[CodeBlockPair],
        all_functions: Sequence[FunctionArtifact],
        class_infos: List[ClassInfo],
        *,
        progress: ProgressMode,
    ) -> List[RefactoringProposal]:
        """Evaluate pairs in forked workers; results are ordered as the serial path orders them.

        A serial probe over the first pairs measures the real per-pair cost;
        the rest is split into contiguous chunks so pairs sharing a template
        block land in one worker and hit that worker's own caches.
        """
        global _worker_engine, _worker_functions, _worker_class_infos, _worker_pairs
        global _worker_probed

        # Every pair costs something even when its analyses are cached, and
        # most pairs are rejected before unification, so the probe samples the
        # whole list rather than the pairs the unification cache has not seen.
        cold = list(range(len(block_pairs)))
        workers = min(self._parallel_workers(), max(1, len(cold) // 64))
        if workers <= 1 or len(cold) < self.PARALLEL_PAIR_THRESHOLD:
            return self._evaluate_pairs_serial(
                block_pairs, all_functions, class_infos, progress=progress
            )
        # Probe: evaluate a strided sample of the pairs here and project the
        # rest. Pairs are ordered by function, and in a fixed-point iteration
        # the early functions are the untouched ones whose analyses are
        # cached, so a prefix would understate the cost; a stride samples
        # cached and cold regions alike.
        # Every pair's verdict, by index, settled in index order at the end:
        # the probe, the workers and a serial finish each judge out of it.
        verdicts: Dict[int, PairVerdict] = {}
        stride = max(1, len(cold) // self.PARALLEL_PROBE_PAIRS)
        probe = cold[::stride][: self.PARALLEL_PROBE_PAIRS]
        started = time.monotonic()
        for index in probe:
            verdicts[index] = self._pair_verdict(block_pairs[index], all_functions, class_infos)
        per_pair = (time.monotonic() - started) / max(1, len(probe))
        evaluated = set(probe)
        cold = [index for index in cold if index not in evaluated]

        def settled() -> List[RefactoringProposal]:
            results = _DistinctProposals()
            for index in sorted(verdicts):
                proposal = self._settle(block_pairs[index], verdicts[index])
                if proposal is not None:
                    results.add(index, proposal)
            return results.in_order()

        def finish_serially(indices: Iterable[int]) -> List[RefactoringProposal]:
            # Pairs judged here may precede the probe's; settling decides.
            self._seen_proposals.clear()
            for index in indices:
                verdicts[index] = self._pair_verdict(block_pairs[index], all_functions, class_infos)
            return settled()

        if per_pair * len(cold) < self.PARALLEL_MIN_PROJECTED_SECONDS:
            return finish_serially(cold)
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
        _worker_probed = frozenset(probe)
        running: Tuple[str, ...] = ()
        try:
            with ExitStack() as pool:
                outcomes: Iterable[List[Tuple[int, PairVerdict]]]
                outcomes = ()
                with _alone_at_fork() as running:
                    if not running:
                        executor = pool.enter_context(
                            ProcessPoolExecutor(
                                max_workers=workers,
                                mp_context=multiprocessing.get_context("fork"),
                                initializer=_start_parent_watchdog,
                            )
                        )
                        # ``map`` submits every chunk now, and under the fork
                        # context the first submission forks every worker,
                        # before the pool starts threads of its own.
                        outcomes = executor.map(_evaluate_pair_chunk, chunks)
                for bounds, judged in zip(chunks, outcomes):
                    verdicts.update(judged)
                    evaluated.update(range(*bounds))
        except (BrokenProcessPool, OSError) as error:
            # A pool that cannot be started or that lost a worker; anything a
            # worker raised itself propagates, since it is a bug to fix.
            LOG.warning("Parallel pair evaluation unavailable (%s); finishing serially", error)
            return finish_serially([index for index in cold if index not in evaluated])
        finally:
            _worker_engine = _worker_functions = _worker_class_infos = _worker_pairs = None
            _worker_probed = frozenset()
        if running:
            _note_serial_evaluation(running)
            return finish_serially(cold)
        return settled()
