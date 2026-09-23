"""End-to-end regression for the ecosystem check's out-of-place workflow.

The standing ecosystem check refactors a package *out of place* (into a
differently located output directory) and then adopts the cleaned copy back
over the package, exactly the way a user diffs a refactor before keeping it.
That workflow once produced circular imports: the engine hosted a shared helper
in a module the borrower already imported, and the import-cycle guard — blinded
by the relocated layout — failed to veto it, so the adopted package raised
``ImportError: cannot import name '...' (circular import)`` on import.

These tests reproduce that workflow on a self-contained fixture. They fail on
the pre-fix engine (the adopted package does not import) and pass once the guard
sees the relocated edge and the engine places the helper in the module that
avoids the cycle.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from towel.unification.refactor_engine import UnificationRefactorEngine

# A block duplicated within ``a`` (a1/a2), within ``b`` (b1/b2), and across the
# two modules, plus a second block unique to ``b`` so both modules are rewritten
# in the first pass and re-analysed together in the localized follow-up.
_SHARED = (
    "    total = 0\n"
    "    for it in seq:\n"
    "        total += it * factor\n"
    "        total -= it % 3\n"
    "    return total\n"
)
_OTHER = (
    "    out = []\n"
    "    for it in seq:\n"
    "        out.append(it * factor + 7)\n"
    "        out.append(it - 2)\n"
    "    return out\n"
)


class TestOutOfPlaceCycleRegression(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="towel-oop-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # Mirror the harness: input package under ready/, output under a
        # *separate* parent (work/), never a sibling of the input.
        self.pkg = self.tmp / "ready" / "app"
        self.pkg.mkdir(parents=True)
        (self.pkg / "__init__.py").write_text("", encoding="utf-8")
        # ``a`` imports ``b`` (an absolute, package-qualified import), so hosting
        # the helper in ``a`` would close an a<->b cycle; ``b`` is the safe home.
        (self.pkg / "a.py").write_text(
            "from app.b import BASE\n\n"
            "def a1(seq, factor):\n" + _SHARED + "\ndef a2(seq, factor):\n" + _SHARED,
            encoding="utf-8",
        )
        (self.pkg / "b.py").write_text(
            "BASE = 1\n\n"
            "def b1(seq, factor):\n"
            + _SHARED
            + "\ndef b2(seq, factor):\n"
            + _SHARED
            + "\ndef b3(seq, factor):\n"
            + _OTHER
            + "\ndef b4(seq, factor):\n"
            + _OTHER,
            encoding="utf-8",
        )

    def _refactor_and_adopt(self) -> None:
        cleaned = self.tmp / "work" / "cleaned"
        UnificationRefactorEngine(cross_module_helpers=True).refactor_directory_to_fixed_point(
            str(self.pkg), str(cleaned), max_iterations=0, progress="none"
        )
        # Adopt the cleaned copy back over the package (as the harness does).
        shutil.rmtree(self.pkg)
        shutil.copytree(cleaned, self.pkg)

    def _import(self, module: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            cwd=str(self.pkg.parent),
            capture_output=True,
            text=True,
        )

    def test_adopted_package_imports_without_circular_import(self) -> None:
        self._refactor_and_adopt()
        for module in ("app", "app.a", "app.b"):
            result = self._import(module)
            self.assertEqual(
                result.returncode,
                0,
                msg=f"importing {module} failed:\n{result.stderr}",
            )
            self.assertNotIn("circular import", result.stderr)

    def test_shared_helper_is_kept_and_placed_in_the_safe_module(self) -> None:
        self._refactor_and_adopt()
        a_source = (self.pkg / "a.py").read_text(encoding="utf-8")
        b_source = (self.pkg / "b.py").read_text(encoding="utf-8")
        # The cross-file duplicate is still deduplicated (not declined): the
        # helper both modules share lives in ``b``, which ``a`` already
        # imports, so no back-edge is introduced, and no function is made to
        # call another existing one.
        self.assertRegex(a_source, r"from (?:app\.|\.)b import __extracted_func_\d+")
        self.assertNotIn("b1(", a_source)
        self.assertIn("def __extracted_func", b_source)
        self.assertNotRegex(b_source, r"from (?:app\.|\.)a import")


if __name__ == "__main__":
    unittest.main()


class TestSiblingOriginalRegression(unittest.TestCase):
    """The out-of-place output sits *beside* the original clone, as the ecosystem
    harness lays it out (``sphinx`` next to ``sphinx-cleaned``).

    A submodule's absolute import of its own package (``from sphinx.transforms
    import SphinxTransform``) then resolved against the original, where the
    helper import that closes the cycle did not yet exist, so the guard let
    ``transforms/__init__`` import a submodule that imports the package back.
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="towel-sibling-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.pkg = self.tmp / "sphinx"
        transforms = self.pkg / "transforms"
        transforms.mkdir(parents=True)
        (self.pkg / "__init__.py").write_text("", encoding="utf-8")
        (transforms / "__init__.py").write_text(
            "class SphinxTransform:\n    pass\n\n"
            "def t1(seq, factor):\n" + _SHARED + "\ndef t2(seq, factor):\n" + _SHARED,
            encoding="utf-8",
        )
        (transforms / "compact.py").write_text(
            "from sphinx.transforms import SphinxTransform\n\n"
            "def c1(seq, factor):\n" + _SHARED + "\ndef c2(seq, factor):\n" + _SHARED,
            encoding="utf-8",
        )

    def test_adopted_package_imports_without_cycle(self) -> None:
        cleaned = self.tmp / "sphinx-cleaned"  # the harness's sibling layout
        UnificationRefactorEngine(cross_module_helpers=True).refactor_directory_to_fixed_point(
            str(self.pkg), str(cleaned), max_iterations=0, progress="none"
        )
        shutil.rmtree(self.pkg)
        shutil.copytree(cleaned, self.pkg)
        init_source = (self.pkg / "transforms" / "__init__.py").read_text(encoding="utf-8")
        self.assertNotRegex(init_source, r"from \.compact import")
        result = subprocess.run(
            [sys.executable, "-c", "import sphinx.transforms.compact"],
            cwd=str(self.tmp),
            capture_output=True,
            text=True,
            env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": ""},
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertNotIn("circular import", result.stderr)
