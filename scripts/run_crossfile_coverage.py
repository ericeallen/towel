#!/usr/bin/env python3
"""
Run cross-file observational equivalence tests to contribute to coverage.
This is intended to be executed under coverage run --append.
"""
from pathlib import Path
import sys

# Ensure project root is on sys.path so tests/* modules can be imported BEFORE local imports
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.crossfile_equivalence_tester import CrossFileEquivalenceTester  # noqa: E402
from src.towel.unification.refactor_engine import UnificationRefactorEngine  # noqa: E402


def main() -> None:
    engine = UnificationRefactorEngine(max_parameters=5, min_lines=4)
    tester = CrossFileEquivalenceTester(engine)
    # Run in quiet mode to avoid excessive output; still exercises code paths
    tester.test_all_projects("test_examples_crossfile", verbose=False)


if __name__ == "__main__":
    main()
