# Copyright 2026 Eric Allen
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

"""An observer the memory budget stops is described in the log before it is killed.

CI's 3.15 job (run 46071013861) stopped seed 167's observer at a 619 MB
resident sample, for a program that holds about 22 MiB locally, and its
record could not say what the sampler had read. The log now carries every
sample and, on Linux, the kernel's accounts of the live process and its
Python stack; the verdict stays what it was.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys

import pytest

from tests.differential import runner
from tests.differential.cases import Calls, Case
from tests.differential.memory_stop_log import largest_mappings, render_samples

MIB = 1024 * 1024

_HELD_OVER_BUDGET = (
    "import time\n"
    "def f(arg):\n"
    f"    values = bytearray({300 * MIB})\n"
    "    for offset in range(0, len(values), 4096):\n"
    "        values[offset] = 1\n"
    "    time.sleep(20)  # held, so a live sample must stop it\n"
    "    return len(values)\n"
)

_SMAPS = """\
55d4c8a00000-55d4c8a21000 r--p 00000000 08:01 123                        /usr/bin/python3.15
Size:                132 kB
Rss:                 120 kB
Pss:                 120 kB
7f0000000000-7f0012c00000 rw-p 00000000 00:00 0
Size:             307200 kB
Rss:              307200 kB
VmFlags: rd wr mr mw me ac
55d4c9000000-55d4c9400000 rw-p 00000000 00:00 0                          [heap]
Size:               4096 kB
Rss:                3000 kB
"""


def test_largest_mappings_are_ranked_by_resident_memory() -> None:
    assert largest_mappings(_SMAPS, 2) == [
        (307200, "7f0000000000-7f0012c00000 rw-p 00000000 00:00 0"),
        (3000, "55d4c9000000-55d4c9400000 rw-p 00000000 00:00 0                          [heap]"),
    ]


def test_a_long_run_of_samples_keeps_its_start_and_end() -> None:
    samples = [(index / 100, index * 1024) for index in range(500)]
    rendered = render_samples(samples)
    assert rendered.startswith("+0.0ms:0k +10.0ms:1k ")
    assert "... 380 more ..." in rendered
    assert rendered.endswith("+4990.0ms:499k")


def test_a_stopped_observer_is_described_and_the_verdict_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Memory held over budget is stopped by a live sample, which logs what the observer held."""

    def rewrite(case: Case, mode: runner.Mode, root: Path, oracle: object, scopes: object) -> int:
        (root / "m.py").write_text(_HELD_OVER_BUDGET + "\n# changed\n")
        return 1

    monkeypatch.setattr(runner, "_refactor", rewrite)
    case = Case(
        "memory-stop-log",
        1,
        False,
        "single",
        frozenset(),
        (("m.py", _HELD_OVER_BUDGET),),
        (("m", "m"),),
        (Calls("m.f", "[((),)]"),),
    )
    outcome = runner.run_case(case, runner.DEFAULT, tmp_path, call_seconds=30)

    assert outcome.status == "inconclusive"
    detail = json.loads(outcome.detail.split(": ", 1)[1])
    assert set(detail) == {"observer_resource", "observer_memory"}
    assert set(detail["observer_memory"]) == {"stopped_by", "budget_bytes", "resident_bytes"}
    assert detail["observer_memory"]["stopped_by"] == "resident memory"

    log = capsys.readouterr().err
    assert "=== observer memory stop: pid " in log
    assert f"against a {runner.OBSERVER_MEMORY_BYTES}-byte budget" in log
    assert " samples (time since start: resident): +" in log
    # The observer was asked for its stack before the kill: it was in the program.
    assert "--- the observer's Python stack ---" in log
    assert re.search(r'm\.py", line [3-6] in f\n', log), log
    if sys.platform == "linux":
        assert "VmRSS:" in log and "RssAnon:" in log  # the kernel's counters
        assert "--- /proc/" in log and "smaps_rollup ---" in log
        assert "Anonymous:" in log  # the page-table walk
        assert "--- mappings with the most resident memory" in log
