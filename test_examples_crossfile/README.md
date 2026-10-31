# Cross-File Observational Equivalence Test Projects

This directory contains test projects for verifying cross-file refactoring correctness through observational equivalence testing.

## Overview

Each subdirectory is a **test project** containing multiple Python files with duplicate code across nested directory structures. The `CrossFileEquivalenceTester` analyzes these projects, applies cross-file refactorings, and verifies that the refactored code preserves the original behavior.

Cross-module sharing is opt-in (`cross_module_helpers=True`). These fixtures
exercise caller-side builtin lookup with `parameterize_builtins=True`, matching
the test harness; default cross-module builtin refusal is tested separately.
A host must also satisfy the existing import, packaging and initialization
checks described in [the cross-module guide](../docs/CROSS_MODULE.md).

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

Run from the repository root in the pinned development environment:

```bash
uv run --frozen python - <<'PYTHON'
from tests.crossfile_equivalence_tester import CrossFileEquivalenceTester
from towel.unification.refactor_engine import UnificationRefactorEngine

engine = UnificationRefactorEngine(
    max_parameters=5,
    min_lines=4,
    cross_module_helpers=True,
    parameterize_builtins=True,
)
tester = CrossFileEquivalenceTester(engine)
results = tester.test_all_projects("test_examples_crossfile", verbose=True)
assert results["total_proposals_tested"] > 0, results
assert results["total_failed"] == 0, results
for project in results["project_results"].values():
    assert not project["errors"], project
print(results)
PYTHON
```

To test one project with the same engine, replace the `test_all_projects` call
and assertions with:

```python
passed, failed, errors = tester.test_project("test_examples_crossfile/simple_crossfile")
assert passed > 0, (passed, failed, errors)
assert failed == 0 and not errors, (passed, failed, errors)
```

---

## How Cross-File Testing Works

1. **Project Copying**: Creates temporary copies of the entire project directory (original and refactored versions)

2. **Cross-File Analysis**: Runs `engine.analyze_directory(recursive=True)` to find duplicates across all files in nested directories

3. **Refactoring Application**: Applies cross-file refactorings using `engine.apply_refactoring_multi_file()`, which:
   - Selects a host that passes placement and import checks
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

The cross-file observational-equivalence harness
(`tests/test_crossfile_observational_equivalence.py`, `just test-crossfile`) is
collected by the full test suite and CI. It applies accepted proposals and
compares affected callable observations on generated inputs in isolated import
contexts. It requires actual proposals and no failures or unverified target
errors. This finite fixture check is distinct from the pinned ecosystem runs
recorded in [production readiness](../docs/PRODUCTION_READINESS.md); it is not
a universal equivalence proof or a standing ecosystem result.

---

## Design Principles

### Reuse Existing Infrastructure

The tester reuses `prepare_target_cases` for generated inputs and
`execute_callable` / `compare_executions` for observations. It resolves affected
functions from replacement sites in the original source, then isolates project
imports while executing the original and changed callables. The harness is
serial because `sys.path` and `sys.modules` are process-global.

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

## Extending coverage

Add fixtures for a specific import or placement boundary, and require a useful
accepted proposal where the case is intended to demonstrate a capability.
Conservative controls should preserve the original source and observations.
Circular imports, package-relative imports and package initialization also have
focused regression families under `tests/`; these four example projects are
only one layer of that coverage.
