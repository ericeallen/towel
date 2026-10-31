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

"""Resource exhaustion in the original is inconclusive; a new one is a finding."""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from tests.differential import export, runner
from tests.differential.cases import Calls, Case
from tests.differential.grammar import generate_case


def _case(source: str) -> Case:
    return Case(
        "resource-control",
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


def test_growing_input_seed_is_inconclusive_without_launching_after(tmp_path: Path) -> None:
    case = generate_case(10060797319587863840408861851, bindings=True)
    outcome = runner.run_case(case, runner.DEFAULT, tmp_path)
    assert outcome.status == "inconclusive"
    assert outcome.applied > 0
    assert "memory" in outcome.detail or "timed_out" in outcome.detail
    assert not (tmp_path / "after.observer.stdout").exists()


@pytest.mark.parametrize(
    ("body", "limited"),
    [
        ("values = []\n    while True:\n        values.append(1)", True),
        ("while True:\n        pass", True),
        ("while True:\n        print('x' * 1000)", True),
        ("raise MemoryError('control')", True),
        ("return 2", False),
    ],
    ids=["memory", "timeout", "output", "memory-exception", "wrong-result"],
)
def test_transformed_only_failures_remain_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str, limited: bool
) -> None:
    _rewrite(monkeypatch, f"def f(arg):\n    {body}\n")
    outcome = runner.run_case(
        _case("def f(arg):\n    return 1\n"),
        runner.DEFAULT,
        tmp_path,
        call_seconds=0.5 if body.startswith("values") else 0.05,
    )
    assert outcome.status == "different"
    assert outcome.is_defect
    assert outcome.observation_limited is limited
    if body.startswith("values") or body.startswith("raise MemoryError"):
        assert any(change.after == "memory" for change in outcome.changes)
    if "print(" in body:
        assert any(change.after == "output" for change in outcome.changes)
    if limited:
        unsafe_replay = Mock(side_effect=AssertionError("unbounded replay"))
        monkeypatch.setattr(export, "observe", unsafe_replay)
        fixture = export.write_fixture(outcome, tmp_path / "export")
        assert fixture.path.exists()
        assert "replay was skipped" in fixture.note(outcome)
        unsafe_replay.assert_not_called()


@pytest.mark.parametrize("termination", ["os._exit(7)", "os.kill(os.getpid(), 9)"])
def test_unexplained_transformed_exit_is_a_harness_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, termination: str
) -> None:
    _rewrite(monkeypatch, f"import os\ndef f(arg):\n    {termination}\n")
    with pytest.raises(RuntimeError, match="the observer failed"):
        runner.run_case(_case("def f(arg):\n    return 1\n"), runner.DEFAULT, tmp_path)


@pytest.mark.parametrize(
    "source",
    [
        "raise MemoryError('import')\n",
        "class Result:\n    def __repr__(self):\n        raise MemoryError('repr')\n"
        "def f(arg):\n    return Result()\n",
        "class Result:\n    def __reduce__(self):\n        raise MemoryError('pickle')\n"
        "def f(arg):\n    return Result()\n",
    ],
    ids=["import", "repr", "pickle"],
)
def test_observation_helpers_do_not_swallow_memory_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    _rewrite(monkeypatch, source + "\n# changed\n")
    outcome = runner.run_case(_case(source), runner.DEFAULT, tmp_path)
    assert outcome.status == "inconclusive"
    assert '"observer_resource": "memory"' in outcome.detail
    assert not (tmp_path / "after.observer.stdout").exists()


def test_timeout_does_not_represent_mutated_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = (
        "class Arg:\n"
        "    interrupted = False\n"
        "    def __repr__(self):\n"
        "        if self.interrupted:\n"
        "            raise MemoryError('post-timeout repr must not run')\n"
        "        return 'arg'\n"
        "def f(arg):\n"
        "    arg.interrupted = True\n"
        "    while True:\n"
        "        pass\n"
    )
    case = _case(source)
    case = replace(case, probes=(Calls("m.f", "[(m.Arg(),)]"),))
    _rewrite(monkeypatch, source + "\n# changed\n")
    outcome = runner.run_case(case, runner.DEFAULT, tmp_path, call_seconds=0.02)
    assert outcome.status == "inconclusive"
    observed = json.loads((tmp_path / "before.observed.json").read_text())
    assert observed["timed_out"]
    assert "observer_resource" not in observed
    assert "arguments_after" not in observed["probes"][0]["calls"][0]


def test_parent_deadline_contains_imports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _rewrite(monkeypatch, "import time\ntime.sleep(60)\ndef f(arg):\n    return 1\n")
    monkeypatch.setattr(runner, "OBSERVER_SECONDS", 0.5)
    outcome = runner.run_case(_case("def f(arg):\n    return 1\n"), runner.DEFAULT, tmp_path)
    assert outcome.status == "different"
    assert outcome.observation_limited


def test_full_representation_allocation_is_contained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _rewrite(
        monkeypatch,
        "class Result:\n    def __repr__(self):\n        return 'x' * (512 * 1024 * 1024)\n"
        "def f(arg):\n    return Result()\n",
    )
    outcome = runner.run_case(_case("def f(arg):\n    return 1\n"), runner.DEFAULT, tmp_path)
    assert outcome.status == "different"
    assert outcome.observation_limited
    assert any(change.after == "memory" for change in outcome.changes)


def test_watchdog_failure_reaps_observer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pids: list[int] = []

    def broken_measurement(pid: int) -> int:
        pids.append(pid)
        raise PermissionError("RSS measurement denied")

    _rewrite(monkeypatch, "def f(arg):\n    return 2\n")
    monkeypatch.setattr(runner, "_resident_bytes", broken_measurement)
    with pytest.raises(PermissionError, match="RSS measurement denied"):
        runner.run_case(_case("def f(arg):\n    return 1\n"), runner.DEFAULT, tmp_path)
    assert len(pids) == 1
    with pytest.raises(ProcessLookupError):
        os.kill(pids[0], 0)
