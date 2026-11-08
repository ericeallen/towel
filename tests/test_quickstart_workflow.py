# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Execute the tutorial's source, rename mapping and tests through adoption."""

import json
from pathlib import Path
import re
import subprocess
import sys

from tests.test_cli_integration import invoke

ROOT = Path(__file__).resolve().parents[1]


def _blocks(language: str) -> list[str]:
    document = (ROOT / "docs/QUICKSTART.md").read_text()
    return re.findall(rf"(?ms)^```{language}\n(.*?)^```", document)


def _check_project(project: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", str(project)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Ran 1 test" in result.stderr


def test_quickstart_extracts_names_tests_and_adopts_one_reviewed_file(tmp_path: Path) -> None:
    project = tmp_path / "demo"
    project.mkdir()
    source, tests = _blocks("python")
    (project / "pyproject.toml").write_text('[project]\nname = "towel-demo"\nversion = "0.0.0"\n')
    (project / "charges.py").write_text(source)
    (project / "test_charges.py").write_text(tests)
    _check_project(project)

    preview = invoke(["preview", str(project), "--no-types", "--no-format"])
    assert preview.status == 0, preview.stderr
    assert (project / "charges.py").read_text() == source
    output = tmp_path / "cleaned"
    applied = invoke(
        ["dry", str(project), str(output), "--no-interactive", "--no-types", "--no-format"]
    )
    assert applied.status == 0, applied.stderr
    generated = (output / "charges.py").read_text()
    assert generated != source and "__extracted_func_0" in generated
    assert (project / "charges.py").read_text() == source

    inventory = invoke(["rename-helpers", str(output), "--list", "--json"])
    assert inventory.status == 0, inventory.stderr
    mapping = _blocks("json")[0]
    requested = json.loads(mapping)
    listed = json.loads(inventory.stdout)
    assert set(requested) <= {helper["rename_key"] for helper in listed["helpers"]}
    renames = tmp_path / "renames.json"
    renames.write_text(mapping)
    command = [
        "rename-helpers",
        str(output),
        "--complete-scope",
        "--rename-file",
        str(renames),
    ]
    planned = invoke([*command, "--preview"])
    assert planned.status == 0, planned.stderr
    assert (output / "charges.py").read_text() == generated
    renamed = invoke(command)
    assert renamed.status == 0, renamed.stderr
    adopted = (output / "charges.py").read_text()
    assert "discounted_total_with_shipping" in adopted
    assert "__extracted_func_0" not in adopted
    _check_project(output)
    assert (project / "charges.py").read_text() == source

    (project / "charges.py").write_text(adopted)
    _check_project(project)
