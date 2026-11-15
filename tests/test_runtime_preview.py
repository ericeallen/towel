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

"""Runtime R4/R5: bounded preview retains a private snapshot, never publishes it."""

import json
import ast
from pathlib import Path
from typing import NoReturn

import pytest

from tests.test_cli_integration import invoke
from tests.test_progress_modes import _make_fixture
from towel.execution_budget import BudgetExpired
from towel.unification.refactor_engine import UnificationRefactorEngine


@pytest.mark.parametrize("directory", [False, True])
def test_timeout_preview_shows_unconfirmed_applied_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, directory: bool
) -> None:
    """R4: a deadline after one application has a partial diff, never a complete diff."""
    project = tmp_path / "project"
    _make_fixture(project)
    target = project if directory else project / "sample.py"
    before = (project / "sample.py").read_bytes()

    def expire(self: UnificationRefactorEngine) -> None:
        raise BudgetExpired()

    monkeypatch.setattr(UnificationRefactorEngine, "_follow_the_written_change", expire)
    flags = ["--no-types", "--no-format", "--progress", "none", "--json"]
    preview = invoke(["preview", str(target), *flags])
    assert preview.status == 124, preview
    result = json.loads(preview.stdout)
    assert result["status"] == "incomplete" and result["termination"] == "time_limit"
    assert result["applied"] == 1
    assert result["diff"] == ""
    assert "__extracted_func_" in result["partial_diff"]
    assert result["finally_confirmed"] is False
    assert (project / "sample.py").read_bytes() == before
    output = tmp_path / ("out" if directory else "out.py")
    dry = invoke(["dry", str(target), str(output), "--no-interactive", *flags])
    assert dry.status == 124
    assert not output.exists()


@pytest.mark.parametrize("directory", [False, True])
@pytest.mark.parametrize("as_json", [False, True])
def test_timeout_during_pairs_retains_census_without_claiming_no_duplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, directory: bool, as_json: bool
) -> None:
    """R4: expiry before the first completed pair pass cannot report an empty project."""
    project = tmp_path / "project"
    _make_fixture(project)
    source = project / "sample.py"
    before = source.read_bytes()
    functions = sum(isinstance(node, ast.FunctionDef) for node in ast.walk(ast.parse(before)))

    def expire(*args: object, **kwargs: object) -> NoReturn:
        raise BudgetExpired()

    monkeypatch.setattr(UnificationRefactorEngine, "process_block_pairs", expire)
    flags = ["--no-types", "--no-format", "--progress", "none"]
    preview = invoke(
        [
            "preview",
            str(project if directory else source),
            *flags,
            "--json" if as_json else "--summary",
        ]
    )
    assert preview.status == 124, preview
    if as_json:
        result = json.loads(preview.stdout)
        assert result["analysed"] == {"files": 1, "functions": functions, "skipped": []}
        assert result["outcome"] is None
        assert result["status"] == "incomplete"
        assert result["diff"] == result["partial_diff"] == ""
    else:
        assert f"Analysed 1 Python file ({functions} functions)." in preview.stdout
        assert "No Python" not in preview.stdout
        assert "No repeated code" not in preview.stdout
    assert source.read_bytes() == before
