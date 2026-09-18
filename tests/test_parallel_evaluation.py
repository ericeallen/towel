"""The forked pair-evaluation pool produces what the serial path produces.

Forking is normally reserved for thousands of pairs whose serial probe
projects more than several seconds, so the suite never reached it. These
tests lower those thresholds so a small module of near-identical functions
(enough pairs for the pool's ``len // 64`` worker cap to allow two workers)
takes the pool path, and assert that the proposals, their descriptions and
their rendered results are the serial path's. Two fault injections then
break the pool, once with a chunk raising ``OSError`` and once with a worker
dying mid-chunk, and assert the documented fallback: a warning on the
``towel`` logger and the serial result.
"""

from __future__ import annotations

import contextlib
import io
import logging
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Callable, List, Tuple

import pytest

from towel.diagnostics import Settings
from towel.unification import parallel
from towel.unification.models import RefactoringProposal
from towel.unification.parallel import ParallelEvaluation
from towel.unification.refactor_engine import UnificationRefactorEngine

pytestmark = pytest.mark.skipif(
    "fork" not in multiprocessing.get_all_start_methods(), reason="needs the fork start method"
)

# Two block shapes, so the run yields two clustered proposals rather than one.
SHAPES = (
    "def {name}(x):\n    a = x + {index}\n    b = a * 2\n    c = b - 3\n    return c\n",
    "def {name}(items):\n    total = 0\n    for item in items:\n        total += item * {index}\n    return total\n",
)


def _module(functions_per_shape: int) -> str:
    return "\n\n".join(
        shape.format(name=f"f{shape_index}_{index}", index=index)
        for shape_index, shape in enumerate(SHAPES)
        for index in range(functions_per_shape)
    )


def _settings(workers: int) -> Settings:
    return Settings(
        workers=workers,
        check_ast_immutable=False,
        debug_rejections=False,
        debug_validation=False,
        debug_overlap=False,
        debug_types=False,
    )


def _lower_thresholds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ParallelEvaluation, "PARALLEL_PAIR_THRESHOLD", 2)
    monkeypatch.setattr(ParallelEvaluation, "PARALLEL_PROBE_PAIRS", 1)
    monkeypatch.setattr(ParallelEvaluation, "PARALLEL_MIN_PROJECTED_SECONDS", 0.0)


def _analyze(
    path: Path, workers: int
) -> Tuple[UnificationRefactorEngine, List[RefactoringProposal]]:
    engine = UnificationRefactorEngine(min_lines=3, settings=_settings(workers))
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        proposals = engine.analyze_files([str(path)], progress="none")
    return engine, proposals


def _rendered(
    engine: UnificationRefactorEngine, path: Path, proposals: List[RefactoringProposal]
) -> List[str]:
    return [engine.apply_refactoring(str(path), proposal) for proposal in proposals]


def _pair_counts(monkeypatch: pytest.MonkeyPatch) -> List[int]:
    """Record how many pairs each ``process_block_pairs`` call is given."""
    counts: List[int] = []
    original = UnificationRefactorEngine.process_block_pairs

    def counting(
        self: UnificationRefactorEngine, block_pairs: List[Any], *args: Any, **kwargs: Any
    ) -> Any:
        counts.append(len(block_pairs))
        return original(self, block_pairs, *args, **kwargs)

    monkeypatch.setattr(UnificationRefactorEngine, "process_block_pairs", counting)
    return counts


def _recording_executor(monkeypatch: pytest.MonkeyPatch) -> List[dict[str, Any]]:
    """Replace the module's ``ProcessPoolExecutor`` with one that records its construction."""
    constructions: List[dict[str, Any]] = []

    class Recording(ProcessPoolExecutor):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            constructions.append(dict(kwargs))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(parallel, "ProcessPoolExecutor", Recording)
    return constructions


def test_forked_pool_produces_the_serial_proposals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "m.py"
    path.write_text(_module(8))
    serial_engine, serial = _analyze(path, workers=1)
    serial_rendered = _rendered(serial_engine, path, serial)
    assert len(serial) >= 2, [p.description for p in serial]

    _lower_thresholds(monkeypatch)
    counts = _pair_counts(monkeypatch)
    constructions = _recording_executor(monkeypatch)
    parallel_engine, forked = _analyze(path, workers=2)

    assert counts and counts[0] >= 128, counts
    assert [c["max_workers"] for c in constructions] == [2]
    assert constructions[0]["initializer"] is parallel._start_parent_watchdog
    assert [p.description for p in forked] == [p.description for p in serial]
    assert _rendered(parallel_engine, path, forked) == serial_rendered
    # The worker globals never outlive the pool.
    assert parallel._worker_engine is None and parallel._worker_pairs is None


def test_probe_that_projects_a_cheap_run_stays_serial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "m.py"
    path.write_text(_module(8))
    _, serial = _analyze(path, workers=1)
    monkeypatch.setattr(ParallelEvaluation, "PARALLEL_PAIR_THRESHOLD", 2)
    monkeypatch.setattr(ParallelEvaluation, "PARALLEL_PROBE_PAIRS", 4)
    # The projected serial time is finite, so a huge minimum keeps it serial.
    monkeypatch.setattr(ParallelEvaluation, "PARALLEL_MIN_PROJECTED_SECONDS", 1e9)
    constructions = _recording_executor(monkeypatch)
    _, probed = _analyze(path, workers=2)
    assert constructions == []
    assert [p.description for p in probed] == [p.description for p in serial]


def test_too_few_pairs_for_two_workers_stays_serial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Above the pair threshold but under 128 pairs, the pool would have one worker: serial."""
    path = tmp_path / "m.py"
    path.write_text(_module(3))
    _, serial = _analyze(path, workers=1)
    _lower_thresholds(monkeypatch)
    counts = _pair_counts(monkeypatch)
    constructions = _recording_executor(monkeypatch)
    _, evaluated = _analyze(path, workers=2)
    assert counts and 2 <= counts[0] < 128, counts
    assert constructions == []
    assert [p.description for p in evaluated] == [p.description for p in serial]


def test_default_worker_count_is_bounded_by_cores_and_memory() -> None:
    engine = UnificationRefactorEngine(min_lines=3, settings=_settings(1))
    engine._settings = Settings(**{**engine._settings.__dict__, "workers": None})
    workers = engine._parallel_workers()
    assert 1 <= workers <= max(1, os.cpu_count() or 1)
    assert ParallelEvaluation._workers_that_fit_in_memory() >= 0
    assert engine._should_use_parallel(0) is False
    assert engine._should_use_parallel(ParallelEvaluation.PARALLEL_PAIR_THRESHOLD) is (workers > 1)


def _raise_os_error(bounds: Tuple[int, int]) -> List[Tuple[int, RefactoringProposal]]:
    raise OSError("injected worker failure")


def _die(bounds: Tuple[int, int]) -> List[Tuple[int, RefactoringProposal]]:
    os._exit(3)


@pytest.mark.parametrize(
    "chunk_evaluator, failure",
    [(_raise_os_error, "injected worker failure"), (_die, "terminated abruptly")],
    ids=["chunk-raises-OSError", "worker-dies-BrokenProcessPool"],
)
def test_broken_pool_falls_back_to_the_serial_result_with_a_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    chunk_evaluator: Callable[[Tuple[int, int]], List[Tuple[int, RefactoringProposal]]],
    failure: str,
) -> None:
    path = tmp_path / "m.py"
    path.write_text(_module(8))
    serial_engine, serial = _analyze(path, workers=1)
    serial_rendered = _rendered(serial_engine, path, serial)

    _lower_thresholds(monkeypatch)
    constructions = _recording_executor(monkeypatch)
    # Workers are forked after the patch, so the pickled reference to the
    # chunk evaluator resolves to the injected function in every worker.
    monkeypatch.setattr(parallel, "_evaluate_pair_chunk", chunk_evaluator)
    with caplog.at_level(logging.WARNING, logger="towel"):
        parallel_engine, recovered = _analyze(path, workers=2)

    assert len(constructions) == 1
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "Parallel pair evaluation unavailable" in w and failure in w for w in warnings
    ), warnings
    assert [p.description for p in recovered] == [p.description for p in serial]
    assert _rendered(parallel_engine, path, recovered) == serial_rendered
    assert parallel._worker_engine is None and parallel._worker_pairs is None
