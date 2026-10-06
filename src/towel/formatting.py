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
before and after, and each tool leaves alone what the project's
configuration of it excludes. :func:`import_sorter_for_project` supplies a
``FileFinisher`` that sorts a modified file's imports with ruff's ``I`` rules
or isort, as the project does, on a file the tool selects and already leaves
as it is (:func:`sort_added_imports`): only the imports Towel added may move,
since the order of a file's own imports is the order their modules run in.
The result is kept only when it permutes or merges import statements and
nothing else, and keeps the file's own imports in their order.
"""

from __future__ import annotations

import ast
from collections import OrderedDict
from collections import Counter
import configparser
from dataclasses import dataclass
import copy
import json
from enum import Enum
from pathlib import Path
import sys
import shutil
import subprocess
from typing import Callable, List, Mapping, NamedTuple, Optional, Set, Tuple, cast

from .canonical_ast import canonical_dump
from .project_layout import find_project_root, load_pyproject
from .project_tools import IsolatedFormatTool, ToolChoice, ToolFailure, python_tool_environment
from .diagnostics import LOG
from .source_text import decorator_line_numbers, definition_start_line, try_read_source

SnippetFormatter = Callable[[str], str]
"""Maps one generated snippet (a definition or a statement) to its formatted text."""

FileFinisher = Callable[[str, str], str]
"""Maps ``(path, source)`` of a modified file to its finished text, e.g. with imports sorted.

The file at ``path`` still holds the text the change started from."""

ImportSorter = Callable[[str, str], Optional[str]]
"""Maps ``(path, source)`` to ``source`` with its imports sorted by the project's tool.

``source`` itself for a file the tool's own configuration leaves alone; None
when the tool failed or declined, which it reports."""

TOOL_TIMEOUT_SECONDS = 120.0
"""How long an external formatter or sorter may take on one file before Towel gives up on it."""

DEFAULT_LINE_LENGTH = 88


class FormattingChangedCode(ValueError):
    """A formatter returned code whose syntax tree differs from its input, or failed on it.

    Either way nothing it returned is written: the proposal it was formatting
    is declined, and the run goes on.
    """


class LintRejected(FormattingChangedCode):
    """The configured Ruff check failed or found a diagnostic the change introduced."""


@dataclass(frozen=True)
class _LintDiagnostic:
    code: str
    message: str
    row: int
    column: int
    scopes: tuple[tuple[str, str], ...] = ()


def _lint_diagnostics(output: str) -> tuple[_LintDiagnostic, ...]:
    """Validate Ruff's located JSON diagnostics before using them as a baseline."""
    try:
        values: object = json.loads(output)
    except ValueError as error:
        raise LintRejected("Ruff returned invalid diagnostic JSON") from error
    if not isinstance(values, list):
        raise LintRejected("Ruff returned a diagnostic value that is not a list")
    diagnostics: list[_LintDiagnostic] = []
    for value in cast(list[object], values):
        if not isinstance(value, dict):
            raise LintRejected("Ruff returned a diagnostic that is not an object")
        record = cast(Mapping[str, object], value)
        code, message, location = record.get("code"), record.get("message"), record.get("location")
        if (
            not isinstance(code, str)
            or not isinstance(message, str)
            or not isinstance(location, dict)
        ):
            raise LintRejected("Ruff returned an unlocated diagnostic")
        coordinates = cast(Mapping[str, object], location)
        row, column = coordinates.get("row"), coordinates.get("column")
        if type(row) is not int or type(column) is not int or row < 1 or column < 1:
            raise LintRejected("Ruff returned an invalid diagnostic location")
        diagnostics.append(_LintDiagnostic(code, message, row, column))
    return tuple(diagnostics)


def _ruff_lint_guard(command: List[str], root: Path) -> FileFinisher:
    """Reject new diagnostics; only errors on exactly retained lines spend the baseline.

    A warning moved into a helper is conservatively new. Code, message, aligned
    line, column and named lexical scopes must all match; a removed warning
    cannot pay for one in a new helper. The file still holds this proposal's input.
    """
    from .type_baseline import unchanged_lines

    def diagnostics(path: str, source: str) -> tuple[_LintDiagnostic, ...]:
        try:
            result = subprocess.run(
                [
                    *command,
                    "check",
                    "--no-fix",
                    "--no-fix-only",
                    "--no-cache",
                    "--force-exclude",
                    "--output-format",
                    "json",
                    "--stdin-filename",
                    str(_counterpart(root, Path(path))),
                    "-",
                ],
                input=source,
                capture_output=True,
                text=True,
                cwd=root,
                env=python_tool_environment(),
                check=False,
                timeout=TOOL_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise LintRejected(f"The configured Ruff check failed: {error}") from error
        if result.returncode not in (0, 1):
            raise LintRejected(f"The configured Ruff check failed: {result.stderr.strip()}")
        found = _lint_diagnostics(result.stdout)
        if result.returncode == 1 and not found:
            raise LintRejected("Ruff failed without a located diagnostic")
        try:
            decorators = decorator_line_numbers(source)
            definitions = [
                node
                for node in ast.walk(ast.parse(source))
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            ]
        except SyntaxError as error:
            raise LintRejected(
                "Cannot establish Ruff diagnostic scopes in invalid source"
            ) from error
        return tuple(
            _LintDiagnostic(
                error.code,
                error.message,
                error.row,
                error.column,
                tuple(
                    (type(node).__name__, node.name)
                    for node in sorted(
                        definitions,
                        key=lambda node: (
                            definition_start_line(node, decorators),
                            -(node.end_lineno or node.lineno),
                        ),
                    )
                    if definition_start_line(node, decorators)
                    <= error.row
                    <= (node.end_lineno or node.lineno)
                ),
            )
            for error in found
        )

    def finish(path: str, source: str) -> str:
        original = try_read_source(Path(path))
        if original is None:
            raise LintRejected(f"Cannot read the configured Ruff baseline: {path}")
        lines = unchanged_lines(original, source).after_of
        baseline = Counter(
            _LintDiagnostic(error.code, error.message, lines[error.row], error.column, error.scopes)
            for error in diagnostics(path, original)
            if error.row in lines
        )
        introduced = Counter(diagnostics(path, source)) - baseline
        if introduced:
            first = next(iter(introduced))
            raise LintRejected(f"The change introduces Ruff {first.code}: {first.message}")
        return source

    return finish


def _ruff_configures_file(path: Path, root: Path) -> bool:
    """Whether Ruff can find a project configuration above this exact source file."""
    folder = _counterpart(root, path).parent
    while folder.is_relative_to(root):
        if _tool_section(folder, "ruff") or any(
            (folder / name).is_file() for name in ("ruff.toml", ".ruff.toml")
        ):
            return True
        if folder == root:
            break
        folder = folder.parent
    return False


def file_finisher_for_project(path: Path, *, sort_imports: bool = True) -> ToolChoice[FileFinisher]:
    """Sort added imports, then require the project's configured Ruff lint baseline."""
    sorting = (
        import_sorter_for_project(path) if sort_imports else ToolChoice[FileFinisher](None, "")
    )
    command = _ruff_executable()
    if command is None:
        return (
            ToolChoice(sorting.tool, "ruff is configured but not installed; lint not checked")
            if project_configures_ruff(path)
            else sorting
        )
    root = _root(path)
    guard = _ruff_lint_guard(command, root)

    def finish(file_path: str, source: str) -> str:
        finished = sorting.tool(file_path, source) if sorting.tool else source
        return (
            guard(file_path, finished) if _ruff_configures_file(Path(file_path), root) else finished
        )

    return ToolChoice(
        finish, "; ".join(note for note in (sorting.note, "configured Ruff lint") if note)
    )


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
    raises :class:`FormattingChangedCode`, and so does any failure of the
    formatter itself: Black's ``InvalidInput`` on text it cannot parse, ruff's
    nonzero exit or timeout, or anything else a formatter raises. Trailing
    newlines are dropped so the caller can indent and splice the text as it
    does unformatted output.
    """

    def format_snippet(source: str) -> str:
        try:
            formatted = formatter(source)
        except FormattingChangedCode:
            raise
        except Exception as error:
            # The formatter is another tool's code; whatever stops it declines
            # the proposal it was given, never the run (numbagg's snippet
            # that Black could not parse ended every formatted run).
            raise FormattingChangedCode(
                "the formatter failed: "
                + (
                    str(error)
                    if isinstance(error, ToolFailure)
                    else f"{type(error).__name__}: {error}"
                )
            ) from error
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


def _leading_blank_lines(source: str) -> int:
    for index, line in enumerate(source.split("\n")):
        if line.strip():
            return index
    return source.count("\n") + 1


def format_at_indentation(formatter: SnippetFormatter, source: str, prefix: str) -> str:
    """Format a snippet at its destination depth, returning column-zero text.

    A call formatted at column zero can exceed the project's width after
    insertion into a function or method. Inert surrounding suites let the
    formatter see that depth without formatting any existing source. Like
    the materializer's reindentation, each tab represents a four-space level.
    The suites are only formatter input, never generated program statements.
    """
    depth = (len(prefix.expandtabs(4)) + 3) // 4
    if not depth:
        return formatter(source)
    indentation = "    " * depth
    wrapped = "\n".join(
        [
            *("    " * level + "if True:" for level in range(depth)),
            *(indentation + line for line in source.split("\n")),
        ]
    )
    formatted = formatter(wrapped)
    tree = ast.parse(formatted)
    body = tree.body
    for _ in range(depth):
        if len(body) != 1 or not isinstance(body[0], ast.If):
            raise FormattingChangedCode("formatting changed the snippet's enclosing suites")
        suite = body[0]
        body = suite.body
    lines = formatted.split("\n")[suite.lineno :]
    # Formatter indentation may use tabs or a configured width of its own.
    first = next(line for line in lines if line.strip())
    margin = first[: len(first) - len(first.lstrip())]
    unwrapped = "\n".join(
        line[len(margin) :] if line.startswith(margin) else line for line in lines
    )
    # Ruff separates a definition from its synthetic enclosing ``if`` with a
    # blank line. That suite's spacing does not belong to the destination;
    # insertion supplies its own separator. Keep blanks the input already had.
    extra = max(_leading_blank_lines(unwrapped) - _leading_blank_lines(source), 0)
    unwrapped = "\n".join(unwrapped.split("\n")[extra:])
    if canonical_dump(ast.parse(unwrapped)) != canonical_dump(ast.parse(source)):
        raise FormattingChangedCode(
            "formatting changed the generated code's meaning:\n" + unwrapped
        )
    return unwrapped.rstrip("\n")


def black_formatter(settings: BlackSettings) -> SnippetFormatter:
    """A checked formatter that runs Black with ``settings``.

    Raises ``ImportError`` when Black is not installed; install the
    ``format`` extra (``pip install "code-towel[format]"``) to provide it.
    """
    return _black_formatter(IsolatedFormatTool("black"), settings)


def _black_formatter(tool: IsolatedFormatTool, settings: BlackSettings) -> SnippetFormatter:
    def run_black(source: str) -> str:
        return tool.render(
            {
                "source": source,
                "line_length": settings.line_length,
                "string_normalization": settings.string_normalization,
            }
        )

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
        probe = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                "import importlib.util;raise SystemExit(0 if importlib.util.find_spec('ruff') else 1)",
            ],
            capture_output=True,
            env=python_tool_environment(),
            timeout=TOOL_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise FormattingChangedCode(f"Could not discover installed Ruff: {error}") from error

    if probe.returncode == 0:
        return [sys.executable, "-I", "-m", "ruff"]
    if probe.returncode != 1 or probe.stderr:
        raise FormattingChangedCode(
            "Could not discover installed Ruff: " + probe.stderr.decode("utf-8", "replace")
        )
    executable = shutil.which("ruff")
    return [executable] if executable else None


def ruff_formatter(path: Path) -> SnippetFormatter:
    """A checked formatter that runs ``ruff format`` with the project's configuration.

    ``--force-exclude`` makes ruff apply its ``exclude``, ``extend-exclude``
    and ``format.exclude`` to ``path``, which names the snippet's text on
    stdin: where they exclude it, ruff returns the snippet as it was given.
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
                [*command, "format", "--force-exclude", "--stdin-filename", target, "-"],
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


def _unformatted(source: str) -> str:
    return source


def black_excludes(path: Path) -> bool:
    """Whether Black, run over the project, leaves ``path`` alone by its own configuration.

    Black matches ``exclude`` (its defaults when unset), ``extend-exclude``
    and ``force-exclude`` against each directory it walks into and each file
    it finds, as a path from the project root with a leading ``/`` and, for
    a directory, a trailing one (``black.files.gen_python_files``); a path it
    is given directly answers to ``force-exclude`` alone. Any of them
    excluding ``path`` or a directory above it counts, since either way of
    running Black would leave it unformatted. Black's own error for a regex
    that does not compile is its own to report, so such a pattern excludes
    nothing here.
    """
    tool = IsolatedFormatTool("black")
    try:
        return _black_excludes(tool, path)
    finally:
        tool.close()


def _black_excludes(tool: IsolatedFormatTool, path: Path) -> bool:
    root = _root(path)
    configuration = _tool_section(root, "black")
    patterns = {
        name: (
            None
            if not configuration.get(name)
            else configuration[name] if isinstance(configuration[name], str) else ""
        )
        for name in ("exclude", "extend-exclude", "force-exclude")
    }
    return tool.excludes(
        {"operation": "excludes", "root": str(root), "path": str(path), **patterns}
    )


def _black_for_project(path: Path, note: str) -> ToolChoice[SnippetFormatter]:
    """Black with the project's settings, or no formatting where its configuration excludes ``path``.

    Raises ``ImportError`` when Black is not installed.
    """
    tool = IsolatedFormatTool("black")
    try:
        excluded = _black_excludes(tool, path)
    except BaseException:
        tool.close()
        raise
    if excluded:
        tool.close()
        return ToolChoice(checked(_unformatted), f"{note}, which the project excludes {path} from")
    return ToolChoice(_black_formatter(tool, BlackSettings.for_project(path)), note)


def formatter_for_project(path: Path) -> ToolChoice[SnippetFormatter]:
    """The formatter the project's configuration calls for, and a note on what was chosen.

    ruff when the project configures it and it is installed; otherwise Black
    when installed; otherwise none. The note explains a fallback or absence.
    A formatter whose configuration excludes ``path`` leaves the code Towel
    writes there as ``ast.unparse`` renders it.
    """
    if project_configures_ruff(path):
        try:
            return ToolChoice(ruff_formatter(path), "ruff (project configuration)")
        except FormatterUnavailable:
            note = "ruff is configured but not installed"
            try:
                return _black_for_project(path, f"Black; {note}")
            except ImportError:
                return ToolChoice(None, f"{note}, and Black is not installed either")
    try:
        return _black_for_project(path, "Black")
    except ImportError:
        return ToolChoice(None, "Black is not installed")


def import_sorter_for_project(path: Path) -> ToolChoice[FileFinisher]:
    """A finisher that sorts a modified file's imports the way the project does, if it does.

    ruff's ``I`` rules when the project selects them and ruff is installed;
    isort when the project configures it and isort is importable. A project
    that configures neither has its imports left where Towel put them. The
    tool sorts only a file its own configuration selects, and only one whose
    imports it already leaves as they are (``sorted_where_already_sorted``).
    """
    if project_configures_ruff_import_sorting(path):
        command = _ruff_executable()
        if command is None:
            return ToolChoice(None, "ruff import sorting is configured but ruff is not installed")
        note = "ruff import sorting"
        return ToolChoice(
            sorted_where_already_sorted(_ruff_sorter(command, _root(path)), note), note
        )
    if project_configures_isort(path):
        try:
            sorter = _isort_sorter(_root(path))
        except ImportError:
            return ToolChoice(None, "isort is configured but not installed")
        return ToolChoice(sorted_where_already_sorted(sorter, "isort"), "isort")
    return ToolChoice(None, "")


def _counterpart(root: Path, path: Path) -> Path:
    """``path`` as the project at ``root`` holds it: itself, or the file its staged copy was made from.

    A run refactors a copy of the whole project (``filesystem.staged_project``),
    but a tool's own file selection is written for the project: ``exclude =
    ["pkg/app.py"]`` names ``root/pkg/app.py``, and isort's ``skip_gitignore``
    asks the project's git. The copy keeps every file at its place relative to
    ``root``, so the counterpart is the longest trailing part of ``path`` that
    ``root`` also holds. A path with none is its own.
    """
    absolute = path.resolve()
    if absolute.is_relative_to(root):
        return absolute
    for ancestor in reversed(absolute.parents):
        candidate = root / absolute.relative_to(ancestor)
        if candidate.exists():
            return candidate
    return absolute


def _ruff_sorter(command: List[str], root: Path) -> ImportSorter:
    """ruff's ``I`` rules over one file's text, with the project's configuration.

    ``--force-exclude`` makes ruff apply ``exclude``, ``extend-exclude`` and
    ``lint.exclude`` to text it reads from stdin, as it does to the files it
    finds itself: an excluded file comes back as it went in. ``per-file-ignores``
    and ``# ruff: noqa`` need no flag, since they only silence the rule.
    """

    def sort(file_path: str, source: str) -> Optional[str]:
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
                    "--force-exclude",
                    "--stdin-filename",
                    str(_counterpart(root, Path(file_path))),
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
            return None
        if completed.returncode != 0 or not completed.stdout:
            LOG.warning(
                "ruff import sorting failed for %s; imports left as assembled: %s",
                file_path,
                completed.stderr.strip(),
            )
            return None
        return completed.stdout

    return sort


def _isort_sorter(root: Path) -> ImportSorter:
    """isort over one file's text, with the project's configuration found from ``root``."""
    tool = IsolatedFormatTool("isort")

    def sort(file_path: str, source: str) -> Optional[str]:
        target = _counterpart(root, Path(file_path))
        try:
            return tool.render({"source": source, "root": str(root), "path": str(target)})
        except ToolFailure as error:
            LOG.warning("isort declined %s; imports left as assembled: %s", file_path, error)
            return None

    return sort


class SortOutcome(Enum):
    """What came of sorting one modified file's imports."""

    SORTED = "sorted"
    UNCHANGED = "the sorter left the text as it was"
    TOOL_FAILED = "the sorter failed or declined, and said why"
    ORIGINAL_UNREADABLE = "the file's text before the change could not be read"
    ORIGINAL_UNSORTED = "the sorter would change the imports the file already had"
    NOT_A_PERMUTATION = "the sorter changed more than the order of imports"
    MOVED_EXISTING = "the sorter moved imports the file already had"


@dataclass(frozen=True)
class SortedImports:
    """A modified file's finished text, and what sorting its imports came to."""

    text: str
    outcome: SortOutcome


def sort_added_imports(
    sort: ImportSorter, path: str, original: str, assembled: str
) -> SortedImports:
    """``assembled`` with the imports Towel added sorted by ``sort``, and nothing else moved.

    ``original`` is the file's text before the change. A sorter that would
    change it is not run: the project does not sort this file that way, and
    sorting it would reorder imports Towel never touched, whose order is the
    order their modules run in. Where ``original`` is already as the sorter
    leaves it, its imports are in the sorter's order, so sorting
    ``assembled`` can move only the imports Towel added; that is checked
    rather than assumed. The result must only permute or merge imports
    (``imports_permuted_only``), and the imports ``original`` had must stay
    in the order they had, or ``assembled`` is kept as it is.
    """
    unchanged = sort(path, original)
    if unchanged is None:
        return SortedImports(assembled, SortOutcome.TOOL_FAILED)
    if unchanged != original:
        return SortedImports(assembled, SortOutcome.ORIGINAL_UNSORTED)
    finished = sort(path, assembled)
    if finished is None:
        return SortedImports(assembled, SortOutcome.TOOL_FAILED)
    if finished == assembled:
        return SortedImports(assembled, SortOutcome.UNCHANGED)
    trees = _parsed(original, assembled, finished)
    if trees is None or _normalized_imports(trees[1]) != _normalized_imports(trees[2]):
        return SortedImports(assembled, SortOutcome.NOT_A_PERMUTATION)
    if not _keeps_existing_import_order(*trees):
        return SortedImports(assembled, SortOutcome.MOVED_EXISTING)
    return SortedImports(finished, SortOutcome.SORTED)


_SORT_REPORTS: Mapping[SortOutcome, str] = {
    SortOutcome.ORIGINAL_UNSORTED: (
        "%s: %s would change the imports this file already has, so it was not run on the"
        " file; the imports Towel added stay where it put them"
    ),
    SortOutcome.NOT_A_PERMUTATION: (
        "%s: %s changed more than the order of its imports, so its result was not used;"
        " the imports Towel added stay where it put them"
    ),
    SortOutcome.MOVED_EXISTING: (
        "%s: %s moved imports this file already had, which would change the order their"
        " modules run in, so its result was not used; the imports Towel added stay where"
        " it put them"
    ),
    SortOutcome.ORIGINAL_UNREADABLE: (
        "%s: its text before the change could not be read, so %s was not run on it"
    ),
}

_SORT_MEMO_SIZE = 256
"""How many ``(path, text)`` sorts a finisher remembers: every variant of a change re-checks one original."""


def sorted_where_already_sorted(sort: ImportSorter, tool: str) -> FileFinisher:
    """The ``FileFinisher`` that sorts with ``sort`` by ``sort_added_imports``, reporting what it skipped.

    The file at the finisher's ``path`` still holds the text the change
    started from, as it does whenever the engine finishes a file, and that
    is the original ``sort_added_imports`` judges by. ``sort`` depends only
    on its arguments and the project's configuration, which a run does not
    change, so its results are remembered: each variant of a change asks
    again about the same original. A file that is left unsorted is reported
    once, naming why.
    """
    remembered: "OrderedDict[Tuple[str, str], str]" = OrderedDict()
    reported: Set[Tuple[str, SortOutcome]] = set()

    def memoized(file_path: str, source: str) -> Optional[str]:
        key = (file_path, source)
        if key in remembered:
            remembered.move_to_end(key)
            return remembered[key]
        result = sort(file_path, source)
        if result is not None:
            remembered[key] = result
            if len(remembered) > _SORT_MEMO_SIZE:
                remembered.popitem(last=False)
        return result

    def finish(file_path: str, assembled: str) -> str:
        original = try_read_source(file_path)
        result = (
            SortedImports(assembled, SortOutcome.ORIGINAL_UNREADABLE)
            if original is None
            else sort_added_imports(memoized, file_path, original, assembled)
        )
        report = _SORT_REPORTS.get(result.outcome)
        if report is not None and (file_path, result.outcome) not in reported:
            reported.add((file_path, result.outcome))
            LOG.warning(report, file_path, tool)
        return result.text

    return finish


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
        trees = _parsed(source, finished)
        if trees is None or _normalized_imports(trees[0]) != _normalized_imports(trees[1]):
            return source
        return finished

    return finish


def _parsed(*sources: str) -> Optional[Tuple[ast.Module, ...]]:
    try:
        return tuple(ast.parse(source) for source in sources)
    except SyntaxError:
        return None


class _ImportedBinding(NamedTuple):
    """One name an import statement binds: ``import a.b as c`` or ``from ..a import b as c``."""

    source: str
    name: str
    asname: Optional[str]


def _import_bindings(module: ast.Module) -> List[_ImportedBinding]:
    """Every name the module's import statements bind, in the order the text writes them."""
    statements = sorted(
        (node for node in ast.walk(module) if isinstance(node, (ast.Import, ast.ImportFrom))),
        key=lambda node: (node.lineno, node.col_offset),
    )
    return [
        _ImportedBinding(
            (
                "import"
                if isinstance(statement, ast.Import)
                else f"from {'.' * statement.level}{statement.module or ''}"
            ),
            alias.name,
            alias.asname,
        )
        for statement in statements
        for alias in statement.names
    ]


def _keeps_existing_import_order(
    original: ast.Module, assembled: ast.Module, finished: ast.Module
) -> bool:
    """Whether ``finished`` binds the names ``original`` imported in the order ``assembled`` did.

    Each import runs its module at the place it stands, so the order of the
    imports a file already had is the order of their import-time effects:
    ``from pkg import zeta`` then ``from pkg import alpha`` registers zeta's
    plugin first, and ``from pkg import alpha, zeta`` does not. Only an
    import Towel added may move.
    """
    existing = set(_import_bindings(original))

    def order(module: ast.Module) -> List[_ImportedBinding]:
        return [binding for binding in _import_bindings(module) if binding in existing]

    return order(assembled) == order(finished)


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
            and (statement.level != 0 or statement.module != "__future__")
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
