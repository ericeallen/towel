# Copyright 2026 Eric Allen
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

"""Every independently authored relaxed review input remains behaviorally correct."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import NotRequired, TypedDict, cast

import pytest


class ReviewCase(TypedDict):
    source: str
    strict: str | None
    modules: NotRequired[dict[str, str]]


CASES = cast(
    dict[str, ReviewCase],
    json.loads((Path(__file__).parent / "fixtures" / "relaxed_review.json").read_text()),
)
pytestmark = pytest.mark.relaxed_mode


def _run(path: Path) -> tuple[int, str, str]:
    result = subprocess.run(
        [sys.executable, str(path)], capture_output=True, text=True, timeout=30, check=False
    )
    return result.returncode, result.stdout, result.stderr


@pytest.mark.parametrize("name", CASES)
@pytest.mark.parametrize("strict", [False, True], ids=["relaxed", "strict"])
def test_review_program(tmp_path: Path, name: str, strict: bool) -> None:
    """Review defects 1 and guard gaps: retain behavior; strict output stays byte-identical."""
    case = CASES[name]
    source = tmp_path / "input.py"
    output = tmp_path / "output.py"
    source.write_text(case["source"])
    for name, contents in case.get("modules", {}).items():
        (tmp_path / name).write_text(contents)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "independent-review-fixture"\nversion = "0.0.0"\nrequires-python = ">=3.11"\n'
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "towel",
            "dry",
            str(source),
            str(output),
            "--no-interactive",
            "--no-types",
            "--no-format",
            "--progress",
            "none",
            "--strict" if strict else "--no-strict",
        ],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    before, after = _run(source), _run(output)
    assert before[0] == 0, before
    assert after == before, (name, result.stdout, output.read_text(), before, after)
    if strict and case["strict"] is not None:
        assert output.read_text() == case["strict"]
