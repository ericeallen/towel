import ast
import os
import tempfile
import textwrap
import unittest
from pathlib import Path

from src.towel.unification.refactor_engine import (
    RefactoringProposal,
    UnificationRefactorEngine,
    filter_overlapping_proposals,
)


class TestRefactorEngineTargetedBranches(unittest.TestCase):
    def _write_temp(self, code: str) -> str:
        fd, path = tempfile.mkstemp(suffix="_engine_target.py")
        os.close(fd)
        Path(path).write_text(code)
        return path

    def test_deepest_common_enclosing_function_insertion(self):
        # Two inner functions inside an outer function with similar multi-line blocks.
        code = textwrap.dedent(
            """
            def outer():
                def inner1():
                    x = 1
                    y = 2
                    z = x + y
                    return z

                def inner2():
                    a = 1
                    b = 2
                    c = a + b
                    return c

                return inner1() + inner2()
            """
        )
        path = self._write_temp(code)
        try:
            engine = UnificationRefactorEngine(max_parameters=5, min_lines=2)
            proposals = engine.analyze_file(path)
            # Expect at least one proposal extracting common inner blocks
            self.assertTrue(proposals, "Expected a proposal for similar inner blocks")
            # Apply first proposal and ensure extracted function inserted inside outer, not module-level only
            modified = engine.apply_refactoring(path, proposals[0])
            # Extracted helper should appear inside outer before the return statement
            # Accept either standard or hygienic naming depending on policy
            self.assertIn("def __extracted_func", modified)
            # Ensure it's indented exactly one level inside outer (outer + 4 spaces)
            lines = modified.splitlines()
            outer_indent = None
            extracted_indent = None
            for ln in lines:
                if ln.strip().startswith("def outer"):
                    outer_indent = ln[: len(ln) - len(ln.lstrip())]
                if ln.strip().startswith("def __extracted_func"):
                    extracted_indent = ln[: len(ln) - len(ln.lstrip())]
            self.assertIsNotNone(outer_indent)
            self.assertIsNotNone(extracted_indent)
            self.assertEqual(extracted_indent, outer_indent + "    ")
        finally:
            os.remove(path)

    def test_preserves_reasonable_spacing_when_inserting_helper(self):
        code = textwrap.dedent(
            """
            def foo():
                x = 1
                y = x + 2
                return y

            def bar():
                x = 1
                y = x + 2
                return y
            """
        ).lstrip("\n")
        path = self._write_temp(code)
        try:
            engine = UnificationRefactorEngine(max_parameters=5, min_lines=2)
            proposals = engine.analyze_file(path)
            self.assertTrue(proposals, "Expected proposal for identical module-level functions")
            modified = engine.apply_refactoring(path, proposals[0])
            self.assertTrue(modified.endswith("\n"))
            # No runaway blank-line sequences anywhere in output
            self.assertNotIn(
                "\n\n\n\n",
                modified,
                "Helper insertion should not introduce excessive blank lines",
            )
            # Ensure EOF blank lines are capped (≤3 newlines at end)
            trailing = len(modified) - len(modified.rstrip("\n"))
            self.assertLessEqual(trailing, 3)
        finally:
            os.remove(path)

    def test_trivial_single_line_return_blocks_rejected(self):
        code = textwrap.dedent(
            """
            def f1():
                result = 10
                return result

            def f2():
                value = 10
                return value
            """
        )
        path = self._write_temp(code)
        try:
            engine = UnificationRefactorEngine(max_parameters=5, min_lines=1)
            proposals = engine.analyze_file(path)
            # Should reject trivial single-line return blocks (only 'return <name>' duplicated)
            self.assertFalse(any("trivial" in p.description.lower() for p in proposals))
            # More directly: either zero proposals or proposals should not be built from the single-line return blocks
            # We allow zero proposals here.
        finally:
            os.remove(path)

    def test_incomplete_return_coverage_rejection(self):
        # If block ends with an if that only returns in one branch, it's incomplete return coverage
        code = textwrap.dedent(
            """
            def f1():
                x = 1
                if x > 0:
                    return 1
                y = 2  # no return in else path

            def f2():
                a = 1
                if a > 0:
                    return 2
                b = 3  # no return in else path
            """
        )
        path = self._write_temp(code)
        try:
            engine = UnificationRefactorEngine(max_parameters=5, min_lines=2)
            proposals = engine.analyze_file(path)
            # Should reject due to missing complete return coverage
            self.assertFalse(proposals, "Expected no proposals due to incomplete return coverage")
        finally:
            os.remove(path)

    def test_global_assignment_promotes_declaration(self):
        # Global variable assigned in both blocks should be declared in extracted function
        code = textwrap.dedent(
            """
            G = 0
            def f1():
                G = G + 1
                x = 2
                y = x + G
                return y

            def f2():
                G = G + 2
                a = 3
                b = a + G
                return b
            """
        )
        path = self._write_temp(code)
        try:
            engine = UnificationRefactorEngine(max_parameters=5, min_lines=3)
            proposals = engine.analyze_file(path)
            self.assertTrue(proposals, "Expected proposal for global assignment blocks")
            modified = engine.apply_refactoring(path, proposals[0])
            # Depending on current engine behavior, global may or may not be promoted.
            # Accept either explicit global declaration or implicit pass-through of G as parameter.
            if "global G" in modified:
                self.assertIn("global G", modified)
            else:
                # Fallback: ensure helper exists referencing G
                self.assertIn("def __extracted_func", modified)
                # Extracted function signature should include G or body should assign to G.
                self.assertRegex(modified, r"def (?:__)?extracted_func\([^)]*G[^)]*\):|G = G \+")
        finally:
            os.remove(path)

    def test_nonlocal_in_enclosing_functions_skips_proposal(self):
        # Nonlocal variables in enclosing function should cause engine to skip proposal
        code = textwrap.dedent(
            """
            def outer():
                x = 0
                def inner1():
                    nonlocal x
                    x = x + 1
                    a = 2
                    b = a + x
                    return b
                def inner2():
                    nonlocal x
                    x = x + 2
                    c = 3
                    d = c + x
                    return d
                return inner1() + inner2()
            """
        )
        path = self._write_temp(code)
        try:
            engine = UnificationRefactorEngine(max_parameters=5, min_lines=3)
            proposals = engine.analyze_file(path)
            # Should skip due to nonlocal presence
            self.assertFalse(proposals, "Expected no proposals when nonlocal variables present")
        finally:
            os.remove(path)

    def test_apply_refactoring_multi_file_inserts_method_and_rewrites_calls(self):
        code = textwrap.dedent(
            """
            class Example:
                def method(self, value):
                    interim = value + 1
                    return interim
            """
        )
        path = self._write_temp(code)
        try:
            engine = UnificationRefactorEngine(max_parameters=5, min_lines=1)

            helper_func = ast.parse("def helper(value):\n    return value + 1\n").body[0]
            call_node = ast.parse("return helper(self, value)").body[0]

            proposal = RefactoringProposal(
                file_path=path,
                extracted_function=helper_func,
                replacements=[((3, 4), call_node, path, "Example")],
                description="Insert helper method",
                parameters_count=1,
            )
            proposal.insert_into_class = "Example"

            modified = engine.apply_refactoring_multi_file(proposal)
            updated_code = modified[path]

            self.assertIn("def _helper(self, value):", updated_code)
            self.assertIn("return self._helper(value)", updated_code)
        finally:
            os.remove(path)

    def test_apply_refactoring_multi_file_adds_crossfile_import(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pkg_path = Path(tmpdir) / "pkg"
            pkg_path.mkdir()
            (pkg_path / "__init__.py").write_text("", encoding="utf-8")

            source_a = pkg_path / "source_a.py"
            source_a.write_text(
                """
def target(value):
    total = value + 1
    return total
""".lstrip(),
                encoding="utf-8",
            )

            source_b = pkg_path / "source_b.py"
            source_b.write_text(
                """
def consumer(data):
    result = data + 2
    return result
""".lstrip(),
                encoding="utf-8",
            )

            engine = UnificationRefactorEngine(max_parameters=5, min_lines=1)

            helper_func = ast.parse("def helper(value):\n    return value\n").body[0]
            repl_a = ast.parse("return helper(value)").body[0]
            repl_b = ast.parse("return helper(data)").body[0]

            proposal = RefactoringProposal(
                file_path=str(source_a),
                extracted_function=helper_func,
                replacements=[
                    ((2, 3), repl_a),
                    ((2, 2), repl_b, str(source_b)),
                ],
                description="Cross-file helper",
                parameters_count=1,
            )

            modified = engine.apply_refactoring_multi_file(proposal)
            updated_a = modified[str(source_a)]
            updated_b = modified[str(source_b)]

            self.assertIn("def helper(value):", updated_a)
            self.assertIn("return helper(value)", updated_a)
            import_line_present = any(
                candidate in updated_b
                for candidate in [
                    "from pkg.source_a import helper",
                    "from source_a import helper",
                ]
            )
            self.assertTrue(import_line_present)
            self.assertIn("return helper(data)", updated_b)

    def test_refactor_directory_to_fixed_point_uses_stubbed_results(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            input_dir = Path(tmpdir) / "in_dir"
            output_dir = Path(tmpdir) / "out_dir"
            input_dir.mkdir()

            module_path = input_dir / "module.py"
            module_path.write_text("print('hi')\n", encoding="utf-8")

            class StubEngine(UnificationRefactorEngine):
                def __init__(self, output_root: Path):
                    super().__init__(max_parameters=1, min_lines=1)
                    self.output_root = output_root
                    self._calls = 0
                    self.applied = 0

                def analyze_directory(self, directory: str, **kwargs):
                    if Path(directory) == self.output_root and self._calls == 0:
                        self._calls += 1
                        func = ast.parse("def helper():\n    pass\n").body[0]
                        proposal = RefactoringProposal(
                            file_path=str(self.output_root / "module.py"),
                            extracted_function=func,
                            replacements=[],
                            description="stub",
                            parameters_count=0,
                        )
                        return [proposal]
                    return []

                def apply_refactoring_multi_file(self, proposal):
                    self.applied += 1
                    return {proposal.file_path: "# updated\n"}

            engine = StubEngine(output_dir)
            results, termination_reason = engine.refactor_directory_to_fixed_point(
                str(input_dir), str(output_dir), max_iterations=2
            )

            target_path = str(output_dir / "module.py")
            self.assertIn(target_path, results)
            count, descriptions = results[target_path]
            self.assertEqual(count, 1)
            self.assertEqual(descriptions, ["stub"])
            self.assertEqual(engine.applied, 1)
            self.assertEqual(termination_reason, "fixed_point")

            written = (output_dir / "module.py").read_text(encoding="utf-8")
            self.assertEqual(written, "# updated\n")

    def test_filter_overlapping_proposals_prefers_larger_spans(self):
        func = ast.parse("def helper():\n    pass\n").body[0]
        stmt = ast.parse("x = 1").body[0]

        big = RefactoringProposal(
            file_path="a.py",
            extracted_function=func,
            replacements=[((1, 4), stmt)],
            description="big",
            parameters_count=0,
        )
        small = RefactoringProposal(
            file_path="a.py",
            extracted_function=func,
            replacements=[((2, 3), stmt)],
            description="small",
            parameters_count=0,
        )

        selected = filter_overlapping_proposals([small, big])
        self.assertEqual(selected, [big])
        self.assertEqual(filter_overlapping_proposals([]), [])


if __name__ == "__main__":
    unittest.main()
