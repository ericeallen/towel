# Towel Test Suite

Comprehensive unit tests for Towel, the DRY (Don't Repeat Yourself) code refactoring tool.

## Running Tests

```bash
# Run all unit tests
just test

# Run the fast smoke subset (signature gate, pairing, unifier, extractor, regressions)
just test-smoke

# Run a single test file
uv run --frozen pytest tests/test_bindings.py

# Coverage with the 85% gate (forked pair workers are traced; `combine` merges
# their data files before the report)
just coverage

# Strict mypy over src/towel and tests/
just typecheck
```

The suite is type-checked with the same strict flags as the source; test
functions and unittest methods need no signatures (the `tests.*` override in
`pyproject.toml`), and every `# type: ignore` must be needed and name its
error code.

## Test Structure

### Unit Tests (`tests/`)

The suite is 126 `test_*.py` files holding 1,978 tests and 34 subtests (about
70 s; counts as of this writing). Rather than list them all (they change
often), here is how they group by concern, with a representative file for
each. Small helpers the tests share (parsing a dedented block, fixing
synthesized positions, taking a module's functions by name, writing a module
into `tmp_path`, running the engine to a fixed point with its output
silenced, the `EngineOptions` a test may forward) live in `test_helpers.py`,
which is strictly typed; `conftest.py` only keeps pytest from collecting the
example corpora and scratch output directories.

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
  `test_candidate_index.py` (bucket invariants),
  `test_incremental_global_passes.py` (byte-identical `dry` output with
  incremental global passes on and off), `test_statement_facts.py`,
  `test_structural_memo.py`, `test_cache_lifetimes.py`,
  `test_binding_context_memo.py`, `test_substitution_keys.py`,
  `test_thunk_inlining.py`, `test_perf_memos.py` (each memo against the
  uncached computation and across a re-parse) and `test_analysis_sessions.py`
  (the bounded session and its sizing).
- **Repeated extraction and input errors** — `test_forwarder_chains.py`
  (repeated extraction never stacks helpers into a chain) and
  `test_cli_input_errors.py` (a single file that cannot be refactored is
  named, a negative count is refused); `test_release_regressions.py` pins
  the defects the ecosystem runs found and `test_progress_modes.py` the three
  progress displays and the silence of `none`.
- **Command line** — `test_cli_integration.py`: real `towel` runs, the
  `--x/--no-x` option pairs and their hidden aliases;
  `test_cli_dispatch_paths.py`: the `recover` subcommand and its error exit,
  the JSON helper listing, a run that finds nothing, and the confirmation an
  input without a `.py` suffix requires.
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
  small grammars with Hypothesis (300 deterministic examples for the pure
  properties, 60 for the engine property, no deadline) and checks three invariants: consistently renamed binders
  unify with no parameters, `definitely_bound_after` matches a
  path-enumerating reference, and every engine proposal for two blocks that
  differ in one leaf passes the public instantiation check.
- **AST visitors** — `test_visitors.py`: each visitor in
  `towel.unification.visitors` (function collection, method-call rewriting,
  loop-return and name collection, assignment targets, class and function
  insertion points) driven through its public surface.
- **Boundaries and seams** — `test_filesystem_guards.py`,
  `test_lazy_engine_import.py`, `test_function_index.py`,
  `test_source_encoding.py`, `test_unsupported_layout.py`,
  `test_symlinked_input_directory.py`: the refusals of the atomic project
  copy, the lazy engine import, the per-analysis function index,
  byte-convention preservation, a layout Towel cannot model, and a symlinked
  input directory (followed, while links inside it are copied as links and
  never analyzed or rewritten). `test_analysis_edge_paths.py` holds the
  smaller analysis branches driven one at a time: the `nonlocal` scan without
  a scope analyzer, differing f-string format specs, `try` blocks of imports
  ahead of a helper, attribute reflection, keyword-passed callables, the
  validation trace, and mypy configuration in `setup.cfg`.
- **Error paths and recovery** — `test_robustness_paths.py`,
  `test_error_paths.py`, `test_recovery_journal.py`,
  `test_stale_proposal_recovery.py`, `test_parallel_evaluation.py`,
  `test_parent_watchdog.py`, `test_semantic_guard_traces.py`: tool failures
  and timeouts, the command line's error exits and JSON contract, every
  guard of the recovery journal with a Hypothesis round trip, a proposal
  that goes stale mid-run, the forked pool against the serial path, workers
  ending when their parent is killed, and the rejection reasons reached by
  name.

### Test Examples (`test_examples/`, at the repository root)

Real Python code examples used by the test suite. Their expected fixed-point
outputs are the goldens in `test_examples_expected_output/` and, for the
cross-file examples in `test_examples_crossfile/`,
`test_examples_crossfile_expected_output/`; regenerate them only after
verifying the new output (`just regenerate-baseline`) and review the diff,
since regeneration is not validation.

Three goldens are byte-identical to their inputs because single-file mode
finds nothing to extract in them. `EXPECTED_UNCHANGED_EXAMPLES` in
`test_regression.py` names them, and the regression test asserts that the
unchanged goldens, and the inputs the engine leaves untouched, are exactly
that set: an extraction that silently stops happening, or one that starts,
fails the test instead of being regenerated into the baseline unnoticed.
When such a change is intended, move the file in or out of the set and say
why in the commit.

The corpus has 26 files; each is a self-contained module whose name says
what it exercises (see `test_examples/README.md`):

- `binding_constructs_comprehensive.py`
- `bindings_comprehensions.py`
- `bindings_for_loops.py`
- `closure_adversarial.py`
- `complex_expressions.py`
- `control_flow_adversarial.py`
- `edge_cases_stress_test.py`
- `example1_simple.py`
- `example2_classes.py`
- `example3_file1.py`
- `example3_file2.py`
- `example4_complex.py`
- `exception_adversarial.py`
- `fstrings_constants.py`
- `functional_patterns.py`
- `global_nonlocal_examples.py`
- `hygienic_naming.py`
- `method_chains.py`
- `nested_structures.py`
- `real_world_patterns.py`
- `referential_transparency.py`
- `return_values.py`
- `scoping_edge_cases.py`
- `side_effects_adversarial.py`
- `syntactic_coverage_comprehensive.py`
- `tricky_edge_cases_adversarial.py`

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

The full suite passes; `just coverage` reproduces the enforced 85% gate
(93% of `src/towel` as of this writing). Coverage traces the forked pair
workers and their watchdog threads, so `src/towel/unification/parallel.py`
is measured like any other module; each process writes its own data file and
`coverage combine` runs before the report.
