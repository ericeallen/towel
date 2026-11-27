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

"""The corpus's reviewed verdict for a project whose tests relaxed mode changes.

Eric's ruling of 2026-11-15: a corpus project whose tests change under relaxed
mode is a project for strict mode, not a release blocker, if it is rare and the
change is traced to a named assumption of relaxed mode. The harness holds that
to evidence from the same run: the manifest names the assumption and the tests,
only those tests differ, the test count is unchanged, and a strict refactor of
the same tree leaves the suite as it was. Anything else stays BROKEN.
"""

from __future__ import annotations

import concurrent.futures
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
from typing import Sequence

import pytest

from scripts import ecosystem_check as ecosystem
from tests.test_ecosystem_verdicts import _check, CANDIDATE

BEFORE = (0, "3 passed in 0.01s\n")
CHANGED = (1, "FAILED test_case.py::test_finalizer - closed late\n1 failed, 2 passed in 0.01s\n")
OTHER = (1, "FAILED test_case.py::test_total - wrong sum\n1 failed, 2 passed in 0.01s\n")
FEWER = (0, "2 passed in 0.01s\n")
REVIEWED = ecosystem.RelaxedAssumption(
    "object_lifetimes",
    "test_finalizer asserts a __del__ ran before the helper returned (handles.py:12)",
    ("test_case.py::test_finalizer",),
)


def test_a_reviewed_change_that_strict_mode_does_not_make_is_relaxed_assumption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    strict_calls: list[Sequence[str]] = []
    result = _check(
        tmp_path,
        monkeypatch,
        BEFORE,
        CHANGED,
        relaxed_assumption=REVIEWED,
        strict_calls=strict_calls,
    )
    assert result.verdict == "RELAXED_ASSUMPTION"
    assert result.detail == f"object_lifetimes: {REVIEWED.reason}"
    assert result.towel_mode == "relaxed"
    [command] = strict_calls
    assert ecosystem.STRICT_FLAG in command and "--no-strict" not in command
    assert ecosystem.CROSS_MODULE_FLAG in command
    check = result.strict_check
    assert check is not None and check.verdict == "PASS" and check.after is not None
    assert Path(check.refactor.log).name == "fixture-strict-refactor.log"
    assert Path(check.after.log).name == "fixture-strict-after.log"


def test_an_unreviewed_change_stays_broken_with_the_strict_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _check(tmp_path, monkeypatch, BEFORE, CHANGED)
    assert result.verdict == "BROKEN"
    assert result.strict_check is not None and result.strict_check.verdict == "PASS"


def test_a_change_strict_mode_makes_too_is_a_defect_whatever_the_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _check(
        tmp_path, monkeypatch, BEFORE, CHANGED, relaxed_assumption=REVIEWED, strict_after=CHANGED
    )
    assert result.verdict == "BROKEN"
    assert result.strict_check is not None and result.strict_check.verdict == "BROKEN"


def test_a_strict_refactor_that_fails_cannot_clear_the_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _check(
        tmp_path,
        monkeypatch,
        BEFORE,
        CHANGED,
        relaxed_assumption=REVIEWED,
        strict_refactor=(1, "Error: something\n"),
    )
    assert result.verdict == "BROKEN"
    assert result.strict_check is not None
    assert result.strict_check.verdict == "REFACTOR_FAILED" and result.strict_check.after is None


def test_a_test_the_review_does_not_name_stays_broken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _check(tmp_path, monkeypatch, BEFORE, OTHER, relaxed_assumption=REVIEWED)
    assert result.verdict == "BROKEN"


def test_a_changed_test_count_is_never_a_reviewed_assumption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _check(tmp_path, monkeypatch, BEFORE, FEWER, relaxed_assumption=REVIEWED)
    assert result.verdict == "BROKEN" and "test count changed" in result.detail
    assert result.strict_check is not None


def test_strict_mode_runs_strict_and_never_checks_itself_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _check(
        tmp_path, monkeypatch, BEFORE, CHANGED, mode="strict", relaxed_assumption=REVIEWED
    )
    assert result.verdict == "BROKEN" and result.towel_mode == "strict"
    assert result.strict_check is None


def test_the_relaxed_notice_is_recorded_from_the_refactor_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    notice = (
        "Note: relaxed mode assumes exact object lifetimes and function lookup timing are not "
        "observed; --strict (or strict = true under [tool.towel]) preserves them. "
        "Found handles.py:5: Handle.__del__."
    )
    result = _check(
        tmp_path, monkeypatch, BEFORE, BEFORE, refactor=(0, f"{notice}\nApplied 1 refactoring\n")
    )
    assert result.verdict == "PASS" and result.relaxed_notice == notice


@pytest.mark.parametrize(
    "table",
    [
        'reason = "why"\ntests = ["t"]',
        'assumption = "object_lifetimes"\ntests = ["t"]',
        'assumption = "garbage_collection"\nreason = "why"\ntests = ["t"]',
        'assumption = "object_lifetimes"\nreason = " "\ntests = ["t"]',
        'assumption = "object_lifetimes"\nreason = "why"\ntests = []',
        'assumption = "object_lifetimes"\nreason = "why"\ntests = ["t", "t"]',
        'assumption = "object_lifetimes"\nreason = "why"\ntests = "t"',
        'assumption = "object_lifetimes"\nreason = "why"\ntests = ["t"]\nextra = 1',
    ],
)
def test_a_relaxed_assumption_must_be_a_complete_reviewed_table(tmp_path: Path, table: str) -> None:
    manifest = tmp_path / "manifest.toml"
    manifest.write_text(
        '[[project]]\nname = "fixture"\nurl = "unused"\nrev = "' + "a" * 40 + '"\n'
        'package = "package.py"\n\n'
        f"[project.relaxed_assumption]\n{table}\n"
    )
    with pytest.raises(ValueError, match="fixture: relaxed_assumption"):
        ecosystem.load_manifest(manifest, [])


def test_a_complete_table_loads(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.toml"
    manifest.write_text(
        '[[project]]\nname = "fixture"\nurl = "unused"\nrev = "' + "a" * 40 + '"\n'
        'package = "package.py"\n\n'
        '[project.relaxed_assumption]\nassumption = "stable_module_names"\n'
        'reason = "a test patches mod.f while it runs"\ntests = ["test_a.py::test_patch"]\n'
    )
    [project] = ecosystem.load_manifest(manifest, [])
    assert project.relaxed_assumption == ecosystem.RelaxedAssumption(
        "stable_module_names", "a test patches mod.f while it runs", ("test_a.py::test_patch",)
    )


def test_the_shipped_manifest_loads() -> None:
    assert ecosystem.load_manifest(ecosystem.REPO / "scripts/ecosystem/manifest.toml", [])


@pytest.mark.parametrize("strict", [False, True])
def test_main_runs_the_requested_mode_and_reports_reviewed_assumptions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, strict: bool
) -> None:
    reviewed = ecosystem.Project(
        "reviewed", "unused", "pinned", "a.py", relaxed_assumption=REVIEWED
    )
    stale = ecosystem.Project("stale", "unused", "pinned", "b.py", relaxed_assumption=REVIEWED)
    plain = ecosystem.Project("plain", "unused", "pinned", "c.py")
    monkeypatch.setattr(ecosystem, "load_manifest", lambda *_: [reviewed, stale, plain])
    modes: list[str] = []

    def check_project(
        project: ecosystem.Project,
        work: Path,
        candidate: ecosystem.Candidate,
        timeout: int,
        no_types: bool = False,
        mode: ecosystem.TowelMode = "relaxed",
    ) -> ecosystem.Result:
        modes.append(mode)
        if project.name == "reviewed" and mode == "relaxed":
            return ecosystem.Result(
                "reviewed",
                "RELAXED_ASSUMPTION",
                detail=f"object_lifetimes: {REVIEWED.reason}",
                relaxed_notice="Note: relaxed mode assumes ...",
                strict_check=ecosystem.StrictCheck(
                    "PASS", ecosystem.Phase(0, 0.0, "Applied", "r.log")
                ),
            )
        return ecosystem.Result(project.name, "PASS", towel_mode=mode)

    monkeypatch.setattr(ecosystem, "check_project", check_project)
    monkeypatch.setattr(ecosystem, "_lock_work_directory", lambda _: 0)
    monkeypatch.setattr(ecosystem, "prepare_candidate", lambda *_: CANDIDATE)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, stdout="", stderr=""),
    )
    monkeypatch.setattr(
        concurrent.futures,
        "ProcessPoolExecutor",
        lambda max_workers, initializer: ThreadPoolExecutor(max_workers=max_workers),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ecosystem_check.py",
            "--run-untrusted-code",
            "--work",
            str(tmp_path),
            *(["--strict"] if strict else []),
        ],
    )
    assert ecosystem.main() == 0
    mode = "strict" if strict else "relaxed"
    assert modes == [mode] * 3
    report = json.loads((tmp_path / "report/summary.json").read_text())
    summary = (tmp_path / "report/summary.md").read_text()
    assert report["towel_mode"] == mode and f"Towel mode: `{mode}`" in summary
    if strict:
        assert report["relaxed_assumptions"] == {} and report["relaxed_notices"] == {}
        # Strict mode makes none of relaxed mode's assumptions, so none is awaited.
        assert report["relaxed_assumptions_not_seen"] == {}
        return
    assert report["relaxed_assumptions"] == {"reviewed": f"object_lifetimes: {REVIEWED.reason}"}
    assert report["relaxed_assumptions_not_seen"] == {"stale": "PASS"}
    assert report["strict_checks"] == {"reviewed": "PASS"}
    assert report["relaxed_notices"] == {"reviewed": "Note: relaxed mode assumes ..."}
    assert "project for strict mode (1): reviewed (object_lifetimes" in summary
    assert "relaxed notice: yes" in summary
    assert "Relaxed-mode notice printed: 1 project(s): reviewed" in summary
