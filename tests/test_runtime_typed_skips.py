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

"""Round 2 D2: default type verification shares the relaxed program boundary."""

import ast
import json
import os
from pathlib import Path

import pytest

from tests.test_cli_integration import LITERAL_DUPLICATES, invoke
from towel.program_files import refuse_unparsed_program
from towel.source_files import source_policy
from towel.type_inference import CheckSuccess, MypyInferrer, PyrightOracle

pytestmark = pytest.mark.relaxed_mode


@pytest.mark.parametrize("command", ["preview", "dry"])
@pytest.mark.parametrize("checker", ["mypy", "pyright"])
@pytest.mark.parametrize("unavailable", ["invalid", "new-syntax", "locked"])
def test_default_typed_run_skips_unavailable_input(
    tmp_path: Path, command: str, checker: str, unavailable: str
) -> None:
    """D2: real checker discovery, probes and final confirmation honour relaxed skips."""
    pytest.importorskip(checker)
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(f"[tool.{checker}]\n")
    source = project / "a.py"
    source.write_text(LITERAL_DUPLICATES)
    bad = project / ("locked" if unavailable == "locked" else "bad_fixture.py")
    if unavailable == "locked":
        bad.mkdir()
        (bad / "fixture.py").write_text("def broken(:\n")
        bad.chmod(0)
    else:
        text = "lazy import math\n" if unavailable == "new-syntax" else "def broken(:\n"
        if unavailable == "new-syntax":
            try:
                ast.parse(text)
            except SyntaxError:
                pass
            else:
                pytest.skip("This interpreter understands the newer syntax")
        bad.write_text(text)
    try:
        if unavailable == "locked" and os.access(bad, os.R_OK | os.X_OK):
            pytest.skip("Current user bypasses directory permissions")
        args = [command, str(project), "--json", "--progress", "none"]
        output = tmp_path / "out"
        if command == "dry":
            args += [str(output), "--no-interactive"]
        result = invoke(args)
        assert result.status == 0, result
        payload = json.loads(result.stdout)
        assert payload["applied"] > 0, payload
        assert result.stderr.count("Skipping ") == 1, result.stderr
        assert "type checker does not look at" not in result.stderr
        assert str(bad) in result.stderr
        assert source.read_text() == LITERAL_DUPLICATES
        if command == "dry":
            assert "extracted_func" in (output / "a.py").read_text()
        else:
            assert "extracted_func" in payload["diff"]
    finally:
        if unavailable == "locked":
            bad.chmod(0o700)


@pytest.mark.parametrize("checker", ["mypy", "pyright"])
def test_skipping_invalid_input_keeps_real_type_errors_visible(
    tmp_path: Path, checker: str
) -> None:
    """D2: omissions do not hide original or introduced errors in readable code."""
    pytest.importorskip(checker)
    (tmp_path / "pyproject.toml").write_text(f"[tool.{checker}]\n")
    source = tmp_path / "good.py"
    source.write_text("x: int = 1\n")
    (tmp_path / "bad.py").write_text("def broken(:\n")
    oracle = MypyInferrer() if checker == "mypy" else PyrightOracle()
    try:
        with source_policy(strict=False):
            refuse_unparsed_program(tmp_path)
            baseline = oracle.check_project({str(source): source.read_text()})
            assert isinstance(baseline, CheckSuccess) and not baseline.errors, baseline
            changed = oracle.check_project({str(source): 'x: int = "wrong"\n'})
            assert isinstance(changed, CheckSuccess) and changed.errors, changed
            assert any(Path(error.path) == source for error in changed.errors)
            source.write_text('x: int = "already wrong"\n')
            original = oracle.check_project({str(source): source.read_text()})
            assert isinstance(original, CheckSuccess) and original.errors, original
            assert any(Path(error.path) == source for error in original.errors)
    finally:
        oracle.close()


@pytest.mark.parametrize("checker", ["mypy", "pyright"])
def test_checker_does_not_follow_import_into_skipped_module(tmp_path: Path, checker: str) -> None:
    """D2: an import reports an absent module instead of reparsing unavailable input."""
    pytest.importorskip(checker)
    (tmp_path / "pyproject.toml").write_text(f"[tool.{checker}]\n")
    source = tmp_path / "good.py"
    source.write_text("import bad\nx: int = 1\n")
    (tmp_path / "bad.py").write_text("def broken(:\n")
    oracle = MypyInferrer() if checker == "mypy" else PyrightOracle()
    try:
        with source_policy(strict=False):
            refuse_unparsed_program(tmp_path)
            result = oracle.check_project({str(source): source.read_text()})
            assert isinstance(result, CheckSuccess), result
            assert result.errors, result
            assert any(Path(error.path) == source for error in result.errors)
    finally:
        oracle.close()
