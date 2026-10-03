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

"""Harness log formatting must not override the program whose behavior it tests."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from scripts import ecosystem_check as ecosystem


def test_colour_controls_apply_only_to_towel_subprocesses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "sample"
    source.mkdir()
    (source / "package.py").write_text("value = 1\n")
    observations = tmp_path / "observations.jsonl"
    record = (
        "import json, os\n"
        "from pathlib import Path\n"
        f"observations = Path({str(observations)!r})\n"
        "def record(phase):\n"
        "    with observations.open('a') as output:\n"
        "        output.write(json.dumps({'phase': phase, 'env': dict(os.environ)}) + '\\n')\n"
    )
    # Sphinx gives NO_COLOR precedence over FORCE_COLOR. A test that explicitly
    # enables colour must therefore be able to exercise its normal code path.
    (source / "consumer.py").write_text(
        record + "import unittest\n"
        "record('test')\n"
        "class ColourTest(unittest.TestCase):\n"
        "    def test_explicit_colour(self):\n"
        "        os.environ['FORCE_COLOR'] = '1'\n"
        "        enabled = 'NO_COLOR' not in os.environ and 'FORCE_COLOR' in os.environ\n"
        "        self.assertEqual('\\r' if enabled else '\\n', '\\r')\n"
        "unittest.main()\n"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    python = bin_dir / "python"
    python.symlink_to(sys.executable)
    towel = bin_dir / "towel"
    towel.write_text(
        f"#!{sys.executable}\n" + record + "import sys\n"
        "assert os.environ['NO_COLOR'] == '1'\n"
        "assert os.environ['PY_COLORS'] == '0'\n"
        "assert os.environ['TERM'] == 'dumb'\n"
        "assert 'PYTHONPATH' not in os.environ\n"
        "assert os.environ['TOWEL_WORKERS'] == '1'\n"
        "if sys.argv[1:] == ['dry', '--help']:\n"
        "    record('help')\n"
        "    print('usage: towel dry --cross-module')\n"
        "else:\n"
        "    record('refactor')\n"
        "    Path(sys.argv[3]).write_text('value = 2\\n')\n"
        "    print('Applied 1 refactoring')\n"
    )
    towel.chmod(0o755)
    candidate = ecosystem.Candidate(
        tmp_path / "code_towel-0-py3-none-any.whl", "code-towel", "0", "0", (), (), ()
    )
    installed = ecosystem.ProjectEnvironment(
        python, None, ecosystem.Environment(sys.version, "0", (), ())
    )
    monkeypatch.setattr(ecosystem, "clone", lambda *_: "pinned")
    monkeypatch.setattr(ecosystem, "environment", lambda *_: installed)
    monkeypatch.setattr(ecosystem, "changed", lambda *_: (1, "package.py changed"))
    # Neither consumer inherits the caller's formatting choices accidentally.
    for name, value in (
        ("NO_COLOR", "inherited"),
        ("PY_COLORS", "inherited"),
        ("TERM", "inherited"),
        ("TOWEL_WORKERS", "1"),
    ):
        monkeypatch.setenv(name, value)
    project = ecosystem.Project(
        "sample", "unused", "pinned", "package.py", test=("{python}", "consumer.py")
    )
    result = ecosystem.check_project(project, tmp_path, candidate, 30)

    assert result.baseline is not None and result.baseline.returncode == 0
    assert result.after is not None and result.after.returncode == 0
    assert result.verdict == "PASS"
    records = [json.loads(line) for line in observations.read_text().splitlines()]
    assert [entry["phase"] for entry in records] == ["test", "help", "refactor", "test"]
    for entry in (records[0], records[-1]):
        assert not {"NO_COLOR", "PY_COLORS", "TERM"}.intersection(entry["env"])
        assert entry["env"]["PYTHONPATH"] == "."
    assert (tmp_path / "sample-ready" / "package.py").read_text() == "value = 2\n"
