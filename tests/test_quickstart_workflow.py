# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Execute the tutorial's source, rename mapping and tests through adoption.

tests/test_documentation_examples.py follows the page command by command and
compares every output it shows. This test checks what the page teaches, in
either mode: one helper, callers of one line in relaxed mode, a mapping whose
keys the inventory lists, and tests that pass before, after and once adopted.
"""

import ast
import json
from pathlib import Path
import re
import subprocess
import sys

from tests.test_helpers import lifetime_order_certified
from tests.test_cli_integration import invoke
from towel import cli

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


def _bodies(source: str) -> dict[str, list[str]]:
    return {
        node.name: [ast.unparse(statement) for statement in node.body]
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef)
    }


def test_quickstart_extracts_names_tests_and_adopts_one_reviewed_file(tmp_path: Path) -> None:
    project = tmp_path / "demo"
    project.mkdir()
    source, tests = _blocks("python")
    (project / "pyproject.toml").write_text('[project]\nname = "demo"\nversion = "0.1.0"\n')
    (project / "charges.py").write_text(source)
    (project / "test_charges.py").write_text(tests)
    _check_project(project)

    preview = invoke(["preview", str(project)])
    assert preview.status == 0, preview.stderr
    assert (project / "charges.py").read_text() == source
    output = tmp_path / "cleaned"
    applied = invoke(["dry", str(project), str(output), "--no-interactive"])
    assert applied.status == 0, applied.stderr
    generated = (output / "charges.py").read_text()
    if not lifetime_order_certified():
        assert "lifetime_order_uncertified" in applied.stdout
        assert generated == source
        assert (project / "charges.py").read_text() == source
        _check_project(output)
        return
    assert generated != source
    assert (project / "charges.py").read_text() == source
    if not cli.DEFAULT_STRICT:
        # Audit finding 1: the quick start's callers read as a person would write them.
        callers = _bodies(generated)
        for function, rate in (
            ("checkout_total", "0.1"),
            ("member_total", "0.25"),
            ("wholesale_total", "0.4"),
        ):
            assert callers[function] == [f"return __extracted_func_0(amount, {rate})"]
        assert "_towel_" not in generated

    inventory = invoke(["rename-helpers", str(output), "--list", "--json"])
    assert inventory.status == 0, inventory.stderr
    listed = json.loads(inventory.stdout)
    keys = {key for helper in listed["helpers"] for key in _keys(helper)}
    mapping = _blocks("json")[0]
    requested = json.loads(mapping)
    assert {f"charges.py:{key}" for key in requested} <= keys
    renames = tmp_path / "renames.json"
    renames.write_text(mapping)
    command = ["rename-helpers", str(output), "--rename-file", str(renames)]
    planned = invoke([*command, "--preview"])
    assert planned.status == 0, planned.stderr
    assert (output / "charges.py").read_text() == generated
    renamed = invoke(command)
    assert renamed.status == 0, renamed.stderr
    adopted = (output / "charges.py").read_text()
    assert "def discounted_total(amount, discount_rate" in adopted
    assert "__extracted_func_0" not in adopted
    _check_project(output)
    assert (project / "charges.py").read_text() == source

    (project / "charges.py").write_text(adopted)
    _check_project(project)


def _keys(helper: dict[str, object]) -> list[str]:
    parameters = helper["parameters"]
    assert isinstance(parameters, list)
    return [str(helper["rename_key"])] + [str(parameter["rename_key"]) for parameter in parameters]
