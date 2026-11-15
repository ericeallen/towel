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

"""Unify review defect 1: generated format fields must fit the target Python grammar."""

import ast
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from towel.unification.extractor import HygienicExtractor, UnsupportedExtraction
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.unifier import Unifier

FIXTURE = Path(__file__).parent / "fixtures" / "unify_review" / "d1_spec_nesting_311"


def test_nested_spec_parameter_is_refused_without_a_newer_target() -> None:
    """Review defect 1: a rule-level test independent of the host's f-string parser."""
    blocks = [ast.parse(f'return f"{{x:{{width:{spec}}}}}"').body for spec in ("02", "03")]
    substitution = Unifier().unify_blocks(blocks, [{}, {}])
    assert substitution is not None
    with pytest.raises(UnsupportedExtraction, match="format spec.*Python 3.11"):
        HygienicExtractor().extract_function(
            template_block=blocks[0],
            substitution=substitution,
            free_variables={"x", "width"},
            enclosing_names=set(),
            is_value_producing=True,
        )


def test_nested_spec_parameter_is_allowed_by_a_newer_target_rule() -> None:
    """Review defect 1: the target rule permits newer ASTs even on an older host."""
    blocks = [ast.parse(f'return f"{{x:{{width:{spec}}}}}"').body for spec in ("02", "03")]
    substitution = Unifier().unify_blocks(blocks, [{}, {}])
    assert substitution is not None
    helper, _ = HygienicExtractor().extract_function(
        template_block=blocks[0],
        substitution=substitution,
        free_variables={"x", "width"},
        enclosing_names=set(),
        is_value_producing=True,
        oldest_python=(3, 12),
    )
    assert _has_deep_format_field(helper)


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("requirement", [None, ">=3.11", ">=3.12"])
def test_review_nested_specs_keep_target_syntax_and_values(
    tmp_path: Path, strict: bool, requirement: str | None
) -> None:
    """Review defect 1: default and declared 3.11 targets run on an actual 3.11."""
    if requirement is not None:
        (tmp_path / "pyproject.toml").write_text(
            f'[project]\nname = "specs"\nrequires-python = "{requirement}"\n'
        )
    source = (FIXTURE / "m.py.txt").read_text()
    path = tmp_path / "m.py"
    path.write_text(source)
    transformed, _, _ = UnificationRefactorEngine(
        min_lines=1, strict=strict, skip_trivial_helpers=False
    ).refactor_to_fixed_point(str(path))
    driver = (FIXTURE / "driver.py.txt").read_text()
    # Run the review's original driver against each module in a fresh process.
    before = subprocess.run(
        [sys.executable, "-B", "-c", driver, str(tmp_path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    path.write_text(transformed)
    after = subprocess.run(
        [sys.executable, "-B", "-c", driver, str(tmp_path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert after == before
    assert _has_deep_format_field(ast.parse(transformed)) is (
        requirement == ">=3.12" and sys.version_info >= (3, 12)
    )
    if requirement == ">=3.12":
        return
    python311 = shutil.which("python3.11")
    if python311 is None:
        pytest.skip(
            "Python 3.11 unavailable: target-depth and value checks passed; parser check skipped"
        )
    result = subprocess.run(
        [python311, "-B", "-c", driver, str(tmp_path)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == before


def _has_deep_format_field(tree: ast.AST) -> bool:
    return any(
        isinstance(node, ast.FormattedValue)
        for formatted in ast.walk(tree)
        if isinstance(formatted, ast.FormattedValue)
        and isinstance(formatted.format_spec, ast.JoinedStr)
        for inner in formatted.format_spec.values
        if isinstance(inner, ast.FormattedValue) and isinstance(inner.format_spec, ast.JoinedStr)
        for node in inner.format_spec.values
    )


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("versions", [("3.12", "3.11"), ("3.11", "3.12")])
def test_review_target_uses_each_paths_checker_configuration(
    tmp_path: Path, strict: bool, versions: tuple[str, str]
) -> None:
    """Review defect 1: a sibling's cached Python floor cannot replace this path's target."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "specs"\nversion = "0"\n')
    source = (FIXTURE / "m.py.txt").read_text()
    engine = UnificationRefactorEngine(min_lines=1, strict=strict, skip_trivial_helpers=False)
    for version in versions:
        directory = tmp_path / version
        directory.mkdir()
        (directory / "pyrightconfig.json").write_text('{"pythonVersion": "' + version + '"}')
        path = directory / "m.py"
        path.write_text(source)
        proposals = engine.analyze_file(str(path))
        assert any(
            _has_deep_format_field(proposal.extracted_function) for proposal in proposals
        ) is (version == "3.12")
