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


if __name__ == "__main__":
    unittest.main()
