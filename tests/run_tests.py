#!/usr/bin/env python3
"""
Comprehensive test runner for DRY Detector.

Runs all unit tests and integration tests.
"""
import unittest
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def run_all_tests():
    """Discover and run all tests."""
    # Discover tests in the tests directory
    loader = unittest.TestLoader()
    start_dir = Path(__file__).parent
    suite = loader.discover(start_dir, pattern='test_*.py')

    # Run tests with verbose output
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Show skipped test info
    if result.skipped:
        print()
        print(f"Note: {len(result.skipped)} test(s) skipped (documented known issues)")

    # Return exit code
    return 0 if result.wasSuccessful() else 1


def main():
    """Main entry point."""
    print("=" * 70)
    print("DRY DETECTOR - COMPREHENSIVE TEST SUITE")
    print("=" * 70)
    print()

    exit_code = run_all_tests()

    print()
    print("=" * 70)
    if exit_code == 0:
        print("✓ ALL TESTS PASSED")
    else:
        print("✗ SOME TESTS FAILED")
    print("=" * 70)

    sys.exit(exit_code)


if __name__ == '__main__':
    main()
