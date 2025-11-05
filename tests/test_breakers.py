import textwrap
import unittest
from pathlib import Path
import tempfile

from tests.automatic_equivalence_tester import AutomaticEquivalenceTester
from src.towel.unification.refactor_engine import UnificationRefactorEngine


class TestAdversarialBreakers(unittest.TestCase):
    def _write_temp_py(self, code: str) -> str:
        tmpdir = tempfile.TemporaryDirectory()
        path = Path(tmpdir.name) / "mod.py"
        path.write_text(textwrap.dedent(code).strip() + "\n")
        # Keep a reference so directory doesn't get GC'd
        self.addCleanup(lambda: tmpdir.cleanup())
        return str(path)

    def test_staticmethod_vs_instance_method_extraction_breaks(self):
        code = """
        class C:
            @staticmethod
            def fa(a, b):
                x = 1
                y = 2
                return x + y

            def fb(self, a, b):
                x = 1
                y = 2
                return x + y
        """
        path = self._write_temp_py(code)
        engine = UnificationRefactorEngine(
            max_parameters=5, min_lines=1, parameterize_constants=True
        )
        tester = AutomaticEquivalenceTester(engine)
        passed, failed, errors = tester.test_file(path)
        # Expect at least one refactoring proposal and no failures (bug fixed)
        self.assertGreater(passed + failed, 0, "Expected at least one proposal to be tested")
        self.assertEqual(
            failed, 0, f"Expected no failures for staticmethod handling, got: {errors}"
        )

    def test_classmethod_vs_instance_method_extraction_breaks(self):
        code = """
        class D:
            @classmethod
            def ga(cls, a, b):
                x = 1
                y = 2
                return x + y

            def gb(self, a, b):
                x = 1
                y = 2
                return x + y
        """
        path = self._write_temp_py(code)
        engine = UnificationRefactorEngine(
            max_parameters=5, min_lines=1, parameterize_constants=True
        )
        tester = AutomaticEquivalenceTester(engine)
        passed, failed, errors = tester.test_file(path)
        # Expect at least one proposal and no failures (bug fixed)
        self.assertGreater(passed + failed, 0, "Expected at least one proposal to be tested")
        self.assertEqual(failed, 0, f"Expected no failures for classmethod handling, got: {errors}")

    def test_staticmethod_vs_staticmethod_extraction_breaks(self):
        code = """
        class E:
            @staticmethod
            def a(x):
                y = x + 1
                z = y * 2
                return z

            @staticmethod
            def b(x):
                y = x + 1
                z = y * 2
                return z
        """
        path = self._write_temp_py(code)
        engine = UnificationRefactorEngine(
            max_parameters=5, min_lines=1, parameterize_constants=True
        )
        tester = AutomaticEquivalenceTester(engine)
        passed, failed, errors = tester.test_file(path)
        self.assertGreater(passed + failed, 0, "Expected at least one proposal to be tested")
        self.assertEqual(failed, 0, "Expected no failures for staticmethod→staticmethod extraction")

    def test_classmethod_vs_classmethod_extraction_breaks(self):
        code = """
        class F:
            @classmethod
            def a(cls, x):
                y = x + 1
                z = y * 2
                return z

            @classmethod
            def b(cls, x):
                y = x + 1
                z = y * 2
                return z
        """
        path = self._write_temp_py(code)
        engine = UnificationRefactorEngine(
            max_parameters=5, min_lines=1, parameterize_constants=True
        )
        tester = AutomaticEquivalenceTester(engine)
        passed, failed, errors = tester.test_file(path)
        self.assertGreater(passed + failed, 0, "Expected at least one proposal to be tested")
        self.assertEqual(failed, 0, "Expected no failures for classmethod→classmethod extraction")


if __name__ == "__main__":
    unittest.main()
