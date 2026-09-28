# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by law or agreed in writing, software distributed under the
# License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS
# OF ANY KIND, either express or implied. See the License for the specific
# language governing permissions and limitations under the License.

"""A release command must refuse incomplete, stale, or unreviewed observations.

The positive fixture is deliberately a tiny synthetic Git project and evidence
bundle, not a new claim about a real Towel release. Mutations exercise the public
verifier and independent raw-output comparisons, including under python -O.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from typing import Callable
import zipfile

import pytest

from scripts import verify_release_evidence as gate

VERSION = "9.0.0"
START, FINISH = "2026-09-01T00:00:00+00:00", "2026-09-01T00:00:10+00:00"
ROOT = Path(__file__).resolve().parents[1]
SUMMARY = "20 passed in 1.00s\n"


def command(**fields: object) -> dict[str, object]:
    return {"started": START, "finished": FINISH, "exit_status": 0, **fields}


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"},
        check=True,
    )
    return result.stdout.strip()


@dataclass
class Bundle:
    repo: Path
    root: Path
    value: dict[str, object]
    source: dict[str, bytes]
    commit: str

    @property
    def path(self) -> Path:
        return self.root / "release-evidence.json"

    def save(self) -> None:
        self.path.write_text(json.dumps(self.value))

    def ref(self, name: str, data: bytes) -> dict[str, object]:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {"path": name, "sha256": gate.digest(data)}

    def write(self, name: str, value: object) -> None:
        data = value.encode() if isinstance(value, str) else json.dumps(value).encode()
        records = gate.table(self.value["records"])
        records[name] = self.ref(name, data)
        self.value["records"] = records
        self.save()

    def read(self, name: str) -> object:
        return json.loads((self.root / name).read_bytes())

    def alter(self, name: str, change: Callable[[dict[str, object]], None]) -> None:
        value = gate.table(self.read(name))
        change(value)
        self.write(name, value)

    def review(self, change: Callable[[dict[str, object]], None]) -> None:
        value = gate.table(self.read("review.json"))
        change(value)
        self.value["review"] = self.ref("review.json", json.dumps(value).encode())
        self.save()

    def check(self, *, validation_only: bool = False) -> tuple[Path, Path]:
        self.save()
        return gate.verify(self.repo, self.path, VERSION, validation_only=validation_only)

    def commit_file(self, name: str, value: str) -> None:
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
        git(self.repo, "add", name)
        git(self.repo, "commit", "-qm", "Synthetic source amendment")


def artifacts(bundle: Bundle) -> tuple[str, str]:
    wheel_name = f"dist/code_towel-{VERSION}-py3-none-any.whl"
    sdist_name = f"dist/code_towel-{VERSION}.tar.gz"
    metadata = (
        f"Metadata-Version: 2.4\nName: code-towel\nVersion: {VERSION}\n\n".encode()
        + bundle.source["README.md"]
    )
    wheel_files = {
        n.removeprefix("src/"): b for n, b in bundle.source.items() if n.startswith("src/towel/")
    }
    prefix = f"code_towel-{VERSION}.dist-info/"
    wheel_files[prefix + "METADATA"] = metadata
    entries = []
    for name, data in wheel_files.items():
        encoded = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        entries.append(f"{name},sha256={encoded},{len(data)}")
    wheel_files[prefix + "RECORD"] = ("\n".join(entries) + f"\n{prefix}RECORD,,\n").encode()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, data in wheel_files.items():
            archive.writestr(name, data)
    wheel = bundle.ref(wheel_name, stream.getvalue())
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for name, data in {**bundle.source, "PKG-INFO": metadata}.items():
            member = tarfile.TarInfo(f"code_towel-{VERSION}/{name}")
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    sdist = bundle.ref(sdist_name, stream.getvalue())
    bundle.value["artifacts"] = {"wheel": wheel, "sdist": sdist}
    bundle.write(
        "artifacts.json",
        {
            "commit": bundle.commit,
            "artifacts": {
                name: {"sha256": ref["sha256"], "bytes": (bundle.root / name).stat().st_size}
                for name, ref in ((wheel_name, wheel), (sdist_name, sdist))
            },
        },
    )
    return gate.text(wheel["sha256"]), gate.text(sdist["sha256"])


@pytest.fixture
def bundle(tmp_path: Path) -> Bundle:
    repo = tmp_path / "repo"
    repo.mkdir()
    initial = {
        "src/towel/__init__.py": b"VALUE = 1\n",
        "src/towel/py.typed": b"",
        "README.md": b"Synthetic release gate fixture\n",
        "tests/test_example.py": b"def test_example(): pass\n",
        "pyproject.toml": f'[project]\nname="code-towel"\nversion="{VERSION}"\n'.encode(),
        "scripts/ecosystem/manifest.toml": (
            "[[" + 'project]]\nname="example"\nrev="' + "a" * 40 + '"\n'
        ).encode(),
    }
    for name, data in initial.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "config", "user.name", "Synthetic Fixture")
    git(repo, "config", "commit.gpgsign", "false")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Synthetic validated source")
    commit = git(repo, "rev-parse", "HEAD")
    runtime = git(repo, "rev-parse", "HEAD:src/towel")
    root = tmp_path / "evidence"
    root.mkdir()
    result = Bundle(
        repo,
        root,
        {
            "schema": 1,
            "version": VERSION,
            "purpose": "release",
            "validation_commit": commit,
            "records": {},
            "amendments": None,
        },
        initial,
        commit,
    )
    result.write(
        "source.json",
        {
            "commit": commit,
            "runtime_tree": runtime,
            "runtime_sha256": {
                n: gate.digest(b)
                for n, b in initial.items()
                if n.startswith("src/towel/") and n.endswith(".py")
            },
            "py_typed_sha256": gate.digest(b""),
        },
    )
    wheel, archive = artifacts(result)
    result.write(
        "run-matrix.sh",
        "\n".join(gate.MATRIX_COMMANDS) + "\nassert actual == expected # src/towel/__init__.py\n",
    )
    for version in gate.VERSIONS:
        matrix = command(
            commit=commit,
            runtime_tree=runtime,
            python_job=version,
            python=version + ".9",
            flags=gate.MATRIX_FLAGS,
        )
        result.write(f"matrix-{version}-result.json", matrix)
        result.write(
            f"matrix-{version}.log",
            "/work/src/towel/__init__.py\n"
            + SUMMARY
            + "src/towel/__init__.py 1 0 100%\nTOTAL 1 0 100%\n"
            + json.dumps(matrix)
            + "\n",
        )
        result.write(
            f"coverage-{version}.xml",
            '<coverage lines-valid="1" lines-covered="1"><sources><source>/work/src/towel</source></sources><packages><package><classes><class filename="__init__.py"><lines><line number="1" hits="1"/></lines></class></classes></package></packages></coverage>',
        )
    result.write(
        "build-results.json",
        [
            command(
                name=name,
                args=[
                    "uvx",
                    "twine",
                    "check",
                    "--strict",
                    f"/dist/code_towel-{VERSION}-py3-none-any.whl",
                    f"/dist/code_towel-{VERSION}.tar.gz",
                ],
            )
            for name in (
                "quality-313",
                "build-313",
                "artifact-inspection-313",
                "distribution-contents",
                "twine-313",
            )
        ],
    )
    result.write(
        "quality-313.log",
        "black --check\nflake8\nmypy\nbandit\nSuccess: no issues found\nNo issues identified.\nQuality checks passed.\n",
    )
    result.write(
        "twine-313.log",
        f"code_towel-{VERSION}-py3-none-any.whl: PASSED\ncode_towel-{VERSION}.tar.gz: PASSED\n",
    )
    result.write("dependencies-audit-command.json", command())
    result.write("dependencies-dev-audit.log", "No known vulnerabilities found\n")
    result.write("dependencies-dev.txt", "pytest==9.0.3\n")
    result.write("diagrams-command.json", command())
    result.write("diagrams-writable-cache.log", "1 diagram(s) checked, 0 broken\n")
    result.write(
        "fuzz-results.json",
        [
            command(family=family, args=["--count", "2000", "--seed", "1"])
            for family in ("grammar", "scope")
        ],
    )
    for family in ("grammar", "scope"):
        result.write(f"fuzz-{family}.log", "Done in 1s: 0 failure(s).\n")
    smokes = []
    for version in gate.VERSIONS:
        for mode in ("bare", "extras"):
            case = version + "-" + mode
            smokes.append(
                {
                    "case": case,
                    "wheel_sha256": wheel,
                    "installed": {
                        "python": version + ".9",
                        "source": "/venv/lib/python" + version + "/site-packages/towel/__init__.py",
                        "distributions": {"code-towel": VERSION},
                    },
                }
            )
            commands = [
                {
                    "args": [
                        "uv",
                        "pip",
                        "install",
                        f"/dist/code_towel-{VERSION}-py3-none-any.whl"
                        + ("[format,types]" if mode == "extras" else ""),
                    ],
                    "status": 0,
                },
                *[
                    {"args": [token], "status": 0, "stdout": "Would apply 1 refactoring(s)"}
                    for token in ("preview", "dry", "--version", "mypy")
                ],
                *[
                    {"args": ["python", "/work/" + name], "status": 0, "stdout": "same output\n"}
                    for name in ("program.py", "output.py", "cold-output.py")
                ],
            ]
            result.write(f"wheel-smoke/{case}/commands.json", commands)
            if mode == "extras":
                result.write(
                    f"wheel-smoke/{case}/cold-check.json",
                    {"resets": ["oracle"], "source": gate.table(smokes[-1]["installed"])["source"]},
                )
    result.write("wheel-smoke/results.json", smokes)
    result.write(
        "sdist-docs-result.json",
        {
            "archive_sha256": archive,
            "steps": [command(name=name) for name in ("sync", "source", "tests")],
        },
    )
    result.write("sdist-docs-tests.log", SUMMARY)
    result.write("self-generation-result.json", command())
    result.write(
        "selfdogfood/results.json",
        {
            "input_commit": commit,
            "results": [
                command(
                    name=name, command=["python", "-c", "assert p.is_relative_to(Path.cwd()/'src')"]
                )
                for name in ("sync", "imported-source", "typecheck", "tests")
            ],
        },
    )
    result.write(
        "selfdogfood/imported-source.log", "/tmp/selfdogfood/project/src/towel/__init__.py\n"
    )
    result.write("selfdogfood/tests.log", SUMMARY)
    result.write("selfdogfood/typecheck.log", "Success: no issues found\n")
    runtime_files = [
        {"path": n, "before": gate.digest(b), "after": gate.digest(b)}
        for n, b in initial.items()
        if n.startswith("src/towel/")
    ]
    result.write(
        "selfdogfood/source.json", {"input_commit": commit, "runtime_files": runtime_files}
    )
    result.write(
        "selfdogfood/post-test-integrity.json",
        {
            "input_commit": commit,
            "transformed_runtime_files_unchanged_by_test_run": len(runtime_files),
            "all_generated_runtime_files_included": True,
        },
    )
    row = {
        "name": "example",
        "commit": "a" * 40,
        "verdict": "PASS",
        "fallback": "",
        "baseline": {"returncode": 0, "log": "/logs/example-before.log"},
        "after": {"returncode": 0, "log": "/logs/example-after.log"},
        "refactor": {"returncode": 0, "log": "/logs/example-refactor.log"},
    }
    result.write(
        "corpus-report/summary.json",
        {
            "towel": commit,
            "typing_mode": "default",
            "candidate": {"sha256": wheel},
            "counts": {"PASS": 1},
            "results": [row],
        },
    )
    result.write("corpus-report/example.json", row)
    for phase in ("before", "after", "refactor"):
        result.write(f"corpus-logs/example-{phase}.log", SUMMARY)
    result.write("corpus.log", "PASS example files=1 refactor=1.0s\n")
    result.write("corpus-command.json", {"commit": commit, "started": START})
    result.write("corpus-exit.json", {"status": 0, "oom_killed": False, "finished": FINISH})
    result.write(
        "corpus-environment.json",
        {"manifest_sha256": gate.digest(initial["scripts/ecosystem/manifest.toml"])},
    )
    report = result.ref("review.md", b"Independent synthetic review\n")
    review = {
        "validation_commit": commit,
        "wheel_sha256": wheel,
        "reviewer": "fixture",
        "report": report,
        "open_findings": [],
        **{
            name: {}
            for name in (
                "reviewed_nonaccepted",
                "reviewed_fallbacks",
                "reviewed_known_failures",
                "reviewed_baseline_failures",
            )
        },
    }
    result.value["review"] = result.ref("review.json", json.dumps(review).encode())
    result.save()
    return result


def test_complete_bundle_verifies_exact_prebuilt_artifacts(bundle: Bundle) -> None:
    files = bundle.check()
    assert all(p.is_file() for p in files)
    assert not git(bundle.repo, "status", "--porcelain")


@pytest.mark.parametrize("record", gate.REQUIRED_RECORDS)
def test_every_required_record_is_mandatory(bundle: Bundle, record: str) -> None:
    records = gate.table(bundle.value["records"])
    records.pop(record)
    bundle.value["records"] = records
    with pytest.raises(gate.EvidenceError, match="Missing required record"):
        bundle.check()


def test_missing_incomplete_and_failed_matrix_refuse(bundle: Bundle) -> None:
    def remove_finished(row: dict[str, object]) -> None:
        del row["finished"]

    bundle.alter("matrix-3.12-result.json", remove_finished)
    with pytest.raises(gate.EvidenceError, match="nonempty"):
        bundle.check()


@pytest.mark.parametrize("value", [1, False, "0", None])
def test_failed_or_invalid_status_is_not_success(bundle: Bundle, value: object) -> None:
    bundle.alter("matrix-3.11-result.json", lambda row: row.update(exit_status=value))
    with pytest.raises(gate.EvidenceError):
        bundle.check()


def test_rehashing_failed_summary_cannot_turn_it_green(bundle: Bundle) -> None:
    record = bundle.read("matrix-3.11-result.json")
    bundle.write("matrix-3.11.log", "1 failed, 19 passed in 1.00s\n" + json.dumps(record) + "\n")
    with pytest.raises(gate.EvidenceError, match="failures"):
        bundle.check()


def test_coverage_uses_actual_line_hits(bundle: Bundle) -> None:
    log = (bundle.root / "coverage-3.12.xml").read_text().replace('hits="1"', 'hits="0"')
    bundle.write("coverage-3.12.xml", log)
    with pytest.raises(gate.EvidenceError, match="coverage totals"):
        bundle.check()


@pytest.mark.parametrize(
    "record,field",
    [
        ("matrix-3.12-result.json", "commit"),
        ("source.json", "runtime_tree"),
        ("corpus-report/summary.json", "towel"),
        ("selfdogfood/results.json", "input_commit"),
    ],
)
def test_mismatched_source_refuses(bundle: Bundle, record: str, field: str) -> None:
    bundle.alter(record, lambda row: row.update({field: "f" * 40}))
    with pytest.raises(gate.EvidenceError, match="mismatch"):
        bundle.check()


def test_another_wheel_in_a_smoke_refuses(bundle: Bundle) -> None:
    smokes = gate.rows(bundle.read("wheel-smoke/results.json"))
    smokes[0]["wheel_sha256"] = "f" * 64
    bundle.write("wheel-smoke/results.json", smokes)
    with pytest.raises(gate.EvidenceError, match="another wheel"):
        bundle.check()


def test_artifact_byte_tampering_refuses(bundle: Bundle) -> None:
    ref = gate.table(gate.table(bundle.value["artifacts"])["wheel"])
    (bundle.root / gate.text(ref["path"])).write_bytes(b"not the validated wheel")
    with pytest.raises(gate.EvidenceError, match="hash mismatch"):
        bundle.check()


def test_rehashed_other_runtime_still_refuses(bundle: Bundle) -> None:
    bundle.source["src/towel/__init__.py"] = b"VALUE = 2\n"
    artifacts(bundle)
    with pytest.raises(gate.EvidenceError, match="runtime differs"):
        bundle.check()


def test_incomplete_corpus_is_not_a_success_flag(bundle: Bundle) -> None:
    bundle.alter(
        "corpus-report/summary.json", lambda row: row.update(results=[], counts={}, status="passed")
    )
    with pytest.raises(gate.EvidenceError, match="Corpus incomplete"):
        bundle.check()


def test_changed_failure_identity_is_not_a_pass(bundle: Bundle) -> None:
    row = gate.table(bundle.read("corpus-report/example.json"))
    for phase, test in [("baseline", "old"), ("after", "new")]:
        step = gate.table(row[phase])
        step["returncode"] = 1
        row[phase] = step
        name = Path(gate.text(step["log"])).name
        bundle.write(
            "corpus-logs/" + name,
            f"FAILED test_fixture.py::test_{test}\n1 failed, 19 passed in 1.00s\n",
        )
    bundle.write("corpus-report/example.json", row)
    bundle.alter("corpus-report/summary.json", lambda summary: summary.update(results=[row]))
    with pytest.raises(gate.EvidenceError, match="retest.json"):
        bundle.check()


def test_same_preexisting_failures_need_explicit_review(bundle: Bundle) -> None:
    row = gate.table(bundle.read("corpus-report/example.json"))
    for phase in ("baseline", "after"):
        step = gate.table(row[phase])
        step["returncode"] = 1
        row[phase] = step
        bundle.write(
            "corpus-logs/" + Path(gate.text(step["log"])).name,
            "FAILED test_fixture.py::test_old\n1 failed, 19 passed in 1.00s\n",
        )
    bundle.write("corpus-report/example.json", row)
    bundle.alter("corpus-report/summary.json", lambda summary: summary.update(results=[row]))
    with pytest.raises(gate.EvidenceError, match="reviewed_baseline_failures"):
        bundle.check()
    detail = {
        "reason": "Pre-existing failure in this run/environment, unchanged after transformation.",
        "disposition": "pre-existing-baseline",
        "evidence": [gate.table(bundle.value["records"])["corpus-logs/example-before.log"]],
    }
    bundle.review(lambda review: review.update(reviewed_baseline_failures={"example": detail}))
    bundle.check()


def test_explicit_validation_purpose_cannot_be_relabelled(bundle: Bundle) -> None:
    bundle.alter(
        "artifacts.json",
        lambda row: row.update(publication_status="local validation artifacts; not for upload"),
    )
    with pytest.raises(gate.EvidenceError, match="not for publication"):
        bundle.check()
    bundle.check(validation_only=True)


def test_dirty_checkout_and_version_drift_refuse(bundle: Bundle) -> None:
    (bundle.repo / "README.md").write_text("uncommitted")
    with pytest.raises(gate.EvidenceError, match="clean"):
        bundle.check()
    git(bundle.repo, "add", ".")
    git(bundle.repo, "commit", "-qm", "Synthetic docs")
    with pytest.raises(gate.EvidenceError):
        gate.verify(bundle.repo, bundle.path, "9.0.1")


def test_duplicate_json_key_and_path_escape_refuse(bundle: Bundle) -> None:
    with pytest.raises(gate.EvidenceError, match="Duplicate"):
        gate.decode(b'{"schema":1,"schema":1}')
    refs = gate.table(bundle.value["records"])
    refs["source.json"] = {"path": "../source.json", "sha256": "a" * 64}
    bundle.value["records"] = refs
    with pytest.raises(gate.EvidenceError, match="Unsafe"):
        bundle.check()


def amendment(bundle: Bundle, name: str) -> None:
    changed = {name: gate.digest((bundle.repo / name).read_bytes())}
    report = bundle.ref(
        "amendment-review.md", b"Exact synthetic documentation/test change reviewed\n"
    )
    check = command(
        kind="tests",
        command=["python", "-m", "pytest", "tests/test_example.py"],
        files=changed,
        covers=[name],
        log=bundle.ref("supplemental-tests.log", SUMMARY.encode()),
    )
    quality = command(
        kind="quality",
        command=["just", "check"],
        files=changed,
        covers=[name],
        log=bundle.ref("supplemental-quality.log", b"Quality checks passed.\n"),
    )
    bundle.value["amendments"] = {"files": changed, "report": report, "checks": [check, quality]}
    bundle.save()


def test_exact_supplemental_checks_can_admit_new_documentation(bundle: Bundle) -> None:
    name = "docs/new-evidence.md"
    bundle.commit_file(name, "Historical evidence with exact provenance\n")
    with pytest.raises(gate.EvidenceError):
        bundle.check(validation_only=True)
    amendment(bundle, name)
    bundle.check(validation_only=True)
    raw = gate.table(bundle.value["amendments"])
    raw["checks"] = []
    bundle.value["amendments"] = raw
    with pytest.raises(gate.EvidenceError, match="supplemental"):
        bundle.check(validation_only=True)


@pytest.mark.parametrize(
    "name",
    [
        "src/towel/__init__.py",
        "pyproject.toml",
        "scripts/ecosystem/manifest.toml",
        "scripts/ecosystem_check.py",
        "tests/differential/fuzz.py",
        "tests/conftest.py",
    ],
)
def test_runtime_config_and_harness_changes_invalidate_old_gates(bundle: Bundle, name: str) -> None:
    content = (bundle.repo / name).read_text() if (bundle.repo / name).exists() else ""
    bundle.commit_file(name, content + "\n# changed\n")
    amendment(bundle, name)
    with pytest.raises(gate.EvidenceError, match="invalidates evidence"):
        bundle.check(validation_only=True)


def test_release_rejects_amendment_to_prebuilt_shipped_document(bundle: Bundle) -> None:
    bundle.commit_file("README.md", "New shipping text\n")
    amendment(bundle, "README.md")
    with pytest.raises(gate.EvidenceError, match="predates shipped"):
        bundle.check()
    bundle.check(validation_only=True)


@pytest.mark.parametrize("optimized", [False, True])
def test_cli_missing_record_refuses_even_under_optimization(
    bundle: Bundle, optimized: bool
) -> None:
    refs = gate.table(bundle.value["records"])
    refs.pop("fuzz-results.json")
    bundle.value["records"] = refs
    bundle.save()
    args = [
        sys.executable,
        *(["-O"] if optimized else []),
        "-B",
        str(ROOT / "scripts/verify_release_evidence.py"),
        VERSION,
        str(bundle.path),
        "--repo",
        str(bundle.repo),
    ]
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    assert result.returncode == 1 and "Missing required record" in result.stderr
    assert "verified" not in result.stdout


def test_supported_release_recipe_only_verifies_the_prebuilt_evidence() -> None:
    recipe = (
        (ROOT / "justfile").read_text().split("release VERSION EVIDENCE:", 1)[1].split("\n\n", 1)[0]
    )
    assert "verify_release_evidence" in recipe
    assert "bump-version" not in recipe and "just build" not in recipe
    assert "--validation-only" not in recipe


@pytest.mark.parametrize("change", ["renamed", "duplicate", "missing", "console"])
def test_coverage_inventory_and_console_bind_the_whole_package(bundle: Bundle, change: str) -> None:
    name = "coverage-3.11.xml"
    xml = (bundle.root / name).read_text()
    if change == "renamed":
        xml = xml.replace("__init__.py", "unrelated.py")
    elif change == "duplicate":
        xml = xml.replace("</classes>", '<class filename="__init__.py"><lines/></class></classes>')
    elif change == "missing":
        start, end = xml.index("<class filename="), xml.index("</class>") + len("</class>")
        xml = xml[:start] + xml[end:]
    else:
        log = (bundle.root / "matrix-3.11.log").read_text().replace("TOTAL 1 0", "TOTAL 2 0")
        bundle.write("matrix-3.11.log", log)
    bundle.write(name, xml)
    with pytest.raises(gate.EvidenceError, match="coverage"):
        bundle.check()


def test_assume_unchanged_cannot_hide_different_working_bytes(bundle: Bundle) -> None:
    git(bundle.repo, "update-index", "--assume-unchanged", "src/towel/__init__.py")
    (bundle.repo / "src/towel/__init__.py").write_text("VALUE = 999\n")
    assert not git(bundle.repo, "status", "--porcelain")
    with pytest.raises(gate.EvidenceError, match="working bytes"):
        bundle.check()


@pytest.mark.parametrize("mode", ["flags", "import", "command"])
def test_matrix_execution_scope_is_required(bundle: Bundle, mode: str) -> None:
    if mode == "flags":
        row = gate.table(bundle.read("matrix-3.11-result.json"))
        row["flags"] = {**gate.MATRIX_FLAGS, "coverage": "pytest tests/unit"}
        bundle.write("matrix-3.11-result.json", row)
    elif mode == "import":
        log = (
            (bundle.root / "matrix-3.11.log")
            .read_text()
            .replace("/work/src/towel/__init__.py", "/venv/site-packages/towel/__init__.py")
        )
        bundle.write("matrix-3.11.log", log)
    else:
        driver = (
            (bundle.root / "run-matrix.sh")
            .read_text()
            .replace(
                "coverage run -m pytest -q --durations=20", "coverage run -m pytest tests/unit"
            )
        )
        bundle.write("run-matrix.sh", driver)
    with pytest.raises(gate.EvidenceError, match="Matrix"):
        bundle.check()


def test_raw_smoke_output_cannot_differ_after_transformation(bundle: Bundle) -> None:
    name = "wheel-smoke/3.11-bare/commands.json"
    commands = gate.rows(bundle.read(name))
    commands[-2]["stdout"] = "different result\n"
    bundle.write(name, commands)
    with pytest.raises(gate.EvidenceError, match="runtime outputs"):
        bundle.check()


def test_open_review_findings_refuse(bundle: Bundle) -> None:
    bundle.review(lambda review: review.update(open_findings=["unresolved regression"]))
    with pytest.raises(gate.EvidenceError, match="open findings"):
        bundle.check()


def test_unreviewed_fallback_refuses(bundle: Bundle) -> None:
    row = gate.table(bundle.read("corpus-report/example.json"))
    row["fallback"] = "checker failed to start"
    bundle.write("corpus-report/example.json", row)
    bundle.alter("corpus-report/summary.json", lambda summary: summary.update(results=[row]))
    with pytest.raises(gate.EvidenceError, match="reviewed_fallbacks"):
        bundle.check()


def test_valid_bundle_verifies_under_optimized_python(bundle: Bundle) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-O",
            "-B",
            str(ROOT / "scripts/verify_release_evidence.py"),
            VERSION,
            str(bundle.path),
            "--repo",
            str(bundle.repo),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Local release evidence verified" in result.stdout


def set_corpus_row(bundle: Bundle, row: dict[str, object]) -> None:
    verdict = gate.text(row["verdict"])
    bundle.write("corpus-report/example.json", row)
    bundle.alter(
        "corpus-report/summary.json",
        lambda summary: summary.update(results=[row], counts={verdict: 1}),
    )
    bundle.write(
        "corpus.log", f"{verdict} example files={row.get('changed_files', 1)} refactor= 1.0s\n"
    )
    bundle.alter(
        "corpus-exit.json",
        lambda record: record.update(
            status=int(verdict not in {"PASS", "NO_CHANGE", "BROKEN_KNOWN"})
        ),
    )


def reviewed_exception(bundle: Bundle, category: str, disposition: str) -> None:
    decision = {
        "reason": "Explicit synthetic review",
        "disposition": disposition,
        "evidence": [gate.table(bundle.value["records"])["corpus-logs/example-before.log"]],
    }
    bundle.review(lambda review: review.update({category: {"example": decision}}))


def test_reviewed_no_change_refactor_refusal_is_admissible(bundle: Bundle) -> None:
    row = gate.table(bundle.read("corpus-report/example.json"))
    row.update(
        verdict="CRASH",
        changed_files=0,
        after=None,
        refactor={"returncode": 1, "log": "/logs/example-refactor.log"},
    )
    set_corpus_row(bundle, row)
    with pytest.raises(gate.EvidenceError, match="reviewed_nonaccepted"):
        bundle.check()
    reviewed_exception(bundle, "reviewed_nonaccepted", "accepted-refusal")
    bundle.check()


@pytest.mark.parametrize(
    "mutation", ["baseline_error", "timeout", "killed", "after", "changed", "incomplete"]
)
def test_review_text_cannot_admit_incomplete_or_modified_refusals(
    bundle: Bundle, mutation: str
) -> None:
    row = gate.table(bundle.read("corpus-report/example.json"))
    row.update(
        verdict="CRASH",
        changed_files=0,
        after=None,
        refactor={"returncode": 1, "log": "/logs/example-refactor.log"},
    )
    if mutation == "baseline_error":
        row["verdict"] = "BASELINE_ERROR"
    elif mutation == "timeout":
        row["verdict"] = "TIMEOUT"
    elif mutation == "killed":
        row["refactor"] = {"returncode": -9, "log": "/logs/example-refactor.log"}
    elif mutation == "after":
        row["after"] = row["baseline"]
    elif mutation == "changed":
        row["changed_files"] = 1
    else:
        bundle.write("corpus-logs/example-before.log", "Tests started but did not finish\n")
    set_corpus_row(bundle, row)
    reviewed_exception(bundle, "reviewed_nonaccepted", "accepted-refusal")
    with pytest.raises(gate.EvidenceError):
        bundle.check()


@pytest.mark.parametrize("mutation", ["none", "unexpected", "lost_baseline", "wrong_disposition"])
def test_known_limitations_are_bound_to_failed_identities(bundle: Bundle, mutation: str) -> None:
    row = gate.table(bundle.read("corpus-report/example.json"))
    row.update(verdict="BROKEN_KNOWN", detail="frame reflection", changed_files=1)
    for phase in ("baseline", "after"):
        step = gate.table(row[phase])
        step["returncode"] = 1
        row[phase] = step
    before = "FAILED test_case.py::test_old\n1 failed, 19 passed in 1.0s\n"
    after = "FAILED test_case.py::test_old\nFAILED test_case.py::test_frame\n2 failed, 18 passed in 1.0s\n"
    if mutation == "unexpected":
        after = after.replace("test_frame", "test_unexpected")
    elif mutation == "lost_baseline":
        after = "FAILED test_case.py::test_frame\n1 failed, 19 passed in 1.0s\n"
    bundle.write("corpus-logs/example-before.log", before)
    bundle.write("corpus-logs/example-after.log", after)
    set_corpus_row(bundle, row)
    reviewed_exception(
        bundle,
        "reviewed_known_failures",
        "upstream-baseline" if mutation == "wrong_disposition" else "known-limitation",
    )
    reviewed_exception(bundle, "reviewed_baseline_failures", "pre-existing-baseline")
    source = dict(bundle.source)
    source[
        "scripts/ecosystem/manifest.toml"
    ] += b'expect_broken="frame reflection"\nknown_failures=["test_case.py::test_frame"]\n'
    bundle.alter(
        "corpus-environment.json",
        lambda record: record.update(
            manifest_sha256=gate.digest(source["scripts/ecosystem/manifest.toml"])
        ),
    )
    records = gate.Records(
        bundle.root,
        {name: gate.FileRef.read(ref) for name, ref in gate.table(bundle.value["records"]).items()},
    )
    review = gate.table(bundle.read("review.json"))
    wheel = gate.text(review["wheel_sha256"])
    if mutation == "none":
        gate.verify_corpus(records, bundle.commit, wheel, source, review)
    else:
        with pytest.raises(gate.EvidenceError):
            gate.verify_corpus(records, bundle.commit, wheel, source, review)


@pytest.mark.parametrize("check", ["twine", "self-import"])
def test_distribution_and_transformed_source_proofs_are_not_optional(
    bundle: Bundle, check: str
) -> None:
    if check == "twine":
        bundle.write("twine-313.log", f"code_towel-{VERSION}-py3-none-any.whl: PASSED\n")
    else:
        bundle.write("selfdogfood/imported-source.log", "/venv/site-packages/towel/__init__.py\n")
    with pytest.raises(gate.EvidenceError):
        bundle.check()


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_hash_bound_coverage_xml_cannot_declare_entities(bundle: Bundle, encoding: str) -> None:
    """A matching evidence digest establishes identity, not permission to expand XML entities."""
    payload = (
        '<!DOCTYPE coverage [<!ENTITY injected "not coverage">]><coverage>&injected;</coverage>'
    )
    records = gate.table(bundle.value["records"])
    records["coverage-3.11.xml"] = bundle.ref("coverage-3.11.xml", payload.encode(encoding))
    bundle.value["records"] = records
    with pytest.raises(gate.EvidenceError, match="Unsafe coverage XML"):
        bundle.check()
