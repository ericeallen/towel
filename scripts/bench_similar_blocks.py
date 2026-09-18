#!/usr/bin/env python3
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

"""How ``towel dry`` scales with the number of similar blocks in one file.

Emits a module of N functions that share a four-line block and differ only
in a trailing constant, runs ``towel dry`` on it serially with types and
formatting off, and prints the wall time per N. Doubling N should roughly
quadruple the time (the pairing is quadratic); a larger ratio means a
per-pair cost that itself grows with N.

    python scripts/bench_similar_blocks.py 50 100 200
    python scripts/bench_similar_blocks.py --emit 100 /tmp/similar.py
"""

from __future__ import annotations

import argparse
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Sequence

_FUNCTION = """\
def f{index}(order):
    items = [i for i in order.items if i.in_stock]
    subtotal = sum(i.price for i in items)
    total = round(subtotal * 1.08, 2)
    return "F{index} " + str(total)
"""


def similar_functions_module(count: int) -> str:
    """A module of ``count`` functions sharing one four-line block."""
    return "\n".join(_FUNCTION.format(index=index) for index in range(count))


def time_dry_run(count: int, python: str, workdir: Path) -> float:
    """Wall seconds ``towel dry`` takes on a module of ``count`` similar functions."""
    source_dir = workdir / f"in{count}"
    output_dir = workdir / f"out{count}"
    source_dir.mkdir()
    (source_dir / "big.py").write_text(similar_functions_module(count))
    command = [
        python,
        "-m",
        "towel.cli",
        "dry",
        str(source_dir),
        str(output_dir),
        "--no-interactive",
        "--progress",
        "none",
        "--no-types",
        "--no-format",
    ]
    started = time.monotonic()
    subprocess.run(
        command,
        check=True,
        env={**os.environ, "TOWEL_WORKERS": "1"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return time.monotonic() - started


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("counts", nargs="*", type=int, help="function counts to time")
    parser.add_argument("--emit", nargs=2, metavar=("N", "PATH"), help="write a module and stop")
    parser.add_argument("--python", default=sys.executable, help="interpreter running towel")
    args = parser.parse_args(argv)
    if args.emit:
        count, path = int(args.emit[0]), Path(args.emit[1])
        path.write_text(similar_functions_module(count))
        print(f"wrote {count} functions to {path}")
        return 0
    if not args.counts:
        parser.error("give one or more function counts, or --emit")
    workdir = Path(tempfile.mkdtemp(prefix="towel-bench-"))
    try:
        previous: tuple[int, float] | None = None
        for count in args.counts:
            elapsed = time_dry_run(count, args.python, workdir)
            ratio = ""
            if previous is not None and previous[1] > 0:
                exponent = math.log(elapsed / previous[1]) / math.log(count / previous[0])
                ratio = f"  (x{elapsed / previous[1]:.1f} for x{count / previous[0]:.1f}"
                ratio += f" functions, exponent ~{exponent:.2f})"
            print(f"N={count:4d}: {elapsed:8.1f} s{ratio}", flush=True)
            previous = (count, elapsed)
    finally:
        shutil.rmtree(workdir)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
