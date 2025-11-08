import ast
import unittest

from src.towel.unification.nominal_unifier import (
    NominalUnificationContext,
    NominalVariableMatcher,
    analyze_nominal_patterns,
    build_hygienic_renames_from_unification,
)


def parse_stmts(code: str):
    import textwrap

    return ast.parse(textwrap.dedent(code).strip()).body


class TestNominalUnifier(unittest.TestCase):
    def test_binding_detection_and_sites(self):
        block0 = parse_stmts(
            """
        x = 1
        y = x + 2
        """
        )
        block1 = parse_stmts(
            """
        z = 3
        """
        )
        ctx = NominalUnificationContext(num_blocks=2)
        ctx.detect_bindings_in_blocks([block0, block1])

        self.assertTrue(ctx.is_bound_in_block("x", 0))
        self.assertTrue(ctx.is_bound_in_block("z", 1))
        self.assertIn((0, "x"), ctx.binding_sites)
        self.assertGreaterEqual(len(ctx.binding_sites[(0, "x")]), 1)

    def test_match_variables_bound_vs_free_and_free_name_eq(self):
        # block0 binds x; block1 uses y without binding
        block0 = parse_stmts("x = 1\nuse(x)")
        block1 = parse_stmts("use(y)")
        ctx = NominalUnificationContext(num_blocks=2)
        ctx.detect_bindings_in_blocks([block0, block1])
        matcher = NominalVariableMatcher(ctx)

        # bound vs free -> cannot match
        self.assertFalse(matcher.try_match_variables("x", "y", 0, 1))

        # free vs free: names must be equal
        self.assertTrue(matcher.try_match_variables("k", "k", 0, 1))
        self.assertFalse(matcher.try_match_variables("k", "m", 0, 1))

    def test_match_bound_variables_and_correspondence_updates(self):
        block0 = parse_stmts("a = 1\nuse(a)")
        block1 = parse_stmts("b = 2\nuse(b)")
        ctx = NominalUnificationContext(num_blocks=2)
        ctx.detect_bindings_in_blocks([block0, block1])
        matcher = NominalVariableMatcher(ctx)

        # Neither mapped yet -> create new correspondence with canonical 'a'
        self.assertTrue(matcher.try_match_variables("a", "b", 0, 1))
        self.assertEqual(ctx.get_original_name("a", 0), "a")
        self.assertEqual(ctx.get_original_name("a", 1), "b")
        self.assertEqual(ctx.get_canonical_name("a", 0), "a")
        self.assertEqual(ctx.get_canonical_name("b", 1), "a")

        # If both have canonicals that differ, matching fails
        # Seed a different mapping: map c<->c and d<->d across blocks
        ctx2 = NominalUnificationContext(num_blocks=2)
        ctx2.detect_bindings_in_blocks([block0, block1])
        ctx2.add_correspondence("c", 0, "c")
        ctx2.add_correspondence("c", 1, "c")
        ctx2.add_correspondence("d", 0, "d")
        ctx2.add_correspondence("d", 1, "d")
        matcher2 = NominalVariableMatcher(ctx2)
        # Attempt to match c (canonical c) with d (canonical d) -> False
        self.assertFalse(matcher2.try_match_variables("c", "d", 0, 1))

    def test_export_hygienic_renames(self):
        ctx = NominalUnificationContext(num_blocks=2)
        # Canonical is 'a'; block0 original 'a' (identity), block1 original 'b'
        ctx.add_correspondence("a", 0, "a")
        ctx.add_correspondence("a", 1, "b")
        renames = ctx.export_to_hygienic_renames()
        self.assertEqual(renames[0], {})
        self.assertEqual(renames[1], {"b": "a"})

    def test_analyze_patterns_and_build_renames(self):
        # Canonical block uses a; other block uses b; both bound then used
        canon = parse_stmts("a = f(); g(a)")
        other = parse_stmts("b = f(); g(b)")

        # analyze_nominal_patterns should record bound variables
        ctx = analyze_nominal_patterns([canon, other])
        self.assertIn("a", ctx.bound_variables[0])
        self.assertIn("b", ctx.bound_variables[1])

        # build_hygienic_renames_from_unification produces { "b": "a" } for block 1
        renames = build_hygienic_renames_from_unification(
            [canon, other], canonical_block=canon, canonical_idx=0
        )
        self.assertEqual(renames, [{}, {"b": "a"}])

    def test_get_original_name_for_missing_canonical(self):
        ctx = NominalUnificationContext(num_blocks=1)
        self.assertIsNone(ctx.get_original_name("missing", 0))

    def test_match_when_both_have_same_canonical(self):
        # Pre-seed correspondences so both vars have the same canonical
        ctx = NominalUnificationContext(num_blocks=2)
        # Ensure both variables are considered bound in their blocks
        b0 = parse_stmts("a = 1")
        b1 = parse_stmts("b = 2")
        ctx.detect_bindings_in_blocks([b0, b1])
        ctx.add_correspondence("t", 0, "a")
        ctx.add_correspondence("t", 1, "b")
        matcher = NominalVariableMatcher(ctx)
        self.assertTrue(matcher.try_match_variables("a", "b", 0, 1))

    def test_match_add_to_existing_canonical_var1_and_var2_paths(self):
        # Only var1 has a canonical
        ctx1 = NominalUnificationContext(num_blocks=2)
        ctx1.detect_bindings_in_blocks([parse_stmts("x = 1"), parse_stmts("y = 2")])
        ctx1.add_correspondence("c", 0, "x")
        matcher1 = NominalVariableMatcher(ctx1)
        self.assertTrue(matcher1.try_match_variables("x", "y", 0, 1))
        self.assertEqual(ctx1.get_canonical_name("y", 1), "c")

        # Only var2 has a canonical
        ctx2 = NominalUnificationContext(num_blocks=2)
        ctx2.detect_bindings_in_blocks([parse_stmts("x = 1"), parse_stmts("y = 2")])
        ctx2.add_correspondence("d", 1, "y")
        matcher2 = NominalVariableMatcher(ctx2)
        self.assertTrue(matcher2.try_match_variables("x", "y", 0, 1))
        self.assertEqual(ctx2.get_canonical_name("x", 0), "d")

    def test_match_name_nodes_delegates(self):
        ctx = NominalUnificationContext(num_blocks=2)
        matcher = NominalVariableMatcher(ctx)
        n1 = ast.Name(id="k")
        n2 = ast.Name(id="k")
        self.assertTrue(matcher.match_name_nodes(n1, n2, 0, 1))


if __name__ == "__main__":
    unittest.main()
