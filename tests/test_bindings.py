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

"""
Tests for binding construct handling.

Tests that the system correctly handles:
- For loop variables (i vs j should be alpha-equivalent)
- Comprehension variables
- Lambda parameters
- Nested function scopes

IMPORTANT: These tests NEVER modify test_examples files.
All tests read from test_examples in read-only mode.
"""

import unittest
import ast
import tempfile
from pathlib import Path
from towel.unification.refactor_engine import UnificationRefactorEngine
from tests.test_helpers import get_test_example_path, assert_file_not_modified


class TestForLoopBindings(unittest.TestCase):
    """Test for-loop variable binding and alpha-renaming."""

    def setUp(self):
        self.engine = UnificationRefactorEngine(
            max_parameters=5, min_lines=4, parameterize_constants=True
        )
        # Capture original content to verify it's never modified
        self.example_path = get_test_example_path("bindings_for_loops.py")
        self.original_content = self.example_path.read_text()

    def tearDown(self):
        """Verify test_examples file was never modified."""
        assert_file_not_modified(self.example_path, self.original_content)

    def test_simple_for_loop_alpha_renaming(self):
        """Test that loop variables i and j are treated as alpha-equivalent."""
        proposals = self.engine.analyze_file(str(self.example_path))
        self.assertGreater(
            len(proposals), 0, "Should find duplicates with different loop variables"
        )

        # Find the proposal for process_list_a and process_list_b
        relevant = [p for p in proposals if "process_list" in p.description.lower()]
        self.assertGreater(len(relevant), 0, "Should find process_list duplicates")

        # Check the extracted function
        prop = relevant[0]
        func_code = ast.unparse(prop.extracted_function)

        # Loop variable should be present but not as a parameter
        self.assertIn("for", func_code.lower())
        # Should have 'range' as builtin, not parameter
        self.assertNotIn("range", [arg.arg for arg in prop.extracted_function.args.args])

    def test_nested_for_loops(self):
        """Test nested for loops with different variable names."""
        proposals = self.engine.analyze_file(str(self.example_path))

        # Find nested loop proposals
        nested = [p for p in proposals if "nested_loops" in p.description.lower()]
        self.assertGreater(len(nested), 0, "Should find nested loop duplicates")

    def test_tuple_unpacking_in_for_loop(self):
        """Test for loops with tuple unpacking."""
        proposals = self.engine.analyze_file(str(self.example_path))

        # Find tuple unpacking proposals
        tuple_props = [p for p in proposals if "tuple_unpacking" in p.description.lower()]
        self.assertGreater(len(tuple_props), 0, "Should find tuple unpacking duplicates")


class TestComprehensionBindings(unittest.TestCase):
    """Test comprehension variable binding."""

    def setUp(self):
        # Use min_lines=1 for comprehensions since they're typically short, and
        # keep trivial single-expression helpers so the binding probes below see
        # the small extractions they exercise.
        self.engine = UnificationRefactorEngine(
            max_parameters=5,
            min_lines=1,
            parameterize_constants=True,
            skip_trivial_helpers=False,
        )
        # Capture original content to verify it's never modified
        self.example_path = get_test_example_path("bindings_comprehensions.py")
        self.original_content = self.example_path.read_text()

    def tearDown(self):
        """Verify test_examples file was never modified."""
        assert_file_not_modified(self.example_path, self.original_content)

    def test_list_comprehension_variables(self):
        """Test that list comprehension variables are recognized as bindings."""
        proposals = self.engine.analyze_file(str(self.example_path))

        # Find list comprehension proposals
        list_comp = [p for p in proposals if "list_comp" in p.description.lower()]
        self.assertGreater(len(list_comp), 0, "Should find list comprehension duplicates")

    def test_dict_comprehension_variables(self):
        """Test dict comprehension variable binding."""
        proposals = self.engine.analyze_file(str(self.example_path))

        dict_comp = [p for p in proposals if "dict_comp" in p.description.lower()]
        self.assertGreater(len(dict_comp), 0, "Should find dict comprehension duplicates")

    def test_nested_comprehensions(self):
        """Test nested comprehension variables."""
        proposals = self.engine.analyze_file(str(self.example_path))

        nested = [p for p in proposals if "nested_comp" in p.description.lower()]
        self.assertGreater(len(nested), 0, "Should find nested comprehension duplicates")

    def test_generator_expressions(self):
        """Test generator expression variables."""
        proposals = self.engine.analyze_file(str(self.example_path))

        gen_expr = [p for p in proposals if "generator_expr" in p.description.lower()]
        self.assertGreater(len(gen_expr), 0, "Should find generator expression duplicates")

    def test_set_comprehensions(self):
        """Test set comprehension variables."""
        proposals = self.engine.analyze_file(str(self.example_path))

        set_comp = [p for p in proposals if "set_comp" in p.description.lower()]
        self.assertGreater(len(set_comp), 0, "Should find set comprehension duplicates")


class TestScopingEdgeCases(unittest.TestCase):
    """Test scoping edge cases."""

    def setUp(self):
        self.engine = UnificationRefactorEngine(
            max_parameters=5, min_lines=4, parameterize_constants=True
        )
        # Capture original content to verify it's never modified
        self.example_path = get_test_example_path("scoping_edge_cases.py")
        self.original_content = self.example_path.read_text()

    def tearDown(self):
        """Verify test_examples file was never modified."""
        assert_file_not_modified(self.example_path, self.original_content)

    def test_builtin_not_parameterized(self):
        """Test that builtins like len, str are not treated as parameters."""
        proposals = self.engine.analyze_file(str(self.example_path))

        builtin_props = [p for p in proposals if "builtin_override" in p.description.lower()]
        self.assertGreater(len(builtin_props), 0, "Should find builtin test duplicates")

        # Check that len and str are not parameters
        prop = builtin_props[0]
        param_names = [arg.arg for arg in prop.extracted_function.args.args]
        self.assertNotIn("len", param_names, "len should not be a parameter")
        self.assertNotIn("str", param_names, "str should not be a parameter")
        self.assertNotIn("print", param_names, "print should not be a parameter")

    def _analyze_source(self, source: str):
        """Proposals for an inline module (the example fixture has no nested defs)."""
        with tempfile.TemporaryDirectory(prefix="towel-bindings-") as directory:
            path = Path(directory) / "module.py"
            path.write_text(source)
            return self.engine.analyze_file(str(path))

    def test_nested_functions(self):
        """A block that calls a nested function is extracted; the def stays at the site.

        The nested definition is a binding local to each site, so the helper
        receives it as a parameter instead of carrying a copy of the def.
        """
        proposals = self._analyze_source(
            "def first(items):\n"
            "    def helper(value):\n"
            "        return value * 2\n"
            "    out = []\n"
            "    for item in items:\n"
            "        out.append(helper(item))\n"
            "    return out\n"
            "\n"
            "def second(items):\n"
            "    def helper(value):\n"
            "        return value * 2\n"
            "    out = []\n"
            "    for item in items:\n"
            "        out.append(helper(item))\n"
            "    return out\n"
        )
        self.assertEqual(len(proposals), 1, [p.description for p in proposals])
        helper = proposals[0].extracted_function
        nested_defs = [node for node in ast.walk(helper) if isinstance(node, ast.FunctionDef)]
        self.assertEqual(nested_defs, [helper], "nested def must stay at the site")
        self.assertIn("helper", [arg.arg for arg in helper.args.args])
        for replacement in proposals[0].replacements:
            start, _ = replacement.line_range
            self.assertGreater(start, 3, "replacement must begin after the nested def")

    def test_lambda_expressions(self):
        """Duplicates that bind the same lambda are unified with the lambda kept intact."""
        proposals = self._analyze_source(
            "def first(items):\n"
            "    double = lambda value: value * 2\n"
            "    out = []\n"
            "    for item in items:\n"
            "        out.append(double(item))\n"
            "    return out\n"
            "\n"
            "def second(items):\n"
            "    double = lambda value: value * 2\n"
            "    out = []\n"
            "    for item in items:\n"
            "        out.append(double(item))\n"
            "    return out\n"
        )
        self.assertEqual(len(proposals), 1, [p.description for p in proposals])
        helper = proposals[0].extracted_function
        lambdas = [node for node in ast.walk(helper) if isinstance(node, ast.Lambda)]
        self.assertEqual(len(lambdas), 1)
        self.assertEqual(ast.unparse(lambdas[0]), "lambda value: value * 2")
        self.assertEqual(proposals[0].parameters_count, 0)
        ast.parse(ast.unparse(helper))

    def test_lambda_parameter_spelled_differently_is_alpha_equivalent(self):
        """A lambda whose parameter is spelled differently unifies whole.

        ``lambda value: value * 2`` and ``lambda other: other * 2`` are the same
        function: the unifier alpha-renames the parameters and the
        instantiation check renames them within their lambda, so the whole
        pair unifies, lambda included; here that makes the two bodies
        identical, and both functions call one helper.
        """
        proposals = self._analyze_source(
            "def first(items):\n"
            "    double = lambda value: value * 2\n"
            "    out = []\n"
            "    for item in items:\n"
            "        out.append(double(item))\n"
            "    return out\n"
            "\n"
            "def second(items):\n"
            "    double = lambda other: other * 2\n"
            "    out = []\n"
            "    for item in items:\n"
            "        out.append(double(item))\n"
            "    return out\n"
        )
        self.assertEqual(len(proposals), 1, [p.description for p in proposals])
        # The whole bodies match, so both become calls of the helper.
        proposal = proposals[0]
        self.assertIsNone(proposal.reused_function)
        self.assertEqual(sorted(r.line_range for r in proposal.replacements), [(2, 6), (9, 13)])


if __name__ == "__main__":
    unittest.main()
