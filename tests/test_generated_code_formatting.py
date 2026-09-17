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

import pytest

from tests.test_cli_integration import invoke
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


def _refactor(path: Path, **engine_options: object) -> str:
    engine = UnificationRefactorEngine(min_lines=2, **engine_options)  # type: ignore[arg-type]
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
            ["dry", str(source), str(formatted), "--non-interactive", "--progress", "none"]
        ).status
        == 0
    )
    assert (
        invoke(
            [
                "dry",
                str(source),
                str(plain),
                "--non-interactive",
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
