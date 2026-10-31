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

"""Keep completed corrected-candidate claims tied to immutable measurements.

These assertions protect historical facts, not expectations of future engine
output. New runs need separately identified evidence. A failed source gate,
untyped fallback, baseline failure, or resource kill must never become a green
release claim through a documentation edit. Mutation cases exercise those exact
mistakes. Nothing here executes retained code or depends on today's machine.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from functools import cache
import gzip
import hashlib
import json
from pathlib import Path
import re
import tomllib
from xml.etree import ElementTree

import pytest

from tests.test_next_validation_documentation import (
    _contains,
    _number,
    _one,
    _rows,
    _strings,
    _suite,
    _table,
)

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "tests/release_evidence/post-1772-corrected-validation"
DOCUMENT = ROOT / "docs/proposals/next-validation-final.md"
COMMIT = "3af8c4bfe8acd56c204f5e31c7cf80f4f028b654"
RUNTIME = "78c8c6ea954f370a1afa8846a41988856589acc5"
TESTS = "63c2bb4b75e17dbe12947d83dc9219346eecd6af"
PROVENANCE_SHA256 = "948666f9bc8dd5dad115068cdf2cde0a34f04c1c18f416901ed5621653531148"
VERSIONS = ("3.11", "3.12", "3.13")


@cache
def _text(name: str) -> str:
    return gzip.decompress((EVIDENCE / (name.replace("/", "--") + ".gz")).read_bytes()).decode()


def _value(name: str) -> object:
    return json.loads(_text(name))


def _json(name: str) -> dict[str, object]:
    return _table(_value(name))


def _corpus_rows() -> list[dict[str, object]]:
    return _rows(_json("corpus-report/summary.json")["results"])


def test_completed_evidence_inventory_and_each_original_byte_are_pinned() -> None:
    raw = (EVIDENCE / "provenance.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == PROVENANCE_SHA256
    manifest = _table(json.loads(raw))
    assert (manifest["frozen_commit"], manifest["runtime_tree"], manifest["tests_tree"]) == (
        COMMIT,
        RUNTIME,
        TESTS,
    )
    records = _table(manifest["retained_raw"])
    assert {path.name for path in EVIDENCE.glob("*.gz")} == set(records)
    for name, item in records.items():
        record = _table(item)
        data = gzip.decompress((EVIDENCE / name).read_bytes())
        assert len(data) == record["bytes"]
        assert hashlib.sha256(data).hexdigest() == record["sha256"]
        assert name == str(record["source"]).replace("/", "--") + ".gz"
    assert len(_table(_json("source.json")["runtime_sha256"])) == 96


def test_every_corpus_project_and_phase_has_its_actual_record_and_log() -> None:
    """A summary alone cannot hide missing projects, fallback logs or killed phases."""
    summary = _json("corpus-report/summary.json")
    rows = _corpus_rows()
    projects = _rows(tomllib.loads(_text("candidate/scripts/ecosystem/manifest.toml"))["project"])
    names = {str(row["name"]) for row in rows}
    assert len(rows) == len(names) == len(projects) == 141
    assert names == {str(project["name"]) for project in projects}
    assert summary["towel"] == COMMIT and summary["typing_mode"] == "default"
    assert summary["counts"] == dict(Counter(str(row["verdict"]) for row in rows))
    console = re.findall(r"^([A-Z_]+)\s+([\w.-]+)\s+files=", _text("corpus.log"), re.MULTILINE)
    assert len(console) == len(set(name for _, name in console)) == 141
    assert {name: verdict for verdict, name in console} == {
        str(row["name"]): row["verdict"] for row in rows
    }
    for row in rows:
        assert _json(f"corpus-report/{row['name']}.json") == row
        for phase in ("baseline", "refactor", "after"):
            if row[phase] is None:
                continue
            step = _table(row[phase])
            path = Path(str(step["log"]))
            assert path.parent == Path("/work/logs")
            raw = _text("corpus-logs/" + path.name).encode()
            retained = _table(
                _table(_json("corpus-reconciliation.json")["raw_phase_logs"])[
                    "corpus-logs/" + path.name
                ]
            )
            assert len(raw) == retained["bytes"]
            assert hashlib.sha256(raw).hexdigest() == retained["sha256"]
            assert _number(step["seconds"]) >= 0
        assert _table(row["baseline"])["returncode"] in (0, 1)
    outcome = _json("corpus-exit.json")
    state = _json("corpus-container-state.json")
    assert outcome["oom_killed"] is False and state["OOMKilled"] is False
    assert state["Running"] is False and state["ExitCode"] == outcome["status"]
    assert _json("corpus-command.json")["project_workers"] == 1
    assessment = _json("corpus-assessment.json")
    reconciliation = _json("corpus-reconciliation.json")
    for reviewed, actual in (
        ("reviewed_nonaccepted", "nonaccepted"),
        ("reviewed_fallbacks", "fallbacks"),
        ("reviewed_known_failures", "known_failures"),
        ("reviewed_baseline_failures", "baseline_nonzero"),
    ):
        assert set(_table(assessment[reviewed])) == set(_table(reconciliation[actual]))


def test_prior_oom_and_completed_nonzero_serial_baseline_are_kept_distinct() -> None:
    """Completion fixes the OOM evidence gap; it never converts failures into passes.

    The current corpus runs the published project's tests against the new tool.
    Its offline fixture builds and old annotation-spelling assertions fail before
    extraction. Preserve those facts and the separate current-source matrix.
    """
    previous = _json("predecessor-oom/result.json")
    resources = _json("predecessor-oom/resource-failure.json")
    assert previous["verdict"] == "BASELINE_ERROR"
    assert _table(previous["baseline"])["returncode"] == -9
    assert previous["refactor"] is None and previous["after"] is None
    assert _table(resources["container_state"])["OOMKilled"] is True
    cgroup = _table(resources["cgroup"])
    assert int(str(cgroup["memory.peak"]).strip()) == 7385477120
    assert "oom_kill 1" in str(cgroup["memory.events"])
    current = _json("corpus-report/towel-main.json")
    assert current["commit"] == previous["commit"]
    assert _table(current["baseline"])["returncode"] == 1
    assert _table(current["baseline"])["summary"] == "18 failed, 7771 passed, 38 skipped"
    assert current["after"] is None and current["changed_files"] == 0
    review = _json("corpus-review/towel-main/assessment.json")
    assert review["baseline_completed"] is True and review["oom_killed"] is False
    failures = _rows(review["failures"])
    assert Counter(row["category"] for row in failures) == {
        "offline-build-dependency": 16,
        "old-annotation-spelling": 2,
    }
    assert len(_strings(review["failed_test_ids"])) == len(failures) == 18
    assert _json("corpus-exit.json")["oom_killed"] is False
    text = DOCUMENT.read_text()
    for claim in (
        "The earlier two-project run suffered an out-of-memory kill",
        "Its Towel baseline exited -9 before extraction",
        "Observed cgroup lifetime peak: 7385477120 bytes",
        "This is not a baseline-only peak or a minimum memory requirement",
        "The corrected one-project run completed that baseline without an OOM kill",
    ):
        _contains(text, claim)


def _check_document(text: str) -> None:
    for value in (COMMIT, RUNTIME, TESTS):
        _contains(text, f"`{value}`")
    source = _json("source.json")
    assert source["host_memory_bytes"] == 137438953472
    assert _table(source["docker_vm"])["memory_bytes"] == 8215732224
    for claim in (
        "macOS 26.5.1 arm64, Apple M5 Max, 18 cores, 128 GiB",
        "Docker VM: 18 CPUs and 8215732224 bytes",
        "Linux arm64",
        "No Windows or Linux x86_64 validation",
        "These are observed environments, not minimum resource requirements",
        "Validation elapsed times are not controlled performance comparisons",
        "Artifacts still carry version 1.772 and must not be uploaded",
        "Later documentation and evidence tests are separate from these frozen suite counts",
        "one project at a time",
        "Known refusals remain CRASH",
        "Untyped fallbacks do not establish typed extraction coverage",
        "Matching pre-existing test failures do not make an upstream suite green",
        "Towel-main baseline: 18 failed, 7771 passed, 38 skipped",
        "16 offline fixture-build dependency failures and two historical annotation-spelling assertions",
        "This is a completed nonzero baseline, not a green Towel suite",
        "Neither is a count of checker invocations or all construction attempts",
        "Unchanged and inconclusive fuzz cases are not demonstrated transformed equivalence",
    ):
        _contains(text, claim)
    for version in VERSIONS:
        result = _json(f"matrix-{version}-result.json")
        assert result["exit_status"] == 0 and result["commit"] == COMMIT
        assert result["runtime_tree"] == RUNTIME
        log = _text(f"matrix-{version}.log")
        assert _table(json.loads(log.splitlines()[-1])) == result
        suite = _suite(log)
        wall = (
            datetime.fromisoformat(str(result["finished"]))
            - datetime.fromisoformat(str(result["started"]))
        ).total_seconds()
        coverage = ElementTree.fromstring(_text(f"coverage-{version}.xml"))
        lines = coverage.findall("./packages/package/classes/class/lines/line")
        covered = sum(int(line.attrib["hits"]) > 0 for line in lines)
        assert len(lines) == int(coverage.attrib["lines-valid"])
        assert covered == int(coverage.attrib["lines-covered"])
        assert covered / len(lines) >= 0.85
        assert coverage.attrib["branches-valid"] == "0"
        _contains(
            text,
            f"| {str(result['python']).split()[0]} | {suite.passed:,} | {suite.skipped} | {suite.subtests} | {suite.seconds:.2f} | {wall:,.0f} | {covered:,} / {len(lines):,} | {100 * covered / len(lines):.2f}% |",
        )
        _contains(log, f"/work/py{version}/src/towel/__init__.py")
        launch = _json(f"matrix-{version}-launch.json")
        args = _strings(launch["args"])
        assert args[args.index("--network") + 1] == "none"
    _contains(text, "Coverage is line coverage; the gate was 85%")
    _contains(text, "Pytest seconds and whole-job wall seconds have different boundaries")
    self_result = _json("selfdogfood/results.json")
    assert self_result["input_commit"] == COMMIT
    steps = {str(row["name"]): row for row in _rows(self_result["results"])}
    assert set(steps) == {"sync", "imported-source", "typecheck", "tests"}
    assert all(row["exit_status"] == 0 for row in steps.values())
    suite = _suite(_text("selfdogfood/tests.log"))
    _contains(
        text,
        f"{suite.passed:,} passed, {suite.skipped} skipped, {suite.subtests} subtests in {suite.seconds:.2f} pytest seconds",
    )
    _contains(text, f"Test subprocess: {_number(steps['tests']['seconds']):.3f} seconds")
    _contains(_text("selfdogfood.log"), "Applied 31 refactoring(s) across 24 file(s)")
    _contains(text, "31 extractions across 24 files")
    assert (
        _json("selfdogfood/post-test-integrity.json")[
            "transformed_runtime_files_unchanged_by_test_run"
        ]
        == 97
    )
    _contains(text, "All 97 transformed runtime files remained unchanged during testing")
    for family in ("grammar", "scope"):
        log = _text(f"fuzz-{family}.log")
        assert _one(r"^Done in \d+s: (\d+) failure\(s\)\.$", log) == "0"
        values = {
            name: int(value)
            for name, value in re.findall(
                r"(equivalent|unchanged|inconclusive) (\d+)", log.splitlines()[-2]
            )
        }
        seconds = _one(r"^Done in (\d+)s: 0 failure\(s\)\.$", log)
        _contains(
            text,
            f"| {family} | {sum(values.values()):,} | {values['equivalent']:,} | {values['unchanged']:,} | {values.get('inconclusive', 0)} | {seconds} |",
        )
    corpus = _json("corpus-reconciliation.json")
    _contains(
        text, f"141 projects; driver elapsed {_number(corpus['driver_wall_seconds']):,.1f} seconds"
    )
    for verdict, count in sorted(_table(corpus["counts"]).items()):
        _contains(text, f"| {verdict} | {int(_number(count))} |")
    _contains(text, f"{int(_number(corpus['changed_files'])):,} changed files")
    for name in _table(corpus["nonaccepted"]):
        _contains(text, f"`{name}`")
    for name in _table(corpus["fallbacks"]):
        _contains(text, f"`{name}`")
    for name, artifact in _table(_json("artifacts.json")["artifacts"]).items():
        row = _table(artifact)
        _contains(
            text, f"| `{Path(name).name}` | {int(_number(row['bytes'])):,} | `{row['sha256']}` |"
        )
    smokes = _rows(_value("wheel-smoke/results.json"))
    assert len(smokes) == 6 and all(row["status"] == "passed" for row in smokes)
    _contains(
        text,
        "Six fresh wheel environments passed: bare and format/types extras on CPython 3.11.14, 3.12.12 and 3.13.7",
    )
    _contains(text, "The extras fixture configured strict mypy, not both installed checkers")
    _contains(_text("dependencies-dev-audit.log"), "No known vulnerabilities found")
    _contains(text, "The retained dependency pins had no known vulnerabilities at audit time")
    assert _text("diagrams-writable-cache.log").strip() == "5 diagram(s) checked, 0 broken"
    _contains(text, "5 diagrams, 0 broken")
    reasons = {}
    for name in ("sphinx", "tornado"):
        log = _text(f"corpus-logs/{name}-refactor.log")
        matches = re.findall(r"^  (\d+) proposal\(s\) not applied: (.+)$", log, re.MULTILINE)
        assert len(matches) == 1
        total, summary = matches[0]
        entries = dict(
            (reason, int(count))
            for reason, count in re.findall(r"(?:^|, )(.+?) (\d+)(?=, |$)", summary)
        )
        assert sum(entries.values()) == int(total)
        reasons[name] = entries
    for reason in reasons["sphinx"]:
        _contains(
            text,
            f"| {reason} | {reasons['sphinx'][reason]} | {reasons['tornado'].get(reason, 0)} |",
        )
    assert set(reasons["tornado"]) <= set(reasons["sphinx"])
    _contains(
        text,
        f"| Total distinct built proposals not applied | {sum(reasons['sphinx'].values())} | {sum(reasons['tornado'].values())} |",
    )
    for name, applied in (("sphinx", 318), ("tornado", 160)):
        assert _one(
            r"Applied (\d+) refactoring\(s\) across", _text(f"corpus-logs/{name}-refactor.log")
        ) == str(applied)
        _contains(text, f"{name.capitalize()} applied {applied}")


def test_completed_validation_document_matches_raw_measurements() -> None:
    _check_document(DOCUMENT.read_text())


@pytest.mark.parametrize(
    "before,after",
    [
        (COMMIT, "HEAD"),
        ("128 GiB", "8 GiB"),
        ("8215732224", "137438953472"),
        ("No Windows or Linux x86_64 validation", "Windows and Linux x86_64 validation"),
        ("not minimum resource requirements", "minimum resource requirements"),
        ("not controlled performance comparisons", "controlled performance comparisons"),
        ("must not be uploaded", "are ready to upload"),
        ("one project at a time", "two projects at a time"),
        ("Known refusals remain CRASH", "Known refusals count as PASS"),
        (
            "Untyped fallbacks do not establish typed extraction coverage",
            "Untyped fallbacks establish typed extraction coverage",
        ),
        ("Coverage is line coverage", "Coverage is branch coverage"),
        ("not both installed checkers", "both installed checkers"),
        ("at audit time", "forever"),
        ("18 failed, 7771 passed, 38 skipped", "7789 passed, 38 skipped"),
        ("not a green Towel suite", "a green Towel suite"),
        (
            "Neither is a count of checker invocations or all construction attempts",
            "These count every checker invocation and construction attempt",
        ),
    ],
)
def test_plausible_release_claim_overstatements_are_rejected(before: str, after: str) -> None:
    """Do not relax a factual assertion merely because a later release behaves differently."""
    text = DOCUMENT.read_text()
    pattern = r"\s+".join(re.escape(word) for word in before.split())
    assert re.search(pattern, text)
    with pytest.raises(AssertionError):
        _check_document(re.sub(pattern, lambda _: after, text))


def test_rewrapping_document_does_not_change_its_claims() -> None:
    _check_document("\n".join(DOCUMENT.read_text().split()))
