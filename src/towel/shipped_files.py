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

Each distribution's own configuration is read for its modules: the nearest
directory above a module holding a ``setup.py``, a ``setup.cfg`` with
``[metadata]`` or ``[options]``, or a ``pyproject.toml`` with a
``[project]``, ``[build-system]`` or ``[tool.poetry]`` table, else the
project root (:func:`distribution_root`). A backend's default selection is
read only where ``build-system.build-backend`` names that backend; a
setting is read wherever its table is. Read, from pyproject.toml unless
said otherwise:

- hatch's ``exclude``, ``include``, ``only-include``, ``packages`` and
  ``only-packages``, for ``[tool.hatch.build]`` and its ``wheel`` and
  ``sdist`` targets, and, with none of the three selections, the wheel's
  default: the package or module named after the project;
- setuptools' ``packages.find`` ``where``, ``include`` and ``exclude``, an
  explicit ``packages`` list, ``package-dir`` and ``py-modules``, here or in
  setup.cfg: once one of them selects, a module below no ``where`` and a
  top-level module ``py-modules`` does not name are left out of the wheel;
- MANIFEST.in's ``exclude``, ``recursive-exclude``, ``global-exclude`` and
  ``prune``;
- Poetry's ``packages`` (``include``, ``from`` and ``format``), ``include``
  (``path`` and ``format``) and ``exclude``, and without ``packages`` the
  package named after the project, globbed as poetry-core globs them;
- PDM's ``includes``, ``excludes``, ``source-includes`` and ``package-dir``,
  and without ``includes`` the packages in ``package-dir``, merged as
  pdm-backend merges them;
- uv's ``module-name``, ``module-root``, ``source-include``,
  ``source-exclude`` and ``wheel-exclude``, the wheel holding only that
  module; flit's module (``[tool.flit.module]`` ``name``, or the project's)
  and its sdist ``exclude``; and scikit-build-core's ``wheel.packages``, by
  default ``src/<name>``, ``python/<name>`` or ``<name>``, and its
  ``sdist.exclude`` and ``wheel.exclude``;
- every ``.gitignore`` in the tree.

A setting of a type the backend would reject selects nothing, which leaves
out everything it would have selected. Not read, and so never known to
leave anything out: a setup.py, a build hook, setuptools' discovery where
nothing selects, and any other backend's configuration (meson-python,
maturin). setuptools' ``exclude-package-data`` leaves no module out, since
it applies to data files only, and what puts a file back (hatch's
``force-include`` and ``artifacts``, MANIFEST.in's ``graft``, flit's sdist
``include``) is not read.
"""

from __future__ import annotations

import configparser
import enum
import fnmatch
import glob
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import (
    Callable,
    Dict,
    FrozenSet,
    Iterable,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
)

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
    by_distribution: Dict[Path, List[Path]] = {}
    for module in inside:
        by_distribution.setdefault(distribution_root(module, project) or project, []).append(module)
    found: Dict[Path, FrozenSet[Artifact]] = {}
    for distribution, members in sorted(by_distribution.items()):
        found.update(_left_out_of(distribution, members, _gitignores(project, members)))
    return found


def _left_out_of(
    root: Path, modules: Sequence[Path], ignored: Iterable[_Exclusion]
) -> Dict[Path, FrozenSet[Artifact]]:
    """``left_out`` for the modules of the one distribution whose configuration is at ``root``."""
    data = load_pyproject(root)
    packages = frozenset(module.parent for module in modules if module.name == "__init__.py")
    exclusions = [
        *_hatch(root, data, packages),
        *_setuptools(root, data, _setup_cfg(root), packages),
        *_manifest(root),
        *_simple_globs(data),
        *_poetry(root, data),
        *_pdm(root, data),
        *_selected_module(root, data),
    ]
    found: Dict[Path, FrozenSet[Artifact]] = {}
    for module in modules:
        relative = module.relative_to(root).as_posix()
        artifacts = frozenset(
            artifact
            for exclusion in exclusions
            if exclusion.covers(relative)
            for artifact in exclusion.artifacts
        )
        artifacts |= frozenset(
            artifact
            for exclusion in ignored
            if exclusion.covers(module.as_posix())
            for artifact in exclusion.artifacts
        )
        if Artifact.SDIST in artifacts:
            artifacts = _BOTH
        if artifacts:
            found[module] = artifacts
    return found


def distribution_root(path: Path, bound: Optional[Path] = None) -> Optional[Path]:
    """The directory of the distribution ``path`` belongs to, or None when it belongs to none.

    That is the nearest directory above it, up to ``bound`` when one is given,
    holding a ``setup.py``, a ``setup.cfg`` with ``[metadata]`` or
    ``[options]``, or a ``pyproject.toml`` with a ``[project]``,
    ``[build-system]`` or ``[tool.poetry]`` table. A ``pyproject.toml`` that
    only configures tools (``[tool.ruff]``) makes no distribution.
    """
    for directory in path.parents:
        if bound is not None and not directory.is_relative_to(bound):
            return None
        if _declares_distribution(directory):
            return directory
    return None


def _declares_distribution(directory: Path) -> bool:
    if (directory / "setup.py").is_file():
        return True
    if (directory / "setup.cfg").is_file():
        parser = _setup_cfg(directory)
        if parser.has_section("metadata") or parser.has_section("options"):
            return True
    if (directory / "pyproject.toml").is_file():
        data = load_pyproject(directory)
        return bool(
            isinstance(data.get("project"), dict)
            or isinstance(data.get("build-system"), dict)
            or _table(data, "tool", "poetry")
        )
    return False


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


def _hatch(
    root: Path, data: Mapping[str, object], packages: FrozenSet[Path]
) -> Iterator[_Exclusion]:
    """``exclude`` leaves out what it matches; ``include``, ``only-include`` or ``packages`` all else.

    ``only-packages`` leaves out of the wheel every module in a directory
    without ``__init__.py``. With none of the three selections, hatchling's
    wheel ships the package or module named after the project, at the root,
    under ``src``, or in one namespace directory, and nothing else.
    """
    build = _table(data, "tool", "hatch", "build")
    hatchling = _backend(data) == "hatchling.build"
    if not build and not hatchling:
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
    wheel = _table(build, "targets", "wheel")
    if wheel.get("only-packages", build.get("only-packages")) is True:
        yield _Exclusion(_WHEEL, lambda path: (root / path).parent not in packages)
    selected = any(
        wheel.get(key) or build.get(key) for key in ("include", "packages", "only-include")
    )
    if hatchling and not selected:
        yield _Exclusion(_WHEEL, _not_included(_under_any(_hatch_default(root, data))))


def _hatch_default(root: Path, data: Mapping[str, object]) -> List[str]:
    """What hatchling's wheel ships when nothing selects: the project's own package or module."""
    raw = _project_name(data)
    names = dict.fromkeys(
        re.sub(r"[^\w\d.]+", "_", name) for name in (raw, re.sub(r"[-_.]+", "-", raw).lower())
    )
    for name in names:
        if not name:
            continue
        if (root / name / "__init__.py").is_file():
            return [name]
        if (root / "src" / name / "__init__.py").is_file():
            return [f"src/{name}"]
        if (root / f"{name}.py").is_file():
            return [f"{name}.py"]
        namespaces = sorted(root.glob(f"*/{glob.escape(name)}/__init__.py"))
        if len(namespaces) == 1:
            return [namespaces[0].relative_to(root).parts[0]]
    return []  # hatchling refuses to build a wheel it can choose nothing for


def _backend(data: Mapping[str, object]) -> str:
    backend = _table(data, "build-system").get("build-backend")
    return backend.strip() if isinstance(backend, str) else ""


def _project_name(data: Mapping[str, object]) -> str:
    for keys in (("project",), ("tool", "poetry")):
        name = _table(data, *keys).get("name")
        if isinstance(name, str):
            return name
    return ""


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
    the wheel. Once ``packages``, ``find`` or ``py-modules`` selects, a
    top-level module ships only when ``py-modules`` names it, and with
    ``py-modules`` alone no package ships.
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
    modules: Optional[List[str]] = (
        _strings(tool.get("py-modules")) if isinstance(tool.get("py-modules"), list) else None
    )
    if parser.has_option("options", "py_modules"):
        modules = _cfg_list(parser.get("options", "py_modules"))
    finds = bool(find) or (
        parser.has_option("options", "packages")
        and parser.get("options", "packages").strip().startswith("find")
    )
    if listed is not None or finds or modules is not None:
        # Explicit selection turns setuptools' discovery off: a top-level module
        # ships only when ``py-modules`` names it, and a package only when a
        # list or a ``find`` selects it.
        home = base or (wheres[0] if len(wheres) == 1 else ".")
        named = frozenset(modules or ())
        yield _Exclusion(_WHEEL, _top_level_module_unless(root, [home, *wheres, "."], home, named))
        roots = wheres if finds else [base or "."] if listed is not None else []
        yield _Exclusion(_WHEEL, _outside(root, roots, home, named))
    if listed is not None:
        known = frozenset(listed)
        yield from _packages_where(root, [base or "."], packages, lambda name: name not in known)
        return
    if includes or excludes:

        def omitted(name: str) -> bool:
            kept = not includes or any(fnmatch.fnmatchcase(name, p) for p in includes)
            return not kept or any(fnmatch.fnmatchcase(name, pattern) for pattern in excludes)

        yield from _packages_where(root, wheres, packages, omitted)


def _outside(
    root: Path, roots: Sequence[str], home: str, named: FrozenSet[str]
) -> Callable[[str], bool]:
    """Covers a module below none of ``roots`` that is not a ``py-modules`` module in ``home``."""
    places = [(root / directory).resolve() for directory in roots]
    home_place = (root / home).resolve()

    def covers(path: str) -> bool:
        module = (root / path).resolve()
        if any(module.is_relative_to(place) for place in places):
            return False
        return not (module.parent == home_place and module.stem in named)

    return covers


def _top_level_module_unless(
    root: Path, directories: Sequence[str], home: str, named: FrozenSet[str]
) -> Callable[[str], bool]:
    """Covers a module directly in one of ``directories`` unless ``py-modules`` names it in ``home``."""
    places = {(root / directory).resolve() for directory in directories}
    home_place = (root / home).resolve()

    def covers(path: str) -> bool:
        module = (root / path).resolve()
        if module.parent not in places or module.name == "__init__.py":
            return False
        return not (module.parent == home_place and module.stem in named)

    return covers


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


# -- Poetry and PDM, which choose the files by globbing the tree ---------------------


def _globbed(root: Path, base: Path, pattern: str) -> FrozenSet[str]:
    """The files ``base.glob(pattern)`` names, a directory's own included, relative to ``root``."""
    if not pattern.strip() or Path(pattern).is_absolute():
        return frozenset()
    try:
        elements = list(base.glob(pattern))
    except (ValueError, NotImplementedError, OSError):
        return frozenset()
    return _files_below(root, elements)


def _files_below(root: Path, elements: Iterable[Path]) -> FrozenSet[str]:
    found: Set[str] = set()
    for element in elements:
        for file in [element] if element.is_file() else element.glob("**/*"):
            if file.is_file():
                try:
                    found.add(file.resolve().relative_to(root).as_posix())
                except ValueError:
                    continue
    return frozenset(found)


def _formats(value: object, default: Sequence[str]) -> FrozenSet[Artifact]:
    names = [value] if isinstance(value, str) else _strings(value) if value is not None else default
    return frozenset(
        {"sdist": Artifact.SDIST, "wheel": Artifact.WHEEL}[name]
        for name in names
        if name in ("sdist", "wheel")
    )


def _poetry(root: Path, data: Mapping[str, object]) -> Iterator[_Exclusion]:
    """What Poetry's ``packages`` and ``include`` select for each artifact; all else is left out.

    Each ``packages`` entry globs ``include`` below its ``from`` directory, a
    directory bringing everything below it, into the artifacts its
    ``format`` names (both by default); each ``include`` entry globs its
    path into its formats (the sdist by default), at its path from the root,
    so a module it names below a ``from`` directory is not where its name
    finds it and counts as left out. Without ``packages``,
    poetry-core ships the package or module named after the project, at the
    root or under ``src``. ``exclude`` is read with the other glob lists.
    """
    poetry = _table(data, "tool", "poetry")
    backend = _backend(data)
    if not poetry and not backend.startswith(("poetry.core.masonry", "poetry.masonry")):
        return
    declared = poetry.get("packages")
    if declared is None and not backend.startswith(("poetry.core.masonry", "poetry.masonry")):
        return
    entries: List[Tuple[Path, str, FrozenSet[Artifact]]] = []
    if declared is None:
        entries = [(base, name, _BOTH) for base, name in _poetry_default(root, data)]
    elif isinstance(declared, list):
        for entry in declared:
            include = entry.get("include") if isinstance(entry, dict) else None
            source = entry.get("from", ".") if isinstance(entry, dict) else None
            if not isinstance(include, str) or not isinstance(source, str):
                continue  # an entry poetry-core rejects selects nothing
            entries.append((root / source, include, _formats(entry.get("format"), _FORMATS)))
    # An ``include`` places a file at its path from the root, so one below a
    # ``from`` directory lands where its module's name does not find it.
    sources = [base.resolve() for base, _, _ in entries if base.resolve() != root]
    extras = poetry.get("include")
    included: List[Tuple[str, FrozenSet[Artifact]]] = []
    for item in extras if isinstance(extras, list) else []:
        path = item.get("path") if isinstance(item, dict) else item
        if isinstance(path, str):
            format_value = item.get("format") if isinstance(item, dict) else None
            included.append((path, _formats(format_value, ("sdist",))))
    for artifact in (Artifact.WHEEL, Artifact.SDIST):
        packaged = frozenset().union(
            *(_globbed(root, base, pattern) for base, pattern, into in entries if artifact in into)
        )
        placed = frozenset(
            path
            for pattern, into in included
            if artifact in into
            for path in _globbed(root, root, pattern)
            if not any((root / path).is_relative_to(source) for source in sources)
        )
        shipped = packaged | placed
        yield _Exclusion(frozenset({artifact}), _not_included(shipped.__contains__))


_FORMATS = ("sdist", "wheel")


def _poetry_default(root: Path, data: Mapping[str, object]) -> List[Tuple[Path, str]]:
    """poetry-core's package when ``packages`` is not given: the project's name as a module."""
    name = re.sub(r"[-_.]+", "-", _project_name(data)).lower().replace("-", "_")
    if not name:
        return []
    for base in (root, root / "src"):
        for candidate in (name, f"{name}.py"):
            if (base / candidate).exists():
                return [(base, candidate)]
    return []


def _pdm(root: Path, data: Mapping[str, object]) -> Iterator[_Exclusion]:
    """What pdm-backend's ``includes``, ``excludes``, ``source-includes`` and ``package-dir`` ship.

    As pdm-backend 2.4 reads them: without ``includes``, the packages
    directly in ``package-dir`` (``src`` when that exists and nothing else
    is said), or its top-level modules when it holds none; ``source-includes``
    (``tests`` by default) go into the sdist and are excluded from the
    wheel; where an include and an exclude name the same path, the one
    with more parts, then the more concrete, wins, and an exclude on a tie.
    A file an include names itself ships whatever the excludes say.
    """
    build = _table(data, "tool", "pdm", "build")
    if not build and _backend(data) != "pdm.backend":
        return
    includes = _strings(build.get("includes"))
    excludes = _strings(build.get("excludes"))
    source_includes = _strings(build.get("source-includes")) or ["tests"]
    package_dir = build.get("package-dir")
    if not isinstance(package_dir, str):
        in_src = any(Path(pattern).parts[:1] == ("src",) for pattern in includes)
        package_dir = (
            "src"
            if (root / "src").is_dir()
            and not includes
            or in_src
            and "src" not in excludes
            and "src/" not in excludes
            else ""
        )
    if not includes:
        base = root / package_dir if package_dir else root
        tops = sorted(
            entry.relative_to(root).as_posix()
            for entry in (base.iterdir() if base.is_dir() else ())
            if entry.is_dir()
            and entry.name not in ("__pycache__", "__pypackages__")
            and (
                (entry / "__init__.py").is_file()
                or entry.name.endswith("-stubs")
                and (entry / "__init__.pyi").is_file()
            )
        )
        includes = tops or [f"{package_dir or '.'}/*.py"]
    for artifact in (Artifact.WHEEL, Artifact.SDIST):
        wanted = [*includes, *source_includes]
        unwanted = [
            *excludes,
            ".pdm-build",
            *(source_includes if artifact is Artifact.WHEEL else []),
        ]
        shipped = _pdm_files(root, wanted, unwanted)
        yield _Exclusion(frozenset({artifact}), _not_included(shipped.__contains__))


def _pdm_files(root: Path, includes: Sequence[str], excludes: Sequence[str]) -> FrozenSet[str]:
    """pdm-backend's ``_collect_files``: included paths, less what the merged excludes cover."""

    def matched(patterns: Sequence[str]) -> Dict[str, str]:
        found: Dict[str, str] = {}
        for pattern in patterns:
            for path in glob.glob(pattern, root_dir=root, recursive=True):
                found[os.path.normpath(path)] = pattern
        return found

    def weight(pattern: str) -> Tuple[int, int]:
        parts = Path(pattern).parts
        wild = sum(2 if part == "**" else 1 for part in parts if glob.has_magic(part))
        return len(parts), -wild

    included, excluded = matched(includes), matched(excludes)
    for path, pattern in list(included.items()):
        if path in excluded:
            if weight(pattern) <= weight(excluded[path]):
                del included[path]
            else:
                del excluded[path]
    shipped: Set[str] = set()
    for path in included:
        if (root / path).is_file():
            shipped.add(Path(path).as_posix())
            continue
        for relative in _files_below(root, [root / path]):
            if not any(
                relative == exclude
                or relative.startswith(f"{Path(exclude).as_posix()}/")
                or fnmatch.fnmatch(relative, exclude)
                for exclude in excluded
            ):
                shipped.add(relative)
    return frozenset(shipped)


# -- uv, flit and scikit-build-core, which ship one named module --------------------


def _selected_module(root: Path, data: Mapping[str, object]) -> Iterator[_Exclusion]:
    """The one module or package uv_build, flit or scikit-build-core ships in the wheel.

    uv's build backend ships ``module-name`` (the project's normalized name
    by default) below ``module-root`` (``src`` by default), and its sdist
    only that and ``source-include``; flit ships ``[tool.flit.module]
    name``, or the project's name with its dashes as underscores, found at
    the root or under ``src``, in both; scikit-build-core copies
    ``wheel.packages`` into the wheel, by default the first of
    ``src/<name>``, ``python/<name>`` and ``<name>`` that exists, as its
    documentation says.
    """
    backend = _backend(data)
    name = _project_name(data)
    canonical = re.sub(r"[-_.]+", "_", name).lower()
    if backend == "uv_build":
        options = _table(data, "tool", "uv", "build-backend")
        value = options.get("module-name", canonical)
        names = [value] if isinstance(value, str) else _strings(value)
        home = options.get("module-root", "src")
        base = root / home if isinstance(home, str) else root
        shipped = _named_modules(root, [base], names)
        sources = _strings(options.get("source-include"))
        yield _Exclusion(_WHEEL, _not_included(shipped.__contains__))
        tests = [_strictly_matches(pattern) for pattern in sources]
        yield _Exclusion(
            frozenset({Artifact.SDIST}),
            _not_included(lambda path: path in shipped or any(t(path) for t in tests)),
        )
    elif backend == "flit_core.buildapi":
        module = _table(data, "tool", "flit", "module").get("name")
        chosen = module if isinstance(module, str) else name.replace("-", "_")
        shipped = _named_modules(root, [root, root / "src"], [chosen])
        yield _Exclusion(_BOTH, _not_included(shipped.__contains__))
    elif backend == "scikit_build_core.build":
        declared = _table(data, "tool", "scikit-build", "wheel").get("packages")
        if isinstance(declared, dict):
            paths = _strings(list(declared.values()))
        elif isinstance(declared, list):
            paths = _strings(declared)
        else:
            paths = [
                next(
                    (
                        candidate
                        for candidate in (f"src/{canonical}", f"python/{canonical}", canonical)
                        if (root / candidate).is_dir()
                    ),
                    "",
                )
            ]
        yield _Exclusion(_WHEEL, _not_included(_under_any([path for path in paths if path])))


def _named_modules(root: Path, bases: Sequence[Path], names: Sequence[str]) -> FrozenSet[str]:
    """The files of each dotted module ``names`` holds, as a package or a ``.py`` below ``bases``."""
    found: List[Path] = []
    for name in names:
        parts = name.split(".")
        for base in bases:
            package = base.joinpath(*parts)
            if package.is_dir():
                found.append(package)
            elif package.with_name(f"{parts[-1]}.py").is_file():
                found.append(package.with_name(f"{parts[-1]}.py"))
    return _files_below(root, found)


def _simple_globs(data: Mapping[str, object]) -> Iterator[_Exclusion]:
    for keys, option, artifacts in _GLOB_LISTS:
        patterns = _strings(_table(data, *keys).get(option))
        if patterns:
            yield _Exclusion(artifacts, _any_of(patterns))


def _gitignores(root: Path, modules: Sequence[Path]) -> List[_Exclusion]:
    """Each ``.gitignore`` above a module, up to the root, covering what lies below its directory.

    Its tests take a module's absolute POSIX path, since a ``.gitignore``
    above a distribution's own directory still covers it.
    """
    directories = sorted(
        {
            directory
            for module in modules
            for directory in (module.parent, *module.parent.parents)
            if directory == root or directory.is_relative_to(root)
        }
    )
    found: List[_Exclusion] = []
    for directory in directories:
        ignore = directory / ".gitignore"
        try:
            patterns = ignore.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        if patterns:
            found.append(_Exclusion(_BOTH, _any_of(patterns, directory.as_posix().lstrip("/"))))
    return [_Exclusion(e.artifacts, _absolute(e.covers)) for e in found]


def _absolute(covers: Callable[[str], bool]) -> Callable[[str], bool]:
    """A test of paths relative to ``/`` taking absolute POSIX paths."""
    return lambda path: covers(path.lstrip("/"))
