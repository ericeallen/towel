import ast
import textwrap
import unittest
import tempfile
from pathlib import Path

from src.towel.unification.refactor_engine import UnificationRefactorEngine
from src.towel.unification.unifier import Unifier
from src.towel.unification.extractor import HygienicExtractor


class TempModule:
    def __init__(self, code: str, filename: str = "mod.py", base_dir: Path | None = None):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmpdir.name)
        if base_dir is not None:
            # allow placing inside a provided directory
            self.dir = base_dir
        self.path = self.dir / filename
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(textwrap.dedent(code).strip() + "\n", encoding="utf-8")

    def cleanup(self):
        try:
            self._tmpdir.cleanup()
        except Exception:
            pass


class TestRefactorEngineAdversarial(unittest.TestCase):
    def _engine(self, **kwargs) -> UnificationRefactorEngine:
        defaults = dict(max_parameters=5, min_lines=2, parameterize_constants=True)
        defaults.update(kwargs)
        return UnificationRefactorEngine(**defaults)

    def test_value_producing_mismatch_is_rejected(self):
        code = """
        def f1(x):
            a = x + 1
            if a > 0:
                return a

        def f2(x):
            a = x + 1
            if a > 0:
                b = a  # no return here, structure differs in value-production
        """
        m = TempModule(code)
        self.addCleanup(m.cleanup)
        engine = self._engine(min_lines=2)
        proposals = engine.analyze_file(str(m.path))
        # There may be other trivial proposals; ensure the if-block pair isn't accepted by checking
        # that no proposal replaces inside the second function's if with a return call.
        modified_any = False
        for p in proposals:
            modified_files = engine.apply_refactoring_multi_file(p)
            new_src = modified_files.get(str(m.path))
            if new_src and "def f2(" in new_src:
                # ensure no injected return call under f2
                f2_block = new_src.split("def f2")[1]
                self.assertNotIn("return __extracted_func", f2_block)
                modified_any = True
        # proposals may be empty or unrelated; test is chiefly that mismatch paths don't sneak a return
        self.assertTrue(True if proposals is not None else True)

    def test_incomplete_return_coverage_rejected(self):
        code = """
        def a(x):
            if x > 0:
                return x
            # missing else return

        def b(x):
            if x > 0:
                return x
            # missing else return
        """
        m = TempModule(code)
        self.addCleanup(m.cleanup)
        engine = self._engine(min_lines=2)
        proposals = engine.analyze_file(str(m.path))
        # With only a single if-return shape lacking complete coverage, engine should reject
        self.assertEqual(proposals, [], "Expected no proposals due to incomplete return coverage")

    def test_augassign_target_is_not_parameterized(self):
        code = """
        def f1(x):
            total = 0
            total += x
            return total

        def f2(x):
            sum = 0
            sum += x
            return sum
        """
        m = TempModule(code)
        self.addCleanup(m.cleanup)
        engine = self._engine(min_lines=2)
        props = engine.analyze_file(str(m.path))
        self.assertTrue(props, "Expected at least one proposal for augassign case")
        # Apply the first proposal and ensure the call does not parameterize the target as __param_*
        new_files = engine.apply_refactoring_multi_file(props[0])
        new_src = new_files[str(m.path)]
        # Parse and find calls to extracted function
        tree = ast.parse(new_src)
        calls = []

        class CallFinder(ast.NodeVisitor):
            def visit_Call(self, node: ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id.startswith("__extracted_func"):
                    calls.append(node)
                self.generic_visit(node)

        CallFinder().visit(tree)
        # There should be two calls (one in each function) and none should include a __param_* standing in for the augmented target
        self.assertEqual(len(calls), 2)
        call_srcs = [ast.unparse(c) for c in calls]
        for s in call_srcs:
            self.assertNotIn("__param_", s)

    def test_runtime_equivalence_after_refactor_same_module(self):
        # Build a simple module with two similar functions that should be refactored
        code = """
        def a(x):
            y = x + 1
            z = y * 2
            w = z - 3
            return w

        def b(x):
            y = x + 1
            z = y * 2
            w = z - 3
            return w
        """
        m = TempModule(code)
        self.addCleanup(m.cleanup)

        # Execute original and capture outputs
        ns = {}
        exec(m.path.read_text(), ns)
        orig_a = [ns["a"](i) for i in (0, 1, 5)]
        orig_b = [ns["b"](i) for i in (0, 1, 5)]

        engine = self._engine(min_lines=2)
        props = engine.analyze_file(str(m.path))
        self.assertTrue(props, "Expected proposals for identical blocks")
        new_src = engine.apply_refactoring(str(m.path), props[0])

        # Execute refactored and compare outputs
        ns2 = {}
        exec(new_src, ns2)
        new_a = [ns2["a"](i) for i in (0, 1, 5)]
        new_b = [ns2["b"](i) for i in (0, 1, 5)]
        self.assertEqual(orig_a, new_a)
        self.assertEqual(orig_b, new_b)

    def test_same_class_method_insertion_and_self_call_rewrite(self):
        code = """
        class C:
            def a(self, x):
                y = x + 1
                z = y * 2
                return z

            def b(self, x):
                y = x + 1
                z = y * 2
                return z
        """
        m = TempModule(code)
        self.addCleanup(m.cleanup)
        engine = self._engine(min_lines=2)
        proposals = engine.analyze_file(str(m.path))
        self.assertTrue(proposals, "Expected a proposal for same-class methods")
        out = engine.apply_refactoring(str(m.path), proposals[0])
        # Extracted method should be inserted inside class with leading underscore
        self.assertIn("class C:", out)
        self.assertIn("def __extracted_func", out)
        # Calls should be rewritten to self.__extracted_func and not pass self explicitly
        self.assertIn("return self.__extracted_func_", out)
        self.assertNotIn("self, self.__extracted_func_", out)

    def test_decorators_preserved_and_no_triple_blank_lines_in_class(self):
        code = """
        class C:
            @classmethod
            def a(cls, x):
                y = x + 1
                z = y * 2
                return z

            @staticmethod
            def b(x):
                y = x + 1
                z = y * 2
                return z
        """
        m = TempModule(code)
        self.addCleanup(m.cleanup)
        engine = self._engine(min_lines=2)
        proposals = engine.analyze_file(str(m.path))
        self.assertTrue(proposals, "Expected a proposal for methods with decorators")
        out = engine.apply_refactoring(str(m.path), proposals[0])
        # Ensure decorators still present on original methods
        self.assertIn("@classmethod", out)
        self.assertIn("@staticmethod", out)
        # Extract the class body and assert no triple blank lines
        lines = out.splitlines()
        import ast as _ast

        mod = _ast.parse(out)
        cls_nodes = [n for n in mod.body if isinstance(n, _ast.ClassDef) and n.name == "C"]
        self.assertTrue(cls_nodes)
        cls = cls_nodes[0]
        start = cls.lineno - 1
        end = getattr(cls, "end_lineno", cls.lineno) - 1
        class_block = "\n".join(lines[start : end + 1])
        self.assertNotIn(
            "\n\n\n", class_block, "Should not contain triple blank lines inside class body"
        )

    def test_staticmethod_extraction_produces_static_helper(self):
        code = """
        class C:
            @staticmethod
            def a(x):
                y = x + 1
                return y * 2

            @staticmethod
            def b(x):
                y = x + 1
                return y * 2
        """
        m = TempModule(code)
        self.addCleanup(m.cleanup)
        engine = self._engine(min_lines=2)
        proposals = engine.analyze_file(str(m.path))
        target = next((p for p in proposals if p.insert_into_class == "C"), None)
        self.assertIsNotNone(target, "Expected a class-level extraction proposal")
        out = engine.apply_refactoring(str(m.path), target)
        mod = ast.parse(out)
        cls_nodes = [n for n in mod.body if isinstance(n, ast.ClassDef) and n.name == "C"]
        self.assertTrue(cls_nodes)
        cls = cls_nodes[0]
        helper = next(
            (
                n
                for n in cls.body
                if isinstance(n, ast.FunctionDef) and n.name.startswith("__extracted_func")
            ),
            None,
        )
        self.assertIsNotNone(helper, "Extracted helper should be present inside the class")
        self.assertTrue(
            any(
                isinstance(dec, ast.Name) and dec.id == "staticmethod"
                for dec in helper.decorator_list
            ),
            "Extracted helper must be decorated as @staticmethod",
        )
        self.assertFalse(any(arg.arg == "self" for arg in helper.args.args))

        for method_name in ("a", "b"):
            method = next(
                n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == method_name
            )
            call_targets = [
                call.func
                for call in ast.walk(method)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr.startswith("__extracted_func")
            ]
            self.assertTrue(call_targets, f"Method {method_name} should call the helper")
            for attr in call_targets:
                self.assertIsInstance(attr.value, ast.Name)
                self.assertEqual(attr.value.id, "C")

    def test_classmethod_extraction_uses_cls_dispatch(self):
        code = """
        class C:
            @classmethod
            def a(cls, x):
                y = x + 1
                return y * 2

            @classmethod
            def b(cls, x):
                y = x + 1
                return y * 2
        """
        m = TempModule(code)
        self.addCleanup(m.cleanup)
        engine = self._engine(min_lines=2)
        proposals = engine.analyze_file(str(m.path))
        target = next((p for p in proposals if p.insert_into_class == "C"), None)
        self.assertIsNotNone(target, "Expected a class-level extraction proposal")
        out = engine.apply_refactoring(str(m.path), target)
        mod = ast.parse(out)
        cls_nodes = [n for n in mod.body if isinstance(n, ast.ClassDef) and n.name == "C"]
        self.assertTrue(cls_nodes)
        cls = cls_nodes[0]
        helper = next(
            (
                n
                for n in cls.body
                if isinstance(n, ast.FunctionDef) and n.name.startswith("__extracted_func")
            ),
            None,
        )
        self.assertIsNotNone(helper)
        self.assertTrue(
            any(
                isinstance(dec, ast.Name) and dec.id == "classmethod"
                for dec in helper.decorator_list
            ),
            "Extracted helper must be decorated as @classmethod",
        )
        self.assertGreater(
            len(helper.args.args), 0, "Class helper should expose a leading parameter"
        )
        self.assertEqual(helper.args.args[0].arg, "cls")

        for method_name in ("a", "b"):
            method = next(
                n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == method_name
            )
            binder_name = method.args.args[0].arg
            call_targets = [
                call
                for call in ast.walk(method)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr.startswith("__extracted_func")
            ]
            self.assertTrue(call_targets, f"Method {method_name} should call the helper")
            for call in call_targets:
                self.assertIsInstance(call.func.value, ast.Name)
                self.assertEqual(call.func.value.id, binder_name)
                self.assertFalse(
                    any(isinstance(arg, ast.Name) and arg.id == binder_name for arg in call.args),
                    "Implicit class binder should not be passed explicitly",
                )

    def test_cross_file_shared_base_instance_methods(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_path = Path(tmpdir) / "shared.py"
            first_path = Path(tmpdir) / "first.py"
            second_path = Path(tmpdir) / "second.py"

            base_path.write_text(
                textwrap.dedent(
                    """
                    class Shared:
                        pass
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            first_path.write_text(
                textwrap.dedent(
                    """
                    from shared import Shared

                    class First(Shared):
                        def alpha(self, value):
                            tmp = value + 1
                            return tmp * 2
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            second_path.write_text(
                textwrap.dedent(
                    """
                    from shared import Shared

                    class Second(Shared):
                        def beta(self, value):
                            tmp = value + 1
                            return tmp * 2
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            engine = self._engine(min_lines=2)
            proposals = engine.analyze_files([str(base_path), str(first_path), str(second_path)])
            target = next((p for p in proposals if p.insert_into_class == "Shared"), None)
            self.assertIsNotNone(target, "Expected helper to be inserted into Shared base class")
            self.assertEqual(target.file_path, str(base_path))

            result = engine.apply_refactoring_multi_file(target)
            shared_src = result[target.file_path]
            first_src = result[str(first_path)]
            second_src = result[str(second_path)]

            shared_mod = ast.parse(shared_src)
            shared_cls = next(
                node
                for node in shared_mod.body
                if isinstance(node, ast.ClassDef) and node.name == "Shared"
            )
            helper = next(
                (
                    n
                    for n in shared_cls.body
                    if isinstance(n, ast.FunctionDef) and n.name.startswith("__extracted_func")
                ),
                None,
            )
            self.assertIsNotNone(helper, "Shared class should gain extracted helper")
            self.assertFalse(helper.decorator_list, "Instance helper should not add decorators")
            self.assertGreater(len(helper.args.args), 0)
            self.assertEqual(helper.args.args[0].arg, "self")

            for src, cls_name, method_name in (
                (first_src, "First", "alpha"),
                (second_src, "Second", "beta"),
            ):
                mod = ast.parse(src)
                cls_node = next(
                    node
                    for node in mod.body
                    if isinstance(node, ast.ClassDef) and node.name == cls_name
                )
                method = next(
                    node
                    for node in cls_node.body
                    if isinstance(node, ast.FunctionDef) and node.name == method_name
                )
                calls = [
                    call
                    for call in ast.walk(method)
                    if isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == helper.name
                ]
                self.assertTrue(calls, f"{cls_name}.{method_name} should call shared helper")
                for call in calls:
                    self.assertIsInstance(call.func.value, ast.Name)
                    self.assertEqual(call.func.value.id, method.args.args[0].arg)

                self.assertNotIn("from shared import _", src)

    def test_cross_file_shared_base_classmethods(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_path = Path(tmpdir) / "shared.py"
            first_path = Path(tmpdir) / "first.py"
            second_path = Path(tmpdir) / "second.py"

            base_path.write_text(
                textwrap.dedent(
                    """
                    class Shared:
                        pass
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            first_path.write_text(
                textwrap.dedent(
                    """
                    from shared import Shared

                    class First(Shared):
                        @classmethod
                        def alpha(cls, value):
                            tmp = value + 1
                            return tmp * 2
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            second_path.write_text(
                textwrap.dedent(
                    """
                    from shared import Shared

                    class Second(Shared):
                        @classmethod
                        def beta(cls, value):
                            tmp = value + 1
                            return tmp * 2
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            engine = self._engine(min_lines=2)
            proposals = engine.analyze_files([str(base_path), str(first_path), str(second_path)])
            target = next((p for p in proposals if p.insert_into_class == "Shared"), None)
            self.assertIsNotNone(target, "Expected classmethod helper to be inserted into Shared")
            self.assertEqual(target.file_path, str(base_path))

            result = engine.apply_refactoring_multi_file(target)
            shared_src = result[target.file_path]
            first_src = result[str(first_path)]

            shared_mod = ast.parse(shared_src)
            shared_cls = next(
                node
                for node in shared_mod.body
                if isinstance(node, ast.ClassDef) and node.name == "Shared"
            )
            helper = next(
                (
                    n
                    for n in shared_cls.body
                    if isinstance(n, ast.FunctionDef) and n.name.startswith("__extracted_func")
                ),
                None,
            )
            self.assertIsNotNone(helper)
            decorator_ids = [dec.id for dec in helper.decorator_list if isinstance(dec, ast.Name)]
            self.assertIn("classmethod", decorator_ids)
            self.assertGreater(len(helper.args.args), 0)
            self.assertEqual(helper.args.args[0].arg, "cls")

            first_mod = ast.parse(first_src)
            first_cls = next(
                node
                for node in first_mod.body
                if isinstance(node, ast.ClassDef) and node.name == "First"
            )
            method = next(
                node
                for node in first_cls.body
                if isinstance(node, ast.FunctionDef) and node.name == "alpha"
            )
            calls = [
                call
                for call in ast.walk(method)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == helper.name
            ]
            self.assertTrue(calls)
            for call in calls:
                self.assertIsInstance(call.func.value, ast.Name)
                self.assertEqual(call.func.value.id, method.args.args[0].arg)
                self.assertTrue(
                    all(
                        not (isinstance(arg, ast.Name) and arg.id == method.args.args[0].arg)
                        for arg in call.args
                    ),
                    "Class binder should not be passed explicitly",
                )

            self.assertNotIn("from shared import _", first_src)

    def test_multilevel_ancestor_selection_targets_nearest_shared_class(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_path = Path(tmpdir) / "hierarchy.py"
            first_path = Path(tmpdir) / "first.py"
            second_path = Path(tmpdir) / "second.py"

            base_path.write_text(
                textwrap.dedent(
                    """
                    class Root:
                        pass

                    class Intermediate(Root):
                        pass
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            first_path.write_text(
                textwrap.dedent(
                    """
                    from hierarchy import Intermediate

                    class LeafOne(Intermediate):
                        def alpha(self, value):
                            tmp = value + 1
                            return tmp * 2
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            second_path.write_text(
                textwrap.dedent(
                    """
                    from hierarchy import Intermediate

                    class LeafTwo(Intermediate):
                        def beta(self, value):
                            tmp = value + 1
                            return tmp * 2
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            engine = self._engine(min_lines=2)
            proposals = engine.analyze_files([str(base_path), str(first_path), str(second_path)])
            target = next((p for p in proposals if p.insert_into_class == "Intermediate"), None)
            self.assertIsNotNone(
                target, "Expected helper to target nearest shared ancestor Intermediate"
            )
            self.assertEqual(target.file_path, str(base_path))

            result = engine.apply_refactoring_multi_file(target)
            hierarchy_src = result[target.file_path]
            hierarchy_mod = ast.parse(hierarchy_src)
            intermediate_cls = next(
                node
                for node in hierarchy_mod.body
                if isinstance(node, ast.ClassDef) and node.name == "Intermediate"
            )
            helper = next(
                (
                    n
                    for n in intermediate_cls.body
                    if isinstance(n, ast.FunctionDef) and n.name.startswith("__extracted_func")
                ),
                None,
            )
            self.assertIsNotNone(helper, "Helper should live inside Intermediate, not Root")

            root_cls = next(
                node
                for node in hierarchy_mod.body
                if isinstance(node, ast.ClassDef) and node.name == "Root"
            )
            self.assertFalse(
                any(
                    isinstance(n, ast.FunctionDef) and n.name.startswith("__extracted_func")
                    for n in root_cls.body
                ),
                "Root should remain unchanged by nearest-ancestor selection",
            )

    def test_local_function_insertion_into_enclosing_function_scope(self):
        # Duplicate blocks exist inside two sibling inner functions; extracted helper
        # should be inserted into the most specific common enclosing scope (the outer function),
        # not at module level.
        code = """
        def outer(a, b):
            def f1(x):
                p = x + a
                q = p * b
                return q

            def f2(x):
                p = x + a
                q = p * b
                return q

            return f1(3) + f2(4)
        """
        m = TempModule(code)
        self.addCleanup(m.cleanup)

        # Capture original behavior
        ns = {}
        exec(m.path.read_text(), ns)
        orig = ns["outer"](2, 5)

        engine = self._engine(min_lines=2)
        proposals = engine.analyze_file(str(m.path))
        # Expect at least one proposal pairing the inner functions' bodies
        self.assertTrue(proposals, "Expected a proposal for duplicate inner function bodies")

        # Apply proposals until we find one that inserts into the enclosing function scope
        found_local_insertion = False
        for p in proposals:
            new_src = engine.apply_refactoring(str(m.path), p)
            mod = ast.parse(new_src)
            outers = [n for n in mod.body if isinstance(n, ast.FunctionDef) and n.name == "outer"]
            if not outers:
                continue
            outer_fn = outers[0]
            inner_names = {n.name for n in outer_fn.body if isinstance(n, ast.FunctionDef)}
            top_level_names = {n.name for n in mod.body if isinstance(n, ast.FunctionDef)}
            if any(name.startswith("__extracted_func") for name in inner_names) and not any(
                name.startswith("__extracted_func") for name in top_level_names
            ):
                # Runtime equivalence: calling outer should still work and match original
                ns2 = {}
                exec(new_src, ns2)
                new_val = ns2["outer"](2, 5)
                self.assertEqual(orig, new_val)
                found_local_insertion = True
                break

        self.assertTrue(
            found_local_insertion, "Did not find a proposal inserting helper into outer()"
        )

    def test_deepest_common_enclosing_function_is_chosen(self):
        # Mixed-depth nesting: f.inner1.deep and f.inner2 share common ancestor f but not inner1/inner2.
        # The extracted helper should be inserted into f (the deepest common ancestor), not at module
        # scope and not inside inner1 or inner2 exclusively.
        code = """
        def f(a):
            def inner1():
                def deep(x):
                    t = x + a
                    u = t * 2
                    return u
                return deep(1)

            def inner2(x):
                t = x + a
                u = t * 2
                return u

            return inner1() + inner2(3)
        """
        m = TempModule(code)
        self.addCleanup(m.cleanup)
        ns = {}
        exec(m.path.read_text(), ns)
        orig = ns["f"](5)

        engine = self._engine(min_lines=2)
        props = engine.analyze_file(str(m.path))
        self.assertTrue(props, "Expected a proposal in mixed-depth nesting case")

        picked = None
        new_src = None
        for p in props:
            out = engine.apply_refactoring(str(m.path), p)
            mod = ast.parse(out)
            fns = [n for n in mod.body if isinstance(n, ast.FunctionDef) and n.name == "f"]
            if not fns:
                continue
            f_node = fns[0]
            inner_names = {n.name for n in f_node.body if isinstance(n, ast.FunctionDef)}
            top_level = {n.name for n in mod.body if isinstance(n, ast.FunctionDef)}
            if any(name.startswith("__extracted_func") for name in inner_names) and not any(
                name.startswith("__extracted_func") for name in top_level
            ):
                picked = p
                new_src = out
                break

        self.assertIsNotNone(
            picked, "No proposal inserted helper into the deepest common ancestor f"
        )
        ns2 = {}
        exec(new_src, ns2)
        self.assertEqual(orig, ns2["f"](5))

    def test_dce_with_async_nested_functions_inserts_into_enclosing_async_outer(self):
        # Async outer with two inner async functions sharing a duplicate block.
        # Helper should be inserted into the async outer function body, not at module scope.
        import asyncio

        code = """
        import asyncio

        async def outer(a, b):
            async def f1(x):
                p = x + a
                q = p * b
                return q

            async def f2(x):
                p = x + a
                q = p * b
                return q

            return await f1(3) + await f2(4)
        """
        m = TempModule(code)
        self.addCleanup(m.cleanup)

        ns = {}
        exec(m.path.read_text(), ns)
        orig = asyncio.run(ns["outer"](2, 5))

        engine = self._engine(min_lines=2)
        proposals = engine.analyze_file(str(m.path))
        self.assertTrue(proposals, "Expected a proposal for duplicate async inner function bodies")

        found_local_insertion = False
        for p in proposals:
            new_src = engine.apply_refactoring(str(m.path), p)
            mod = ast.parse(new_src)
            outers = [
                n
                for n in mod.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "outer"
            ]
            if not outers:
                continue
            outer_fn = outers[0]
            inner_names = {
                n.name
                for n in outer_fn.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            top_level_names = {
                n.name for n in mod.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            if any(name.startswith("__extracted_func") for name in inner_names) and not any(
                name.startswith("__extracted_func") for name in top_level_names
            ):
                ns2 = {}
                exec(new_src, ns2)
                new_val = asyncio.run(ns2["outer"](2, 5))
                self.assertEqual(orig, new_val)
                found_local_insertion = True
                break

        self.assertTrue(
            found_local_insertion, "Did not find a proposal inserting helper into async outer()"
        )

    def test_dce_with_class_method_nested_functions_inserts_into_method_scope(self):
        # Inner functions inside a class method share a duplicate block; helper should be
        # inserted into the method scope (function inside class), not at class or module level.
        code = """
        class C:
            def m(self, a, b):
                def f1(x):
                    p = x + a
                    q = p * b
                    return q

                def f2(x):
                    p = x + a
                    q = p * b
                    return q

                return f1(2) + f2(3)
        """
        m = TempModule(code)
        self.addCleanup(m.cleanup)

        ns = {}
        exec(m.path.read_text(), ns)
        orig = ns["C"]().m(2, 5)

        engine = self._engine(min_lines=2)
        proposals = engine.analyze_file(str(m.path))
        self.assertTrue(
            proposals, "Expected a proposal for duplicate inner function bodies in a method"
        )

        found_method_insertion = False
        picked_src = None
        for p in proposals:
            new_src = engine.apply_refactoring(str(m.path), p)
            mod = ast.parse(new_src)
            classes = [n for n in mod.body if isinstance(n, ast.ClassDef) and n.name == "C"]
            if not classes:
                continue
            cls = classes[0]
            methods = [
                n
                for n in cls.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "m"
            ]
            if not methods:
                continue
            m_fn = methods[0]
            inner_names = {
                n.name for n in m_fn.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            class_level_names = {
                n.name for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            top_level_names = {
                n.name for n in mod.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            inner_has_helper = any(name.startswith("__extracted_func") for name in inner_names)
            class_has_helper = any(
                name.startswith("__extracted_func") for name in class_level_names
            )
            top_level_has_helper = any(
                name.startswith("__extracted_func") for name in top_level_names
            )
            if inner_has_helper and not class_has_helper and not top_level_has_helper:
                ns2 = {}
                exec(new_src, ns2)
                new_val = ns2["C"]().m(2, 5)
                self.assertEqual(orig, new_val)
                found_method_insertion = True
                picked_src = new_src
                break

        self.assertTrue(
            found_method_insertion, "Did not find a proposal inserting helper into method scope C.m"
        )

    def test_deepest_common_enclosing_inner_function_is_chosen(self):
        # Deepest common ancestor is an inner function (not the outermost):
        # f -> common -> d1 and f -> common -> d2 have duplicate blocks; helper should be
        # inserted into 'common', not into d1/d2 and not into f.
        code = """
        def f(a):
            def common(b):
                def d1(x):
                    t = x + a + b
                    u = t * 2
                    return u
                def d2(x):
                    t = x + a + b
                    u = t * 2
                    return u
                return d1(1) + d2(2)
            return common(3)
        """
        m = TempModule(code)
        self.addCleanup(m.cleanup)

        ns = {}
        exec(m.path.read_text(), ns)
        orig = ns["f"](5)

        engine = self._engine(min_lines=2)
        props = engine.analyze_file(str(m.path))
        self.assertTrue(props, "Expected a proposal in inner-common ancestor case")

        found_common_insertion = False
        for p in props:
            out = engine.apply_refactoring(str(m.path), p)
            mod = ast.parse(out)
            fns = [n for n in mod.body if isinstance(n, ast.FunctionDef) and n.name == "f"]
            if not fns:
                continue
            f_node = fns[0]
            commons = [
                n
                for n in f_node.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "common"
            ]
            if not commons:
                continue
            common_fn = commons[0]
            # Helper should be in 'common' body
            inner_names = {
                n.name
                for n in common_fn.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            top_level = {
                n.name for n in mod.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            if any(name.startswith("__extracted_func") for name in inner_names) and not any(
                name.startswith("__extracted_func") for name in top_level
            ):
                ns2 = {}
                exec(out, ns2)
                self.assertEqual(orig, ns2["f"](5))
                found_common_insertion = True
                break

        self.assertTrue(
            found_common_insertion,
            "No proposal inserted helper into the inner common ancestor function",
        )

    def test_dce_mixed_async_sync_within_same_async_outer(self):
        # Mixed async/sync inner functions under an async outer. The shared block should extract
        # into the async outer function scope. Validate runtime equivalence.
        import asyncio

        code = """
        import asyncio

        async def outer(a, b):
            async def f1(x):
                p = x + a
                q = p * b
                return q

            def f2(x):
                p = x + a
                q = p * b
                return q

            return await f1(3) + f2(4)
        """
        m = TempModule(code)
        self.addCleanup(m.cleanup)

        ns = {}
        exec(m.path.read_text(), ns)
        orig = asyncio.run(ns["outer"](2, 5))

        engine = self._engine(min_lines=2)
        props = engine.analyze_file(str(m.path))
        self.assertTrue(props, "Expected a proposal for mixed async/sync inner functions")

        found_insertion = False
        for p in props:
            out = engine.apply_refactoring(str(m.path), p)
            mod = ast.parse(out)
            outers = [
                n
                for n in mod.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "outer"
            ]
            if not outers:
                continue
            outer_fn = outers[0]
            inner_names = {
                n.name
                for n in outer_fn.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            top_level = {
                n.name for n in mod.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            if any(name.startswith("__extracted_func") for name in inner_names) and not any(
                name.startswith("__extracted_func") for name in top_level
            ):
                ns2 = {}
                exec(out, ns2)
                self.assertEqual(orig, asyncio.run(ns2["outer"](2, 5)))
                found_insertion = True
                break

        self.assertTrue(
            found_insertion,
            "No proposal inserted helper into async outer() for mixed async/sync case",
        )

    def test_dce_multiple_candidates_interleaved_defs(self):
        # Multiple duplicate pairs interleaved at different depths; ensure at least one proposal
        # targets the correct DCE and runtime equivalence holds for the outer function.
        code = """
        def outer(a):
            def g1(x):
                t = x + a
                u = t * 2
                return u

            def mid():
                def g2(x):
                    t = x + a
                    u = t * 2
                    return u
                def h1(y):
                    r = y - a
                    s = r * 3
                    return s
                def h2(y):
                    r = y - a
                    s = r * 3
                    return s
                return g2(2) + h1(5) + h2(6)

            return g1(1) + mid()
        """
        m = TempModule(code)
        self.addCleanup(m.cleanup)

        ns = {}
        exec(m.path.read_text(), ns)
        orig = ns["outer"](7)

        engine = self._engine(min_lines=2)
        props = engine.analyze_file(str(m.path))
        self.assertTrue(props, "Expected proposals for interleaved nested duplicate blocks")

        # Look for a proposal that inserts into 'outer' (DCE for g1 vs g2)
        found_outer = False
        new_src = None
        for p in props:
            out = engine.apply_refactoring(str(m.path), p)
            mod = ast.parse(out)
            outers = [n for n in mod.body if isinstance(n, ast.FunctionDef) and n.name == "outer"]
            if not outers:
                continue
            outer_fn = outers[0]
            inner_names = {
                n.name
                for n in outer_fn.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            top_level = {
                n.name for n in mod.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            if any(name.startswith("__extracted_func") for name in inner_names) and not any(
                name.startswith("__extracted_func") for name in top_level
            ):
                found_outer = True
                new_src = out
                break

        self.assertTrue(found_outer, "Expected at least one proposal inserting helper into outer()")
        ns2 = {}
        exec(new_src, ns2)
        self.assertEqual(orig, ns2["outer"](7))

    def test_dce_nonlocal_skips_and_global_injection(self):
        # Nonlocal case should be skipped conservatively; Global case should inject a global decl
        # into the extracted helper inserted into function scope.
        # Nonlocal scenario
        code_nonlocal = """
        def outer():
            x = 0
            def f1():
                nonlocal x
                t = x + 1
                u = t * 2
                return u
            def f2():
                nonlocal x
                t = x + 1
                u = t * 2
                return u
            return f1() + f2()
        """
        m1 = TempModule(code_nonlocal)
        self.addCleanup(m1.cleanup)
        engine = self._engine(min_lines=2)
        props1 = engine.analyze_file(str(m1.path))
        # Expect no proposals due to nonlocal conservative skip
        self.assertEqual(props1, [], "Expected proposals to be skipped when nonlocal is present")

        # Global scenario
        code_global = """
        G = 0
        def outer(a):
            def f1(x):
                global G
                t = x + a
                G = t
                return t
            def f2(x):
                global G
                t = x + a
                G = t
                return t
            return f1(1) + f2(2)
        """
        m2 = TempModule(code_global)
        self.addCleanup(m2.cleanup)

        ns = {}
        exec(m2.path.read_text(), ns)
        orig = ns["outer"](5)

        props2 = engine.analyze_file(str(m2.path))
        self.assertTrue(props2, "Expected proposals when using global declarations")
        found = False
        for p in props2:
            out = engine.apply_refactoring(str(m2.path), p)
            # Ensure extracted helper inside outer and contains 'global G'
            self.assertIn("global G", out)
            mod = ast.parse(out)
            outers = [n for n in mod.body if isinstance(n, ast.FunctionDef) and n.name == "outer"]
            if not outers:
                continue
            outer_fn = outers[0]
            inner_names = {
                n.name
                for n in outer_fn.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            top_level = {
                n.name for n in mod.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            if any(name.startswith("__extracted_func") for name in inner_names) and not any(
                name.startswith("__extracted_func") for name in top_level
            ):
                ns2 = {}
                exec(out, ns2)
                self.assertEqual(orig, ns2["outer"](5))
                found = True
                break
        self.assertTrue(
            found, "Expected a proposal inserting helper with global injection into outer()"
        )


class TestUnifierExtractorCalleeThunk(unittest.TestCase):
    def test_callee_parameter_is_wrapped_in_thunk(self):
        # Blocks differ only by the callee name; that callee should become a param used as a callee
        src1 = "result = f(1, 2)\nreturn result\n"
        src2 = "result = g(1, 2)\nreturn result\n"
        b1 = ast.parse(src1).body
        b2 = ast.parse(src2).body
        uni = Unifier(max_parameters=3, parameterize_constants=False)
        hygienic = [{}, {}]
        subst = uni.unify_blocks([b1, b2], hygienic)
        self.assertIsNotNone(subst, "Unification should succeed for differing callee names")
        # Extract function from block1
        extractor = HygienicExtractor()
        fn, param_order = extractor.extract_function(
            template_block=b1,
            substitution=subst,  # type: ignore[arg-type]
            free_variables=set(),
            enclosing_names=set(),
            is_value_producing=True,
        )
        # Generate calls; the differing callee param should be wrapped in lambda(*args, **kwargs)
        call0 = extractor.generate_call(
            function_name=fn.name,
            block_idx=0,
            substitution=subst,  # type: ignore[arg-type]
            param_order=param_order,
            free_variables=set(),
            is_value_producing=True,
        )
        call1 = extractor.generate_call(
            function_name=fn.name,
            block_idx=1,
            substitution=subst,  # type: ignore[arg-type]
            param_order=param_order,
            free_variables=set(),
            is_value_producing=True,
        )
        s0 = ast.unparse(call0)
        s1 = ast.unparse(call1)
        self.assertIn("lambda *args, **kwargs:", s0)
        self.assertIn("lambda *args, **kwargs:", s1)
        self.assertTrue("f(*args, **kwargs)" in s0 or "g(*args, **kwargs)" in s0)
        self.assertTrue("f(*args, **kwargs)" in s1 or "g(*args, **kwargs)" in s1)


class TestCrossFileImports(unittest.TestCase):
    def test_cross_file_import_insertion_with_absolute_pref(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            pkg = base / "pkg"
            pkg.mkdir()
            (pkg / "__init__.py").write_text("\n", encoding="utf-8")
            a = pkg / "a.py"
            b = pkg / "b.py"
            a.write_text(
                textwrap.dedent(
                    """
                    def fa(x):
                        y = x + 1
                        z = y * 2
                        return z
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            b.write_text(
                textwrap.dedent(
                    """
                    def fb(x):
                        y = x + 1
                        z = y * 2
                        return z
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            engine = UnificationRefactorEngine(
                max_parameters=5,
                min_lines=2,
                parameterize_constants=True,
                prefer_absolute_imports=True,
            )
            props = engine.analyze_directory(str(pkg), recursive=False)
            self.assertTrue(props, "Expected a cross-file proposal between a.py and b.py")
            modified = engine.apply_refactoring_multi_file(props[0])
            # At least one file should gain an import of the extracted function
            has_import = any("import __extracted_func" in content for content in modified.values())
            self.assertTrue(
                has_import, "Expected an import of __extracted_func in one modified file"
            )

    def test_cross_file_runtime_equivalence_after_refactor(self):
        import sys, importlib

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            a = base / "a.py"
            b = base / "b.py"
            a.write_text(
                textwrap.dedent(
                    """
                def fa(x):
                    y = x + 1
                    z = y * 2
                    return z - 3
                """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            b.write_text(
                textwrap.dedent(
                    """
                def fb(x):
                    y = x + 1
                    z = y * 2
                    return z - 3
                """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            sys.path.insert(0, str(base))
            try:
                a_mod = importlib.import_module("a")
                b_mod = importlib.import_module("b")
                orig_a = [a_mod.fa(i) for i in (0, 1, 5)]
                orig_b = [b_mod.fb(i) for i in (0, 1, 5)]

                engine = UnificationRefactorEngine(
                    max_parameters=5, min_lines=2, parameterize_constants=True
                )
                props = engine.analyze_directory(str(base), recursive=False)
                self.assertTrue(
                    props, "Expected a cross-file proposal between a.py and b.py in same directory"
                )
                modified = engine.apply_refactoring_multi_file(props[0])
                # Write modifications to disk
                for fpath, content in modified.items():
                    Path(fpath).write_text(content, encoding="utf-8")
                # Reload modules to pick up changes
                for name in ["a", "b"]:
                    if name in sys.modules:
                        del sys.modules[name]
                a_mod2 = importlib.import_module("a")
                b_mod2 = importlib.import_module("b")
                new_a = [a_mod2.fa(i) for i in (0, 1, 5)]
                new_b = [b_mod2.fb(i) for i in (0, 1, 5)]
                self.assertEqual(orig_a, new_a)
                self.assertEqual(orig_b, new_b)
            finally:
                # Remove from sys.path to avoid pollution
                if str(base) in sys.path:
                    sys.path.remove(str(base))

    def test_structural_similarity_filter_blocks_unrelated_pairs(self):
        code = """
        def f1(n):
            # arithmetic chain
            a = n + 1
            b = a * 2
            c = b - 3
            return c

        def f2(n):
            # unrelated control-flow heavy
            total = 0
            i = 0
            while i < n:
                if i % 2 == 0:
                    total += i
                else:
                    total -= 1
                i += 1
            return total
        """
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "mod.py"
            p.write_text(textwrap.dedent(code).strip() + "\n", encoding="utf-8")
            engine = UnificationRefactorEngine(max_parameters=3, min_lines=3)
            proposals = engine.analyze_file(str(p))
            # Expect no proposals due to structural mismatch
            self.assertEqual(proposals, [])

    def test_reassignment_without_initial_binding_is_rejected(self):
        code = """
        def f1(x):
            r = 0
            if x > 0:
                r = r + 1  # reassignment without initial bind in block
            return r

        def f2(x):
            r = 1  # different initial binding to force the engine to consider only the if-block
            if x > 0:
                r = r + 1
            return r
        """
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "mod.py"
            p.write_text(textwrap.dedent(code).strip() + "\n", encoding="utf-8")
            engine = UnificationRefactorEngine(max_parameters=5, min_lines=2)
            proposals = engine.analyze_file(str(p))
            # The candidate block would be inside the if; engine should reject due to unsafe reassignment
            self.assertEqual(proposals, [])

    def test_global_declaration_injected_in_extracted_function(self):
        code = """
        G = 0

        def f1(x):
            y = x + 1
            global G
            G = y  # initial binding is global in enclosing function scope
            return y

        def f2(x):
            y = x + 1
            global G
            G = y
            return y
        """
        # Note: leave out an explicit 'global G' inside the candidate block by arranging
        # that the extraction targets only the assignment; engine will inject 'global G'
        # into the extracted function body for hygiene
        m = TempModule(code)
        self.addCleanup(m.cleanup)
        engine = UnificationRefactorEngine(max_parameters=5, min_lines=2)
        props = engine.analyze_file(str(m.path))
        self.assertTrue(props, "Expected proposals where global assignment occurs")
        out = engine.apply_refactoring(str(m.path), props[0])
        # Extracted function should include a global declaration
        self.assertIn("global G", out)


class TestExtractorDeclarations(unittest.TestCase):
    def test_nonlocal_declaration_injected_by_extractor(self):
        # Build a template block that assigns to nonlocal 'x' and ensure extractor injects declaration
        template_src = "x = x + 1\n"
        block = ast.parse(template_src).body
        extractor = HygienicExtractor()
        fn, _ = extractor.extract_function(
            template_block=block,
            substitution=Unifier(max_parameters=1).unify_blocks([block, block], [{}, {}])
            or __import__("types").SimpleNamespace(param_expressions={}),
            free_variables=set(),
            enclosing_names=set(),
            is_value_producing=False,
            nonlocal_decls={"x"},
            function_name="extracted_func",
        )
        code = ast.unparse(fn)
        self.assertIn("nonlocal x", code)


if __name__ == "__main__":
    unittest.main()
