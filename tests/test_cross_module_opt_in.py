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

"""A helper is shared across modules only when the user asks for it.

Sharing a helper between two modules adds an import between them, a change to
how the project's modules depend on each other that a user should ask for
rather than find in the diff. By default Towel pairs duplicates only within a
module, spends no pair budget on the rest, and writes no import of a project
module that runs; ``--cross-module`` (``cross_module_helpers``) asks for the
rest.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from typing import Mapping

import pytest

import towel
from towel.cli import DryOptions, PreviewOptions, _build_parser
from towel.diagnostics import Settings
from towel.unification.exceptions import RefactoringError
from towel.unification.pipeline import analyze_scopes, collect_functions, parse_modules
from towel.unification.refactor_engine import UnificationRefactorEngine

SERIAL = Settings.from_environ({"TOWEL_WORKERS": "1"})

_BLOCK = """
def {name}(values):
    print({tag!r})
    total = 0
    for value in values:
        if value > 1:
            total += value * 2
        else:
            total -= value
    total = total + 1
    return total
"""


def _write(root: Path, files: Mapping[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
    return root


def _package(root: Path) -> Path:
    """One package whose two modules each hold the same block, and nothing else to share."""
    return _write(
        root,
        {
            "zzshared/__init__.py": "",
            "zzshared/first.py": _BLOCK.format(name="total_first", tag="first"),
            "zzshared/second.py": _BLOCK.format(name="total_second", tag="second"),
        },
    )


def _dry(target: Path, output: Path, *flags: str) -> str:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "towel.cli",
            "dry",
            str(target),
            str(output),
            "--no-interactive",
            "--no-types",
            "--no-format",
            "--progress",
            "none",
            *flags,
        ],
        capture_output=True,
        text=True,
        cwd=target.parent,
        env=dict(os.environ, PYTHONPATH=str(Path(towel.__file__).resolve().parents[1])),
        timeout=600,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout + result.stderr


def _results(root: Path) -> str:
    """What both functions print and return, run from the package's parent."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from zzshared.first import total_first\n"
            "from zzshared.second import total_second\n"
            "print(total_first([0, 2, 3]), total_second([1, 5]))\n",
        ],
        capture_output=True,
        text=True,
        cwd=root,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"},
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_the_flag_is_off_unless_asked_for() -> None:
    parser = _build_parser()
    for command in (["dry", "in", "out"], ["preview", "in"]):
        assert parser.parse_args(command).cross_module is False
        assert parser.parse_args([*command, "--cross-module"]).cross_module is True
        assert parser.parse_args([*command, "--no-cross-module"]).cross_module is False
    assert DryOptions.from_namespace(parser.parse_args(["dry", "in", "out"])).cross_module is False
    assert DryOptions.from_namespace(
        parser.parse_args(["dry", "in", "out", "--cross-module"])
    ).cross_module
    assert PreviewOptions.from_namespace(
        parser.parse_args(["preview", "in", "--cross-module"])
    ).cross_module
    assert UnificationRefactorEngine().cross_module_helpers is False


def test_no_pair_across_modules_is_formed_or_budgeted_by_default(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = _package(tmp_path / "project")
    paths = [str(root / "zzshared" / name) for name in ("first.py", "second.py")]
    functions = collect_functions(analyze_scopes(parse_modules(paths)))
    across = UnificationRefactorEngine(
        min_lines=3, cross_module_helpers=True, settings=SERIAL
    ).find_block_pairs(functions, progress="none")
    assert across and all(pair.is_cross_file for pair in across)
    assert UnificationRefactorEngine(min_lines=3, settings=SERIAL).find_block_pairs(
        functions, progress="none"
    ) == [pair for pair in across if not pair.is_cross_file]
    # The budget counts only the pairs a run can form: none here by default.
    with caplog.at_level(logging.WARNING, logger="towel"):
        UnificationRefactorEngine(
            min_lines=3, max_candidate_pairs=1, settings=SERIAL
        ).find_block_pairs(functions, progress="none")
    assert "exceed the budget" not in caplog.text
    with caplog.at_level(logging.WARNING, logger="towel"):
        UnificationRefactorEngine(
            min_lines=3, max_candidate_pairs=1, cross_module_helpers=True, settings=SERIAL
        ).find_block_pairs(functions, progress="none")
    assert "exceed the budget of 1" in caplog.text


def test_a_duplicate_across_modules_is_shared_only_when_asked(tmp_path: Path) -> None:
    root = _package(tmp_path / "project")
    expected = _results(root)
    originals = {
        name: (root / "zzshared" / name).read_text(encoding="utf-8")
        for name in ("first.py", "second.py")
    }

    alone = tmp_path / "alone"
    log = _dry(root / "zzshared", alone / "zzshared")
    assert "No refactorings found!" in log, log
    for name, text in originals.items():
        assert (alone / "zzshared" / name).read_text(encoding="utf-8") == text

    shared = tmp_path / "shared"
    _dry(root / "zzshared", shared / "zzshared", "--cross-module")
    written = {name: (shared / "zzshared" / name).read_text(encoding="utf-8") for name in originals}
    assert sum("def __extracted_func_0(" in text for text in written.values()) == 1
    assert sum("import __extracted_func_0" in text for text in written.values()) == 1
    assert _results(shared) == expected


def test_a_cross_module_proposal_built_elsewhere_is_refused_by_default(tmp_path: Path) -> None:
    """Pairing never forms one; a caller that brings one gets no import between modules."""
    root = _package(tmp_path / "project")
    paths = [str(root / "zzshared" / name) for name in ("first.py", "second.py")]
    (proposal,) = UnificationRefactorEngine(
        min_lines=3, cross_module_helpers=True, settings=SERIAL
    ).analyze_files(paths, progress="none")
    with pytest.raises(RefactoringError, match="--cross-module"):
        UnificationRefactorEngine(min_lines=3, settings=SERIAL).apply_refactoring_multi_file(
            proposal
        )
