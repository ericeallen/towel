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

"""A run that declined what it found says why, in counts.

"No refactorings found!" was all a run printed when it declined every pair
and every proposal, even with ``--progress detail``: a project whose two
packages share code but never import each other (the audit's L40, where a
Hatch ``force-include`` also hid the layout from the packaging readers that
named modules then) read exactly like a project with nothing duplicated. A
proposal the type checker refused was reported as one that "could not be
rendered". The summary now counts what was declined, by reason.
"""

from __future__ import annotations

import contextlib
import io
import multiprocessing
from pathlib import Path
import re
import textwrap
from typing import Dict, List, Mapping, Sequence, Tuple

import pytest

from towel.type_inference import (
    CheckResult,
    CheckSuccess,
    RevealKey,
    RevealRequest,
    Subtyping,
    TypeDiagnostic,
)
from towel.unification.parallel import ParallelEvaluation
from towel.unification.refactor_engine import UnificationRefactorEngine
from tests.probe_answers import answer_probes
from tests.test_cli_integration import invoke

BLOCK = """
def {name}(items: list[int], scale: int) -> int:
    print("begin {tag}")
    total = 0
    for item in items:
        if item > 0:
            total += item * scale
        else:
            total -= item
    count = len(items)
    print("{tag}", total, count)
    return total + count + {number}
"""


def _force_include_project(root: Path) -> Path:
    """The audit's L40: two packages, neither importing the other, under a Hatch force-include."""
    (root / "src" / "alpha").mkdir(parents=True)
    (root / "src" / "beta").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        textwrap.dedent("""
            [build-system]
            requires = ["hatchling"]
            build-backend = "hatchling.build"

            [project]
            name = "alpha"
            version = "0.1"
            requires-python = ">=3.10"

            [tool.hatch.build.targets.wheel.force-include]
            "src/alpha" = "alpha"
            """).lstrip(),
        encoding="utf-8",
    )
    for package, name, tag, number in (("alpha", "fa", "a", 1), ("beta", "fb", "bb", 2)):
        (root / "src" / package / "__init__.py").write_text("", encoding="utf-8")
        (root / "src" / package / f"{name[1]}.py").write_text(
            BLOCK.format(name=name, tag=tag, number=number).lstrip(), encoding="utf-8"
        )
    return root


@pytest.mark.parametrize("progress", ["none", "detail"])
def test_a_run_whose_imports_allow_no_helper_says_why(tmp_path: Path, progress: str) -> None:
    root = _force_include_project(tmp_path / "project")
    result = invoke(
        [
            "dry",
            str(root),
            str(root),
            "--no-interactive",
            "--no-types",
            "--cross-module",
            "--progress",
            progress,
        ]
    )
    assert result.status == 0, result
    assert "No refactorings found!" in result.stdout
    # No module of either package is known to import the other: the
    # program's own imports show no import between them that works.
    assert re.search(r"candidate pair\(s\) declined: .*unproven_import \d+", result.stdout)
    if progress == "detail":
        assert re.search(r"\[towel\] Declined \d+ candidate pair\(s\):", result.stderr)


class _RefusesEveryCandidate:
    """Passes the original project and finds an error in every candidate."""

    def __init__(self) -> None:
        self.checks = 0

    def check_project(
        self, sources: Mapping[str, str], *, excluded_paths: Sequence[str] = ()
    ) -> CheckResult:
        self.checks += 1
        if self.checks == 1:
            return CheckSuccess()
        path = next(iter(sources))
        return CheckSuccess((TypeDiagnostic(path, "invented: incompatible types", 1),))

    def check(self, file_path: str, source: str) -> CheckResult:
        return self.check_project({file_path: source})

    def reveal(self, requests: Sequence[RevealRequest]) -> Mapping[RevealKey, str]:
        return answer_probes(requests)

    def is_subtype(
        self, file_path: str, source: str, pairs: Sequence[Tuple[str, str]]
    ) -> Sequence[Subtyping]:
        return [Subtyping.UNKNOWN] * len(pairs)

    def close(self) -> None:
        pass


def test_a_refusal_by_the_checker_is_called_that(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "m.py").write_text(
        BLOCK.format(name="fa", tag="a", number=1) + BLOCK.format(name="fb", tag="bb", number=2),
        encoding="utf-8",
    )
    monkeypatch.setattr("towel.cli._type_oracle", lambda path: _RefusesEveryCandidate())
    target = str(root / "pkg")
    result = invoke(
        ["dry", target, target, "--no-interactive", "--no-format", "--progress", "none"]
    )
    assert result.status == 0, result
    assert "refused by the type checker 1" in result.stdout
    assert "could not be rendered" not in result.stderr
    assert "the type checker refused" in result.stderr


def _similar_functions(root: Path, count: int) -> Path:
    root.mkdir(parents=True)
    body = "".join(
        f"def f{index}(items, scale):\n"
        f"    print('start {index}')\n"
        "    total = 0\n"
        "    for item in items:\n"
        "        total += item * scale\n"
        f"    print('end', total, {index})\n"
        f"    return total + {index}\n\n\n"
        for index in range(count)
    )
    path = root / "m.py"
    path.write_text(body, encoding="utf-8")
    return path


def _declined(engine: UnificationRefactorEngine, path: Path) -> Dict[str, int]:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        engine.analyze_files([str(path)], progress="none")
    return dict(engine.declined_pairs)


@pytest.mark.skipif(
    "fork" not in multiprocessing.get_all_start_methods(), reason="needs the fork start method"
)
@pytest.mark.filterwarnings("ignore:This process .* is multi-threaded:DeprecationWarning")
def test_forked_evaluation_counts_what_serial_evaluation_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Workers count their own pairs and not the probe's, so the totals do not move."""
    path = _similar_functions(tmp_path / "project", 24)
    serial = _declined(UnificationRefactorEngine(min_lines=2), path)
    monkeypatch.setattr(ParallelEvaluation, "PARALLEL_PAIR_THRESHOLD", 1)
    monkeypatch.setattr(ParallelEvaluation, "PARALLEL_PROBE_PAIRS", 8)
    monkeypatch.setattr(ParallelEvaluation, "PARALLEL_MIN_PROJECTED_SECONDS", 0.0)
    monkeypatch.setattr(ParallelEvaluation, "_parallel_workers", lambda self: 2)
    forked: List[bool] = []
    original = ParallelEvaluation._evaluate_pairs_parallel

    def recording(self: ParallelEvaluation, *args: object, **kwargs: object) -> object:
        forked.append(True)
        return original(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(ParallelEvaluation, "_evaluate_pairs_parallel", recording)
    parallel = _declined(UnificationRefactorEngine(min_lines=2), path)
    assert forked, "the second analysis forked"
    assert sum(serial.values()) > 0
    assert parallel == serial
