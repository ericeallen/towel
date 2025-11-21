import ast
import io
import os
import tempfile
import unittest

from towel.unification.refactor_engine import (
    UnificationRefactorEngine,
    RefactoringProposal,
    filter_overlapping_proposals,
)


def write_file(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


class TestRefactorEngineMore(unittest.TestCase):
    def test_cross_file_import_insertion_and_replacement(self):
        with tempfile.TemporaryDirectory() as td:
            f1 = os.path.join(td, "f1.py")
            f2 = os.path.join(td, "f2.py")

            # Two functions with unifiable 4-line blocks
            write_file(
                f1,
                """
def a(x, y):
    r = x
    r += y
    r += 0
    return r
""".lstrip(),
            )

            write_file(
                f2,
                """
def b(m, n):
    out = m
    out += n
    out += 0
    return out
""".lstrip(),
            )

            engine = UnificationRefactorEngine(min_lines=3)
            proposals = engine.analyze_directory(td, recursive=False)
            self.assertTrue(proposals, "Expected at least one proposal")

            mod_files = engine.apply_refactoring_multi_file(proposals[0])
            # f2 should have an import line for the extracted function from f1's stem
            f2_content = mod_files[f2]
            self.assertIn("from f1 import", f2_content)
            # Replacement in f2 should contain a return calling extracted function
            self.assertIn("return __extracted_func_", f2_content)

    def test_same_file_deepest_common_insert_into_function(self):
        with tempfile.TemporaryDirectory() as td:
            fn = os.path.join(td, "one.py")
            write_file(
                fn,
                """
def outer():
    def f(x, y):
        r = x
        r += y
        return r

    def g(a, b):
        out = a
        out += b
        return out
""".lstrip(),
            )

            engine = UnificationRefactorEngine(min_lines=3)
            proposals = engine.analyze_file(fn)
            self.assertTrue(proposals)
            modified = engine.apply_refactoring_multi_file(proposals[0])[fn]
            # Helper should be inserted inside outer(), look for indentation before def name
            lines = modified.splitlines()
            joined = "\n".join(lines)
            self.assertIn("    def __extracted_func_", joined)

    def test_filter_overlapping_keeps_largest(self):
        # Build two dummy proposals overlapping on same file
        dummy_func = ast.FunctionDef(
            name="__extracted_func_0",
            args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
            body=[ast.Pass()],
            decorator_list=[],
            returns=None,
        )
        file_path = os.path.join(os.getcwd(), "dummy.py")
        small = RefactoringProposal(
            file_path=file_path,
            extracted_function=dummy_func,
            replacements=[((10, 12), ast.Pass(), file_path)],
            description="small",
            parameters_count=0,
        )
        large = RefactoringProposal(
            file_path=file_path,
            extracted_function=dummy_func,
            replacements=[((10, 15), ast.Pass(), file_path)],
            description="large",
            parameters_count=0,
        )

        selected = filter_overlapping_proposals([small, large])
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].description, "large")


if __name__ == "__main__":
    unittest.main()
