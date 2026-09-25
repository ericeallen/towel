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

"""Generated helpers and calls are formatted by an injected formatter.

The engine renders each inserted snippet through ``snippet_formatter`` when
one is given; ``towel dry`` supplies Black configured from the project's own
``[tool.black]`` settings unless ``--no-format`` is passed. A formatter may
only change layout: the wrapper compares syntax trees and refuses anything
else.
"""

from __future__ import annotations

import ast
from pathlib import Path
import textwrap
from typing import Unpack

import pytest

from tests.test_cli_integration import invoke
from tests.test_helpers import EngineOptions
from towel.formatting import BlackSettings, FormattingChangedCode, black_formatter, checked
from towel.unification.refactor_engine import UnificationRefactorEngine

DUPLICATED_STRINGS = textwrap.dedent("""
    def first(name, a_rather_long_parameter_name, another_long_parameter_name):
        prefix = 'Mr. '
        greeting = prefix + name + a_rather_long_parameter_name + another_long_parameter_name
        return greeting.strip()

    def second(name, a_rather_long_parameter_name, another_long_parameter_name):
        prefix = 'Dr. '
        greeting = prefix + name + a_rather_long_parameter_name + another_long_parameter_name
        return greeting.strip()
    """)


def _refactor(path: Path, **engine_options: Unpack[EngineOptions]) -> str:
    engine = UnificationRefactorEngine(min_lines=2, **engine_options)
    (proposal,) = engine.analyze_file(str(path))
    return engine.apply_refactoring(str(path), proposal)


def _evaluate(source: str) -> list[str]:
    namespace: dict[str, object] = {}
    exec(compile(source, "<formatted>", "exec"), namespace)
    return [namespace[name]("x", "-y", "-z") for name in ("first", "second")]  # type: ignore[operator]


def test_checked_formatter_strips_trailing_newline_and_keeps_meaning() -> None:
    formatter = checked(lambda source: source.replace("'", '"') + "\n\n")
    assert formatter("x = 'a'") == 'x = "a"'


def test_checked_formatter_refuses_a_change_of_meaning() -> None:
    formatter = checked(lambda source: source.replace("+", "-"))
    with pytest.raises(FormattingChangedCode):
        formatter("x = a + b")


def test_black_settings_come_from_the_nearest_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.black]\nline-length = 72\nskip-string-normalization = true\n"
    )
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    assert BlackSettings.for_project(package / "m.py") == BlackSettings(
        line_length=72, string_normalization=False
    )


def test_black_settings_default_without_configuration(tmp_path: Path) -> None:
    assert BlackSettings.for_project(tmp_path / "m.py") == BlackSettings()


def test_engine_inserts_unparsed_text_without_a_formatter(tmp_path: Path) -> None:
    path = tmp_path / "m.py"
    path.write_text(DUPLICATED_STRINGS)
    result = _refactor(path)
    assert "__extracted_func_0('Mr. '" in result
    assert _evaluate(result) == _evaluate(DUPLICATED_STRINGS)


def test_engine_formats_helper_and_calls_with_black(tmp_path: Path) -> None:
    path = tmp_path / "m.py"
    path.write_text(DUPLICATED_STRINGS)
    result = _refactor(path, snippet_formatter=black_formatter(BlackSettings(line_length=60)))
    assert '__extracted_func_0(\n        "Mr. ",' in result, result
    # The helper's long line is wrapped to the configured width and its
    # strings are normalized; the surrounding original text is untouched.
    assert "prefix = 'Mr. '" not in result
    assert max(len(line) for line in result.splitlines() if "__extracted_func" in line) <= 60
    assert _evaluate(result) == _evaluate(DUPLICATED_STRINGS)


def test_formatted_method_helper_is_reindented_inside_the_class(tmp_path: Path) -> None:
    path = tmp_path / "m.py"
    path.write_text(textwrap.dedent("""
            class Greeter:
                def first(self, name):
                    prefix = 'Mr. '
                    greeting = prefix + name + self.suffix
                    return greeting.strip()

                def second(self, name):
                    prefix = 'Dr. '
                    greeting = prefix + name + self.suffix
                    return greeting.strip()
            """))
    result = _refactor(path, snippet_formatter=black_formatter(BlackSettings()))
    tree = ast.parse(result)
    (cls,) = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    assert {node.name for node in cls.body if isinstance(node, ast.FunctionDef)} >= {
        "first",
        "second",
    }
    assert 'prefix = "Mr. "' not in result.split("def first")[1].split("def second")[0]
    assert '"Mr. "' in result


def test_dry_formats_by_default_and_not_with_no_format(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "m.py").write_text(DUPLICATED_STRINGS)
    formatted = tmp_path / "formatted"
    plain = tmp_path / "plain"
    assert (
        invoke(
            ["dry", str(source), str(formatted), "--no-interactive", "--progress", "none"]
        ).status
        == 0
    )
    assert (
        invoke(
            [
                "dry",
                str(source),
                str(plain),
                "--no-interactive",
                "--progress",
                "none",
                "--no-format",
            ]
        ).status
        == 0
    )
    assert '"Mr. "' in (formatted / "m.py").read_text()
    assert "'Mr. '" in (plain / "m.py").read_text()
    assert _evaluate((formatted / "m.py").read_text()) == _evaluate((plain / "m.py").read_text())


R9P2_DOCSTRING_BLOCK = textwrap.dedent("""
    def first(a):
        if a:
            x = 1
        else:
            x = 2
        "  explain the next steps  "
        y = x * 2
        z = y - 3
        print(z)
        return z * 10


    def second(b):
        x = len(b)
        "  explain the next steps  "
        y = x * 2
        z = y - 3
        print(z)
        return z * 10
    """)


def test_r9p2_a_formatter_that_changes_code_declines_the_proposal_not_the_directory_run(
    tmp_path: Path,
) -> None:
    """Round 4's D1: Black normalizes a moved string that becomes the helper's docstring.

    ``checked`` refused it with ``FormattingChangedCode``, which the directory
    loop did not catch, so the whole run failed and wrote nothing. The check
    stays; the proposal whose formatting it refuses is declined, with its
    reason, and the rest of the run is applied.
    """
    source = tmp_path / "r9p2_pkg"
    source.mkdir()
    (source / "__init__.py").write_text("")
    (source / "docstring.py").write_text(R9P2_DOCSTRING_BLOCK)
    (source / "strings.py").write_text(DUPLICATED_STRINGS)
    normalizing = checked(
        lambda text: text.replace("  explain the next steps  ", "explain the next steps")
    )
    engine = UnificationRefactorEngine(min_lines=2, snippet_formatter=normalizing)
    output = tmp_path / "out"
    results, _ = engine.refactor_directory_to_fixed_point(str(source), str(output), progress="none")
    assert engine.run_report.declined_proposals == {"the formatter changed its code or failed": 1}
    assert (output / "docstring.py").read_text() == R9P2_DOCSTRING_BLOCK
    assert "__extracted_func" in (output / "strings.py").read_text()
    assert set(results) == {str(output / "strings.py")}


@pytest.mark.parametrize(
    "filename, contents, expected",
    [
        ("pyproject.toml", "[tool.ruff]\nline-length = 100\n", 100),
        ("pyproject.toml", "[tool.pycodestyle]\nmax-line-length = 79\n", 79),
        ("setup.cfg", "[flake8]\nmax-line-length = 79\n", 79),
        ("tox.ini", "[pycodestyle]\nmax_line_length = 120\n", 120),
        (".flake8", "[flake8]\nmax-line-length = 99\n", 99),
    ],
)
def test_line_length_follows_the_projects_own_declaration(
    tmp_path: Path, filename: str, contents: str, expected: int
) -> None:
    (tmp_path / filename).write_text(contents)
    if filename != "pyproject.toml":
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    assert BlackSettings.for_project(tmp_path / "m.py").line_length == expected


def test_ruff_formats_when_the_project_configures_it(tmp_path: Path) -> None:
    from towel.formatting import formatter_for_project

    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 60\n")
    module = tmp_path / "m.py"
    module.write_text("x = 1\n")
    choice = formatter_for_project(module)
    formatter, note = choice.tool, choice.note
    assert formatter is not None and note.startswith("ruff")
    formatted = formatter(
        "value = helper('a', 'bbbbbbbbbbbbbbbbbbbb', 'cccccccccccccccccccc', 'dddddddddd')"
    )
    assert max(len(line) for line in formatted.splitlines()) <= 60
    assert '"a"' in formatted


def test_black_is_the_formatter_without_ruff_configuration(tmp_path: Path) -> None:
    from towel.formatting import formatter_for_project

    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    choice = formatter_for_project(tmp_path / "m.py")
    formatter, note = choice.tool, choice.note
    assert formatter is not None and note == "Black"


def test_isort_sorts_the_inserted_import_when_configured(tmp_path: Path) -> None:
    from towel.formatting import import_sorter_for_project

    (tmp_path / "pyproject.toml").write_text('[tool.isort]\nprofile = "black"\n')
    module = tmp_path / "m.py"
    source = (
        "from typing import Any\nimport os\nimport sys\n\n\ndef f() -> Any:\n    return os, sys\n"
    )
    # The file as it stood before Towel added ``from typing import Any``.
    module.write_text(source.replace("from typing import Any\n", ""))
    choice = import_sorter_for_project(module)
    finisher, note = choice.tool, choice.note
    assert finisher is not None and note == "isort"
    finished = finisher(str(module), source)
    assert finished.startswith("import os\nimport sys\nfrom typing import Any\n")


def test_ruff_import_rules_sort_when_selected(tmp_path: Path) -> None:
    from towel.formatting import import_sorter_for_project

    (tmp_path / "pyproject.toml").write_text('[tool.ruff.lint]\nselect = ["E", "I"]\n')
    module = tmp_path / "m.py"
    source = (
        "from typing import Any\nimport os\nimport sys\n\n\ndef f() -> Any:\n    return os, sys\n"
    )
    # The file as it stood before Towel added ``from typing import Any``.
    module.write_text(source.replace("from typing import Any\n", ""))
    choice = import_sorter_for_project(module)
    finisher, note = choice.tool, choice.note
    assert finisher is not None and note == "ruff import sorting"
    finished = finisher(str(module), source)
    assert finished.index("import os") < finished.index("from typing import Any")


def test_no_import_sorting_without_configuration(tmp_path: Path) -> None:
    from towel.formatting import import_sorter_for_project
    from towel.project_tools import ToolChoice

    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    assert import_sorter_for_project(tmp_path / "m.py") == ToolChoice(None, "")


def test_an_import_sorter_may_only_permute_imports() -> None:
    from towel.formatting import imports_permuted_only

    original = "import os\nimport sys\nx = 1\n"
    dropping = imports_permuted_only(lambda path, source: source.replace("import os\n", ""))
    assert dropping("m.py", original) == original  # refused: an import vanished
    rewriting = imports_permuted_only(lambda path, source: source.replace("x = 1", "x = 2"))
    assert rewriting("m.py", original) == original  # refused: code changed
    swapping = imports_permuted_only(lambda path, source: "import sys\nimport os\nx = 1\n")
    assert swapping("m.py", original) == "import sys\nimport os\nx = 1\n"
    guarded = (
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n"
        "    import sys\n    import os\nx = 1\n"
    )
    nested = imports_permuted_only(
        lambda path, source: source.replace(
            "    import sys\n    import os\n", "    import os\n    import sys\n"
        )
    )
    assert "    import os\n    import sys\n" in nested("m.py", guarded)


def test_dry_sorts_inserted_imports_for_an_isort_project(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "pyproject.toml").write_text('[tool.isort]\nprofile = "black"\n')
    (source_dir / "m.py").write_text(textwrap.dedent("""
            import os

            def first(items: list[int]) -> int:
                total = sum(items) + 1
                print(total, os.sep)
                return total * 2

            def second(items: list[int]) -> int:
                total = sum(items) + 1
                print(total, os.sep)
                return total * 3
            """))
    out = tmp_path / "out"
    assert (
        invoke(["dry", str(source_dir), str(out), "--no-interactive", "--progress", "none"]).status
        == 0
    )
    text = (out / "m.py").read_text()
    if "from typing import" in text:
        assert text.index("import os") < text.index("from typing import")
