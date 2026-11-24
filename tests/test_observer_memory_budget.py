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

"""The observer's memory budget is a budget of resident memory, not of address space.

CI run 45322452556 on 426efe56 (tests (3.15), x86_64 Linux) judged the
original programs of grammar seeds 19 and 237 inconclusive, "exceeds its
observation budget: memory", in two attempts. Those programs touch about
20 MiB. The observer capped its address space (``RLIMIT_AS``) at the 256 MiB
budget, so address space reserved but never touched counted as memory used;
and its record did not say which check stopped it, or what it measured.
"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

from tests.differential import export, runner
from tests.differential.cases import Calls, Case
from tests.differential.grammar import generate_case

RESERVATION = 512 * 1024 * 1024
"""Twice the budget: untouched, it must not count; touched, it would be over budget."""

_RESERVING_OBSERVER = """\
import mmap, runpy, sys

# Reserve address space the way an interpreter or allocator may: mapped,
# writable, never touched, so it holds no resident memory.
RESERVED = mmap.mmap(-1, {size}, flags=mmap.MAP_PRIVATE)
observer = {observer!r}
sys.argv = [observer, *sys.argv[1:]]
runpy.run_path(observer, run_name="__main__")
"""


def _case(source: str) -> Case:
    return Case(
        "memory-budget",
        1,
        False,
        "single",
        frozenset(),
        (("m.py", source),),
        (("m", "m"),),
        (Calls("m.f", "[((),)]"),),
    )


def _rewrite(monkeypatch: pytest.MonkeyPatch, source: str) -> None:
    def rewrite(case: Case, mode: runner.Mode, root: Path, oracle: object, scopes: object) -> int:
        (root / "m.py").write_text(source)
        return 1

    monkeypatch.setattr(runner, "_refactor", rewrite)


def _reserving_observer(monkeypatch: pytest.MonkeyPatch, directory: Path) -> None:
    """Make every observer interpreter start with ``RESERVATION`` bytes of untouched address space."""
    shim = directory / "reserving_observer.py"
    shim.write_text(_RESERVING_OBSERVER.format(size=RESERVATION, observer=str(runner.OBSERVER)))
    monkeypatch.setattr(runner, "OBSERVER", shim)


def test_untouched_reservation_in_the_program_is_not_memory_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A program that maps twice the budget without touching it is observed in full.

    Before 2.0's fix, Linux's ``RLIMIT_AS`` refused the mapping, so the
    observer recorded an ``OSError`` the program never raises elsewhere.
    """
    source = (
        "import mmap\n"
        f"RESERVED = mmap.mmap(-1, {RESERVATION}, flags=mmap.MAP_PRIVATE)\n"
        "def f(arg):\n"
        "    return len(RESERVED)\n"
    )
    _rewrite(monkeypatch, source + "\n# changed\n")
    outcome = runner.run_case(_case(source), runner.DEFAULT, tmp_path)
    assert outcome.status == "equivalent", outcome.report()
    observed = json.loads((tmp_path / "before.observed.json").read_text())
    assert observed["imports"][0]["ok"], observed["imports"]
    assert observed["probes"][0]["calls"][0]["value"] == str(RESERVATION)


@pytest.mark.parametrize(
    ("seed", "mode"),
    [(19, runner.CROSS_MODULE), (237, runner.DEFAULT)],
    ids=["seed19-cross-module", "seed237-default"],
)
def test_ci_seeds_are_conclusive_when_the_interpreter_reserves_address_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seed: int, mode: runner.Mode
) -> None:
    """The programs CI judged over budget stay conclusive beside a large untouched reservation."""
    _reserving_observer(monkeypatch, tmp_path)
    outcome = runner.run_case(generate_case(seed), mode, tmp_path / "work")
    assert outcome.status in ("unchanged", "equivalent"), outcome.report()


def test_touched_memory_over_budget_is_stopped_by_resident_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Touching the reservation is real use: the record names the check and what it measured."""
    source = (
        "def f(arg):\n"
        f"    values = bytearray({RESERVATION})\n"
        "    for offset in range(0, len(values), 4096):\n"
        "        values[offset] = 1\n"
        "    return len(values)\n"
    )
    _rewrite(monkeypatch, source + "\n# changed\n")
    outcome = runner.run_case(_case(source), runner.DEFAULT, tmp_path, call_seconds=30)
    assert outcome.status == "inconclusive"
    detail = json.loads(outcome.detail.split(": ", 1)[1])
    assert detail["observer_resource"] == "memory"
    measured = detail["observer_memory"]
    assert measured["stopped_by"] == "resident memory"
    assert measured["budget_bytes"] == runner.OBSERVER_MEMORY_BYTES
    assert measured["resident_bytes"] > runner.OBSERVER_MEMORY_BYTES


def test_runaway_seed_is_contained_and_says_how(tmp_path: Path) -> None:
    """Strict seed 58534072798479847871598822171 grows the list it iterates without end.

    It is still stopped within the budget, and its record says which check
    stopped it, with the resident memory last sampled, so a later inconclusive
    observation can be told apart from a real runaway.
    """
    case = generate_case(58534072798479847871598822171, bindings=True)
    outcome = runner.run_case(case, replace(runner.DEFAULT, strict=True), tmp_path)
    assert outcome.status == "inconclusive"
    assert not (tmp_path / "after.observer.stdout").exists()
    detail = json.loads(outcome.detail.split(": ", 1)[1])
    assert detail["observer_resource"] == "memory"
    measured = detail["observer_memory"]
    assert measured["stopped_by"] in ("resident memory", "MemoryError in the observer")
    assert measured["budget_bytes"] == runner.OBSERVER_MEMORY_BYTES


@pytest.mark.skipif(sys.platform != "linux", reason="RLIMIT_AS is applied on Linux only")
def test_address_space_cap_contains_a_runaway_the_sampler_misses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the RSS sampler blind, the address-space safety cap still stops a runaway.

    The observer then writes its fallback record itself (the path the 2.0
    strict campaign once crashed in), and the parent says which check fired,
    with the peak the observer reported rather than the blind sampler's zero.
    """
    assert runner.OBSERVER_ADDRESS_SPACE_BYTES >= 4 * runner.OBSERVER_MEMORY_BYTES
    monkeypatch.setattr(runner, "_resident_bytes", lambda pid: 0)
    source = (
        "def f(arg):\n    values = [0]\n    for value in values:\n        values.extend(values)\n"
    )
    _rewrite(monkeypatch, source + "\n# changed\n")
    outcome = runner.run_case(_case(source), runner.DEFAULT, tmp_path)
    assert outcome.status == "inconclusive"
    observed = json.loads((tmp_path / "before.observed.json").read_text())
    assert observed == {"observer_resource": "memory"}
    detail = json.loads(outcome.detail.split(": ", 1)[1])
    assert detail["observer_memory"]["stopped_by"] == "MemoryError in the observer"
    assert detail["observer_memory"]["resident_bytes"] > runner.OBSERVER_MEMORY_BYTES


# Round 2: the budget is decided from the observer's true peak, read when it
# exits (the review of c7c7304f, findings D1 and D2). The live sample only
# stops a runaway early; a sampler that misses the peak, or stalls, must not
# let an observation over budget pass as complete.

MIB = 1024 * 1024
TRIALS = 20
"""The review missed the transient spike in 13 of 20 runs; every run must flag it."""

_TRIVIAL = "def f(arg):\n    return 1\n"

_OVER_BUDGET_BEFORE_EXIT = {
    # About 300 MiB resident, over budget for a millisecond or two.
    "transient-spike": (
        "def f(arg):\n"
        f"    hold = bytearray({200 * MIB})\n"
        f"    spike = bytearray({80 * MIB})\n"
        "    total = hold[0] + spike[-1]\n"
        "    del spike, hold\n"
        "    return 1 + total\n"
    ),
    # About 320 MiB resident from the call until the observer exits. A view,
    # so that describing the module does not represent 300 MiB of bytes.
    "retained-to-exit": (
        "_retained = []\n"
        "def f(arg):\n"
        f"    _retained.append(memoryview(bytearray({300 * MIB})))\n"
        "    return 1\n"
    ),
    # About 320 MiB resident only in the program's own exit handler.
    "allocated-at-exit": (
        "import atexit\n"
        f"atexit.register(lambda: bytearray({300 * MIB}))\n"
        "def f(arg):\n"
        "    return 1\n"
    ),
}


@pytest.mark.parametrize("program", sorted(_OVER_BUDGET_BEFORE_EXIT))
def test_peak_over_budget_between_samples_is_flagged_every_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, program: str
) -> None:
    """Review of c7c7304f, D1: memory over budget that the 10 ms sample misses is still flagged.

    The refactored program returns what the original does, but touches more
    than the budget on the way. Its observation is over budget, so it may not
    pass as an equivalent, complete one.
    """
    _rewrite(monkeypatch, _OVER_BUDGET_BEFORE_EXIT[program])
    missed = [
        trial
        for trial in range(TRIALS)
        if not (
            outcome := runner.run_case(_case(_TRIVIAL), runner.DEFAULT, tmp_path / str(trial))
        ).observation_limited
        or outcome.status == "equivalent"
    ]
    assert missed == [], f"over budget unflagged in {len(missed)} of {TRIALS} runs"


def _blind_sampler(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate a parent whose live sampling stalls for the whole observation: it never sees memory."""
    monkeypatch.setattr(runner, "_resident_bytes", lambda pid: 0)


@pytest.mark.parametrize(
    "source",
    [
        f"def f(arg):\n    values = bytearray({RESERVATION})\n    return len(values)\n",
        "_retained = []\ndef f(arg):\n"
        f"    _retained.append(memoryview(bytearray({RESERVATION})))\n",
    ],
    ids=["freed-at-return", "retained-to-exit"],
)
def test_stalled_sampler_still_flags_the_original_and_records_its_peak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    """Review of c7c7304f, D1: with the live sample stalled, the peak read at exit decides.

    Touching twice the budget makes the original inconclusive, whether it is
    freed or kept until exit, and the record carries the peak, not a sample
    that saw nothing.
    """
    _blind_sampler(monkeypatch)
    _rewrite(monkeypatch, source + "\n# changed\n")
    outcome = runner.run_case(_case(source), runner.DEFAULT, tmp_path)
    assert outcome.status == "inconclusive", outcome.report()
    measured = json.loads(outcome.detail.split(": ", 1)[1])["observer_memory"]
    assert measured["stopped_by"] == "peak resident memory at exit"
    assert measured["resident_bytes"] >= RESERVATION


def test_stalled_sampler_keeps_export_from_replaying_without_a_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review of c7c7304f, D1: a stalled sample must not clear ``observation_limited``.

    Battery replay (``export.observe``) has no memory budget; it may run only
    when the observer's run stayed within its own.
    """
    _blind_sampler(monkeypatch)
    _rewrite(
        monkeypatch,
        "class Result:\n    def __repr__(self):\n"
        f"        return 'x' * {RESERVATION}\n"
        "def f(arg):\n    return Result()\n",
    )
    outcome = runner.run_case(_case(_TRIVIAL), runner.DEFAULT, tmp_path)
    assert outcome.status == "different"
    assert outcome.observation_limited
    unsafe_replay = Mock(side_effect=AssertionError("unbounded replay"))
    monkeypatch.setattr(export, "observe", unsafe_replay)
    export.write_fixture(outcome, tmp_path / "export")
    unsafe_replay.assert_not_called()


def test_memory_record_keeps_the_highest_sample(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review of c7c7304f, D2: a later, smaller sample never replaces the highest one.

    Samples taken while ``MemoryError`` unwinds, or after the observer has
    exited (zero), used to overwrite an earlier, higher one.
    """
    samples = iter([220 * MIB, 20 * MIB])
    monkeypatch.setattr(runner, "_resident_bytes", lambda pid: next(samples, 0))
    source = "import time\ndef f(arg):\n    time.sleep(0.2)\n    raise MemoryError('budget')\n"
    _rewrite(monkeypatch, source + "\n# changed\n")
    outcome = runner.run_case(_case(source), runner.DEFAULT, tmp_path)
    assert outcome.status == "inconclusive"
    measured = json.loads(outcome.detail.split(": ", 1)[1])["observer_memory"]
    assert measured["stopped_by"] == "MemoryError in the observer"
    assert measured["resident_bytes"] == 220 * MIB
