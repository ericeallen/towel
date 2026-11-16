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

"""Review item 5: relaxed CLI preserves branch errors, values, identity and ordinary effects."""

import json
from pathlib import Path

import pytest

from tests import test_if_fresh_bindings_cli as branches

pytestmark = pytest.mark.relaxed_mode


def _ordinary_trace(output: str) -> tuple[list[str], list[str]]:
    ordinary: list[str] = []
    finalizers: list[str] = []
    for line in output.splitlines():
        if not line.startswith(('["first",', '["second",', '["third",')):
            ordinary.append(line)
            continue
        record = json.loads(line)
        assert isinstance(record, list) and len(record) == 2
        name, events = record
        assert isinstance(name, str) and isinstance(events, list)
        kept = []
        for event in events:
            assert isinstance(event, list) and event
            if event[0] == "del":
                finalizers.append(json.dumps(event))
            else:
                kept.append(event)
        ordinary.append(json.dumps([name, kept]))
    return ordinary, sorted(finalizers)


@pytest.mark.parametrize("case", branches._CASES, ids=lambda case: case.name)
def test_branch_observations_in_relaxed_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: branches.BranchCase
) -> None:
    environment = branches._environment()
    environment["TOWEL_WORKERS"] = "2"
    monkeypatch.setattr(branches, "_environment", lambda: environment.copy())
    monkeypatch.setattr(branches, "child_mode_flags", lambda: ["--no-strict"])
    original, output, _ = branches._extract(tmp_path, case)
    driver = tmp_path / "observe.py"
    driver.write_text(branches._DRIVER)
    for mode in branches._MODES:
        before = branches._observe(driver, original, case, mode)
        after = branches._observe(driver, output, case, mode)
        assert before[0] == after[0] == 0 and before[2] == after[2] == ""
        assert _ordinary_trace(after[1]) == _ordinary_trace(before[1]), (
            case.name,
            mode,
            before,
            after,
        )


def test_branch_comparator_detects_ordinary_order_and_values() -> None:
    reference = '["first", [["call", "a"], ["del", "x"], ["call", "b"], ["return", "int", "1"]]]'
    finalizer_moved = (
        '["first", [["del", "x"], ["call", "a"], ["call", "b"], ["return", "int", "1"]]]'
    )
    assert _ordinary_trace(reference) == _ordinary_trace(finalizer_moved)
    assert _ordinary_trace(reference) != _ordinary_trace(reference.replace('"a"', '"c"'))
    assert _ordinary_trace(reference) != _ordinary_trace(reference.replace('"1"', '"2"'))
