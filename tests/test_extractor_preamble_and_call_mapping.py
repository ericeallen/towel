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
from typing import Sequence

from towel.unification.extractor import HygienicExtractor
from towel.unification.substitution import Substitution


def _name_ids(nodes: Sequence[ast.expr]) -> list[str]:
    ids: list[str] = []
    for node in nodes:
        assert isinstance(node, ast.Name)
        ids.append(node.id)
    return ids


class TestExtractorPreambleAndCallMapping(unittest.TestCase):
    def test_preamble_injection_and_generate_call_mapping(self) -> None:
        # Template block contains a call where unified Name 'x' is used as callee:
        # res = x(1)
        # This should mark __param_0 as params_used_as_callee
        template_block_raw: list[ast.stmt] = [
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
        global_decl = func_def.body[0]
        assert isinstance(global_decl, ast.Global)
        self.assertEqual(global_decl.names, ["g1", "g2"])
        nonlocal_decl = func_def.body[1]
        assert isinstance(nonlocal_decl, ast.Nonlocal)
        self.assertEqual(nonlocal_decl.names, ["n"])

        # Last statement should be a return of tuple (rv1, rv2)
        ret = func_def.body[-1]
        assert isinstance(ret, ast.Return)
        assert isinstance(ret.value, ast.Tuple)
        self.assertEqual(_name_ids(ret.value.elts), ["rv1", "rv2"])

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
        subst.aug_assign_mappings = {"fv": {1: "fv_aug"}}

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
        assert isinstance(call_stmt, ast.Assign)
        target = call_stmt.targets[0]
        assert isinstance(target, ast.Tuple)
        self.assertEqual(_name_ids(target.elts), ["res1", "res2"])

        # Call should be to the extracted function
        assert isinstance(call_stmt.value, ast.Call)
        call = call_stmt.value
        assert isinstance(call.func, ast.Name)
        self.assertEqual(call.func.id, func_def.name)

        # Args should include a lambda wrapping the callee param (g) and the free var name overridden by aug-assign mapping
        # Determine param ordering
        # First args correspond to unified params (['__param_0']) then free vars (['fv'])
        self.assertEqual(len(call.args), len(param_order))
        # Arg for __param_0 should be a lambda(*args, **kwargs): g(*args, **kwargs)
        callee_arg = call.args[list(param_order.keys()).index("__param_0")]
        assert isinstance(callee_arg, ast.Lambda)
        lam = callee_arg
        # vararg and kwarg present
        self.assertIsNotNone(lam.args.vararg)
        self.assertIsNotNone(lam.args.kwarg)
        assert isinstance(lam.body, ast.Call)
        assert isinstance(lam.body.func, ast.Name)
        self.assertEqual(lam.body.func.id, "g")

        # Arg for free variable 'fv' should be Name('fv_aug') per mapping
        fv_idx = list(param_order.keys()).index("fv")
        fv_arg = call.args[fv_idx]
        assert isinstance(fv_arg, ast.Name)
        self.assertEqual(fv_arg.id, "fv_aug")


if __name__ == "__main__":
    unittest.main()
