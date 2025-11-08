import ast
import unittest
import towel.unification.extractor  # explicit import for coverage collection

from towel.unification.extractor import HygienicExtractor
from towel.unification.unifier import Substitution


class TestExtractorPreambleAndCallMapping(unittest.TestCase):
    def test_preamble_injection_and_generate_call_mapping(self) -> None:
        # Template block contains a call where unified Name 'x' is used as callee:
        # res = x(1)
        # This should mark __param_0 as params_used_as_callee
        template_block_raw = [
            ast.Assign(
                targets=[ast.Name(id="res", ctx=ast.Store())],
                value=ast.Call(
                    func=ast.Name(id="x", ctx=ast.Load()),
                    args=[ast.Constant(value=1)],
                    keywords=[],
                ),
            )
        ]

        # Ensure nodes have lineno/col_offset for ast.unparse inside Substitution
        m = ast.Module(body=template_block_raw, type_ignores=[])
        ast.fix_missing_locations(m)
        template_block = m.body

        subst = Substitution()
        subst.add_mapping(0, ast.Name(id="x", ctx=ast.Load()), "__param_0")

        extractor = HygienicExtractor()
        # Include preamble declarations and multiple return vars
        func_def, param_order = extractor.extract_function(
            template_block=template_block,
            substitution=subst,
            free_variables={"fv"},
            enclosing_names=set(),
            is_value_producing=True,
            return_variables=["rv1", "rv2"],
            global_decls={"g2", "g1"},
            nonlocal_decls={"n"},
            function_name="extracted_function",
        )

        # Preamble Global/Nonlocal should be injected at top, sorted names
        self.assertIsInstance(func_def.body[0], ast.Global)
        self.assertEqual(func_def.body[0].names, ["g1", "g2"])
        self.assertIsInstance(func_def.body[1], ast.Nonlocal)
        self.assertEqual(func_def.body[1].names, ["n"])

        # Last statement should be a return of tuple (rv1, rv2)
        self.assertIsInstance(func_def.body[-1], ast.Return)
        ret = func_def.body[-1]
        self.assertIsInstance(ret.value, ast.Tuple)
        tuple_elts = ret.value.elts  # type: ignore[attr-defined]
        self.assertEqual([e.id for e in tuple_elts], ["rv1", "rv2"])  # type: ignore

        # The callee param should be recorded for call-site wrapping
        self.assertIn("__param_0", subst.params_used_as_callee)

        # Build substitution expressions for both blocks for the callee param
        # Block 0: x, Block 1: g
        subst.param_expressions["__param_0"] = [
            (0, ast.Name(id="x", ctx=ast.Load())),
            (1, ast.Name(id="g", ctx=ast.Load())),
        ]

        # Simulate hygienic renames and aug-assign mapping for free variable 'fv'
        # hygienic_renames[block_idx] maps original -> canonical
        hygienic_renames = [
            {"fv": "fv"},
            {"fv_orig": "fv", "res1": "rv1", "res2": "rv2"},
        ]
        subst.hygienic_renames = hygienic_renames

        # Force an augmented assignment rename override for block 1
        subst.aug_assign_mappings = {"fv": {1: "fv_aug"}}  # type: ignore[attr-defined]

        # Generate call for block 1 (index 1), value-producing with mapped return vars
        call_stmt = extractor.generate_call(
            function_name=func_def.name,
            block_idx=1,
            substitution=subst,
            param_order=param_order,
            free_variables={"fv"},
            is_value_producing=True,
            return_variables=["rv1", "rv2"],
            hygienic_renames=hygienic_renames,
        )

        # Expect an Assign to (res1, res2) = extracted_function(...)
        self.assertIsInstance(call_stmt, ast.Assign)
        target = call_stmt.targets[0]  # type: ignore[index]
        self.assertIsInstance(target, ast.Tuple)
        t_elts = target.elts  # type: ignore[attr-defined]
        self.assertEqual([e.id for e in t_elts], ["res1", "res2"])  # type: ignore

        # Call should be to the extracted function
        self.assertIsInstance(call_stmt.value, ast.Call)
        call = call_stmt.value  # type: ignore[assignment]
        self.assertIsInstance(call.func, ast.Name)
        self.assertEqual(call.func.id, func_def.name)

        # Args should include a lambda wrapping the callee param (g) and the free var name overridden by aug-assign mapping
        # Determine param ordering
        # First args correspond to unified params (['__param_0']) then free vars (['fv'])
        self.assertEqual(len(call.args), len(param_order))
        # Arg for __param_0 should be a lambda(*args, **kwargs): g(*args, **kwargs)
        callee_arg = call.args[list(param_order.keys()).index("__param_0")]
        self.assertIsInstance(callee_arg, ast.Lambda)
        lam = callee_arg  # type: ignore[assignment]
        # vararg and kwarg present
        self.assertIsNotNone(lam.args.vararg)
        self.assertIsNotNone(lam.args.kwarg)
        self.assertIsInstance(lam.body, ast.Call)
        self.assertIsInstance(lam.body.func, ast.Name)
        self.assertEqual(lam.body.func.id, "g")

        # Arg for free variable 'fv' should be Name('fv_aug') per mapping
        fv_idx = list(param_order.keys()).index("fv")
        fv_arg = call.args[fv_idx]
        self.assertIsInstance(fv_arg, ast.Name)
        self.assertEqual(fv_arg.id, "fv_aug")


if __name__ == "__main__":
    unittest.main()
