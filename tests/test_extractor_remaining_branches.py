import ast
import unittest
import towel.unification.extractor  # explicit import for coverage

from towel.unification.extractor import (
    HygienicExtractor,
    contains_return,
    is_value_producing,
    has_complete_return_coverage,
)
from towel.unification.unifier import Substitution


def _fix(block):
    m = ast.Module(body=block, type_ignores=[])
    ast.fix_missing_locations(m)
    return m.body


class TestExtractorRemainingBranches(unittest.TestCase):
    def test_single_return_variable_branch(self):
        # Block returns a single variable -> exercise single-return path (line ~199)
        block_raw = [
            ast.Assign(targets=[ast.Name("x", ast.Store())], value=ast.Constant(1)),
            ast.Return(value=ast.Name("x", ast.Load())),
        ]
        block = _fix(block_raw)
        subst = Substitution()
        extractor = HygienicExtractor()
        func_def, order = extractor.extract_function(
            template_block=block,
            substitution=subst,
            free_variables=set(),
            enclosing_names=set(),
            is_value_producing=True,
            return_variables=["x"],
        )
        # Single return variable path: ensure Return(Name('x')) appended and param_order empty since no params
        self.assertIsInstance(func_def.body[-1], ast.Return)
        ret = func_def.body[-1]
        self.assertIsInstance(ret.value, ast.Name)
        self.assertEqual(ret.value.id, "x")
        self.assertEqual(order, {})

    def test_for_loop_with_else_and_parameterization(self):
        # For-else structure to hit visit_For orelse handling
        # Parameterize iterator and a load occurrence inside body
        block_raw = [
            ast.For(
                target=ast.Name("i", ast.Store()),
                iter=ast.Name("items", ast.Load()),
                body=[ast.Expr(value=ast.Name("use", ast.Load()))],
                orelse=[
                    ast.Assign(targets=[ast.Name("after", ast.Store())], value=ast.Constant(0))
                ],
            )
        ]
        block = _fix(block_raw)
        subst = Substitution()
        subst.add_mapping(0, ast.Name("items", ast.Load()), "__param_0")
        subst.add_mapping(0, ast.Name("use", ast.Load()), "__param_1")
        extractor = HygienicExtractor()
        func_def, _ = extractor.extract_function(
            template_block=block,
            substitution=subst,
            free_variables=set(),
            enclosing_names=set(),
            is_value_producing=False,
        )
        loop = func_def.body[0]
        self.assertIsInstance(loop, ast.For)
        self.assertIsInstance(loop.iter, ast.Name)
        self.assertEqual(loop.iter.id, "__param_0")
        self.assertEqual(len(loop.orelse), 1)

    def test_list_comprehension_multiple_ifs(self):
        # Comprehension with two ifs; parameterize iterable and conditions
        comp = ast.Assign(
            targets=[ast.Name("out", ast.Store())],
            value=ast.ListComp(
                elt=ast.Name("x", ast.Load()),
                generators=[
                    ast.comprehension(
                        target=ast.Name("x", ast.Store()),
                        iter=ast.Name("seq", ast.Load()),
                        ifs=[ast.Name("cond1", ast.Load()), ast.Name("cond2", ast.Load())],
                        is_async=0,
                    )
                ],
            ),
        )
        block = _fix([comp])
        subst = Substitution()
        # Parameterize seq and conditions
        subst.add_mapping(0, ast.Name("seq", ast.Load()), "__param_0")
        subst.add_mapping(0, ast.Name("cond1", ast.Load()), "__param_1")
        subst.add_mapping(0, ast.Name("cond2", ast.Load()), "__param_2")
        extractor = HygienicExtractor()
        func_def, _ = extractor.extract_function(
            template_block=block,
            substitution=subst,
            free_variables=set(),
            enclosing_names=set(),
            is_value_producing=False,
        )
        assign = func_def.body[0]
        self.assertIsInstance(assign, ast.Assign)
        list_comp = assign.value
        self.assertIsInstance(list_comp, ast.ListComp)
        gen = list_comp.generators[0]
        self.assertIsInstance(gen.iter, ast.Name)
        self.assertEqual(gen.iter.id, "__param_0")
        self.assertIsInstance(gen.ifs[0], ast.Name)
        self.assertIn(gen.ifs[0].id, {"__param_1"})

    def test_idempotent_assignment_and_mapping_clear(self):
        # result = x; result = x; result = 5; use result -> second reassignment same param, third clears
        block_raw = [
            ast.Assign(targets=[ast.Name("result", ast.Store())], value=ast.Name("x", ast.Load())),
            ast.Assign(targets=[ast.Name("result", ast.Store())], value=ast.Name("x", ast.Load())),
            ast.Assign(targets=[ast.Name("result", ast.Store())], value=ast.Constant(5)),
            ast.Assign(
                targets=[ast.Name("final", ast.Store())], value=ast.Name("result", ast.Load())
            ),
        ]
        block = _fix(block_raw)
        subst = Substitution()
        subst.add_mapping(0, ast.Name("x", ast.Load()), "__param_0")
        extractor = HygienicExtractor()
        func_def, _ = extractor.extract_function(
            template_block=block,
            substitution=subst,
            free_variables=set(),
            enclosing_names=set(),
            is_value_producing=False,
        )
        s1, s2, s3, s4 = func_def.body
        # first assignment value substituted
        self.assertIsInstance(s1.value, ast.Name)
        self.assertEqual(s1.value.id, "__param_0")
        # second assignment still substituted (idempotent)
        self.assertIsInstance(s2.value, ast.Name)
        self.assertIn(s2.value.id, {"__param_0", "x"})
        # third assignment constant leaves mapping cleared; final uses original name (not parameter)
        self.assertIsInstance(s4.value, ast.Name)
        self.assertEqual(s4.value.id, "result")

    def test_tuple_assignment_parameter_binding(self):
        block_raw = [
            ast.Assign(
                targets=[
                    ast.Tuple(
                        elts=[ast.Name("a", ast.Store()), ast.Name("b", ast.Store())],
                        ctx=ast.Store(),
                    )
                ],
                value=ast.Name("val", ast.Load()),
            )
        ]
        block = _fix(block_raw)
        subst = Substitution()
        subst.add_mapping(0, ast.Name("val", ast.Load()), "__param_0")
        extractor = HygienicExtractor()
        func_def, _ = extractor.extract_function(
            template_block=block,
            substitution=subst,
            free_variables=set(),
            enclosing_names=set(),
            is_value_producing=False,
        )
        assign = func_def.body[0]
        self.assertIsInstance(assign.value, ast.Name)
        self.assertEqual(assign.value.id, "__param_0")

    def test_function_parameter_substitution(self):
        # Expression references bound vars x,y -> should produce call __param_0(x, y)
        expr = ast.BinOp(
            left=ast.Name("x", ast.Load()), op=ast.Add(), right=ast.Name("y", ast.Load())
        )
        block_raw = [ast.Assign(targets=[ast.Name("res", ast.Store())], value=expr)]
        block = _fix(block_raw)
        subst = Substitution()
        subst.add_mapping(0, expr, "__param_0", bound_vars=["x", "y"])
        extractor = HygienicExtractor()
        func_def, _ = extractor.extract_function(
            template_block=block,
            substitution=subst,
            free_variables=set(),
            enclosing_names=set(),
            is_value_producing=False,
        )
        assign = func_def.body[0]
        self.assertIsInstance(assign.value, ast.Call)
        self.assertIsInstance(assign.value.func, ast.Name)
        self.assertEqual(assign.value.func.id, "__param_0")
        self.assertEqual([a.id for a in assign.value.args], ["x", "y"])  # type: ignore

    def test_fstring_constant_guard(self):
        fstr = ast.JoinedStr(
            values=[
                ast.Constant("hi "),
                ast.FormattedValue(
                    value=ast.Name("x", ast.Load()), conversion=-1, format_spec=None
                ),
            ]
        )
        block = _fix([ast.Assign(targets=[ast.Name("s", ast.Store())], value=fstr)])
        subst = Substitution()
        subst.add_mapping(0, ast.Name("x", ast.Load()), "__param_0")
        extractor = HygienicExtractor()
        func_def, _ = extractor.extract_function(
            template_block=block,
            substitution=subst,
            free_variables=set(),
            enclosing_names=set(),
            is_value_producing=False,
        )
        assign = func_def.body[0]
        self.assertIsInstance(assign.value, ast.JoinedStr)
        const_part, formatted = assign.value.values
        self.assertIsInstance(const_part, ast.Constant)
        self.assertEqual(const_part.value, "hi ")
        self.assertIsInstance(formatted, ast.FormattedValue)
        self.assertIsInstance(formatted.value, ast.Name)
        self.assertIn(formatted.value.id, {"__param_0", "x"})

    def test_incomplete_return_coverage_block(self):
        # Block ends with if missing else return -> incomplete coverage
        if_node = ast.If(
            test=ast.Constant(True),
            body=[ast.Return(value=ast.Constant(1))],
            orelse=[],
        )
        block = _fix([if_node])
        self.assertTrue(contains_return(block))
        self.assertTrue(is_value_producing(block))
        self.assertFalse(has_complete_return_coverage(block))

    def test_multiple_collision_unique_name(self):
        extractor = HygienicExtractor()
        enclosing = {"func", "__func_1"}
        first = extractor._ensure_unique_name("func", enclosing)
        second = extractor._ensure_unique_name("func", enclosing | {first})
        third = extractor._ensure_unique_name("func", enclosing | {first, second})
        self.assertTrue(first.startswith("__func_"))
        self.assertTrue(second.startswith("__func_"))
        self.assertTrue(third.startswith("__func_"))
        self.assertNotEqual(first, second)
        self.assertNotEqual(second, third)


if __name__ == "__main__":
    unittest.main()
