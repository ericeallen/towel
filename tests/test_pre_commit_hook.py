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

"""Audit W2: the published hook runs a read-only gate on the entire repository."""

import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest
from pre_commit.clientlib import load_manifest

from tests.test_cli_integration import DUPLICATES


@pytest.mark.parametrize("duplicated", [False, True])
def test_w2_hook_manifest_is_valid_and_its_entry_gates_without_writes(
    tmp_path: Path, duplicated: bool
) -> None:
    manifest = Path(__file__).resolve().parents[1] / ".pre-commit-hooks.yaml"
    # pre-commit's own schema validates the file, not a subset of its YAML grammar.
    hooks = load_manifest(str(manifest))
    assert len(hooks) == 1
    hook = hooks[0]
    assert hook["id"] == "towel-check"
    assert hook["language"] == "python"
    assert hook["pass_filenames"] is False
    assert hook["always_run"] is True
    assert hook["entry"] == "towel preview . --check --summary"
    (tmp_path / "pyproject.toml").write_text(
        '[tool.towel]\ntypes = false\nformat = false\nprogress = "none"\n'
    )
    nested = tmp_path / "src"
    nested.mkdir()
    (nested / "example.py").write_text(DUPLICATES if duplicated else "VALUE = 42\n")
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = subprocess.run(
        shlex.split(hook["entry"]),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
            "TOWEL_WORKERS": "2",
        },
        timeout=60,
    )
    assert result.returncode == (3 if duplicated else 0), result.stdout + result.stderr
    assert "--- before/" not in result.stdout
    assert {
        p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()
    } == before
