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

"""Configured Ruff checks reject introduced errors while retaining useful neighbors."""

from pathlib import Path
import subprocess
import ast

import pytest

from tests.test_cli_integration import invoke

from towel.formatting import LintRejected, file_finisher_for_project
from towel.unification.refactor_engine import UnificationRefactorEngine

DUPLICATED_LABELS = 'def first(label, pos):\n    if len(label) > 100:\n        raise ValueError("too long")\n    cp = ord(label[pos])\n    return cp\n\ndef second(label, pos):\n    if len(label) > 100:\n        raise ValueError("too long")\n    cp = ord(label[pos])\n    return cp\n'


def _module(root: Path, source: str, rules: str) -> Path:
    (root / "pyproject.toml").write_text(f"[tool.ruff.lint]\nselect = [{rules}]\n")
    module = root / "app.py"
    module.write_text(source)
    return module


@pytest.mark.parametrize(
    ("rules", "source", "modified", "code"),
    [
        (
            '"RET504"',
            "def f(x):\n    return x + 1\n",
            "def f(x):\n    value = x + 1\n    return value\n",
            "RET504",
        ),
        ('"E402"', "value = 1\n", "value = 1\nimport typing\n", "E402"),
        (
            '"PYI063"',
            "def f(value: int):\n    return value\n",
            "def f(__param_0: int):\n    return __param_0\n",
            "PYI063",
        ),
    ],
)
def test_generated_source_cannot_introduce_configured_diagnostics(
    tmp_path: Path, rules: str, source: str, modified: str, code: str
) -> None:
    module = _module(tmp_path, source, rules)
    finisher = file_finisher_for_project(module).tool
    assert finisher is not None
    with pytest.raises(LintRejected, match=code):
        finisher(str(module), modified)
    assert module.read_text() == source


def test_unchanged_baseline_error_is_aligned_after_new_lines(tmp_path: Path) -> None:
    source = "def f():\n    unused = 1\n    return 2\n"
    module = _module(tmp_path, source, '"F841"')
    finisher = file_finisher_for_project(module).tool
    assert finisher is not None
    modified = "def helper():\n    return 3\n\n" + source
    assert finisher(str(module), modified) == modified


def test_removed_warning_cannot_pay_for_the_same_warning_in_generated_code(tmp_path: Path) -> None:
    source = "def original():\n    unused = 1\n    return 2\n"
    module = _module(tmp_path, source, '"F841"')
    finisher = file_finisher_for_project(module).tool
    assert finisher is not None
    modified = (
        "def helper():\n    unused = 1\n    return 2\n\ndef original():\n    return helper()\n"
    )
    with pytest.raises(LintRejected, match="F841"):
        finisher(str(module), modified)


def test_disabling_import_sorting_keeps_configured_lint_check(tmp_path: Path) -> None:
    module = _module(tmp_path, "value = 1\n", '"E402", "I"')
    finisher = file_finisher_for_project(module, sort_imports=False).tool
    assert finisher is not None
    with pytest.raises(LintRejected, match="E402"):
        finisher(str(module), "value = 1\nimport typing\n")


@pytest.mark.parametrize("decorator", ["@missing\n", "@(\n    missing\n)\n"])
@pytest.mark.parametrize("definition", ["def", "async def", "class"])
def test_decorator_warning_cannot_move_to_a_different_definition(
    tmp_path: Path, decorator: str, definition: str
) -> None:
    body = "    pass\n" if definition == "class" else "    return 2\n"
    suffix = ":\n" if definition == "class" else "():\n"
    original = f"{decorator}{definition} original{suffix}{body}"
    modified = f"{decorator}{definition} helper{suffix}{body}\n{definition} original{suffix}{body}"
    module = _module(tmp_path, original, '"F821"')
    finisher = file_finisher_for_project(module).tool
    assert finisher is not None
    with pytest.raises(LintRejected, match="F821"):
        finisher(str(module), modified)


def test_ruff_file_exclusion_applies_to_staged_source(tmp_path: Path) -> None:
    module = _module(tmp_path, "value = 1\n", '"E402"')
    (tmp_path / "pyproject.toml").write_text(
        '[tool.ruff]\nexclude = ["app.py"]\n[tool.ruff.lint]\nselect = ["E402"]\n'
    )
    staging = tmp_path / "stage"
    staging.mkdir()
    staged = staging / "app.py"
    staged.write_text(module.read_text())
    finisher = file_finisher_for_project(module).tool
    assert finisher is not None
    modified = "value = 1\nimport typing\n"
    assert finisher(str(staged), modified) == modified


def test_nested_configuration_is_checked_when_parent_has_only_project_metadata(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "sample"\n')
    nested = tmp_path / "pkg"
    nested.mkdir()
    (nested / "ruff.toml").write_text('[lint]\nselect = ["E402"]\n')
    module = nested / "app.py"
    module.write_text("value = 1\n")
    finisher = file_finisher_for_project(tmp_path).tool
    assert finisher is not None
    with pytest.raises(LintRejected, match="E402"):
        finisher(str(module), "value = 1\nimport typing\n")
    unconfigured = tmp_path / "other.py"
    unconfigured.write_text("value = 1\n")
    assert finisher(str(unconfigured), "value = 1\nimport typing\n") == "value = 1\nimport typing\n"


@pytest.mark.parametrize("setting", ["fix = true", "fix-only = true"])
def test_project_fix_setting_does_not_turn_validation_into_rewriting(
    tmp_path: Path, setting: str
) -> None:
    module = _module(tmp_path, "value = 1\n", '"F401"')
    (tmp_path / "pyproject.toml").write_text(
        f'[tool.ruff]\n{setting}\n[tool.ruff.lint]\nselect = ["F401"]\n'
    )
    finisher = file_finisher_for_project(module).tool
    assert finisher is not None
    assert finisher(str(module), "value = 2\n") == "value = 2\n"
    with pytest.raises(LintRejected, match="F401"):
        finisher(str(module), "import typing\nvalue = 1\n")
    assert module.read_text() == "value = 1\n"


@pytest.mark.parametrize(
    "output",
    [
        "{}",
        "[{}]",
        '[{"code":"E402","message":"bad","location":{"row":true,"column":1}}]',
        "invalid",
    ],
)
def test_invalid_tool_diagnostics_cannot_approve_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: str
) -> None:
    module = _module(tmp_path, "value = 1\n", '"E402"')
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, output, ""),
    )
    finisher = file_finisher_for_project(module).tool
    assert finisher is not None
    with pytest.raises(LintRejected):
        finisher(str(module), "value = 2\n")


def test_lint_refusal_does_not_hide_a_useful_neighbor(tmp_path: Path) -> None:
    source = tmp_path / "pkg"
    source.mkdir()
    (tmp_path / "pyproject.toml").write_text('[tool.ruff.lint]\nselect = ["RET504"]\n')
    labels = DUPLICATED_LABELS
    (source / "labels.py").write_text(labels)
    neighbors = 'def first(x):\n    print(x)\n    print("hello")\n\ndef second(y):\n    print(y)\n    print("hello")\n'
    (source / "neighbors.py").write_text(neighbors)
    engine = UnificationRefactorEngine(
        min_lines=2, file_finisher=file_finisher_for_project(source).tool
    )
    output = tmp_path / "out"
    results, _ = engine.refactor_directory_to_fixed_point(str(source), str(output), progress="none")
    assert (output / "labels.py").read_text() == labels
    assert "__extracted_func" in (output / "neighbors.py").read_text()
    assert set(results) == {str(output / "neighbors.py")}
    assert engine.run_report.declined_proposals == {
        "the configured Ruff check failed or introduced diagnostics": 1
    }


@pytest.mark.parametrize("formatting_enabled", [True, False])
def test_cli_preserves_lint_baseline_with_and_without_formatting(
    tmp_path: Path, formatting_enabled: bool
) -> None:
    module = _module(
        tmp_path,
        DUPLICATED_LABELS,
        '"RET504"',
    )
    # The original warnings must not pay for a new warning in the helper.
    original = module.read_text()
    destination = tmp_path / "out.py"
    result = invoke(
        [
            "dry",
            str(module),
            str(destination),
            "--no-interactive",
            "--no-types",
            "--min-lines",
            "2",
            *([] if formatting_enabled else ["--no-format"]),
        ]
    )
    assert result.status == 0, result
    assert destination.read_text() == original
    assert not any(
        isinstance(node, ast.FunctionDef) and node.name.startswith("__extracted_func")
        for node in ast.walk(ast.parse(destination.read_text()))
    )
    assert "the configured Ruff check failed or introduced diagnostics" in result.stdout
