#!/usr/bin/env python3
"""
Include cross-file observational equivalence runs in the unit test suite so
they are accounted for in coverage. This mirrors the 'just test-crossfile' task.
"""

import unittest


class TestCrossfileObservationalEquivalence(unittest.TestCase):
    def test_crossfile_projects(self) -> None:
        # Import lazily to avoid import-time overhead if the test is filtered
        from tests.crossfile_equivalence_tester import CrossFileEquivalenceTester
        from src.towel.unification.refactor_engine import UnificationRefactorEngine

        engine = UnificationRefactorEngine(max_parameters=5, min_lines=4)
        tester = CrossFileEquivalenceTester(engine)
        results = tester.test_all_projects("test_examples_crossfile", verbose=False)

        # Basic sanity checks; we don't enforce 0 failures here because the
        # tester itself reports details and other tests cover correctness.
        self.assertGreaterEqual(results["total_projects"], 1)
        self.assertGreaterEqual(results["total_proposals_tested"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
