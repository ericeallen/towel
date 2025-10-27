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

### Running Semantic Tests

```bash
# Run all observational equivalence tests
just test-observational

# Run observational tests as part of comprehensive suite
just test
```

### Writing New Tests

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

### ✓ Passing Tests

Tests that verify observational equivalence for refactorings that work correctly:

- **`test_bindings_for_loops_observational_equivalence`**: For loop variable bindings
- **`test_return_values_observational_equivalence`**: Early returns and return value propagation
- **`test_execute_simple_function`**: Basic execution framework
- **`test_compare_identical_functions`**: Framework comparison logic

### ⚠ Known Issues (Skipped Tests)

Tests that document known bugs found by observational equivalence testing:

- **`test_example1_simple_observational_equivalence`**: Variable capture bug
  - **Issue**: Refactored code uses wrong variable name (`user` instead of `admin`)
  - **Example**: `extracted_func_2(admin, user)` → `NameError: name 'user' is not defined`

- **`test_simple_arithmetic_observational_equivalence`**: Same variable capture issue
  - **Issue**: Parameter names from first function leak into second function

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

## Future Enhancements

Potential improvements to the framework:

1. **Automatic test case generation** using property-based testing (Hypothesis)
2. **Side effect tracking** for mutable state changes
3. **Performance comparison** to ensure refactorings don't degrade performance
4. **Coverage-guided fuzzing** to find edge cases
5. **State comparison** for objects and data structures

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
