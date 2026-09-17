# Towel Test Suite

Comprehensive unit tests for Towel, the DRY (Don't Repeat Yourself) code refactoring tool.

## Running Tests

```bash
# Run all unit tests
just test

# Run the fast smoke subset (signature gate, unifier, extractor, regressions)
just test-smoke

# Run a single test file
uv run --frozen pytest tests/test_bindings.py
```

## Test Structure

### Unit Tests (`tests/`)

The suite is roughly 90 `test_*.py` files. Rather than list them all (they
change often), here is how they group by concern, with a representative file
for each:

- **Binding & scope** — `test_bindings.py`, `test_binding_detector.py`,
  `test_definite_assignment.py`: alpha-renaming, comprehension and loop
  variables, `global`/`nonlocal`, orphan detection, builtins left untouched.
- **Return propagation** — `test_return_values.py`,
  `test_extractor_return_statements.py`: early, nested, and multi-path returns
  wired back into the replacement call.
- **F-strings & constants** — `test_fstrings.py`,
  `test_extractor_augassign_and_fstrings.py`: format-string handling and
  constant parameterization without AST breakage.
- **Extraction & rendering** — the many `test_extractor_*.py` files: helper and
  call-site generation, hygienic naming, overlapping-replacement detection.
- **Engine end-to-end** — `test_refactoring_engine.py`,
  `test_engine_adversarial.py`: single-file, directory, and cross-file runs,
  parameter and min-lines limits.
- **Cross-file & layout** — `test_crossfile_integration.py`,
  `test_backend_layouts.py`, `test_project_layout_and_imports.py`: import-path
  inference across packaging backends and relative-vs-absolute import choice.
- **Soundness batteries** — `test_adversarial_semantics.py`,
  `test_adversarial_renaming.py`, `test_*_observational_equivalence.py`:
  instantiation-based equivalence checks and the hostile fixtures behind them.
- **Application & recovery** — `test_change_transactions.py`,
  `test_copy_preservation.py`: atomic byte plans, rollback, and interruption
  recovery.

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

The full suite passes; run `just coverage` to reproduce the enforced 85%
coverage gate.
