# Cross-File Observational Equivalence Test Projects

This directory contains test projects for verifying cross-file refactoring correctness through observational equivalence testing.

## Overview

Each subdirectory is a **test project** containing multiple Python files with duplicate code across nested directory structures. The `CrossFileEquivalenceTester` analyzes these projects, applies cross-file refactorings, and verifies that the refactored code preserves the original behavior.

In every project the module that borrows a helper already imports the module that hosts it: a module may gain an import only of a top-level package it already imports, since nothing else is known to ship with it (see `docs/KNOWN_LIMITATIONS.md`).

## Test Projects

### 1. simple_crossfile

**Structure**:
```
simple_crossfile/
├── user_service.py
└── admin_service.py
```

**Purpose**: Tests basic cross-file refactoring with two files in the same directory.

**Duplicates**: `validate_user_email()` and `validate_admin_email()` contain identical validation logic.

**Expected**: Tool should extract the common validation logic into a single function and have one file import it.

---

### 2. nested_structure

**Structure**:
```
nested_structure/
├── src/
│   └── data_processor.py
└── lib/
    └── report_generator.py
```

**Purpose**: Tests cross-file refactoring across different subdirectories.

**Duplicates**: `calculate_statistics()` and `calculate_report_stats()` contain identical statistics calculation logic spread across `src/` and `lib/` subdirectories.

**Expected**: Tool should identify duplicates across subdirectories and extract to a common location.

---

### 3. multi_level

**Structure**:
```
multi_level/
├── core/
│   └── services/
│       └── payment.py
├── utils/
│   └── validators.py
└── api/
    └── checkout.py
```

**Purpose**: Tests cross-file refactoring across multiple nested levels (3 directories deep).

**Duplicates**: `validate_payment_amount()`, `validate_transaction_amount()`, and `validate_checkout_amount()` all contain identical validation logic spread across `core/services/`, `utils/`, and `api/` directories.

**Expected**: Tool should identify the 3-way duplication across different nesting levels.

---

### 4. class_hierarchy

**Structure**:
```
class_hierarchy/
├── base_record.py
└── scored_record.py
```

**Purpose**: Tests a class hierarchy that spans two modules: `ScoredRecord`
in `scored_record.py` inherits from `Record` in `base_record.py`.

**Duplicates**: `Record.summary()` and `ScoredRecord.report()` compute the
same four series statistics before building different results.

**Expected**: The statistics move into one module-level helper beside the
base class, taking the receiver as its argument; the subclass's module
imports it next to the base class it already imports. The equivalence
harness constructs the subclass through its own `__init__`.

---

## Running Cross-File Tests

### Using Justfile

```bash
just test-crossfile
```

### Manual Testing

```bash
cd /path/to/towel
source .venv/bin/activate

python -c "
from tests.crossfile_equivalence_tester import CrossFileEquivalenceTester
from towel.unification.refactor_engine import UnificationRefactorEngine

engine = UnificationRefactorEngine(max_parameters=5, min_lines=4)
tester = CrossFileEquivalenceTester(engine)

# Test all projects
results = tester.test_all_projects('test_examples_crossfile', verbose=True)
print(f'\\nSuccess rate: {100 * results[\"total_passed\"] / results[\"total_proposals_tested\"]:.1f}%')
"
```

### Testing a Single Project

```bash
python -c "
from tests.crossfile_equivalence_tester import CrossFileEquivalenceTester
from towel.unification.refactor_engine import UnificationRefactorEngine

engine = UnificationRefactorEngine(max_parameters=5, min_lines=4)
tester = CrossFileEquivalenceTester(engine)

# Test specific project
passed, failed, errors = tester.test_project('test_examples_crossfile/simple_crossfile')
print(f'Passed: {passed}, Failed: {failed}')
"
```

---

## How Cross-File Testing Works

1. **Project Copying**: Creates temporary copies of the entire project directory (original and refactored versions)

2. **Cross-File Analysis**: Runs `engine.analyze_directory(recursive=True)` to find duplicates across all files in nested directories

3. **Refactoring Application**: Applies cross-file refactorings using `engine.apply_refactoring_multi_file()`, which:
   - Extracts common code to a canonical location (typically the first file)
   - Adds import statements to other files
   - Replaces original code with function calls

4. **Behavior Verification**: Imports functions from both original and refactored versions and compares their behavior with generated test inputs

5. **Import Verification**: Ensures that generated import statements work correctly and cross-module references resolve properly

---

## Current Status

**Infrastructure**: ✅ Complete

The cross-file testing framework is fully implemented and working, including:
- Test project structure
- `CrossFileEquivalenceTester` class
- Justfile integration
- Support for nested directories at any depth

**Test results**: cross-file refactoring is verified end to end.

The cross-file observational-equivalence harness (`test_crossfile_observational_equivalence.py`, `just test-crossfile`) refactors these fixtures and confirms the programs behave identically before and after. Cross-file extraction also clears the standing 91-project ecosystem check (see [../docs/PRODUCTION_READINESS.md](../docs/PRODUCTION_READINESS.md)): duplicates are identified across files and directories, a shared helper is placed in a reachable location, and each importing file receives a correct relative import.

---

## Design Principles

### Reuse Existing Infrastructure

The `CrossFileEquivalenceTester` reuses components from `AutomaticEquivalenceTester`:
- `test_all_refactored_functions()` - Behavioral comparison logic
- `generate_test_cases_for_function()` - Test input generation
- `compare_function_behavior()` - Equivalence verification

### DRY (Don't Repeat Yourself)

Instead of duplicating test logic, we:
- Extended the existing test framework
- Added cross-file specific behavior on top
- Reused test value generation and comparison

### Separation of Concerns

- **Single-file tests**: `test_examples/` - Individual files with internal duplicates
- **Cross-file tests**: `test_examples_crossfile/` - Projects with cross-file duplicates
- Each has its own tester class but shares core logic

---

## Adding New Test Projects

To add a new cross-file test project:

1. Create a subdirectory in `test_examples_crossfile/`:
   ```bash
   mkdir -p test_examples_crossfile/my_new_project/subdir1/subdir2
   ```

2. Add Python files with duplicate code across the directory structure

3. Run the tests:
   ```bash
   just test-crossfile
   ```

The tester will automatically discover and test your new project!

---

## Future Enhancements

1. **Improve Cross-File Refactoring Engine**: Fix the syntax errors and indentation issues identified by these tests

2. **More Complex Projects**: Add test projects with:
   - Circular import scenarios
   - Package-relative imports
   - `__init__.py` files

3. **Integration with CI**: Add cross-file tests to the `just ci` command

4. **Performance Optimization**: Cache refactoring results to speed up testing

---

## Summary

This cross-file testing infrastructure provides:

✅ **Comprehensive Coverage**: Tests simple flat structures, nested directories, and multi-level hierarchies

✅ **Reusable Design**: Leverages existing testing infrastructure (DRY principle)

✅ **Easy to Extend**: New projects are automatically discovered

✅ **Behavioral Verification**: Uses observational equivalence to ensure correctness

✅ **Behavior preservation**: confirms the refactored program runs identically to the original

The harness guards cross-file refactoring quality as part of the test suite.
