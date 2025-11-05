#!/usr/bin/env python3
"""Test if the validation fix allows example1_simple.py to generate proposals."""

from src.towel.unification.refactor_engine import UnificationRefactorEngine

engine = UnificationRefactorEngine(max_parameters=5, min_lines=4)
proposals = engine.analyze_file("test_examples/example1_simple.py")

print(f"Found {len(proposals)} proposals in example1_simple.py")
if proposals:
    print("\nProposal details:")
    for i, prop in enumerate(proposals, 1):
        print(f"\nProposal {i}:")
        print(f"  Functions: {[loc.function_name for loc in prop.locations]}")
        print(f"  Lines: {[(loc.start_line, loc.end_line) for loc in prop.locations]}")
else:
    print("\nNO PROPOSALS FOUND - validation may still be too strict")
