import ast
import unittest

from towel.unification.extractor import HygienicExtractor
from towel.unification.unifier import Substitution


def _fix(nodes):
    m = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(m)
    return m.body


class TestExtractorCalleeAndMultiReturn(unittest.TestCase):
    def test_params_used_as_callee_wrapping(self):
        # Block calls a unified parameter directly; should mark params_used_as_callee and wrap lambda on call generation
        # Template uses __param_0() and assigns its result
        block = [
            ast.Assign(
                targets=[ast.Name(id="res", ctx=ast.Store())],
                value=ast.Call(func=ast.Name(id="__param_0", ctx=ast.Load()), args=[], keywords=[]),
            ),
            ast.Return(value=ast.Name(id="res", ctx=ast.Load())),
        ]
        block = _fix(block)

        subst = Substitution()
        subst.param_expressions["__param_0"] = [
            (0, ast.Name(id="f", ctx=ast.Load())),
            (1, ast.Name(id="g", ctx=ast.Load())),
        ]

        extractor = HygienicExtractor()
        func_def, order = extractor.extract_function(
            template_block=block,
            substitution=subst,
            free_variables=set(),
            enclosing_names=set(),
            is_value_producing=True,
            return_variables=["res"],
            function_name="extracted_function",
        )
        # params_used_as_callee should be populated for __param_0
        self.assertIn("__param_0", subst.params_used_as_callee)

        call_stmt = extractor.generate_call(
            function_name=func_def.name,
            block_idx=0,
            substitution=subst,
            param_order=order,
            free_variables=set(),
            is_value_producing=True,
            return_variables=["res"],
            hygienic_renames=[{}, {}],
        )
        # Expect assignment with lambda wrapping forwarding args/kwargs
        self.assertIsInstance(call_stmt, ast.Assign)
        arg_expr = call_stmt.value.args[0]  # first argument passed to extracted function
        self.assertIsInstance(arg_expr, ast.Lambda)
        # Lambda should have vararg/kwarg and body making a call to original f
        self.assertIsNotNone(arg_expr.args.vararg)
        self.assertIsNotNone(arg_expr.args.kwarg)

    def test_multi_return_tuple_and_assignment(self):
        # Template returns two locals bound from parameters
        block = [
            ast.Assign(
                targets=[ast.Name(id="a", ctx=ast.Store())],
                value=ast.Name(id="__param_0", ctx=ast.Load()),
            ),
            ast.Assign(
                targets=[ast.Name(id="b", ctx=ast.Store())],
                value=ast.Name(id="__param_1", ctx=ast.Load()),
            ),
            ast.Return(
                value=ast.Tuple(
                    elts=[ast.Name(id="a", ctx=ast.Load()), ast.Name(id="b", ctx=ast.Load())],
                    ctx=ast.Load(),
                )
            ),
        ]
        block = _fix(block)
        subst = Substitution()
        subst.param_expressions["__param_0"] = [
            (0, ast.Name(id="x", ctx=ast.Load())),
            (1, ast.Name(id="y", ctx=ast.Load())),
        ]
        subst.param_expressions["__param_1"] = [
            (0, ast.Name(id="p", ctx=ast.Load())),
            (1, ast.Name(id="q", ctx=ast.Load())),
        ]

        extractor = HygienicExtractor()
        func_def, order = extractor.extract_function(
            template_block=block,
            substitution=subst,
            free_variables=set(),
            enclosing_names=set(),
            is_value_producing=True,
            return_variables=["a", "b"],
            function_name="extracted_function",
        )
        # Return should be a tuple in function body
        self.assertTrue(any(isinstance(s, ast.Return) for s in func_def.body))

        call_stmt = extractor.generate_call(
            function_name=func_def.name,
            block_idx=1,
            substitution=subst,
            param_order=order,
            free_variables=set(),
            is_value_producing=True,
            return_variables=["a", "b"],
            hygienic_renames=[{}, {}],
        )
        self.assertIsInstance(call_stmt, ast.Assign)
        target = call_stmt.targets[0]
        self.assertIsInstance(target, ast.Tuple)
        self.assertEqual(len(target.elts), 2)


if __name__ == "__main__":
    unittest.main()
