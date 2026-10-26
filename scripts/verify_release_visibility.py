#!/usr/bin/env python3
# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Refuse publication while GitHub's default branch still presents an older release.

Read remote state through the authenticated GitHub CLI. A correct local checkout,
release branch or tag cannot establish what the repository front page presents.
This check does not mutate GitHub, upload artifacts or replace local validation.
"""

from __future__ import annotations

import argparse
import base64
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tomllib
from urllib.parse import quote, unquote, urlsplit


class VisibilityError(ValueError):
    """The public default branch does not establish release readiness."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VisibilityError(message)


def object_value(value: object) -> dict[str, object]:
    require(isinstance(value, dict), "Expected a GitHub JSON object")
    if not isinstance(value, dict):
        raise VisibilityError("Expected a GitHub JSON object")
    require(all(isinstance(key, str) for key in value), "Invalid JSON keys")
    return {str(key): item for key, item in value.items()}


def text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise VisibilityError("Missing nonempty text in release metadata")
    return value


def commit_id(value: object) -> str:
    result = text(value)
    require(bool(re.fullmatch(r"[0-9a-f]{40}", result)), "Expected a full commit SHA")
    return result


def github(endpoint: str) -> bytes:
    return subprocess.run(
        ["gh", "api", endpoint], check=True, capture_output=True, timeout=60
    ).stdout


def document_links(readme: str, repository: str, version: str) -> tuple[str, ...]:
    """Check every repository document link, retaining unrelated external links."""
    paths: set[str] = set()
    for destination in re.findall(r"https://github\.com/[^)\s>]+", readme):
        parsed = urlsplit(destination)
        prefix = f"/{repository}/"
        if not parsed.path.startswith(prefix):
            continue
        kind, _, target = parsed.path.removeprefix(prefix).partition("/")
        if kind not in {"blob", "tree"}:
            continue
        ref, separator, path = unquote(target).partition("/")
        require(ref == f"v{version}", f"Default-branch README has a stale link: {destination}")
        relative = PurePosixPath(path)
        require(
            bool(separator and path) and not relative.is_absolute() and ".." not in relative.parts,
            f"Unsafe documentation path: {destination}",
        )
        if kind == "blob":
            paths.add(path)
    require(bool(paths), "Default-branch README has no version-pinned documentation links")
    return tuple(sorted(paths))


def verify_documents(documents: Mapping[str, str], version: str, repository: str) -> None:
    project = object_value(tomllib.loads(documents["pyproject.toml"]).get("project"))
    require(project.get("version") == version, "Default-branch package version is stale")
    statuses = re.findall(r"\*\*Release status: ([^ ]+) \(beta\)\.\*\*", documents["README.md"])
    require(statuses == [version], "Default-branch README release status is stale or ambiguous")
    require(
        f"This source tree targets **{version}**" in documents["SECURITY.md"],
        "Default-branch security policy targets another version",
    )
    require(
        f"## [{version}]\n" in documents["CHANGELOG.md"],
        "Default-branch changelog lacks the release",
    )
    for path in document_links(documents["README.md"], repository, version):
        require(bool(documents.get(path, "").strip()), f"Missing default-branch document: {path}")


@dataclass(frozen=True)
class Visibility:
    default_branch: str
    head: str
    release_commit: str
    documents_checked: int


def verify(
    version: str,
    release_commit: str,
    repository: str = "ericeallen/towel",
    request: Callable[[str], bytes] = github,
) -> Visibility:
    """Inspect one pinned snapshot of the actual configured default branch."""
    require(bool(re.fullmatch(r"[\w.-]+/[\w.-]+", repository)), "Invalid GitHub repository")
    commit_id(release_commit)

    def read_object(endpoint: str) -> dict[str, object]:
        value: object = json.loads(request(endpoint))
        return object_value(value)

    prefix = f"repos/{repository}"
    branch = text(read_object(prefix).get("default_branch"))
    head = commit_id(read_object(f"{prefix}/commits/{quote(branch, safe='')}").get("sha"))
    comparison = read_object(f"{prefix}/compare/{release_commit}...{head}")
    require(
        comparison.get("status") in {"identical", "ahead"}
        and object_value(comparison.get("merge_base_commit")).get("sha") == release_commit,
        f"GitHub default branch {branch} does not contain validated release {release_commit}; "
        "promote the tested release before publishing",
    )

    def read_document(path: str) -> str:
        record = read_object(f"{prefix}/contents/{quote(path, safe='/')}?ref={head}")
        require(
            record.get("type") == "file" and record.get("encoding") == "base64",
            f"GitHub did not return complete file contents: {path}",
        )
        return base64.b64decode("".join(text(record.get("content")).split()), validate=True).decode(
            "utf-8"
        )

    documents = {
        path: read_document(path)
        for path in ("pyproject.toml", "README.md", "SECURITY.md", "CHANGELOG.md")
    }
    for path in document_links(documents["README.md"], repository, version):
        if path not in documents:
            documents[path] = read_document(path)
    verify_documents(documents, version, repository)
    return Visibility(branch, head, release_commit, len(documents))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--commit", help="Full validated release commit SHA")
    source.add_argument("--evidence", type=Path, help="Existing local release-evidence.json")
    parser.add_argument("--github-repo", default="ericeallen/towel")
    args = parser.parse_args(argv)
    try:
        commit = args.commit
        if args.evidence is not None:
            raw: object = json.loads(args.evidence.read_bytes())
            evidence = object_value(raw)
            require(evidence.get("version") == args.version, "Evidence version differs")
            commit = commit_id(evidence.get("validation_commit"))
        result = verify(args.version, commit_id(commit), args.github_repo)
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f"Release visibility refused: {error}", file=sys.stderr)
        return 1
    print(
        f"GitHub default branch {result.default_branch} at {result.head} contains "
        f"{result.release_commit} and presents {args.version}; "
        f"{result.documents_checked} documents checked."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
