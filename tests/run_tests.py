#!/usr/bin/env python3
"""Wrapper to execute the full pytest suite used by tooling and Just recipes."""

import sys
from pathlib import Path

import pytest

# Ensure project root is on sys.path for direct invocations.
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def run_all_tests() -> int:
    """Run the full pytest suite and propagate its exit code."""
    return pytest.main(["tests"])


def main():
    """Main entry point."""
    print("=" * 70)
    print("TOWEL - COMPREHENSIVE TEST SUITE")
    print("=" * 70)
    print()

    exit_code = run_all_tests()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
