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

Which files it measures and reports comes from the same file
(``CoverageMeasurement``): ``[run]`` ``source``, ``source_pkgs``,
``source_dirs``, ``include`` and ``omit``, and ``[report]`` ``include`` and
``omit``, matched as coverage.py 7.16's ``InOrOut.check_include_omit_etc``
(``coverage/inorout.py``) and ``get_analysis_to_report``
(``coverage/report_core.py``) match them, with its glob syntax
(``coverage/files.py``, ``prep_patterns`` and ``globs_to_regex``).

A configuration coverage.py itself could not read, because it does not
parse, holds an invalid regex or a value of the wrong type, or is named by
``COVERAGE_RCFILE`` and absent, gives the defaults and a ``problem`` saying
why, for the run to report.
"""

from __future__ import annotations

import configparser
from dataclasses import dataclass, field
import functools
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


# The list options that decide which files coverage.py measures and reports,
# by (section, option): ``CoverageConfig.CONFIG_FILE_OPTIONS``.
_MEASUREMENT_OPTIONS: Tuple[Tuple[str, str], ...] = (
    ("run", "source"),
    ("run", "source_pkgs"),
    ("run", "source_dirs"),
    ("run", "include"),
    ("run", "omit"),
    ("report", "include"),
    ("report", "omit"),
)


@dataclass(frozen=True)
class _Settings:
    """The exclusion and measurement settings of a configuration file coverage.py uses."""

    exclude_lines: Optional[List[str]]
    exclude_also: Optional[List[str]]
    # By ``_MEASUREMENT_OPTIONS``: each list the file sets.
    lists: Mapping[Tuple[str, str], Tuple[str, ...]] = field(default_factory=dict)


# coverage.py 7.16.0, coverage/files.py: G2RX_TOKENS, for _glob_to_regex. None
# as a substitution means the pattern is refused.
_GLOB_TOKENS: Tuple[Tuple[re.Pattern[str], Optional[str]], ...] = tuple(
    (re.compile(pattern), substitution)
    for pattern, substitution in (
        (r"\*\*\*+", None),
        (r"[^/]+\*\*+", None),
        (r"\*\*+[^/]+", None),
        (r"\*\*/\*\*", None),
        (r"^\*+/", r"(.*[/\\\\])?"),
        (r"/\*+$", r"[/\\\\].*"),
        (r"\*\*/", r"(.*[/\\\\])?"),
        (r"/", r"[/\\\\]"),
        (r"\*", r"[^/\\\\]*"),
        (r"\?", r"[^/\\\\]"),
        (r"\[.*?\]", r"\g<0>"),
        (r"[a-zA-Z0-9_-]+", r"\g<0>"),
        (r"[\[\]]", None),
        (r".", r"\\\g<0>"),
    )
)


def _glob_to_regex(pattern: str) -> str:
    """One file-path glob as a regex (coverage.py 7.16.0, coverage/files.py, ``_glob_to_regex``)."""
    pattern = pattern.replace("\\", "/")
    if "/" not in pattern:
        pattern = f"**/{pattern}"
    pieces: List[str] = []
    position = 0
    while position < len(pattern):
        for token, substitution in _GLOB_TOKENS:
            match = token.match(pattern, pos=position)
            if match:
                if substitution is None:
                    raise _Unreadable(f"File pattern can't include {match[0]!r}")
                pieces.append(match.expand(substitution))
                position = match.end()
                break
    return "".join(pieces)


@functools.lru_cache(maxsize=64)
def _glob_matcher(patterns: Tuple[str, ...], root: Path) -> Optional[re.Pattern[str]]:
    """coverage.py's ``GlobMatcher(prep_patterns(patterns))`` run from ``root``; None for none.

    A pattern not starting with a wildcard counts both as written and made
    absolute from where coverage.py runs, its links resolved.
    """
    if not patterns:
        return None
    prepared: List[str] = []
    for pattern in patterns:
        prepared.append(pattern)
        if not pattern.startswith(("*", "?")):
            prepared.append(os.path.abspath(os.path.realpath(root / pattern)))
    return re.compile(rf"(?:{join_regex([_glob_to_regex(item) for item in prepared])})\Z")


@dataclass(frozen=True)
class CoverageMeasurement:
    """Which files a project's coverage.py measures and reports, as its configuration sets it.

    A file is measured, as ``InOrOut.check_include_omit_etc`` decides:
    where ``source``, ``source_pkgs`` or ``source_dirs`` is set, only inside
    a source directory or a source package (a ``source`` entry that is no
    directory is a package); else, where ``[run] include`` is set, only if
    it matches; and never if ``[run] omit`` matches. A measured file is
    reported, and counts toward ``fail_under``, unless ``[report] include``
    leaves it out or ``[report] omit`` takes it out.
    """

    source: Tuple[str, ...] = ()
    source_pkgs: Tuple[str, ...] = ()
    source_dirs: Tuple[str, ...] = ()
    run_include: Tuple[str, ...] = ()
    run_omit: Tuple[str, ...] = ()
    report_include: Tuple[str, ...] = ()
    report_omit: Tuple[str, ...] = ()

    def names_packages(self, root: Path) -> bool:
        """Whether a module's name, not only its file, decides whether it is measured."""
        return bool(self.source_pkgs) or any(not (root / entry).is_dir() for entry in self.source)

    def status(
        self, path: Path, root: Path, module_name: Optional[str] = None
    ) -> Optional[Tuple[bool, bool]]:
        """Whether coverage.py, run from ``root``, measures the file at ``path``, and reports it.

        ``path`` is resolved, as coverage.py records files, and
        ``module_name`` the name the program imports it by, when a source
        package may decide; None when it may and the name is not known. A
        configuration coverage.py refuses (a source directory that is none,
        a glob it cannot read) measures nothing, alike for every file.
        """
        if not (
            self.source
            or self.source_pkgs
            or self.source_dirs
            or self.run_include
            or self.run_omit
            or self.report_include
            or self.report_omit
        ):
            return (True, True)
        try:
            return self._status(str(path), root, module_name)
        except _Unreadable:
            return (False, False)

    def _status(
        self, filename: str, root: Path, module_name: Optional[str]
    ) -> Optional[Tuple[bool, bool]]:
        directories = list(self.source_dirs)
        packages = list(self.source_pkgs)
        for entry in self.source:
            (directories if (root / entry).is_dir() else packages).append(entry)
        trees = []
        for directory in directories:
            if not (root / directory).is_dir():
                raise _Unreadable(f"Source dir is not a directory: {directory!r}")
            trees.append(os.path.abspath(os.path.realpath(root / directory)))
        omit, include = _glob_matcher(self.run_omit, root), _glob_matcher(self.run_include, root)
        report_include = _glob_matcher(self.report_include, root)
        report_omit = _glob_matcher(self.report_omit, root)
        if trees or packages:
            inside = any(filename == tree or filename.startswith(tree + os.sep) for tree in trees)
            if not inside and packages:
                if module_name is None:
                    return None
                inside = any(
                    module_name == package or module_name.startswith(package + ".")
                    for package in packages
                )
            if not inside:
                return (False, False)
        elif include is not None and not include.match(filename):
            return (False, False)
        if omit is not None and omit.match(filename):
            return (False, False)
        reported = (report_include is None or report_include.match(filename) is not None) and (
            report_omit is None or report_omit.match(filename) is None
        )
        return (True, reported)


@dataclass(frozen=True)
class CoverageConfiguration:
    """What a project's coverage.py excludes lines by, and which files it measures."""

    exclusion: CoverageExclusion = field(default_factory=lambda: CoverageExclusion())
    measurement: CoverageMeasurement = field(default_factory=CoverageMeasurement)


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

    def strings(section: str, option: str) -> Optional[Tuple[str, ...]]:
        """A list option as ``HandyConfigParser.getlist`` reads it: by lines and commas."""
        real = next(
            (
                prefix + section
                for prefix in prefixes
                if parser.has_section(prefix + section)
                and parser.has_option(prefix + section, option)
            ),
            None,
        )
        if real is None:
            return None
        value = substitute_variables(parser.get(real, option), environ)
        return tuple(
            item.strip() for line in value.split("\n") for item in line.split(",") if item.strip()
        )

    used = ours or any(
        has_option(section, option) for section, options in _OPTIONS.items() for option in options
    )
    paths = real_section("paths")
    used = used or (paths is not None and bool(parser.options(paths)))
    if not used:
        return None
    lists = {key: found for key in _MEASUREMENT_OPTIONS if (found := strings(*key)) is not None}
    return _Settings(regexes("exclude_lines"), regexes("exclude_also"), lists)


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

    def strings(name: str, option: str) -> Optional[Tuple[str, ...]]:
        """A list option as ``TomlConfigParser.getlist`` reads it: a list of strings."""
        real, values = section(name)
        if values is None or option not in values:
            return None
        found = values[option]
        if not isinstance(found, list) or not all(isinstance(item, str) for item in found):
            raise _Unreadable(f"Option [{real}]{option} is not a list of strings: {found!r}")
        return tuple(substitute_variables(item, environ) for item in found)

    used = ours
    for name, options in _OPTIONS.items():
        _, values = section(name)
        if values is not None and any(option in values for option in options):
            used = True
    _, paths = section("paths")
    used = used or bool(paths)
    if not used:
        return None
    lists = {key: found for key in _MEASUREMENT_OPTIONS if (found := strings(*key)) is not None}
    return _Settings(regexes("exclude_lines"), regexes("exclude_also"), lists)


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


def _read(root: Path, environ: Mapping[str, str]) -> CoverageConfiguration:
    for path, ours, named in _files_to_try(root, environ):
        reader = _from_toml if path.suffix == ".toml" else _from_ini
        settings = reader(path, ours, environ)
        if settings is not None:
            regexes = (
                list(DEFAULT_EXCLUDE) if settings.exclude_lines is None else settings.exclude_lines
            )
            regexes += settings.exclude_also or []
            lists = settings.lists
            measurement = CoverageMeasurement(*(lists.get(key, ()) for key in _MEASUREMENT_OPTIONS))
            return CoverageConfiguration(CoverageExclusion(tuple(regexes), str(path)), measurement)
        if named:
            raise _Unreadable(f"Couldn't read {str(path)!r} as a config file")
    return CoverageConfiguration()


def coverage_configuration(
    root: Path, environ: Optional[Mapping[str, str]] = None
) -> CoverageConfiguration:
    """What coverage.py, run at ``root``, would exclude and measure.

    A configuration coverage.py could not read gives its defaults, with the
    reason in the exclusion's ``problem``: coverage.py itself would refuse
    to run, so no file is measured differently from another.
    """
    try:
        return _read(root, os.environ if environ is None else environ)
    except _Unreadable as error:
        return CoverageConfiguration(CoverageExclusion(problem=str(error)))


def coverage_exclusion(
    root: Path, environ: Optional[Mapping[str, str]] = None
) -> CoverageExclusion:
    """The exclusion regexes coverage.py would use for the project at ``root``.

    A configuration coverage.py could not read gives its defaults, with the
    reason in ``problem``.
    """
    return coverage_configuration(root, environ).exclusion
