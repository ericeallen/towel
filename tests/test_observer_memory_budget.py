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

import pytest

from tests.differential import runner
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
    strict campaign once crashed in), and the parent says which check fired.
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
    assert detail["observer_memory"]["resident_bytes"] == 0
