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
import unittest

from towel.unification.unifier import Unifier

NESTED_A = """
def a(matrix):
    result = [[cell * 2 for cell in row] for row in matrix]
    return result
"""

NESTED_B = """
def b(matrix):
    result = [[item * 2 for item in line] for line in matrix]
    return result
"""


class TestUnifierNestedComprehensions(unittest.TestCase):
    def _get_blocks(self, src: str):
        mod = ast.parse(src)
        func = next(n for n in mod.body if isinstance(n, ast.FunctionDef))
        # Extract the entire body (assign + return)
        return func.body

    def test_nested_list_comprehensions_unify(self):
        """Nested list comprehensions should unify via alpha-renaming of targets."""
        block_a = self._get_blocks(NESTED_A)
        block_b = self._get_blocks(NESTED_B)

        unifier = Unifier(max_parameters=5, parameterize_constants=True)
        hyg: list[dict[str, str]] = [{}, {}]
        subst = unifier.unify_blocks([block_a, block_b], hyg)

        # Should unify successfully with zero parameters (pure alpha-equivalence)
        self.assertIsNotNone(subst, "Nested comprehensions should unify")
        assert subst is not None
        self.assertEqual(
            len(subst.param_expressions), 0, "No parameters expected for alpha-equivalent code"
        )

    # Hygienic renames for bound comprehension vars are an internal detail and
    # may not be persisted; the critical property is that no parameters are needed.

    def test_unify_assign_only(self):
        """Even unifying just the assignment statements should succeed."""
        a_body = self._get_blocks(NESTED_A)
        b_body = self._get_blocks(NESTED_B)
        a_assign = [s for s in a_body if isinstance(s, ast.Assign)]
        b_assign = [s for s in b_body if isinstance(s, ast.Assign)]

        unifier = Unifier(max_parameters=5, parameterize_constants=True)
        hyg: list[dict[str, str]] = [{}, {}]
        subst = unifier.unify_blocks([a_assign, b_assign], hyg)

        self.assertIsNotNone(subst)
        assert subst is not None
        self.assertEqual(len(subst.param_expressions), 0)


if __name__ == "__main__":
    unittest.main()
