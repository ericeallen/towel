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

from tests.test_helpers import fix_locations
from towel.unification.extractor import HygienicExtractor
from towel.unification.substitution import Substitution


class TestExtractorAugAssignAndFStrings(unittest.TestCase):
    def test_aug_assign_param_removed_and_mapped(self):
        # Template has augmented assignment to a variable that was parameterized by refactor_engine; extractor should treat it as free var and map per block
        # Simulate refactor_engine having removed the parameter and stored aug_assign_mappings
        block = [
            ast.AugAssign(
                target=ast.Name(id="acc", ctx=ast.Store()),
                op=ast.Add(),
                value=ast.Constant(value=1),
            ),
            ast.Return(value=ast.Name(id="acc", ctx=ast.Load())),
        ]
        block = fix_locations(block)

        subst = Substitution()
        # Suppose it was originally parameterized as __param_9 mapping to acc in block 0 and total in block 1, but removed.
        subst.aug_assign_mappings = {"acc": {0: "acc", 1: "total"}}

        extractor = HygienicExtractor()
        func_def, order = extractor.extract_function(
            template_block=block,
            substitution=subst,
            free_variables={"acc"},  # acc treated as free var param
            enclosing_names=set(),
            is_value_producing=True,
            return_variables=["acc"],
            function_name="extracted_function",
        )
        # The param order should include the free variable 'acc'
        self.assertIn("acc", order)

        # Generate call for block 0 (acc) and block 1 (total) using mapping
        call0 = extractor.generate_call(
            function_name=func_def.name,
            block_idx=0,
            substitution=subst,
            param_order=order,
            free_variables={"acc"},
            is_value_producing=True,
            return_variables=["acc"],
            hygienic_renames=[{}, {}],
        )
        call1 = extractor.generate_call(
            function_name=func_def.name,
            block_idx=1,
            substitution=subst,
            param_order=order,
            free_variables={"acc"},
            is_value_producing=True,
            return_variables=["acc"],
            hygienic_renames=[{}, {}],
        )
        # First call should pass Name('acc'), second should pass Name('total') per mapping
        assert isinstance(call0, ast.Assign) and isinstance(call0.value, ast.Call)
        assert isinstance(call1, ast.Assign) and isinstance(call1.value, ast.Call)
        arg0 = call0.value.args[order["acc"]]
        arg1 = call1.value.args[order["acc"]]
        self.assertIsInstance(arg0, ast.Name)
        self.assertIsInstance(arg1, ast.Name)
        assert isinstance(arg0, ast.Name) and isinstance(arg1, ast.Name)
        self.assertEqual(arg0.id, "acc")
        self.assertEqual(arg1.id, "total")

    def test_fstring_parameter_guard(self):
        # If a unified parameter corresponds to a JoinedStr in template, extractor should not try to replace the entire f-string
        # Here we just ensure substitute pass-through for f-string parts and FormattedValue children are visitable
        fstr = ast.JoinedStr(
            values=[
                ast.Constant(value="Hello "),
                ast.FormattedValue(
                    value=ast.Name(id="name", ctx=ast.Load()), conversion=-1, format_spec=None
                ),
            ]
        )
        block = fix_locations([ast.Expr(value=fstr)])

        subst = Substitution()
        # Map a different expression for block 1 to force parameterization attempt, but since it's a JoinedStr, extractor should not break it
        subst.param_expressions["__param_0"] = [
            (0, fstr),
            (1, ast.JoinedStr(values=[ast.Constant(value="Hi ")])),
        ]

        extractor = HygienicExtractor()
        # Even with this setup, the _substitute_parameters' JoinedStr handler should leave constants untouched
        func_def, _ = extractor.extract_function(
            template_block=block,
            substitution=subst,
            free_variables=set(),
            enclosing_names=set(),
            is_value_producing=False,
            function_name="extracted_function",
        )
        # Ensure the body still contains a JoinedStr and Constant child
        self.assertTrue(
            any(
                isinstance(n, ast.Expr) and isinstance(n.value, ast.JoinedStr)
                for n in func_def.body
            )
        )


if __name__ == "__main__":
    unittest.main()
