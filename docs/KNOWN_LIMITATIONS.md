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
- **Argument evaluation.** Only names, literals, and containers of those are
  passed eagerly. Every other differing expression is passed as a
  zero-argument thunk and evaluated inside the helper at the original
  position, so it runs as often, as late, and as conditionally as before.
  Expressions that read names bound inside the block are lambda-lifted with
  those names as arguments. Expressions in call position are forwarded lazily.
- **Binding discipline.** The block may not rebind, delete, or `except ... as`
  a name bound before it; may not carry a `global`/`nonlocal` declaration the
  caller still uses; may not rebind a name a closure outside the block reads;
  and may not define a closure over a name the caller rebinds after the block.
  Names bound in the block and read afterwards are returned.
- **Frame and control flow.** Blocks containing `yield`, `await`, `async`
  loops or context managers, `locals()`, `globals()`, `vars()`, `eval`,
  `exec`, zero-argument `super()`, `break`/`continue` targeting an outer loop,
  or comprehension assignment expressions are rejected.
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
- **Unbound-local timing.** A local that may be unbound is read at the call
  site when passed eagerly, so an `UnboundLocalError` can move from inside a
  branch that would not have executed to the call itself.
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
- Project layouts other than setuptools, Hatch, and Flit conventions are
  refused for directory mode because import roots cannot be inferred safely.

Set `DEBUG_PROPOSAL_REJECTIONS=1` to print the reason for each rejected pair.

## Performance

Analysis is quadratic in candidate blocks per file. Measured with the CLI
defaults on September 13, 2026 (macOS, Python 3.13): a single 5,000-line
module reaches a fixed point in about 23 seconds; a 4,000-line package with
its tests applied 18 extractions in about 6 minutes; an 18,000-line package
completes in about 2.5 minutes. Progress is reported per phase. There is no
time budget; interrupt with Ctrl-C, which leaves files unchanged.
