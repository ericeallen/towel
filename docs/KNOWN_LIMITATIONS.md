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
  `exec`, zero-argument `super()`, `break`/`continue` targeting an outer loop,
  comprehension assignment expressions, `warnings.warn(..., stacklevel=)`,
  or direct frame or stack inspection are rejected.
- **Unbound locals.** A free variable that is a local of the containing
  function, bound before the block only on some path, is passed as a thunk
  so it is read where the block read it. Definite assignment is computed
  conservatively: loops, `contextlib.suppress`, and non-exhaustive `match`
  statements never bind definitely.
- **Rendering.** Every generated file compiles, and every generated call binds
  to the generated helper's signature after method conversion.

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
  suspension, `locals()`/`globals()`/`vars()`/`super()` with no arguments,
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
- **Not detected, and therefore silent.** Three kinds of frame or source
  sensitivity are outside both the guard and the warning, and are documented
  `BROKEN_KNOWN` cases in the ecosystem check rather than things Towel can flag:
  a module that reads a *sibling's* source as text through a plain `open` of a
  `__file__`-relative path and copies regions of it (lark's standalone parser
  generator); a *caller or test* that asserts on the exact frames or text of a
  traceback the refactored code raises normally (glom and rich assert on
  rendered tracebacks); and a plain `warnings.warn` with no `stacklevel`, whose
  once-per-location deduplication is keyed on the line number, so moving code
  can change how many warnings a run reports without changing any test result.
  None of these can be distinguished statically from safe code that does the
  same thing (a linter also opens `.py` files; every library raises
  exceptions), so Towel does not warn on them to avoid a flood of false
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
  in a module, after imports. Cross-file helpers add a module import; static
  local import cycles are rejected, dynamic ones are not detected.
- **Concurrency of application.** Files are replaced atomically one at a time;
  a batch is not atomic across files. Application requires exclusive write
  access; a concurrent editor writing in the check/replace interval is not
  prevented. Interrupted batches leave a recovery journal.

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

## Conservative rejections

Towel prefers to leave code unchanged rather than transform it under
uncertainty. Common reasons a real duplicate is not extracted:

- A differing sub-expression is a slice, a starred item, or a whole f-string;
  these are container syntax rather than values.
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
- Project layouts other than setuptools, Hatch, Flit, Poetry, and pdm
  conventions are refused for directory mode because import roots cannot be
  inferred safely; the ecosystem check reports these as `UNSUPPORTED`.
  Poetry ``packages`` entries with ``to`` or glob patterns, and a pdm
  ``package-dir`` pattern, are refused likewise.

Set `DEBUG_PROPOSAL_REJECTIONS=1` to print the reason for each rejected pair.

## Performance

Analysis is quadratic in candidate blocks per file. Five measures keep it
tractable, all exact: they change no proposal.

- Blocks that can never be accepted are not enumerated: one that returns on
  some path but not every path, or a lone expression statement. On pyflakes'
  2,167-line `test_other.py` that removes 32,857 of 44,826 rejected pairs.
- Every analysis result is cached by the block's structure, not by node
  identity, so a fixed-point iteration that re-parses a file still reuses
  results for the blocks it did not touch. Unification results are stored as
  positions and rehydrated onto the matching blocks.
- The clustering pass memoizes its per-candidate pipeline on the template,
  the candidate, and the pair's helper.
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

Measured with the CLI defaults (macOS, Python 3.13, single core unless
stated), before and after this pass, with identical output in every case:

| Target | Before | After, one core | After, forking |
|---|---|---|---|
| pyflakes `test_other.py`, one analysis | more than 540 s, capped | 30.6 s | 7.4 s |
| boltons, fixed point | 32 s | 17 s | 15 s |
| pygments, fixed point | 170 s | 107 s | 60 s |
| pyflakes, fixed point | 300 s | 224 s | 54 s |

The remaining cost is the pairwise evaluation of structurally distinct
candidates, which no cache can share; large test modules with hundreds of
similar methods remain the worst case. Progress is reported per phase.
There is no time budget; interrupt with Ctrl-C, which leaves files
unchanged. The ecosystem check applies a 30-minute limit per phase and
reports `TIMEOUT`.

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
`TOWEL_WORKERS=1`. Forking multiplies that: each worker is a copy-on-write fork
whose caches then diverge, so peak memory scales with the worker count.
networkx (200,000 lines, tests excluded) peaked at about 1 GB in one process
with `TOWEL_WORKERS=1`, and near 7.4 GB across twenty processes when forking
on an 18-core machine. The engine estimates the parent's resident
size against physical memory at fork time and caps the workers at about a third
of RAM, but that is an estimate, not a guarantee; on a memory-constrained
machine, or when running several large refactorings at once, set
`TOWEL_WORKERS` low. At `TOWEL_WORKERS=1` the footprint stays at the
single-process figure above.
