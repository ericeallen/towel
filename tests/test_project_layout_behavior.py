import os
from pathlib import Path
import tempfile
import textwrap
import unittest

from src.towel.unification.project_layout import ProjectLayout


class TestProjectLayoutBehavior(unittest.TestCase):
    def test_default_layout_no_pyproject(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Create a simple tree without pyproject
            (root / "src" / "pkg").mkdir(parents=True)
            mod = root / "src" / "pkg" / "mod.py"
            mod.write_text("x = 1\n")

            layout = ProjectLayout.discover(root)
            self.assertEqual(layout.project_root, root.resolve())
            self.assertEqual(layout.source_roots, [root.resolve()])

            name = layout.module_name_for(mod)
            # Without package-dir mapping, we intentionally keep 'src' in the name
            self.assertEqual(name, "src.pkg.mod")

            nonpy = root / "src" / "pkg" / "data.txt"
            nonpy.write_text("data")
            self.assertIsNone(layout.module_name_for(nonpy))

    def test_with_pyproject_mapping_src(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Write pyproject mapping "" -> "src"
            (root / "pyproject.toml").write_text(
                textwrap.dedent(
                    """
                    [tool.setuptools]
                    package-dir = {"" = "src"}
                    """
                ).strip()
            )
            (root / "src" / "pkg").mkdir(parents=True)
            mod = root / "src" / "pkg" / "mod.py"
            mod.write_text("x = 1\n")

            layout = ProjectLayout.discover(root)
            # Should pick src as the sole source root
            self.assertEqual(layout.source_roots, [(root / "src").resolve()]) 

            name = layout.module_name_for(mod)
            self.assertEqual(name, "pkg.mod")

            # A file outside the source root should fall back to project-root-relative
            other_dir = root / "other"
            other_dir.mkdir()
            other = other_dir / "file.py"
            other.write_text("pass\n")
            self.assertEqual(layout.module_name_for(other), "other.file")

    def test_pep420_toggle_and_nonpackages(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # No pyproject mapping: default source_root is project root
            pkgdir = root / "ns" / "sub"
            pkgdir.mkdir(parents=True)
            mod = pkgdir / "mod.py"
            mod.write_text("x = 1\n")

            # pep420 True (default): directories are considered packages implicitly
            layout_ns = ProjectLayout.discover(root, pep420_namespace_packages=True)
            self.assertEqual(layout_ns.module_name_for(mod), "ns.sub.mod")

            # pep420 False: require classic packages with __init__.py, but our implementation
            # still returns a module name; exercise the branch without __init__.py
            layout_no_ns = ProjectLayout.discover(root, pep420_namespace_packages=False)
            self.assertEqual(layout_no_ns.module_name_for(mod), "ns.sub.mod")

            # Add __init__.py and ensure the same result (covers the classic package path)
            (root / "ns" / "__init__.py").write_text("")
            (root / "ns" / "sub" / "__init__.py").write_text("")
            self.assertEqual(layout_no_ns.module_name_for(mod), "ns.sub.mod")

    def test_discover_from_file_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            nested = root / "nested"
            nested.mkdir()
            f = nested / "child.py"
            f.write_text("pass\n")

            # Discover starting from a file path chooses its directory as project_root
            layout = ProjectLayout.discover(f)
            self.assertEqual(layout.project_root, nested.resolve())
            self.assertEqual(layout.module_name_for(f), "child")

    def test_malformed_pyproject_and_outside_paths(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Malformed TOML should be handled gracefully (treated as no mapping)
            (root / "pyproject.toml").write_text("this = not = valid [[\n")
            (root / "src" / "pkg").mkdir(parents=True)
            mod = root / "src" / "pkg" / "mod.py"
            mod.write_text("x = 1\n")

            layout = ProjectLayout.discover(root)
            # Falls back to project root as source root due to parsing failure
            self.assertEqual(layout.source_roots, [root.resolve()])
            self.assertEqual(layout.module_name_for(mod), "src.pkg.mod")

            # File completely outside the project should return None
            with tempfile.TemporaryDirectory() as other_td:
                outside = Path(other_td) / "ext.py"
                outside.write_text("pass\n")
                self.assertIsNone(layout.module_name_for(outside))

    def test_multiple_source_roots_mixed_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Mixed mapping: default '' -> src, plus named package 'pkg2' in lib2
            (root / "pyproject.toml").write_text(
                textwrap.dedent(
                    """
                    [tool.setuptools]
                    package-dir = {"" = "src", "pkg2" = "lib2"}
                    """
                ).strip()
            )
            (root / "src" / "alpha").mkdir(parents=True)
            (root / "lib2" / "pkg2").mkdir(parents=True)
            f1 = root / "src" / "alpha" / "beta.py"
            f2 = root / "lib2" / "pkg2" / "mod.py"
            f1.write_text("pass\n")
            f2.write_text("pass\n")

            layout = ProjectLayout.discover(root)
            # Ensure both source roots recognized
            self.assertTrue((root / "src").resolve() in layout.source_roots)
            self.assertTrue((root / "lib2").resolve() in layout.source_roots)

            # module names relative to their respective roots
            self.assertEqual(layout.module_name_for(f1), "alpha.beta")
            self.assertEqual(layout.module_name_for(f2), "pkg2.mod")


if __name__ == "__main__":
    unittest.main()
