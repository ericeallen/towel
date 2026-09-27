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

"""Release claims must retain their evidence, scope, units, and limitations.

These are documentation regressions, not benchmarks of the pytest host. Current
CI configuration and historical completed results are deliberately separate:
configuring a job does not prove it passed, and a future matrix cannot change
what 1.772 tested. Archived outputs independently supply counts and elapsed
time; context.toml identifies the environment fields recorded by the operator.

Changing engine output is not grounds for updating these expectations. Add
new release evidence, or substantiate a correction to the old record. Keep
the intent with each test; see docs/DECISIONS.md, September 27, and the policy
regression rule in CONTRIBUTING.md. Formatting changes are harmless; removing
conditions or changing a number without evidence is not.
"""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import tomllib
from typing import Callable, Mapping

import pytest

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "tests/release_evidence/1.772"
PATHS = (
    "README.md",
    "CHANGELOG.md",
    "docs/KNOWN_LIMITATIONS.md",
    "docs/PRODUCTION_READINESS.md",
    "docs/RELEASING.md",
    "pyproject.toml",
    ".github/workflows/ci.yml",
    ".github/workflows/ecosystem.yml",
)


def _table(value: object) -> dict[str, object]:
    assert isinstance(value, dict) and all(isinstance(key, str) for key in value)
    return {str(key): item for key, item in value.items()}


def _context() -> dict[str, object]:
    return _table(tomllib.loads((EVIDENCE / "context.toml").read_text()))


def _compact(text: str) -> str:
    return " ".join(text.split())


def _contains(text: str, claim: str) -> None:
    assert _compact(claim) in _compact(text), f"Missing or changed evidence-backed claim: {claim}"


def _one(pattern: str, text: str) -> str:
    matches = re.findall(pattern, text, re.MULTILINE)
    assert len(matches) == 1, f"Expected one {pattern!r}, found {len(matches)}"
    result: object = matches[0]
    assert isinstance(result, str)
    return result


def _section(text: str, heading: str) -> str:
    start = text.index(heading) + len(heading)
    return text[start:].split("\n## ", 1)[0]


def _job(workflow: str, name: str) -> str:
    # These workflows have literal job names, runners and a one-axis flow-list
    # matrix. Fail if that shape changes: do not silently omit matrix entries.
    return _one(rf"(?s)^  {name}:\n(.*?)(?=\n  [\w-]+:\n|\Z)", workflow)


def _python_matrix(job: str) -> list[str]:
    value: object = ast.literal_eval(_one(r"^\s+python-version: (\[.*\])$", job))
    assert isinstance(value, list) and value and all(isinstance(v, str) for v in value)
    return [str(v) for v in value]


def _runner(job: str) -> str:
    return _one(r"^\s+runs-on: ([\w-]+)$", job)


def _commands(job: str) -> str:
    """Executable run steps in these workflows; comments are not enabled flags."""
    blocks = re.findall(r"(?m)^      - run: (.*(?:\n          [^\n]*)*)", job)
    assert blocks, "No executable run steps found"
    return "\n".join(
        line.strip()
        for block in blocks
        for line in block.splitlines()
        if line.strip() != "|" and not line.strip().startswith("#")
    )


@pytest.fixture
def documents() -> dict[str, str]:
    return {path: (ROOT / path).read_text() for path in PATHS}


def _check_automation(documents: Mapping[str, str]) -> None:
    workflow = documents[".github/workflows/ci.yml"]
    tests = _job(workflow, "tests")
    quality = _job(workflow, "quality")
    versions = _python_matrix(tests)
    project = _table(_table(tomllib.loads(documents["pyproject.toml"]))["project"])
    classifiers = project["classifiers"]
    assert isinstance(classifiers, list)
    declared = [
        str(value).removeprefix("Programming Language :: Python :: ")
        for value in classifiers
        if re.fullmatch(r"Programming Language :: Python :: 3\.\d+", str(value))
    ]
    assert versions == declared, "CI must cover every advertised Python minor"
    assert project["requires-python"] == f">={versions[0]}"
    assert "Operating System :: POSIX" in classifiers
    platform = (
        f"Python {versions[0]} to {versions[-1]} on a POSIX system (macOS or Linux). "
        "Applying changes needs POSIX filesystem semantics. Parallel analysis uses the "
        "`fork` start method; under other start methods analysis runs on a single core. "
        "Windows is not a supported or validated release platform."
    )
    _contains(_section(documents["README.md"], "## Requirements"), platform)
    _contains(
        _section(documents["docs/KNOWN_LIMITATIONS.md"], "## Resources and platform"), platform
    )
    quality_python = _one(r"^\s+python-version: '([\d.]+)'$", quality)
    coverage = _one(r"coverage report --fail-under=(\d+)", _commands(tests))
    _contains(_commands(tests), "uv run --frozen coverage run -m pytest -q")
    _contains(_commands(tests), "uv run --frozen coverage combine")
    for command in (
        "black --check src/towel tests scripts",
        "flake8 src/towel scripts tests",
        "mypy",
        "bandit -r src/towel scripts -ll",
        "python scripts/audit_dependencies.py",
        "python -m pip_audit --strict --no-deps --disable-pip",
        "python -m build",
    ):
        _contains(_commands(quality), "uv run --frozen " + command)
    ecosystem = _job(documents[".github/workflows/ecosystem.yml"], "ecosystem")
    ecosystem_python = _one(r"^\s+python-version: '([\d.]+)'$", ecosystem)
    workers = _one(r"--workers (\d+)", _commands(ecosystem))
    timeout = _one(r"^\s+timeout-minutes: (\d+)$", ecosystem)
    mode = "untyped (`--no-types`)" if "--no-types" in _commands(ecosystem) else "default typing"
    _contains(
        _section(documents["docs/RELEASING.md"], "### Configured automation"),
        f"| Full tests and coverage | `{_runner(tests)}` | {', '.join(versions)} | "
        f"{coverage}% coverage gate |\n"
        f"| Quality and distributions | `{_runner(quality)}` | {quality_python} | "
        "Black, Flake8, mypy, Bandit, dependency audit, build |\n"
        f"| Scheduled/manual corpus | `{_runner(ecosystem)}` | {ecosystem_python} | "
        f"{mode}; {workers} concurrent projects; {timeout}-minute job limit |",
    )


@dataclass(frozen=True)
class Outcome:
    verdict: str
    files: int
    seconds: float
    detail: str


def _outcomes(name: str) -> dict[str, Outcome]:
    log = (EVIDENCE / name).read_text()
    rows = re.findall(
        r"^([A-Z_]+)\s+([\w.-]+)\s+files=(\d+)\s+refactor=\s*([\d.]+)s(.*)$",
        log,
        re.MULTILINE,
    )
    count = int(_one(r"ecosystem check: (\d+) projects,", log))
    assert len(rows) == count, "Do not silently drop an outcome the parser cannot read"
    result = {
        project: Outcome(verdict, int(files), float(seconds), detail)
        for verdict, project, files, seconds, detail in rows
    }
    assert len(result) == count, "Repeated projects are not independent validation"
    return result


def _release(documents: Mapping[str, str]) -> str:
    return _section(
        documents["docs/PRODUCTION_READINESS.md"],
        "## 1.772 release validation (September 26, 2026)",
    )


def _check_completed_results(documents: Mapping[str, str]) -> None:
    full = _outcomes("corpus-full.txt")
    rerun = _outcomes("corpus-sphinx.txt")
    for name in ("corpus-full.txt", "corpus-sphinx.txt"):
        log = (EVIDENCE / name).read_text()
        _contains(log, "typing mode: default")
        _contains(log, "Cross-module extraction off: none")
    assert rerun.keys() <= full.keys()
    assert full["sphinx"].verdict == "TIMEOUT" and rerun["sphinx"].verdict == "PASS"
    totals = Counter(result.verdict for result in (full | rerun).values())
    assert totals.keys() == {"PASS", "NO_CHANGE", "CRASH"}
    # The six raw CRASH outcomes were classified as import-problem refusals.
    # Do not silently turn a new crash category into an accepted verdict.
    assert {name for name, row in full.items() if row.verdict == "CRASH"} == {
        "anyio",
        "invoke",
        "isort",
        "towel-v1.414",
        "towel-v1.618",
        "towel-main",
    }
    report = _release(documents)
    for verdict, label in (
        ("PASS", "PASS"),
        ("NO_CHANGE", "NO_CHANGE"),
        ("CRASH", "Refused, import problem"),
    ):
        _contains(report, f"| {label} | {totals[verdict]} |")
    _contains(report, f"**The {len(full)}-project corpus**")
    _contains(
        documents["docs/RELEASING.md"],
        f"gave {totals['PASS']} `PASS`, {totals['NO_CHANGE']} `NO_CHANGE` "
        f"and six import-problem refusals among {len(full)} projects",
    )
    untyped = {name for name, row in full.items() if "[no-types]" in row.detail}
    assert untyped == {"blinker", "cheroot", "trio", "typing_extensions"}
    _contains(report, "**Four projects took the untyped path**")
    for name in untyped:
        _contains(report, name)
    suites = _context()["suite"]
    assert isinstance(suites, list) and suites
    record = (EVIDENCE / "release-record.txt").read_text()
    _contains(record, f"code-towel {_context()['release']}")
    _contains(
        record,
        f"Corpus, {len(full)} projects, typed and --cross-module, Python {_context()['corpus_python']}",
    )
    for raw in suites:
        row = _table(raw)
        passed = row["passed"]
        assert isinstance(passed, int)
        _contains(record, f"{row['python']} {passed:,} passed")
        _contains(
            report, f"| {row['python']} | {passed:,} | {row['skipped']} | {row['subtests']} |"
        )


def _check_measurements(documents: Mapping[str, str]) -> None:
    context = _context()
    for heading in ("## Requirements", "## How long it takes"):
        _contains(
            _section(documents["README.md"], heading),
            f"{context['host']} ({context['host_cores']} cores, {context['host_memory_gib']} GiB)",
        )
    for path in ("docs/KNOWN_LIMITATIONS.md", "docs/PRODUCTION_READINESS.md"):
        _contains(
            _section(documents[path], "## Measurement environment"),
            f"{context['host']} with {context['host_cores']} cores and "
            f"{context['host_memory_gib']} GiB of memory, writing to an "
            f"{context['host_filesystem']} internal volume",
        )
        _contains(
            documents[path],
            f"mypy {context['sphinx_mypy']} and pyright {context['sphinx_pyright']}",
        )
    _contains(
        _release(documents),
        f"the local gates and native timings ran on {context['local_platform']}, "
        f"GitHub CI ran the full suite on {context['ci_platform']} for 3.11, 3.12 and 3.13, "
        f"and the corpus ran in a {context['corpus_platform']} container, on Python "
        f"{context['corpus_python']} only; no run was made on {context['untested_platform']}",
    )
    _contains(
        documents["README.md"],
        f"Native timings use {context['local_platform']}; the release-corpus timing below "
        f"uses a Docker {context['corpus_platform']} VM.",
    )
    assert context["corpus_concurrency"] == 4 and context["sphinx_concurrency"] == 1
    _contains(
        _release(documents),
        "The full corpus ran four projects at a time; the Sphinx-only rerun ran alone.",
    )
    sphinx = _outcomes("corpus-sphinx.txt")["sphinx"]
    commit = _one(r"towel ([0-9a-f]+),", (EVIDENCE / "corpus-sphinx.txt").read_text())
    claim = (
        f"The Sphinx-only rerun at `{commit[:7]}` took {round(sphinx.seconds):,} s "
        f"(about {round(sphinx.seconds / 60)} minutes) and changed {sphinx.files} files. "
        f"It ran alone in a Docker {context['corpus_platform']} VM with "
        f"{context['vm_cores']} cores and {context['vm_memory_gb']} GB of memory, using "
        f"Python {context['corpus_python']}, `TOWEL_WORKERS={context['refactor_workers']}`, "
        "`--cross-module`, and default typing (mypy and Pyright both strict). "
        "These are measured conditions, not minimum resource requirements or time guarantees."
    )
    for path in (
        "README.md",
        "CHANGELOG.md",
        "docs/KNOWN_LIMITATIONS.md",
        "docs/PRODUCTION_READINESS.md",
    ):
        _contains(documents[path], claim)


def test_advertised_platforms_and_automation_match_executable_configuration(
    documents: dict[str, str],
) -> None:
    """A fallback for process start methods does not establish Windows release support."""
    _check_automation(documents)


@pytest.mark.parametrize("name", ["corpus-full.txt", "corpus-sphinx.txt", "release-record.txt"])
def test_retained_machine_output_is_byte_exact(name: str) -> None:
    """Preserve the original instrument output, including spaces a formatter might trim."""
    assert (
        hashlib.sha256((EVIDENCE / name).read_bytes()).hexdigest()
        == _table(_context()["source_sha256"])[name]
    )


def test_release_totals_match_retained_completed_runs(documents: dict[str, str]) -> None:
    """Count each project once, replace the timeout with its rerun, retain refusals/fallbacks."""
    _check_completed_results(documents)


def test_documented_measurements_keep_their_platform_resources_and_conditions(
    documents: dict[str, str],
) -> None:
    """Do not turn a dated observation on one machine into a minimum or speed guarantee."""
    _check_measurements(documents)


@pytest.mark.parametrize(
    "path,before,after,check",
    [
        (
            "README.md",
            "Windows is not a supported or validated",
            "Windows is a supported and validated",
            _check_automation,
        ),
        (
            ".github/workflows/ci.yml",
            "['3.11', '3.12', '3.13']",
            "['3.12', '3.13']",
            _check_automation,
        ),
        (".github/workflows/ecosystem.yml", "--workers 2", "--workers 4", _check_automation),
        (".github/workflows/ecosystem.yml", "--no-types ", "", _check_automation),
        (
            ".github/workflows/ci.yml",
            "- run: uv run --frozen mypy",
            "# mypy is disabled",
            _check_automation,
        ),
        (
            "docs/PRODUCTION_READINESS.md",
            "| PASS | 90 |",
            "| PASS | 141 |",
            _check_completed_results,
        ),
        (
            "docs/PRODUCTION_READINESS.md",
            "| 3.13 | 7,899 |",
            "| 3.13 | 7,900 |",
            _check_completed_results,
        ),
        (
            "docs/PRODUCTION_READINESS.md",
            "**Four projects took the untyped path**",
            "**All projects were typed**",
            _check_completed_results,
        ),
        ("README.md", "5,660 s", "560 s", _check_measurements),
        ("README.md", "128 GiB", "8 GiB", _check_measurements),
        ("docs/KNOWN_LIMITATIONS.md", "128 GiB", "8 GiB", _check_measurements),
        ("README.md", "18 cores and 8 GB", "18 cores and 128 GB", _check_measurements),
        ("README.md", "It ran alone", "It ran with four other projects", _check_measurements),
        (
            "README.md",
            "not minimum resource requirements or time guarantees",
            "minimum resource requirements and time guarantees",
            _check_measurements,
        ),
    ],
)
def test_the_checks_detect_false_claims(
    documents: dict[str, str],
    path: str,
    before: str,
    after: str,
    check: Callable[[Mapping[str, str]], None],
) -> None:
    """Exercise the instrument: a green check must reject realistic documentation drift."""
    pattern = r"\s+".join(re.escape(word) for word in before.split())
    assert re.search(pattern, documents[path])
    changed = {**documents, path: re.sub(pattern, lambda _: after, documents[path])}
    with pytest.raises(AssertionError):
        check(changed)


def test_line_wrapping_is_not_a_change_in_evidence(documents: dict[str, str]) -> None:
    """Permit presentation edits without weakening any factual expectation."""
    rewrapped = {
        path: (
            "".join(
                line if line.startswith("#") else line.replace(" ", "\n")
                for line in text.splitlines(keepends=True)
            )
            if path.endswith(".md")
            else text
        )
        for path, text in documents.items()
    }
    _check_automation(rewrapped)
    _check_completed_results(rewrapped)
    _check_measurements(rewrapped)
