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

"""Runtime item 2: visible counts and an honest apply rate."""

import contextlib
import io
import re
from pathlib import Path

import pytest

from tests.test_parallel_evaluation import _module, _settings, _lower_thresholds
from towel.unification import parallel, progress
from towel.unification.fixed_point import _ApplyProgress
from towel.unification.refactor_engine import UnificationRefactorEngine


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.mark.parametrize("fallback", [False, True])
def test_pair_analysis_displays_judged_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fallback: bool
) -> None:
    """Runtime 2: pair progress survives both serial analysis and unavailable pools."""
    source = tmp_path / "module.py"
    source.write_text(_module(8))
    monkeypatch.setattr(progress, "load_tqdm", lambda: None)
    engine = UnificationRefactorEngine(min_lines=3, settings=_settings(2 if fallback else 1))
    if fallback:
        _lower_thresholds(monkeypatch)

        def unavailable(**kwargs: object) -> None:
            raise OSError("injected unavailable pool")

        monkeypatch.setattr(parallel, "ProcessPoolExecutor", unavailable)
    stream = _Terminal()
    with contextlib.redirect_stderr(stream):
        engine.analyze_files([str(source)], progress="auto")
        output = stream.getvalue()
    assert "pairs judged" in output
    counts = re.findall(r"pairs judged: (\d+)/(\d+) pair", output)
    assert counts and counts[-1][0] == counts[-1][1]


def test_apply_progress_displays_applied_queued_and_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runtime 2: an expanding work queue reports throughput instead of an ETA."""
    monkeypatch.setattr(progress, "load_tqdm", lambda: None)
    stream = _Terminal()
    with contextlib.redirect_stderr(stream):
        reporter = _ApplyProgress("auto")
        try:
            reporter.applied(2, 3, 2, "shared helper")
            output = stream.getvalue()
        finally:
            reporter.close()
    assert "2" in output and "3" in output
    assert "rate" in output or "/s" in output
    assert "ETA" not in output
