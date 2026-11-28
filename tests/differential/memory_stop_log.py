# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Describe an observer the memory budget stopped, for the log; never part of the verdict.

CI's 3.15 job stopped the observer of grammar seed 167's refactored program
at a 619 MB resident sample (run 46071013861), a program that holds about
22 MiB on every local run. What the sampler read cannot be told from the
record alone. The description written here can tell it: every sample taken
of that observer, and, on Linux, the kernel's own accounts of the process
while it is still alive. ``/proc/<pid>/status`` carries ``VmRSS`` from the
kernel's resident-memory counters, split into anonymous, file and shared
memory, with the process's name, parent and threads; ``smaps_rollup`` sums
the same memory by walking the page tables, so the two disagreeing means a
counting fault rather than memory used. The mappings holding the most
resident memory say whether it is the heap, an anonymous mapping or a file.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import sys
from typing import List, Sequence, Tuple

from tests.differential.process_memory import resident_bytes

Sample = Tuple[float, int]
"""One resident-memory sample: seconds since the observer started, and bytes."""

_MAPPING_HEADER = re.compile(r"[0-9a-f]+-[0-9a-f]+ ")
"""How a mapping's header line in ``smaps`` begins: its address range."""
MAPPINGS_SHOWN = 8
SAMPLES_HEAD = 20
SAMPLES_TAIL = 100


def render_samples(samples: Sequence[Sample]) -> str:
    """The samples in order as ``+milliseconds:KiB``, eliding the middle of a long run."""

    def render(sample: Sample) -> str:
        seconds, resident = sample
        return f"+{seconds * 1000:.1f}ms:{resident // 1024}k"

    if len(samples) <= SAMPLES_HEAD + SAMPLES_TAIL:
        return " ".join(map(render, samples))
    elided = len(samples) - SAMPLES_HEAD - SAMPLES_TAIL
    return " ".join(
        [
            *map(render, samples[:SAMPLES_HEAD]),
            f"... {elided} more ...",
            *map(render, samples[-SAMPLES_TAIL:]),
        ]
    )


def largest_mappings(smaps: str, count: int = MAPPINGS_SHOWN) -> List[Tuple[int, str]]:
    """The ``count`` mappings of a ``/proc/<pid>/smaps`` text with the most resident KiB.

    Each is ``(Rss in KiB, the mapping's header line)``, largest first.
    """
    mappings: List[Tuple[int, str]] = []
    header = ""
    for line in smaps.splitlines():
        if _MAPPING_HEADER.match(line):
            header = line
        elif line.startswith("Rss:") and header:
            mappings.append((int(line.split()[1]), header))
    return sorted(mappings, key=lambda mapping: -mapping[0])[:count]


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError as error:
        return f"(unreadable: {error})"


def _process_accounts(pid: int) -> List[str]:
    """The kernel's accounts of a live Linux process, as log sections."""
    proc = Path(f"/proc/{pid}")
    smaps = _read(proc / "smaps")
    largest = [f"{rss:>10} kB  {header}" for rss, header in largest_mappings(smaps)]
    return [
        f"--- cmdline: {_read(proc / 'cmdline').replace(chr(0), ' ').strip()}",
        f"--- /proc/{pid}/status ---",
        _read(proc / "status").rstrip(),
        f"--- /proc/{pid}/smaps_rollup ---",
        _read(proc / "smaps_rollup").rstrip(),
        f"--- mappings with the most resident memory (of /proc/{pid}/smaps) ---",
        *(largest or ["(none read)"]),
    ]


def describe_stop(
    pid: int,
    stopped_by: str,
    budget: int,
    resident: int,
    samples: Sequence[Sample],
    *,
    alive: bool,
) -> str:
    """The log text for one observer the budget stopped.

    ``alive`` says the process is still there to read (a sample stopped it
    before the kill); after exit only the samples remain.
    """
    runner = os.getpid()
    lines = [
        f"=== observer memory stop: pid {pid}, {stopped_by}, {resident} bytes"
        f" against a {budget}-byte budget ===",
        f"runner pid {runner} resident {resident_bytes(runner)} bytes,"
        f" Python {sys.version.split()[0]}",
        f"{len(samples)} samples (time since start: resident): {render_samples(samples)}",
    ]
    if alive and sys.platform == "linux":
        lines += _process_accounts(pid)
    return "\n".join(lines)
