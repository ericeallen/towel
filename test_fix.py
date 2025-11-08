#!/usr/bin/env python3
"""Test if the validation fix allows example1_simple.py to generate proposals."""

from src.towel.unification.refactor_engine import UnificationRefactorEngine
import ast

engine = UnificationRefactorEngine(max_parameters=5, min_lines=4)
proposals = engine.analyze_file("test_examples/example1_simple.py")

print(f"Found {len(proposals)} proposals in example1_simple.py")
if proposals:
    print("\nProposal details:")
    for i, prop in enumerate(proposals, 1):
        print(f"\nProposal {i}:")
        # Extracted function name and parameter count
        fn = prop.extracted_function
        print(f"  Extracted function: {fn.name}")
        print(f"  Parameter count: {prop.parameters_count}")
        # Replacement ranges
        ranges = []
        for r in prop.replacements:
            # Each replacement tuple has at least (line_range, node,...)
            if r and isinstance(r[0], tuple):
                ranges.append(r[0])
        print(f"  Replacement line ranges: {ranges}")
        # Return variables (if any)
        if prop.return_variables:
            print(f"  Returns variables: {prop.return_variables}")
        # Basic structural sanity
        assert isinstance(fn, ast.FunctionDef), "extracted_function should be a FunctionDef"
else:
    print("\nNO PROPOSALS FOUND - validation may still be too strict")
