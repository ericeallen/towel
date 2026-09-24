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
