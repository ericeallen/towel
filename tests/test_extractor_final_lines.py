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
from types import SimpleNamespace
import unittest

from towel.unification.extractor import (
    HygienicExtractor,
    contains_return,
)
from towel.unification.substitution import Substitution


class TestExtractorFinalLines(unittest.TestCase):
    """Target the last uncovered lines in `extractor.py` to push coverage to 100%."""

    def test_augmented_attribute_and_subscript_targets_execute(self) -> None:
        template = ast.parse("obj.value += 2\ndata['count'] += 3\n").body
        function, _ = HygienicExtractor().extract_function(
            template_block=template,
            substitution=Substitution(),
            free_variables={"obj", "data"},
            enclosing_names=set(),
            is_value_producing=False,
        )
        module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
        namespace: dict[str, object] = {}
        exec(compile(module, "<augmented-target-regression>", "exec"), namespace)
        obj = SimpleNamespace(value=10)
        data = {"count": 20}
        extracted = namespace[function.name]
        assert callable(extracted)
        extracted(data, obj)
        self.assertEqual(obj.value, 12)
        self.assertEqual(data["count"], 23)

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
        assert isinstance(call_stmt, ast.Expr)
        assert isinstance(call_stmt.value, ast.Call)
        self.assertEqual(len(call_stmt.value.args), 1)
        argument = call_stmt.value.args[0]
        assert isinstance(argument, ast.Name)
        self.assertEqual(argument.id, "original_x")

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
        template_block: list[ast.stmt] = [assign]
        replaced = extractor._substitute_parameters(
            template_block, subst, ["__param_0"], {"__param_0": "__param_0"}
        )
        self.assertEqual(len(replaced), 1)
        replaced_assign = replaced[0]
        assert isinstance(replaced_assign, ast.Assign)
        # Target should remain 'a' (not replaced with '__param_0')
        target = replaced_assign.targets[0]
        assert isinstance(target, ast.Name)
        self.assertEqual(target.id, "a")
        self.assertIsInstance(target.ctx, ast.Store)

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
        template_block: list[ast.stmt] = [ast.Expr(value=joined)]
        replaced = extractor._substitute_parameters(
            template_block, subst, ["__param_0"], {"__param_0": "__param_0"}
        )
        expr = replaced[0]
        assert isinstance(expr, ast.Expr)
        assert isinstance(expr.value, ast.JoinedStr)
        self.assertEqual(len(expr.value.values), 1)
        fv = expr.value.values[0]
        assert isinstance(fv, ast.FormattedValue)
        # Child value should be replaced with param name
        assert isinstance(fv.value, ast.Name)
        self.assertEqual(fv.value.id, "__param_0")
        assignment = ast.Assign(targets=[ast.Name(id="result", ctx=ast.Store())], value=expr.value)
        module = ast.fix_missing_locations(ast.Module(body=[assignment], type_ignores=[]))
        namespace: dict[str, object] = {"__param_0": 42}
        exec(compile(module, "<formatted-value-regression>", "exec"), namespace)
        self.assertEqual(namespace["result"], "42")

    def test_substitution_distinguishes_formatted_value_from_fstring(self) -> None:
        formatted = ast.FormattedValue(
            value=ast.Name(id="value", ctx=ast.Load()), conversion=-1, format_spec=None
        )
        substitution = Substitution()
        substitution.add_mapping(0, formatted, "__param_0")
        self.assertIsNone(substitution.get_param_for_expr(0, ast.JoinedStr(values=[formatted])))
        # Structural matches remain valid after copying/reparsing, irrespective
        # of source coordinates attached to otherwise identical nodes.
        copy = ast.FormattedValue(
            value=ast.Name(id="value", ctx=ast.Load(), lineno=100, col_offset=4),
            conversion=-1,
            format_spec=None,
        )
        self.assertEqual(substitution.get_param_for_expr(0, copy), "__param_0")

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
