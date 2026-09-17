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
uv run --frozen pytest tests/test_observational_equivalence.py

# Run observational tests as part of comprehensive suite
just test

# Run only the automatic comprehensive tests
python -m unittest tests.test_observational_equivalence.TestAutomaticObservationalEquivalence -v
```

### Automatic Testing Framework

The framework now includes **automatic comprehensive testing** that tests ALL refactored functions from ALL example files without manual configuration:

```python
from tests.automatic_equivalence_tester import AutomaticEquivalenceTester
from towel.unification.refactor_engine import UnificationRefactorEngine

# Create tester
engine = UnificationRefactorEngine(max_parameters=5, min_lines=4)
tester = AutomaticEquivalenceTester(engine)

# Test all example files automatically
results = tester.test_all_examples('test_examples')

# Results show:
# - Total files tested (every .py under test_examples/)
# - Total proposals tested
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

## Test results

The observational-equivalence tests run as part of the suite
(`test_observational_equivalence.py`) and pass. The automatic framework extracts
every proposal Towel makes for the example files and checks, on generated inputs,
that each refactored function returns the same value and raises the same
exceptions as the original.

### Passing manual tests

Scenario tests that verify observational equivalence directly:

- **`test_bindings_for_loops_observational_equivalence`**: for-loop variable bindings.
- **`test_return_values_observational_equivalence`**: early returns and return-value propagation.
- **`test_execute_simple_function`**: the execution framework itself.
- **`test_compare_identical_functions`**: the framework's comparison logic.
- **`test_all_examples_automatically`**: the comprehensive automatic sweep.
- **`test_automatic_single_file`**: single-file automatic testing.

### Historical defects (now fixed)

Observational-equivalence testing found two real bugs early in development, both
since fixed and each now covered by a regression fixture (see
[../docs/ADVERSARIAL_REVIEW.md](../docs/ADVERSARIAL_REVIEW.md)):

- **Variable capture** — a parameter name from one block leaked into another, so
  the refactored call referenced an undefined name. The extractor now renames
  binders hygienically, and the per-proposal instantiation check would reject any
  recurrence before it is offered.
- **Sequential corruption** — applying several proposals in a row misaligned line
  ranges and rewrote unrelated code. Changes are now staged and applied
  atomically per file, and the fixed-point loop re-derives ranges after each
  application.
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
uv run --frozen pytest tests/test_observational_equivalence.py
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
- **`justfile`**: `test-smoke` includes the observational tests

## Contributing

When adding new refactoring capabilities:

1. Add observational equivalence tests
2. Ensure tests pass or document known issues with `skipTest()`
3. Add test cases that exercise edge cases
4. Update this documentation with findings

---

**Note**: This testing framework is essential for ensuring Towel produces correct, semantically equivalent code. It has already proven valuable by finding bugs that would have been difficult to catch otherwise.
