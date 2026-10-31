# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by law or agreed in writing, software distributed under the
# License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS
# OF ANY KIND, either express or implied. See the License for the specific
# language governing permissions and limitations under the License.

"""Preserve what the published-versus-candidate experiment actually measured.

The full Packaging command and first Sphinx reveal have different boundaries.
These tests intentionally reject a phase speedup promoted to a whole-corpus
claim. They freeze historical observations, not an expectation that every new
version runs faster. New experiments need separately identified records.
"""

from __future__ import annotations

from datetime import datetime
from functools import cache
import gzip
import hashlib
import json
from pathlib import Path
import re

import pytest

from tests.test_next_validation_documentation import _contains, _number, _rows, _table

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "tests/release_evidence/post-1772-published-comparison"
DOCUMENT = ROOT / "docs/proposals/published-1772-comparison.md"
MANIFEST_SHA256 = "75589c7960dbc805fc39ccee061a871b1e78b442228552aa77b4d2e2ba3032cb"
ORDER = ["published", "candidate", "candidate", "published"]


@cache
def _bytes(name: str) -> bytes:
    return gzip.decompress((EVIDENCE / (name.replace("/", "--") + ".gz")).read_bytes())


def _value(name: str) -> object:
    return json.loads(_bytes(name))


def _json(name: str) -> dict[str, object]:
    return _table(_value(name))


def test_published_comparison_retains_its_exact_original_observations() -> None:
    manifest = (EVIDENCE / "provenance.json").read_bytes()
    assert hashlib.sha256(manifest).hexdigest() == MANIFEST_SHA256
    records = _table(_table(json.loads(manifest))["retained_raw"])
    assert set(records) == {path.name for path in EVIDENCE.glob("*.gz")}
    for name, item in records.items():
        row = _table(item)
        data = gzip.decompress((EVIDENCE / name).read_bytes())
        assert len(data) == row["bytes"]
        assert hashlib.sha256(data).hexdigest() == row["sha256"]


def test_comparison_started_after_validation_and_ran_sequentially() -> None:
    """A timing measured alongside heavy gates cannot become our quiet comparison."""
    assert _json("prerequisites/continuation-result.json")["status"] == "mechanical_gates_complete"
    ready = datetime.fromisoformat(str(_json("ready-for-timing.json")["created"]))
    corpus = _json("prerequisites/corpus-exit.json")
    assert corpus["oom_killed"] is False
    assert datetime.fromisoformat(str(corpus["finished"])) <= ready
    for version in ("3.11", "3.12", "3.13"):
        result = _json(f"prerequisites/matrix-{version}-result.json")
        assert result["exit_status"] == 0
        assert datetime.fromisoformat(str(result["finished"])) <= ready
    checks = _rows(_json("prerequisites/prebenchmark-checks.json")["checks"])
    assert checks
    for check in checks:
        assert check["exit_status"] == 0
        assert datetime.fromisoformat(str(check["finished"])) <= ready
    previous = ready
    for row in _rows(_value("packaging-results.json")):
        start = datetime.fromisoformat(str(row["started"]))
        finish = datetime.fromisoformat(str(row["finished"]))
        assert previous <= start < finish
        previous = finish
    for index in range(1, 5):
        row = _json(f"sphinx-{index}-command.json")
        start = datetime.fromisoformat(str(row["started"]))
        finish = datetime.fromisoformat(str(row["finished"]))
        assert row["exit_status"] == 0 and previous <= start < finish
        previous = finish


def _means(name: str, field: str) -> dict[str, float]:
    rows = _rows(_value(name))
    assert [row["arm"] for row in rows] == ORDER
    return {
        arm: sum(_number(row[field]) for row in rows if row["arm"] == arm) / 2
        for arm in ("published", "candidate")
    }


def _check_claims(text: str) -> None:
    assert _json("benchmark-result.json")["status"] == "completed"
    identity = _json("wheel-source-identity.json")
    for arm, commit in (
        ("published", "088a4c7c7992aa1ff59d90cc88e083ccf157b82d"),
        ("candidate", "3af8c4bfe8acd56c204f5e31c7cf80f4f028b654"),
    ):
        row = _table(identity[arm])
        assert row["commit"] == commit
        _contains(text, f"`{commit}`")
        _contains(text, f"`{row['wheel_sha256']}`")
    published = _json("published-provenance.json")
    assert published["verified_sha256"] == _table(identity["published"])["wheel_sha256"]
    assert _table(_json("published-environment.json")["versions"]) == _table(
        _json("candidate-environment.json")["versions"]
    )
    assert (
        _json("published-environment.json")["python"]
        == _json("candidate-environment.json")["python"]
    )
    for claim in (
        "The baseline is the published PyPI wheel, not a rebuilt tag",
        "Both artifacts report version 1.772; their hashes distinguish them",
        "published, candidate, candidate, published",
        "Two observations per version do not establish a statistical confidence interval",
        "No whole-corpus speedup is established",
        "Packaging: whole unmodified CLI subprocess",
        "Sphinx: first production Pyright reveal only",
        "Sphinx extraction and runtime tests are outside this timing",
        "Unanswered expression probes are not counted as inferred types",
        "Fresh input for each run; one Towel worker",
        "The benchmark begins after the corpus and serial matrix have completed",
        "This was not a dedicated idle machine",
        "This does not establish a meaningful whole-command speedup over published 1.772",
        "The earlier approximately 16% improvement compared intermediate commits",
    ):
        _contains(text, claim)
    background = _rows(_json("idle-preflight.json")["top_cpu"])
    mail = next(row for row in background if str(row["command"]).endswith("/MacOS/Mail"))
    vm = next(
        row
        for row in background
        if str(row["command"]).endswith("/MacOS/com.apple.Virtualization.VirtualMachine")
    )
    _contains(text, f"Mail using about {_number(mail['cpu_percent']):.0f}% of one CPU")
    _contains(text, f"Docker VM process about {_number(vm['cpu_percent']):.0f}% of one CPU")
    for label, records, field, comparison in (
        ("Packaging", "packaging-results.json", "seconds", "packaging-comparison.json"),
        ("Sphinx reveal", "sphinx-results.json", "reveal_seconds", "sphinx-comparison.json"),
    ):
        means = _means(records, field)
        saved = _json(comparison)
        assert _table(saved["means_seconds"]) == means
        reduction = 100 * (1 - means["candidate"] / means["published"])
        assert _number(saved["percent_less_time"]) == pytest.approx(reduction)
        _contains(
            text,
            f"| {label} | {means['published']:.3f} | {means['candidate']:.3f} | {reduction:.2f}% |",
        )
    rows = _rows(_value("packaging-results.json"))
    inputs = _table(_json("input-provenance.json")["files"])
    for row in rows:
        assert row["exit_status"] == 0 and row["input_sha256"] == inputs
        assert _table(row["environment"])["TOWEL_WORKERS"] == "1"
        directory = f"packaging-{row['run']}-{row['arm']}"
        assert (
            _json(directory + "/runtime-identity.json")
            == _table(identity[str(row["arm"])])["runtime_sha256"]
        )
        assert (
            _json(directory + "/imported-source.json")["versions"]
            == _json(str(row["arm"]) + "-environment.json")["versions"]
        )
        _contains(
            text,
            f"| {row['run']} | {row['arm']} | {_number(row['seconds']):.3f} | {row['applied_extractions']} | {row['changed_files']} |",
        )
    for arm in ("published", "candidate"):
        selected = [row for row in rows if row["arm"] == arm]
        assert selected[0]["output_python_sha256"] == selected[1]["output_python_sha256"]
        expected = (18, 8) if arm == "published" else (19, 9)
        assert {(row["applied_extractions"], row["changed_files"]) for row in selected} == {
            expected
        }
    published_times = [_number(row["seconds"]) for row in rows if row["arm"] == "published"]
    candidate_times = [_number(row["seconds"]) for row in rows if row["arm"] == "candidate"]
    assert max(min(published_times), min(candidate_times)) <= min(
        max(published_times), max(candidate_times)
    )
    _contains(
        text,
        f"a {_number(_json('packaging-comparison.json')['percent_less_time']):.2f}% difference in means, with overlapping run times",
    )
    rows = _rows(_value("sphinx-results.json"))
    assert len({row["request_sha256"] for row in rows}) == 1
    assert len({row["request_fingerprints_sha256"] for row in rows}) == 1
    assert len({row["answer_sha256"] for row in rows}) == 1
    for row in rows:
        name = f"sphinx-{row['run']}-{row['arm']}"
        fingerprints = _bytes(name + "/request-fingerprints.json")
        assert hashlib.sha256(fingerprints).hexdigest() == row["request_fingerprints_sha256"]
        requests = _rows(json.loads(fingerprints))
        for request in requests:
            assert re.fullmatch(r"[0-9a-f]{64}", str(_table(request["source"])["sha256"]))
        environment = _json(name + "/environment.json")
        _contains(
            text,
            f"{int(_number(environment['requests'])):,} requests across {environment['files']} files; {int(_number(environment['expressions'])):,} expression probes and {int(_number(row['answers'])):,} reported types",
        )
        answer = _bytes(name + "/answers.json")
        assert hashlib.sha256(answer).hexdigest() == row["answer_sha256"]
        values = _table(json.loads(answer))
        answers = values["answers"]
        assert isinstance(answers, list) and len(answers) == int(_number(row["answers"]))
        assert values["unanswered"] == row["failed_files"] == {}
        _contains(
            text,
            f"| {row['run']} | {row['arm']} | {_number(row['reveal_seconds']):.3f} | {row['answers']} |",
        )
    state = _json("sphinx-container-final.json")
    assert _table(state["State"])["ExitCode"] == 0
    assert _table(state["State"])["OOMKilled"] is False
    assert _table(state["HostConfig"])["NetworkMode"] == "none"
    _contains(
        text, "Identical complete Sphinx requests and answers were verified across all four runs"
    )


def test_documented_published_comparison_matches_raw_results() -> None:
    _check_claims(DOCUMENT.read_text())


@pytest.mark.parametrize(
    "before,after",
    [
        ("first production Pyright reveal only", "complete fixed-point extraction"),
        ("No whole-corpus speedup is established", "A whole-corpus speedup is established"),
        ("outside this timing", "included in this timing"),
        ("published PyPI wheel, not a rebuilt tag", "rebuilt tag, not the published wheel"),
        ("one Towel worker", "all available Towel workers"),
        (
            "do not establish a statistical confidence interval",
            "establish a statistical confidence interval",
        ),
        ("This was not a dedicated idle machine", "This was a dedicated idle machine"),
        (
            "does not establish a meaningful whole-command speedup",
            "establishes a meaningful whole-command speedup",
        ),
    ],
)
def test_phase_and_sample_claims_cannot_be_promoted_to_broader_evidence(
    before: str, after: str
) -> None:
    text = DOCUMENT.read_text()
    pattern = r"\s+".join(re.escape(word) for word in before.split())
    assert re.search(pattern, text)
    with pytest.raises(AssertionError):
        _check_claims(re.sub(pattern, lambda _: after, text))


def test_layout_changes_do_not_change_the_measured_claims() -> None:
    _check_claims("\n".join(DOCUMENT.read_text().split()))
