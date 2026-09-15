# Known Issues

> **Historical.** This file is the early (2024–2025) bug log from the tool's
> observational-equivalence testing. **Every issue listed here is resolved.**
> It is kept for provenance. The variable-capture and related name-safety
> defects are fixed: every accepted proposal is now verified by instantiating
> the helper with each call site's arguments and comparing against the block it
> replaces (see [docs/ADVERSARIAL_REVIEW.md](docs/ADVERSARIAL_REVIEW.md)). For
> the current, maintained list of what the tool verifies, rejects, and leaves
> outside its model, see [docs/KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md),
> and for the current disposition see
> [docs/PRODUCTION_READINESS.md](docs/PRODUCTION_READINESS.md). The test and
> project counts below are from the period and do not reflect the current
> suite (1,257 tests) or corpus (91 projects).

This document tracks **critical bugs** discovered through observational equivalence testing. All of them have since been fixed; see the banner above.

## Fixed Issues

### 1. Sequential Refactoring Corruption ✅ FIXED

**Status**: ✅ **FIXED** - Now uses fixed-point iteration

**Fix Applied**: October 2024

**Discovery**: Found via `test_referential_transparency_observational_equivalence`

**Issue**: When applying multiple refactorings sequentially (as the `dry` script does), line numbers from later proposals become invalid after earlier refactorings change the file. This causes **unrelated functions to be corrupted** with code from completely different functions.

**Example**:

Original code:
```python
def update_mutable_state_v1(items, counter):
    """Version 1: Modifies mutable state within block."""
    counter = {"total": 0, "processed": 0}

    for item in items:
        counter["total"] += item
        counter["processed"] += 1
        result = counter["total"] / counter["processed"]
        print(f"Current average: {result}")

    return counter
```

After running `dry test_examples/referential_transparency.py output/`:
```python
def update_mutable_state_v1(items, counter):
    """Version 1: Modifies mutable state within block."""
    counter = {"total": 0, "processed": 0}

    return extracted_func_4(value.strip().upper, data, logger)
    # ERROR: value, data, logger are not defined!
```

**Impact**:
- Refactored code is **completely broken**
- Functions reference undefined variables
- Code throws `NameError` at runtime
- Corruption affects functions that weren't even in the refactoring proposals

**Root Cause**:
1. First refactoring adds extracted function at top of file
2. This shifts all subsequent line numbers down
3. Second refactoring's line numbers are now invalid
4. Refactoring engine replaces wrong code blocks
5. Unrelated functions get corrupted with incorrect code

**Evidence**:
```
Warning: Invalid line range 185-194 for /tmp/file.py (file has 158 lines)
Skipping this replacement
```

**Fix Applied**:
The tool now uses **fixed-point iteration** with two key improvements:

1. **One refactoring at a time**: Applies one refactoring, re-analyzes the code, then applies the next
2. **Extracted functions at end of file**: Places new functions at the end to prevent line number shifts

**Technical Details**:
- New methods: `refactor_to_fixed_point()` and `refactor_directory_to_fixed_point()`
- `_find_insert_position()` now returns end-of-file instead of after-imports
- `scripts/dry` updated to use fixed-point iteration by default
- Max 10 iterations per file to prevent infinite loops

**Verification**:
```python
# Test shows fix works
final_code, num_applied, descriptions = engine.refactor_to_fixed_point(file_path)
# All observational equivalence tests now pass!
```

---

### 2. Variable Shadowing in Extracted Functions ✅ FIXED

**Status**: ✅ **FIXED** - Binding occurrences now preserved

**Fix Applied**: October 2024

**Discovery**: Found during testing of `update_mutable_state` functions

**Issue**: When extracting code containing loop variables, the extractor was replacing ALL occurrences of variable names with parameters, including binding occurrences like loop variable definitions. This caused parameter names to shadow loop variables.

**Example**:

Before fix:
```python
def extracted_func(param_0, items):
    counter = {'total': 0, 'processed': 0}
    for param_0 in items:  # ❌ BUG: param_0 shadows parameter!
        counter['total'] += param_0
        counter['processed'] += 1
        result = counter['total'] / counter['processed']
        print(f'Current average: {result}')
    return counter
```

After fix:
```python
def extracted_func(param_0, items):
    counter = {'total': 0, 'processed': 0}
    for item in items:           # ✅ Loop variable preserved
        counter['total'] += param_0  # ✅ Usage parameterized
        counter['processed'] += 1
        result = counter['total'] / counter['processed']
        print(f'Current average: {result}')
    return counter
```

**Impact**:
- Extracted function parameter was unused (shadowed by loop variable)
- Loop variable had wrong name (parameter name instead of original)
- Confusing and semantically incorrect code

**Root Cause**:
The `ParameterSubstituter.visit()` method in `extractor.py` was replacing ALL Name nodes that matched parameterized expressions, without distinguishing between:
- **Binding occurrences** (Store/Del context): `for item in items:`, `x = 5`
- **Usage occurrences** (Load context): `result = x + 1`

Only usage occurrences should be parameterized; binding occurrences define the variable and must be preserved.

**Fix Applied**:
Added context check in `extractor.py` line 277:
```python
if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
    # This is a binding occurrence (Store or Del context)
    # Don't replace it - return as-is
    return node
```

**Technical Details**:
- Python AST distinguishes contexts: `ast.Load` (usage), `ast.Store` (binding), `ast.Del` (deletion)
- In `for item in items:`, `item` has `Store` context
- In `counter['total'] += item`, `item` has `Load` context
- The fix checks context before replacing Name nodes with parameters

**Verification**:
```python
# Debug output shows fix works:
✅ NO SHADOWING: Parameters and loop variables are distinct
Loop variable: 'item'
Parameter: 'param_0'
```

All 125 tests continue to pass after this fix.

---

## Formerly Outstanding Bugs (all since fixed)

### 3. Variable Capture Bug

**Status**: ✅ FIXED - lifted parameters keep each call site's own names, and the instantiation check would reject any proposal that did not

**Discovery**: Found via `test_example1_simple_observational_equivalence`

**Issue**: When extracting duplicate code into a function, the unifier incorrectly captures variable names from the first occurrence and uses them in the second occurrence, even when the variable names are different.

**Example**:

```python
# Original code
def process_user_data(user_id):
    user = fetch_user(user_id)
    validate(user)  # Uses 'user'

def process_admin_data(admin_id):
    admin = fetch_admin(admin_id)
    validate(admin)  # Uses 'admin'
```

After refactoring:
```python
def extracted_func(param):
    validate(param)

def process_user_data(user_id):
    user = fetch_user(user_id)
    extracted_func(user)  # Correct

def process_admin_data(admin_id):
    admin = fetch_admin(admin_id)
    extracted_func(user)  # BUG: 'user' is not defined here!
```

**Impact**:
- Refactored code throws `NameError: name 'user' is not defined`
- Happens consistently across different test cases
- Affects both simple and complex refactorings

**Root Cause**:
- The unifier captures variable names from the template (first function)
- When generating the call for the second function, it uses those captured names
- Should be using the actual variable names from the second function's scope

---

## Test Coverage

**Total Tests**: 125
- **Passing**: 122
- **Skipped**: 3 (documented known issues)

### Skipped Tests (Known Bugs)

1. `test_example1_simple_observational_equivalence` - Variable capture bug
2. `test_simple_arithmetic_observational_equivalence` - Variable capture bug
3. `test_referential_transparency_observational_equivalence` - Sequential corruption bug

## Implications

**2 of 3 critical bugs have been fixed** (sequential corruption ✅, shadowing ✅). One critical bug remains:

1. ✅ **Sequential corruption bug**: FIXED - Now uses fixed-point iteration
2. ✅ **Variable shadowing bug**: FIXED - Binding occurrences now preserved
3. ✅ **Variable capture bug**: FIXED - each call site passes its own names; the per-proposal instantiation check gates against any capture

All three bugs listed here are fixed. The current disposition is in [docs/PRODUCTION_READINESS.md](docs/PRODUCTION_READINESS.md): usable as a reviewed refactoring tool, not for unattended use.

## Recommendations

### Immediate

1. Historical note: at the time, production use was advised against until the variable capture bug was fixed; it has since been fixed and verified
2. **Always run observational equivalence tests** on output
3. **Review generated function calls** for incorrect variable names

### Remaining Work

1. **CRITICAL**: Fix variable capture bug
   - Makes refactored code incorrect (uses wrong variable names in calls)
   - Easier to detect (immediate NameError)
   - Less dangerous than silent corruption
   - Only remaining critical bug

### Testing

The observational equivalence testing framework successfully catches these bugs:

```bash
# Run observational tests
just test-observational

# All tests pass but 3 are skipped with documented reasons
Ran 10 tests in 0.018s
OK (skipped=3)
```

## Value of Observational Equivalence Testing

These critical bugs were **discovered by the observational equivalence testing framework**, demonstrating its value:

1. **Found bugs that unit tests missed** - The refactoring engine unit tests all pass
2. **Showed actual runtime behavior** - Executed refactored code with real inputs
3. **Provided clear diagnostics** - Showed exactly what broke and why
4. **Prevented production disasters** - Caught bugs before production use

Without observational equivalence testing, these bugs would have gone undetected until users ran the refactored code in production.

## See Also

- `tests/OBSERVATIONAL_EQUIVALENCE.md` - Testing framework documentation
- `tests/test_observational_equivalence.py` - Test implementation
- `test_examples/referential_transparency.py` - Comprehensive test cases
