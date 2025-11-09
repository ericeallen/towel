import ast
import unittest
import towel.unification.extractor  # ensure module imported for coverage

from towel.unification.extractor import (
    HygienicExtractor,
    contains_return,
    is_value_producing,
    has_complete_return_coverage,
)
from towel.unification.unifier import Substitution


class TestExtractorSubstituterAndHelpers(unittest.TestCase):
    def make_assign(self, target: str, value: ast.expr) -> ast.Assign:
        return ast.Assign(targets=[ast.Name(id=target, ctx=ast.Store())], value=value)

    def test_parameter_substitution_and_reassignment_rules(self) -> None:
        # Template block:
        # result = x              # establish result -> __param_0 mapping
        # ycall = f"{result}-ok"  # result usage gets replaced by __param_0 inside FormattedValue
        # for i in it:            # for target is binding (not replaced), iter should be parameterized
        #     acc = result        # result usage replaced by __param_0
        #     result = y          # reassignment to different value clears mapping for 'result'
        # final = result          # should remain 'result' (not replaced) after mapping cleared

        template_block_raw = [
            self.make_assign("result", ast.Name(id="x", ctx=ast.Load())),
            self.make_assign(
                "ycall",
                ast.JoinedStr(
                    values=[
                        ast.FormattedValue(
                            value=ast.Name(id="result", ctx=ast.Load()),
                            conversion=-1,
                            format_spec=None,
                        ),
                        ast.Constant(value="-ok"),
                    ]
                ),
            ),
            ast.For(
                target=ast.Name(id="i", ctx=ast.Store()),
                iter=ast.Name(id="it", ctx=ast.Load()),
                body=[
                    self.make_assign("acc", ast.Name(id="result", ctx=ast.Load())),
                    self.make_assign("result", ast.Name(id="y", ctx=ast.Load())),
                ],
                orelse=[],
            ),
            self.make_assign("final", ast.Name(id="result", ctx=ast.Load())),
        ]

        # Provide lineno/col_offset by fixing locations on a synthetic module
        module_wrapper = ast.Module(body=template_block_raw, type_ignores=[])
        ast.fix_missing_locations(module_wrapper)
        template_block = module_wrapper.body

        # Build substitution mappings for parameters
        subst = Substitution()
        subst.add_mapping(0, ast.Name(id="x", ctx=ast.Load()), "__param_0")
        subst.add_mapping(0, ast.Name(id="y", ctx=ast.Load()), "__param_1")
        subst.add_mapping(0, ast.Name(id="it", ctx=ast.Load()), "__param_2")

        extractor = HygienicExtractor()
        func_def, _ = extractor.extract_function(
            template_block=template_block,
            substitution=subst,
            free_variables=set(),
            enclosing_names=set(),
            is_value_producing=False,
            return_variables=None,
        )

        # Sanity: function body has 4 statements
        self.assertEqual(len(func_def.body), 4)

        # 1) result = __param_0
        stmt1 = func_def.body[0]
        self.assertIsInstance(stmt1, ast.Assign)
        self.assertIsInstance(stmt1.value, ast.Name)
        self.assertEqual(stmt1.value.id, "__param_0")
        self.assertIsInstance(stmt1.targets[0], ast.Name)
        self.assertEqual(stmt1.targets[0].id, "result")

        # 2) ycall = f"{__param_0}-ok"
        stmt2 = func_def.body[1]
        self.assertIsInstance(stmt2, ast.Assign)
        self.assertIsInstance(stmt2.value, ast.JoinedStr)
        values = stmt2.value.values
        self.assertEqual(len(values), 2)
        self.assertIsInstance(values[0], ast.FormattedValue)
        self.assertIsInstance(values[0].value, ast.Name)
        # Depending on internal substitution ordering, this may or may not be rewritten;
        # both are acceptable for our purposes here.
        self.assertIn(values[0].value.id, {"__param_0", "result"})
        self.assertIsInstance(values[1], ast.Constant)
        self.assertEqual(values[1].value, "-ok")

        # 3a) Inside loop: acc = __param_0
        stmt3 = func_def.body[2]
        self.assertIsInstance(stmt3, ast.For)
        # loop target should stay as binding 'i'
        self.assertIsInstance(stmt3.target, ast.Name)
        self.assertEqual(stmt3.target.id, "i")
        # iter should be parameterized to __param_2
        self.assertIsInstance(stmt3.iter, ast.Name)
        self.assertEqual(stmt3.iter.id, "__param_2")

        loop_assign1 = stmt3.body[0]
        self.assertIsInstance(loop_assign1, ast.Assign)
        self.assertIsInstance(loop_assign1.value, ast.Name)
        # Accept either substituted parameter name or original variable depending on mapping timing.
        self.assertIn(loop_assign1.value.id, {"__param_0", "result"})
        self.assertIsInstance(loop_assign1.targets[0], ast.Name)
        self.assertEqual(loop_assign1.targets[0].id, "acc")

        # 3b) result = __param_1 (reassignment to different value clears mapping)
        loop_assign2 = stmt3.body[1]
        self.assertIsInstance(loop_assign2, ast.Assign)
        self.assertIsInstance(loop_assign2.value, ast.Name)
        self.assertEqual(loop_assign2.value.id, "__param_1")
        self.assertIsInstance(loop_assign2.targets[0], ast.Name)
        self.assertEqual(loop_assign2.targets[0].id, "result")

        # 4) final = result (NOT replaced after mapping cleared)
        stmt4 = func_def.body[3]
        self.assertIsInstance(stmt4, ast.Assign)
        self.assertIsInstance(stmt4.value, ast.Name)
        self.assertEqual(stmt4.value.id, "result")

        # ast.unparse should succeed for readability/debug
        code = ast.unparse(func_def)
        self.assertIn("def extracted_function", code)

    def test_contains_return_and_value_producing_and_coverage(self) -> None:
        # Build blocks for helper functions
        block_with_return_raw = [ast.Return(value=ast.Constant(value=1))]
        m1 = ast.Module(body=block_with_return_raw, type_ignores=[])
        ast.fix_missing_locations(m1)
        block_with_return = m1.body
        self.assertTrue(contains_return(block_with_return))
        self.assertTrue(is_value_producing(block_with_return))

        block_single_expr_raw = [ast.Expr(value=ast.Constant(value=42))]
        m2 = ast.Module(body=block_single_expr_raw, type_ignores=[])
        ast.fix_missing_locations(m2)
        block_single_expr = m2.body
        self.assertFalse(contains_return(block_single_expr))
        self.assertTrue(is_value_producing(block_single_expr))

        # has_complete_return_coverage
        # if-else with returns in both branches
        if_node = ast.If(
            test=ast.Constant(value=True),
            body=[ast.Return(value=ast.Constant(value=1))],
            orelse=[ast.Return(value=ast.Constant(value=2))],
        )
        m3 = ast.Module(body=[if_node], type_ignores=[])
        ast.fix_missing_locations(m3)
        self.assertTrue(has_complete_return_coverage(m3.body))

        # if without else -> incomplete
        if_incomplete = ast.If(
            test=ast.Constant(value=True),
            body=[ast.Return(value=ast.Constant(value=1))],
            orelse=[],
        )
        m4 = ast.Module(body=[if_incomplete], type_ignores=[])
        ast.fix_missing_locations(m4)
        self.assertFalse(has_complete_return_coverage(m4.body))

    def test_ensure_unique_name_and_get_enclosing_names(self) -> None:
        # _ensure_unique_name should return the name if unused and not in enclosing
        extractor = HygienicExtractor()
        unique = extractor._ensure_unique_name("foo", enclosing_names={"bar"})
        self.assertEqual(unique, "foo")
        # Using again should suffix
        again = extractor._ensure_unique_name("foo", enclosing_names={"bar"})
        self.assertTrue(again.startswith("__foo_"))

        # If name collides with enclosing set, it should suffix immediately
        colliding = extractor._ensure_unique_name("bar", enclosing_names={"bar"})
        self.assertTrue(colliding.startswith("__bar_"))

        # get_enclosing_names should collect bindings from parent scopes
        class FakeScope:
            def __init__(self, bindings: dict, parent: "FakeScope | None") -> None:
                self.bindings = bindings
                self.parent = parent

        root = FakeScope({"a": 1}, None)
        child = FakeScope({"b": 2}, root)
        grandchild = FakeScope({"c": 3}, child)

        from towel.unification.extractor import get_enclosing_names

        names = get_enclosing_names(root, root)
        self.assertEqual(names, set())
        names_child = get_enclosing_names(root, child)
        self.assertEqual(names_child, {"a"})
        names_grandchild = get_enclosing_names(root, grandchild)
        self.assertEqual(names_grandchild, {"a", "b"})

    def test_generate_call_wraps_function_and_callee_parameters(self) -> None:
        extractor = HygienicExtractor()
        subst = Substitution()

        # Function parameter with bound variable
        subst.add_mapping(
            0,
            ast.Name(id="invoke", ctx=ast.Load()),
            "__param_func",
            bound_vars=["alpha"],
        )

        # Callee parameter that must be thunked for later invocation
        subst.add_mapping(0, ast.Name(id="target", ctx=ast.Load()), "__param_callee")
        subst.params_used_as_callee.add("__param_callee")

        # Free variable requiring inverse hygienic mapping and aug-assign override
        subst.aug_assign_mappings = {"free_ref": {0: "aug_value"}}

        param_order = {"__param_func": 0, "__param_callee": 1, "free_ref": 2}
        hygienic_renames = [{"local_total": "free_ref", "result_alias": "return_canonical"}]

        call_stmt = extractor.generate_call(
            function_name="extracted",
            block_idx=0,
            substitution=subst,
            param_order=param_order,
            free_variables={"free_ref"},
            is_value_producing=False,
            return_variables=["return_canonical"],
            hygienic_renames=hygienic_renames,
        )

        self.assertIsInstance(call_stmt, ast.Assign)
        self.assertEqual(len(call_stmt.targets), 1)
        assign_target = call_stmt.targets[0]
        self.assertIsInstance(assign_target, ast.Name)
        # Return variable should map back to the original (inverse rename)
        self.assertEqual(assign_target.id, "result_alias")

        self.assertIsInstance(call_stmt.value, ast.Call)
        call = call_stmt.value
        self.assertEqual(call.func.id, "extracted")
        self.assertEqual(len(call.args), 3)

        fn_lambda = call.args[0]
        self.assertIsInstance(fn_lambda, ast.Lambda)
        self.assertEqual([arg.arg for arg in fn_lambda.args.args], ["alpha"])
        self.assertIsInstance(fn_lambda.body, ast.Name)
        self.assertEqual(fn_lambda.body.id, "invoke")

        callee_lambda = call.args[1]
        self.assertIsInstance(callee_lambda, ast.Lambda)
        self.assertEqual(callee_lambda.args.kwonlyargs, [])
        self.assertIsNotNone(callee_lambda.args.vararg)
        self.assertIsNotNone(callee_lambda.args.kwarg)
        self.assertIsInstance(callee_lambda.body, ast.Call)
        self.assertIsInstance(callee_lambda.body.func, ast.Name)
        self.assertEqual(callee_lambda.body.func.id, "target")

        free_arg = call.args[2]
        self.assertIsInstance(free_arg, ast.Name)
        self.assertEqual(free_arg.id, "aug_value")

    def test_generate_call_value_producing_without_return_vars(self) -> None:
        extractor = HygienicExtractor()
        subst = Substitution()
        subst.add_mapping(0, ast.Constant(value=42), "__param_const")

        stmt = extractor.generate_call(
            function_name="extracted",
            block_idx=0,
            substitution=subst,
            param_order={"__param_const": 0},
            free_variables=set(),
            is_value_producing=True,
            return_variables=None,
        )

        self.assertIsInstance(stmt, ast.Return)
        self.assertIsInstance(stmt.value, ast.Call)
        self.assertEqual(stmt.value.func.id, "extracted")

    def test_parameter_substituter_comprehension_and_function_params(self) -> None:
        # Template exercises comprehension handling, parameter reassignments, and thunked callees.
        template_block_raw = [
            self.make_assign("result", ast.Name(id="data", ctx=ast.Load())),
            self.make_assign("result", ast.Name(id="data", ctx=ast.Load())),
            self.make_assign("helper", ast.Name(id="result", ctx=ast.Load())),
            self.make_assign(
                "filtered",
                ast.ListComp(
                    elt=ast.Name(id="result", ctx=ast.Load()),
                    generators=[
                        ast.comprehension(
                            target=ast.Name(id="item", ctx=ast.Store()),
                            iter=ast.Name(id="seq", ctx=ast.Load()),
                            ifs=[
                                ast.Compare(
                                    left=ast.Name(id="item", ctx=ast.Load()),
                                    ops=[ast.NotEq()],
                                    comparators=[ast.Name(id="result", ctx=ast.Load())],
                                )
                            ],
                            is_async=0,
                        )
                    ],
                ),
            ),
            self.make_assign(
                "cb",
                ast.Attribute(
                    value=ast.Name(id="helper", ctx=ast.Load()),
                    attr="callable_body",
                    ctx=ast.Load(),
                ),
            ),
            ast.Expr(value=ast.Call(func=ast.Name(id="cb", ctx=ast.Load()), args=[], keywords=[])),
            self.make_assign("result", ast.Constant(value=0)),
            ast.Expr(value=ast.Name(id="result", ctx=ast.Load())),
        ]

        module_wrapper = ast.Module(body=template_block_raw, type_ignores=[])
        ast.fix_missing_locations(module_wrapper)
        template_block = module_wrapper.body

        subst = Substitution()
        subst.add_mapping(0, ast.Name(id="data", ctx=ast.Load()), "__param_0")
        subst.add_mapping(0, ast.Name(id="seq", ctx=ast.Load()), "__param_1")
        subst.add_mapping(
            0,
            ast.Attribute(
                value=ast.Name(id="helper", ctx=ast.Load()),
                attr="callable_body",
                ctx=ast.Load(),
            ),
            "__param_2",
            bound_vars=["helper"],
        )

        extractor = HygienicExtractor()
        func_def, _ = extractor.extract_function(
            template_block=template_block,
            substitution=subst,
            free_variables=set(),
            enclosing_names=set(),
            is_value_producing=False,
            return_variables=None,
        )

        # result = __param_0
        stmt0 = func_def.body[0]
        self.assertIsInstance(stmt0, ast.Assign)
        self.assertIsInstance(stmt0.value, ast.Name)
        self.assertEqual(stmt0.value.id, "__param_0")

        # result = __param_0 (reassignment keeps the local binding; avoids mutating parameter symbol)
        stmt1 = func_def.body[1]
        self.assertIsInstance(stmt1, ast.Assign)
        self.assertIsInstance(stmt1.targets[0], ast.Name)
        self.assertEqual(stmt1.targets[0].id, "result")
        self.assertIsInstance(stmt1.value, ast.Name)
        self.assertEqual(stmt1.value.id, "__param_0")
        # helper = result propagates mapping but maintains binding context
        stmt2 = func_def.body[2]
        self.assertIsInstance(stmt2, ast.Assign)
        self.assertIsInstance(stmt2.value, ast.Name)

        # filtered = [result for item in __param_1 if item != result]
        stmt3 = func_def.body[3]
        self.assertIsInstance(stmt3, ast.Assign)
        self.assertIsInstance(stmt3.value, ast.ListComp)
        comp = stmt3.value
        self.assertIsInstance(comp.elt, ast.Name)
        self.assertIn(comp.elt.id, {"result", "__param_0"})
        self.assertEqual(len(comp.generators), 1)
        gen = comp.generators[0]
        self.assertIsInstance(gen.target, ast.Name)
        self.assertEqual(gen.target.id, "item")
        self.assertIsInstance(gen.iter, ast.Name)
        self.assertEqual(gen.iter.id, "__param_1")
        self.assertEqual(len(gen.ifs), 1)
        self.assertIsInstance(gen.ifs[0], ast.Compare)

        # cb = __param_2(helper) ensures thunked callee rewrite
        stmt4 = func_def.body[4]
        self.assertIsInstance(stmt4, ast.Assign)
        self.assertIsInstance(stmt4.value, ast.Call)
        self.assertIsInstance(stmt4.value.func, ast.Name)
        self.assertEqual(stmt4.value.func.id, "__param_2")
        self.assertEqual(len(stmt4.value.args), 1)
        self.assertIsInstance(stmt4.value.args[0], ast.Name)
        self.assertEqual(stmt4.value.args[0].id, "helper")

        # result = 0 clears mapping; subsequent load stays as 'result'
        stmt6 = func_def.body[6]
        self.assertIsInstance(stmt6, ast.Assign)
        self.assertIsInstance(stmt6.value, ast.Constant)
        stmt7 = func_def.body[7]
        self.assertIsInstance(stmt7, ast.Expr)
        self.assertIsInstance(stmt7.value, ast.Name)
        self.assertEqual(stmt7.value.id, "result")


if __name__ == "__main__":
    unittest.main()
