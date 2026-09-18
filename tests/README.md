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

The suite is over a hundred `test_*.py` files. Rather than list them all (they
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
- **Reuse of existing functions** — `test_reuse_existing_function.py`: a
  whole-body duplicate calls the existing function, the fallbacks
  (decorated, async, variadic, shadowed, rebound), and the cross-file
  cycle refusal.
- **Generated-code formatting** — `test_generated_code_formatting.py`:
  Black and ruff snippet formatting, the declared line length, the
  syntax-tree check, and the import-sorter guard.
- **Helper annotations and typing** — `test_helper_annotations.py` (copying
  from call sites, unions, the declared-return meet, `Any` completion,
  quoting and bare-name placement) and `test_type_inference.py` (the mypy
  and pyright oracles; skipped when the checker is not installed).
- **Exactness of the performance work** — `test_function_facts_equivalence.py`
  (per-function facts against the uncached analysis),
  `test_candidate_index.py` (bucket invariants), and
  `test_incremental_global_passes.py` (byte-identical `dry` output with
  incremental global passes on and off).
- **Command line** — `test_cli_integration.py`: real `towel` runs, the
  `--x/--no-x` option pairs and their hidden aliases.
- **Hostile batteries** — `test_hostile_battery.py` executes every fixture
  in `hostile_cases/` (numbered `h*` and `r*`) before and after fixed-point
  refactoring and asserts identical output; its `TRANSFORMED` set names the
  fixtures that must change, so a lost extraction fails as loudly as a wrong
  one. `test_hostile_crossfile_battery.py` does the same for the packages in
  `hostile_crossfile/` (`xf*`), with its own `TRANSFORMED` set: every
  package is in exactly one state, and today all of them are transformed.
  When the engine gains or loses a cross-file extraction, move the package
  and say why in the commit. A fixture that came from a repaired ecosystem
  defect (`xf9_same_named_base_class`) is cited in
  `docs/ADVERSARIAL_REVIEW.md`; the others pin behaviour the engine must keep.
- **Property-based tests** — `test_properties.py` generates programs from
  small grammars with Hypothesis (fifty deterministic examples per property,
  no deadline) and checks three invariants: consistently renamed binders
  unify with no parameters, `definitely_bound_after` matches a
  path-enumerating reference, and every engine proposal for two blocks that
  differ in one leaf passes the public instantiation check.
- **AST visitors** — `test_visitors.py`: each visitor in
  `towel.unification.visitors` (function collection, method-call rewriting,
  loop-return and name collection, assignment targets, class and function
  insertion points) driven through its public surface.
- **Boundaries and seams** — `test_filesystem_guards.py`,
  `test_lazy_engine_import.py`, `test_insertion_scan.py`,
  `test_function_index.py`: the refusals of the atomic project copy, the
  lazy engine import, the docstring-and-imports scan, and the per-analysis
  function index.

### Test Examples (`test_examples/`, at the repository root)

Real Python code examples used by the test suite. Their expected fixed-point
outputs are the goldens in `test_examples_expected_output/` and, for the
cross-file examples in `test_examples_crossfile/`,
`test_examples_crossfile_expected_output/`; regenerate them only after
verifying the new output (`just regenerate-baseline`) and review the diff,
since regeneration is not validation.

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
