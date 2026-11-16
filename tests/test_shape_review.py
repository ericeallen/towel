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

"""Executable regressions from the independent shape round-two review."""

from pathlib import Path
import subprocess
import sys

import pytest

from tests.test_cli_integration import CliResult, invoke


def dry(source: Path, output: Path, strict: bool, *options: str) -> CliResult:
    result = invoke(
        [
            "dry",
            str(source),
            str(output),
            "--strict" if strict else "--no-strict",
            "--no-types",
            "--no-interactive",
            "--progress",
            "none",
            *options,
        ]
    )
    assert result.status == 0, result
    return result


def observe(root: Path, driver: str) -> str:
    result = subprocess.run(
        [sys.executable, "-c", driver],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize("strict", [False, True])
def test_return_permutation_preserves_strict_finalizer_order(tmp_path: Path, strict: bool) -> None:
    """Review 3: redirect return permutations must preserve strict frame-slot order."""
    source = tmp_path / "src"
    source.mkdir()
    (source / "pyproject.toml").write_text('[project]\nname="example"\nversion="0"\n')
    (source / "mod.py").write_text("""events = []
class Noisy:
    def __init__(self, label): self.label = label
    def __del__(self): events.append(f"del {self.label}")

def total():
    first = Noisy("1")
    second = Noisy("2")
    events.append("built")
    return second, first

def report():
    first = Noisy("1")
    second = Noisy("2")
    events.append("built")
    events.append("after block")
    return 0
""")
    driver = "import mod; mod.report(); print(mod.events)"
    before = observe(source, driver)
    assert before == "['built', 'after block', 'del 1', 'del 2']\n"
    output = tmp_path / "out"
    dry(source, output, strict, "--complete-scope")
    after = observe(output, driver)
    if strict:
        assert after == before
    else:
        assert "built" in after and "after block" in after
