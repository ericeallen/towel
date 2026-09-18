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

"""
Utilities for understanding project/package layout to generate robust import paths
for cross-file refactorings.

Python 3.10 uses the TOML backport; newer versions use the standard library.
"""

from __future__ import annotations

from keyword import iskeyword
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .diagnostics import LOG
from .unification.exceptions import UnsupportedLayoutError
import re
import sys

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


def load_pyproject(project_root: Path) -> Dict[str, Any]:
    """Best-effort load of pyproject.toml using the available TOML parser.

    Returns an empty dict if parsing fails or file does not exist.
    """
    pyproject_path = project_root / "pyproject.toml"
    if not pyproject_path.exists():
        return {}

    # Use the platform TOML parser (tomli on Python 3.10).
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


def is_package_dir(path: Path, pep420: bool) -> bool:
    """Determine if a directory is a Python package root.

    - Traditional packages: must contain __init__.py
    - Namespace packages (PEP 420): any directory is a potential package component
    """
    if not path.is_dir():
        return False
    if pep420:
        return True
    return (path / "__init__.py").exists()


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
    hatch = tool.get("hatch", {}) if isinstance(tool, dict) else {}
    build = hatch.get("build", {}) if isinstance(hatch, dict) else {}
    targets = build.get("targets", {}) if isinstance(build, dict) else {}
    wheel = targets.get("wheel", {}) if isinstance(targets, dict) else {}
    if not isinstance(build, dict) or not isinstance(wheel, dict):
        raise UnsupportedLayoutError("Invalid Hatch wheel configuration")
    for key in ("include", "force-include"):
        if key in wheel or key in build:
            raise UnsupportedLayoutError(
                f"Unsupported Hatch {key} layout; cannot infer safe imports"
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


def _flit_source_roots(project_root: Path, data: Dict[str, Any]) -> List[Path]:
    """Locate the single module Flit builds, in the project root or under ``src``.

    Flit packages exactly one importable module named by ``[tool.flit.module]``
    or, failing that, by the project name. It looks for that module as a
    package directory or a single file, first beside ``pyproject.toml`` and
    then under ``src``. The module's parent is therefore the import root.
    """
    tool = data.get("tool", {})
    flit = tool.get("flit", {}) if isinstance(tool, dict) else {}
    module = flit.get("module", {}) if isinstance(flit, dict) else {}
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
    poetry = tool.get("poetry", {}) if isinstance(tool, dict) else {}
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
    pdm = tool.get("pdm", {}) if isinstance(tool, dict) else {}
    build = pdm.get("build", {}) if isinstance(pdm, dict) else {}
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
        module = flit.get("module", {}) if isinstance(flit, dict) else {}
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
        build = data.get("build-system", {})
        backend = build.get("build-backend") if isinstance(build, dict) else None
        recognized_backend = backend in (
            None,
            "setuptools.build_meta",
            "setuptools.build_meta:__legacy__",
            "hatchling.build",
            "flit_core.buildapi",
            "poetry.core.masonry.api",
            "pdm.backend",
        )

        # Default settings
        prefer_abs = True if prefer_absolute_imports is None else prefer_absolute_imports
        pep420 = True if pep420_namespace_packages is None else pep420_namespace_packages

        # Determine source roots
        source_roots: List[Path] = []

        package_prefixes: Dict[Path, str] = {}
        tool = data.get("tool", {})
        setuptools = tool.get("setuptools", {}) if isinstance(tool, dict) else {}
        mapping = setuptools.get("package-dir", {}) if isinstance(setuptools, dict) else {}
        # ``[tool.setuptools]`` is meaningful only under setuptools (or an
        # undeclared backend, which defaults to setuptools); a foreign backend's
        # incidental setuptools table is not trusted.
        if backend in (
            None,
            "setuptools.build_meta",
            "setuptools.build_meta:__legacy__",
        ) and isinstance(mapping, dict):
            for prefix, rel in mapping.items():
                if not isinstance(prefix, str) or not isinstance(rel, str):
                    continue
                root = (project_root / rel).resolve()
                if root.is_dir():
                    source_roots.append(root)
                    if prefix:
                        package_prefixes[root] = prefix
        if not source_roots and backend == "flit_core.buildapi":
            source_roots = _flit_source_roots(project_root, data)
        if not source_roots and backend == "poetry.core.masonry.api":
            source_roots = _poetry_source_roots(project_root, data)
        if not source_roots and backend == "pdm.backend":
            source_roots = _pdm_source_roots(project_root, data)
        if not source_roots:
            source_roots = _hatch_source_roots(project_root, data)

        start_resolved = start_path.resolve()
        start_dir = start_resolved.parent if start_resolved.is_file() else start_resolved

        # An unrecognized backend gets conventional, name-based inference rather
        # than an outright rejection: a package or module named after the
        # distribution, in the project root or under ``src``. Only a layout that
        # cannot be inferred that way is refused.
        if not source_roots and not recognized_backend:
            conventional = _conventional_source_roots(project_root, data)
            if conventional:
                source_roots = conventional
            else:
                raise UnsupportedLayoutError(
                    f"Unsupported build backend {backend!r}; no conventional "
                    "name-based package or src layout to infer safe imports from"
                )

        # Setuptools discovers classic packages under src when project metadata
        # leaves package discovery implicit. Unconfigured trees and other build
        # systems retain the project-root fallback rather than guessing layout.
        if not source_roots:
            inferred_root = _setuptools_default_src_root(project_root, data)
            source_roots = [inferred_root if inferred_root is not None else project_root]

        # Prefer source roots that actually contain the starting directory. When none of the
        # discovered roots include the path we're analyzing (common for test fixtures copied
        # outside the main package tree), fall back to treating the starting directory as the
        # root so relative imports remain valid.
        filtered_roots: List[Path] = []
        for root in source_roots:
            try:
                start_dir.relative_to(root)
                filtered_roots.append(root)
            except ValueError:
                continue

        if filtered_roots:
            source_roots = filtered_roots
        else:
            # Fallback: try to map start_dir to an existing source root by matching directory structure
            # This handles cases where we are running on a copy of the source tree (e.g. 'cleaned' dir)
            best_candidate = None
            max_match_len = 0

            start_parts = start_dir.parts

            for root in source_roots:
                # Try to find the longest suffix of start_dir that exists under root
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
                source_roots = [best_candidate]
            elif start_dir != project_root:
                project_root = start_dir
                source_roots = [start_dir]

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
                if not self.pep420_namespace_packages:
                    # Ensure every directory in the chain is a traditional package
                    cursor = src_root
                    for comp in parts[:-1]:
                        cursor = cursor / comp
                        if not is_package_dir(cursor, pep420=False):
                            # Not a classic package path; fall back to absolute-from-project
                            break
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
