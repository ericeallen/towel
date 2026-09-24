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

from towel.unification.extractor import HygienicExtractor
from towel.unification.substitution import Substitution


class TestExtractorGenerateCallVariants(unittest.TestCase):
    def test_return_stmt_value_producing_without_return_vars(self):
        # Build a simple substitution/param_order and request a value-producing call
        subst = Substitution()
        # One unified param
        expr = ast.Name("x", ast.Load())
        subst.add_mapping(0, expr, "__param_0")
        # Provide expression for this block
        subst.param_expressions["__param_0"] = [(0, expr)]
        param_order = {"__param_0": 0}

        extractor = HygienicExtractor()
        call_stmt = extractor.generate_call(
            function_name="extracted_function",
            block_idx=0,
            substitution=subst,
            param_order=param_order,
            free_variables=set(),
            is_value_producing=True,  # no return_variables supplied
            return_variables=None,
            hygienic_renames=[{}],
        )
        assert isinstance(call_stmt, ast.Return)
        self.assertIsInstance(call_stmt.value, ast.Call)

    def test_expr_stmt_non_value_producing(self):
        subst = Substitution()
        expr = ast.Name("y", ast.Load())
        subst.add_mapping(0, expr, "__param_0")
        subst.param_expressions["__param_0"] = [(0, expr)]
        param_order = {"__param_0": 0}

        extractor = HygienicExtractor()
        call_stmt = extractor.generate_call(
            function_name="extracted_function",
            block_idx=0,
            substitution=subst,
            param_order=param_order,
            free_variables=set(),
            is_value_producing=False,
            hygienic_renames=[{}],
        )
        assert isinstance(call_stmt, ast.Expr)
        self.assertIsInstance(call_stmt.value, ast.Call)

    def test_function_param_lambda_wrapping_in_call(self):
        # Mark __param_0 as a function parameter that takes bound vars ['a','b']
        subst = Substitution()
        func_expr = ast.Name("f", ast.Load())
        subst.add_mapping(0, func_expr, "__param_0", bound_vars=["a", "b"])  # function param
        subst.param_expressions["__param_0"] = [(0, func_expr)]
        param_order = {"__param_0": 0, "fv": 1}

        # add free var for position 1
        extractor = HygienicExtractor()
        call_stmt = extractor.generate_call(
            function_name="extracted_function",
            block_idx=0,
            substitution=subst,
            param_order=param_order,
            free_variables={"fv"},
            is_value_producing=False,
            hygienic_renames=[{"fv": "fv"}],
        )
        assert isinstance(call_stmt, ast.Expr)
        call = call_stmt.value
        assert isinstance(call, ast.Call)
        self.assertEqual(len(call.args), 2)
        lam = call.args[0]
        assert isinstance(lam, ast.Lambda)
        self.assertEqual([a.arg for a in lam.args.args], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
