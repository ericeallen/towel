# Copyright 2025-2026 Eric Allen
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

import ast
import os
import tempfile
import unittest

from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.overlap import filter_overlapping_proposals
from towel.unification.models import RefactoringProposal, Replacement


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
import f1


def b(m, n):
    out = m
    out += n
    out += 0
    return out
""".lstrip(),
            )

            # The helper's import is under test; keep the extraction rather
            # than having ``b`` call ``a``.
            engine = UnificationRefactorEngine(
                min_lines=3, reuse_existing_functions=False, cross_module_helpers=True
            )
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
            self.assertEqual(
                [p.description for p in proposals], ["Extract common code from f and g"]
            )
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
            replacements=[Replacement(line_range=(10, 12), node=ast.Pass(), file_path=file_path)],
            description="small",
            parameters_count=0,
        )
        large = RefactoringProposal(
            file_path=file_path,
            extracted_function=dummy_func,
            replacements=[Replacement(line_range=(10, 15), node=ast.Pass(), file_path=file_path)],
            description="large",
            parameters_count=0,
        )

        selected = filter_overlapping_proposals([small, large])
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].description, "large")


if __name__ == "__main__":
    unittest.main()
