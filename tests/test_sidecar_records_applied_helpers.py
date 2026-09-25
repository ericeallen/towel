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

"""The ``.towel-helpers.json`` sidecar describes only helpers the output contains.

Each call site is recorded while a proposal is rendered, before the driver
decides whether to write it. A proposal dropped after rendering -- its text
cannot be written in the file's encoding, it changed nothing, its files moved
under it -- kept its records, and the sidecar described ``__extracted_func_0``
in a run that printed "No refactorings found!", so ``rename-helpers`` offered
before/after pairs for code that does not exist.

The dropped proposal here is the most direct one there is: a Latin-1 module
whose duplicated blocks bind ``µ`` (MICRO SIGN, which Latin-1 holds). Python
reads that identifier as ``μ`` (GREEK SMALL LETTER MU), which Latin-1 does
not hold and no escape can spell in a name, so the rendered helper cannot be
written back to its file.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path

import pytest

from towel.unification.refactor_engine import UnificationRefactorEngine
from tests.test_cli_integration import invoke

LATIN1_MODULE = (
    "# -*- coding: latin-1 -*-\n\n\n"
    'def first(value):\n    print("first")\n    \u00b5 = value + 1\n'
    "    scaled = \u00b5 * 2\n    label = str(scaled)\n    return label\n\n\n"
    'def second(value):\n    print("second")\n    \u00b5 = value + 1\n'
    '    scaled = \u00b5 * 2\n    label = str(scaled)\n    return label + "!"\n'
).encode("latin-1")


def _module(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "m.py"
    path.write_bytes(LATIN1_MODULE)
    return path


@pytest.mark.parametrize("directory", [False, True])
def test_a_proposal_dropped_after_rendering_leaves_no_record(
    tmp_path: Path, directory: bool
) -> None:
    path = _module(tmp_path / "project")
    engine = UnificationRefactorEngine(min_lines=3, reuse_existing_functions=False)
    with contextlib.redirect_stdout(io.StringIO()):
        if directory:
            results, _ = engine.refactor_directory_to_fixed_point(
                str(path.parent), str(path.parent), progress="none"
            )
            applied = sum(count for count, _ in results.values())
        else:
            _code, applied, _ = engine.refactor_to_fixed_point(str(path), progress="none")
    assert applied == 0
    assert path.read_bytes() == LATIN1_MODULE
    assert engine.change_log == ()


def test_the_command_line_writes_no_sidecar_for_a_run_that_applied_nothing(
    tmp_path: Path,
) -> None:
    path = _module(tmp_path / "project")
    target = str(path.parent)
    result = invoke(
        [
            "dry",
            target,
            target,
            "--no-interactive",
            "--no-types",
            "--no-format",
            "--progress",
            "none",
        ]
    )
    assert result.status == 0, result
    assert "No refactorings found" in result.stdout
    assert not (path.parent / ".towel-helpers.json").exists()
