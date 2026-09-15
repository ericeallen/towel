# How to Analyze a Whole Directory

> **Supplementary API guide.** A how-to for the `UnificationRefactorEngine` API. The maintained overview and CLI workflow are in the [README](../README.md); current limitations are in [docs/KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md).

## TL;DR

```python
from towel.unification.refactor_engine import UnificationRefactorEngine

engine = UnificationRefactorEngine(max_parameters=5, min_lines=4)

# Analyze entire directory
proposals = engine.analyze_directory("test_examples")

# Apply refactorings
for proposal in proposals:
    modified_files = engine.apply_refactoring_multi_file(proposal)
    for file_path, content in modified_files.items():
        with open(file_path, 'w') as f:
            f.write(content)
```

## Three Methods

### 1. Using analyze_directory() - RECOMMENDED

```python
from towel.unification.refactor_engine import UnificationRefactorEngine

engine = UnificationRefactorEngine(max_parameters=5, min_lines=4)

# Analyze all Python files in directory
proposals = engine.analyze_directory("src/")

# This automatically:
# - Finds all .py files
# - Skips __pycache__, venv, etc.
# - Analyzes them together (detects cross-file duplicates)
# - Returns proposals sorted by code savings
```

### 2. Using the Command-Line Script

```bash
# Interactive mode - shows proposals and asks before applying
python3 analyze_directory.py test_examples

# Preview only
python3 simple_example.py
```

### 3. Manual File List

```python
from pathlib import Path
from towel.unification.refactor_engine import UnificationRefactorEngine

# Find files manually
files = [str(f) for f in Path("src").rglob("*.py")]

engine = UnificationRefactorEngine(max_parameters=5, min_lines=4)
proposals = engine.analyze_files(files)
```

## Complete Example

```python
#!/usr/bin/env python3
from towel.unification.refactor_engine import UnificationRefactorEngine

def refactor_directory(directory):
    """Analyze and refactor all Python files in a directory."""

    # Create engine
    engine = UnificationRefactorEngine(
        max_parameters=5,  # Max params for extracted functions
        min_lines=4        # Minimum lines for code blocks
    )

    # Analyze directory
    print(f"Analyzing {directory}...")
    proposals = engine.analyze_directory(directory, recursive=True)

    if not proposals:
        print("No duplicates found!")
        return

    print(f"Found {len(proposals)} opportunities")

    # Show proposals
    for i, prop in enumerate(proposals, 1):
        print(f"\n{i}. {prop.description}")
        print(f"   Parameters: {prop.parameters_count}")

    # Apply all refactorings
    for i, proposal in enumerate(proposals, 1):
        print(f"\nApplying {i}/{len(proposals)}...")

        # Apply refactoring
        modified_files = engine.apply_refactoring_multi_file(proposal)

        # Write modified files
        for file_path, content in modified_files.items():
            with open(file_path, 'w') as f:
                f.write(content)
            print(f"  Updated: {file_path}")

    print(f"\n✓ Done! Applied {len(proposals)} refactorings")


if __name__ == "__main__":
    refactor_directory("test_examples")
```

## What Gets Analyzed?

When you call `analyze_directory("src/")`:

1. **Finds all Python files** recursively
2. **Skips** these directories:
   - Hidden directories (`.git`, `.venv`, etc.)
   - `__pycache__`
   - `venv`, `env`
   - `node_modules`

3. **Analyzes all files together** to detect:
   - Within-file duplicates
   - Cross-file duplicates

4. **Returns proposals** sorted by code savings (largest first)

## Recursive vs Non-Recursive

```python
# Recursive (default) - includes subdirectories
proposals = engine.analyze_directory("src/", recursive=True)

# Non-recursive - only files directly in src/
proposals = engine.analyze_directory("src/", recursive=False)
```

## Handling Cross-File Refactorings

The tool automatically handles cross-file refactorings correctly:

```python
# This works for both same-file and cross-file refactorings
modified_files = engine.apply_refactoring_multi_file(proposal)

# For cross-file proposals:
#   - Extracted function goes in first file (canonical location)
#   - Other files get import statements
#   - Free variables are passed as parameters

# Write all modified files
for file_path, content in modified_files.items():
    with open(file_path, 'w') as f:
        f.write(content)
```

## Configuration Options

```python
engine = UnificationRefactorEngine(
    max_parameters=5,  # Reject extractions with >5 parameters
                       # Prevents over-parameterization
                       # Lower = stricter (cleaner code)
                       # Higher = more opportunities

    min_lines=4        # Ignore code blocks < 4 lines
                       # Prevents trivial extractions
                       # Lower = more opportunities
                       # Higher = only significant duplicates
)
```

### Recommended Settings

```python
# Conservative (clean, high-quality extractions)
engine = UnificationRefactorEngine(max_parameters=2, min_lines=6)

# Balanced (default)
engine = UnificationRefactorEngine(max_parameters=5, min_lines=4)

# Aggressive (find all duplicates)
engine = UnificationRefactorEngine(max_parameters=10, min_lines=3)
```

## Examples

### Example: Refactor test_examples directory

```bash
python3 analyze_directory.py test_examples
```

Output:
```
Analyzing directory: test_examples

Found 5 Python files in test_examples

Found 7 refactoring opportunities

======================================================================
REFACTORING OPPORTUNITIES
======================================================================

1. Extract common code from process_json_data and process_xml_data
   Parameters: 1
   Function: extracted_func_3
   Cross-file: No (same file)

2. Extract common code from process_user_data and process_admin_data
   Parameters: 1
   Function: extracted_func
   Cross-file: No (same file)

...

Apply refactorings? (y/N):
```

### Example: Preview without applying

```python
from towel.unification.refactor_engine import UnificationRefactorEngine
import ast

engine = UnificationRefactorEngine(max_parameters=5, min_lines=4)
proposals = engine.analyze_directory("src/")

for proposal in proposals:
    print(f"\n{proposal.description}")
    print("Extracted function:")
    print(ast.unparse(proposal.extracted_function))
```

## Tips

1. **Start with preview** - Run `simple_example.py` first to see what would change
2. **Use version control** - Commit before refactoring so you can review/revert
3. **Iterative approach** - Run multiple times to catch nested opportunities
4. **Review parameters** - Proposals with many parameters may need manual review

## Troubleshooting

**Q: I don't see cross-file refactorings**
- Make sure you're using `analyze_directory()` or `analyze_files()` with multiple files
- Don't analyze files separately with `analyze_file()`

**Q: Too many proposals**
- Increase `min_lines` (e.g., `min_lines=6`)
- Decrease `max_parameters` (e.g., `max_parameters=3`)

**Q: No proposals found**
- Decrease `min_lines` (e.g., `min_lines=3`)
- Increase `max_parameters` (e.g., `max_parameters=10`)
- Check that files actually have duplicates

**Q: Import errors after refactoring**
- The tool generates imports based on file names
- Make sure files are in the same directory or have a common parent
- Consider using relative imports for packages
