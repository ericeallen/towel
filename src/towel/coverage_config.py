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

"""The lines a project's coverage.py excludes, from its configuration, read as coverage.py reads it.

coverage.py excludes every line one of its exclusion regexes matches, and a
whole clause or decorated definition when the line opens one. The regexes
are its defaults (``DEFAULT_EXCLUDE``) unless the configuration it uses sets
``exclude_lines``, which replaces them; ``exclude_also`` adds to whichever
apply. Its configuration is the first of these that it can use, in the
directory it runs from, here the project's root: ``COVERAGE_RCFILE`` or
``.coveragerc`` (``[report]`` or ``[coverage:report]``), ``.coveragerc.toml``,
``setup.cfg`` and ``tox.ini`` (``[coverage:report]``), and ``pyproject.toml``
(``[tool.coverage.report]``). ``.coveragerc``, ``.coveragerc.toml`` and a named
file are used whenever they exist; the others only when they set some
coverage.py option, so a ``setup.cfg`` setting only ``[coverage:run]`` still
decides, and its lack of ``exclude_lines`` means the defaults. Values have
``$VAR`` and ``${VAR}`` replaced from the environment and each regex is
stripped and compiled, as coverage.py does. This follows coverage.py 7.16.0
(``coverage/config.py``, ``tomlconfig.py``, ``misc.py``).

A configuration coverage.py itself could not read, because it does not
parse, holds an invalid regex or a value of the wrong type, or is named by
``COVERAGE_RCFILE`` and absent, gives the defaults and a ``problem`` saying
why, for the run to report.
"""

from __future__ import annotations

import configparser
from dataclasses import dataclass
import os
from pathlib import Path
import re
import tomllib
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

# coverage.py 7.16.0, coverage/config.py: DEFAULT_EXCLUDE.
DEFAULT_EXCLUDE: Tuple[str, ...] = (
    r"#\s*(pragma|PRAGMA)[:\s]?\s*(no|NO)\s*(cover|COVER)",
    r"^\s*(((async )?def .*?)?[\])]+(\s*->.*?)?:\s*)?\.\.\.\s*(#|$)",
    r"if (typing\.)?TYPE_CHECKING:",
)

# coverage.py 7.16.0, coverage/config.py: CoverageConfig.CONFIG_FILE_OPTIONS,
# by section. A file that is not coverage.py's own is used only when it sets
# one of these (or a ``[paths]`` entry).
_OPTIONS: Mapping[str, FrozenSet[str]] = {
    "run": frozenset(
        {
            "_crash",
            "branch",
            "command_line",
            "concurrency",
            "context",
            "core",
            "cover_pylib",
            "data_file",
            "debug",
            "debug_file",
            "disable_warnings",
            "dynamic_context",
            "include",
            "omit",
            "parallel",
            "patch",
            "plugins",
            "relative_files",
            "sigterm",
            "source",
            "source_dirs",
            "source_pkgs",
            "timid",
        }
    ),
    "report": frozenset(
        {
            "contexts",
            "exclude_also",
            "exclude_lines",
            "fail_under",
            "format",
            "ignore_errors",
            "include",
            "include_namespace_packages",
            "omit",
            "partial_also",
            "partial_branches",
            "partial_branches_always",
            "precision",
            "show_missing",
            "skip_covered",
            "skip_empty",
            "sort",
        }
    ),
    "html": frozenset(
        {"directory", "extra_css", "show_contexts", "skip_covered", "skip_empty", "title"}
    ),
    "xml": frozenset({"output", "package_depth"}),
    "json": frozenset({"output", "pretty_print", "show_contexts"}),
    "lcov": frozenset({"line_checksums", "output"}),
}


def join_regex(regexes: Sequence[str]) -> str:
    """One regex matching any of ``regexes``, as coverage.py joins them; empty for none."""
    if len(regexes) == 1:
        return regexes[0]
    return "|".join(f"(?:{regex})" for regex in regexes)


@dataclass(frozen=True)
class CoverageExclusion:
    """The regexes a project's coverage.py excludes lines by, and where they came from."""

    regexes: Tuple[str, ...] = DEFAULT_EXCLUDE
    # The configuration file they came from; None for coverage.py's defaults.
    source: Optional[str] = None
    # Why a configuration could not be read, when the defaults stand in for it.
    problem: Optional[str] = None

    @property
    def pattern(self) -> str:
        """The regexes joined as coverage.py matches them; empty when nothing is excluded."""
        return join_regex(self.regexes)


class _Unreadable(Exception):
    """coverage.py could not read this configuration."""


_DOLLAR = re.compile(r"""(?x)   # Use extended regex syntax
    \$                      # A dollar sign,
    (?:                     # then
        (?P<dollar> \$ ) |      # a dollar sign, or
        (?P<word1> \w+ ) |      # a plain word, or
        \{                      # a {-wrapped
            (?P<word2> \w+ )        # word,
            (?:                         # either
                (?P<strict> \? ) |      # with a strict marker
                -(?P<defval> [^}]* )    # or a default value
            )?                      # maybe.
        }
    )
    """)


def substitute_variables(text: str, variables: Mapping[str, str]) -> str:
    """``text`` with ``$VAR``, ``${VAR}``, ``${VAR?}``, ``${VAR-default}`` and ``$$`` replaced.

    coverage.py 7.16.0, coverage/misc.py; an undefined ``${VAR?}`` makes the
    configuration unreadable.
    """

    def replace(match: re.Match[str]) -> str:
        word = next(group for group in match.group("dollar", "word1", "word2") if group)
        if word == "$":
            return "$"
        if word in variables:
            return variables[word]
        if match["strict"]:
            raise _Unreadable(f"Variable {word} is undefined: {text!r}")
        return match["defval"] or ""

    return _DOLLAR.sub(replace, text)


def _regex_list(where: str, values: Sequence[str]) -> List[str]:
    """The non-blank values, stripped, each checked to compile (coverage's ``process_regexlist``)."""
    regexes: List[str] = []
    for value in values:
        value = value.strip()
        try:
            re.compile(value)
        except re.error as error:
            raise _Unreadable(f"Invalid {where} value {value!r}: {error}") from error
        if value:
            regexes.append(value)
    return regexes


@dataclass(frozen=True)
class _Settings:
    """The exclusion settings of a configuration file coverage.py uses."""

    exclude_lines: Optional[List[str]]
    exclude_also: Optional[List[str]]


def _from_ini(path: Path, ours: bool, environ: Mapping[str, str]) -> Optional[_Settings]:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        read = parser.read(path, encoding="utf-8")
    except (configparser.Error, UnicodeDecodeError) as error:
        raise _Unreadable(f"Couldn't read config file {path}: {error}") from error
    if not read:
        return None
    prefixes = ["coverage:"] + ([""] if ours else [])

    def real_section(section: str) -> Optional[str]:
        return next(
            (prefix + section for prefix in prefixes if parser.has_section(prefix + section)),
            None,
        )

    def has_option(section: str, option: str) -> bool:
        real = real_section(section)
        return real is not None and parser.has_option(real, option)

    def regexes(option: str) -> Optional[List[str]]:
        if not has_option("report", option):
            return None
        real = next(
            prefix + "report"
            for prefix in prefixes
            if parser.has_section(prefix + "report")
            and parser.has_option(prefix + "report", option)
        )
        value = substitute_variables(parser.get(real, option), environ)
        return _regex_list(f"[{real}].{option}", value.splitlines())

    used = ours or any(
        has_option(section, option) for section, options in _OPTIONS.items() for option in options
    )
    paths = real_section("paths")
    used = used or (paths is not None and bool(parser.options(paths)))
    if not used:
        return None
    return _Settings(regexes("exclude_lines"), regexes("exclude_also"))


def _from_toml(path: Path, ours: bool, environ: Mapping[str, str]) -> Optional[_Settings]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    except UnicodeDecodeError as error:
        raise _Unreadable(f"Couldn't read config file {path}: {error}") from error
    try:
        data: Dict[str, Any] = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise _Unreadable(f"Couldn't read config file {path}: {error}") from error
    prefixes = ["tool.coverage."] + ([""] if ours else [])

    def section(name: str) -> Tuple[Optional[str], Optional[Mapping[str, Any]]]:
        for prefix in prefixes:
            real = prefix + name
            node: Any = data
            for part in real.split("."):
                if not isinstance(node, Mapping) or part not in node:
                    break
                node = node[part]
            else:
                if isinstance(node, Mapping):
                    return real, node
        return None, None

    def regexes(option: str) -> Optional[List[str]]:
        real, values = section("report")
        if values is None or option not in values:
            return None
        found = values[option]
        if not isinstance(found, list):
            raise _Unreadable(f"Option [{real}]{option} is not a list: {found!r}")
        strings: List[str] = []
        for value in found:
            if not isinstance(value, str):
                raise _Unreadable(f"Option [{real}]{option} is not a list of strings: {found!r}")
            strings.append(substitute_variables(value, environ))
        return _regex_list(f"[{real}].{option}", strings)

    used = ours
    for name, options in _OPTIONS.items():
        _, values = section(name)
        if values is not None and any(option in values for option in options):
            used = True
    _, paths = section("paths")
    used = used or bool(paths)
    if not used:
        return None
    return _Settings(regexes("exclude_lines"), regexes("exclude_also"))


def _files_to_try(root: Path, environ: Mapping[str, str]) -> List[Tuple[Path, bool, bool]]:
    """(path, coverage.py's own file, named by ``COVERAGE_RCFILE``), in coverage.py's order.

    A relative ``COVERAGE_RCFILE`` is taken from the project's root, where
    coverage.py is taken to run.
    """
    named = environ.get("COVERAGE_RCFILE")
    first = Path(named) if named else Path(".coveragerc")
    return [
        (first if first.is_absolute() else root / first, True, bool(named)),
        (root / ".coveragerc.toml", True, False),
        (root / "setup.cfg", False, False),
        (root / "tox.ini", False, False),
        (root / "pyproject.toml", False, False),
    ]


def _read(root: Path, environ: Mapping[str, str]) -> CoverageExclusion:
    for path, ours, named in _files_to_try(root, environ):
        reader = _from_toml if path.suffix == ".toml" else _from_ini
        settings = reader(path, ours, environ)
        if settings is not None:
            regexes = (
                list(DEFAULT_EXCLUDE) if settings.exclude_lines is None else settings.exclude_lines
            )
            regexes += settings.exclude_also or []
            return CoverageExclusion(tuple(regexes), str(path))
        if named:
            raise _Unreadable(f"Couldn't read {str(path)!r} as a config file")
    return CoverageExclusion()


def coverage_exclusion(
    root: Path, environ: Optional[Mapping[str, str]] = None
) -> CoverageExclusion:
    """The exclusion regexes coverage.py would use for the project at ``root``.

    A configuration coverage.py could not read gives its defaults, with the
    reason in ``problem``.
    """
    try:
        return _read(root, os.environ if environ is None else environ)
    except _Unreadable as error:
        return CoverageExclusion(problem=str(error))
