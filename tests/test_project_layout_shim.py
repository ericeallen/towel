"""The old import path for the project layout still resolves to the moved module."""

from __future__ import annotations

import towel.project_layout as current
import towel.unification.project_layout as shim


def test_the_unification_path_re_exports_the_project_layout_module() -> None:
    assert shim.find_project_root is current.find_project_root
    assert shim.is_package_dir is current.is_package_dir
    assert shim.load_pyproject is current.load_pyproject
    assert set(shim.__all__) == {"find_project_root", "is_package_dir", "load_pyproject"}
