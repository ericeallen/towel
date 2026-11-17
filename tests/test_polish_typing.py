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

"""Polish item 7: a failed original type check follows the CLI's selected mode."""

import ast
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from towel import cli
from towel.type_inference import CheckFailure, TypeOracle
from tests.test_cli_integration import BODY, invoke

SOURCE = "from . import provider\n\n" + "\n".join(
    f"def {name}(x: int) -> int:\n{BODY}" for name in ("first", "second")
)


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("command", ["preview", "dry"])
def test_relative_import_baseline_failure_obeys_selected_mode(
    tmp_path: Path, strict: bool, command: str
) -> None:
    """Polish item 7: real mypy's No parent module warns or refuses with a remedy."""
    pytest.importorskip("mypy")
    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\n")
    package = tmp_path / "package"
    package.mkdir()
    module = package / "module.py"
    module.write_text(SOURCE)
    (package / "provider.py").write_text("value = 1\n")
    output = tmp_path / "output.py"
    arguments = [command, str(module)]
    if command == "dry":
        arguments += [str(output), "--no-interactive"]
    arguments += ["--json", "--no-format", "--progress", "none"]
    arguments += ["--strict"] if strict else ["--no-strict"]
    result = invoke(arguments)
    assert "No parent module" in result.stderr
    assert module.read_text() == SOURCE
    if strict:
        assert result.status == 1
        assert "target the package" in result.stderr.lower()
        assert not output.exists()
    else:
        assert result.status == 0, result.stderr
        assert result.stderr.count("Continuing without type verification") == 1
        report = json.loads(result.stdout)
        assert report["types"] == "unverified"
        assert report["applied"] > 0
        generated = output.read_text() if command == "dry" else report["diff"]
        if command == "preview":
            generated = "\n".join(
                f"{line[1:]}\n    pass"
                for line in generated.splitlines()
                if line.startswith("+def __extracted_func_")
            )
        helpers = [
            node
            for node in ast.parse(generated).body
            if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name
        ]
        assert helpers
        assert all(helper.returns is None for helper in helpers)
        assert all(
            argument.annotation is None for helper in helpers for argument in helper.args.args
        )


@pytest.mark.parametrize("reason", ["checker timed out", "checker worker crashed"])
def test_relaxed_baseline_fallback_closes_checker_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    """Polish item 7: fallback is the baseline failure policy, independent of wording."""
    source = tmp_path / "module.py"
    source.write_text(SOURCE)
    oracle = Mock(spec=TypeOracle)
    oracle.check_project.return_value = CheckFailure(reason)
    monkeypatch.setattr(cli, "_type_oracle", lambda path: oracle)
    result = invoke(["preview", str(source), "--no-strict", "--json", "--no-format"])
    assert result.status == 0, result.stderr
    assert json.loads(result.stdout)["types"] == "unverified"
    oracle.check_project.assert_called_once()
    oracle.close.assert_called_once_with()
    assert source.read_text() == SOURCE


def test_package_target_with_namespace_bases_can_be_checked(tmp_path: Path) -> None:
    """Polish item 7: the namespace-package remedy restores an actual typed check."""
    pytest.importorskip("mypy")
    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\nexplicit_package_bases = true\n")
    package = tmp_path / "package"
    package.mkdir()
    (package / "provider.py").write_text("value = 1\n")
    source = "from . import provider\n\ndef value() -> int:\n    return provider.value\n"
    module = package / "module.py"
    module.write_text(source)
    result = invoke(
        ["preview", str(package), "--strict", "--json", "--no-format", "--progress", "none"]
    )
    assert result.status == 0, result.stderr
    assert "No parent module" not in result.stderr
    assert json.loads(result.stdout)["types"] == "verified"
    assert module.read_text() == source
