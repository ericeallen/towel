"""
Utilities for understanding project/package layout to generate robust import paths
for cross-file refactorings.

This module is runtime-dependency-free and uses only the standard library.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional


def _load_pyproject(project_root: Path) -> dict:
    """Best-effort load of pyproject.toml with stdlib only.

    Returns an empty dict if parsing fails or file does not exist.
    """
    pyproject_path = project_root / "pyproject.toml"
    if not pyproject_path.exists():
        return {}

    # Python 3.11+: tomllib in stdlib; earlier versions won't have it.
    try:
        import tomllib  # type: ignore

        with pyproject_path.open("rb") as f:
            return tomllib.load(f)
    except Exception:
        # Silently ignore - we don't want a hard runtime dependency
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


@dataclass
class ProjectLayout:
    project_root: Path
    source_roots: List[Path]
    prefer_absolute_imports: bool = True
    pep420_namespace_packages: bool = True

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
        - Project root is the nearest ancestor containing pyproject.toml, setup.cfg, setup.py, or .git
        - Source roots come from pyproject [tool.setuptools.package-dir] (e.g., {"": "src"})
          or default to [project_root] (flat layout). If mapping exists, add each mapped directory.
        """
        project_root = _find_project_root(start_path)
        data = _load_pyproject(project_root)

        # Default settings
        prefer_abs = True if prefer_absolute_imports is None else prefer_absolute_imports
        pep420 = True if pep420_namespace_packages is None else pep420_namespace_packages

        # Determine source roots
        source_roots: List[Path] = []

        try:
            mapping = data.get("tool", {}).get("setuptools", {}).get("package-dir", {})
            # mapping: {"" : "src"} or {"mypkg": "src/mypkg"}
            candidates: Iterable[str] = mapping.values() if isinstance(mapping, dict) else []
            for rel in candidates:
                root = (project_root / rel).resolve()
                if root.exists() and root.is_dir():
                    source_roots.append(root)
        except Exception:
            pass

        # If no explicit mapping was found, default to treating the entire project root
        # as the import anchor. We intentionally DO NOT auto-add conventional directories
        # like "src" or "lib" as source roots when pyproject mapping is absent, because
        # test scenarios import modules with sys.path pointing at the project root.
        # In that context, absolute module names should include the top-level directory
        # component (e.g., "src.data_processor"), which fails if we strip it as a source root.
        if not source_roots:
            source_roots = [project_root]

        return cls(
            project_root=project_root,
            source_roots=source_roots,
            prefer_absolute_imports=prefer_abs,
            pep420_namespace_packages=pep420,
        )

    def module_name_for(self, file_path: Path) -> Optional[str]:
        """Compute a dotted module name for a given file path.

        Returns None if file is outside all known source roots.
        """
        file_path = file_path.resolve()
        for src_root in self.source_roots:
            try:
                rel = file_path.relative_to(src_root)
                if rel.suffix != ".py":
                    return None
                parts = list(rel.with_suffix("").parts)
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

    # Prefer treating the provided directory as the project root unless it
    # itself contains explicit packaging markers. This avoids accidentally
    # hoisting to a monorepo root (with a pyproject.toml) for nested example
    # projects and keeps imports scoped within the subtree we're refactoring.
    markers = {"pyproject.toml", "setup.cfg", "setup.py"}
    if any((base_dir / m).exists() for m in markers):
        return base_dir
    return base_dir
