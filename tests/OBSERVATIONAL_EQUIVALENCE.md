# Observational Equivalence Testing

## Overview

The observational equivalence testing framework ensures that refactored code behaves **identically** to the original code. This is crucial for validating that refactorings preserve program semantics and don't introduce subtle bugs.

## How It Works

The framework:

1. **Executes original functions** with test inputs and captures:
   - Return values
   - Exceptions (type and message)
   - stdout/stderr output

2. **Executes refactored functions** with the same inputs and captures the same information

3. **Compares results** to ensure perfect equivalence

4. **Reports differences** with detailed information about what changed

## Architecture

### Core Components

- **`FunctionExecutionResult`**: Captures the complete result of a function execution
- **`execute_function()`**: Safely executes Python code in an isolated namespace
- **`compare_function_behavior()`**: Compares original vs refactored behavior across test cases

### Test Classes

- **`TestSemanticEquivalence`**: Integration tests that verify real refactoring examples
- **`TestExecutionFramework`**: Unit tests for the execution framework itself

## Usage

### Running Observational Tests

```bash
# Run all observational equivalence tests
just test-observational

# Run observational tests as part of comprehensive suite
just test

# Run only the automatic comprehensive tests
python -m unittest tests.test_observational_equivalence.TestAutomaticObservationalEquivalence -v
```

### Automatic Testing Framework

The framework now includes **automatic comprehensive testing** that tests ALL refactored functions from ALL example files without manual configuration:

```python
from tests.automatic_equivalence_tester import AutomaticEquivalenceTester
from dry_detector.unification.refactor_engine import UnificationRefactorEngine

# Create tester
engine = UnificationRefactorEngine(max_parameters=5, min_lines=4)
tester = AutomaticEquivalenceTester(engine)

# Test all example files automatically
results = tester.test_all_examples('test_examples')

# Results show:
# - Total files tested: 18
# - Total proposals tested: 64
# - Automatic test input generation based on function signatures
# - Comprehensive observational equivalence testing
```

**How it works:**
1. **Extracts function names** from refactoring proposal descriptions
2. **Analyzes function signatures** using AST to understand parameter usage
3. **Generates test inputs automatically** by:
   - Detecting tuple unpacking patterns: `for a, b in pairs:` → generates `[('a', 1), ('b', 2)]`
   - Detecting dictionary access: `data['key']` → generates `{'key': 'value'}`
   - Using type annotations and parameter name heuristics
4. **Tests observational equivalence** for all refactored functions

### Writing New Manual Tests

For specific test cases, you can still write manual tests:

```python
from tests.test_observational_equivalence import compare_function_behavior

def test_my_refactoring(self):
    """Test that my refactoring preserves semantics."""
    # Get original and refactored code
    original_code = "..."
    refactored_code = "..."

    # Define test cases: list of (args, kwargs) tuples
    test_cases = [
        ((arg1, arg2), {}),
        ((arg3, arg4), {'key': 'value'}),
    ]

    # Compare behavior
    all_passed, differences = compare_function_behavior(
        original_code,
        refactored_code,
        'function_name',
        test_cases
    )

    # Assert equivalence
    if not all_passed:
        self.fail("\n".join(differences))
```

## Test Results

### Automatic Comprehensive Testing Results

The automatic testing framework tests **64 refactoring proposals across 18 example files**:

**Summary:**
- **Total files tested:** 18
- **Total proposals tested:** 64
- **Passed:** 29 proposals (45%)
- **Failed:** 35 proposals (55%)

**Files with all tests passing:**
- `complex_expressions.py` - 5 proposals passed
- `example4_complex.py` - 3 proposals passed
- `bindings_comprehensions.py` - 0 proposals (no duplicates found)

**Files with failures:**
- `example1_simple.py` - 0 passed, 3 failed (variable capture bug)
- `referential_transparency.py` - 0 passed, 5 failed (multiple bugs)
- `functional_patterns.py` - 0 passed, 5 failed
- `hygienic_naming.py` - 0 passed, 5 failed
- `scoping_edge_cases.py` - 1 passed, 4 failed
- And 7 more files with partial failures

The failures document real bugs in the refactoring engine that are tracked in `KNOWN_ISSUES.md`.

### ✓ Passing Manual Tests

Manual tests that verify observational equivalence for specific scenarios:

- **`test_bindings_for_loops_observational_equivalence`**: For loop variable bindings
- **`test_return_values_observational_equivalence`**: Early returns and return value propagation
- **`test_execute_simple_function`**: Basic execution framework
- **`test_compare_identical_functions`**: Framework comparison logic
- **`test_all_examples_automatically`**: Comprehensive automatic testing (passes with documented failures)
- **`test_automatic_single_file`**: Single file automatic testing

### ⚠ Known Issues (Skipped Tests)

Tests that document **critical bugs** found by observational equivalence testing:

#### 1. Variable Capture Bug

- **`test_example1_simple_observational_equivalence`**: Variable capture bug
  - **Issue**: Refactored code uses wrong variable name (`user` instead of `admin`)
  - **Example**: `extracted_func_2(admin, user)` → `NameError: name 'user' is not defined`
  - **Impact**: Refactored code throws NameError at runtime

- **`test_simple_arithmetic_observational_equivalence`**: Same variable capture issue
  - **Issue**: Parameter names from first function leak into second function
  - **Impact**: Identical to above

#### 2. Sequential Refactoring Corruption Bug (CRITICAL)

- **`test_referential_transparency_observational_equivalence`**: Sequential refactorings corrupt code
  - **Issue**: Applying multiple refactorings sequentially causes line number misalignment
  - **Example**: Function `update_mutable_state_v1` gets corrupted from:
    ```python
    def update_mutable_state_v1(items, counter):
        counter = {"total": 0, "processed": 0}
        for item in items:
            counter["total"] += item
            # ... correct code ...
    ```
    To:
    ```python
    def update_mutable_state_v1(items, counter):
        counter = {"total": 0, "processed": 0}
        return extracted_func_4(value.strip().upper, data, logger)
        # References undefined: value, data, logger!
    ```
  - **Impact**: **CRITICAL** - Corrupts unrelated functions, making them completely broken
  - **Root Cause**: When refactoring changes file length, line numbers in subsequent proposals become invalid
  - **Warnings Seen**: `Warning: Invalid line range 185-194 for ... (file has 158 lines)`

## Value Demonstrated

The observational equivalence testing framework has already proven its value by:

1. **Finding real bugs** in the refactoring engine that would have gone undetected
2. **Providing detailed diagnostics** showing exactly how behavior differs
3. **Documenting expected behavior** through executable specifications
4. **Building confidence** in refactorings that do pass

## Example Output

### When a test fails:

```
Semantic equivalence failed for process_admin_data:
Test case 0 with args=(123,), kwargs={}:
  Original:   FunctionExecutionResult(return_value={'id': 123, 'name': 'Admin', ...})
  Refactored: FunctionExecutionResult(exception=NameError: name 'user' is not defined)
```

This immediately shows:
- Which function failed
- What inputs were used
- What the original function returned
- What error the refactored function raised

## Automatic Test Input Generation

The framework uses sophisticated analysis to generate appropriate test inputs:

### Parameter Usage Analysis

The framework analyzes the function body AST to understand how parameters are used:

```python
def tuple_unpacking_a(pairs):
    result = {}
    for key, value in pairs:  # ← Detects tuple unpacking!
        result[key] = value * 2
    return result

# Automatically generates test inputs like:
# [('a', 1), ('b', 2)]
# [('x', 'foo'), ('y', 'bar')]
# [(1, 10), (2, 20)]
```

### Detection Strategies

1. **Tuple Unpacking Detection**: `for a, b in param:` → generates lists of tuples
2. **Dictionary Access Detection**: `param['key']` or `param.get()` → generates dicts with keys
3. **List Iteration Detection**: `for item in param:` → generates lists
4. **Type Annotations**: Uses type hints if available
5. **Name Heuristics**: Parameter names like `items`, `pairs`, `data` guide generation

### Test Input Examples

```python
# Parameter: pairs (detected tuple unpacking)
[],
[('a', 1), ('b', 2)],
[('x', 'foo'), ('y', 'bar')],
[(1, 10), (2, 20)]

# Parameter: data (detected dict access)
{},
{'key': 'value'},
{'id': 123, 'name': 'test'}

# Parameter: items (name heuristic)
[],
[1, 2, 3],
[10, 20, 30]
```

## Future Enhancements

Potential improvements to the framework:

1. ~~**Automatic test case generation** using AST analysis~~ ✅ **IMPLEMENTED**
2. **Property-based testing** using Hypothesis for exhaustive testing
3. **Side effect tracking** for mutable state changes
4. **Performance comparison** to ensure refactorings don't degrade performance
5. **Coverage-guided fuzzing** to find edge cases
6. **State comparison** for objects and data structures

## Integration with CI/CD

The observational equivalence tests run as part of the standard test suite:

```bash
# All tests including observational equivalence
just test

# Or specifically
just test-observational
```

Tests are automatically discovered and run by the test runner. Skipped tests (known issues) don't fail the build but are reported for tracking.

## Best Practices

1. **Always write observational tests** for new refactoring patterns
2. **Use representative test inputs** that cover edge cases
3. **Document known issues** with `skipTest()` and detailed docstrings
4. **Compare return values AND exceptions** to ensure complete equivalence
5. **Test both functions** in a duplicate pair, not just one

## Technical Details

### Execution Isolation

Each function execution uses a fresh Python namespace created with `exec()`. This ensures:
- No state pollution between tests
- Clean function definitions
- Isolated exception handling

### Exception Comparison

Exceptions are compared by:
- Exception type (e.g., `ValueError`, `NameError`)
- Exception message (string comparison)

This catches cases where refactored code raises different exceptions than the original.

### Output Capture

stdout and stderr are captured using `io.StringIO` and `contextlib.redirect_stdout/stderr`. This allows testing functions with print statements or logging.

## Related Files

- **`tests/test_observational_equivalence.py`**: Main test file
- **`tests/test_helpers.py`**: Shared test utilities
- **`tests/run_tests.py`**: Comprehensive test runner
- **`justfile`**: `test-observational` target

## Contributing

When adding new refactoring capabilities:

1. Add observational equivalence tests
2. Ensure tests pass or document known issues with `skipTest()`
3. Add test cases that exercise edge cases
4. Update this documentation with findings

---

**Note**: This testing framework is essential for ensuring the DRY Detector produces correct, semantically equivalent code. It has already proven valuable by finding bugs that would have been difficult to catch otherwise.
