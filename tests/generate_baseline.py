#!/usr/bin/env python3
"""
Generate baseline expected output for regression testing.

This script:
1. Creates expected output directories
2. Runs refactorings on all test examples
3. Saves the refactored output as baseline for future comparison

WARNING: This script should NOT be run directly!
Use: just regenerate-baseline
"""
import sys
import shutil
import argparse
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.towel.unification.refactor_engine import UnificationRefactorEngine
import tempfile


def generate_single_file_baseline(engine, test_examples_dir: Path, output_dir: Path):
    """
    Generate baseline for single-file refactorings.

    Args:
        engine: UnificationRefactorEngine instance
        test_examples_dir: Directory containing test example files
        output_dir: Directory to save refactored output
    """
    print(f"\nGenerating single-file baseline from {test_examples_dir}...")

    # Get all Python files
    python_files = [
        f for f in test_examples_dir.glob("*.py") if f.is_file() and not f.name.startswith("_")
    ]

    for py_file in sorted(python_files):
        print(f"  Processing {py_file.name}...")

        # Apply refactorings to fixed point using a temporary copy to avoid
        # modifying files under test_examples.
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".py", delete=False) as tmp:
            tmp.write(py_file.read_text())
            tmp.flush()
            tmp_path = Path(tmp.name)

        try:
            final_code, num_applied, descriptions = engine.refactor_to_fixed_point(str(tmp_path))
            if num_applied > 0:
                print(f"    Applied {num_applied} refactoring(s) to fixed point")
                for i, d in enumerate(descriptions[:3], 1):
                    print(f"      {i}. {d}")
            else:
                print(f"    No proposals found (fixed point)")
        except Exception as e:
            print(f"    Fixed-point refactoring failed: {e}")
            final_code = py_file.read_text()
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass

        # Save final code to output directory
        output_file = output_dir / py_file.name
        output_file.write_text(final_code)
        print(f"    Saved to {output_file}")


def generate_crossfile_baseline(engine, crossfile_dir: Path, output_dir: Path):
    """
    Generate baseline for cross-file refactorings.

    Args:
        engine: UnificationRefactorEngine instance
        crossfile_dir: Directory containing cross-file test projects
        output_dir: Directory to save refactored output
    """
    print(f"\nGenerating cross-file baseline from {crossfile_dir}...")

    # Get all project directories
    project_dirs = [d for d in crossfile_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]

    for project_dir in sorted(project_dirs):
        print(f"  Processing project {project_dir.name}...")

        # Apply refactorings to fixed point across the project into the output directory
        project_output = output_dir / project_dir.name
        try:
            results = engine.refactor_directory_to_fixed_point(
                str(project_dir), str(project_output), max_iterations=10
            )
            total = sum(v[0] for v in results.values()) if results else 0
            print(f"    Applied {total} refactoring(s) across project to fixed point")
        except Exception as e:
            print(f"    Fixed-point cross-file refactoring failed: {e}")


def main():
    """Generate all baseline outputs."""
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="Generate baseline expected output for regression testing.",
        epilog="WARNING: This script should NOT be run directly! Use: just regenerate-baseline",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Confirm that you want to regenerate the baseline (required)",
    )
    args = parser.parse_args()

    # Require --confirm flag
    if not args.confirm:
        print("\n" + "=" * 70)
        print("ERROR: BASELINE REGENERATION REQUIRES CONFIRMATION")
        print("=" * 70)
        print("\nThis script will OVERWRITE all baseline expected output files!")
        print("This should only be done when:")
        print("  1. You have made INTENTIONAL changes to the refactoring engine")
        print("  2. You have VERIFIED the new output is CORRECT")
        print("  3. All observational equivalence tests are PASSING")
        print("\nDo NOT run this script directly!")
        print("Use the safe wrapper: just regenerate-baseline")
        print("\nIf you really need to run this directly, use:")
        print("  python tests/generate_baseline.py --confirm")
        print("=" * 70 + "\n")
        sys.exit(1)

    print("=" * 70)
    print("GENERATING BASELINE EXPECTED OUTPUT")
    print("=" * 70)

    # Create engine
    engine = UnificationRefactorEngine(max_parameters=5, min_lines=3)

    # Define directories
    test_examples = project_root / "test_examples"
    crossfile_examples = project_root / "test_examples_crossfile"

    output_single = project_root / "test_examples_expected_output"
    output_crossfile = project_root / "test_examples_crossfile_expected_output"

    # Create output directories
    output_single.mkdir(exist_ok=True)
    output_crossfile.mkdir(exist_ok=True)

    # Generate baselines
    if test_examples.exists():
        generate_single_file_baseline(engine, test_examples, output_single)
    else:
        print(f"\nWarning: {test_examples} not found")

    if crossfile_examples.exists():
        generate_crossfile_baseline(engine, crossfile_examples, output_crossfile)
    else:
        print(f"\nWarning: {crossfile_examples} not found")

    print("\n" + "=" * 70)
    print("BASELINE GENERATION COMPLETE")
    print("=" * 70)
    print(f"\nSingle-file baseline: {output_single}")
    print(f"Cross-file baseline: {output_crossfile}")


if __name__ == "__main__":
    main()
