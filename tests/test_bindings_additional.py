"""
Additional binding construct tests:

- with-as bindings should be treated as alpha-equivalent (not parameters)
- except-as bindings should be treated as alpha-equivalent (not parameters)
- walrus (NamedExpr) target names should be treated as bindings (alpha-equivalent)

These tests create temporary modules to avoid modifying fixtures.
"""

import textwrap
import unittest
from pathlib import Path
import tempfile

from tests.automatic_equivalence_tester import AutomaticEquivalenceTester
from towel.unification.refactor_engine import UnificationRefactorEngine


class TestAdditionalBindings(unittest.TestCase):
    def _write_temp_py(self, code: str) -> str:
        tmpdir = tempfile.TemporaryDirectory()
        path = Path(tmpdir.name) / "mod.py"
        path.write_text(textwrap.dedent(code).strip() + "\n")
        # Keep a reference so directory doesn't get GC'd
        self.addCleanup(lambda: tmpdir.cleanup())
        return str(path)

    def test_with_as_binding_alpha_equivalent(self):
        code = """
        def fa(path):
            with open(path) as f:
                data = f.read()
            return len(data)

        def fb(path):
            with open(path) as fh:
                data = fh.read()
            return len(data)
        """
        path = self._write_temp_py(code)
        engine = UnificationRefactorEngine(
            max_parameters=5, min_lines=1, parameterize_constants=True
        )
        tester = AutomaticEquivalenceTester(engine)
        passed, failed, errors = tester.test_file(path)
        self.assertGreater(passed + failed, 0, "Expected at least one proposal to be tested")
        self.assertEqual(failed, 0, f"with-as binding should not break equivalence: {errors}")

    def test_except_as_binding_alpha_equivalent(self):
        code = """
        def ga(x):
            try:
                return 10 // x
            except Exception as e:
                return str(e)

        def gb(x):
            try:
                return 10 // x
            except Exception as err:
                return str(err)
        """
        path = self._write_temp_py(code)
        engine = UnificationRefactorEngine(
            max_parameters=5, min_lines=1, parameterize_constants=True
        )
        tester = AutomaticEquivalenceTester(engine)
        passed, failed, errors = tester.test_file(path)
        self.assertGreater(passed + failed, 0, "Expected at least one proposal to be tested")
        self.assertEqual(failed, 0, f"except-as binding should not break equivalence: {errors}")

    def test_walrus_binding_alpha_equivalent(self):
        code = """
        def xa(items):
            if (n := len(items)) > 1:
                return n
            return 0

        def xb(items):
            if (k := len(items)) > 1:
                return k
            return 0
        """
        path = self._write_temp_py(code)
        engine = UnificationRefactorEngine(
            max_parameters=5, min_lines=1, parameterize_constants=True
        )
        tester = AutomaticEquivalenceTester(engine)
        passed, failed, errors = tester.test_file(path)
        self.assertGreater(passed + failed, 0, "Expected at least one proposal to be tested")
        self.assertEqual(failed, 0, f"walrus target should be treated as binding: {errors}")


if __name__ == "__main__":
    unittest.main()
