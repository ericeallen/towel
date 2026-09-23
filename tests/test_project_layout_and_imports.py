import tempfile
from pathlib import Path

from tests.test_helpers import write_file
from towel.project_layout import ProjectLayout
from towel.unification.refactor_engine import UnificationRefactorEngine


def test_project_layout_module_name_src_layout_pep420():
    # Create a temporary src-layout project with pyproject.toml
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write_file(
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

        write_file(
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

        write_file(
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


def test_the_retired_absolute_preference_no_longer_decides_an_import():
    # prefer_absolute_imports is accepted and ignored: between two modules of
    # one package that never spell their own package absolutely, the import
    # is the relative one, which holds wherever the package is imported.
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write_file(
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
        # Regular packages: setuptools ships no bare directory without
        # find_namespace, so only these give the name a second derivation.
        write_file(root / "src" / "pkg" / "__init__.py", "")
        write_file(pkg_dir / "__init__.py", "")

        # Two functions with identical blocks to trigger extraction
        write_file(
            a_py,
            """
def fa(x):
    s = 0
    if x:
        s += 1
    return s
""".lstrip(),
        )

        write_file(
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
            reuse_existing_functions=False,  # the helper import spelling is under test
            cross_module_helpers=True,
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

        expected_prefix = f"from .{canonical.stem} import __extracted_func"
        assert (
            expected_prefix in content
        ), f"Expected a relative import: {expected_prefix}\nGot:\n{content}"


def test_module_name_none_for_non_identifier_root():
    """A project root whose directory name is not a valid identifier is not importable.

    Regression: module_name_for used to join path parts into a dotted name
    without checking they were legal identifiers, producing names like
    ``my-clean-copy.pkg.mod`` that are a SyntaxError when emitted as an import.
    """
    import ast

    with tempfile.TemporaryDirectory() as td:
        # A classic-package tree whose top directory name contains a hyphen and
        # no packaging metadata, so discovery falls back to the directory name.
        root = Path(td) / "my-clean-copy"
        pkg = root / "unification"
        write_file(root / "__init__.py", "")
        write_file(pkg / "__init__.py", "")
        module = pkg / "scope_analyzer.py"
        write_file(module, "VALUE = 1\n")

        layout = ProjectLayout.discover(module)
        # The only candidate absolute name would contain the invalid component
        # 'my-clean-copy'; refuse it rather than emit an illegal dotted name.
        assert layout.module_name_for(module) is None
        # Sanity: the invalid name really is not a legal import.
        try:
            ast.parse("from my-clean-copy.unification.scope_analyzer import x")
            raise AssertionError("expected the hyphenated import to be a SyntaxError")
        except SyntaxError:
            pass


def test_same_dir_helper_uses_relative_import_under_non_identifier_root():
    """Cross-file extraction into a hyphenated output dir must still compile.

    Regression: the engine emitted ``from my-out-dir.pkg.mod import helper`` and
    its own compile gate aborted the whole run. A same-directory helper is now
    imported relatively, which is valid for any directory name.
    """
    import ast

    with tempfile.TemporaryDirectory() as td:
        # Hyphenated package root, no pyproject: mirrors `towel dry X /tmp/my-out`.
        root = Path(td) / "my-out-dir"
        pkg = root / "unification"
        write_file(root / "__init__.py", "")
        write_file(pkg / "__init__.py", "")
        write_file(
            pkg / "alpha.py",
            "def f1(x):\n    if x is None:\n        return 0\n    if x < 0:\n"
            "        return -x\n    return x\n",
        )
        write_file(
            pkg / "beta.py",
            "def f2(x):\n    if x is None:\n        return 0\n    if x < 0:\n"
            "        return -x\n    return x\n",
        )

        engine = UnificationRefactorEngine(
            max_parameters=5,
            min_lines=3,
            prefer_absolute_imports=True,
            reuse_existing_functions=False,  # the helper import spelling is under test
            cross_module_helpers=True,
        )
        proposals = engine.analyze_directory(str(root), recursive=True)
        assert proposals, "Expected a cross-file proposal between alpha.py and beta.py"
        modified = engine.apply_refactoring_multi_file(proposals[0])

        for path, content in modified.items():
            # Must compile: the whole point of the fix.
            ast.parse(content)
        # The importing file references the helper via a relative import.
        importer = next(
            content for content in modified.values() if "import __extracted_func" in content
        )
        assert "from .alpha import __extracted_func" in importer or (
            "from .beta import __extracted_func" in importer
        ), importer


def test_out_of_place_package_import_is_relative_not_output_dir_name():
    """Refactoring a package out-of-place must not bake the output dir into imports.

    Regression: with prefer_absolute_imports defaulting on, a same-directory
    cross-file helper used to be imported as `from <output_dir>.sub.mod import ...`,
    which breaks once the cleaned copy is adopted into its real location. With no
    packaging metadata to anchor an absolute name, the import must be relative.
    """
    import ast

    with tempfile.TemporaryDirectory() as td:
        # A classic package whose directory name is a valid identifier but is NOT
        # the real installed package name — e.g. a temp output directory.
        pkg = Path(td) / "cleaned_output"
        write_file(pkg / "__init__.py", "")
        write_file(
            pkg / "alpha.py",
            "def f1(x):\n    if x is None:\n        return 0\n    if x < 0:\n"
            "        return -x\n    return x\n",
        )
        write_file(
            pkg / "beta.py",
            "def f2(x):\n    if x is None:\n        return 0\n    if x < 0:\n"
            "        return -x\n    return x\n",
        )

        engine = UnificationRefactorEngine(
            max_parameters=5,
            min_lines=3,
            prefer_absolute_imports=True,
            reuse_existing_functions=False,  # the helper import spelling is under test
            cross_module_helpers=True,
        )
        proposals = engine.analyze_directory(str(pkg), recursive=True)
        assert proposals, "Expected a cross-file proposal between alpha.py and beta.py"
        modified = engine.apply_refactoring_multi_file(proposals[0])

        for content in modified.values():
            ast.parse(content)
        importer = next(c for c in modified.values() if "import __extracted_func" in c)
        # Relative import, and the output dir name never appears.
        assert "cleaned_output" not in importer, importer
        assert "from .alpha import __extracted_func" in importer or (
            "from .beta import __extracted_func" in importer
        ), importer
