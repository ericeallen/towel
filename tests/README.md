# DRY Detector Test Suite

Comprehensive unit tests for the DRY (Don't Repeat Yourself) Detector.

## Running Tests

```bash
# Run all unit tests
just test

# Run specific test categories
just test-bindings      # Test binding construct handling
just test-returns       # Test return value propagation
just test-fstrings      # Test f-string handling
just test-engine        # Test refactoring engine end-to-end
```

## Test Structure

### Unit Tests (`tests/`)

- **`test_bindings.py`** - Tests for binding construct handling
  - For loop variables (alpha-renaming: `i` vs `j`)
  - Comprehension variables (list, dict, set, generator)
  - Tuple unpacking in loops
  - Builtin functions not parameterized

- **`test_return_values.py`** - Tests for return value propagation
  - Early returns in if statements
  - Nested returns
  - Returns inside loops
  - Multiple return paths
  - Functions with no explicit return

- **`test_fstrings.py`** - Tests for f-string and constant handling
  - F-strings with identical/different literals
  - F-string AST unparsing (no errors)
  - Constant parameterization (numeric and string)
  - Mixed f-strings and regular strings

- **`test_refactoring_engine.py`** - End-to-end refactoring tests
  - Single file refactoring
  - Directory analysis
  - Cross-file duplicate detection
  - Parameter limits
  - Min lines threshold

### Test Examples (`test_examples/`)

Real Python code examples used by the test suite:

#### Original Examples
- `example1_simple.py` - Simple validation logic
- `example2_classes.py` - Class-based code
- `example3_file1.py`, `example3_file2.py` - Cross-file duplicates
- `example4_complex.py` - Complex data processing loops

#### Edge Case Examples
- `bindings_for_loops.py` - For loop binding tests
- `bindings_comprehensions.py` - Comprehension binding tests
- `return_values.py` - Return value propagation tests
- `fstrings_constants.py` - F-string and constant tests
- `scoping_edge_cases.py` - Scoping and closure tests

## Test Coverage

The test suite covers:

- ✓ **Binding constructs**: For loops, comprehensions, lambdas, nested functions
- ✓ **Alpha-renaming**: Variables with different names (`i` vs `j`) treated as equivalent
- ✓ **Return values**: Proper propagation of return values to replacement calls
- ✓ **F-strings**: Correct handling without AST errors
- ✓ **Constants**: Parameterization of numeric and string constants
- ✓ **Builtins**: Builtin functions never parameterized
- ✓ **Cross-file**: Detection and refactoring across multiple files
- ✓ **Parameter limits**: Max parameters and min lines respected
- ✓ **Code validity**: All refactored code is valid Python

## Current Status

```
Ran 30 tests in 0.307s

OK
```

All tests passing! ✓
