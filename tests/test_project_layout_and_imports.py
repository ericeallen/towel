import os
import tempfile
from pathlib import Path

from towel.unification.project_layout import ProjectLayout
from towel.unification.refactor_engine import UnificationRefactorEngine


def _write(p: Path, content: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def test_project_layout_module_name_src_layout_pep420():
    # Create a temporary src-layout project with pyproject.toml
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(
            root / "pyproject.toml",
            """
[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"

[tool.setuptools]
package-dir = {"" = "src"}
            """.strip(),
        )

        a_py = root / "src" / "acme" / "core" / "a.py"
        b_py = root / "src" / "acme" / "core" / "b.py"

        _write(
            a_py,
            """
def f1(x):
    # duplicate block start
    if x is None:
        return 0
    if x < 0:
        return -x
    return x
    # duplicate block end
""".lstrip(),
        )

        _write(
            b_py,
            """
def f2(x):
    # duplicate block start
    if x is None:
        return 0
    if x < 0:
        return -x
    return x
    # duplicate block end
""".lstrip(),
        )

        layout = ProjectLayout.discover(root)
        mod_a = layout.module_name_for(a_py)
        mod_b = layout.module_name_for(b_py)

        # By default prefer absolute imports and PEP420 enabled
        assert mod_a == "acme.core.a"
        assert mod_b == "acme.core.b"


def test_import_insertion_prefers_absolute_even_same_dir_when_requested():
    # Verify that when prefer_absolute_imports=True, engine uses absolute module name
    # even when files are in the same directory within a package.
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(
            root / "pyproject.toml",
            """
[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"

[tool.setuptools]
package-dir = {"" = "src"}
            """.strip(),
        )

        pkg_dir = root / "src" / "pkg" / "mod"
        a_py = pkg_dir / "alpha.py"
        b_py = pkg_dir / "beta.py"

        # Two functions with identical blocks to trigger extraction
        _write(
            a_py,
            """
def fa(x):
    s = 0
    if x:
        s += 1
    return s
""".lstrip(),
        )

        _write(
            b_py,
            """
def fb(x):
    s = 0
    if x:
        s += 1
    return s
""".lstrip(),
        )

        engine = UnificationRefactorEngine(
            max_parameters=5,
            min_lines=3,
            parameterize_constants=True,
            prefer_absolute_imports=True,
            pep420_namespace_packages=True,
        )

        proposals = engine.analyze_directory(str(root / "src"), recursive=True)
        assert proposals, "Expected at least one proposal in src-layout package"

        proposal = proposals[0]
        modified = engine.apply_refactoring_multi_file(proposal)

        # Determine which file got the import (the non-canonical file)
        canonical = Path(proposal.file_path)
        other_files = [Path(fp) for fp in modified.keys() if Path(fp) != canonical]
        # There should be one other file in this simple case
        assert other_files, "Expected another file to import the extracted function"
        other_path = other_files[0]
        content = modified[str(other_path)]

        # With prefer_absolute_imports=True and src-layout package, expect absolute import
        # Module should be pkg.mod.alpha if alpha.py is the canonical file
        if canonical.name == "alpha.py":
            expected_prefix = "from pkg.mod.alpha import __extracted_func"
        else:
            expected_prefix = "from pkg.mod.beta import __extracted_func"

        assert (
            expected_prefix in content
        ), f"Expected absolute import: {expected_prefix}\nGot:\n{content}"
