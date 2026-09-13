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

- **Tracebacks, `warnings.warn(stacklevel=...)`, and frame inspection.** A
  helper adds a frame. Code that inspects `sys._getframe`, walks tracebacks, or
  relies on `stacklevel` to attribute a warning to a specific caller will see
  the helper instead. Only direct calls to the frame-sensitive builtins listed
  above are rejected; aliased or indirect inspection is not detected.
- **Frame-relative callees.** A called function that itself uses
  `warnings.warn(stacklevel=...)` or inspects the stack sees one more frame.
  Only direct calls in the block are detected; pluggy's argument validation
  is the documented example in the ecosystem check.
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
module-level common ancestor, every decorator on the source methods is known
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
- A block that begins at an `elif` is never extracted, because its call
  would have to be rendered inside the preceding branch's `else`; the
  `elif`'s own body and further branches remain candidates. This gives up a
  valid extraction when the preceding branch always exits (tabulate).
- Project layouts other than setuptools, Hatch, and Flit conventions are
  refused for directory mode because import roots cannot be inferred safely;
  the ecosystem check reports these as `UNSUPPORTED` (tomlkit, Poetry).

Set `DEBUG_PROPOSAL_REJECTIONS=1` to print the reason for each rejected pair.

## Performance

Analysis is quadratic in candidate blocks per file. Measured with the CLI
defaults on September 13, 2026 (macOS, Python 3.13): a single 5,000-line
module reaches a fixed point in about 23 seconds; a 4,000-line package with
its tests applied 18 extractions in about 6 minutes; an 18,000-line package
completes in about 2.5 minutes. The worst case is a module of many small,
similar functions: pyflakes' 2,167-line `test_other.py` forms 444,250
candidate pairs. One analysis pass over it took more than nine minutes
before the safety guards, unification, and per-block binding analyses were
memoized per (function, block) within an analysis, and about one minute
after; unification had been repeated for 98 percent of its calls because
the clustering pass unifies one template against the same candidates for
every pair that shares it. Progress is reported per phase. There is no time
budget; interrupt with Ctrl-C, which leaves files unchanged. The ecosystem
check applies a 30-minute limit per phase and reports `TIMEOUT`.
