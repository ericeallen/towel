# Copyright 2025 Eric Allen
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

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional
import re
import sys

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


def _load_pyproject(project_root: Path) -> Dict[str, Any]:
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
    except (OSError, ValueError):
        # Malformed or inaccessible configuration provides no layout information.
        return {}


def _is_package_dir(path: Path, pep420: bool) -> bool:
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
        raise ValueError("Hatch layout in hatch.toml is unsupported; cannot infer safe imports")
    tool = data.get("tool", {})
    hatch = tool.get("hatch", {}) if isinstance(tool, dict) else {}
    build = hatch.get("build", {}) if isinstance(hatch, dict) else {}
    targets = build.get("targets", {}) if isinstance(build, dict) else {}
    wheel = targets.get("wheel", {}) if isinstance(targets, dict) else {}
    if not isinstance(build, dict) or not isinstance(wheel, dict):
        raise ValueError("Invalid Hatch wheel configuration")
    for key in ("sources", "only-include", "include", "force-include"):
        if key in wheel or key in build:
            raise ValueError(f"Unsupported Hatch {key} layout; cannot infer safe imports")
    packages = wheel.get("packages", build.get("packages"))
    if packages is not None:
        if not isinstance(packages, list) or not packages:
            raise ValueError("Hatch packages must be a nonempty list of classic package paths")
        roots = []
        for value in packages:
            if not isinstance(value, str) or any(char in value for char in "*?[]"):
                raise ValueError("Unsupported Hatch package path")
            package = (project_root / value).resolve()
            if not package.is_relative_to(project_root) or not (package / "__init__.py").is_file():
                raise ValueError("Hatch package must be a classic package within the project")
            if package.parent not in roots:
                roots.append(package.parent)
        return roots
    project = data.get("project", {})
    name = project.get("name") if isinstance(project, dict) else None
    if not isinstance(name, str):
        raise ValueError("Hatch project name is required to infer safe imports")
    normalized = re.sub(r"[-_.]+", "_", name).lower()
    for root in (project_root, project_root / "src"):
        if (root / normalized / "__init__.py").is_file():
            return [root.resolve()]
    raise ValueError("Unsupported Hatch default package layout; cannot infer safe imports")


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
        raise ValueError("Flit module name is required to infer safe imports")
    normalized = name.replace("-", "_")
    for root in (project_root, project_root / "src"):
        if (root / normalized / "__init__.py").is_file() or (root / f"{normalized}.py").is_file():
            return [root.resolve()]
    raise ValueError("Flit module was not found beside pyproject.toml or under src")


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
        project_root = _find_project_root(start_path)
        data = _load_pyproject(project_root)
        build = data.get("build-system", {})
        backend = build.get("build-backend") if isinstance(build, dict) else None
        if backend not in (
            None,
            "setuptools.build_meta",
            "setuptools.build_meta:__legacy__",
            "hatchling.build",
            "flit_core.buildapi",
        ):
            raise ValueError(f"Unsupported build backend {backend!r}; cannot infer safe imports")

        # Default settings
        prefer_abs = True if prefer_absolute_imports is None else prefer_absolute_imports
        pep420 = True if pep420_namespace_packages is None else pep420_namespace_packages

        # Determine source roots
        source_roots: List[Path] = []

        package_prefixes: Dict[Path, str] = {}
        tool = data.get("tool", {})
        setuptools = tool.get("setuptools", {}) if isinstance(tool, dict) else {}
        mapping = setuptools.get("package-dir", {}) if isinstance(setuptools, dict) else {}
        if backend not in ("hatchling.build", "flit_core.buildapi") and isinstance(mapping, dict):
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
        if not source_roots:
            source_roots = _hatch_source_roots(project_root, data)

        start_resolved = start_path.resolve()
        start_dir = start_resolved.parent if start_resolved.is_file() else start_resolved

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

        return cls(
            project_root=project_root,
            source_roots=source_roots,
            prefer_absolute_imports=prefer_abs,
            pep420_namespace_packages=pep420,
            package_prefixes=package_prefixes,
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
            return ".".join([prefix, *parts])
        for src_root in self.source_roots:
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
                        if not _is_package_dir(cursor, pep420=False):
                            # Not a classic package path; fall back to absolute-from-project
                            break
                return ".".join(parts)
            except ValueError:
                continue

        # Fallback: compute relative to project_root
        try:
            rel = file_path.relative_to(self.project_root)
            if rel.suffix != ".py":
                return None
            return ".".join(rel.with_suffix("").parts)
        except Exception:
            return None


def _find_project_root(start_path: Path) -> Path:
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
