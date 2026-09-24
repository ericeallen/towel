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

"""Formatting of the code Towel generates, with the project's own tools.

``ast.unparse`` renders a helper or a call on one line with single-quoted
strings. A formatter makes the inserted text read like the surrounding code.
The engine accepts any ``SnippetFormatter``; :func:`formatter_for_project`
supplies ``ruff format`` when the project configures ruff and Black
otherwise, whichever is installed, at the line length the project declares
in any of its tool sections. Formatting must never change meaning, so every
formatter is wrapped by :func:`checked`, which compares the syntax tree
before and after. :func:`import_sorter_for_project` supplies a
``FileFinisher`` that sorts a modified file's imports with ruff's ``I`` rules
or isort, as the project does, guarded by :func:`imports_permuted_only`,
which keeps the sorter's result only when it permutes or merges import
statements and nothing else.
"""

from __future__ import annotations

import ast
import configparser
from dataclasses import dataclass
import copy
from pathlib import Path
import sys
import shutil
import subprocess
from typing import Callable, List, Mapping, Optional

from .canonical_ast import canonical_dump
from .project_layout import find_project_root, load_pyproject
from .project_tools import ToolChoice, python_tool_environment
from .diagnostics import LOG

SnippetFormatter = Callable[[str], str]
"""Maps one generated snippet (a definition or a statement) to its formatted text."""

FileFinisher = Callable[[str, str], str]
"""Maps ``(path, source)`` of a modified file to its finished text, e.g. with imports sorted."""

TOOL_TIMEOUT_SECONDS = 120.0
"""How long an external formatter or sorter may take on one file before Towel gives up on it."""

DEFAULT_LINE_LENGTH = 88


class FormattingChangedCode(ValueError):
    """A formatter returned code whose syntax tree differs from its input."""


@dataclass(frozen=True)
class BlackSettings:
    """The Black options that shape generated code: width and string quoting."""

    line_length: int = DEFAULT_LINE_LENGTH
    string_normalization: bool = True

    @classmethod
    def for_project(cls, path: Path) -> "BlackSettings":
        """Settings from the project's own configuration above ``path``, else defaults.

        The line length is the limit the project declares for its code, in
        the first of: ``[tool.black]``, ``[tool.ruff]``, ``[tool.pycodestyle]``
        in ``pyproject.toml``; ``[flake8]`` or ``[pycodestyle]`` in
        ``setup.cfg``, ``tox.ini`` or ``.flake8``. A project that checks its
        own style (pycodestyle at 79) would otherwise fail its own check on
        code formatted to Black's default of 88.
        """
        root = find_project_root(path)
        pyproject = load_pyproject(root)
        tool = pyproject.get("tool", {}) if isinstance(pyproject, Mapping) else {}
        if not isinstance(tool, Mapping):
            tool = {}
        black = tool.get("black", {})
        skip_normalization = (
            black.get("skip-string-normalization", False) if isinstance(black, Mapping) else False
        )
        line_length = _declared_line_length(root, tool)
        return cls(
            line_length=line_length if line_length is not None else DEFAULT_LINE_LENGTH,
            string_normalization=not bool(skip_normalization),
        )


def _declared_line_length(root: Path, tool: Mapping[str, object]) -> Optional[int]:
    """The line limit the project declares, from its formatter or linter configuration."""
    for section_name, key in (
        ("black", "line-length"),
        ("ruff", "line-length"),
        ("pycodestyle", "max-line-length"),
    ):
        section = tool.get(section_name)
        if isinstance(section, Mapping):
            value = section.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                return value
    for filename in ("setup.cfg", "tox.ini", ".flake8"):
        candidate = root / filename
        if not candidate.is_file():
            continue
        parser = configparser.ConfigParser()
        try:
            parser.read(candidate, encoding="utf-8")
        except (configparser.Error, OSError, UnicodeError):
            continue
        for section_name in ("flake8", "pycodestyle", "pep8"):
            if parser.has_section(section_name):
                for key in ("max-line-length", "max_line_length"):
                    text = parser.get(section_name, key, fallback=None)
                    if text is not None and text.strip().isdigit():
                        return int(text.strip())
    return None


def checked(formatter: SnippetFormatter) -> SnippetFormatter:
    """``formatter`` guarded so it can only change layout, never meaning.

    The result is compared with the input as syntax trees; any difference
    raises :class:`FormattingChangedCode`. Trailing newlines are dropped so
    the caller can indent and splice the text as it does unformatted output.
    """

    def format_snippet(source: str) -> str:
        formatted = formatter(source)
        try:
            formatted_tree = ast.parse(formatted)
        except SyntaxError as error:
            raise FormattingChangedCode(f"formatting produced unparsable code: {error}") from error
        if canonical_dump(formatted_tree) != canonical_dump(ast.parse(source)):
            raise FormattingChangedCode(
                "formatting changed the generated code's meaning:\n" + formatted
            )
        return formatted.rstrip("\n")

    return format_snippet


def black_formatter(settings: BlackSettings) -> SnippetFormatter:
    """A checked formatter that runs Black with ``settings``.

    Raises ``ImportError`` when Black is not installed; install the
    ``format`` extra (``pip install "code-towel[format]"``) to provide it.
    """
    import black

    mode = black.Mode(
        line_length=settings.line_length,
        string_normalization=settings.string_normalization,
    )

    def run_black(source: str) -> str:
        return black.format_str(source, mode=mode)

    return checked(run_black)


class FormatterUnavailable(RuntimeError):
    """The tool a project configures is not installed."""


def _root(path: Path) -> Path:
    return find_project_root(path)


def _tool_section(root: Path, name: str) -> Mapping[str, object]:
    tool = load_pyproject(root).get("tool", {})
    section = tool.get(name, {}) if isinstance(tool, Mapping) else {}
    return section if isinstance(section, Mapping) else {}


def project_configures_ruff(path: Path) -> bool:
    """Whether the project configures ruff (``[tool.ruff]``, ``ruff.toml`` or ``.ruff.toml``)."""
    root = _root(path)
    return bool(_tool_section(root, "ruff")) or any(
        (root / name).is_file() for name in ("ruff.toml", ".ruff.toml")
    )


def project_configures_ruff_import_sorting(path: Path) -> bool:
    """Whether ruff's import-sorting rules (``I``) are selected in the project's configuration."""
    root = _root(path)
    ruff = _tool_section(root, "ruff")
    lint = ruff.get("lint", {}) if isinstance(ruff.get("lint", {}), Mapping) else {}
    selected: List[str] = []
    for section in (ruff, lint):
        if not isinstance(section, Mapping):
            continue
        for key in ("select", "extend-select"):
            value = section.get(key)
            if isinstance(value, list):
                selected.extend(str(item) for item in value)
    return any(rule == "ALL" or rule == "I" or rule.startswith("I0") for rule in selected)


def project_configures_isort(path: Path) -> bool:
    """Whether the project configures isort (``[tool.isort]``, ``.isort.cfg``, or an ``[isort]`` section)."""
    root = _root(path)
    if _tool_section(root, "isort"):
        return True
    if (root / ".isort.cfg").is_file():
        return True
    for filename in ("setup.cfg", "tox.ini"):
        candidate = root / filename
        if candidate.is_file():
            parser = configparser.ConfigParser()
            try:
                parser.read(candidate, encoding="utf-8")
            except (configparser.Error, OSError, UnicodeError):
                continue
            if parser.has_section("isort"):
                return True
    return False


def _ruff_executable() -> Optional[List[str]]:
    """How to run ruff: this interpreter's copy first, then one on PATH."""
    try:
        import ruff  # noqa: F401
    except ImportError:
        executable = shutil.which("ruff")
        return [executable] if executable else None
    # Both callers run from the analyzed project's root. Do not let a
    # project-owned ruff.py, or a shadow of one of its dependencies, run.
    return [sys.executable, "-I", "-m", "ruff"]


def ruff_formatter(path: Path) -> SnippetFormatter:
    """A checked formatter that runs ``ruff format`` with the project's configuration.

    Raises :class:`FormatterUnavailable` when ruff is not installed.
    """
    command = _ruff_executable()
    if command is None:
        raise FormatterUnavailable("ruff")
    root = _root(path)
    target = str(path.resolve())

    def run_ruff(source: str) -> str:
        try:
            completed = subprocess.run(
                [*command, "format", "--stdin-filename", target, "-"],
                input=source,
                capture_output=True,
                text=True,
                cwd=str(root),
                env=python_tool_environment(),
                check=False,
                timeout=TOOL_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as error:
            raise FormattingChangedCode(f"ruff format timed out after {error.timeout} s") from error
        if completed.returncode != 0:
            raise FormattingChangedCode(f"ruff format failed: {completed.stderr.strip()}")
        return completed.stdout

    return checked(run_ruff)


def formatter_for_project(path: Path) -> ToolChoice[SnippetFormatter]:
    """The formatter the project's configuration calls for, and a note on what was chosen.

    ruff when the project configures it and it is installed; otherwise Black
    when installed; otherwise none. The note explains a fallback or absence.
    """
    if project_configures_ruff(path):
        try:
            return ToolChoice(ruff_formatter(path), "ruff (project configuration)")
        except FormatterUnavailable:
            note = "ruff is configured but not installed"
            try:
                return ToolChoice(
                    black_formatter(BlackSettings.for_project(path)), f"Black; {note}"
                )
            except ImportError:
                return ToolChoice(None, f"{note}, and Black is not installed either")
    try:
        return ToolChoice(black_formatter(BlackSettings.for_project(path)), "Black")
    except ImportError:
        return ToolChoice(None, "Black is not installed")


def import_sorter_for_project(path: Path) -> ToolChoice[FileFinisher]:
    """A finisher that sorts a modified file's imports the way the project does, if it does.

    ruff's ``I`` rules when the project selects them and ruff is installed;
    isort when the project configures it and isort is importable. A project
    that configures neither has its imports left where Towel put them.
    """
    if project_configures_ruff_import_sorting(path):
        command = _ruff_executable()
        if command is None:
            return ToolChoice(None, "ruff import sorting is configured but ruff is not installed")
        root = _root(path)

        def sort_with_ruff(file_path: str, source: str) -> str:
            try:
                completed = subprocess.run(
                    [
                        *command,
                        "check",
                        "--select",
                        "I",
                        "--fix",
                        "--exit-zero",
                        "--quiet",
                        "--stdin-filename",
                        str(Path(file_path).resolve()),
                        "-",
                    ],
                    input=source,
                    capture_output=True,
                    text=True,
                    cwd=str(root),
                    env=python_tool_environment(),
                    check=False,
                    timeout=TOOL_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired as error:
                LOG.warning("ruff import sorting timed out for %s (%s s)", file_path, error.timeout)
                return source
            if completed.returncode != 0 or not completed.stdout:
                LOG.warning(
                    "ruff import sorting failed for %s; imports left as assembled: %s",
                    file_path,
                    completed.stderr.strip(),
                )
                return source
            return completed.stdout

        return ToolChoice(imports_permuted_only(sort_with_ruff), "ruff import sorting")
    if project_configures_isort(path):
        try:
            import isort
        except ImportError:
            return ToolChoice(None, "isort is configured but not installed")
        settings_path = str(_root(path))

        def sort_with_isort(file_path: str, source: str) -> str:
            try:
                return isort.code(
                    source,
                    config=isort.Config(settings_path=settings_path),
                    file_path=Path(file_path),
                )
            except isort.exceptions.ISortError as error:
                # A file the project told isort to skip, or one it declines:
                # a sorter never costs a refactoring.
                LOG.warning("isort declined %s; imports left as assembled: %s", file_path, error)
                return source

        return ToolChoice(imports_permuted_only(sort_with_isort), "isort")
    return ToolChoice(None, "")


def imports_permuted_only(finisher: FileFinisher) -> FileFinisher:
    """``finisher`` guarded so it can only reorder or merge import statements.

    Each consecutive group of imports may move or merge within its own
    statement list, including inside ``if TYPE_CHECKING:`` or ``try``.
    Repeated bindings to the same name keep their order. Other statements,
    wildcard imports and future imports are boundaries a sort cannot cross.
    A result that fails that test is discarded and the text is left as
    Towel assembled it: a sorter must never cost a refactoring.
    """

    def finish(file_path: str, source: str) -> str:
        finished = finisher(file_path, source)
        if finished == source:
            return source
        try:
            before, after = ast.parse(source), ast.parse(finished)
        except SyntaxError:
            return source
        if _normalized_imports(before) != _normalized_imports(after):
            return source
        return finished

    return finish


def _normalized_imports(module: ast.Module) -> str:
    """A dump that ignores safe permutations within each import group."""
    normalized = copy.deepcopy(module)
    for node in ast.walk(normalized):
        for field, value in ast.iter_fields(node):
            if isinstance(value, list) and value and isinstance(value[0], ast.stmt):
                setattr(node, field, _normalize_import_groups(value))
    return canonical_dump(normalized)


def _normalize_import_groups(statements: list[ast.stmt]) -> list[ast.stmt]:
    """Sort independent bindings, keeping every binding's providers in order.

    Splitting aliases into singleton imports lets merged statements compare
    equal. Keeping an ordered list per bound name preserves last-writer wins
    for aliases such as ``import math as numeric; import cmath as numeric``.
    Wildcard imports bind unknown names, so they cannot join a sorted group.
    """
    result: list[ast.stmt] = []
    bindings: dict[str, list[ast.stmt]] = {}

    def flush() -> None:
        result.extend(statement for name in sorted(bindings) for statement in bindings[name])
        bindings.clear()

    for statement in statements:
        if isinstance(statement, ast.Import):
            for alias in statement.names:
                name = alias.asname or alias.name.split(".")[0]
                bindings.setdefault(name, []).append(ast.Import(names=[alias]))
        elif (
            isinstance(statement, ast.ImportFrom)
            and statement.module != "__future__"
            and all(alias.name != "*" for alias in statement.names)
        ):
            for alias in statement.names:
                name = alias.asname or alias.name
                bindings.setdefault(name, []).append(
                    ast.ImportFrom(module=statement.module, names=[alias], level=statement.level)
                )
        else:
            flush()
            result.append(statement)
    flush()
    return result
