#!/usr/bin/env python3
# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by law or agreed in writing, software distributed under the
# License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS
# OF ANY KIND, either express or implied. See the License for the specific
# language governing permissions and limitations under the License.

"""Rebuild the source distribution from a fresh clone and require the same bytes.

Run in the release's build environment, after the primary build, from a checkout
of the commit that was built, with ``SOURCE_DATE_EPOCH`` set to its commit time
for that build. Writes ``rebuild-dist/`` and ``sdist-rebuild.json`` into the
evidence directory, which ``verify_release_evidence.py`` reads.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Sequence

RECORD = "sdist-rebuild.json"


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def check(repo: Path, evidence: Path, primary: Path, *, isolation: bool) -> bool:
    """Whether a clean rebuild of ``repo``'s HEAD equals ``primary``; records it if so."""
    evidence = evidence.resolve()
    expected = primary.read_bytes()
    record, outdir = evidence / RECORD, evidence / "rebuild-dist"
    if record.exists() or outdir.exists():
        raise FileExistsError(f"An earlier rebuild is already recorded in {evidence}")
    commit = _git(repo, "rev-parse", "HEAD")
    epoch = int(_git(repo, "show", "-s", "--format=%ct", commit))
    with tempfile.TemporaryDirectory(prefix="towel-sdist-rebuild-") as scratch:
        clone = Path(scratch) / "clone"
        _git(repo, "clone", "--quiet", "--no-hardlinks", ".", str(clone))
        _git(clone, "checkout", "--quiet", "--detach", commit)
        command = [sys.executable, "-m", "build", "--sdist", "--outdir", str(outdir)]
        command += [] if isolation else ["--no-isolation"]
        print(f"Rebuilding the sdist of {commit} from a fresh clone", flush=True)
        started = _now()
        status = subprocess.run(
            command, cwd=clone, env={**os.environ, "SOURCE_DATE_EPOCH": str(epoch)}
        ).returncode
        finished = _now()
    if status:
        print(f"Rebuild failed with exit status {status}", file=sys.stderr)
        return False
    rebuilt = outdir / primary.name
    actual = rebuilt.read_bytes()
    if actual != expected:
        print(
            f"Sdist is NOT reproducible: {primary} is "
            f"{hashlib.sha256(expected).hexdigest()}, {rebuilt} is "
            f"{hashlib.sha256(actual).hexdigest()}. Was the first build run with "
            f"SOURCE_DATE_EPOCH={epoch}, from this commit, with these tool versions?",
            file=sys.stderr,
        )
        return False
    observation = {
        "commit": commit,
        "source_date_epoch": epoch,
        "build": {
            "args": command,
            "started": started,
            "finished": finished,
            "exit_status": status,
        },
        "rebuild": {
            "path": str(rebuilt.relative_to(evidence)),
            "sha256": hashlib.sha256(actual).hexdigest(),
        },
    }
    with record.open("x") as stream:
        stream.write(json.dumps(observation, indent=2) + "\n")
    print(f"Sdist rebuilt byte for byte; recorded in {record}")
    return True


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path, help="Directory that receives the record")
    parser.add_argument("sdist", type=Path, help="The primary build's .tar.gz")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--no-isolation", action="store_true", help="As for python -m build")
    args = parser.parse_args(argv)
    same = check(args.repo, args.evidence, args.sdist, isolation=not args.no_isolation)
    return 0 if same else 1


if __name__ == "__main__":
    raise SystemExit(main())
