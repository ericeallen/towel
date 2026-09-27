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

"""Bind frozen non-corpus validation claims to completed raw evidence, offline.

These historical expectations must not be changed to match newer engine output.
A new run needs new evidence; a correction needs the original records. Assertions
reconcile independent logs/results and counts rather than trusting summary prose.
Mutations exercise the instrument, while whitespace rewrapping must remain valid.
No check executes retained scripts, consults the external archive, or reads live
host characteristics. Corpus assessment is deliberately owned separately.
"""

from __future__ import annotations

from dataclasses import dataclass
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

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "tests/release_evidence/post-1772-validation"
DOCUMENT = ROOT / "docs/proposals/next-validation.md"
COMMIT = "3f09421f2139508c5c9549b6791eca43f30c5c41"
RUNTIME = "747008c225808e48bfaf97455e1d808e8800aa59"
TESTS = "829ef595f3249c06d92685ff702ed536e6b6c3a2"
VERSIONS = ("3.11", "3.12", "3.13")


def _table(value: object) -> dict[str, object]:
    assert isinstance(value, dict) and all(isinstance(key, str) for key in value)
    return {str(key): item for key, item in value.items()}


def _rows(value: object) -> list[dict[str, object]]:
    assert isinstance(value, list)
    return [_table(item) for item in value]


def _strings(value: object) -> list[str]:
    assert isinstance(value, list) and all(isinstance(item, str) for item in value)
    return [str(item) for item in value]


def _number(value: object) -> float:
    assert isinstance(value, (int, float)) and not isinstance(value, bool)
    return float(value)


def _text(name: str) -> str:
    return gzip.decompress((EVIDENCE / (name.replace("/", "--") + ".gz")).read_bytes()).decode()


def _value(name: str) -> object:
    return json.loads(_text(name))


def _json(name: str) -> dict[str, object]:
    return _table(_value(name))


def _contains(text: str, claim: str) -> None:
    assert " ".join(claim.split()) in " ".join(text.split()), claim


def _one(pattern: str, text: str) -> str:
    found = re.findall(pattern, text, re.MULTILINE)
    assert len(found) == 1, (pattern, found)
    result: object = found[0]
    assert isinstance(result, str)
    return result


@dataclass(frozen=True)
class Suite:
    passed: int
    skipped: int
    subtests: int
    seconds: float


def _suite(log: str) -> Suite:
    line = _one(r"^(\d+ passed, \d+ skipped, \d+ subtests passed in [\d.]+s) \(", log)
    values = re.findall(r"[\d.]+", line)
    assert len(values) == 4
    return Suite(int(values[0]), int(values[1]), int(values[2]), float(values[3]))


@dataclass(frozen=True)
class MatrixRun:
    python: str
    suite: Suite
    wall: float
    covered: int
    executable: int


@cache
def _matrix() -> tuple[MatrixRun, ...]:
    measured = []
    for version in VERSIONS:
        result = _json(f"matrix-{version}-result.json")
        assert result["exit_status"] == 0 and result["commit"] == COMMIT
        assert result["runtime_tree"] == RUNTIME and result["python_job"] == version
        log = _text(f"matrix-{version}.log")
        assert _table(json.loads(log.splitlines()[-1])) == result
        suite = _suite(log)
        wall = (
            datetime.fromisoformat(str(result["finished"]))
            - datetime.fromisoformat(str(result["started"]))
        ).total_seconds()
        assert 0 < suite.seconds < wall
        # Count line elements themselves; the XML header alone is another summary.
        coverage = ElementTree.fromstring(_text(f"coverage-{version}.xml"))
        lines = coverage.findall("./packages/package/classes/class/lines/line")
        covered = sum(int(line.attrib["hits"]) > 0 for line in lines)
        total = len(lines)
        assert total == int(coverage.attrib["lines-valid"])
        assert covered == int(coverage.attrib["lines-covered"])
        assert 100 * covered / total >= 85
        assert coverage.attrib["branches-valid"] == "0"
        assert _one(r"^(TOTAL\s+\d+\s+\d+\s+\d+%)$", log).split() == [
            "TOTAL",
            str(total),
            str(total - covered),
            f"{100 * covered / total:.0f}%",
        ]
        _contains(log, f"Python {str(result['python']).split()[0]}")
        _contains(log, f"/work/py{version}/src/towel/__init__.py")
        measured.append(MatrixRun(str(result["python"]).split()[0], suite, wall, covered, total))
    return tuple(measured)


def test_retained_records_and_source_identity_are_byte_exact() -> None:
    """Changing an expected result requires evidence, not editing a mutable summary."""
    raw = (EVIDENCE / "provenance.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (
        "8a481f17442cca06902f0aa9918c75eab647947467c256491194bdb85069ab82"
    )
    manifest = _table(json.loads(raw))
    assert (manifest["frozen_commit"], manifest["runtime_tree"], manifest["tests_tree"]) == (
        COMMIT,
        RUNTIME,
        TESTS,
    )
    records = _table(manifest["retained_raw"])
    assert {path.name for path in EVIDENCE.glob("*.gz")} == set(records)
    assert len(records) == 48
    for name, value in records.items():
        record = _table(value)
        data = gzip.decompress((EVIDENCE / name).read_bytes())
        assert len(data) == record["bytes"]
        assert hashlib.sha256(data).hexdigest() == record["sha256"]
        assert name == str(record["source"]).replace("/", "--") + ".gz"
        assert "corpus" not in name
    source = _json("source.json")
    assert (source["commit"], source["runtime_tree"], source["tests_tree"]) == (
        COMMIT,
        RUNTIME,
        TESTS,
    )
    assert len(_table(source["runtime_sha256"])) == 96


def test_completed_matrix_has_its_declared_source_flags_and_checker_environment() -> None:
    """Exit status, imported path, final pytest output, and raw line counts must agree."""
    assert len(_matrix()) == 3
    config = tomllib.loads(_text("candidate/pyproject.toml"))
    tool = _table(config["tool"])
    mypy = _table(tool["mypy"])
    assert mypy["strict"] is True and mypy["python_version"] == "3.11"
    assert mypy["files"] == ["src/towel", "tests"]
    assert _table(_table(tool["coverage"])["run"])["concurrency"] == ["thread", "multiprocessing"]
    shell = _text("run-matrix.sh")
    for command in (
        "export UV_LINK_MODE=hardlink",
        "unset PYTHONDONTWRITEBYTECODE PYTEST_ADDOPTS",
        "COVERAGE_RCFILE COVERAGE_PROCESS_START PYTHONPATH",
        "assert actual == expected",
        "uv run --frozen coverage run -m pytest -q --durations=20",
        "uv run --frozen coverage combine",
        "uv run --frozen coverage report --fail-under=85",
        "git diff --exit-code HEAD",
    ):
        _contains(shell, command)
    driver = _text("matrix-driver.log").splitlines()
    assert len(driver) == 6
    for index, version in enumerate(("3.11", "3.13", "3.12")):
        assert driver[2 * index].startswith(f"START {version} {COMMIT} ")
        assert driver[2 * index + 1].startswith(f"END {version} 0 ")
    for version in VERSIONS:
        result = _json(f"matrix-{version}-result.json")
        assert result["platform"] == "Linux-6.10.14-linuxkit-aarch64-with-glibc2.41"
        assert result["tools"] == {
            "mypy": "2.3.1",
            "pyright": "1.1.414",
            "pytest": "9.1.1",
            "coverage": "7.16.0",
        }
        flags = _table(result["flags"])
        assert flags["UV_LINK_MODE"] == "hardlink" and flags["bytecode"] == "on"
        launch = _json(f"matrix-{version}-launch.json")
        args = _strings(launch["args"])
        assert args[args.index("--network") + 1] == "none" and launch["commit"] == COMMIT


def test_transformed_source_suite_and_post_run_integrity_match() -> None:
    """A successful suite must have loaded the transformed project, not frozen originals."""
    result = _json("selfdogfood/results.json")
    assert result["input_commit"] == COMMIT
    steps = {str(row["name"]): row for row in _rows(result["results"])}
    assert set(steps) == {"sync", "imported-source", "typecheck", "tests"}
    assert all(row["exit_status"] == 0 for row in steps.values())
    assert steps["tests"]["command"] == ["uv", "run", "--frozen", "pytest", "-q", "--durations=20"]
    assert steps["typecheck"]["command"] == ["uv", "run", "--frozen", "mypy"]
    assert _suite(_text("selfdogfood/tests.log")).seconds < _number(steps["tests"]["seconds"])
    _contains(_text("selfdogfood/imported-source.log"), "selfdogfood/project/src/towel/__init__.py")
    _contains(_text("selfdogfood/typecheck.log"), "Success: no issues found in 437 source files")
    source = _json("selfdogfood/source.json")
    rows = _rows(source["runtime_files"])
    assert len(rows) == len({str(row["path"]) for row in rows}) == 97
    assert source["input_commit"] == COMMIT
    assert sum(row["before"] != row["after"] for row in rows) == source["changed_files"] == 24
    original = _table(_json("source.json")["runtime_sha256"])
    assert {
        str(row["path"]): row["before"] for row in rows if str(row["path"]).endswith(".py")
    } == original
    assert _json("selfdogfood/post-test-integrity.json") == {
        "input_commit": COMMIT,
        "transformed_runtime_files_unchanged_by_test_run": 97,
        "all_generated_runtime_files_included": True,
        "frozen_candidate_still_clean": True,
    }


def test_wheel_smokes_exercise_installed_code_and_both_dependency_modes() -> None:
    """Installing two checkers is not evidence both checked the configured smoke fixture."""
    results = _rows(_value("wheel-smoke/results.json"))
    assert {row["case"] for row in results} == {
        f"{version}-{mode}" for version in VERSIONS for mode in ("bare", "extras")
    }
    wheel = _table(
        _table(_json("artifacts.json")["artifacts"])["dist/code_towel-1.772-py3-none-any.whl"]
    )
    for row in results:
        case = str(row["case"])
        assert row["status"] == "passed" and row["wheel_sha256"] == wheel["sha256"]
        installed = _table(row["installed"])
        assert (
            str(installed["python"]).split()[0]
            == {"3.11": "3.11.14", "3.12": "3.12.12", "3.13": "3.13.7"}[case.split("-")[0]]
        )
        assert f"/{case}/venv/" in str(installed["source"]) and "/site-packages/towel/" in str(
            installed["source"]
        )
        assert str(installed["platform"]).startswith("macOS-26.5.1-arm64-")
        commands = _rows(_value(f"wheel-smoke/{case}/commands.json"))
        assert all(command["status"] == 0 for command in commands)
        preview = [command for command in commands if "preview" in _strings(command["args"])]
        assert len(preview) == 1 and "Would apply 1 refactoring(s)" in str(preview[0]["stdout"])
        # The executed original, normal output, and (extras only) cold output agree.
        programs = [
            command
            for command in commands
            if len(_strings(command["args"])) == 2
            and str(_strings(command["args"])[-1]).endswith(
                ("/program.py", "/output.py", "/cold-output.py")
            )
        ]
        assert len(programs) == (3 if case.endswith("extras") else 2)
        assert {command["stdout"] for command in programs} == {"13 18\n"}
        distributions = _table(installed["distributions"])
        if case.endswith("extras"):
            assert {"mypy", "pyright", "black"} <= distributions.keys()
            assert any(
                "mypy" in _strings(command["args"]) and "--strict" in _strings(command["args"])
                for command in commands
            )
            cold = _json(f"wheel-smoke/{case}/cold-check.json")
            assert cold["source"] == installed["source"] and cold["resets"] == ["_RelocatedOracle"]
        else:
            assert not {"mypy", "pyright", "black"} & distributions.keys()
            assert any("not inferred or verified" in str(command["stdout"]) for command in commands)
    script = _text("smoke_wheels.py")
    for assertion in (
        "assert program.read_text()==original",
        "assert not (work/'declined.py').exists()",
        "assert helpers[0].returns is not None",
        "[tool.mypy]",
        "strict=true",
    ):
        _contains(script, assertion)
    assert "[tool.pyright]" not in script
    _contains(_text("observe_cold_cli.py"), "original(oracle)")


def _check_document(text: str) -> None:
    source = _json("source.json")
    for claim in (f"`{COMMIT}`", f"`{RUNTIME}`", f"`{TESTS}`"):
        _contains(text, claim)
    assert source["host_platform"] == "macOS-26.5.1-arm64-arm-64bit-Mach-O"
    assert source["host_cpu"] == "Apple M5 Max"
    assert source["host_memory_bytes"] == 128 * 2**30 and source["host_cores"] == 18
    assert _table(source["docker_vm"])["memory_bytes"] == 8215732224
    assert _table(source["docker_vm"])["cpus"] == 18
    for claim in (
        "local validation of an unreleased candidate, not results for the published 1.772 release",
        "artifacts still carry version 1.772 and must not be uploaded",
        "Documentation and evidence tests added after this freeze are separate checks; they are not included in the suite counts below",
        "native host was macOS 26.5.1 arm64, Apple M5 Max, 18 cores, with 128 GiB of memory (137438953472 bytes)",
        "Docker VM had 18 CPUs and 8215732224 bytes of memory",
        "Linux matrix ran on Linux arm64 (aarch64)",
        "No Windows or Linux x86_64 validation is established by these records",
        "not minimum resource requirements or timing guarantees",
        "network access disabled",
        "`UV_LINK_MODE=hardlink` and bytecode were on",
        "Coverage is line coverage, not branch coverage",
        "Approximate job wall seconds come from the recorded shell start and finish timestamps",
        "Pytest seconds come from pytest's completed summary",
        "mypy 2.3.1, Pyright 1.1.414, pytest 9.1.1, and coverage 7.16.0",
    ):
        _contains(text, claim)
    for run in _matrix():
        suite = run.suite
        _contains(
            text,
            f"| {run.python} | {suite.passed:,} | {suite.skipped} | {suite.subtests} | {suite.seconds:.2f} | {run.wall:,.0f} | {run.covered:,} / {run.executable:,} | {100 * run.covered / run.executable:.2f}% |",
        )
    quality = _text("quality-313.log")
    _contains(quality, "Quality checks passed.")
    formatted = _one(r"^(\d+) files would be left unchanged\.", quality)
    checked = _one(r"^Success: no issues found in (\d+) source files$", quality)
    _contains(text, f"Black checked {formatted} files")
    _contains(text, f"mypy gate checked {checked} source files")
    _contains(text, "mypy configuration targeted Python 3.11")
    for level in ("Low", "Medium", "High"):
        assert (
            _one(rf"^\s*{level}: (\d+)$", quality.split("Total issues (by confidence)")[0])
            == {"Low": "62", "Medium": "0", "High": "0"}[level]
        )
    _contains(text, "0 medium and 0 high severity issues; it also recorded 62 low severity issues")
    _contains(_text("dependencies-dev-audit.log"), "No known vulnerabilities found")
    _contains(text, "no known vulnerabilities for its retained installed pins at that time")
    assert _text("dependencies-dev.txt").strip()
    assert _text("diagrams-writable-cache.log").strip() == "5 diagram(s) checked, 0 broken"
    _contains(text, "5 diagrams with 0 broken")
    self_log = _text("selfdogfood.log")
    _contains(self_log, "Applied 31 refactoring(s) across 24 file(s)")
    _contains(text, "Self-refactoring applied 31 refactorings across 24 files")
    _contains(
        self_log,
        "8 proposal(s) not applied: refused by the type checker 6, narrows what a call-site lambda reads 2",
    )
    _contains(
        text,
        "Eight proposals were not applied: 6 were refused by the type checker and 2 narrowed what a call-site lambda reads",
    )
    suite = _suite(_text("selfdogfood/tests.log"))
    _contains(
        text,
        f"{suite.passed:,} passed, {suite.skipped} skipped, and {suite.subtests} subtests passed in {suite.seconds:.2f} pytest seconds",
    )
    step = next(
        row for row in _rows(_json("selfdogfood/results.json")["results"]) if row["name"] == "tests"
    )
    _contains(text, f"test subprocess wall time was {_number(step['seconds']):.3f} seconds")
    _contains(text, "all 97 runtime files, including `py.typed`")
    for family in ("grammar", "scope"):
        log = _text(f"fuzz-{family}.log")
        header = re.fullmatch(
            r"Fuzzing \w+ seeds (\d+)\.\.(\d+): (\d+) runs on (\d+) worker\(s\);.*",
            log.splitlines()[0],
        )
        assert header is not None
        start, end, count, workers = map(int, header.groups())
        outcomes = {
            name: int(number)
            for name, number in re.findall(
                r"(equivalent|unchanged|inconclusive) (\d+)", log.splitlines()[-2]
            )
        }
        assert sum(outcomes.values()) == count and workers == 4
        assert int(_one(r"^Done in \d+s: (\d+) failure\(s\)\.$", log)) == 0
        seconds = int(_one(r"^Done in (\d+)s: 0 failure\(s\)\.$", log))
        _contains(
            text,
            f"{family.capitalize()} seeds {start}..{end} produced {count:,} runs: {outcomes['equivalent']:,} equivalent",
        )
        _contains(text, f"{outcomes['unchanged']:,} unchanged")
        _contains(text, f"{outcomes.get('inconclusive', 0)} inconclusive")
        _contains(text, f"harness reported {seconds} seconds")
    _contains(
        text,
        "Inconclusive and unchanged cases are not counted as demonstrated transformed runtime equivalence",
    )
    artifacts = _json("artifacts.json")
    assert artifacts["commit"] == COMMIT and artifacts["status"] == "passed"
    for path, value in _table(artifacts["artifacts"]).items():
        record = _table(value)
        _contains(
            text,
            f"| `{Path(path).name}` | {int(_number(record['bytes'])):,} | `{record['sha256']}` |",
        )
    contents = _json("distribution-contents.json")
    assert contents["commit"] == COMMIT and contents["status"] == "passed"
    assert contents["runtime_files"] == 97 and contents["record_entries_verified"] == 103
    assert _text("twine-313.log").count(": PASSED") == 2
    for claim in (
        "Twine metadata checks passed for both artifacts",
        "wheel's 103 RECORD entries were verified",
        "96 Python files plus `py.typed`",
        "Six clean virtual-environment wheel smokes passed on native macOS arm64",
        "CPython 3.11.14, 3.12.12, and 3.13.7",
        "Bare installs had no mypy, Pyright, or Black",
        "Extras installs exercised configured strict mypy and Black",
        "This smoke fixture configured mypy, not a dual-checker project",
    ):
        _contains(text, claim)


def test_documented_non_corpus_results_match_completed_records() -> None:
    _check_document(DOCUMENT.read_text())


# These are plausible errors, not arbitrary edits: conflating source states,
# platforms, timing boundaries, or checker availability would overstate evidence.
@pytest.mark.parametrize(
    "before,after",
    [
        (COMMIT, "HEAD"),
        (RUNTIME, "runtime-tree-from-a-later-build"),
        ("not results for the published 1.772 release", "results for the published 1.772 release"),
        (
            "they are not included in the suite counts below",
            "they are included in the suite counts below",
        ),
        ("128 GiB", "8 GiB"),
        ("8215732224", "137438953472"),
        ("Linux arm64", "Linux x86_64"),
        ("No Windows or Linux x86_64 validation", "Windows and Linux x86_64 validation"),
        (
            "not minimum resource requirements or timing guarantees",
            "minimum resource requirements and timing guarantees",
        ),
        ("| 7,808 | 76 |", "| 7,884 | 0 |"),
        ("| 2951.30 |", "| 2993.33 |"),
        ("| 93.95% |", "| 100.00% |"),
        ("line coverage, not branch coverage", "branch coverage"),
        ("1859.416 seconds", "1829.530 seconds"),
        ("37 inconclusive", "0 inconclusive"),
        ("Eight proposals were not applied", "All proposals were applied"),
        ("62 low severity", "0 low severity"),
        ("not a dual-checker project", "a dual-checker project"),
        ("must not be uploaded", "are ready to upload"),
    ],
)
def test_plausible_false_claims_are_rejected(before: str, after: str) -> None:
    text = DOCUMENT.read_text()
    pattern = r"\s+".join(re.escape(word) for word in before.split())
    assert re.search(pattern, text)
    changed = re.sub(pattern, lambda _: after, text)
    with pytest.raises(AssertionError):
        _check_document(changed)


def test_harmless_whitespace_rewrapping_preserves_claims() -> None:
    """Presentation changes must not tempt maintainers to loosen factual expectations."""
    _check_document("\n".join(DOCUMENT.read_text().split()))
