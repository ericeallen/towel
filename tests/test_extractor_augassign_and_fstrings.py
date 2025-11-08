import ast
import unittest

from towel.unification.extractor import HygienicExtractor
from towel.unification.unifier import Substitution


def _fix(nodes):
    m = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(m)
    return m.body


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
        block = _fix(block)

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
        arg0 = call0.value.args[order["acc"]]
        arg1 = call1.value.args[order["acc"]]
        self.assertIsInstance(arg0, ast.Name)
        self.assertIsInstance(arg1, ast.Name)
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
        block = [ast.Expr(value=fstr)]
        block = _fix(block)

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
