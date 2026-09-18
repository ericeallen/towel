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

"""Formatting of the code Towel generates.

``ast.unparse`` renders a helper or a call on one line with single-quoted
strings. A formatter makes the inserted text read like the surrounding code.
The engine accepts any ``SnippetFormatter``; this module supplies Black, when
it is installed, configured from the project's own ``[tool.black]`` settings.
Formatting must never change meaning, so every formatter is wrapped by
:func:`checked`, which compares the syntax tree before and after.
"""

from __future__ import annotations

import ast
import configparser
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional

from .unification.project_layout import _find_project_root, _load_pyproject

SnippetFormatter = Callable[[str], str]
"""Maps one generated snippet (a definition or a statement) to its formatted text."""

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
        root = _find_project_root(path)
        pyproject = _load_pyproject(root)
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
        if ast.dump(ast.parse(formatted)) != ast.dump(ast.parse(source)):
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
