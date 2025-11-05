import ast
import textwrap
import unittest

from src.towel.unification.binding_detector import (
    BindingDetector,
    BindingKind,
    detect_bindings,
    get_bindings_by_kind,
    get_bound_variables,
)


def _parse(code: str) -> ast.AST:
    return ast.parse(textwrap.dedent(code).strip())


class TestBindingDetectorEdgeCases(unittest.TestCase):
    def test_assignments_and_targets(self) -> None:
        code = """
        x = 1
        a, b = (2, 3)
        [c, d] = [3, 4]
        e, *rest = [1, 2, 3]
        x.y = 5
        arr[0] = 7
        z: int = 1
        w: int
        n = (m := 10)
        """
        tree = _parse(code)

        # Overall bound names
        names = get_bound_variables(tree)
        self.assertTrue({"x", "a", "b", "c", "d", "e", "rest", "z", "m"}.issubset(names))
        self.assertNotIn("w", names, "Annotated name without value should not bind")

        # Kinds
        assign = {b.name for b in get_bindings_by_kind(tree, BindingKind.ASSIGNMENT)}
        self.assertTrue({"x", "a", "b", "c", "d", "e", "rest", "z"}.issubset(assign))

        aug = {b.name for b in get_bindings_by_kind(_parse("x = 0\nx += 1"), BindingKind.AUG_ASSIGNMENT)}
        self.assertEqual(aug, {"x"})

        named = {b.name for b in get_bindings_by_kind(tree, BindingKind.NAMED_EXPR)}
        self.assertEqual(named, {"m"})

        # Ensure attribute/subscript did not create bindings
        self.assertNotIn("y", names)
        self.assertNotIn("arr", names)

    def test_loops_and_comprehensions(self) -> None:
        code = """
        for i, j in xs:
            pass

        ys = [i + j for i, j in xs]
        zs = {k: v for (k, v) in pairs}
        ws = (q for q in xs)
        """
        tree = _parse(code)

        for_loop = {b.name for b in get_bindings_by_kind(tree, BindingKind.FOR_LOOP)}
        self.assertEqual(for_loop, {"i", "j"})

        comp = {b.name for b in get_bindings_by_kind(tree, BindingKind.COMPREHENSION)}
        self.assertTrue({"i", "j", "k", "v", "q"}.issubset(comp))

    def test_exceptions_with_with_and_imports(self) -> None:
        code = """
        try:
            1/0
        except ZeroDivisionError as exc:
            pass

        with open("a") as f, open("b") as g:
            pass

        import os as o, sys
        from math import sin as s, cos, tan as t
        from math import *
        """
        tree = _parse(code)

        exc = {b.name for b in get_bindings_by_kind(tree, BindingKind.EXCEPTION)}
        self.assertEqual(exc, {"exc"})

        with_vars = {b.name for b in get_bindings_by_kind(tree, BindingKind.WITH_STMT)}
        self.assertEqual(with_vars, {"f", "g"})

        imports = {b.name for b in get_bindings_by_kind(tree, BindingKind.IMPORT)}
        # Should bind alias names and bare imports; ignore wildcard
        self.assertTrue({"o", "sys", "s", "cos", "t"}.issubset(imports))

    def test_functions_lambdas_async_and_class(self) -> None:
        code = """
        @dec
        def foo(a, /, b, *args, c, **kwargs):
            x_inner = 1
            return a + b

        async def bar(x):
            return x

        f = lambda x, /, y, *, z, **kw: (x, y, z, kw)

        class C:
            pass
        """
        tree = _parse(code)
        bindings = detect_bindings(tree)

        # Top-level function/class names bound in enclosing (module) scope
        top_defs = {b.name for b in bindings if b.kind in {BindingKind.FUNCTION_DEF, BindingKind.CLASS_DEF} and b.scope_node is None}
        self.assertTrue({"foo", "bar", "C"}.issubset(top_defs))

        # Collect specific scope nodes
        func_node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "foo")
        async_node = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "bar")
        lambda_node = next(n for n in ast.walk(tree) if isinstance(n, ast.Lambda))

        # Params within function scope
        foo_params = get_bound_variables(tree, scope_node=func_node)
        self.assertTrue({"a", "b", "args", "c", "kwargs"}.issubset(foo_params))

        bar_params = get_bound_variables(tree, scope_node=async_node)
        self.assertEqual(bar_params, {"x"})

        lambda_params = get_bound_variables(tree, scope_node=lambda_node)
        self.assertTrue({"x", "y", "z", "kw"}.issubset(lambda_params))

    def test_match_statement_bindings(self) -> None:
        code = """
        def matchy(value):
            match value:
                case [x, y] | {"a": b, **rest}:
                    pass
                case Point(px, py):
                    pass
                case [*tail]:
                    pass
                case [u] as whole:
                    pass
                case _:
                    pass
        """
        tree = _parse(code)

        # Limit to function scope to avoid collecting any top-level names
        func_node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "matchy")
        names_in_func = get_bound_variables(tree, scope_node=func_node)
        self.assertTrue({"x", "y", "b", "rest", "px", "py", "tail", "u", "whole"}.issubset(names_in_func))

    def test_exotic_match_and_line_numbers_and_scopes(self) -> None:
        code = """
        def outer(a, b):
            # line 2
            match a:
                case (A(val) | B(val)) as either:
                    pass
                case [x, y, *rest, z]:
                    pass
                case Point(x=px, y=py):
                    pass
                case 0 | 1:
                    pass
                case True:
                    pass
            lam = (lambda u, /, v, *, w: (u, v, w))
            return a, b, lam
        """
        tree = _parse(code)

        # Collect bindings
        detector = BindingDetector()
        detector.visit(tree)
        bindings = detector.bindings

        # Function def name bound at module scope (None)
        fn_name = next(b for b in bindings if b.kind == BindingKind.FUNCTION_DEF and b.name == "outer")
        self.assertIsNone(fn_name.scope_node)
        self.assertGreaterEqual(fn_name.line_number, 1)

        # Function params have scope = function node; check scope line matches function line
        func_node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "outer")
        func_line = func_node.lineno
        params = [b for b in bindings if b.kind == BindingKind.FUNCTION_PARAM and b.scope_node is func_node]
        self.assertTrue({"a", "b"}.issubset({p.name for p in params}))
        for p in params:
            self.assertIs(p.scope_node, func_node)
            self.assertEqual(p.scope_node.lineno, func_line)
            # Binding line should match its AST node
            self.assertEqual(p.line_number, getattr(p.node, "lineno", -1))

        # MatchOr + MatchAs bind 'val' and 'either'
        names_in_func = get_bound_variables(tree, scope_node=func_node)
        self.assertTrue({"val", "either", "x", "y", "rest", "z", "px", "py"}.issubset(names_in_func))

        # Lambda scope correctness and line numbers
        lam_node = next(n for n in ast.walk(tree) if isinstance(n, ast.Lambda))
        lam_params = [b for b in bindings if b.scope_node is lam_node and b.kind == BindingKind.FUNCTION_PARAM]
        self.assertEqual({p.name for p in lam_params}, {"u", "v", "w"})
        for lp in lam_params:
            self.assertEqual(lp.scope_node.lineno, lam_node.lineno)
            self.assertEqual(lp.line_number, getattr(lp.node, "lineno", -1))


if __name__ == "__main__":
    unittest.main()
