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

"""A file too deeply nested to analyze is skipped with a warning; the run goes on.

The analysis walks each tree recursively, and one expression of a few hundred
chained terms is deeper than the interpreter's recursion limit. That used to
raise RecursionError out of directory mode and end the run for every file.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path

import pytest

from towel.unification.refactor_engine import UnificationRefactorEngine


def _function(name: str, threshold: int, tail: str = "") -> str:
    return (
        f"def {name}(items):\n    total = 0\n    for item in items:\n"
        f"        if item > {threshold}:\n            total += item * 2\n"
        f"    return total + 1{tail}\n"
    )


def test_a_file_too_deep_to_analyze_is_skipped_and_the_others_are_refactored(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    chain = " + (" + "+".join(str(term) for term in range(1500)) + ")"
    deep = _function("a", 4, chain) + "\n\n" + _function("b", 5, chain)
    (project / "deep.py").write_text(deep)
    (project / "ok.py").write_text(_function("c", 4) + "\n\n" + _function("d", 5))
    engine = UnificationRefactorEngine(min_lines=3)
    with contextlib.redirect_stdout(io.StringIO()):
        results, termination = engine.refactor_directory_to_fixed_point(
            str(project), str(project), progress="none"
        )
    assert termination == "fixed_point"
    assert set(results) == {str(project / "ok.py")}
    assert f"Skipping {project / 'deep.py'}: it nests too deeply" in caplog.text
    assert (project / "deep.py").read_text() == deep
