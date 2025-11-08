import ast
import unittest

from towel.unification.extractor import HygienicExtractor
from towel.unification.unifier import Substitution


class TestExtractorMorePaths(unittest.TestCase):
    def test_generate_call_variants(self):
        # Template block: simple assign then return
        block = [
            ast.Assign(
                targets=[ast.Name(id="a", ctx=ast.Store())],
                value=ast.Name(id="__param_0", ctx=ast.Load()),
            ),
            ast.Return(value=ast.Name(id="a", ctx=ast.Load())),
        ]

        # Populate missing lineno/col_offset so unparse works
        mod = ast.Module(body=block, type_ignores=[])
        ast.fix_missing_locations(mod)
        block = mod.body

        subst = Substitution()
        # Map an expression for block 0 and 1 to __param_0
        subst.param_expressions["__param_0"] = [
            (0, ast.Name(id="x", ctx=ast.Load())),
            (1, ast.Name(id="y", ctx=ast.Load())),
        ]

        extractor = HygienicExtractor()
        func_def, order = extractor.extract_function(
            template_block=block,
            substitution=subst,
            free_variables=set(),
            enclosing_names=set(),
            is_value_producing=True,
            return_variables=["a"],
            function_name="extracted_function",
        )

        # Single return variable assignment
        call_assign = extractor.generate_call(
            function_name=func_def.name,
            block_idx=0,
            substitution=subst,
            param_order=order,
            free_variables=set(),
            is_value_producing=True,
            return_variables=["a"],
            hygienic_renames=[{}, {}],
        )
        self.assertIsInstance(call_assign, ast.Assign)

        # Multiple return variables tuple assignment
        call_tuple = extractor.generate_call(
            function_name=func_def.name,
            block_idx=1,
            substitution=subst,
            param_order=order,
            free_variables=set(),
            is_value_producing=True,
            return_variables=["a", "b"],
            hygienic_renames=[{}, {}],
        )
        self.assertIsInstance(call_tuple, ast.Assign)
        self.assertIsInstance(call_tuple.targets[0], ast.Tuple)

        # Value-producing without explicit return variables -> Return
        call_return = extractor.generate_call(
            function_name=func_def.name,
            block_idx=0,
            substitution=subst,
            param_order=order,
            free_variables=set(),
            is_value_producing=True,
            return_variables=[],
            hygienic_renames=[{}, {}],
        )
        self.assertIsInstance(call_return, ast.Return)

        # Non-value-producing -> Expr
        call_expr = extractor.generate_call(
            function_name=func_def.name,
            block_idx=0,
            substitution=subst,
            param_order=order,
            free_variables=set(),
            is_value_producing=False,
            return_variables=[],
            hygienic_renames=[{}, {}],
        )
        self.assertIsInstance(call_expr, ast.Expr)

    def test_preamble_and_unique_name(self):
        block = [ast.Pass()]
        mod = ast.Module(body=block, type_ignores=[])
        ast.fix_missing_locations(mod)
        block = mod.body
        subst = Substitution()
        extractor = HygienicExtractor()

        func_def, _ = extractor.extract_function(
            template_block=block,
            substitution=subst,
            free_variables=set(),
            enclosing_names={"extracted_function"},  # force collision
            is_value_producing=False,
            function_name="extracted_function",
            global_decls={"G"},
            nonlocal_decls={"N"},
        )

        # First statements should include Global/Nonlocal
        self.assertTrue(any(isinstance(s, ast.Global) for s in func_def.body[:2]))
        self.assertTrue(any(isinstance(s, ast.Nonlocal) for s in func_def.body[:2]))
        # Name should have been uniquified
        self.assertNotEqual(func_def.name, "extracted_function")
        self.assertTrue(func_def.name.startswith("__extracted_function_"))


if __name__ == "__main__":
    unittest.main()
