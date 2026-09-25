# Towel Test Suite

Comprehensive unit tests for Towel, the DRY (Don't Repeat Yourself) code refactoring tool.

## Running Tests

```bash
# Run all unit tests
just test

# Run the fast smoke subset (signature gate, pairing, unifier, extractor, engine, regressions)
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

The suite is 133 `test_*.py` files holding 2,182 tests and 34 subtests (about
100 s; counts as of this writing, commit 5ff2458, September 19, 2026). Rather
than list them all (they change often), here is how they group by concern,
with a representative file for each. Small helpers the tests share (parsing a
dedented block, fixing synthesized positions, taking a module's functions by
name, writing a module into `tmp_path`, locating and copying `test_examples`
files, asserting a file was left unmodified, running the engine to a fixed
point with its output silenced, the `EngineOptions` a test may forward, and
`TemporaryModuleTestCase` for `unittest` classes that write modules) live in
`test_helpers.py`, which is strictly typed; `conftest.py` keeps pytest from
collecting the example corpora and scratch output directories, and guards
typed tests against passing vacuously: a test whose typed runs declined
proposals as code the type checker does not look at, and verified none,
fails at teardown unless it is marked `@pytest.mark.looks_nowhere`, because
such a checker answered nowhere and the test verified nothing. A project
path through a symbolic link (every temporary directory on macOS) once had
every mypy answer dropped in exactly that way.

A test that writes a module the engine will refactor puts it in a directory
of its own: pytest's `tmp_path`, or `TemporaryModuleTestCase._write_temp` in
a `unittest` class. Never `NamedTemporaryFile` or `mkstemp` at the temp root:
Towel places its transaction journal at the common parent of the files a run
changes, so a module refactored directly in `$TMPDIR` leaves the journal
there and every other run under `$TMPDIR` stops with `RecoveryRequired`.

- **Binding & scope** — `test_bindings.py`, `test_binding_detector.py`,
  `test_definite_assignment.py`, `test_module_names_stay_free.py`:
  alpha-renaming, comprehension and loop variables, `global`/`nonlocal`,
  orphan detection, builtins left untouched, and the module-level names a
  same-module helper reads bare (a cross-file helper still takes them as
  parameters).
- **Return propagation** — `test_return_values.py`,
  `test_extractor_return_statements.py`: early, nested, and multi-path returns
  wired back into the replacement call.
- **F-strings & constants** — `test_fstrings.py`,
  `test_extractor_augassign_and_fstrings.py`: format-string handling and
  constant parameterization without AST breakage.
- **Extraction & rendering** — the many `test_extractor_*.py` files: helper and
  call-site generation, hygienic naming, overlapping-replacement detection.
  `test_splicing.py`: the call replaces exactly its block's text, keeping
  the other statements of a `;` line, a one-line compound body's header and
  a backslash continuation, with a property over generated modules.
  `test_shared_line_directives.py`: every directive form on a line the
  block shares with code that stays declines the pair; a plain comment
  there moves.
  `test_effect_free.py`: what may precede a thunk passed eagerly, a verdict
  for every expression form, and a property that evaluates what the rule
  accepts among objects recording every operation.
- **Engine end-to-end** — `test_refactoring_engine.py`,
  `test_engine_adversarial.py`: single-file, directory, and cross-file runs,
  parameter and min-lines limits.
- **Cross-file & import names** — `test_cross_module_opt_in.py`: helpers are
  shared across modules only with `--cross-module`, and no pair across
  modules is formed or budgeted otherwise; `test_crossfile_integration.py`;
  `test_import_name_corroboration.py`: `towel dry --cross-module` over eight
  layouts that broke before (setup.cfg and setup.py src layouts, a stray
  `src/__init__.py`, a project named like its package, a Hatch include, a
  namespace package, tests that borrow and never lend, tests inside the
  package), each imported in the source tree and from an installed wheel;
  `test_import_cycles_by_the_program.py`: the cycle guard follows the
  program's imports; `test_program_imports.py`: a run's paths, and imports
  into excluded directories counted as unseen;
  `test_import_problem_refusal.py`: import problems reported, and a run
  refused when they concern its own package; `test_rename_by_the_programs_names.py`:
  renames follow the program's module names; `test_project_layout_and_imports.py`,
  `test_project_layout_behavior.py`: the project root, configuration and the
  retired import preferences. The model itself is `test_import_model.py`.
- **Soundness batteries** — `test_adversarial_semantics.py`,
  `test_adversarial_renaming.py`, `test_*_observational_equivalence.py`:
  instantiation-based equivalence checks and the hostile fixtures behind them.
  The equivalence harness (`automatic_equivalence_tester.py`,
  `equivalence_targets.py`, `crossfile_equivalence_tester.py`) consumes a
  returned generator and runs a returned coroutine before comparing, compares
  instances of a class each executed module defines for itself by their
  state, and constructs a subclass target when its module declares the
  constructor.
- **Application & recovery** — `test_change_transactions.py`,
  `test_copy_preservation.py`: atomic byte plans, rollback, and interruption
  recovery.
- **Whole-body duplicates** — `test_reuse_existing_function.py`: a duplicate
  that is the whole body of a function calls one new helper like any other
  copy, never another existing function, whatever the deprecated
  `reuse_existing_functions` says; decorated, async and rebound functions,
  and the cross-file host that closes no cycle.
- **Where a method helper goes** — `test_method_helper_hosting.py`,
  `test_class_private_helpers.py`, `test_method_host_machinery.py`: only
  into the class holding both duplicates, class-private, and never into a
  class whose machinery or attribute lookup would change what its methods
  reach; blocks shared across classes become module functions taking the
  receiver. `test_rename_class_private_helpers.py` renames such helpers by
  their class.
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
  uncached computation and across a re-parse), `test_analysis_sessions.py`
  (the bounded session and its sizing), `test_pair_budget.py` (the
  candidate-pair budget and its flags) and `test_distinct_proposals.py` (one
  proposal per distinct refactoring).
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
  in `hostile_cases/` (129 fixtures as of this writing, numbered `h*` and
  `r01` to `r145`) before and after fixed-point refactoring and asserts
  identical output; its `TRANSFORMED` set names the fixtures that must
  change (87 today), so a lost extraction fails as loudly as a wrong one.
  Four of the fixtures (`r142` to `r145`) pin that a same-module helper reads
  a module-level name bare, that `__class__` stays a parameter, and that an
  assigned alias of `sys._getframe` declines.
  `test_hostile_crossfile_battery.py` does the same for the packages in
  `hostile_crossfile/` (`xf*`), with its own `TRANSFORMED` and `REJECTED`
  sets: every package is in exactly one state. Today twelve are transformed
  and one is rejected (`xf13_import_time_effects`, whose helper import would
  load a module that prints at import time). When the engine gains or loses
  a cross-file extraction, move the package and say why in the commit. A
  fixture that came from a repaired ecosystem defect
  (`xf9_same_named_base_class`) is cited in
  `docs/ADVERSARIAL_REVIEW.md`; the others pin behaviour the engine must keep.
- **Audit findings and differential testing** — the batteries hold the
  round-3 audit's cases (`r7fz_*`, `xf7fz_*`): every one it reported as a
  P1, and a sample of the rest from each of its families. A fixture whose
  defect is not yet fixed is in its battery's `KNOWN_DEFECTS`, a strict
  expected failure with its reason from `audit_defects.py`, and its
  transformed state is not pinned. The package battery runs a fixture in
  `WITHOUT_CROSS_MODULE` as the default mode does and one in `TYPED` with the
  checker its `pyproject.toml` configures (both run the refactoring the way
  `hostile_refactoring.py` does, which the harness's exporter shares).
  `test_differential_grammar.py` runs the auditor's program generator,
  rewritten in `differential/`: each seed's project is refactored and
  observed before and after in fresh interpreters (`differential/observer.py`
  records output, exceptions, signatures, class dictionaries, MROs, pickling
  and public names), on seeds 0 to 299 in the default mode, the two-module
  layouts of seeds 0 to 199 with `--cross-module`, a few typed seeds with
  mypy strict, and every seed on which a defect was found. `just fuzz` runs
  the same harness over many seeds (`differential/fuzz.py`) and writes each
  failure as a fixture (`differential/export.py`).
- **Property-based tests** — `test_properties.py` generates programs from
  small grammars with Hypothesis (300 deterministic examples for the pure
  properties, 60 for each engine property, no deadline). The pure properties:
  consistently renamed binders unify with no parameters, and
  `definitely_bound_after` is sound against a path-enumerating reference
  whose grammar includes `with`, `del` and `except ... as` (containment,
  since the analysis treats both path-insensitively). The engine properties, on two
  blocks that differ in one leaf: every proposal passes the public
  instantiation check, a directory run's output is a fixed point, byte
  conventions survive, both call sites stay observationally equivalent, a
  block defining a closure over a binder rebound after it is declined or
  kept equivalent, no proposal covers an argument-free `dir()`, `locals()`
  or `vars()`, and a name nothing binds under `if limit < 0` is never
  hoisted into an eager argument.
- **AST visitors** — `test_visitors.py`: each visitor in
  `towel.unification.visitors` (function collection, method-call rewriting,
  loop-return and name collection, assignment targets, class and function
  insertion points) driven through its public surface.
- **Boundaries and seams** — `test_filesystem_guards.py`,
  `test_lazy_engine_import.py`, `test_function_index.py`,
  `test_source_encoding.py`, `test_unsupported_layout.py`,
  `test_symlinked_input_directory.py`: the refusals of the atomic project
  copy, the lazy engine import, the per-analysis function index,
  byte-convention preservation, a layout the packaging readers could not
  model and the import model can, and a symlinked
  input directory (followed, while links inside it are copied as links and
  never analyzed or rewritten); `test_environment_independence.py` (the
  result does not depend on the working directory, path spelling or line
  endings) and `test_import_cycle_package_init.py` (`from . import name` is
  an edge through the package initializer). `test_analysis_edge_paths.py`
  holds the smaller analysis branches driven one at a time: the `nonlocal`
  scan without a scope analyzer, differing f-string format specs, `try`
  blocks of imports ahead of a helper, attribute reflection, keyword-passed
  callables, the validation trace, and mypy configuration in `setup.cfg`.
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
cross-file packages in `test_examples_crossfile/` (`simple_crossfile`,
`nested_structure`, `multi_level`, and `class_hierarchy`, a subclass in a
second module repeating its base class's method body),
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

The corpus has 28 files; each is a self-contained module whose name says
what it exercises (see `test_examples/README.md`):

- `annotated_module.py` (annotated locals in the duplicated blocks,
  keyword-only parameters, a frozen dataclass)
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
- `generators_async.py` (a prefix shared by generators and coroutines is
  extracted; blocks containing `yield` or `await` are declined)
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
(94% of `src/towel` at commit 5ff2458, September 19, 2026). Coverage traces
the forked pair workers and their watchdog threads, so
`src/towel/unification/parallel.py`
is measured like any other module; each process writes its own data file and
`coverage combine` runs before the report.
