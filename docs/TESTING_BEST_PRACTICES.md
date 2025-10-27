# Testing Best Practices

## Overview

When writing tests for the DRY detector, it's crucial to **never modify the original test files**. This ensures:
- Tests can be run repeatedly without corruption
- Test examples remain pristine for demonstration
- No need to manually reset files between test runs
- Automatic cleanup prevents clutter

## The Problem

Many refactoring tools modify files in-place. If tests write directly to `test_examples/`, the test files get corrupted and subsequent runs fail or produce incorrect results.

### Example of BAD practice:

```python
# ❌ DON'T DO THIS
engine = UnificationRefactorEngine()
proposals = engine.analyze_file("test_examples/example1_simple.py")

if proposals:
    # This CORRUPTS the original test file!
    refactored = engine.apply_refactoring("test_examples/example1_simple.py", proposals[0])
    with open("test_examples/example1_simple.py", 'w') as f:
        f.write(refactored)
```

### Problems with this approach:
- Original test file is now mangled
- Running the test again will produce different (wrong) results
- Need to manually run `just reset-examples` after every test
- If test crashes, file corruption persists

## The Solution: Temporary Files

Always work on **temporary copies** of test files using the `test_helpers` module.

### Example of GOOD practice:

```python
# ✓ DO THIS
from test_helpers import temporary_test_file

with temporary_test_file("test_examples/example1_simple.py") as temp_file:
    engine = UnificationRefactorEngine()
    proposals = engine.analyze_file(temp_file)

    if proposals:
        # Works on the COPY, not the original
        refactored = engine.apply_refactoring(temp_file, proposals[0])
        with open(temp_file, 'w') as f:
            f.write(refactored)

        # Do assertions, validation, etc.
        assert "extracted_func" in refactored

# temp_file is automatically deleted here
# Original test_examples/example1_simple.py is UNCHANGED
```

## Test Helper Functions

The `test_helpers.py` module provides several utilities:

### 1. `temporary_test_file(source_file)`

Creates a temporary copy of a single file.

```python
from test_helpers import temporary_test_file

with temporary_test_file("test_examples/example1_simple.py") as temp_file:
    # Work with temp_file
    # Original is safe
    pass
# Automatic cleanup
```

### 2. `temporary_test_files(source_dir, files)`

Creates temporary copies of multiple files or an entire directory.

```python
from test_helpers import temporary_test_files

# Copy entire directory
with temporary_test_files("test_examples") as temp_dir:
    engine.analyze_directory(temp_dir)
    # ...

# Copy specific files only
with temporary_test_files(files=["example3_file1.py", "example3_file2.py"]) as temp_dir:
    # Work with copies in temp_dir
    pass
```

### 3. `verify_file_unchanged(file_path, original_content)`

Verify a file hasn't been modified (useful for testing).

```python
from test_helpers import verify_file_unchanged

# Read original
with open("test_examples/example1_simple.py", 'r') as f:
    original = f.read()

# ... do some operations ...

# Verify it's unchanged
assert verify_file_unchanged("test_examples/example1_simple.py", original)
```

### 4. `reset_test_examples()`

Programmatically reset test examples (equivalent to `just reset-examples`).

```python
from test_helpers import reset_test_examples

reset_test_examples()  # Restores all test files from .templates/
```

### 5. `read_template(template_name)`

Read the original template file.

```python
from test_helpers import read_template

original_content = read_template("example1_simple.py")
```

## Complete Example

See `test_with_temp_files.py` for a comprehensive example that demonstrates all best practices:

```python
from test_helpers import temporary_test_files, verify_file_unchanged
from dry_detector.unification.refactor_engine import UnificationRefactorEngine

def test_directory_refactoring():
    """Test with temporary copies."""
    source_dir = "test_examples"

    # Read originals
    original_contents = {}
    for file_path in Path(source_dir).glob("*.py"):
        with open(file_path, 'r') as f:
            original_contents[str(file_path)] = f.read()

    # Work on copies
    with temporary_test_files(source_dir) as temp_dir:
        engine = UnificationRefactorEngine()
        proposals = engine.analyze_directory(temp_dir)

        # Apply refactorings to temp_dir, not originals
        # ...

    # Verify originals unchanged
    for file_path, original in original_contents.items():
        assert verify_file_unchanged(file_path, original)
```

## When to Use Each Approach

### Use `temporary_test_file()` when:
- Testing a single file
- Running quick unit tests
- Don't need cross-file analysis

### Use `temporary_test_files()` when:
- Testing directory-wide analysis
- Testing cross-file refactoring
- Need to work with multiple files

### Use `/tmp/` directly when:
- Generating output files that don't correspond to test inputs
- Creating intermediate files for multi-pass refactoring
- See: `test_iterative.py`

## Template Files

All original test examples are stored in `.templates/`:

```
.templates/
├── example1_simple.py      # Simple same-file duplicates
├── example2_classes.py     # Duplicates in class methods
├── example3_file1.py       # Cross-file duplicate (file 1)
├── example3_file2.py       # Cross-file duplicate (file 2)
└── example4_complex.py     # Complex duplicates with loops
```

### Resetting Test Files

If test files get corrupted (e.g., from running user-facing scripts), reset them:

```bash
# Command line
just reset-examples

# Or programmatically
from test_helpers import reset_test_examples
reset_test_examples()
```

## Running Tests

```bash
# Run all tests (including temp file tests)
just test

# Run specific test
python3 test_with_temp_files.py

# Run comprehensive test suite
just test-all
```

## Summary

| Approach | Safe? | Use Case |
|----------|-------|----------|
| Write to `test_examples/` directly | ❌ Never | None - user scripts only |
| Write to `/tmp/` | ✓ Yes | Intermediate files, iterative tests |
| Use `temporary_test_file()` | ✓ Yes | Single file tests |
| Use `temporary_test_files()` | ✓ Yes | Directory/multi-file tests |

**Golden Rule**: If your test writes files, use temporary copies. Original test examples should NEVER be modified by automated tests.
