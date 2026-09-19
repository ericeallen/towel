# Changelog

All notable changes to Towel are recorded here. This file summarizes releases;
[docs/RELEASE_LOG.md](docs/RELEASE_LOG.md) keeps the detailed engineering log,
and [docs/PRODUCTION_READINESS.md](docs/PRODUCTION_READINESS.md) records the
ecosystem evidence behind each claim. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- A duplicate that is the whole body of a plain module-level function now
  calls that function instead of extracting a helper. Two identical functions
  no longer become a helper plus two forwarders: the first-defined one is kept
  and the other calls it, and a matching block inside a larger function calls
  the existing function directly. Arguments follow the function's parameter
  order; names the body reads from its own module (functions, classes,
  absolute imports) are not passed. Across files the call is imported like a
  helper and refused when it would close an import cycle; a decorated, async,
  variadic, shadowed, or rebound function falls back to ordinary extraction.
  A function whose body is the block followed by a plain `return` of the
  block's live variables is reused too; the other sites unpack its result in
  its order. Construct the engine with `reuse_existing_functions=False` to
  restore the old behavior.
- A helper that returns live variables now admits every further same-file
  occurrence whose call assigns them, so twelve identical functions become
  one helper with twelve calls on the first pass rather than a pair per
  pass. Repeated passes used to stack such helpers into a chain of
  forwarders, each returning the next one's result; a proposal that would
  reduce a helper an earlier pass inserted to a one-line forwarder is now
  declined instead (part of `skip_trivial_helpers`).
- `towel dry` formats the code it inserts with the formatter the project
  configures (`pip install "code-towel[format]"`): `ruff format` when
  `[tool.ruff]` (or `ruff.toml`) is present and ruff is installed, otherwise
  Black with the project's own line length and string quoting. Only the
  generated helper and the rewritten call statements are formatted, never
  the surrounding file, and each snippet is checked to have the same syntax
  tree before and after. Inserted imports are sorted the way the project
  sorts them, with ruff's `I` rules when selected or isort when configured;
  a sorter may only reorder or merge the import statements, which is
  verified. Pass `--no-format` to insert the unformatted rendering; without
  a formatter a note says so. Library callers pass any `snippet_formatter`
  and `file_finisher` callables to the engine.
- Black's line length for generated code is the limit the project declares
  anywhere: `[tool.black]`, `[tool.ruff]`, `[tool.pycodestyle]`, or a
  `[flake8]`/`[pycodestyle]` section in `setup.cfg`, `tox.ini` or `.flake8`
  (pycodestyle's own dog-food test failed on an 86-character generated call
  under Black's default of 88 against its declared 79).
- Extracted helpers carry the type annotations their call sites declare. A
  parameter is annotated when every site passes an annotated, never-rebound
  parameter of its enclosing function, or a literal of one builtin type, and
  the sites agree; the return is annotated from the sites' declared return
  type, from annotated locals the helper returns, or as `None` for a helper
  that returns nothing. Nothing is inferred. Annotations are copied unquoted
  only where they cannot fail to resolve (builtins, deferred annotations, or
  names bound by a module-level import) and as strings otherwise; across
  modules only builtin names are used. Code without annotations stays that
  way. Construct the engine with `annotate_helpers=False` to disable it.
- When a type checker is installed (`pip install "code-towel[types]"`), the
  argument expressions and return values those copied annotations could not
  name are typed by it. The checker is the one the project configures: mypy
  for a project with `[tool.mypy]` or `mypy.ini`, pyright for one with
  `[tool.pyright]` or `pyrightconfig.json`; for a project configuring both,
  mypy infers while both verify the generated code, so the project's own
  check stays green. Pyright runs as a command on a temporary sibling copy
  of the module; mypy reveals each expression in an in-memory copy of the
  site's module, at the point where the call will stand, once per applied
  refactoring with an incremental cache. A revealed type is written only
  when it is not bare `Any` and every name in it resolves where the helper
  is defined. `--no-types` leaves helpers unannotated; library callers pass
  a `type_oracle` to the engine.
- Sites that disagree on a parameter's type, or on a revealed return type,
  join into a union (`int | None`, `int | str`); sites that declare
  different return types take the meet of the declarations, found through
  the checker's subtype relation. Once a helper carries any annotation,
  whatever is still bare becomes `Any`, so the signature is complete (a
  partial one is an error under mypy's `disallow-incomplete-defs`), and
  `from typing import Any` is added to the host when it lacks it. Code with
  no annotations stays bare.
- With mypy installed the subtype relation is mypy's own, asked through
  probe functions appended to an in-memory copy of the host module: unions
  are normalized by it (`int | bool` is `int`, `float | int` is `float`, a
  subclass under its base disappears), the declared return types' meet is
  found through it, and the helper's revealed return type is written only
  when mypy confirms it is a subtype of every site's declared return type,
  which is what keeps the sites type-checking. Without mypy Towel copies
  and does not reason: unions are written unreduced and the meet needs
  identical declarations, since there is no second implementation of the
  subtype relation.
- Thunk and callee arguments get `Callable` annotations from mypy's callable
  spelling (`Callable[[], int]`, `Callable[[int, str], bool]`,
  `Callable[..., T]`), with `from typing import Callable` added as needed.
- A module-level helper whose annotations name classes or functions defined
  in the module is placed after the last such definition, so those names are
  written bare instead of as quoted forward references; it stays at the top,
  with quotes, when any statement before that point could run code at
  import time.
- A subscripted annotation is written bare only when it evaluates at
  definition time: a PEP 585 builtin generic, a name from `typing` or
  `collections.abc`, or a module that defers annotations. Anything else is a
  string annotation: `memoryview[int]`, copied from tornado's own signatures,
  raised `TypeError` at import on an interpreter where `memoryview` is not
  generic.
- With a type checker installed the generated code is type-checked: each
  modified file is checked before and after, and if the change introduces an
  error the helper's annotations degrade to `Any`, and then to none, until
  it does not.
- Property-based tests (hypothesis, in the `dev` extra) check that alpha-variant
  blocks unify with a renaming-only substitution, that definite assignment
  agrees with a path-enumerating reference, and that every generated helper
  passes the instantiation round trip; `visitors.py` has direct tests; the
  cross-file hostile battery pins which packages must transform; ten
  cross-file integration tests fail instead of skipping when a fixture is
  missing; six assertion-free tests assert what their names claim.
- The suite now drives the forked pair-evaluation pool on a small project and
  checks it against the serial path, including the fallback when a worker
  raises or dies; corrupts the recovery journal one guard at a time and
  round-trips interrupted transactions under Hypothesis; drops a proposal
  that goes stale mid-run and covers the three progress displays; reaches
  the return-coverage and free-variable-lifetime rejections by name (and
  pins that the first block's coverage guard is shadowed by block
  enumeration); and adds idempotence, byte-convention and observational
  equivalence properties, with `with` in the flow grammar. The cross-file
  timing test no longer measures the machine, the watchdog test finishes
  in a fraction of a second, and both hostile batteries execute fixtures
  the same isolated way. Three hostile fixtures pin the closure, `dir()`
  and unbound-global cases below, and a forwarder-chain suite pins that
  twelve identical functions yield one helper.
- CI runs `pip-audit --strict` against the locked dependency set, and
  Dependabot proposes weekly, grouped minor/patch updates for the workflow
  actions and the `uv`-managed Python dependencies.

### Changed
- Boolean options come in `--x/--no-x` pairs with the default on:
  `--types/--no-types`, `--format/--no-format`, `--interactive/--no-interactive`,
  and the tri-state `--prefer-absolute-imports/--no-prefer-absolute-imports`
  and `--pep420/--no-pep420` (unset lets the project decide). `--max-refactorings N`
  names what `--max-iterations` always did, and `rename-helpers --preview`
  replaces `--dry-run`, since "dry" already means DRY here.
- A helper whose body only binds parameters and literals to names and returns
  them is not proposed: the call that unpacks the tuple is longer than the
  assignments it replaces and shares no logic. The trivial-forwarding filter
  also recognizes `name = call(...)` followed by `return name`, and the tuple
  form `a, b = call(...)` then `return (a, b)`; without it, once Black
  wrapped such a body over the three-line minimum, two generated helpers of
  that shape paired with each other and extracted a third, without end (h2).
- In directory mode, a global re-pass after the first re-pairs only the
  functions in files rewritten since the previous global pass. This is exact
  (the argument is in `docs/ARCHITECTURE.md`): an unchanged pair's verdict
  depends on its two files, the class hierarchy, which refactoring never
  alters, and the import graph, to which refactoring only adds edges, and
  every proposal such a pair produced has been applied, dropped, or
  filtered along with a rewritten file. `dry` output is byte-identical with
  the restriction on and off; `incremental_global_passes=False` restores
  full re-pairing.
- Analysis facts are computed once per function instead of once per candidate
  block (definite assignment, locally bound names, nested scopes), and the
  same-file clustering pass applies its constant-time filters before the
  semantic guards. Together these remove about half of the AST traversal on
  a 16k-line project with an identical proposal list.
- Candidate blocks are bucketed on their whole statement-type sequence,
  which the unifier requires equal, so far fewer pairs reach the filter; the
  unifier's bound-variable search caches each node's source text instead of
  re-rendering it per query; the value-producing check is memoized per
  block; and five visitor classes that were rebuilt on every call are
  module-level. On Towel's own source these remove a further 28% of all
  function calls (about half in total since 1.618) with an identical
  proposal list.
- The engine is assembled from mixins, one module per responsibility
  (`pair_evaluation`, `placement`, `reuse`, `insertion`,
  `annotation_wiring`, `materialize`, `clustering`, `parallel`,
  `fixed_point`), over an `EngineState` that declares the state and
  operations each may rely on; `refactor_engine.py` went from 5,600 lines
  to 1,400 and the 871-line pair decision is eleven typed stages. The
  outputs are byte-identical on the exactness baselines.
- A second pass over the same seams: the per-block analyses are a
  `BlockAnalysis` mixin of their own and the engine core is 700 lines; the
  unifier is split the same way (`UnifierState` under constant consistency,
  parameterization and literal promotion, with `Substitution` and the
  binding-context finder in leaf modules); every state stub names the class
  that implements it. The functions of an analysis are indexed once
  (`FunctionIndex`: by file, by name, by enclosing range) instead of scanned
  per pair, and the engine's per-function maps are weak. Materialization,
  the directory driver's progress reporting and the rename planner are
  each a set of named steps rather than one long function. Blocks are
  narrowed by checks rather than casts, and the `dry` change sidecar is
  validated on read.
- A third structural pass: the block signatures, clustering guards, cache
  keys, pair context and stage records are typed; the remaining long
  functions (materialization, the drivers, layout discovery, the pairing
  loop, the call generator, the clustering pass, the rename planner, the
  compound-node unifier, the thunk walker) are named steps or dispatch
  tables; every function and closure with no caller is removed; mypy runs
  with `warn_unreachable`, and flake8 checks `tests/` for pyflakes errors.
  One `BoundedCache` replaces three LRU implementations and the parse cache
  is the engine's own; `FunctionNode` is the one name for a function
  definition node; the defaults live in `towel.unification.defaults`;
  `towel.source_text` decodes and re-encodes sources.
- The engine computes what it can once per statement instead of once per
  block: block signatures, return checks, the frame-sensitivity and
  name-binding guards, structural digests, the unifier's per-statement
  facts and the substitution's keys are memoized per AST node and folded
  over each block, the bound-variable query is memoized per block and
  target text, a pair's structural ids are resolved once and passed to its
  guards, and each class's file path is resolved once per run. On the
  Towel source of that day (`towel dry src/towel`, one worker, 45 applied)
  a run took 6.4 s instead of 10.9 s with `--no-types --no-format` and
  10.4 s instead of 15.0 s with the defaults; the profiled run went from
  32.7 s and 293 million calls to 20.3 s and 154 million. The current
  figures, on a smaller source, are in `docs/KNOWN_LIMITATIONS.md`. Every
  proposal and rendering is byte-identical on the exactness baselines. The
  structural-id and source-digest caches, and the definite-assignment
  facts, no longer keep a re-parsed file's old tree alive; peak memory fell
  from 186 MB to 179 MB (1.11 GB to 1.06 GB with type checking).
- A fourth pass, after an audit that found the same concepts written
  several ways: the local name an import alias binds, the names a
  statement binds (`bindings_of`, replacing five collectors that
  disagreed), the names an expression reads, span containment, the
  method-kind literal, the helper-home and cluster-context records and
  package-chain ascent each have one implementation; the unifier's
  dispatch table holds methods rather than their names; blocks are
  sequences of statements throughout, so the casts that the wider
  annotation forced are gone; two typing helpers (`all_instances`,
  `visit_as`) replace most of the rest; each command reads its arguments
  once into a typed record; the rename command decodes sources the way
  the refactoring commands do; and comments that restated the next line
  are removed. The engine's mixins now inherit the mixins they call, so
  the forty-nine one-caller stubs on `EngineState` are gone and it declares
  only the shared attributes and the eleven operations the core provides.
  The import-graph resolution is its own module (`import_graph.py`);
  parsing yields a `RawModule` and scope analysis a `ParsedModule` whose
  analyzer and root scope are required rather than Optional; the
  `verbose` flag threaded through eleven signatures and read once is gone
  (the logger's level decides); and the `get_` prefix on a handful of
  functions is dropped (`free_variables`, `used_names`,
  `enclosing_names`, `affected_lines`, `bound_variables_in_context`).
- The test suite is type-checked with the same strict flags as the source
  (fifteen `type: ignore` comments ignored nothing; the AST accesses behind
  the rest are `isinstance` assertions now), coverage traces the forked
  pair workers (`parallel.py` 85% to 95%), the helpers copied across test
  modules live in `tests/test_helpers.py`, thirty-odd non-emptiness assertions state
  the value they are about, the goldens expected to equal their inputs are
  named and checked exactly, the example3 tests run the cross-file path
  they describe, a symlinked input directory is pinned, and the engine and
  command-line branches the suite left untested have tests.
- The clustering pass scans a file for the sites that can share a helper
  once per distinct helper template instead of once per pair, and the
  reuse redirect finds a function whose body starts at a site through an
  index instead of scanning the file per replacement: 50 identical
  functions took 17 s and 100 took 134 s before; re-measured with
  `scripts/bench_similar_blocks.py` on September 18, 2026, one core of a
  machine shared with other work, 50 take 6.8 s and 100 take 36 s, with
  identical output. Orphan detection, the
  instantiation check's normalized block and the class-private-name scan
  are memoized on structure, so the re-parse after each applied proposal
  hits too. The analysis session grows to the number of files an analysis
  covers, so a project above 128 files no longer re-parses everything on
  every pass (trio's second pass: 144 parses and 7.2 s, now none and
  5.0 s). Both insertion-position parses go through the engine's parse
  memo.
- The AST visitors are built on three Template Method bases in
  `visitors.py`: `OwnScopeVisitor` for collectors that read one scope's own
  code, `DefinitionDepthVisitor` for those that track how deeply a
  definition sits, and `ScopeVisitor` for the analyses that follow lexical
  scopes, which now share one visiting order for definitions and
  comprehensions and override hooks where they deviate on purpose.
- `refactor_directory_to_fixed_point` returns a `TerminationReason` literal;
  `CodeBlockPair` carries every field pairing always gives it (only the
  class and enclosing-function names are Optional) and answers
  `is_cross_file`; `RefactoringProposal.replacements` holds `Replacement`
  values only, the tuple coercion is gone; the unused exception classes are
  gone and `UnsupportedLayoutError` joins `RefactoringError` under
  `TowelError`; `towel.project_layout` is a top-level module (the old path
  re-exports it); `is_package_dir` takes only the path, since whether a
  bare directory is a namespace package is the layout's decision.
- Progress modes are a `ProgressMode` literal (`normalize_progress`,
  `wants_bar`, `DEFAULT_PROGRESS`); `orphaned_variables` returns the set of
  orphaned names and `bound_names_in_block` the names a block binds; the
  engine's `type_inferrer` parameter is `type_oracle` and the
  `TypeInferrer` alias is gone, `TypeOracle` is the name. Module-internal
  helpers that read as public are private, and the predicates that ask
  whether a project configures a tool all say `project_configures_*`.
- `run_pipeline` takes the engine it drives (`engine=`) and the pipeline no
  longer imports the engine; the two phase entry points it calls are public
  (`find_block_pairs`, `process_block_pairs`). `from towel.unification
  import UnificationRefactorEngine` still works and loads the engine lazily.
- `formatter_for_project`, `import_sorter_for_project`, and
  `type_oracle_for_project` return a `ToolChoice` (`tool`, `note`) instead
  of a tuple; `TypeOracle.is_subtype` returns `Subtyping` verdicts
  (`YES`, `NO`, `UNKNOWN`) instead of `Optional[bool]`. Rejection reasons
  are a `RejectReason` enum, evaluation kinds a `ParameterKind` literal,
  the rename inventory a `TypedDict`, and the applied-change log is
  `engine.change_log`, a sequence of `AppliedChange`.
- The library prints no diagnostics. Warnings go to the `towel` logger,
  which reaches stderr even when nothing configures logging, and the
  rejection, validation, overlap, and type traces go to `towel.rejections`,
  `towel.validation`, `towel.overlap`, and `towel.types` at DEBUG. The
  documented environment variables still switch them on; they and
  `TOWEL_WORKERS` are read once into `Settings` at engine construction
  (`settings=` overrides them for library callers), and the engine
  constructor no longer changes logger levels
  (`Settings.enable_debug_logging` is the library caller's to call).
- Helpers other modules imported under leading underscores are public:
  `find_project_root`, `load_pyproject`, `is_package_dir`,
  `walk_own_scope`, `body_without_docstring`, `stored_names`, `reindent`,
  `relative_import_module`. Two implementations of the docstring stripper,
  the own-scope walker, the `__param_N` minting rule, and the subtype
  verdict reader became one each, and ten duplications Towel found in its
  own source are named helpers.
- The import-graph tables are an `ImportGraphCache` the engine owns per run,
  bounded, instead of process-global dictionaries, and a required argument
  of the cycle and resolution checks; the apply path parses each modified
  file once per source text and re-reads a file only when its stat changes;
  the pair loop skips two functions whose blocks share no bucket key before
  visiting any block.
- Two catches that turned bugs into silent defaults (a module-name fallback
  catching every exception, a call renderer swallowing a `TypeError`) are
  narrowed or removed; the nine copies of `try/except/pass` around
  progress-bar calls are one `quietly`; four unjustified `type: ignore`
  comments and three `pragma: no cover` exclusions are gone.
- The candidate pairs an analysis evaluates are bounded by a configurable
  candidate-pair budget, and the minimum block length (`--min-lines`) is
  exposed on the command line.
- The structural memo is keyed on a block's structure, so equal blocks share
  one entry wherever they appear.

### Deprecated
- The `--max-iterations` and `rename-helpers --dry-run` spellings still
  parse but are left out of the help; use `--max-refactorings` and
  `--preview`.

### Removed
- `nominal_unifier.py`, which nothing imported, and the TOML backport for
  Python 3.10, below the supported floor, with its `tomli` dev dependency.

### Fixed
- The eager-argument guard is rebuilt on control flow. Twelve shapes that
  passed a differing name eagerly where the original read it only on some
  path are thunked or declined: a failed optional import, a `TYPE_CHECKING`
  import, a class attribute read bare in a method, a name bound later,
  deleted, or bound conditionally in the enclosing function, and at module
  level a name bound by `except ... as`, a `match` capture, a later `def`,
  or a later import.
- A free variable the two blocks share follows the same rule as a differing
  argument: it is passed eagerly only when the call site resolves it on
  every path, and as a thunk otherwise. A name bound nowhere, read only on
  a branch no run takes, used to be hoisted into an eager argument and raise
  `NameError` at every call (found by the new property test); a helper
  defined below its callers, called once at import time before its
  definition, raised the same way. Both are now thunks, and a helper defined
  below its callers is passed as `lambda: helper` unless the block reads it
  first.
- A call site that would pass a callee as
  `lambda *args, **kwargs: callee(*args, **kwargs)` is declined by name
  (`forwarded_callee`). It used to be declined by accident, because the
  lambda's own parameters were counted as names the site could not resolve;
  that miscount also declined any thunk containing a lambda, such as
  `lambda: sorted(items, key=lambda x: x)`, which now extracts.
- A comprehension variable is no longer thunked.
- The frame-reading builtins and `eval`/`exec` anywhere in the enclosing
  function decline the block, not only inside it, and aliases of them are
  resolved through bindings.
- `warnings.warn` without `stacklevel`, and a `stacklevel` reached through
  an alias, decline the block.
- An object bound in the block whose lifetime a later read observes (a
  temporary file, a weak reference) is returned from the helper rather
  than dropped when the helper's frame ends.
- A cross-file helper import goes after the host's leading executable
  statements.
- A form feed or a Unicode line separator in a source no longer crashes the
  splice: lines are counted the way the tokenizer counts them.
- Async comprehensions are declined.
- A rendering failure in one proposal ends that proposal, not the run.
- A relative output path no longer ends the fixed point early.
- Type inference no longer switches off when the working directory is
  above the output.
- The transaction journal lives under the project root, and only that root
  is checked for one.
- A killed run's pyright probe cannot be mistaken for source.
- The mypy cache and partial-copy directories are cleaned on SIGTERM.
- An ASCII locale no longer breaks a run.
- The change sidecar is written atomically.
- `--interactive` with a closed stdin declines instead of raising.
- Settings are read from the environment once per run.
- Three ways an extraction could change what a program does, each found by
  executing crafted inputs before and after refactoring: a closure created
  inside the block over a name the block binds (a loop target) kept the
  helper's cell where the original saw the caller's later rebinding; an
  argument-free `dir()` read the helper's frame; and a differing free name
  was passed eagerly on the assumption that a name has no failure mode,
  so a global the module never binds raised `NameError` at the call site
  where the original read it only in a branch it did not take. The first
  two are declined; a bare name is hoisted only when the call site can
  resolve it, and passed as a thunk otherwise.
- Single-file `dry` on a file it cannot decode copied it and then stopped
  with a codec message naming nothing, and on a file with a syntax error
  reported success over an unchanged copy; both are an error naming the
  file (and the line). A negative `--max-refactorings` was accepted and
  meant "run to a fixed point"; it is refused. `preview` of one file showed
  a progress bar under `--progress none`, and the banner said helpers go at
  the end of files.
- With `--types`, the variables a helper returns are revealed under each
  call site's own spelling; a site whose names alpha-renaming spelled
  differently from the helper's used to probe names that did not exist
  there, so the helper's return type stayed `Any` and parameter unions the
  return type would have invalidated went unchecked.
- A refactored file keeps its own encoding, byte-order mark and newline
  convention: a CRLF file used to come back with every line changed, a file
  with a UTF-8 BOM was skipped, and one latin-1 file with a coding cookie
  failed a whole directory run. Sources are decoded as the interpreter
  decodes them and written back in the file's own bytes; `rename-helpers`
  reads the same way and keeps each file's encoding.
- A project whose packaging layout Towel cannot model (a `hatch.toml`, some
  flit, poetry and pdm forms) no longer aborts the run when it has a
  cross-file candidate: the pair is declined (`unknown_layout` in the
  rejection trace) and every same-file extraction proceeds. Layout
  discovery raises `UnsupportedLayoutError`, and the command line reports
  any `TowelError` as an error line instead of a traceback.
- `--progress none` is silent through the localized and stale re-analyses
  that follow each applied proposal, the single-file driver takes a
  progress mode, and `preview` accepts `--progress`. Both drivers run to a
  fixed point by default, as the CLI always did; they used to stop after
  ten iterations, and the goldens generated through them were ten-step
  snapshots rather than fixed points.
- An import sorter's result is accepted only if it permutes or merges import
  statements, at any depth (a sorter also orders the imports under
  `if TYPE_CHECKING:`); anything else is discarded and the file is left as
  Towel assembled it, rather than failing the refactoring (trio).
- `rename-helpers --list --json` no longer crashes on a symlinked module.
- A confirmation prompt at a closed stdin declines instead of raising.
- An isort skip setting, a failing or hung ruff, a hung pyright, and pyright
  output of an unexpected shape each degrade with a warning naming the file
  instead of aborting or being swallowed.
- A non-integer `TOWEL_WORKERS` is reported, and a configured worker count
  never reaches a platform without `fork`.
- A corrupt transaction manifest and a concurrent transaction are reported
  as the journal's own errors.
- The engine forgets a rewritten file's cached lines when it invalidates
  the file.
- The parallel fallback no longer hides worker exceptions behind a serial
  retry.
- The command line's stderr handler follows a redirected stderr.
- The cross-file fixture package named `lib` was hidden from fresh checkouts
  by the `lib/` ignore pattern; the pattern exempts the fixtures.
- Two blocks that differed only in the spelling of a lambda's parameter
  (`lambda value: value * 2` against `lambda other: other * 2`), or of an
  assignment expression's target (`(t := f())` against `(temp := f())`),
  were declined. Lambda parameters are now renamed within their own lambda
  by the instantiation check, which leaves a free name spelled the same
  outside the lambda alone, and walrus targets are block-level binders for
  the unifier, since an assignment expression binds in the enclosing scope.
- A block that binds a variable read after it is extracted again, with the
  helper returning the variable and the call rebinding it (`total =
  helper(order)`), as the README has always shown. The orphan guard added for
  networkx in 1.618 did not know the generated call rebinds a returned
  variable, so it rejected every such block; only blocks that returned or
  bound nothing live could be extracted.
- The import-cycle guard treats `from . import name` (and `from .. import
  name`) as an edge to the package's `__init__`, which that import runs
  whether or not `name` is a submodule. A helper hosted in a submodule that
  reaches back into its package this way was imported by the package
  initializer, which the submodule then imported half-initialized
  (beautifulsoup4's tests package). The initializer, which the submodule
  already imports, is now the host.
- The import-cycle guard resolves a file's own package's absolute imports
  inside the tree being refactored, by suffix, even when a full-path match
  exists elsewhere: an out-of-place output beside the original clone
  (`sphinx-cleaned` next to `sphinx`) had `from sphinx.transforms import X`
  resolved against the original, where the helper import that closed the
  cycle did not exist, and sphinx's transforms package broke on import.
- A union of forward references is written as one string (`'Left | Right'`),
  not as `'Left' | 'Right'`, which is a TypeError at definition (Towel's own
  suite, refactored by Towel).
- Definite-assignment analysis now knows that `except E as name` deletes
  `name` when the handler exits, and that a `del` nested in a branch unbinds
  its target on that path. The returned-variable check relies on this; without
  it a helper could return a handler's name and raise `UnboundLocalError`
  where the original code did not.

### Security
- Pyright runs with Towel's interpreter (`--pythonpath`), so a `venv`
  setting in the analyzed project's pyright configuration can no longer
  execute that project's interpreter; SECURITY.md says exactly what each
  checker does and when to pass `--no-types`. Ruff and pyright are resolved
  from the current interpreter before PATH. The ecosystem job runs
  third-party test suites without the checkout token persisted and with a
  scratch HOME; the workflows pin actions to commits. The rename file is
  read as UTF-8 and the change sidecar is never written through a symlink.
- The pyright probe is created with `mkstemp` in the user's package
  (exclusive, owner-only, never following a symlink) and removed at
  interpreter exit if a crash skips the cleanup; it used to be a
  predictable, world-readable file left behind by a kill.
- The ecosystem check (`scripts/ecosystem_check.py`, `just ecosystem`)
  clones public repositories and runs their setup, dependency installation
  and test suites with the caller's privileges. It now refuses to run
  unless the caller passes `--run-untrusted-code` or sets
  `TOWEL_ECOSYSTEM_RUN_UNTRUSTED=1`, the refusal says what would execute
  and recommends a disposable machine or container, and each manifest
  entry's `prepare` command runs without a shell. Every project in the
  manifest is pinned to the full commit the release-gate run tested rather
  than `HEAD`, so a hijacked upstream cannot put unreviewed code into the
  run; `--print-pins` lists the commits a run tested for refreshing the pins.
  `towel-main` alone still tracks `main`. Its work directory is a fresh
  private temporary directory unless `--work` names one, so another local
  user cannot pre-create the tree it clones into and executes from.

## [1.618] - 2026-09-17

### Changed
- Cross-file helpers are imported relatively by default (`from .module import
  helper`), which stays valid when an out-of-place output is adopted into its
  real location. An absolute import is used only when a packaging marker
  (`pyproject.toml`/`setup.*`) anchors the module name, so the previous
  behavior is preserved for in-place refactoring of a packaged project.
- Trivial forwarding helpers are no longer proposed: a block whose helper body
  would be a single `raise`, a `return` of one call, or a bare call adds
  indirection without sharing logic. Construct the engine with
  `skip_trivial_helpers=False` to restore the old behavior.
- A cross-file helper is placed in a module that does not close an import cycle.
  When the shared block spans modules, the helper is hosted in one the others
  already import rather than adding a back-edge; the extraction is declined only
  when no placement is safe (a genuine pre-existing cycle). This replaces the
  earlier behavior of hosting the helper in the first module and declining
  whenever that would cycle.

### Fixed
- The rename tool no longer refuses a module merely because it contains a local
  variable or parameter named `vars`, `globals`, `locals`, `eval`, or `exec`;
  it flags only a genuine reference to the builtin.

## [1.414] - 2026-09-15

First release prepared under the open-source audit. Beta: the engineering and
evidence are strong, but Towel rewrites source code and has documented
limitations it cannot always detect, so preview, review the diff, and run the
affected project's own tests.

### Added
- Cross-file refactoring with import-root inference for setuptools, Hatch,
  Flit, Poetry, and pdm project layouts.
- `towel rename-helpers`: a JSON inventory of extracted helpers and an atomic,
  checked rename batch, intended to be driven by a coding assistant.
- `towel dry --exclude DIR` to leave a directory out of directory mode.
- A pre-run scan that warns, before refactoring a directory, about modules that
  inspect frames or tracebacks, attribute warnings by `stacklevel`, or read
  source through `inspect.getsource`.
- A standing ecosystem check over 91 public projects
  (`scripts/ecosystem_check.py`, `just ecosystem`), run weekly in CI.
- Fork-based parallel analysis, enabled by a timed probe and capped by
  available memory, with a per-worker watchdog so a killed run leaves no
  workers behind. `TOWEL_WORKERS` controls it.

### Changed
- Every accepted proposal is verified by instantiating the helper with each
  call site's arguments and comparing against the block it replaces, up to
  renamed binders. Arguments that could have observable effects or fresh
  identity are evaluated inside the helper at their original position.
- Analysis is memoized by block structure and shares parsed graphs by
  reference, checked under `TOWEL_CHECK_AST_IMMUTABLE=1`.
- Minimum Python is 3.11; the matrix covers 3.11 through 3.13.

### Fixed
- A large set of soundness and scope defects found by adversarial batteries and
  the ecosystem check, each now covered by a fixture and a row in
  [docs/ADVERSARIAL_REVIEW.md](docs/ADVERSARIAL_REVIEW.md).

### Notes
- The PyPI releases `1.0.0`–`1.0.4` are yanked for broken import handling and
  are not a recommended installation target.

[Unreleased]: https://github.com/ericeallen/towel/compare/v1.618...HEAD
[1.618]: https://github.com/ericeallen/towel/compare/v1.414...v1.618
[1.414]: https://github.com/ericeallen/towel/releases/tag/v1.414
