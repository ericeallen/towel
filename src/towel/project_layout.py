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

"""Packaging discovery: the project root, its source roots, and each file's importable name.

Reads pyproject.toml (setuptools, flit, poetry, pdm and hatch tables) so a
cross-file helper can be imported by a name that stays valid where the
project is installed.
"""

from __future__ import annotations

from keyword import iskeyword
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .diagnostics import LOG
from .unification.exceptions import UnsupportedLayoutError
import re

import tomllib


def _table(mapping: object, key: str) -> Mapping[str, object]:
    """``mapping[key]`` when both are tables, else an empty table; TOML is checked, never trusted."""
    value = mapping.get(key, {}) if isinstance(mapping, dict) else {}
    return value if isinstance(value, dict) else {}


def load_pyproject(project_root: Path) -> Dict[str, Any]:
    """Best-effort load of pyproject.toml using the available TOML parser.

    Returns an empty dict if parsing fails or file does not exist.
    """
    pyproject_path = project_root / "pyproject.toml"
    if not pyproject_path.exists():
        return {}

    try:
        with pyproject_path.open("rb") as f:
            return tomllib.load(f)
    except OSError:
        return {}
    except ValueError as error:
        LOG.warning(
            "%s could not be parsed; no layout information taken from it: %s",
            pyproject_path,
            error,
        )
        return {}


def is_package_dir(path: Path) -> bool:
    """Whether ``path`` is a regular package: a directory holding ``__init__.py``.

    Namespace packages (PEP 420) have no marker file; whether a bare
    directory counts is a property of the project layout
    (``ProjectLayout.pep420_namespace_packages``), not of the directory.
    """
    return path.is_dir() and (path / "__init__.py").exists()


def package_chain(path: Path) -> List[Path]:
    """The regular packages enclosing ``path``, innermost first.

    Ascends from the containing directory while each directory holds an
    ``__init__.py``; the chain ends at the first directory that does not,
    so every entry is importable through the ones after it.
    """
    chain: List[Path] = []
    directory = path.parent
    while is_package_dir(directory):
        chain.append(directory)
        if directory.parent == directory:
            break
        directory = directory.parent
    return chain


def package_chain_name(path: Path) -> Optional[str]:
    """The module name ``path`` has when its top regular package's parent is on ``sys.path``.

    Read from ``__init__.py`` markers alone, never from packaging metadata, so
    it is a derivation independent of the layout readers: ``src/pkg/m.py``
    under ``src/pkg/__init__.py`` is ``pkg.m`` whatever a ``setup.cfg`` or a
    Hatch table says. A module in no package is named by its stem.
    """
    resolved = path.resolve()
    parts = [package.name for package in reversed(package_chain(resolved))]
    if resolved.stem != "__init__":
        parts.append(resolved.stem)
    return _valid_module_path(".".join(parts))


def _setuptools_default_src_root(project_root: Path, data: Mapping[str, object]) -> Optional[Path]:
    """Recognize conventional setuptools src discovery without overriding configuration.

    A directory named src alone is insufficient evidence: it may itself be an
    importable package, or belong to an unconfigured source tree. Require valid
    project metadata and a classic package under src. Explicit setuptools or
    legacy configuration, and other build backends, retain their existing rules.
    """
    project = data.get("project")
    if not isinstance(project, dict):
        return None
    name = project.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    build = data.get("build-system", {})
    if not isinstance(build, dict) or build.get("build-backend") not in (
        None,
        "setuptools.build_meta",
        "setuptools.build_meta:__legacy__",
    ):
        return None
    tool = data.get("tool", {})
    if not isinstance(tool, dict):
        return None
    setuptools = tool.get("setuptools", {})
    if not isinstance(setuptools, dict) or any(
        key in setuptools for key in ("package-dir", "packages", "py-modules")
    ):
        return None
    if (project_root / "setup.py").exists() or (project_root / "setup.cfg").exists():
        return None
    source_root = project_root / "src"
    if not source_root.is_dir() or (source_root / "__init__.py").exists():
        return None
    if any(child.is_dir() and (child / "__init__.py").is_file() for child in source_root.iterdir()):
        return source_root.resolve()
    return None


def _setuptools_find_source_roots(project_root: Path, data: Mapping[str, object]) -> List[Path]:
    """The directories ``[tool.setuptools.packages.find]`` searches.

    This is how a setuptools project most often declares a src layout, and it
    was the one setuptools spelling nothing read. Its presence also stopped the
    conventional ``src`` inference, which steps aside for explicit
    configuration, so a project saying ``where = ["src"]`` was left with the
    project root as its source root and every module named an extra component
    deep: waitress' ``src/waitress/task.py`` became ``src.waitress.task``. A
    cross-module helper imported under that name is a module that does not
    exist, and adopting the output made the package unimportable.

    ``where`` defaults to the project root, which is what a flat layout wants
    and what was already being used for it.
    """
    packages = _table(data.get("tool", {}), "setuptools").get("packages")
    if not isinstance(packages, dict):
        return []
    find = packages.get("find")
    if not isinstance(find, dict):
        return []
    where = find.get("where", ["."])
    if isinstance(where, str):
        where = [where]
    if not isinstance(where, list):
        return []
    roots: List[Path] = []
    for entry in where:
        # setuptools does not glob ``where``; anything that looks like a
        # pattern is not something to guess at.
        if not isinstance(entry, str) or any(char in entry for char in "*?[]"):
            continue
        candidate = (project_root / entry).resolve()
        if candidate.is_dir() and candidate not in roots:
            roots.append(candidate)
    return roots


def _hatch_source_roots(project_root: Path, data: Mapping[str, object]) -> List[Path]:
    """Recognize Hatch wheel package selection without guessing custom rewrites."""
    build_system = data.get("build-system", {})
    if not isinstance(build_system, dict) or build_system.get("build-backend") != "hatchling.build":
        return []
    if (project_root / "hatch.toml").exists():
        raise UnsupportedLayoutError(
            "Hatch layout in hatch.toml is unsupported; cannot infer safe imports"
        )
    tool = data.get("tool", {})
    hatch = _table(tool, "hatch")
    build = _table(hatch, "build")
    targets = _table(build, "targets")
    wheel = _table(targets, "wheel")
    if not isinstance(build, dict) or not isinstance(wheel, dict):
        raise UnsupportedLayoutError("Invalid Hatch wheel configuration")
    # ``force-include`` maps a source path to a different path in the wheel,
    # which can rename a module, so there is nothing safe to infer from it.
    # ``include`` only selects which files are shipped and leaves their paths
    # alone: soupsieve's ``include = ["/soupsieve"]`` builds a wheel holding
    # ``soupsieve/css_parser.py``, the same name the project root gives it.
    # Refusing it declined two of the corpus's projects outright.
    if "force-include" in wheel or "force-include" in build:
        raise UnsupportedLayoutError(
            "Unsupported Hatch force-include layout; cannot infer safe imports"
        )
    sources = wheel.get("sources", build.get("sources"))
    if sources is not None:
        # ``sources = ["src"]`` strips the prefix from every file under it, so
        # each listed directory is an import root. The rewrite-map form and
        # glob patterns are refused; ``only-include`` must stay within the
        # listed sources for the roots to be complete.
        if not isinstance(sources, list) or not sources:
            raise UnsupportedLayoutError(
                "Unsupported Hatch sources layout; cannot infer safe imports"
            )
        roots = []
        for value in sources:
            if not isinstance(value, str) or any(char in value for char in "*?[]"):
                raise UnsupportedLayoutError(
                    "Unsupported Hatch sources layout; cannot infer safe imports"
                )
            root = (project_root / value).resolve()
            if not root.is_relative_to(project_root) or not root.is_dir():
                raise UnsupportedLayoutError("Hatch source must be a directory within the project")
            roots.append(root)
        only_include = wheel.get("only-include", build.get("only-include"))
        if only_include is not None:
            if not isinstance(only_include, list):
                raise UnsupportedLayoutError(
                    "Unsupported Hatch only-include layout; cannot infer safe imports"
                )
            for value in only_include:
                if not isinstance(value, str) or any(char in value for char in "*?[]"):
                    raise UnsupportedLayoutError("Unsupported Hatch only-include layout")
                included = (project_root / value).resolve()
                if not any(included == root or included.is_relative_to(root) for root in roots):
                    raise UnsupportedLayoutError(
                        "Hatch only-include outside sources; cannot infer safe imports"
                    )
        return roots
    if "only-include" in wheel or "only-include" in build:
        raise UnsupportedLayoutError(
            "Unsupported Hatch only-include layout; cannot infer safe imports"
        )
    packages = wheel.get("packages", build.get("packages"))
    if packages is not None:
        if not isinstance(packages, list) or not packages:
            raise UnsupportedLayoutError(
                "Hatch packages must be a nonempty list of classic package paths"
            )
        roots = []
        for value in packages:
            if not isinstance(value, str) or any(char in value for char in "*?[]"):
                raise UnsupportedLayoutError("Unsupported Hatch package path")
            package = (project_root / value).resolve()
            if not package.is_relative_to(project_root) or not (package / "__init__.py").is_file():
                raise UnsupportedLayoutError(
                    "Hatch package must be a classic package within the project"
                )
            if package.parent not in roots:
                roots.append(package.parent)
        return roots
    # An ``include`` naming a package selects files and turns Hatch's
    # name-based default off;
    # with no ``sources`` nothing is relocated, so every file ships at its
    # path from the project root. beautifulsoup4's ``"/bs4/**/*.py"`` ships
    # ``bs4/__init__.py``, and ``"/src/foo"`` ships ``src/foo/a.py`` as
    # ``src.foo.a`` -- which the default, had it been applied, would have
    # named ``foo.a``. Both built wheels agree.
    if _hatch_includes_a_package(project_root, wheel, build):
        return [project_root.resolve()]
    project = data.get("project", {})
    name = project.get("name") if isinstance(project, dict) else None
    if not isinstance(name, str):
        raise UnsupportedLayoutError("Hatch project name is required to infer safe imports")
    normalized = re.sub(r"[-_.]+", "_", name).lower()
    for root in (project_root, project_root / "src"):
        if (root / normalized / "__init__.py").is_file():
            return [root.resolve()]
    raise UnsupportedLayoutError(
        "Unsupported Hatch default package layout; cannot infer safe imports"
    )


def _literal_prefix(pattern: str) -> Optional[Path]:
    """The directory a wheel include pattern names before its first wildcard."""
    parts: List[str] = []
    for part in PurePosixPath(pattern.lstrip("/")).parts:
        if any(character in part for character in "*?["):
            break
        parts.append(part)
    return Path(*parts) if parts else None


def _hatch_includes_a_package(
    project_root: Path, wheel: Mapping[str, object], build: Mapping[str, object]
) -> bool:
    """Whether a wheel include names a classic package, evidence of where the code ships.

    A project whose distribution name is not its package name defeats Hatch's
    own default, which looks for a directory named after the project:
    beautifulsoup4 ships ``bs4``. Its ``include`` says so --
    ``"/bs4/**/*.py"`` -- and with no ``sources`` to relocate anything, the
    wheel holds ``bs4/__init__.py`` at its root, which its built wheel
    confirms. The directory named before the first wildcard is a classic
    package. Nothing is relocated, so the import root is the project root:
    ``"/src/foo"`` ships ``src/foo/a.py`` as ``src.foo.a``.

    An include naming no package is no evidence and contributes nothing; where
    none of them does, the layout is still refused rather than guessed at.
    """
    for source in (wheel, build):
        for key in ("include", "only-include"):
            patterns = source.get(key)
            if not isinstance(patterns, list):
                continue
            for pattern in patterns:
                if not isinstance(pattern, str):
                    continue
                prefix = _literal_prefix(pattern)
                if prefix is None:
                    continue
                package = (project_root / prefix).resolve()
                if not package.is_relative_to(project_root):
                    continue
                if (package / "__init__.py").is_file():
                    return True
    return False


def _flit_source_roots(project_root: Path, data: Dict[str, Any]) -> List[Path]:
    """Locate the single module Flit builds, in the project root or under ``src``.

    Flit packages exactly one importable module named by ``[tool.flit.module]``
    or, failing that, by the project name. It looks for that module as a
    package directory or a single file, first beside ``pyproject.toml`` and
    then under ``src``. The module's parent is therefore the import root.
    """
    tool = data.get("tool", {})
    flit = _table(tool, "flit")
    module = _table(flit, "module")
    name = module.get("name") if isinstance(module, dict) else None
    if name is None:
        project = data.get("project", {})
        name = project.get("name") if isinstance(project, dict) else None
    if not isinstance(name, str) or not name:
        raise UnsupportedLayoutError("Flit module name is required to infer safe imports")
    normalized = name.replace("-", "_")
    for root in (project_root, project_root / "src"):
        if (root / normalized / "__init__.py").is_file() or (root / f"{normalized}.py").is_file():
            return [root.resolve()]
    raise UnsupportedLayoutError("Flit module was not found beside pyproject.toml or under src")


def _poetry_source_roots(project_root: Path, data: Mapping[str, object]) -> List[Path]:
    """Locate the packages poetry-core builds, from ``[tool.poetry].packages`` or by name.

    Each ``packages`` entry names a package to ``include`` relative to an
    optional ``from`` directory, and that directory is the import root
    (``from`` is stripped from the installed path). Entries with ``to``
    rewrite the installed path and glob patterns select unknown files, so
    both are refused. Without ``packages`` poetry-core looks for a module or
    package named after the project, beside ``pyproject.toml`` and then under
    ``src``.
    """
    tool = data.get("tool", {})
    poetry = _table(tool, "poetry")
    if not isinstance(poetry, dict):
        raise UnsupportedLayoutError("Invalid Poetry configuration")
    packages = poetry.get("packages")
    if packages is not None:
        if not isinstance(packages, list) or not packages:
            raise UnsupportedLayoutError(
                "Poetry packages must be a nonempty list; cannot infer safe imports"
            )
        roots: List[Path] = []
        for entry in packages:
            if not isinstance(entry, dict) or "to" in entry:
                raise UnsupportedLayoutError(
                    "Unsupported Poetry package entry; cannot infer safe imports"
                )
            include = entry.get("include")
            origin = entry.get("from", "")
            if not isinstance(include, str) or not isinstance(origin, str):
                raise UnsupportedLayoutError(
                    "Unsupported Poetry package entry; cannot infer safe imports"
                )
            if any(char in include + origin for char in "*?[]"):
                raise UnsupportedLayoutError(
                    "Unsupported Poetry package pattern; cannot infer safe imports"
                )
            root = (project_root / origin).resolve()
            included = (root / include).resolve()
            if not root.is_relative_to(project_root) or not included.is_relative_to(root):
                raise UnsupportedLayoutError("Poetry package must lie within the project")
            if not (included.is_dir() or included.suffix == ".py" and included.is_file()):
                raise UnsupportedLayoutError(f"Poetry package {include!r} was not found")
            if root not in roots:
                roots.append(root)
        return roots
    name = poetry.get("name")
    if not isinstance(name, str):
        project = data.get("project", {})
        name = project.get("name") if isinstance(project, dict) else None
    if not isinstance(name, str) or not name:
        raise UnsupportedLayoutError("Poetry project name is required to infer safe imports")
    normalized = re.sub(r"[-.]+", "_", name)
    for root in (project_root, project_root / "src"):
        for candidate in (normalized, normalized.lower()):
            if (root / candidate / "__init__.py").is_file() or (root / f"{candidate}.py").is_file():
                return [root.resolve()]
    raise UnsupportedLayoutError("Poetry package was not found beside pyproject.toml or under src")


def _pdm_source_roots(project_root: Path, data: Mapping[str, object]) -> List[Path]:
    """Locate the directory pdm-backend collects packages from.

    pdm-backend imports every package under ``[tool.pdm.build].package-dir``;
    when that is unset it uses ``src`` if the directory exists and the
    project root otherwise. ``includes`` and ``source-includes`` select
    files without moving them, so they leave the import root alone.
    """
    tool = data.get("tool", {})
    pdm = _table(tool, "pdm")
    build = _table(pdm, "build")
    if not isinstance(build, dict):
        raise UnsupportedLayoutError("Invalid pdm build configuration")
    package_dir = build.get("package-dir")
    if package_dir is None:
        source = project_root / "src"
        return [source.resolve() if source.is_dir() else project_root.resolve()]
    if not isinstance(package_dir, str) or any(char in package_dir for char in "*?[]"):
        raise UnsupportedLayoutError("Unsupported pdm package-dir; cannot infer safe imports")
    root = (project_root / package_dir).resolve()
    if not root.is_relative_to(project_root) or not root.is_dir():
        raise UnsupportedLayoutError("pdm package-dir must be a directory within the project")
    return [root]


def _project_names(data: Mapping[str, object]) -> List[str]:
    """Candidate distribution names from PEP 621 or a build tool's own table."""
    names: List[str] = []
    project = data.get("project")
    if isinstance(project, dict) and isinstance(project.get("name"), str):
        names.append(project["name"])
    tool = data.get("tool", {})
    if isinstance(tool, dict):
        flit = tool.get("flit", {})
        module = _table(flit, "module")
        if isinstance(module, dict) and isinstance(module.get("name"), str):
            names.append(module["name"])
        poetry = tool.get("poetry", {})
        if isinstance(poetry, dict) and isinstance(poetry.get("name"), str):
            names.append(poetry["name"])
    return names


def _conventional_source_roots(project_root: Path, data: Mapping[str, object]) -> List[Path]:
    """Infer a conventional layout by distribution name, for any build backend.

    Nearly every backend that is not doing something unusual installs a single
    package or module named after the distribution, in the project root or under
    ``src``. This finds that package or module by name without trusting the
    backend, so a conventional project built by an unrecognized backend (for
    example ``flit_scm``) resolves the same way its Flit/Hatch/Poetry cousins
    do. It returns nothing when no name-based package or module exists at a
    conventional location, so a genuinely non-standard layout is still refused
    rather than guessed.
    """
    for name in _project_names(data):
        variants = {
            name,
            name.replace("-", "_"),
            re.sub(r"[-_.]+", "_", name),
            re.sub(r"[-_.]+", "_", name).lower(),
        }
        for candidate in variants:
            if not candidate or any(ch in candidate for ch in "*?[]/\\"):
                continue
            for root in (project_root, project_root / "src"):
                if (root / candidate / "__init__.py").is_file() or (
                    root / f"{candidate}.py"
                ).is_file():
                    return [root.resolve()]
    return []


def _valid_module_path(name: Optional[str]) -> Optional[str]:
    """Return ``name`` only if every dotted component is a valid Python identifier.

    A path component such as a project directory named ``my-project`` is not a
    legal module name; emitting ``from my-project.pkg import x`` produces invalid
    syntax. Callers treat ``None`` as "no importable absolute name exists".
    """
    if not name:
        return None
    parts = name.split(".")
    if all(part.isidentifier() and not iskeyword(part) for part in parts):
        return name
    return None


_SETUPTOOLS_BACKENDS = (None, "setuptools.build_meta", "setuptools.build_meta:__legacy__")
_RECOGNIZED_BACKENDS = _SETUPTOOLS_BACKENDS + (
    "hatchling.build",
    "flit_core.buildapi",
    "poetry.core.masonry.api",
    "pdm.backend",
)


def _build_backend(data: Dict[str, Any]) -> Optional[str]:
    """The declared build backend, or None when the project declares none."""
    build = data.get("build-system", {})
    backend = build.get("build-backend") if isinstance(build, dict) else None
    return backend if isinstance(backend, str) else None


def _declared_source_roots(
    project_root: Path, data: Dict[str, Any], backend: Optional[str]
) -> Tuple[List[Path], Dict[Path, str]]:
    """The source roots the project's packaging declares, with any package prefixes.

    ``[tool.setuptools.package-dir]`` and ``[tool.setuptools.packages.find]``
    are read only under setuptools (or no declared backend, which defaults to
    it): a foreign backend's incidental setuptools table is not trusted. Then the flit, poetry, pdm and hatch
    tables in turn; an unrecognized backend gets conventional, name-based
    inference and is refused only when that finds nothing; a setuptools
    project that leaves discovery implicit gets its ``src`` layout.
    """
    source_roots: List[Path] = []
    package_prefixes: Dict[Path, str] = {}
    mapping = _table(_table(data.get("tool", {}), "setuptools"), "package-dir")
    if backend in _SETUPTOOLS_BACKENDS:
        for prefix, rel in mapping.items():
            if not isinstance(prefix, str) or not isinstance(rel, str):
                continue
            root = (project_root / rel).resolve()
            if root.is_dir():
                source_roots.append(root)
                if prefix:
                    package_prefixes[root] = prefix
    if not source_roots and backend in _SETUPTOOLS_BACKENDS:
        source_roots = _setuptools_find_source_roots(project_root, data)
    if not source_roots and backend == "flit_core.buildapi":
        source_roots = _flit_source_roots(project_root, data)
    if not source_roots and backend == "poetry.core.masonry.api":
        source_roots = _poetry_source_roots(project_root, data)
    if not source_roots and backend == "pdm.backend":
        source_roots = _pdm_source_roots(project_root, data)
    if not source_roots:
        source_roots = _hatch_source_roots(project_root, data)
    if not source_roots and backend not in _RECOGNIZED_BACKENDS:
        source_roots = _conventional_source_roots(project_root, data)
        if not source_roots:
            raise UnsupportedLayoutError(
                f"Unsupported build backend {backend!r}; no conventional "
                "name-based package or src layout to infer safe imports from"
            )
    if not source_roots:
        inferred_root = _setuptools_default_src_root(project_root, data)
        source_roots = [inferred_root if inferred_root is not None else project_root]
    return source_roots, package_prefixes


def _roots_around(
    start_dir: Path, project_root: Path, source_roots: List[Path]
) -> Tuple[Path, List[Path]]:
    """The project root and source roots as seen from ``start_dir``.

    Roots that contain the starting directory win. When none does (a fixture
    or a copy of the tree analyzed outside the package), the longest suffix of
    the starting path that exists under a root maps the copy back onto it;
    failing that, the starting directory is its own root so relative imports
    stay valid.
    """
    containing = [root for root in source_roots if _is_under(start_dir, root)]
    if containing:
        return project_root, containing
    best_candidate: Optional[Path] = None
    max_match_len = 0
    start_parts = start_dir.parts
    for root in source_roots:
        for i in range(len(start_parts)):
            suffix = Path(*start_parts[i:])
            if suffix.is_absolute():
                continue
            if (root / suffix).exists():
                match_len = len(start_parts) - i
                if match_len > max_match_len:
                    max_match_len = match_len
                    best_candidate = Path(*start_parts[:i])
                break
    if best_candidate:
        return project_root, [best_candidate]
    if start_dir != project_root:
        return start_dir, [start_dir]
    return project_root, source_roots


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


@dataclass
class ProjectLayout:
    """Represents the directory structure and import configuration of a Python project.

    Discovers packaging root markers (pyproject.toml, setup.py) and source
    roots from package configuration to generate correct import paths for refactored code.
    """

    project_root: Path
    source_roots: List[Path]
    prefer_absolute_imports: bool = True
    pep420_namespace_packages: bool = True
    package_prefixes: Dict[Path, str] = field(default_factory=dict)
    # True when discovery found a real packaging marker (pyproject/setup.*) at the
    # project root, so an absolute module name is anchored and survives adoption.
    # False for a bare directory, where only relative imports are trustworthy.
    metadata_root: bool = False

    @classmethod
    def discover(
        cls,
        start_path: Path,
        *,
        prefer_absolute_imports: Optional[bool] = None,
        pep420_namespace_packages: Optional[bool] = None,
    ) -> "ProjectLayout":
        """Discover project layout from a starting path.

        Heuristics:
        - Project root is the nearest ancestor containing pyproject.toml, setup.cfg, or setup.py
        - Source roots come from pyproject [tool.setuptools.package-dir] (e.g., {"": "src"})
          or default to [project_root] (flat layout). If mapping exists, add each mapped directory.
        """
        project_root = find_project_root(start_path)
        data = load_pyproject(project_root)
        backend = _build_backend(data)
        prefer_abs = True if prefer_absolute_imports is None else prefer_absolute_imports
        pep420 = True if pep420_namespace_packages is None else pep420_namespace_packages

        source_roots, package_prefixes = _declared_source_roots(project_root, data, backend)
        start_resolved = start_path.resolve()
        start_dir = start_resolved.parent if start_resolved.is_file() else start_resolved
        project_root, source_roots = _roots_around(start_dir, project_root, source_roots)

        metadata_root = any(
            (project_root / marker).exists()
            for marker in ("pyproject.toml", "setup.cfg", "setup.py")
        )
        return cls(
            project_root=project_root,
            source_roots=source_roots,
            prefer_absolute_imports=prefer_abs,
            pep420_namespace_packages=pep420,
            package_prefixes=package_prefixes,
            metadata_root=metadata_root,
        )

    def module_name_for(self, file_path: Path) -> Optional[str]:
        """Compute a dotted module name for a given file path.

        Returns None if file is outside all known source roots.
        """
        file_path = file_path.resolve()
        # A named setuptools mapping identifies the package itself, not a sys.path root.
        # Resolve the most specific mapping first, even beneath a default source root.
        for package_root, prefix in sorted(
            self.package_prefixes.items(), key=lambda item: len(item[0].parts), reverse=True
        ):
            try:
                relative = file_path.relative_to(package_root)
            except ValueError:
                continue
            if relative.suffix != ".py":
                return None
            parts = list(relative.with_suffix("").parts)
            if parts and parts[-1] == "__init__":
                parts.pop()
            return _valid_module_path(".".join([prefix, *parts]))
        # The most specific root wins: a project may list a nested source
        # directory beside the project root (poetry ``from``, setuptools
        # ``package-dir``), and the file's module name is relative to the
        # innermost root that contains it.
        for src_root in sorted(self.source_roots, key=lambda root: len(root.parts), reverse=True):
            try:
                rel = file_path.relative_to(src_root)
                if rel.suffix != ".py":
                    return None
                parts = list(rel.with_suffix("").parts)
                if parts and parts[-1] == "__init__":
                    parts.pop()
                # Validate package path components
                if not parts:
                    return None
                # Without namespace packages a bare directory is not a
                # package, so no name passes through one.
                if not self.pep420_namespace_packages and not all(
                    is_package_dir(src_root.joinpath(*parts[: index + 1]))
                    for index in range(len(parts) - 1)
                ):
                    return None
                return _valid_module_path(".".join(parts))
            except ValueError:
                continue

        # Fallback: compute relative to project_root
        try:
            rel = file_path.relative_to(self.project_root)
            if rel.suffix != ".py":
                return None
            return _valid_module_path(".".join(rel.with_suffix("").parts))
        except ValueError:
            return None


def find_project_root(start_path: Path) -> Path:
    """Find nearest ancestor that looks like the project root."""
    start = start_path.resolve()
    base_dir = start.parent if start.is_file() else start

    # Prefer a nearby directory containing packaging markers, but fall back to
    # the provided directory when none are found while walking upward.
    markers = {"pyproject.toml", "setup.cfg", "setup.py"}
    current = base_dir
    while True:
        if any((current / m).exists() for m in markers):
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    # VCS roots do not establish sys.path. Honor classic package ancestry;
    # otherwise the explicit source directory is the only available anchor.
    while (base_dir / "__init__.py").is_file():
        base_dir = base_dir.parent
    return base_dir
