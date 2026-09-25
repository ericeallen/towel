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

"""The distributions a project says it requires, and where it says so.

Two questions read them, and they need opposite errors:

- What a helper's host may import without making the borrower require
  something new (:func:`installed_with_the_project`). A requirement read where
  the project declares none would accept a host whose import fails where the
  project is installed, so only what every installation installs counts:
  PEP 621's ``[project].dependencies``, Poetry's ``[tool.poetry.dependencies]``
  less its optional entries, and setup.cfg's ``[options] install_requires``
  written inline.
- Which top-level names some other distribution may provide where the program
  runs (:func:`declared_requirements`, which the import model reads). A
  requirement missed there lets a project directory named like a library be
  taken for that library, so every declaration counts: the extras and PEP 735
  dependency groups beside those tables, Poetry's development groups, the
  development dependencies of ``[tool.uv]`` and ``[tool.pdm]``, the
  dependencies of every hatch environment (in pyproject.toml or hatch.toml),
  setup.cfg's ``extras_require``, a Pipfile's packages, the lockfiles uv,
  Poetry, PDM and Pipenv write, and the requirements files at the project
  root: ``requirements*.txt``, ``*-requirements.txt`` and
  ``*_requirements.txt``, the same with pip-tools' ``.in``, every ``.txt``
  and ``.in`` in ``requirements/``, and the files they include.

A distribution's name is compared with an import name after normalizing both
(:func:`normalized_name`), so ``Click`` and ``click``, or ``typing-extensions``
and ``typing_extensions``, are one name. A distribution whose import name
differs, as ``PyYAML`` provides ``yaml``, is not recognized by its name. A
setup.py is not run, and a ``file:`` directive or dynamic metadata names a file
that is not read; nor are tox.ini, a noxfile, CI recipes, or hatch's
environment ``overrides``.
"""

from __future__ import annotations

import configparser
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import FrozenSet, Iterable, Iterator, List, Mapping, Optional, Set, Tuple

from .project_layout import load_pyproject

__all__ = [
    "Requirement",
    "declared_requirements",
    "installed_with_the_project",
    "normalized_name",
    "own_names",
]


@dataclass(frozen=True)
class Requirement:
    """One distribution the project says it requires, and where it says so."""

    name: str
    """The distribution's name, normalized (:func:`normalized_name`)."""
    declared: str
    """The requirement as written: ``click>=8``, or the name a lockfile records."""
    source: str
    """Where it is declared: ``pyproject.toml [project].dependencies``."""
    with_the_project: bool
    """Whether every installation of the project installs it, not only an extra, a group or an environment."""

    def describe(self) -> str:
        return f"{self.declared} in {self.source}"


def normalized_name(name: str) -> str:
    """``name`` as distribution and import names are compared: lower case, ``-_.`` runs as one ``_``."""
    return re.sub(r"[-_.]+", "_", name).lower()


def installed_with_the_project(root: Path) -> FrozenSet[str]:
    """The normalized names of the distributions every installation of the project at ``root`` installs."""
    data, parser = load_pyproject(root), _setup_cfg(root)
    return frozenset(
        requirement.name
        for requirement in _metadata_requirements(data, parser)
        if requirement.with_the_project
    )


def declared_requirements(root: Path) -> Tuple[Requirement, ...]:
    """Every distribution the project at ``root`` declares anywhere Towel reads, in the order read.

    The project's own name is left out: an extra that installs others as
    ``name[a,b]`` requires nothing but the project itself.
    """
    data, parser = load_pyproject(root), _setup_cfg(root)
    own = _own_names(data, parser)
    found = [
        *_metadata_requirements(data, parser),
        *_tool_requirements(root, data),
        *_environment_requirements(root),
    ]
    return tuple(requirement for requirement in found if requirement.name not in own)


def own_names(root: Path) -> FrozenSet[str]:
    """The normalized names the metadata of the project at ``root`` gives the project itself.

    A distribution of such a name is the project, wherever it is installed.
    """
    return _own_names(load_pyproject(root), _setup_cfg(root))


# -- Packaging metadata ---------------------------------------------------------


def _metadata_requirements(
    data: Mapping[str, object], parser: configparser.ConfigParser
) -> List[Requirement]:
    return [
        *_pep621_requirements(data),
        *_dependency_group_requirements(data),
        *_poetry_requirements(data),
        *_setup_cfg_requirements(parser),
    ]


def _own_names(data: Mapping[str, object], parser: configparser.ConfigParser) -> FrozenSet[str]:
    """The normalized names the project's metadata gives the project itself."""
    names = [
        _table(data, "project").get("name"),
        _table(data, "tool", "poetry").get("name"),
        parser.get("metadata", "name", fallback=None),
    ]
    return frozenset(normalized_name(name) for name in names if isinstance(name, str) and name)


def _table(data: Mapping[str, object], *keys: str) -> Mapping[str, object]:
    """The table at ``keys``, or an empty one when any level is missing or not a table."""
    current: object = data
    for key in keys:
        current = current.get(key) if isinstance(current, dict) else None
    return current if isinstance(current, dict) else {}


def _pep621_requirements(data: Mapping[str, object]) -> Iterator[Requirement]:
    project = _table(data, "project")
    dependencies = project.get("dependencies")
    yield from _strings(
        dependencies, "pyproject.toml [project].dependencies", with_the_project=True
    )
    for extra, requirements in _table(data, "project", "optional-dependencies").items():
        where = f"pyproject.toml [project.optional-dependencies].{extra}"
        yield from _strings(requirements, where, with_the_project=False)


def _dependency_group_requirements(data: Mapping[str, object]) -> Iterator[Requirement]:
    """PEP 735's groups; an ``{include-group = ...}`` entry names a group read on its own."""
    for group, requirements in _table(data, "dependency-groups").items():
        yield from _strings(
            requirements, f"pyproject.toml [dependency-groups].{group}", with_the_project=False
        )


def _poetry_requirements(data: Mapping[str, object]) -> Iterator[Requirement]:
    """Poetry's dependencies, its optional ones only with an extra, and its development groups."""
    for name, specification in _table(data, "tool", "poetry", "dependencies").items():
        optional = isinstance(specification, dict) and specification.get("optional") is True
        yield from _poetry_entry(
            name, "pyproject.toml [tool.poetry.dependencies]", with_the_project=not optional
        )
    for name in _table(data, "tool", "poetry", "dev-dependencies"):
        yield from _poetry_entry(
            name, "pyproject.toml [tool.poetry.dev-dependencies]", with_the_project=False
        )
    for group, table in _table(data, "tool", "poetry", "group").items():
        for name in _table(table if isinstance(table, dict) else {}, "dependencies"):
            where = f"pyproject.toml [tool.poetry.group.{group}.dependencies]"
            yield from _poetry_entry(name, where, with_the_project=False)


def _tool_requirements(root: Path, data: Mapping[str, object]) -> Iterator[Requirement]:
    """What the project's tools install into its own environments, never into an installation of it.

    uv's and PDM's development dependencies, and every hatch environment's
    ``dependencies`` and ``extra-dependencies``, from pyproject.toml's
    ``[tool.hatch]`` or from hatch.toml, which hatch reads in its place.
    """
    yield from _strings(
        _table(data, "tool", "uv").get("dev-dependencies"),
        "pyproject.toml [tool.uv].dev-dependencies",
        with_the_project=False,
    )
    for group, requirements in _table(data, "tool", "pdm", "dev-dependencies").items():
        where = f"pyproject.toml [tool.pdm.dev-dependencies].{group}"
        yield from _strings(requirements, where, with_the_project=False)
    hatch = (_table(data, "tool", "hatch"), "pyproject.toml [tool.hatch.envs.{}].{}")
    standalone = (_toml(root / "hatch.toml"), "hatch.toml [envs.{}].{}")
    for tables, where in (hatch, standalone):
        for environment, table in _table(tables, "envs").items():
            for key in ("dependencies", "extra-dependencies"):
                listed = table.get(key) if isinstance(table, dict) else None
                yield from _strings(listed, where.format(environment, key), with_the_project=False)
    pipfile = _toml(root / "Pipfile")
    for section in ("packages", "dev-packages"):
        for name in _table(pipfile, section):
            yield from _poetry_entry(name, f"Pipfile [{section}]", with_the_project=False)


def _toml(path: Path) -> Mapping[str, object]:
    """The TOML document at ``path``, or an empty one when it is missing or does not parse."""
    try:
        with path.open("rb") as stream:
            return tomllib.load(stream)
    except (OSError, ValueError):
        return {}


def _poetry_entry(name: object, where: str, *, with_the_project: bool) -> Iterator[Requirement]:
    if isinstance(name, str) and name.lower() != "python" and _NAME.fullmatch(name):
        yield Requirement(normalized_name(name), name, where, with_the_project)


def _setup_cfg(root: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(root / "setup.cfg", encoding="utf-8")
    except (configparser.Error, OSError, UnicodeError):
        return configparser.ConfigParser(interpolation=None)
    return parser


def _setup_cfg_requirements(parser: configparser.ConfigParser) -> Iterator[Requirement]:
    """``install_requires`` and ``extras_require``, one requirement per line.

    A ``file:`` directive names a file this does not read, and declares
    nothing here.
    """
    yield from _setup_cfg_lines(
        parser.get("options", "install_requires", fallback=""),
        "setup.cfg [options] install_requires",
        with_the_project=True,
    )
    if parser.has_section("options.extras_require"):
        for extra, value in parser.items("options.extras_require"):
            yield from _setup_cfg_lines(
                value, f"setup.cfg [options.extras_require] {extra}", with_the_project=False
            )


def _setup_cfg_lines(value: str, where: str, *, with_the_project: bool) -> Iterator[Requirement]:
    if value.strip().startswith("file:"):
        return
    lines = (line.split("#", 1)[0] for line in value.splitlines())
    yield from _strings(list(lines), where, with_the_project=with_the_project)


def _strings(requirements: object, where: str, *, with_the_project: bool) -> Iterator[Requirement]:
    """The requirements a list of PEP 508 strings names; anything else in it names none."""
    if not isinstance(requirements, list):
        return
    for text in requirements:
        name = _requirement_name(text) if isinstance(text, str) else None
        if name is not None:
            yield Requirement(normalized_name(name), text.strip(), where, with_the_project)


_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")
_REQUIREMENT = re.compile(
    r"\s*(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\s*(?:\[[^\]]*\])?\s*(?:[@<>=!~;(]|$)"
)


def _requirement_name(text: str) -> Optional[str]:
    """The distribution a PEP 508 requirement names, or ``None`` for a URL, a path or anything else."""
    match = _REQUIREMENT.match(text)
    return None if match is None else match.group("name")


# -- What an environment of the project installs --------------------------------


_LOCKFILES = ("uv.lock", "poetry.lock", "pdm.lock")


def _environment_requirements(root: Path) -> List[Requirement]:
    """What the project's lockfiles and requirements files install.

    A lockfile records every distribution resolved for the project, those its
    dependencies require among them; a requirements file names what an
    environment of it installs.
    """
    return [
        *(requirement for name in _LOCKFILES for requirement in _toml_lockfile(root / name)),
        *_pipfile_lock(root / "Pipfile.lock"),
        *_requirements_files(root),
    ]


def _toml_lockfile(path: Path) -> Iterator[Requirement]:
    """The ``[[package]]`` names uv, Poetry and PDM record."""
    packages = _toml(path).get("package")
    for package in packages if isinstance(packages, list) else ():
        name = package.get("name") if isinstance(package, dict) else None
        if isinstance(name, str) and _NAME.fullmatch(name):
            yield Requirement(normalized_name(name), name, path.name, with_the_project=False)


def _pipfile_lock(path: Path) -> Iterator[Requirement]:
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    for section in ("default", "develop"):
        table = data.get(section) if isinstance(data, dict) else None
        for name in table if isinstance(table, dict) else ():
            if isinstance(name, str) and _NAME.fullmatch(name):
                yield Requirement(normalized_name(name), name, path.name, with_the_project=False)


_REQUIREMENTS_FILES = (
    "requirements*.txt",
    "*-requirements.txt",
    "*_requirements.txt",
    "requirements*.in",
    "*-requirements.in",
    "*_requirements.in",
    "requirements/*.txt",
    "requirements/*.in",
)
"""Where requirements files are looked for, below the project root: ``dev-requirements.txt`` too."""


def _requirements_files(root: Path) -> Iterator[Requirement]:
    """The root's requirements files (:data:`_REQUIREMENTS_FILES`), with the files they include."""
    pending = sorted({path for pattern in _REQUIREMENTS_FILES for path in root.glob(pattern)})
    seen: Set[Path] = set()
    while pending:
        path = pending.pop(0)
        try:
            resolved = path.resolve()
        except (OSError, RuntimeError):
            continue
        if resolved in seen or not resolved.is_relative_to(root.resolve()) or not path.is_file():
            continue
        seen.add(resolved)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        where = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
        for line in _logical_lines(text):
            included = _INCLUDE.match(line)
            if included is not None:
                pending.append(path.parent / included.group("file"))
                continue
            name = _pip_requirement_name(line)
            if name is not None:
                yield Requirement(normalized_name(name), line, where, with_the_project=False)


_INCLUDE = re.compile(r"(?:-r|--requirement|-c|--constraint)(?:\s*=\s*|\s+|(?=\S))(?P<file>\S+)")
_EGG = re.compile(r"#egg=(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)")


def _logical_lines(text: str) -> Iterable[str]:
    """pip's lines: continuations joined, comments dropped, blank lines skipped."""
    joined = re.sub(r"\\\r?\n", " ", text)
    for line in joined.splitlines():
        stripped = re.sub(r"(^|\s)#.*$", "", line).strip()
        if stripped:
            yield stripped


def _pip_requirement_name(line: str) -> Optional[str]:
    """The distribution one line of a requirements file names, or ``None``.

    A URL or path names one only through its ``#egg=`` fragment, and an
    option line other than ``-e``/``--editable`` names none.
    """
    if line.startswith("-"):
        editable = re.match(r"(?:-e|--editable)(?:\s*=\s*|\s+)(?P<target>\S+)", line)
        if editable is None:
            return None
        line = editable.group("target")
    egg = _EGG.search(line)
    if egg is not None:
        return egg.group("name")
    if "://" in line.split("@", 1)[0] or line.startswith((".", "/", "~")):
        return None
    return _requirement_name(line)
