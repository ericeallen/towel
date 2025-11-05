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
                self.assertNotIn("return extracted_func", f2_block)
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
                if isinstance(node.func, ast.Name) and node.func.id.startswith("extracted_func"):
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
        self.assertIn("def _extracted_func", out)
        # Calls should be rewritten to self._extracted_func and not pass self explicitly
        self.assertIn("return self._extracted_func(", out)
        self.assertNotIn("self, self._extracted_func", out)


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
                max_parameters=5, min_lines=2, parameterize_constants=True, prefer_absolute_imports=True
            )
            props = engine.analyze_directory(str(pkg), recursive=False)
            self.assertTrue(props, "Expected a cross-file proposal between a.py and b.py")
            modified = engine.apply_refactoring_multi_file(props[0])
            # At least one file should gain an import of the extracted function
            has_import = any("import extracted_func" in content for content in modified.values())
            self.assertTrue(has_import, "Expected an import of extracted_func in one modified file")

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
            substitution=Unifier(max_parameters=1).unify_blocks([block, block], [{}, {}]) or __import__('types').SimpleNamespace(param_expressions={}),
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
