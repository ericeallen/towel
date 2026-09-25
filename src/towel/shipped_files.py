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

"""Which of a project's modules its declared build configuration leaves out of what it ships.

A build ships what its configuration selects, not the tree. hatch's
``exclude = ["src/shop/_devtools.py"]`` keeps one module out of a wheel whose
package ships, and a MANIFEST.in ``prune`` keeps a directory out of the sdist
that ``uv build`` and ``python -m build`` then build the wheel from. A helper
hosted in such a module and imported from one that ships breaks the installed
program. Towel takes no names from this configuration (docs/DECISIONS.md,
"Import names come from the program"): a module it leaves out is only a host in
doubt. So every reading errs toward leaving out. A pattern covers at least what
the backend's own matcher would, an include that could put a file back is not
read, and a module a ``.gitignore`` covers is left out, as hatch and Poetry
leave it out by default.

Read, from pyproject.toml unless said otherwise:

- hatch's ``exclude``, ``include``, ``only-include`` and ``packages``, for
  ``[tool.hatch.build]`` and its ``wheel`` and ``sdist`` targets;
- setuptools' ``packages.find`` ``include`` and ``exclude``, and an explicit
  ``packages`` list, here or in setup.cfg;
- MANIFEST.in's ``exclude``, ``recursive-exclude``, ``global-exclude`` and
  ``prune``;
- Poetry's ``exclude``, PDM's ``excludes``, uv's ``source-exclude`` and
  ``wheel-exclude``, flit's sdist ``exclude``, and scikit-build-core's
  ``sdist.exclude`` and ``wheel.exclude``;
- every ``.gitignore`` in the tree.

Not read, and so never known to leave anything out: a setup.py, a build hook,
and any other backend's configuration. setuptools' ``exclude-package-data``
leaves no module out, since it applies to data files only.
"""

from __future__ import annotations

import configparser
import enum
import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, FrozenSet, Iterable, Iterator, List, Mapping, Optional, Sequence

from .project_layout import load_pyproject

__all__ = ["Artifact", "left_out"]


class Artifact(enum.Enum):
    """What a build makes of the project."""

    SDIST = "the sdist"
    WHEEL = "the wheel"


_BOTH = frozenset({Artifact.SDIST, Artifact.WHEEL})
_WHEEL = frozenset({Artifact.WHEEL})


@dataclass(frozen=True)
class _Exclusion:
    """A declaration that leaves the files it covers out of ``artifacts``."""

    artifacts: FrozenSet[Artifact]
    covers: Callable[[str], bool]
    """Whether it covers a file, given as a POSIX path relative to the project root."""


def left_out(root: Path, modules: Iterable[Path]) -> Dict[Path, FrozenSet[Artifact]]:
    """For each of ``modules`` a declared exclusion covers, what it is left out of.

    A module left out of the sdist is left out of the wheel too, since the
    usual front ends build the wheel from the sdist. ``modules`` are paths
    under ``root``; one outside it is never left out.
    """
    project = root.resolve()
    inside = sorted(module for module in modules if module.is_relative_to(project))
    data = load_pyproject(project)
    packages = frozenset(module.parent for module in inside if module.name == "__init__.py")
    exclusions = [
        *_hatch(data),
        *_setuptools(project, data, _setup_cfg(project), packages),
        *_manifest(project),
        *_simple_globs(data),
        *_gitignores(project, inside),
    ]
    found: Dict[Path, FrozenSet[Artifact]] = {}
    for module in inside:
        relative = module.relative_to(project).as_posix()
        artifacts = frozenset(
            artifact
            for exclusion in exclusions
            if exclusion.covers(relative)
            for artifact in exclusion.artifacts
        )
        if Artifact.SDIST in artifacts:
            artifacts = _BOTH
        if artifacts:
            found[module] = artifacts
    return found


# -- Patterns ---------------------------------------------------------------------


def _glob_regex(pattern: str) -> "re.Pattern[str]":
    """``pattern`` as a regular expression over POSIX paths: ``**`` any depth, ``*`` and ``?`` within one part."""
    pieces: List[str] = []
    index = 0
    while index < len(pattern):
        character = pattern[index]
        if pattern.startswith("**", index):
            pieces.append(".*")
            index += 2
            if pattern.startswith("/", index):
                pieces[-1] = "(?:.*/)?"
                index += 1
            continue
        if character == "*":
            pieces.append("[^/]*")
        elif character == "?":
            pieces.append("[^/]")
        elif character == "[":
            closing = pattern.find("]", index + 1)
            if closing == -1:
                pieces.append(re.escape(character))
            else:
                body = pattern[index + 1 : closing].replace("\\", "\\\\")
                pieces.append(f"[{'^' + body[1:] if body.startswith('!') else body}]")
                index = closing
        else:
            pieces.append(re.escape(character))
        index += 1
    return re.compile("".join(pieces))


def _pattern_covers(pattern: str, base: str = "") -> Callable[[str], bool]:
    """Whether a gitignore- or glob-style ``pattern``, relative to ``base``, covers a file.

    Erring toward yes: a pattern covers a file when it matches the file or any
    directory above it, and one not anchored with a leading ``/`` matches at
    any depth below ``base``. A negation (``!``) puts nothing back.
    """
    text = pattern.strip()
    if not text or text.startswith(("#", "!")):
        return lambda path: False
    anchored = text.startswith("/")
    body = text.strip("/")
    regex = _glob_regex(body)
    one_part = "/" not in body and "**" not in body
    prefix = f"{base}/" if base else ""

    def covers(path: str) -> bool:
        if prefix and not path.startswith(prefix):
            return False
        parts = path[len(prefix) :].split("/")
        if one_part:  # It can match only a single name: the file's or a directory's above it.
            return any(regex.fullmatch(part) for part in (parts[:1] if anchored else parts))
        starts = range(1) if anchored else range(len(parts))
        return any(
            regex.fullmatch("/".join(parts[start:end]))
            for start in starts
            for end in range(start + 1, len(parts) + 1)
        )

    return covers


def _any_of(patterns: Sequence[str], base: str = "") -> Callable[[str], bool]:
    tests = [_pattern_covers(pattern, base) for pattern in patterns]
    return lambda path: any(test(path) for test in tests)


def _strictly_matches(pattern: str) -> Callable[[str], bool]:
    """Whether a gitignore-style ``pattern`` of an include-list matches a file, erring toward no.

    An include puts a file in, so here doubt runs the other way: a pattern
    with a ``/`` before its end is anchored at the root, one without matches a
    name at any depth, and either matches a file or a directory above it.
    """
    text = pattern.strip()
    if not text or text.startswith(("#", "!")):
        return lambda path: False
    body = text.lstrip("/").rstrip("/")
    anchored = text.startswith("/") or "/" in body
    regex = _glob_regex(body)

    def matches(path: str) -> bool:
        parts = path.split("/")
        if anchored:
            return any(regex.fullmatch("/".join(parts[:end])) for end in range(1, len(parts) + 1))
        return any(regex.fullmatch(part) for part in parts)

    return matches


def _under_any(paths: Sequence[str]) -> Callable[[str], bool]:
    """Whether a file is one of ``paths``, relative to the root, or lies below one."""
    prefixes = [
        path.strip().lstrip("./").strip("/") if path.strip() not in (".", "./") else ""
        for path in paths
    ]
    return lambda path: any(
        not prefix or path == prefix or path.startswith(f"{prefix}/") for prefix in prefixes
    )


def _not_included(included: Callable[[str], bool]) -> Callable[[str], bool]:
    """Covers a file an include-list does not name: what the list leaves out."""
    return lambda path: not included(path)


def _strings(value: object) -> List[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _table(data: Mapping[str, object], *keys: str) -> Mapping[str, object]:
    current: object = data
    for key in keys:
        current = current.get(key) if isinstance(current, dict) else None
    return current if isinstance(current, dict) else {}


# -- hatch ------------------------------------------------------------------------


def _hatch(data: Mapping[str, object]) -> Iterator[_Exclusion]:
    """``exclude`` leaves out what it matches; ``include``, ``only-include`` or ``packages`` all else."""
    build = _table(data, "tool", "hatch", "build")
    if not build:
        return
    for artifact, target in ((Artifact.WHEEL, "wheel"), (Artifact.SDIST, "sdist")):
        options = _table(build, "targets", target)
        artifacts = frozenset({artifact})
        excluded = [*_strings(build.get("exclude")), *_strings(options.get("exclude"))]
        if excluded:
            yield _Exclusion(artifacts, _any_of(excluded))
        patterns = _strings(options.get("include")) or _strings(build.get("include"))
        if patterns:
            tests = [_strictly_matches(pattern) for pattern in patterns]
            yield _Exclusion(artifacts, _not_included(lambda path: any(t(path) for t in tests)))
        for key in ("only-include", "packages"):
            paths = _strings(options.get(key)) or _strings(build.get(key))
            if paths:
                yield _Exclusion(artifacts, _not_included(_under_any(paths)))


# -- setuptools -------------------------------------------------------------------


def _setup_cfg(root: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(root / "setup.cfg", encoding="utf-8")
    except (configparser.Error, OSError, UnicodeError):
        return configparser.ConfigParser(interpolation=None)
    return parser


def _cfg_list(value: str) -> List[str]:
    """A setup.cfg list: one item per line, or separated by commas."""
    return [item.strip() for line in value.splitlines() for item in line.split(",") if item.strip()]


def _setuptools(
    root: Path,
    data: Mapping[str, object],
    parser: configparser.ConfigParser,
    packages: FrozenSet[Path],
) -> Iterator[_Exclusion]:
    """The packages setuptools' ``find`` excludes or does not include, or an explicit list omits.

    A package is a directory holding ``__init__.py`` below a ``where``
    directory (or the ``package-dir`` root), named by its dotted path there;
    what it leaves out is that directory's own files, which do not ship in
    the wheel.
    """
    tool = _table(data, "tool", "setuptools")
    mapped = _table(tool, "package-dir").get("")
    base: Optional[str] = mapped if isinstance(mapped, str) else None
    if parser.has_option("options", "package_dir"):
        for entry in _cfg_list(parser.get("options", "package_dir")):
            key, _, value = entry.partition("=")
            if not key.strip() and value.strip():
                base = value.strip()
    declared = tool.get("packages")
    find = _table(tool, "packages", "find")
    listed: Optional[List[str]] = _strings(declared) if isinstance(declared, list) else None
    wheres = _strings(find.get("where")) or ([base] if base else ["."])
    includes, excludes = _strings(find.get("include")), _strings(find.get("exclude"))
    if parser.has_option("options", "packages"):
        value = parser.get("options", "packages").strip()
        if value.startswith("find"):
            section = "options.packages.find"
            if parser.has_option(section, "where"):
                wheres = _cfg_list(parser.get(section, "where"))
            if parser.has_option(section, "include"):
                includes = _cfg_list(parser.get(section, "include"))
            if parser.has_option(section, "exclude"):
                excludes = _cfg_list(parser.get(section, "exclude"))
        else:
            listed = _cfg_list(value)
    if listed is not None:
        known = frozenset(listed)
        yield from _packages_where(root, [base or "."], packages, lambda name: name not in known)
        return
    if includes or excludes:

        def omitted(name: str) -> bool:
            kept = not includes or any(fnmatch.fnmatchcase(name, p) for p in includes)
            return not kept or any(fnmatch.fnmatchcase(name, pattern) for pattern in excludes)

        yield from _packages_where(root, wheres, packages, omitted)


def _packages_where(
    root: Path,
    wheres: Sequence[str],
    packages: FrozenSet[Path],
    omitted: Callable[[str], bool],
) -> Iterator[_Exclusion]:
    """An exclusion of each package below ``wheres`` whose dotted name ``omitted`` says is left out."""
    for where in wheres:
        top = (root / where).resolve()
        if not top.is_relative_to(root):
            continue
        for package in sorted(package for package in packages if package.is_relative_to(top)):
            parts = package.relative_to(top).parts
            if not parts or not all(part.isidentifier() for part in parts):
                continue
            if omitted(".".join(parts)):
                yield _Exclusion(_WHEEL, _directly_in(package.relative_to(root).as_posix()))


def _directly_in(directory: str) -> Callable[[str], bool]:
    """Covers the files directly in ``directory``, a POSIX path relative to the root."""
    return lambda path: path.rpartition("/")[0] == directory


# -- MANIFEST.in ------------------------------------------------------------------


def _manifest(root: Path) -> Iterator[_Exclusion]:
    """What MANIFEST.in takes out of the sdist; what it adds is not read."""
    try:
        text = (root / "MANIFEST.in").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for line in text.splitlines():
        words = line.split("#", 1)[0].split()
        if len(words) < 2:
            continue
        command, arguments = words[0], words[1:]
        if command == "exclude":
            yield _Exclusion(_BOTH, _any_of(arguments))
        elif command == "global-exclude":
            yield _Exclusion(_BOTH, _any_of(arguments))
        elif command == "recursive-exclude" and len(arguments) > 1:
            directory = arguments[0].strip("/")
            yield _Exclusion(_BOTH, _any_of(arguments[1:], directory))
        elif command == "prune":
            yield _Exclusion(_BOTH, _any_of([f"/{argument.strip('/')}" for argument in arguments]))


# -- Other backends, and git ------------------------------------------------------

_GLOB_LISTS = (
    (("tool", "poetry"), "exclude", _BOTH),
    (("tool", "pdm", "build"), "excludes", _BOTH),
    (("tool", "uv", "build-backend"), "source-exclude", _BOTH),
    (("tool", "uv", "build-backend"), "wheel-exclude", _WHEEL),
    (("tool", "flit", "sdist"), "exclude", _BOTH),
    (("tool", "scikit-build", "sdist"), "exclude", _BOTH),
    (("tool", "scikit-build", "wheel"), "exclude", _WHEEL),
)


def _simple_globs(data: Mapping[str, object]) -> Iterator[_Exclusion]:
    for keys, option, artifacts in _GLOB_LISTS:
        patterns = _strings(_table(data, *keys).get(option))
        if patterns:
            yield _Exclusion(artifacts, _any_of(patterns))


def _gitignores(root: Path, modules: Sequence[Path]) -> Iterator[_Exclusion]:
    """Each ``.gitignore`` above a module, up to the root, covering what lies below its directory."""
    directories = sorted(
        {
            directory
            for module in modules
            for directory in (module.parent, *module.parent.parents)
            if directory == root or directory.is_relative_to(root)
        }
    )
    for directory in directories:
        ignore = directory / ".gitignore"
        try:
            patterns = ignore.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        base = "" if directory == root else directory.relative_to(root).as_posix()
        if patterns:
            yield _Exclusion(_BOTH, _any_of(patterns, base))
