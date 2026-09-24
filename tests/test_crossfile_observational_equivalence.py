#!/usr/bin/env python3
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
Include cross-file observational equivalence runs in the unit test suite so
they are accounted for in coverage. This mirrors the 'just test-crossfile' task.
"""

import unittest


class TestCrossfileObservationalEquivalence(unittest.TestCase):
    def test_crossfile_projects(self) -> None:
        # Import lazily to avoid import-time overhead if the test is filtered
        from tests.crossfile_equivalence_tester import CrossFileEquivalenceTester
        from towel.unification.refactor_engine import UnificationRefactorEngine

        engine = UnificationRefactorEngine(max_parameters=5, min_lines=4, cross_module_helpers=True)
        tester = CrossFileEquivalenceTester(engine)
        results = tester.test_all_projects("test_examples_crossfile", verbose=False)

        self.assertGreaterEqual(results["total_projects"], 1)
        self.assertGreaterEqual(results["total_proposals_tested"], 1)
        self.assertEqual(results["total_failed"], 0, results["project_results"])
        for project in results["project_results"].values():
            self.assertEqual(project["errors"], [], project)


if __name__ == "__main__":
    unittest.main(verbosity=2)
