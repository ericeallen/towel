#!/usr/bin/env python3
# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by law or agreed in writing, software distributed under the
# License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS
# OF ANY KIND, either express or implied. See the License for the specific
# language governing permissions and limitations under the License.

"""Verify completed, hash-bound local release evidence without running or publishing it.

Schema 1 is documented in docs/RELEASING.md. Records are observations, not signed
attestations: this catches incomplete/stale/inconsistent evidence, not fabrication
by someone who can rewrite all records. No retained command is ever executed.
"""

from __future__ import annotations

import argparse
import base64
from collections import Counter
import configparser
import csv
from dataclasses import dataclass
from datetime import datetime
from email.parser import BytesParser
from fnmatch import fnmatchcase
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import sys
import tarfile
import tomllib
from typing import cast, Mapping, Sequence
from xml.etree import ElementTree
import zipfile

from defusedxml import ElementTree as safe_xml
from defusedxml.common import DefusedXmlException

# Keep direct script invocation as portable as ``python -m scripts...``.
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import ecosystem_check as ecosystem  # noqa: E402

VERSIONS = ("3.11", "3.12", "3.13")
REQUIRED_RECORDS = (
    "source.json",
    "run-matrix.sh",
    "artifacts.json",
    "build-results.json",
    "quality-313.log",
    "twine-313.log",
    "dependencies-audit-command.json",
    "dependencies-dev-audit.log",
    "dependencies-dev.txt",
    "diagrams-command.json",
    "diagrams-writable-cache.log",
    "fuzz-results.json",
    "fuzz-grammar.log",
    "fuzz-scope.log",
    "wheel-smoke/results.json",
    "self-generation-result.json",
    "selfdogfood/source.json",
    "selfdogfood/results.json",
    "selfdogfood/post-test-integrity.json",
    "selfdogfood/tests.log",
    "selfdogfood/imported-source.log",
    "selfdogfood/typecheck.log",
    "corpus-report/summary.json",
    "corpus-command.json",
    "corpus-exit.json",
    "corpus.log",
    "corpus-environment.json",
    "sdist-docs-result.json",
    "sdist-docs-tests.log",
) + tuple(
    name
    for version in VERSIONS
    for name in (
        f"matrix-{version}-result.json",
        f"matrix-{version}.log",
        f"coverage-{version}.xml",
        f"wheel-smoke/{version}-bare/commands.json",
        f"wheel-smoke/{version}-extras/commands.json",
        f"wheel-smoke/{version}-extras/cold-check.json",
    )
)


class EvidenceError(ValueError):
    """Evidence cannot establish the requested local release gate."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise EvidenceError(message)


def table(value: object) -> dict[str, object]:
    require(isinstance(value, dict), "Expected a JSON object")
    if not isinstance(value, dict):
        raise EvidenceError("Expected a JSON object")
    require(all(isinstance(key, str) for key in value), "Object keys must be strings")
    return {str(key): item for key, item in value.items()}


def rows(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise EvidenceError("Expected a JSON array of objects")
    return [table(item) for item in value]


def text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceError("Expected a nonempty string")
    return value


def strings(value: object) -> list[str]:
    if not isinstance(value, list):
        raise EvidenceError("Expected a string array")
    return [text(item) for item in value]


def integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise EvidenceError("Expected an integer, not a boolean or string")
    return value


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    require(len(pairs) == len(dict(pairs)), "Duplicate JSON key")
    return dict(pairs)


def decode(data: bytes) -> object:
    value: object = json.loads(data, object_pairs_hook=unique_object)
    return value


@dataclass(frozen=True)
class FileRef:
    path: str
    sha256: str

    @classmethod
    def read(cls, value: object) -> FileRef:
        row = table(value)
        require(set(row) == {"path", "sha256"}, "File reference needs only path and sha256")
        path, sha256 = text(row["path"]), text(row["sha256"])
        parts = PurePosixPath(path)
        require(
            not parts.is_absolute() and ".." not in parts.parts, f"Unsafe evidence path: {path}"
        )
        require(bool(re.fullmatch(r"[0-9a-f]{64}", sha256)), f"Invalid SHA256 for {path}")
        return cls(path, sha256)

    def bytes(self, root: Path) -> bytes:
        path = (root / self.path).resolve()
        require(path.is_relative_to(root.resolve()), f"Evidence escapes its bundle: {self.path}")
        require(path.is_file(), f"Missing evidence: {self.path}")
        data = path.read_bytes()
        require(digest(data) == self.sha256, f"Evidence hash mismatch: {self.path}")
        return data


@dataclass(frozen=True)
class Records:
    root: Path
    files: Mapping[str, FileRef]

    def bytes(self, name: str) -> bytes:
        require(name in self.files, f"Missing required record: {name}")
        ref = self.files[name]
        data = ref.bytes(self.root)
        return gzip.decompress(data) if ref.path.endswith(".gz") else data

    def text(self, name: str) -> str:
        return self.bytes(name).decode("utf-8")

    def value(self, name: str) -> object:
        return decode(self.bytes(name))

    def table(self, name: str) -> dict[str, object]:
        return table(self.value(name))


def git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)
    require(result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr.decode()}")
    return result.stdout


def source_tree(repo: Path, commit: str) -> dict[str, bytes]:
    require(bool(re.fullmatch(r"[0-9a-f]{40}", commit)), "Source commit must be a full Git SHA")
    with tarfile.open(fileobj=io.BytesIO(git(repo, "archive", "--format=tar", commit))) as archive:
        result = {}
        for member in archive:
            if member.isfile():
                stream = archive.extractfile(member)
                require(stream is not None, f"Unreadable source member {member.name}")
                if stream is not None:
                    result[member.name] = stream.read()
            else:
                require(member.isdir(), f"Unsupported source link: {member.name}")
        return result


def completed(row: Mapping[str, object], *, timed: bool = True) -> None:
    require(integer(row.get("exit_status")) == 0, "A required command failed")
    if timed:
        start = datetime.fromisoformat(text(row.get("started")))
        end = datetime.fromisoformat(text(row.get("finished")))
        require(
            start.tzinfo is not None and end.tzinfo is not None and end >= start,
            "Incomplete or invalid command interval",
        )


def suite(log: str) -> None:
    summaries = re.findall(r"^.*\b\d+ passed\b.*\bin [\d.]+s.*$", log, re.MULTILINE)
    require(len(summaries) == 1, "Expected one completed pytest summary")
    require(
        not re.search(r"\b(?:failed|error|errors|xpassed)\b", summaries[0], re.IGNORECASE),
        "Test summary contains failures",
    )
    require(bool(re.search(r"\b[1-9]\d* passed\b", summaries[0])), "No tests passed")


MATRIX_COMMANDS = (
    "uv run --frozen coverage erase",
    "uv run --frozen coverage run -m pytest -q --durations=20",
    "uv run --frozen coverage combine",
    "uv run --frozen coverage report --fail-under=85",
    'uv run --frozen coverage xml -o "/evidence/coverage-$version.xml"',
)
MATRIX_FLAGS = {
    "UV_LINK_MODE": "hardlink",
    "bytecode": "on",
    "coverage": "run -m pytest -q --durations=20; combine; report --fail-under=85",
}


def verify_matrix(records: Records, commit: str, runtime: str, source: Mapping[str, bytes]) -> None:
    driver = records.text("run-matrix.sh")
    require(
        all(command in driver.splitlines() for command in MATRIX_COMMANDS),
        "Matrix driver omitted full-suite coverage commands",
    )
    require(
        "assert actual == expected" in driver and "src/towel/__init__.py" in driver,
        "Matrix driver omitted import-source proof",
    )
    expected_files = {
        name.removeprefix("src/towel/")
        for name in source
        if name.startswith("src/towel/") and name.endswith(".py")
    }
    for version in VERSIONS:
        row = records.table(f"matrix-{version}-result.json")
        completed(row)
        require(
            row.get("commit") == commit and row.get("runtime_tree") == runtime,
            f"Matrix {version} source mismatch",
        )
        require(
            row.get("python_job") == version and text(row.get("python")).startswith(version + "."),
            f"Matrix {version} interpreter mismatch",
        )
        require(row.get("flags") == MATRIX_FLAGS, f"Matrix {version} flags mismatch")
        log = records.text(f"matrix-{version}.log")
        require(
            decode(log.splitlines()[-1].encode()) == row, f"Matrix {version} result/log mismatch"
        )
        suite(log)
        # File hashes bind the observation; they do not make arbitrary XML safe.
        # defusedxml is present in the frozen development/audit environment.
        try:
            coverage = cast(
                ElementTree.Element,
                safe_xml.fromstring(records.bytes(f"coverage-{version}.xml"), forbid_dtd=True),
            )
        except DefusedXmlException as error:
            raise EvidenceError(f"Unsafe coverage XML: {error}") from error
        locations = coverage.findall("./sources/source")
        require(len(locations) == 1, f"Matrix {version} coverage source missing/ambiguous")
        location = text(locations[0].text)
        require(
            location.endswith("/src/towel") and location + "/__init__.py" in log.splitlines(),
            f"Matrix {version} installed import does not match coverage source",
        )
        classes = coverage.findall("./packages/package/classes/class")
        require(
            len(classes) == len(expected_files)
            and {item.attrib.get("filename") for item in classes} == expected_files,
            f"Matrix {version} coverage file inventory mismatch",
        )
        per_file = {}
        for item in classes:
            name = item.attrib["filename"]
            lines = item.findall("./lines/line")
            numbers = [int(line.attrib["number"]) for line in lines]
            hits = [int(line.attrib["hits"]) for line in lines]
            require(
                len(numbers) == len(set(numbers))
                and all(n > 0 for n in numbers)
                and all(n >= 0 for n in hits),
                f"Matrix {version} invalid coverage lines: {name}",
            )
            per_file["src/towel/" + name] = (len(lines), sum(hit == 0 for hit in hits))
        total = sum(count for count, _ in per_file.values())
        missing = sum(count for _, count in per_file.values())
        require(
            total > 0
            and int(coverage.attrib["lines-valid"]) == total
            and int(coverage.attrib["lines-covered"]) == total - missing,
            f"Matrix {version} coverage totals mismatch",
        )
        console = re.findall(
            r"^(src/towel/\S+|TOTAL)\s+(\d+)\s+(\d+)\s+\d+%\s*$", log, re.MULTILINE
        )
        require(
            len(console) == len(per_file) + 1
            and {name: (int(count), int(missed)) for name, count, missed in console}
            == {**per_file, "TOTAL": (total, missing)},
            f"Matrix {version} console/XML coverage mismatch",
        )
        require((total - missing) / total >= 0.85, f"Matrix {version} coverage below 85%")


def named_steps(value: object, names: Sequence[str], key: str = "name") -> list[dict[str, object]]:
    steps = rows(value)
    require(
        [step.get(key) for step in steps] == list(names), f"Missing/reordered steps: {list(names)}"
    )
    for step in steps:
        completed(step, timed=False)
    return steps


def verify_commands(records: Records, version: str) -> None:
    steps = named_steps(
        records.value("build-results.json"),
        (
            "quality-313",
            "build-313",
            "artifact-inspection-313",
            "distribution-contents",
            "twine-313",
        ),
    )
    quality = records.text("quality-313.log")
    for marker in (
        "black --check",
        "flake8",
        "mypy",
        "bandit",
        "Success: no issues found",
        "No issues identified.",
        "Quality checks passed.",
    ):
        require(marker in quality, f"Missing quality evidence: {marker}")
    twine_args = strings(steps[-1].get("args"))
    distributions = {f"code_towel-{version}-py3-none-any.whl", f"code_towel-{version}.tar.gz"}
    require(
        {"twine", "check", "--strict"} <= set(twine_args)
        and {Path(arg).name for arg in twine_args if arg.endswith((".whl", ".tar.gz"))}
        == distributions,
        "Strict Twine command did not check both exact distributions",
    )
    twine = records.text("twine-313.log")
    require(
        len(re.findall(r": PASSED\s*(?:$|\n)", twine)) == 2
        and all(name + ": PASSED" in twine for name in distributions),
        "Missing strict distribution check results",
    )
    completed(records.table("dependencies-audit-command.json"), timed=False)
    require(
        "No known vulnerabilities found" in records.text("dependencies-dev-audit.log"),
        "Dependency audit did not complete cleanly",
    )
    require(bool(records.text("dependencies-dev.txt").strip()), "Missing audited dependency pins")
    completed(records.table("diagrams-command.json"), timed=False)
    require(
        bool(
            re.fullmatch(
                r"[1-9]\d* diagram\(s\) checked, 0 broken\s*",
                records.text("diagrams-writable-cache.log"),
            )
        ),
        "Diagram check incomplete",
    )
    steps = named_steps(records.value("fuzz-results.json"), ("grammar", "scope"), "family")
    for step in steps:
        completed(step)
        arguments = strings(step.get("args"))
        require("--count" in arguments, "Missing fuzz count")
        count = int(arguments[arguments.index("--count") + 1])
        require(
            count >= 2000 and "--seed" in arguments, "Fuzzing needs 2000 cases and retained seed"
        )
        require(
            bool(
                re.search(
                    r"^Done in [\d.]+s: 0 failure\(s\)\.\s*$",
                    records.text(f"fuzz-{step['family']}.log"),
                    re.MULTILINE,
                )
            ),
            "Fuzz run incomplete or failed",
        )


def archive_files(path: Path) -> dict[str, bytes]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist() if not n.endswith("/")]
            require(len(set(names)) == len(names), "Duplicate wheel member")
            return {name: archive.read(name) for name in names}
    with tarfile.open(path) as archive:
        result = {}
        roots = set()
        for member in archive:
            parts = PurePosixPath(member.name).parts
            require(
                len(parts) >= 1 and ".." not in parts and not member.name.startswith("/"),
                "Unsafe sdist member",
            )
            roots.add(parts[0])
            if member.isfile():
                name = "/".join(parts[1:])
                require(name not in result, "Duplicate sdist member")
                stream = archive.extractfile(member)
                if stream is None:
                    raise EvidenceError(f"Unreadable sdist member: {name}")
                result[name] = stream.read()
            else:
                require(member.isdir(), "Unsupported sdist link")
        require(len(roots) == 1, "Sdist needs one root")
        return result


def sdist_sources(source: Mapping[str, bytes]) -> set[str]:
    """Apply the repository's manifest to tracked files without running a build."""
    included = {name for name in source if name.startswith("src/towel/")}
    included.update(
        set(source) & {"pyproject.toml", "setup.cfg", "setup.py", "MANIFEST.in", "README.md"}
    )
    for line in source.get("MANIFEST.in", b"").decode().splitlines():
        parts = shlex.split(line, comments=True)
        if not parts:
            continue
        operation, *patterns = parts
        if operation in {"include", "exclude"}:
            found = {
                name for name in source if any(fnmatchcase(name, pattern) for pattern in patterns)
            }
            if operation == "include":
                included.update(found)
            else:
                included.difference_update(found)
        elif operation == "recursive-include" and len(patterns) >= 2:
            included.update(
                name
                for name in source
                if name.startswith(patterns[0] + "/")
                and any(fnmatchcase(PurePosixPath(name).name, pattern) for pattern in patterns[1:])
            )
        elif operation == "prune" and len(patterns) == 1:
            included = {name for name in included if not name.startswith(patterns[0] + "/")}
        elif operation == "global-exclude":
            included = {
                name
                for name in included
                if not any(
                    fnmatchcase(part, pattern)
                    for part in PurePosixPath(name).parts
                    for pattern in patterns
                )
            }
        else:
            raise EvidenceError(f"Unsupported source manifest rule: {line}")
    return included


def verify_artifacts(
    records: Records,
    references: Mapping[str, object],
    source: Mapping[str, bytes],
    commit: str,
    version: str,
) -> tuple[str, str, set[str]]:
    require(set(references) == {"wheel", "sdist"}, "Exactly one wheel and sdist are required")
    refs = {name: FileRef.read(value) for name, value in references.items()}
    declared = records.table("artifacts.json")
    require(declared.get("commit") == commit, "Artifact source commit mismatch")
    inventory = table(declared.get("artifacts"))
    packages = {n.removeprefix("src/"): b for n, b in source.items() if n.startswith("src/towel/")}
    require(bool(packages), "Source has no runtime package")
    project = table(tomllib.loads(source["pyproject.toml"].decode())["project"])
    shipped = set()
    for kind, ref in refs.items():
        data = ref.bytes(records.root)
        matches = [
            table(value)
            for name, value in inventory.items()
            if Path(name).name == Path(ref.path).name
        ]
        require(
            len(matches) == 1
            and matches[0].get("sha256") == ref.sha256
            and integer(matches[0].get("bytes")) == len(data),
            f"{kind} inventory mismatch",
        )
        files = archive_files(records.root / ref.path)
        prefix = "towel/" if kind == "wheel" else "src/towel/"
        runtime = {
            n if kind == "wheel" else n.removeprefix("src/"): b
            for n, b in files.items()
            if n.startswith(prefix)
        }
        require(runtime == packages, f"{kind} runtime differs from the validated source")
        metadata = (
            [b for n, b in files.items() if n.endswith(".dist-info/METADATA")]
            if kind == "wheel"
            else [files.get("PKG-INFO", b"")]
        )
        require(len(metadata) == 1, f"Missing/ambiguous {kind} metadata")
        parsed = BytesParser().parsebytes(metadata[0])
        require(
            parsed.get("Name") == "code-towel" and parsed.get("Version") == version,
            f"{kind} package/version mismatch",
        )
        if kind == "wheel":
            require(
                all(name.startswith("towel/") or ".dist-info/" in name for name in files),
                "Wheel contains files outside the validated package/metadata",
            )
            require(
                parsed.get("Requires-Python") == project.get("requires-python"),
                "Wheel Python requirement mismatch",
            )
            require(
                set(parsed.get_all("Classifier", []))
                == set(strings(project.get("classifiers", []))),
                "Wheel advertised classifiers mismatch",
            )
            entry_files = [
                data for name, data in files.items() if name.endswith(".dist-info/entry_points.txt")
            ]
            declared_scripts = table(project.get("scripts", {}))
            require(
                len(entry_files) == int(bool(declared_scripts)),
                "Wheel entry points missing/ambiguous",
            )
            if entry_files:
                entries_config = configparser.ConfigParser()
                entries_config.read_string(entry_files[0].decode())
                require(
                    entries_config.has_section("console_scripts")
                    and dict(entries_config["console_scripts"]) == declared_scripts,
                    "Wheel console entry point mismatch",
                )
            require(
                metadata[0].split(b"\n\n", 1)[1] == source["README.md"], "Wheel README mismatch"
            )
            record_names = [n for n in files if n.endswith(".dist-info/RECORD")]
            require(len(record_names) == 1, "Wheel RECORD missing/ambiguous")
            record_name = record_names[0]
            entries = list(csv.reader(io.StringIO(files[record_name].decode())))
            require(
                len(entries) == len(files) and {row[0] for row in entries} == set(files),
                "Wheel RECORD does not cover the wheel",
            )
            for name, encoded, size in entries:
                if name == record_name:
                    require(encoded == size == "", "RECORD self hash must be empty")
                else:
                    actual = (
                        base64.urlsafe_b64encode(hashlib.sha256(files[name]).digest())
                        .rstrip(b"=")
                        .decode()
                    )
                    require(
                        encoded == f"sha256={actual}" and int(size) == len(files[name]),
                        f"Wheel RECORD mismatch: {name}",
                    )
        else:
            require(
                sdist_sources(source) <= set(files), "Sdist omits manifest-selected source files"
            )
            extras = set(files) - set(source) - {"PKG-INFO"}
            require(
                all(name.startswith("src/code_towel.egg-info/") for name in extras),
                "Sdist includes source not present in the validated commit",
            )
            for name in set(files) & set(source):
                if name == "setup.cfg":
                    old, new = configparser.ConfigParser(), configparser.ConfigParser()
                    old.read_string(source[name].decode())
                    new.read_string(files[name].decode())
                    if new.has_section("egg_info"):
                        require(
                            dict(new["egg_info"]) == {"tag_build": "", "tag_date": "0"},
                            "Sdist changes build tags",
                        )
                        new.remove_section("egg_info")
                    require(dict(old) == dict(new), "Sdist setup.cfg changes configuration")
                else:
                    require(files[name] == source[name], f"Sdist source mismatch: {name}")
            shipped.update(set(files) & set(source))
    return refs["wheel"].sha256, refs["sdist"].sha256, shipped


def verify_smokes(records: Records, wheel: str, archive: str, version: str) -> None:
    results = rows(records.value("wheel-smoke/results.json"))
    expected = {f"{v}-{mode}" for v in VERSIONS for mode in ("bare", "extras")}
    require(
        len(results) == len(expected) and {row.get("case") for row in results} == expected,
        "Incomplete clean-wheel matrix",
    )
    for row in results:
        case = text(row.get("case"))
        require(row.get("wheel_sha256") == wheel, f"Smoke {case} tested another wheel")
        installed = table(row.get("installed"))
        require(
            "/site-packages/towel/" in text(installed.get("source")),
            f"Smoke {case} imported outside the installed wheel",
        )
        require(
            text(installed.get("python")).startswith(case.split("-")[0] + "."),
            f"Smoke {case} interpreter mismatch",
        )
        require(
            table(installed.get("distributions")).get("code-towel") == version,
            f"Smoke {case} installed another version",
        )
        commands = rows(records.value(f"wheel-smoke/{case}/commands.json"))
        require(bool(commands), f"Smoke {case} has no commands")
        arguments = []
        for command in commands:
            require(integer(command.get("status")) == 0, f"Smoke {case} command failed")
            arguments.append(strings(command.get("args")))
        for token in ("install", "preview", "dry", "--version"):
            require(any(token in args for args in arguments), f"Smoke {case} omits {token}")
        require(
            any(
                "install" in args
                and any(
                    token.endswith(
                        f"code_towel-{version}-py3-none-any.whl"
                        + ("[format,types]" if case.endswith("extras") else "")
                    )
                    for token in args
                )
                for args in arguments
            ),
            f"Smoke {case} installed another artifact",
        )
        previews = [item for item in commands if "preview" in strings(item.get("args"))]
        require(
            any("Would apply 1 refactoring(s)" in text(item.get("stdout")) for item in previews),
            f"Smoke {case} has no successful preview",
        )
        programs = [
            text(item.get("stdout"))
            for item in commands
            if len(strings(item.get("args"))) == 2
            and Path(strings(item.get("args"))[-1]).name
            in {"program.py", "output.py", "cold-output.py"}
        ]
        require(
            len(programs) >= (3 if case.endswith("extras") else 2) and len(set(programs)) == 1,
            f"Smoke {case} runtime outputs differ or are missing",
        )
        if case.endswith("extras"):
            require(any("mypy" in args for args in arguments), f"Smoke {case} omitted checker")
            cold = records.table(f"wheel-smoke/{case}/cold-check.json")
            require(
                bool(strings(cold.get("resets"))) and cold.get("source") == installed.get("source"),
                f"Smoke {case} did not cold-check the installed wheel",
            )
    docs = records.table("sdist-docs-result.json")
    require(docs.get("archive_sha256") == archive, "Sdist tests exercised another archive")
    named_steps(docs.get("steps"), ("sync", "source", "tests"))
    suite(records.text("sdist-docs-tests.log"))


def verify_self(records: Records, commit: str, source: Mapping[str, bytes]) -> None:
    completed(records.table("self-generation-result.json"))
    result = records.table("selfdogfood/results.json")
    require(result.get("input_commit") == commit, "Self-dogfood source mismatch")
    steps = named_steps(result.get("results"), ("sync", "imported-source", "typecheck", "tests"))
    probe = strings(steps[1].get("command"))
    require(
        "-c" in probe and any("assert p.is_relative_to(Path.cwd()/'src')" in arg for arg in probe),
        "Self-dogfood imported-source command omitted source proof",
    )
    require(
        any(
            line.endswith("/selfdogfood/project/src/towel/__init__.py")
            for line in records.text("selfdogfood/imported-source.log").splitlines()
        ),
        "Self-dogfood did not import the transformed source",
    )
    suite(records.text("selfdogfood/tests.log"))
    require(
        "Success: no issues found" in records.text("selfdogfood/typecheck.log"),
        "Self typing incomplete",
    )
    manifest = records.table("selfdogfood/source.json")
    require(manifest.get("input_commit") == commit, "Transformed source input mismatch")
    rows_ = rows(manifest.get("runtime_files"))
    expected = {name for name in source if name.startswith("src/towel/")}
    require(
        len(rows_) == len(expected) and {row.get("path") for row in rows_} == expected,
        "Self-dogfood did not include all runtime files",
    )
    for row in rows_:
        name = text(row.get("path"))
        require(row.get("before") == digest(source[name]), f"Self input hash mismatch: {name}")
        require(
            bool(re.fullmatch(r"[0-9a-f]{64}", text(row.get("after")))), "Missing transformed hash"
        )
    integrity = records.table("selfdogfood/post-test-integrity.json")
    require(
        integrity.get("input_commit") == commit
        and integer(integrity.get("transformed_runtime_files_unchanged_by_test_run"))
        == len(expected)
        and integrity.get("all_generated_runtime_files_included") is True,
        "Self-dogfood integrity incomplete",
    )


def verify_review(root: Path, value: object) -> dict[str, object]:
    review = table(decode(FileRef.read(value).bytes(root)))
    text(review.get("reviewer"))
    require(review.get("open_findings") == [], "Release review has missing or open findings")
    require(bool(FileRef.read(review.get("report")).bytes(root).strip()), "Empty review report")
    return review


def phase_outcome(
    records: Records, raw: object, project: Mapping[str, object]
) -> tuple[ecosystem.TestOutcome, frozenset[str]]:
    """Recompute a completed test outcome from retained output, not its summary field."""
    phase = table(raw)
    output = records.text("corpus-logs/" + Path(text(phase.get("log"))).name)
    outcome = ecosystem._test_outcome(output)
    if outcome is None:
        raise EvidenceError("Corpus phase has no completed test outcome")
    status = integer(phase.get("returncode"))
    if outcome.returncode:
        allowed = project.get("failure_exit_codes", [1])
        require(
            isinstance(allowed, list) and status > 0 and status in allowed,
            "Corpus test failure has an invalid process status",
        )
    else:
        require(status == 0, "Corpus test tally disagrees with process status")
    failed = frozenset(ecosystem._failed_test_ids(output))
    require(not outcome.returncode or bool(failed), "Failed suite has no failed test identities")
    return ecosystem.TestOutcome(outcome.summary, status, outcome.collected), failed


def verify_pass(records: Records, row: Mapping[str, object], project: Mapping[str, object]) -> None:
    before = phase_outcome(records, row.get("baseline"), project)
    after = phase_outcome(records, row.get("after"), project)
    require(before[0].collected == after[0].collected, "PASS changed the collected test count")
    if before == after:
        return
    # A flaky initial difference requires both isolated and whole-suite retests.
    name = text(row.get("name"))
    retest = records.table(f"corpus-logs/{name}-retest.json")
    ids = set(strings(retest.get("test_ids")))
    require(ids == before[1] ^ after[1], "Retest does not cover every initial failure difference")
    first = phase_outcome(records, retest.get("before"), project)
    second = phase_outcome(records, retest.get("after"), project)
    require(
        first == second and first[0].collected == len(ids) and first[1] <= ids,
        "Isolated retests do not establish the same outcome",
    )
    full = table(retest.get("full"))
    require(
        full.get("initial_before") == row.get("baseline")
        and full.get("initial_after") == row.get("after"),
        "Retest refers to another initial run",
    )
    first = phase_outcome(records, full.get("before"), project)
    second = phase_outcome(records, full.get("after"), project)
    require(
        first == second and first[0].collected == before[0].collected,
        "Full retests do not establish the same complete outcome",
    )


def verify_corpus(
    records: Records,
    commit: str,
    wheel: str,
    source: Mapping[str, bytes],
    review: Mapping[str, object],
) -> None:
    manifest_bytes = source["scripts/ecosystem/manifest.toml"]
    projects = rows(tomllib.loads(manifest_bytes.decode()).get("project"))
    manifest = {text(row.get("name")): row for row in projects}
    require(bool(manifest) and len(manifest) == len(projects), "Empty/duplicate corpus manifest")
    summary = records.table("corpus-report/summary.json")
    require(
        summary.get("towel") == commit and summary.get("typing_mode") == "default",
        "Corpus source/typing mode mismatch",
    )
    require(table(summary.get("candidate")).get("sha256") == wheel, "Corpus tested another wheel")
    results = rows(summary.get("results"))
    require(
        len(results) == len(manifest) and {row.get("name") for row in results} == set(manifest),
        "Corpus incomplete or contains duplicate/unexpected projects",
    )
    counts = dict(Counter(text(row.get("verdict")) for row in results))
    observed_counts = {key: integer(value) for key, value in table(summary.get("counts")).items()}
    require(observed_counts == counts, "Corpus verdict totals mismatch")
    console = re.findall(
        r"^([A-Z_]+)\s+([\w.-]+)\s+files=\d+\s+refactor=\s*[\d.]+s.*$",
        records.text("corpus.log"),
        re.MULTILINE,
    )
    require(
        len(console) == len(results)
        and dict((name, verdict) for verdict, name in console)
        == {text(row.get("name")): row.get("verdict") for row in results},
        "Corpus console mismatch",
    )
    categories: dict[str, set[str]] = {
        name: set()
        for name in (
            "reviewed_nonaccepted",
            "reviewed_fallbacks",
            "reviewed_known_failures",
            "reviewed_baseline_failures",
        )
    }
    for row in results:
        name, verdict = text(row.get("name")), text(row.get("verdict"))
        require(
            records.table(f"corpus-report/{name}.json") == row, f"Corpus detail mismatch: {name}"
        )
        pin = text(manifest[name].get("rev"))
        require(
            bool(re.fullmatch(r"[0-9a-f]{40}", text(row.get("commit"))))
            and (pin == "HEAD" or row.get("commit") == pin),
            f"Corpus input revision mismatch: {name}",
        )
        require(
            verdict in {"PASS", "NO_CHANGE", "BROKEN_KNOWN", "CRASH", "UNSUPPORTED"},
            f"Incomplete or unaccepted corpus verdict: {name}/{verdict}",
        )
        before = phase_outcome(records, row.get("baseline"), manifest[name])
        if verdict in {"CRASH", "UNSUPPORTED"}:
            require(
                row.get("after") is None
                and integer(row.get("changed_files")) == 0
                and 0 < integer(table(row.get("refactor")).get("returncode")) < 128,
                f"Reviewed refusal must be a completed no-change refactor refusal: {name}",
            )
            categories["reviewed_nonaccepted"].add(name)
        if row.get("fallback"):
            categories["reviewed_fallbacks"].add(name)
        if verdict == "BROKEN_KNOWN":
            require(
                manifest[name].get("expect_broken") == row.get("detail"),
                f"Unpinned known failure: {name}",
            )
            patterns = strings(manifest[name].get("known_failures"))
            after = phase_outcome(records, row.get("after"), manifest[name])
            introduced = after[1] - before[1]
            require(
                before[0].collected == after[0].collected
                and before[1] <= after[1]
                and bool(introduced)
                and all(any(pattern in test for pattern in patterns) for test in introduced),
                f"Known limitation changed unpinned failures or test count: {name}",
            )
            categories["reviewed_known_failures"].add(name)
        if verdict == "PASS":
            verify_pass(records, row, manifest[name])
        elif verdict == "NO_CHANGE":
            require(
                row.get("after") is None and integer(row.get("changed_files")) == 0,
                f"NO_CHANGE contains transformed-suite claims: {name}",
            )
        for phase in ("baseline", "refactor", "after"):
            step = row.get(phase)
            if step is None:
                require(verdict != "PASS", f"PASS omitted {phase}: {name}")
                continue
            detail = table(step)
            log = records.bytes("corpus-logs/" + Path(text(detail.get("log"))).name)
            require(bool(log.strip()), f"Empty corpus phase log: {name}/{phase}")
            status = integer(detail.get("returncode"))
            if phase == "baseline" and status != 0:
                categories["reviewed_baseline_failures"].add(name)
            if phase == "refactor" and verdict in {"PASS", "NO_CHANGE", "BROKEN_KNOWN"}:
                require(status == 0, f"Accepted refactor failed: {name}")
    dispositions = {
        "reviewed_nonaccepted": "accepted-refusal",
        "reviewed_fallbacks": "typed-fallback",
        "reviewed_known_failures": "known-limitation",
        "reviewed_baseline_failures": "pre-existing-baseline",
    }
    for category, names in categories.items():
        decisions = table(review.get(category))
        require(set(decisions) == names, f"Unreviewed or stale corpus category: {category}")
        for name, raw in decisions.items():
            decision = table(raw)
            text(decision.get("reason"))
            require(
                decision.get("disposition") == dispositions[category],
                f"Unaccepted corpus decision: {name}",
            )
            refs = rows(decision.get("evidence"))
            require(bool(refs), f"Review has no evidence: {name}")
            for ref in refs:
                require(
                    bool(FileRef.read(ref).bytes(records.root)), f"Empty review evidence: {name}"
                )
    exit_ = records.table("corpus-exit.json")
    require(
        exit_.get("oom_killed") is False
        and integer(exit_.get("status")) == int(bool(categories["reviewed_nonaccepted"])),
        "Corpus incomplete/resource-killed/exit mismatch",
    )
    launch = records.table("corpus-command.json")
    require(launch.get("commit") == commit, "Corpus launch source mismatch")
    require(
        datetime.fromisoformat(text(exit_.get("finished")))
        > datetime.fromisoformat(text(launch.get("started"))),
        "Corpus completion time missing/invalid",
    )
    require(
        records.table("corpus-environment.json").get("manifest_sha256") == digest(manifest_bytes),
        "Corpus used another harness manifest",
    )


def amendment_path(name: str, old: Mapping[str, bytes], new: Mapping[str, bytes]) -> bool:
    if name == "justfile":
        # Only the release block may change without rerunning the old build/test commands.
        def outside(value: bytes) -> tuple[bytes, bytes]:
            head, marker, rest = value.partition(b"# === Release ===")
            _, end, tail = rest.partition(b"# === Maintenance ===")
            require(bool(marker and end), "Cannot bound justfile release-only change")
            return head, tail

        return outside(old.get(name, b"")) == outside(new.get(name, b""))
    if name == "scripts/verify_release_evidence.py":
        return name not in old
    if name.startswith("tests/"):
        return not name.startswith("tests/differential/") and Path(name).name != "conftest.py"
    return name.startswith("docs/") or ("/" not in name and name.endswith(".md"))


def verify_amendments(
    root: Path,
    raw: object,
    old: Mapping[str, bytes],
    new: Mapping[str, bytes],
    shipped: set[str],
    releasing: bool,
) -> None:
    changed = {
        name: digest(new[name]) if name in new else None
        for name in set(old) | set(new)
        if old.get(name) != new.get(name)
    }
    if not changed:
        require(raw is None, "Stale amendments on unchanged source")
        return
    amendment = table(raw)
    require(
        amendment.get("files") == changed, "Amendments must enumerate every exact source change"
    )
    for name in changed:
        require(
            amendment_path(name, old, new),
            f"Runtime/config/harness change invalidates evidence: {name}",
        )
        require(
            not releasing or name not in shipped,
            f"Release artifact predates shipped amendment: {name}",
        )
    require(bool(FileRef.read(amendment.get("report")).bytes(root)), "Missing amendment review")
    checks = rows(amendment.get("checks"))
    coverage: dict[str, set[str]] = {name: set() for name in changed}
    for check in checks:
        require(check.get("files") == changed, "Supplemental check source hashes mismatch")
        completed(check)
        kind = text(check.get("kind"))
        args = strings(check.get("command"))
        log = FileRef.read(check.get("log")).bytes(root).decode()
        require(kind in {"tests", "quality", "diagrams"}, "Unknown supplemental check kind")
        if kind == "tests":
            require(
                any(token in args for token in ("pytest", "test")),
                "Supplemental tests command missing",
            )
            suite(log)
        elif kind == "quality":
            require(
                "check" in args and "Quality checks passed." in log,
                "Supplemental quality incomplete",
            )
        else:
            require(
                any("mermaid" in token or token == "check-diagrams" for token in args)
                and bool(re.search(r"[1-9]\d* diagram\(s\) checked, 0 broken", log)),
                "Supplemental diagrams incomplete",
            )
        for name in strings(check.get("covers")):
            require(name in changed, f"Supplemental check covers unchanged file: {name}")
            coverage[name].add(kind)
    for name, kinds in coverage.items():
        required = {"tests", "quality"} if name.endswith(".py") or name == "justfile" else {"tests"}
        require(
            required <= kinds, f"Missing supplemental checks for {name}: {sorted(required - kinds)}"
        )


def verify(
    repo: Path, bundle_path: Path, version: str, *, validation_only: bool = False
) -> tuple[Path, Path]:
    require(
        not git(repo, "status", "--porcelain", "--untracked-files=all").strip(),
        "Release checkout must be clean",
    )
    bundle = table(decode(bundle_path.read_bytes()))
    require(integer(bundle.get("schema")) == 1, "Unsupported evidence schema")
    require(bundle.get("version") == version, "Requested version differs from evidence")
    purpose = bundle.get("purpose")
    require(purpose in {"validation", "release"}, "Evidence purpose missing")
    require(
        validation_only or purpose == "release",
        "Validation-only artifacts are not release candidates",
    )
    commit = text(bundle.get("validation_commit"))
    source = source_tree(repo, commit)
    head = git(repo, "rev-parse", "HEAD").decode().strip()
    current = source_tree(repo, head)
    for name, expected in current.items():
        path = repo / name
        require(
            path.is_file() and not path.is_symlink() and path.read_bytes() == expected,
            f"Tracked working bytes differ from HEAD: {name}",
        )
    require(
        tomllib.loads(current["pyproject.toml"].decode())["project"]["version"] == version,
        "Choose and commit the version before validating a release",
    )
    require(
        tomllib.loads(source["pyproject.toml"].decode())["project"]["version"] == version,
        "Version changed after frozen validation",
    )
    records = Records(
        bundle_path.resolve().parent,
        {name: FileRef.read(ref) for name, ref in table(bundle.get("records")).items()},
    )
    for name in REQUIRED_RECORDS:
        records.bytes(name)
    frozen = records.table("source.json")
    runtime = git(repo, "rev-parse", f"{commit}:src/towel").decode().strip()
    require(
        frozen.get("commit") == commit and frozen.get("runtime_tree") == runtime,
        "Frozen source identity mismatch",
    )
    hashes = {
        name: digest(data)
        for name, data in source.items()
        if name.startswith("src/towel/") and name.endswith(".py")
    }
    require(
        frozen.get("runtime_sha256") == hashes
        and frozen.get("py_typed_sha256") == digest(source["src/towel/py.typed"]),
        "Frozen runtime hashes mismatch",
    )
    if not validation_only:
        for row in (frozen, records.table("artifacts.json")):
            require(
                not re.search(
                    r"validation.only|never upload|not for upload|local validation",
                    str(row.get("publication_status", "")),
                    re.IGNORECASE,
                ),
                "Artifacts explicitly marked not for publication",
            )
    wheel, archive, shipped = verify_artifacts(
        records, table(bundle.get("artifacts")), source, commit, version
    )
    verify_amendments(
        records.root, bundle.get("amendments"), source, current, shipped, not validation_only
    )
    verify_matrix(records, commit, runtime, source)
    verify_commands(records, version)
    verify_smokes(records, wheel, archive, version)
    verify_self(records, commit, source)
    review = verify_review(records.root, bundle.get("review"))
    require(
        review.get("validation_commit") == commit and review.get("wheel_sha256") == wheel,
        "Review source/artifact identity mismatch",
    )
    verify_corpus(records, commit, wheel, source, review)
    artifacts = table(bundle["artifacts"])
    return (
        records.root / FileRef.read(artifacts["wheel"]).path,
        records.root / FileRef.read(artifacts["sdist"]).path,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    parser.add_argument("evidence", type=Path, help="Schema-1 release-evidence.json")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--validation-only",
        action="store_true",
        help="Validate local evidence; never authorize upload",
    )
    args = parser.parse_args(argv)
    try:
        wheel, archive = verify(
            args.repo, args.evidence, args.version, validation_only=args.validation_only
        )
    except (
        EvidenceError,
        OSError,
        KeyError,
        TypeError,
        IndexError,
        UnicodeError,
        ValueError,
        zipfile.BadZipFile,
        tarfile.TarError,
        ElementTree.ParseError,
        EOFError,
        configparser.Error,
    ) as error:
        print(f"Release evidence refused: {error}", file=sys.stderr)
        return 1
    label = (
        "Local validation verified; artifacts are NOT approved for upload"
        if args.validation_only
        else "Local release evidence verified; publication remains a maintainer action"
    )
    print(f"{label}.\nWheel: {wheel}\nSource: {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
