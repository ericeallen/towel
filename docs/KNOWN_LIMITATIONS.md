# Known limitations

This document states what Towel verifies about a transformation, what it
rejects, and what remains outside its model. Read it together with
[the production readiness report](PRODUCTION_READINESS.md) and
[the adversarial review](ADVERSARIAL_REVIEW.md).

## What is verified for every accepted proposal

- **Instantiation.** The helper body, with each call site's actual arguments
  substituted for its parameters (thunks beta-reduced), must reproduce the
  block it replaces up to the renaming of names the block itself binds. A
  proposal whose unification, substitution, or renaming disagree is rejected
  before it is offered. (`src/towel/unification/instantiation.py`)
- **Argument evaluation.** Only names, literals, and tuples of those are
  passed eagerly. Every other differing expression is passed as a
  zero-argument thunk and evaluated inside the helper at the original
  position, so it runs as often, as late, and as conditionally as before.
  Expressions that read names bound inside the block are lambda-lifted with
  those names as arguments. Expressions in call position are forwarded lazily.
  A thunk the helper evaluates first, exactly once, and before any other
  effect is passed eagerly after all, because the call site's evaluation is
  then indistinguishable from the in-place one (`thunk_inlining.py`).
- **Binding discipline.** The block may not rebind, delete, or `except ... as`
  a name bound before it; may not carry a `global`/`nonlocal` declaration the
  caller still uses; may not rebind a name a closure outside the block reads;
  and may not define a closure over a name the caller rebinds after the block.
  Names bound in the block and read afterwards are returned, including targets
  of annotated assignments and assignment expressions; a returned name must be
  definitely bound where the block ends or have entered as a parameter.
- **Frame and control flow.** Blocks containing `yield`, `await`, `async`
  loops or context managers, `locals()`, `globals()`, `vars()`, `eval`,
  `exec`, zero-argument `super()` or `dir()`, `break`/`continue` targeting an
  outer loop,
  comprehension assignment expressions, `warnings.warn(..., stacklevel=)`,
  or direct frame or stack inspection are rejected.
- **Unbound locals.** A free variable that is a local of the containing
  function, bound before the block only on some path, is passed as a thunk
  so it is read where the block read it. Definite assignment is computed
  conservatively: loops, `contextlib.suppress`, and non-exhaustive `match`
  statements never bind definitely.
- **Rendering.** Every generated file compiles, and every generated call binds
  to the generated helper's signature after method conversion. A formatter
  may change only layout: each inserted snippet's syntax tree is compared
  before and after formatting, and an import sorter's result is kept only
  when it permutes or merges import statements and nothing else.
- **Annotations.** With the project's type checker installed, every annotated
  helper and its call sites are type-checked in place; a change that
  introduces a type error has its annotations replaced by `Any`, and then
  removed, before it is applied. A helper is annotated only in code that
  already uses annotations, from what the sites declare and what the checker
  reveals; see *Type annotations on helpers* below.

These checks are syntactic and local. They establish that the helper is a
faithful generalization of each block under Python's lexical scoping. They do
not establish behavioral equivalence for programs that observe their own
frames, names, or source.

## Observable differences that remain

Some behavior is outside any static model: a program that reads its own call
stack, the active traceback, or its own source can observe that a helper adds
a frame or shifts line numbers, even though the value the program computes is
unchanged. Towel handles this in three layers, and it is worth being explicit
about where each one stops.

- **Rejected outright.** A block that *itself* contains generator or async
  suspension, `locals()`/`globals()`/`vars()`/`dir()`/`super()` with no
  arguments,
  `eval`/`exec`, direct frame or stack inspection (`sys._getframe`,
  `inspect.stack`, and the like), or `warnings.warn(..., stacklevel=...)` is
  never extracted. This is exact for constructs written directly in the block.
- **Warned before the run.** Directory mode scans every module first and prints
  a stderr warning naming the files that inspect frames or tracebacks,
  attribute warnings by `stacklevel`, or read source through
  `inspect.getsource`. This catches frame sensitivity that a call chain hides
  from the block-level guard, such as pluggy's argument validation, where the
  extracted block calls a function that warns with `stacklevel`. The warning
  tells you which diffs to review or `--exclude`; it is a pointer, not a proof
  of breakage.
- **Not detected, and therefore silent.** Four kinds of frame, line, or
  source sensitivity are outside both the guard and the warning, and are
  documented `BROKEN_KNOWN` cases in the ecosystem check rather than things
  Towel can flag: a module that reads a *sibling's* source as text through a
  plain `open` of a `__file__`-relative path and copies regions of it (lark's
  standalone parser generator); a *caller or test* that asserts on the exact
  frames or text of a traceback the refactored code raises normally (glom and
  rich assert on rendered tracebacks); a test that asserts the exact line
  number a warning is issued from inside its own module, which a helper
  inserted above it shifts (trio's `test_deprecate`); and a plain
  `warnings.warn` with no `stacklevel`, whose once-per-location deduplication
  is keyed on the line number, so moving code can change how many warnings a
  run reports without changing any test result. None of these can be
  distinguished statically from safe code that does the same thing (a linter
  also opens `.py` files; every library raises exceptions; every module has
  line numbers), so Towel does not warn on them to avoid a flood of false
  positives. Review the diff and run the tests, as with any refactoring.

The remaining entries are other kinds of dynamic behavior that no scan
addresses:

- **Reflection and dynamic rebinding.** Code that rebinds module globals or
  closure cells through `globals()[...]`, `setattr(module, ...)`, `exec`, or
  from another thread between two reads inside a block is outside the model.
  Direct calls to the reflection builtins are rejected; aliased or external
  rebinding is not detected.
- **Metaclasses and descriptors.** Method extraction into a class assumes the
  usual descriptor protocol. Methods decorated with anything other than the
  recognized receiver-preserving decorators receive a module-level helper with
  the receiver passed explicitly. Custom metaclasses that alter attribute
  lookup, `__init_subclass__` hooks, and `__slots__` interactions with added
  methods are not modeled beyond compilation.
- **Import-time behavior.** Helpers are inserted before the first definition
  in a module, after imports, except that a helper whose annotations name
  classes or functions of the module goes after the last of them, so the
  names can be written bare, when no statement before that point could run
  code at import time. Cross-file helpers add a module import; static local
  import cycles are rejected (including cycles through a package's
  `__init__`, which `from . import name` runs), dynamic ones are not
  detected.
- **Concurrency of application.** Files are replaced atomically one at a time;
  a batch is not atomic across files. Application requires exclusive write
  access; a concurrent editor writing in the check/replace interval is not
  prevented. Interrupted batches leave a recovery journal.

### A cross-file helper adds an import of its host module

A helper shared across modules lives in one of them and the others import it.
The host is chosen so that no import cycle closes, preferring a module the
borrowers already import; when none qualifies, one borrower gains a new
import edge. Towel does not know whether importing that module has
requirements of its own: gunicorn's `workers/gtornado.py` raises at import
time unless tornado is installed, and a helper hosted there made
`workers/sync.py` import it, so environments without tornado could no longer
import the sync worker. Review new cross-module imports in the diff with that
in mind, and host such helpers in a neutral module by hand when it matters.

## Method insertion

A helper becomes a method only when both blocks belong to functions defined
directly in one unique module-level class, or in classes with a unique
module-level common ancestor (a base name resolves through the referencing
module's own unconditional imports, never by name across the project, so a
base bound by a conditional or star import contributes no ancestor), every
decorator on the source methods is known
to preserve the receiver, and the methods have a first parameter named
`self` (or the method is a `classmethod`). Local classes, duplicated class names, unknown
decorators, functions nested inside methods, and class-body functions with
no parameter or a first parameter other than `self` get a module-level helper that takes the
receiver explicitly. Additional call sites gathered from the same file join
a method helper only when they are methods of the same classes with the same
receiver kind; other occurrences keep their code.

## Type annotations on helpers

Annotations are written only from evidence, and their limits follow from
where the evidence comes from:

- A helper is annotated only when some call site's enclosing function is
  itself annotated. An unannotated project stays unannotated.
- What the sites declare is copied: a parameter whose every argument is an
  annotated, never-rebound parameter of the enclosing function, or a literal
  of one builtin type; a return declared by every site's function, by the
  annotated locals the helper returns, or `None` for a helper that returns
  nothing. Copying does not reason: without a type checker installed, sites
  that disagree on a parameter join into an unreduced union, and sites whose
  declared return types differ leave the return bare.
- Everything else needs the project's type checker (mypy, or pyright when
  that is what the project configures, or both), installed with the `types`
  extra: the type of an argument that is an expression, the reduction of a
  union by subtyping, the meet of declared return types, the check that a
  revealed return type is a subtype of every declaration, and the
  verification of the generated code. Without a checker none of this
  happens, and `--types` only copies.
- A revealed type is written only when every name in it resolves where the
  helper is defined; a type the checker spells with a module the host does
  not import is not written, and the parameter is completed with `Any`.
  `Any` inside a composite (`list[Any]`) is written as the checker revealed
  it. A helper with any annotation has every parameter and its return
  annotated, so the checker's incomplete-definition rule is never tripped.
- Generic helpers are not synthesized: when sites pass `list[int]` and
  `list[str]`, the parameter is their union, not a type variable.
- Placement for bare names holds only within one module. For a helper
  whose sites are in other modules, an annotation may name only builtins
  and the `typing` names Towel imports itself (`Any`, `Callable`), since a
  site's imports are not the host's; a type that names a class is then not
  written and the parameter is completed with `Any`. Any subscripted
  annotation that would not evaluate at definition time (`memoryview[int]`
  on an interpreter where `memoryview` is not generic) is written as a
  string.
- The degradation on a type error is per proposal, not per parameter: one
  annotation the checker rejects costs the helper all of them.
- Pyright reads files, so while it is consulted a probe copy of the module
  exists beside it in the package, created exclusively with owner-only
  permissions under a unique `_towel_probe_` name and removed afterwards,
  or at interpreter exit if a crash skipped the cleanup. A kill signal can
  leave it behind; it imports nothing the module does not.

## Conservative rejections

Towel prefers to leave code unchanged rather than transform it under
uncertainty. Common reasons a real duplicate is not extracted:

- A differing sub-expression is a slice, a starred item, or a whole f-string;
  these are container syntax rather than values.
- The extracted helper body would be a single forwarding statement — a lone
  `raise`, a `return` of one call, or a bare call — or a forwarding
  statement whose result is bound and returned (`x = f(...)` then `return
  x`, or the tuple form), or a body that only binds parameters and literals
  to names and returns them. Such a helper shares no logic, only a name, so
  it is skipped by default; construct the engine with
  `skip_trivial_helpers=False` to keep it.
- A duplicate that is the whole body of an existing function is redirected
  to that function rather than extracted, but only when the function is a
  plain module-level `def`: a decorated, async, variadic, shadowed, or
  rebound function, or one whose call across files would close an import
  cycle, falls back to ordinary extraction (which the trivial-helper filter
  then usually declines, since the helper would restate the function).
- The block deletes, rebinds, or declares a name the caller keeps using.
- A nested function or lambda shares a rebound name with the block.
- The helper would need more than the configured maximum parameters.
- A match capture, `with` target, or exception name would have to cross the
  block boundary in a way the return analysis does not represent.
- Lambda expressions with positional-only, keyword-only, or variadic
  parameters are not unified.
- A name the block binds that later code reads after only a *conditional*
  rebinding is treated as orphaned and the block is rejected, even where the
  helper would return it (the annotated-assignment fixture r86 is now
  rejected for this reason). Returning such names was found unsafe in three
  fixtures; a path-aware return analysis would recover the case.
- A block that begins at an `elif` is never extracted, because its call
  would have to be rendered inside the preceding branch's `else`; the
  `elif`'s own body and further branches remain candidates. This gives up a
  valid extraction when the preceding branch always exits (tabulate).
- setuptools, Hatch, Flit, Poetry, and pdm layouts are read from their own
  configuration. Any other build backend (for example ``flit_scm``) falls back
  to conventional inference: a package or module named after the distribution,
  in the project root or under ``src``. A project whose layout cannot be
  resolved either way is refused for directory mode, and the ecosystem check
  reports it as `UNSUPPORTED`. Poetry ``packages`` entries with ``to`` or glob
  patterns, and a pdm ``package-dir`` pattern, are refused likewise.

Set `DEBUG_PROPOSAL_REJECTIONS=1` to log the reason for each rejected pair
(the `towel.rejections` logger, at DEBUG, on stderr).

## Performance

Analysis is quadratic in candidate blocks per file. The measures below keep
it tractable, all exact: they change no proposal.

- Blocks that can never be accepted are not enumerated: one that returns on
  some path but not every path, or a lone expression statement. On pyflakes'
  2,167-line `test_other.py` that removes 32,857 of 44,826 rejected pairs.
- Every analysis result is cached by the block's structure, not by node
  identity, so a fixed-point iteration that re-parses a file still reuses
  results for the blocks it did not touch. Unification results are stored as
  positions and rehydrated onto the matching blocks.
- The clustering pass scans a file for the sites that can share a helper
  once per distinct helper template, not once per pair (every pair of N
  near-identical blocks renders the same template; 50 identical functions
  took 17 s and 100 took 134 s before this pass and the reuse index below;
  re-measured with `scripts/bench_similar_blocks.py` on September 18, 2026,
  one core of a machine shared with other work, 50 take 6.8 s and 100 take
  36 s), memoizes its per-candidate pipeline on the
  template, the candidate, and the pair's helper, and applies its
  constant-time filters before the semantic guards. The remaining growth is
  cubic: every one of the N²/2 pairs legitimately proposes the same N-site
  extraction until the first application collapses them.
- The reuse redirect finds a function whose body starts where a site does
  through an index, instead of scanning every function of the file for
  every replacement of every proposal.
- Three pure per-block analyses (orphan detection, the instantiation check's
  normalized block, the class-private-name scan) are memoized on structure,
  so the re-parse after each applied proposal hits too: on a 5,300-line
  test package the profiled run fell from 120 s to 97 s with 13,300 private
  name scans reduced to 24.
- The analysis session grows to the number of files an analysis covers, so
  a project above the old 128-file limit (trio: 144) no longer re-parses
  every file on every pass; trio's second pass went from 144 parses and
  7.2 s to none and 5.0 s.
- Facts that depend only on a function, not on the block under test
  (definite assignment at each statement, locally bound names, nested
  scopes), are computed once per function, and candidate blocks are bucketed
  on their whole statement-type sequence, which the unifier requires equal,
  so most pairs are never formed. On Towel's own 16,000 lines these two
  remove about half of all function calls since 1.618 with an identical
  proposal list.
- In directory mode, a global re-pass after the first re-pairs only the
  functions in files rewritten since the previous global pass. This is
  exact; the argument is in
  [ARCHITECTURE.md](ARCHITECTURE.md#incremental-global-passes-and-why-they-are-exact).
- The import-cycle check parses each module once per analysis and caches
  its import edges by path, modification time, and size; it used to
  re-parse every reachable module for every cross-file pair, which on a
  243-module project cost about a second per pair.
- A large cold analysis forks workers after parsing; they inherit the ASTs
  and caches copy-on-write and return only accepted proposals. Forking is
  decided by a timed serial probe, never by pair count alone, because a
  pool per fixed-point iteration costs more than small iterations save.
  `TOWEL_WORKERS=1` disables it; any other value caps the worker count.
  Each worker runs a watchdog thread that ends the worker within a second
  of its parent's death, wherever the worker is (mid-pair or waiting on
  the pool's queue, where a plain pool worker would wait forever because
  its siblings hold the queue open); killing a run leaves no workers
  behind, verified by `tests/test_parent_watchdog.py`. The worker count is
  also capped by the parent's resident size against physical memory at
  fork time, an estimate rather than a guarantee: several simultaneous
  large runs on one machine should still set `TOWEL_WORKERS` low.

Measured in September 2026 with the CLI defaults (macOS, Python 3.13,
single core unless stated): the wall time of a whole `towel dry` run on the
ecosystem check's clone of each project, before and after the measures
above, with identical output in every case. This is the one measured table
of package timings; the README refers here rather than repeating figures.

| Target | Before | After, one core | After, forking |
|---|---|---|---|
| pyflakes `test_other.py`, one analysis | more than 540 s, capped | 30.6 s | 7.4 s |
| boltons, fixed point | 32 s | 17 s | 15 s |
| pygments, fixed point | 170 s | 107 s | 60 s |
| pyflakes, fixed point | 300 s | 224 s | 54 s |

A later pass (September 2026) added the per-function facts, the
statement-sequence buckets, and the exact incremental global passes, and
turned on formatting and typing by default. Measured the same way, one
core, identical output between the on and off settings of each:

| Target | 1.618 | Now, `--no-types --no-format` | Now, defaults |
|---|---|---|---|
| Towel's own source, the 1.618 snapshot the exactness baselines use (16,000 lines, 45 applied), fixed point | 47.8 s | 33.9 s | 41.8 s |
| h2 (hyper-h2), fixed point | 5.2 s | 7.0 s | 11.9 s |
| Sphinx, fixed point, in the ecosystem check | 2513 s | not measured | 2058 s |

The bare-engine speedup is what the caches and buckets buy; the defaults
then spend part of it type-checking each applied refactoring, a cost
proportional to the number of applied changes rather than to project size.
The current engine also applies more refactorings than 1.618 did on the
same input (h2: 20 against 14; Towel's source: 45 against 41), so the
times compare a larger amount of work. The times depend on the input as
much as on the engine: today's Towel source, after the later audits'
removals, has 12 duplicates to apply and runs in 4.7 s without the type
checker and formatter and 9.5 s with them (one core, September 18, 2026,
on a machine shared with other work).

The remaining cost is the pairwise evaluation of structurally distinct
candidates, which no cache can share; large test modules with hundreds of
similar methods remain the worst case. Progress is reported per phase.
There is no time budget; interrupt with Ctrl-C, which leaves files
unchanged. The ecosystem check applies a 30-minute limit per phase by
default, longer for named projects, and reports `TIMEOUT`. It executes
the manifest's projects with the caller's privileges and refuses to run
without `--run-untrusted-code`; use a disposable machine or container.

## Resources and platform

Towel runs on Python 3.11 to 3.13 on a POSIX system. Applying changes needs
POSIX filesystem semantics. Parallel analysis uses the `fork` start method, so
where `fork` is unavailable (Windows, or a non-`fork` start method) the tool
runs correctly on a single core and produces the same output.

One core suffices; more cores shorten a large analysis. Memory is the binding
constraint on the largest projects. A single analysis process holds the parsed
modules and its bounded caches: about 36 MB for one small module, 85 MB for
boltons (24,000 lines), 103 MB for Click (29,000 lines), and 247 MB for
pygments (137,000 lines), measured as peak resident size with
`TOWEL_WORKERS=1` and `--no-types`. With the type checker on (the default
when mypy is installed) mypy runs in-process and its own footprint is added:
Towel's source peaks at about 160 MB without it and about 900 MB with it. Forking multiplies that: each worker is a copy-on-write fork
whose caches then diverge, so peak memory scales with the worker count.
networkx (200,000 lines, tests excluded) peaked at about 1 GB in one process
with `TOWEL_WORKERS=1`, and near 7.4 GB across twenty processes when forking
on an 18-core machine. The engine estimates the parent's resident
size against physical memory at fork time and caps the workers at about a third
of RAM, but that is an estimate, not a guarantee; on a memory-constrained
machine, or when running several large refactorings at once, set
`TOWEL_WORKERS` low. At `TOWEL_WORKERS=1` the footprint stays at the
single-process figure above.
