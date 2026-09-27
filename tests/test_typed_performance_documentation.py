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

"""Keep intermediate typed-performance claims attached to their completed experiments.

These tests never run a checker, benchmark today's checkout, or read the
external archive. Counts come from retained events/spans and completed CLI
logs, independently of comparison-summary assertions. Large omitted records
have retention-time hashes and reductions whose limits the evidence README
states explicitly. Historical expectations cannot be updated merely because
new engine behavior differs: retain a new comparison, or substantiate a
correction. Mutation tests demonstrate the false claims these checks prevent.
"""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
from functools import cache
import gzip
import hashlib
import json
from pathlib import Path
import re
from statistics import mean
import tomllib
from typing import Mapping

import pytest

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "tests/release_evidence/post-1772-performance"
PROPOSAL = "docs/proposals/next-release.md"
CHANGELOG = "CHANGELOG.md"
BASELINE = "35035a747e446368b6a5c4f3b1cee3fd1b5487c3"
CANDIDATE = "1b9719612ae9e844df8fbab2775c0646a0ab7ebf"
RUNS = ("packaging-baseline-1", "packaging-after-1", "packaging-after-2", "packaging-baseline-2")


def _table(value: object) -> dict[str, object]:
    assert isinstance(value, dict) and all(isinstance(key, str) for key in value)
    return {str(key): item for key, item in value.items()}


def _list(value: object) -> list[object]:
    assert isinstance(value, list)
    return list(value)


def _number(value: object) -> float:
    assert isinstance(value, (int, float)) and not isinstance(value, bool)
    return float(value)


def _text(name: str) -> str:
    return gzip.decompress((EVIDENCE / (name + ".gz")).read_bytes()).decode()


def _json(name: str) -> dict[str, object]:
    value: object = json.loads(_text(name))
    return _table(value)


def _rows(name: str) -> list[dict[str, object]]:
    return [_table(json.loads(line)) for line in _text(name).splitlines()]


@cache
def _provenance() -> dict[str, object]:
    value: object = json.loads((EVIDENCE / "provenance.json").read_text())
    return _table(value)


def _contains(text: str, claim: str) -> None:
    assert " ".join(claim.split()) in " ".join(
        text.split()
    ), f"Changed evidence-backed claim: {claim}"


def _one(pattern: str, text: str) -> str:
    found = re.findall(pattern, text, flags=re.MULTILINE)
    assert len(found) == 1, (pattern, found)
    result: object = found[0]
    assert isinstance(result, str)
    return result


@dataclass(frozen=True)
class PackagingRun:
    seconds: float
    applications: int
    signatures: int
    rejected: int
    failures: int


@cache
def _packaging() -> tuple[PackagingRun, ...]:
    """Count raw events and corroborate application counts with the independent CLI summary."""
    measured = []
    comparison = _table(_json("packaging-comparison.json")["runs"])
    for name in RUNS:
        events = _rows(name + "-events.jsonl")
        kinds = Counter(str(event["event"]) for event in events)
        assert set(kinds) == {"proposal", "signature", "materialized", "declined"}
        signatures = [event for event in events if event["event"] == "signature"]
        assert {event["outcome"] for event in signatures} <= {"Verified", "Rejection"}
        rejected = sum(event["outcome"] == "Rejection" for event in signatures)
        assert sum(event["outcome"] == "Verified" for event in signatures) == kinds["materialized"]
        assert all(
            bool(event["errors"]) == (event["outcome"] == "Rejection") for event in signatures
        )
        declines = [event for event in events if event["event"] == "declined"]
        assert len(declines) == 2 and len({str(event["identity"]) for event in declines}) == 1
        assert all("not verifiable" in str(event["reason"]) for event in declines)
        seconds = _number(_json(name + "-boundary.json")["elapsed"])
        times = [_number(event["elapsed"]) for event in events]
        assert times == sorted(times) and 0 <= times[0] <= times[-1] < seconds
        log = _text(name + "-run.log")
        applied = int(_one(r"^Applied (\d+) refactoring\(s\) across", log))
        assert applied == kinds["materialized"] == kinds["proposal"]
        _contains(log, "Termination: fixed_point")
        summary = _table(comparison[name])
        assert summary["counts"] == dict(kinds)
        assert summary["seconds"] == seconds and summary["rejected_signatures"] == rejected
        assert summary["distinct_declines"] == 1
        measured.append(
            PackagingRun(seconds, applied, kinds["signature"], rejected, kinds["failure"])
        )
    return tuple(measured)


@dataclass(frozen=True)
class SphinxRun:
    seconds: float
    exchanges: int
    files: int
    requests: int
    expressions: int
    answers: int


@cache
def _sphinx() -> tuple[SphinxRun, ...]:
    """Exchange counts come from spans, with the completed capture log as another witness."""
    result = []
    diagnostic_sources: list[dict[str, object]] = []
    retained = _table(_provenance()["omitted_sphinx_dumps"])
    for arm in ("before", "after"):
        spans = _rows(f"sphinx-{arm}-spans.jsonl")
        if arm == "before":
            sources = {str(span["file"]): span["source_sha256"] for span in spans}
            assert len(sources) == len(spans)
            assert all(span["outcome"] == "_PyrightDiagnostics" for span in spans)
            count = len(re.findall(r"^FILE \d+ ", _text(f"sphinx-{arm}.log"), re.MULTILINE))
        else:
            assert len(spans) == 1
            sources = _table(spans[0]["sources"])
            assert spans[0]["files"] == len(sources)
            assert spans[0]["outcomes"] == {"_PyrightDiagnostics": len(sources)}
            count = int(_one(r"^BATCH (\d+) 218 files ", _text(f"sphinx-{arm}.log")))
        assert count == len(spans)
        diagnostic_sources.append(sources)
        inputs, summary = _json(f"sphinx-{arm}-inputs.json"), _json(f"sphinx-{arm}-summary.json")
        omission = _table(retained[arm])
        for key in ("requests", "unique_requests", "files", "expressions"):
            assert inputs[key] == omission[key]
        assert inputs["requests"] == inputs["unique_requests"]
        assert omission["unique_expression_keys"] == inputs["expressions"]
        assert omission["unexpected_answers"] == 0 and omission["unanswered_files"] == {}
        assert len(_list(omission["missing_expression_keys"])) == 4
        seconds = _number(summary["reveal_seconds"])
        assert summary["diagnostic_calls"] == count
        assert summary["unique_diagnostic_inputs"] == inputs["files"] == len(sources)
        # Reveal includes bookkeeping outside diagnostics; total startup includes more phases.
        assert (
            sum(_number(span["seconds"]) for span in spans)
            < seconds
            < _number(summary["total_seconds"])
        )
        _contains(_text(f"sphinx-{arm}.log"), "INITIAL PRODUCTION REVEAL COMPLETE")
        assert "Applied " not in _text(f"sphinx-{arm}.log")
        result.append(
            SphinxRun(
                seconds,
                count,
                len(sources),
                int(_number(inputs["requests"])),
                int(_number(inputs["expressions"])),
                int(_number(omission["answers"])),
            )
        )
    assert diagnostic_sources[0] == diagnostic_sources[1]
    assert (
        retained["before"] == retained["after"]
    ), "Raw request/answer hashes and missing keys agree"
    return tuple(result)


def _check_documents(documents: Mapping[str, str]) -> None:
    report = (
        documents[PROPOSAL].split("## Typed performance comparisons\n", 1)[1].split("\n## ", 1)[0]
    )
    changes = documents[CHANGELOG].split("### Performance\n", 1)[1].split("\n## ", 1)[0]
    runs = _packaging()
    baseline, candidate = (runs[0], runs[3]), (runs[1], runs[2])
    _contains(report, f"Frozen baseline `{BASELINE[:7]}` and candidate `{CANDIDATE[:7]}`")
    _contains(
        report,
        "Both source commits are intermediate post-1.772 snapshots; later edits are outside these timings.",
    )
    _contains(report, "controlled experiments, not the final release corpus")
    _contains(
        changes,
        f"compare intermediate commits `{BASELINE[:7]}` and `{CANDIDATE[:7]}`; later changes are outside these measurements.",
    )
    _contains(
        report,
        "Packaging 26.3 used the actual CLI through a fixed point, strict mypy 2.3.1, Python 3.13.7 on macOS arm64, one worker, same-module extraction and no formatter.",
    )
    _contains(report, "The mypy configuration targeted Python 3.10.")
    _contains(
        report,
        "Four runs were sequential in baseline / candidate / candidate / baseline order, after the Sphinx profile finished.",
    )
    _contains(
        report,
        f"| Elapsed seconds, two runs | {baseline[0].seconds:.2f}, {baseline[1].seconds:.2f} | {candidate[0].seconds:.2f}, {candidate[1].seconds:.2f} |",
    )
    for label, attribute in (
        ("Applied extractions", "applications"),
        ("Signature attempts", "signatures"),
        ("Rejected signatures", "rejected"),
        ("Failed materializations", "failures"),
    ):
        assert getattr(baseline[0], attribute) == getattr(baseline[1], attribute)
        assert getattr(candidate[0], attribute) == getattr(candidate[1], attribute)
        _contains(
            report,
            f"| {label} | {getattr(baseline[0], attribute)} | {getattr(candidate[0], attribute)} |",
        )
    before, after = mean(run.seconds for run in baseline), mean(run.seconds for run in candidate)
    percent = (1 - after / before) * 100
    _contains(
        report,
        f"Mean time fell from {before:.2f} to {after:.2f} seconds, about {percent:.0f}% in this small sample.",
    )
    _contains(report, "The one fewer extraction is intentional")
    _contains(report, "One distinct checker-coverage refusal remains, attempted twice in each run.")
    _contains(
        report,
        "Each run completed its final cold type check; no packaging runtime suite was included in this timing experiment.",
    )
    _contains(report, "Output Python files are byte-identical within each arm.")
    _contains(changes, "rejected signatures fell from eight to zero")
    _contains(
        changes,
        f"mean elapsed time fell about {percent:.0f}%; the run intentionally applied {candidate[0].applications} extractions instead of {baseline[0].applications}.",
    )
    old, new = _sphinx()
    _contains(
        report,
        "actual production CLI startup with both strict checkers, stopping immediately after the first Pyright reveal",
    )
    _contains(
        report,
        "Both frozen wheels ran sequentially in the same Docker Linux environment: CPython 3.12.14, Sphinx 9.1.1, mypy 1.19.1 and Pyright 1.1.407.",
    )
    _contains(
        report,
        "enabled cross-module extraction, disabled formatting and network access, and used one Towel worker",
    )
    _contains(
        report,
        f"exactly {old.requests:,} distinct requests across {old.files} files, containing {old.expressions:,} expression probes, and returned the same {old.answers:,} types with no failed-file reports.",
    )
    assert old.expressions - old.answers == new.expressions - new.answers == 4
    _contains(
        report,
        "Four unanswered expression probes were identical in both runs; they are not checker failures.",
    )
    _contains(
        report,
        f"Per-file exchanges took {old.seconds:.2f} seconds; one project batch took {new.seconds:.2f} seconds.",
    )
    _contains(
        report, "This single comparison measures initial reveal time, not the full fixed point."
    )
    _contains(
        changes,
        f"Sphinx startup workload, {old.exchanges} exchanges became one, with identical requests and reported types; initial reveal time fell from {old.seconds:.1f} to {new.seconds:.2f} seconds.",
    )
    _contains(changes, "This is a phase measurement, not a full-run timing.")


def test_raw_records_and_provenance_are_unchanged() -> None:
    """An independently pinned manifest prevents changing both the evidence and the prose to pass."""
    assert (
        hashlib.sha256((EVIDENCE / "provenance.json").read_bytes()).hexdigest()
        == "3a343fcc20eb4dfe463baf85bb3e8fefb7a2bce910ee3af8fc3891c19b93ce63"
    )
    manifest = _provenance()
    assert manifest["baseline_commit"] == BASELINE and manifest["candidate_commit"] == CANDIDATE
    raw = _table(manifest["retained_raw"])
    assert {path.name for path in EVIDENCE.glob("*.gz")} == raw.keys()
    for name, entry in raw.items():
        item = _table(entry)
        contents = gzip.decompress((EVIDENCE / name).read_bytes())
        assert hashlib.sha256(contents).hexdigest() == item["sha256"], name
        assert len(contents) == item["bytes"], name
    assert _text("after-commit.txt").strip() == CANDIDATE
    for arm, commit in (("before", BASELINE), ("after", CANDIDATE)):
        source = _table(_table(manifest["source_identity"])[arm])
        imported = _json(f"packaging-{arm}-source.json")
        assert source["commit"] == commit
        assert f"snapshot-{arm}/src/towel/__init__.py" in str(imported["towel"])
        assert imported["annotation_wiring_sha256"] == source["materialize_sha256"]
        assert (
            hashlib.sha256(_text(f"{arm}-fixed_point.py").encode()).hexdigest()
            == source["fixed_point_sha256"]
        )


def _argv_constants(source: str) -> list[str]:
    commands = [
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Assign)
        and any(ast.unparse(target) == "sys.argv" for target in node.targets)
    ]
    assert len(commands) == 1 and isinstance(commands[0], ast.List)
    return [
        str(node.value)
        for node in commands[0].elts
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def test_recorded_flags_environment_and_phase_boundaries() -> None:
    """Preserve the checking target, enabled features, and deliberate early Sphinx stop."""
    config = _table(tomllib.loads(_text("packaging-config.toml")))
    assert _table(config["project"])["version"] == "26.3"
    assert _table(config["tool"])["mypy"] == {
        "strict": True,
        "files": ["packaging"],
        "python_version": "3.10",
    }
    assert _argv_constants(_text("packaging-measure.py")) == [
        "towel",
        "dry",
        "--no-interactive",
        "--no-format",
        "--progress",
        "detail",
    ]
    log = _text("packaging-comparison.log")
    assert re.findall(r"^START (\S+)$", log, re.MULTILINE) == list(RUNS)
    assert re.findall(r"^END (\S+) status 0 seconds", log, re.MULTILINE) == list(RUNS)
    operator = _text("operator-record.md")
    _contains(operator, "macOS 26.5.1 arm64, mypy 2.3.1 strict mode, one worker")
    _contains(operator, "network disabled and one Towel worker")
    for arm in ("before", "after"):
        assert str(_json(f"packaging-{arm}-source.json")["python"]).startswith("3.13.7 ")
        inputs = _json(f"sphinx-{arm}-inputs.json")
        assert str(inputs["python"]).startswith("3.12.14 ")
        assert inputs["tools"] == {
            "code-towel": "1.772",
            "mypy": "1.19.1",
            "pyright": "1.1.407",
            "Sphinx": "9.1.1",
        }
        profile = _text(f"sphinx-{arm}-profile.py")
        assert _argv_constants(profile)[4:] == [
            "--cross-module",
            "--no-interactive",
            "--no-format",
            "--progress",
            "detail",
        ]
        reveal = next(
            node
            for node in ast.parse(profile).body
            if isinstance(node, ast.FunctionDef) and node.name == "reveal"
        )
        assert (
            isinstance(reveal.body[-1], ast.Raise)
            and ast.unparse(reveal.body[-1]) == "raise Captured"
        )
        driver = next(
            node
            for node in ast.walk(ast.parse(_text(f"{arm}-fixed_point.py")))
            if isinstance(node, ast.FunctionDef) and node.name == "_refactor_directory_in_place"
        )
        calls = [
            node.func.attr
            for node in ast.walk(driver)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        ]
        assert "confirm_run_with_a_cold_checker" in calls and "_apply_until_fixed_point" in calls
        assert isinstance(driver.body[-1], ast.Return)


def test_completed_aggregates_and_within_arm_output_identity() -> None:
    """The summaries corroborate raw counts; equal output hashes apply within each arm only."""
    assert [
        (run.applications, run.signatures, run.rejected, run.failures) for run in _packaging()
    ] == [(19, 27, 8, 0), (18, 18, 0, 0), (18, 18, 0, 0), (19, 27, 8, 0)]
    outputs = _table(_provenance()["packaging_output_sha256"])
    comparison = _table(_json("packaging-comparison.json")["runs"])
    for run in RUNS:
        assert len(_table(outputs[run])) == 22
        assert outputs[run] == _table(comparison[run])["outputs"]
    assert outputs[RUNS[0]] == outputs[RUNS[3]] != outputs[RUNS[1]] == outputs[RUNS[2]]
    assert [
        (run.exchanges, run.files, run.requests, run.expressions, run.answers) for run in _sphinx()
    ] == [(218, 218, 2949, 7391, 7387), (1, 218, 2949, 7391, 7387)]


def test_documented_typed_comparisons_match_completed_evidence() -> None:
    _check_documents({path: (ROOT / path).read_text() for path in (PROPOSAL, CHANGELOG)})


@pytest.mark.parametrize(
    "path, old, new",
    [
        (PROPOSAL, "baseline `35035a7`", "baseline `v1.772`"),
        (PROPOSAL, "candidate `1b97196`", "candidate `HEAD`"),
        (
            PROPOSAL,
            "later edits are\noutside these timings",
            "later edits are included in these timings",
        ),
        (PROPOSAL, "| 34.18, 34.57 |", "| 34.51, 34.79 |"),
        (PROPOSAL, "| Signature attempts | 27 | 18 |", "| Signature attempts | 19 | 18 |"),
        (PROPOSAL, "Python 3.13.7 on macOS arm64", "Python 3.13.7 on Linux x86_64"),
        (PROPOSAL, "targeted Python 3.10", "targeted Python 3.13"),
        (
            PROPOSAL,
            "same-module extraction and no\nformatter",
            "cross-module extraction and formatting",
        ),
        (
            PROPOSAL,
            "no packaging\nruntime suite was included",
            "the packaging runtime suite was included",
        ),
        (PROPOSAL, "within each arm", "across both arms"),
        (
            PROPOSAL,
            "stopping immediately after the first Pyright reveal",
            "running through the complete fixed point",
        ),
        (PROPOSAL, "7,387 types", "7,391 types"),
        (PROPOSAL, "they are not\nchecker failures", "they are checker failures"),
        (CHANGELOG, "980.7 to 8.65 seconds", "980.7 to 8.65 milliseconds"),
        (CHANGELOG, "phase measurement, not a full-run timing", "full-run speedup"),
        (CHANGELOG, "18 extractions instead of 19", "all 19 extractions"),
    ],
)
def test_plausible_false_claims_are_rejected(path: str, old: str, new: str) -> None:
    """Changing the right-looking number, arm, flags or scope must not silently pass."""
    documents = {name: (ROOT / name).read_text() for name in (PROPOSAL, CHANGELOG)}
    heading = "## Typed performance comparisons\n" if path == PROPOSAL else "### Performance\n"
    prefix, remainder = documents[path].split(heading, 1)
    section, separator, suffix = remainder.partition("\n## ")
    pattern = r"\s+".join(re.escape(word) for word in old.split())
    assert re.search(pattern, section), "Keep the mutation live when prose changes"
    changed = re.sub(pattern, lambda _: new, section, count=1)
    documents[path] = prefix + heading + changed + separator + suffix
    with pytest.raises(AssertionError):
        _check_documents(documents)


def test_rewrapping_prose_preserves_evidence() -> None:
    """Formatting changes may move line breaks without changing a measured claim."""
    documents = {
        path: "".join(
            line if line.startswith("#") else line.replace(" ", "\n")
            for line in (ROOT / path).read_text().splitlines(keepends=True)
        )
        for path in (PROPOSAL, CHANGELOG)
    }
    _check_documents(documents)
