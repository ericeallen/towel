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

from towel.unification.argument_ownership import _supported_runtime


class ReviewCase(TypedDict):
    source: str
    strict: str | None
    modules: NotRequired[dict[str, str]]


CASES = cast(
    dict[str, ReviewCase],
    json.loads((Path(__file__).parent / "fixtures" / "relaxed_review.json").read_text()),
)
pytestmark = pytest.mark.relaxed_mode


def _strict_output(name: str, archived: str) -> str:
    """Apply only the shape brief's approved edits to the frozen 1.949 output.

    Keep the review archive untouched and compare every remaining byte. Shape
    items 1 and 2 preserve source parameter order and spelling in both modes.
    Item 5 permits these two redirects: the project scope is complete, no name
    is rebound, and the assertion-only callers need no ownership transfer.
    """
    edits: list[tuple[str, str]] = []
    if name == "ext_long_effects":
        edits += [
            ("(__param_0, /, x)", "(x, __param_0, /)"),
            ("__extracted_func_0(3, x)", "__extracted_func_0(x, 3)"),
            ("__extracted_func_0(4, x)", "__extracted_func_0(x, 4)"),
        ]
    if name in {"ext_long_effects", "ext_long_partial_effects"}:
        edits += [(f"print('stage {i}',", f'print("stage {i}",') for i in range(6)]
    if name == "ext_long_fstring":
        edits += [
            ("(__param_0, /, x, _towel_owner)", "(x, __param_0, /, _towel_owner)"),
            ("return f'{__param_0}:{h}'", 'return f"{__param_0}:{h}"'),
            (
                "__extracted_func_0('first', _towel_arguments[0][0],",
                "__extracted_func_0(_towel_arguments[0][0], 'first',",
            ),
            (
                "__extracted_func_0('second', _towel_arguments[0][0],",
                "__extracted_func_0(_towel_arguments[0][0], 'second',",
            ),
        ]
    if name in {"ext_long_tests", "iso_06_tests"}:
        edits += [
            ("\n\ndef first(x):\n    __extracted_func_0(x)\n", ""),
            ("__extracted_func_0", "first"),
        ]
    if name == "ext_open_lock":
        edits.append(("stream.write('abc')", 'stream.write("abc")'))
    for old, new in edits:
        assert old in archived, (name, old)
        archived = archived.replace(old, new)
    return archived


def _run(path: Path) -> tuple[int, str, str]:
    result = subprocess.run(
        [sys.executable, str(path)], capture_output=True, text=True, timeout=30, check=False
    )
    return result.returncode, result.stdout, result.stderr


@pytest.mark.parametrize("name", CASES)
@pytest.mark.parametrize("strict", [False, True], ids=["relaxed", "strict"])
def test_review_program(tmp_path: Path, name: str, strict: bool) -> None:
    """Review defects 1 and guard gaps: retain behavior and all unapproved strict bytes."""
    case = CASES[name]
    source = tmp_path / "input.py"
    output = tmp_path / "output.py"
    source.write_text(case["source"])
    for module_name, contents in case.get("modules", {}).items():
        (tmp_path / module_name).write_text(contents)
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
    if name == "iso_30_generics" and sys.version_info < (3, 12):
        # PEP 695 cannot run here. Check the command's actual source policy in both modes.
        if strict:
            assert result.returncode == 1
            assert "input.py: line 1:" in result.stderr
            assert "Run Towel on a Python that parses it" in result.stderr
            assert not output.exists()
        else:
            assert result.returncode == 0
            assert "Skipping" in result.stderr and "input.py: line 1:" in result.stderr
            assert output.read_bytes() == source.read_bytes()
        return
    assert result.returncode == 0, result.stdout + result.stderr
    before, after = _run(source), _run(output)
    assert before[0] == 0, before
    assert after == before, (name, result.stdout, output.read_text(), before, after)
    if strict and name == "ext_long_fstring" and not _supported_runtime():
        # This archived extraction transfers ownership of x, which strict
        # mode declines on runtimes whose frame-release order is uncertified.
        assert output.read_text() == case["source"]
        assert "[lifetime_order_uncertified]" in result.stdout
    elif strict and case["strict"] is not None:
        assert output.read_text() == _strict_output(name, case["strict"])
