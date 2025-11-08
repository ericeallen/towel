import ast
import unittest

from towel.unification.unifier import Unifier


def _ret(node: ast.expr) -> ast.stmt:
    return ast.Return(value=node)


class TestUnifierMoreBranches(unittest.TestCase):
    def test_lambda_vararg_rejected(self):
        # Two blocks returning lambdas with varargs should fail unification via _unify_lambda
        lam1 = ast.Lambda(
            args=ast.arguments(
                posonlyargs=[],
                args=[],
                vararg=ast.arg(arg="args"),
                kwonlyargs=[],
                kw_defaults=[],
                kwarg=None,
                defaults=[],
            ),
            body=ast.Name(id="x", ctx=ast.Load()),
        )
        lam2 = ast.Lambda(
            args=ast.arguments(
                posonlyargs=[],
                args=[],
                vararg=ast.arg(arg="args"),
                kwonlyargs=[],
                kw_defaults=[],
                kwarg=None,
                defaults=[],
            ),
            body=ast.Name(id="y", ctx=ast.Load()),
        )
        blocks = [[_ret(lam1)], [_ret(lam2)]]
        u = Unifier()
        res = u.unify_blocks(blocks, [{}, {}])
        self.assertIsNone(res)

    def test_with_type_comment_mismatch(self):
        # With statements with differing type_comment should fail in _unify_with
        item1 = ast.withitem(context_expr=ast.Name(id="f", ctx=ast.Load()), optional_vars=None)
        with1 = ast.With(items=[item1], body=[ast.Pass()], type_comment="tc1")
        item2 = ast.withitem(context_expr=ast.Name(id="f", ctx=ast.Load()), optional_vars=None)
        with2 = ast.With(items=[item2], body=[ast.Pass()], type_comment="tc2")
        # Fix locations
        for w in (with1, with2):
            m = ast.Module(body=[w], type_ignores=[])
            ast.fix_missing_locations(m)
        blocks = [[with1], [with2]]
        u = Unifier()
        res = u.unify_blocks(blocks, [{}, {}])
        self.assertIsNone(res)

    def test_except_handler_alpha_and_name_none_mismatch(self):
        # Success case: names differ but alpha-renamed; same exception type
        try1 = ast.Try(
            body=[ast.Pass()],
            handlers=[
                ast.ExceptHandler(
                    type=ast.Name(id="Exception", ctx=ast.Load()), name="e", body=[ast.Pass()]
                )
            ],
            orelse=[],
            finalbody=[],
        )
        try2 = ast.Try(
            body=[ast.Pass()],
            handlers=[
                ast.ExceptHandler(
                    type=ast.Name(id="Exception", ctx=ast.Load()), name="err", body=[ast.Pass()]
                )
            ],
            orelse=[],
            finalbody=[],
        )
        for t in (try1, try2):
            m = ast.Module(body=[t], type_ignores=[])
            ast.fix_missing_locations(m)
        u = Unifier()
        res_ok = u.unify_blocks([[try1], [try2]], [{}, {}])
        self.assertIsNotNone(res_ok)

        # Mismatch: one has name, other is None
        try3 = ast.Try(
            body=[ast.Pass()],
            handlers=[
                ast.ExceptHandler(
                    type=ast.Name(id="Exception", ctx=ast.Load()),
                    name=None,
                    body=[ast.Pass()],
                )
            ],
            orelse=[],
            finalbody=[],
        )
        m = ast.Module(body=[try3], type_ignores=[])
        ast.fix_missing_locations(m)
        res_bad = u.unify_blocks([[try1], [try3]], [{}, {}])
        self.assertIsNone(res_bad)

    def test_joinedstr_component_type_mismatch(self):
        # At position 0, one has Constant, the other has FormattedValue -> _unify_joined_str returns False
        js1 = ast.JoinedStr(
            values=[
                ast.Constant(value="A"),
                ast.FormattedValue(
                    value=ast.Name(id="x", ctx=ast.Load()), conversion=-1, format_spec=None
                ),
            ]
        )
        js2 = ast.JoinedStr(
            values=[
                ast.FormattedValue(
                    value=ast.Name(id="x", ctx=ast.Load()), conversion=-1, format_spec=None
                ),
                ast.Constant(value="A"),
            ]
        )
        u = Unifier()
        e1 = ast.Expr(value=js1)
        e2 = ast.Expr(value=js2)
        for e in (e1, e2):
            m = ast.Module(body=[e], type_ignores=[])
            ast.fix_missing_locations(m)
        res = u.unify_blocks([[e1], [e2]], [{}, {}])
        self.assertIsNone(res)

    def test_try_parameterize_statement_nodes_rejected(self):
        # Top-level statement type mismatch triggers _try_parameterize with statements -> returns False
        r = ast.Return(value=ast.Constant(value=1))
        e = ast.Expr(value=ast.Constant(value=1))
        for n in (r, e):
            m = ast.Module(body=[n], type_ignores=[])
            ast.fix_missing_locations(m)
        blk1 = [r]
        blk2 = [e]
        u = Unifier()
        res = u.unify_blocks([blk1, blk2], [{}, {}])
        self.assertIsNone(res)

    def test_parameterize_max_params_exceeded(self):
        # Force parameterization but with max_parameters=0 to hit limit rejection
        r1 = ast.Return(value=ast.Name(id="a", ctx=ast.Load()))
        r2 = ast.Return(value=ast.Name(id="b", ctx=ast.Load()))
        for n in (r1, r2):
            m = ast.Module(body=[n], type_ignores=[])
            ast.fix_missing_locations(m)
        blk1 = [r1]
        blk2 = [r2]
        u = Unifier(max_parameters=0)
        res = u.unify_blocks([blk1, blk2], [{}, {}])
        self.assertIsNone(res)

    def test_constant_consistency_counts_and_alignment(self):
        # Different counts for value 3 vs 2 cause inconsistency -> reject
        blk_a = [
            ast.Assign(targets=[ast.Name(id="x", ctx=ast.Store())], value=ast.Constant(value=2)),
            ast.Assign(targets=[ast.Name(id="y", ctx=ast.Store())], value=ast.Constant(value=2)),
        ]
        blk_b = [
            ast.Assign(targets=[ast.Name(id="x", ctx=ast.Store())], value=ast.Constant(value=3)),
            ast.Assign(targets=[ast.Name(id="y", ctx=ast.Store())], value=ast.Constant(value=2)),
        ]
        for seq in (blk_a, blk_b):
            m = ast.Module(body=list(seq), type_ignores=[])
            ast.fix_missing_locations(m)
        u = Unifier(parameterize_constants=True)
        res1 = u.unify_blocks([blk_a, blk_b], [{}, {}])
        self.assertIsNone(res1)

        # Alignment case: both positions differ consistently -> allow parameterization
        blk_c = [
            ast.Assign(targets=[ast.Name(id="x", ctx=ast.Store())], value=ast.Constant(value=2)),
            ast.Assign(targets=[ast.Name(id="y", ctx=ast.Store())], value=ast.Constant(value=2)),
        ]
        blk_d = [
            ast.Assign(targets=[ast.Name(id="x", ctx=ast.Store())], value=ast.Constant(value=3)),
            ast.Assign(targets=[ast.Name(id="y", ctx=ast.Store())], value=ast.Constant(value=3)),
        ]
        for seq in (blk_c, blk_d):
            m = ast.Module(body=list(seq), type_ignores=[])
            ast.fix_missing_locations(m)
        res2 = u.unify_blocks([blk_c, blk_d], [{}, {}])
        # Should either unify with a substitution or at least not crash
        self.assertIsNotNone(res2)

    def test_try_parameterize_joinedstr_guard(self):
        # Field type mismatch with JoinedStr should hit _try_parameterize and be rejected by the guard
        js = ast.JoinedStr(values=[ast.Constant(value="A")])
        name = ast.Name(id="x", ctx=ast.Load())
        e1 = ast.Expr(value=js)
        e2 = ast.Expr(value=name)
        for n in (e1, e2):
            m = ast.Module(body=[n], type_ignores=[])
            ast.fix_missing_locations(m)
        blk1 = [e1]
        blk2 = [e2]
        u = Unifier()
        res = u.unify_blocks([blk1, blk2], [{}, {}])
        self.assertIsNone(res)


if __name__ == "__main__":
    unittest.main()
