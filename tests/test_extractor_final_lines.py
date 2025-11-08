import ast
import unittest

from towel.unification.extractor import (
    HygienicExtractor,
    contains_return,
)
from towel.unification.unifier import Substitution


class TestExtractorFinalLines(unittest.TestCase):
    """Target the last uncovered lines in `extractor.py` to push coverage to 100%."""

    def test_generate_call_hygienic_renames_fallback(self) -> None:
        """Line 199: fallback to substitution.hygienic_renames when argument missing."""
        extractor = HygienicExtractor()
        subst = Substitution()
        # Provide hygienic renames on the substitution but pass hygienic_renames=None
        subst.hygienic_renames = [{"original_x": "canon_x"}]
        # Simulate param ordering with a free variable that has been hygienically renamed
        param_order = {"canon_x": 0}
        free_variables = {"canon_x"}
        call_stmt = extractor.generate_call(
            function_name="extracted_function",
            block_idx=0,
            substitution=subst,
            param_order=param_order,
            free_variables=free_variables,
            is_value_producing=False,
            return_variables=None,
            hygienic_renames=None,  # triggers fallback path
        )
        # Argument should use original name after inverse mapping, proving fallback executed
        self.assertIsInstance(call_stmt, ast.Expr)
        self.assertIsInstance(call_stmt.value, ast.Call)
        self.assertEqual(len(call_stmt.value.args), 1)
        self.assertIsInstance(call_stmt.value.args[0], ast.Name)
        self.assertEqual(call_stmt.value.args[0].id, "original_x")

    def test_binding_occurrence_not_replaced(self) -> None:
        """Line 402: binding Name with Store ctx should not be substituted."""
        extractor = HygienicExtractor()
        subst = Substitution()
        # Map variable 'a' to a parameter; mapping uses Load context but unparse matches Store
        subst.add_mapping(0, ast.Name(id="a", ctx=ast.Load()), "__param_0")
        assign = ast.Assign(
            targets=[ast.Name(id="a", ctx=ast.Store())], value=ast.Constant(value=1)
        )
        # Wrap in Module + fix locations so ast.unparse inside substitution works
        mod = ast.Module(body=[assign], type_ignores=[])
        ast.fix_missing_locations(mod)
        template_block = [assign]
        replaced = extractor._substitute_parameters(
            template_block, subst, ["__param_0"], {"__param_0": "__param_0"}
        )
        self.assertEqual(len(replaced), 1)
        assign = replaced[0]
        self.assertIsInstance(assign, ast.Assign)
        # Target should remain 'a' (not replaced with '__param_0')
        self.assertIsInstance(assign.targets[0], ast.Name)
        self.assertEqual(assign.targets[0].id, "a")
        self.assertIsInstance(assign.targets[0].ctx, ast.Store)

    def test_skip_formatted_value_replacement(self) -> None:
        """Line 406: FormattedValue node itself is not replaced; its child is."""
        extractor = HygienicExtractor()
        subst = Substitution()
        # Use the exact FormattedValue node for the mapping so unparse matches
        name_node = ast.Name(id="v", ctx=ast.Load())
        formatted = ast.FormattedValue(value=name_node, conversion=-1, format_spec=None)
        # Add mappings for both the FormattedValue and the inner Name so the child replacement occurs
        subst.add_mapping(0, formatted, "__param_0")
        subst.add_mapping(0, name_node, "__param_0")

        joined = ast.JoinedStr(values=[formatted])
        # Fix missing locations to allow ast.unparse comparisons
        mod = ast.Module(body=[ast.Expr(value=joined)], type_ignores=[])
        ast.fix_missing_locations(mod)
        template_block = [ast.Expr(value=joined)]
        replaced = extractor._substitute_parameters(
            template_block, subst, ["__param_0"], {"__param_0": "__param_0"}
        )
        expr = replaced[0]
        self.assertIsInstance(expr, ast.Expr)
        self.assertIsInstance(expr.value, ast.JoinedStr)
        self.assertEqual(len(expr.value.values), 1)
        fv = expr.value.values[0]
        self.assertIsInstance(fv, ast.FormattedValue)
        # Child value should be replaced with param name
        self.assertIsInstance(fv.value, ast.Name)
        self.assertEqual(fv.value.id, "__param_0")

    def test_contains_return_async_function_def(self) -> None:
        """Line 472: visiting AsyncFunctionDef should not count as a return."""
        async_func = ast.AsyncFunctionDef(
            name="af",
            args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
            body=[ast.Pass()],
            decorator_list=[],
            returns=None,
        )
        block = [async_func]
        self.assertFalse(contains_return(block))


if __name__ == "__main__":  # pragma: no cover - allow direct invocation
    unittest.main()
