#!/usr/bin/env python3
"""
Targeted tests to cover additional branches in HygienicExtractor.

Focus on:
- Injected global/nonlocal preamble in extract_function
- generate_call branches for function parameters and callee-parameter thunking
"""

import unittest
import ast

from src.towel.unification.extractor import HygienicExtractor
from src.towel.unification.unifier import Substitution


class TestExtractorInjectedPreamble(unittest.TestCase):
    def setUp(self) -> None:
        self.extractor = HygienicExtractor()

    def test_injected_global_and_nonlocal(self) -> None:
        # Minimal body; we only care about preamble injection order and nodes
        code = "x = 1\nreturn x"
        tree = ast.parse(code)

        subst = Substitution()
        func_def, _ = self.extractor.extract_function(
            template_block=tree.body,
            substitution=subst,
            free_variables=set(),
            enclosing_names=set(),
            is_value_producing=True,
            global_decls={"G1", "G2"},
            nonlocal_decls={"n1"},
            function_name="extracted",
        )

        # First statements must be Global then Nonlocal with sorted names
        self.assertGreaterEqual(len(func_def.body), 3)
        self.assertIsInstance(func_def.body[0], ast.Global)
        self.assertEqual(func_def.body[0].names, ["G1", "G2"])  # sorted order
        self.assertIsInstance(func_def.body[1], ast.Nonlocal)
        self.assertEqual(func_def.body[1].names, ["n1"])  # sorted order


class TestExtractorGenerateCallSpecialParams(unittest.TestCase):
    def setUp(self) -> None:
        self.extractor = HygienicExtractor()

    def test_function_parameter_wrapping_lambda(self) -> None:
        # Create a substitution where __param_0 is a function parameter taking bound vars ["a", "b"]
        subst = Substitution()
        param = "__param_0"
        # Expression used for this param in block 0 is a simple name 'fn'
        expr = ast.Name(id="fn", ctx=ast.Load())
        subst.param_expressions[param] = [(0, expr)]
        subst.function_params[param] = ["a", "b"]

        call_stmt = self.extractor.generate_call(
            function_name="extracted",
            block_idx=0,
            substitution=subst,
            param_order={param: 0},
            free_variables=set(),
            is_value_producing=False,
        )

        self.assertIsInstance(call_stmt, ast.Expr)
        self.assertIsInstance(call_stmt.value, ast.Call)
        self.assertEqual(len(call_stmt.value.args), 1)
        lam = call_stmt.value.args[0]
        self.assertIsInstance(lam, ast.Lambda)
        # Check lambda args are the bound vars in order
        arg_names = [a.arg for a in lam.args.args]
        self.assertEqual(arg_names, ["a", "b"])

    def test_callee_parameter_thunking(self) -> None:
        # Create a substitution where __param_0 is used as a callee inside the extracted body
        subst = Substitution()
        param = "__param_0"
        expr = ast.Name(id="callee", ctx=ast.Load())
        subst.param_expressions[param] = [(0, expr)]
        # Mark that this param is used as a callee in the body; should be wrapped in forwarding lambda
        subst.params_used_as_callee.add(param)

        call_stmt = self.extractor.generate_call(
            function_name="extracted",
            block_idx=0,
            substitution=subst,
            param_order={param: 0},
            free_variables=set(),
            is_value_producing=False,
        )

        self.assertIsInstance(call_stmt, ast.Expr)
        self.assertIsInstance(call_stmt.value, ast.Call)
        self.assertEqual(len(call_stmt.value.args), 1)
        lam = call_stmt.value.args[0]
        self.assertIsInstance(lam, ast.Lambda)
        # Check vararg/kwarg forwarding signature
        self.assertIsNotNone(lam.args.vararg)
        self.assertEqual(lam.args.vararg.arg, "args")
        self.assertIsNotNone(lam.args.kwarg)
        self.assertEqual(lam.args.kwarg.arg, "kwargs")
        # Body should call the original callee with *args, **kwargs
        self.assertIsInstance(lam.body, ast.Call)
        self.assertEqual(len(lam.body.args), 1)
        self.assertIsInstance(lam.body.args[0], ast.Starred)
        self.assertEqual(len(lam.body.keywords), 1)
        self.assertIsNone(lam.body.keywords[0].arg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
