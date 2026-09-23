# Known limitations

This document states what Towel verifies about a transformation, what it
rejects, and what remains outside its model. Read it together with
[the production readiness report](PRODUCTION_READINESS.md) and
[the adversarial review](ADVERSARIAL_REVIEW.md).

## Measurement environment

Every time, memory and disk figure in this document was measured on an Apple
M5 Max with 18 cores and 128 GiB of memory, writing to an APFS internal
volume. Unless a figure says otherwise it was taken with the machine
otherwise idle and with Towel's CLI defaults, and a figure that names a
commit was taken at it.

Where a figure concerns a project Towel was checking, the checker is that
project's own rather than Towel's: the Sphinx measurements run mypy 1.19.1
and pyright 1.1.407 from Sphinx 9.1.1 at `e44a40e`, and the mypy costs they
describe belong to that version.

## What is verified for every accepted proposal

- **Instantiation.** The helper body, with each call site's actual arguments
  substituted for its parameters (thunks beta-reduced), must reproduce the
  block it replaces up to the renaming of names the block itself binds. A
  proposal whose unification, substitution, or renaming disagree is rejected
  before it is offered. (`src/towel/unification/instantiation.py`)
- **Argument evaluation.** Only names, literals, a unary operator on a
  literal (`-1`, `not True`), and tuples of those are passed eagerly. Every
  other differing expression is passed as a
  zero-argument thunk and evaluated inside the helper at the original
  position, so it runs as often, as late, and as conditionally as before.
  Expressions that read names bound inside the block are lambda-lifted with
  those names as arguments.
  A thunk the helper evaluates first, exactly once, and before any other
  effect is passed eagerly after all, because the call site's evaluation is
  then indistinguishable from the in-place one (`thunk_inlining.py`).
- **Binding discipline.** The block may not rebind, delete, or `except ... as`
  a name bound before it; may not carry a `global`/`nonlocal` declaration the
  caller still uses; may not rebind a name a closure outside the block reads;
  and may not define a closure over a name the caller rebinds after the block.
  Names bound in the block and read afterwards are returned, including targets
  of annotated assignments and assignment expressions, as is a name bound to
  a class instantiation or to a known resource factory (`open`, `connect`,
  `socket`, `mkdtemp`, `Popen`, `urlopen`, ...), whose lifetime a later
  statement could observe; a factory outside that list is not detected. A
  returned name must be definitely bound where the block ends or have
  entered as a parameter.
- **Frame and control flow.** Blocks containing `yield`, `await`, `async`
  loops, context managers or comprehensions, `locals()`, `globals()`,
  `eval`, `exec`, zero-argument `vars()`, `dir()` or `super()`,
  `break`/`continue` targeting an outer loop, comprehension assignment
  expressions, `warnings.warn` in any spelling with or without
  `stacklevel`, any call with a `stacklevel=` keyword, or direct frame or
  stack inspection are rejected; aliases of these bound by import or
  assignment are resolved. A block is also rejected when its enclosing
  function reads its own frame (`locals()`, `dir()`, `eval`,
  `sys._getframe()`, ...) anywhere outside the block.
- **Names the call site may not resolve.** A free variable is passed eagerly
  only when the call site resolves it on every path: a local bound on every
  path before the block, a module name bound on every path before the
  top-level statement holding the function and never deleted, or a binding
  of an enclosing function made before the inner function's definition. Any
  other free variable (a module name bound later, a cell of an enclosing
  function not yet filled) is passed as a thunk so it is read where the block
  read it. A call site whose thunk would read a *local* of its own function
  that may be unbound there is declined instead: the block raised
  `UnboundLocalError` reading it, the thunk raises `NameError` reading an
  unfilled closure cell, and a handler for `UnboundLocalError` stops matching.
  This gives up extractions whose read of such a local can never happen
  unbound, because only a correlation between paths shows it
  (`r85_conditionally_bound_parameter`). Definite assignment is computed conservatively:
  loops, `contextlib.suppress`, and non-exhaustive `match` statements never
  bind definitely.
- **Module names stay module names.** A free name that both sites resolve at
  module scope (or nowhere: a builtin, or a name the module never binds) is
  not passed to a same-module helper at all; the helper reads it bare, where
  the block did, so a helper defined below its callers, a class, an import,
  or module data a callback rebinds between two reads all behave as before.
  A clustered occurrence whose same-spelled name is a local of its function
  or of an enclosing one does not join such a helper. Across files the
  other module's same-named binding may differ, so the name stays a
  parameter there, and module data that a callback may rebind still
  declines the pair (`module_data_lookup`, `rebound_external_binding`).
  The same holds when a same-file pair's helper becomes a method of a
  shared ancestor class defined in another module: the pair is decided
  again with every name a parameter (oauthlib's `BearerToken`, fixture
  `xf15`).
- **Forwarded callees.** A differing expression in call position would be
  passed as `lambda *args, **kwargs: callee(*args, **kwargs)`; such a call
  site reads worse than the duplication it removes, so the pair is declined
  unless the callee can be passed as a value or a plain thunk.
- **Rendering.** Every generated file compiles, and every generated call binds
  to the generated helper's signature after method conversion. A formatter
  may change only layout: each inserted snippet's syntax tree is compared
  before and after formatting, and an import sorter's result is kept only
  when it only reorders or merges consecutive imports within one statement
  list while preserving each bound name's ordered providers. Wildcard imports,
  future imports and non-import statements are barriers. Configured sorting
  of independent imports can still change import-time side-effect order; static
  binding checks do not establish that arbitrary module initializers commute.
- **Annotations.** When the original project passes its type check, every generated
  helper and its call sites are checked together in the prospective project,
  including unchanged consumers. A change that introduces a type error has
  its annotations replaced by `Any`, and then removed. Every variant must
  pass; checker failure or a remaining error declines the change. If the
  original project already has type errors, Towel aborts and asks the user to
  fix them or explicitly rerun with `--no-types`. It never silently disables
  verification. A helper is annotated only in code that
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
  `inspect.stack`, and the like), or `warnings.warn` is never extracted:
  with a `stacklevel`, because the helper's frame shifts the attribution,
  and without one, because the warnings registry deduplicates per call
  site and two sites that warn would become one. This is exact for
  constructs written in the block or its enclosing function, directly or
  through an alias the module binds. A call to a function or method of the
  same module whose own body reads a frame relative to its caller
  (`sys._getframe(n)`, `inspect.stack()`, a `stacklevel=`), directly or
  through other such functions of the module, counts as a frame read too:
  typing_extensions' `_caller`, which finds a `TypeAliasType`'s defining
  module that way, would otherwise see the helper (fixture r146). The
  functions are matched by name, which over-approximates and only declines.
- **Warned before the run.** Directory mode scans every module first and prints
  a stderr warning naming the files that inspect frames or tracebacks,
  attribute warnings by `stacklevel`, or read source through
  `inspect.getsource`. This catches frame sensitivity that a call chain
  through another module hides from the block-level guard, such as pluggy's
  argument validation, where the extracted block calls a function that warns
  with `stacklevel`. The warning
  tells you which diffs to review or `--exclude`; it is a pointer, not a proof
  of breakage.
- **Not detected, and therefore silent.** Four kinds of frame, line, or
  source sensitivity are outside both the guard and the warning, and are
  documented `BROKEN_KNOWN` cases in the ecosystem check rather than things
  Towel can flag: a module that reads a *sibling's* source as text through a
  plain `open` of a `__file__`-relative path and copies regions of it (lark's
  standalone parser generator); a *caller or test* that asserts on the exact
  frames or text of a traceback the refactored code raises normally (glom and
  rich assert on rendered tracebacks, and pyparsing's `ParseException.explain`
  counts the frames of the traceback it was raised through); a test that asserts the exact line
  number a warning is issued from inside its own module, which a helper
  inserted above it shifts (trio's `test_deprecate`); and a *callee* that
  reads its caller's frame, which the block-level guard cannot see through
  a call: `inspect.stack()` or `sys._getframe(1)` inside a function the
  block calls, the shape of a traceback that now includes the helper's
  frame, and logging's `%(funcName)s`, which names the function whose frame
  issued the record and so names the helper (the four differences the
  fifth audit's battery still shows at `5ff2458`, September 19, 2026, are
  all of this kind). None of these
  can be distinguished statically from safe code that does the same thing
  (a linter also opens `.py` files; every library raises exceptions; every
  module has line numbers; every logging call may carry any format), so
  Towel does not warn on them to avoid a flood of false positives. Review
  the diff and run the tests, as with any refactoring.

The remaining entries are other kinds of dynamic behavior that no scan
addresses:

- **Reflection and dynamic rebinding.** Code that rebinds module globals or
  closure cells through `globals()[...]`, `setattr(module, ...)`, `exec`, or
  from another thread between two reads inside a block is outside the model.
  Calls to the reflection builtins, direct or through an alias the module
  binds by import or assignment, are rejected; rebinding through
  `globals()[...]`, `setattr`, another thread, or a callee is not detected.
- **Metaclasses and descriptors.** Method extraction into a class assumes the
  usual descriptor protocol. Methods decorated with anything other than the
  recognized receiver-preserving decorators receive a module-level helper with
  the receiver passed explicitly. A class decorator is trusted to leave a
  helper in place only when it is one of `dataclasses.dataclass`,
  `functools.total_ordering`, `typing.final`, `typing_extensions.final` and
  `enum.unique`, reached through the module's own absolute imports; a class
  carrying any other decorator takes no helper. What a custom metaclass or an
  inherited `__init_subclass__` hook does to the namespace of a class that
  takes a helper is not modeled: one that wraps or drops every function of
  its classes reaches the helper too. `__slots__` interactions with added methods are not modeled beyond
  compilation.
- **Import-time behavior.** Helpers are inserted before the first definition
  in a module, after imports, except that a helper whose annotations name
  classes or functions of the module goes after the last of them, so the
  names can be written bare, when no statement before that point could run
  code at import time. A statement counts as running code when anything it
  evaluates as the module loads is a call: an assignment such as `Y = f()`, a
  decorator, a default, a base or class keyword, or a statement of a class
  body; what a base's `__init_subclass__` runs is not seen. When a name the
  annotations need is defined only after such code, the helper goes before
  it anyway where annotations are postponed (`from __future__ import
  annotations`), and is declined elsewhere. Cross-file helpers add a module import; a helper
  import goes after the module's last leading import (after the docstring
  when there are none), so a script that runs a statement before its
  imports keeps it first. Static local import cycles are rejected
  (including cycles through a package's `__init__`, which `from . import
  name` runs), dynamic ones are not detected.
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
import the sync worker. Towel now refuses a host whose import would run
module-level statements beyond definitions, imports and literal assignments
that the borrower's own imports do not already run (`import_time_effects`),
and one whose import would require a module the borrower does not already
import: an unconditional import, including one inside a module-level `if`,
of anything outside the project, the standard library and the project's
declared `[project].dependencies`. An import inside `try` is taken as an
optional dependency and requires nothing. A dependency declared under a
distribution name that differs from its import name (`PyYAML` for `yaml`) is
not recognized, which refuses a host rather than accepting one; an import
made by `importlib` or `__import__` is not seen at all.

## Method insertion

A helper shared by methods that never read an attribute of their receiver, or
by static methods, is a module-level function. Such a method works when it is
called through its class with anything in the receiver's place --
`Formatter.as_dollars(None, 1.5)` -- and a helper reached through `self` would
end that. A static method in the class would have to be reached through
something, and nothing a method can spell is sure to be its class: its name
may be a parameter of the method, deleted, rebound through `global`, mangled
(`class __C`), bound to whatever a class decorator returned, or not bound yet
while the class body calls the method, and a metaclass sees the lookup. The
`__class__` cell is always the class, but mypy does not know the name. A
method that ignores its receiver has no dispatch to preserve, so the only cost
is that the helper sits before the class rather than inside it; a block that
does use the receiver takes it as an ordinary argument.

A generated helper's name is one no Python source under the project root
spells yet, not only none of the files under refactoring: a subclass in
another package that already defines `_extracted_func_0` would override a
helper of that name placed in its base. The root is the nearest directory
with packaging metadata above the input (or the directory above its
packages), read with the consumer scan's exclusions; a subclass defined
outside that root, or reached only through `exec`, is not seen.

A base-class name is resolved as the binding in effect where the class
statement runs, never by name across the project. It must be bound there by an
unconditional class statement of that module or by one of its unconditional
module-level imports. A base bound conditionally, declared `global` by some
function, reachable through a star import, or bound in the same top-level
statement as the class that uses it contributes no ancestor, and the helper is
placed at module level instead. Rebinding the name between two subclasses --
`Base = object` on a line of its own -- is therefore respected rather than
overlooked. A *decorated* base contributes no ancestor either, unless each of
its decorators is one of those known to keep the class and its namespace (see
above): `@register class Base:` binds `Base` to whatever `register` returns,
and Towel does not evaluate the decorator to find out. `exec`,
`globals()[name] = ...` and other reflection remain outside what any static
rule here can see.

A helper becomes a method only when both blocks belong to functions defined
directly in one unique module-level class, or in classes with a unique
module-level common ancestor, every
decorator on the source methods is known
to preserve the receiver, the methods have a first parameter named
`self` (or the method is a `classmethod`), and both read an attribute of it.
The class that takes the helper, whether the methods' own or their common
ancestor, must also be able to hold it as an ordinary member: not a
`Protocol` (a method there is one more member every structural implementer
lacks, so a runtime-checkable `isinstance` turns false), not written with its
body on the header's line (`class Base: pass` takes no further statement), and
not decorated beyond the known namespace-preserving decorators. A base that
could be `Protocol` on any path through its module, or is spelled
`Protocol`, counts as one. When the nearest common ancestor is refused, a
farther one that qualifies is used. Local classes, duplicated class names, unknown
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
- A precise ordinary signature is preferred to a generic one. Towel tries the
  signature copied and inferred from the call sites first, and reaches for
  anti-unification only when that signature contains `Any` or when the whole
  project rejects it. So where sites disagree on a column and the resulting
  union happens to type-check everywhere, the union is what ships, even though
  a type parameter would have carried the correlation between that column and
  the result. A generic signature is an alternative to losing the annotation,
  not a systematic replacement for an imprecise one.
- Fresh module-level helpers and helper methods can use generic signatures
  obtained by anti-unifying complete argument/result rows, including nested constructors.
  Type-variable identity includes its original binding scope. Existing free
  variables are rebound with compatible bounds or constraints; dependent bounds,
  conflicting free-variable domains, variadic type parameters, unresolved names,
  and `Any`/`Unknown` decline generic inference. At most two generic contracts
  are tried: unrestricted concrete disagreements, then constraints with two to
  four concrete alternatives. Every fresh helper type parameter must occur in an
  input. Instance and class helpers retain type parameters bound by their host
  class, which can also appear only in the result. A module helper taken from
  static methods freshens source class parameters and must infer them from
  explicit arguments. Generic method
  inference currently requires implicit `self`/`cls` typing; explicit receiver
  contracts are not generalized.
  Inherited helpers must type-check in the chosen ancestor, without assuming
  subclass-only attributes. Function-hosted generic helpers remain unsupported. See
  the [type-parameter design](proposals/type-parameters.md).
- Mypy can report several different specializations for one expression inside
  a constrained generic function. Towel treats that reveal as ambiguous instead
  of keeping whichever note appeared last. A constrained local whose generic
  relationship is absent from the source signature can therefore still decline
  extraction with mypy, even when Pyright supplies its scoped type variable.
  Recovering that relationship needs correlated specialization evidence; a
  union of the notes does not identify which source binder they came from.
- Pyright's strict `reportPrivateUsage` rule rejects the generated private
  helper name when another module imports it, even when its generic signature
  is valid. Such cross-file proposals remain refused under that configuration;
  type inference does not suppress project rules.
- Placement for bare names in ordinary inferred signatures holds only within one module. For a helper
  whose sites are in other modules, an annotation may name only builtins
  and the `typing` names Towel imports itself (`Any`, `Callable`), since a
  site's imports are not the host's. A class the module cannot reach is
  imported under `TYPE_CHECKING` and named directly; only where no module of
  the project owns it, or its short name is already taken, is the type left
  unwritten and the parameter completed with `Any`. Any subscripted
  annotation that would not evaluate at definition time (`memoryview[int]`
  on an interpreter where `memoryview` is not generic) is written as a
  string. Generic inference can also retain a foreign site's imported type when
  the helper's host binds the same canonical import; matching spellings alone
  are insufficient. Its annotations and TypeVar domains are quoted.
- The typing guarantee is exactly as strong as the checker the project
  configures. Towel verifies every prospective change with the configured
  checker or checkers and declines whatever they reject; it makes no claim
  about a checker the project does not configure, even one that happens to be
  installed. A project that configures mypy alone can therefore accept output
  that Pyright would reject, and the reverse. Configure both to be checked by
  both.

  A type guard shows how this bites. Moving `if not isinstance(x, list): raise
  ...` into a helper leaves the caller's `x` at its declared type, so a
  following `for item in x` no longer sees a `list`. An extraction that
  separates a narrowing test from an expression it leaves at the call site is
  now declined where the proposal is built, before any checker is asked, and
  so identically with `--no-types`; what follows is the case that survives,
  where the test and its use stay together and the loss is in the helper's
  revealed type. But the evidence the checker
  itself supplies can hide it: mypy narrows `Mapping[str, object]` through
  `isinstance(_, dict)` to `dict[Any, Any]`, and once a helper's return carries
  that `Any`, the caller's loop is unremarkable to mypy while Pyright still
  objects. Towel wrote the type the checker revealed and accepted the answer
  the checker gave; the limit is the checker's, not the transformation's.
- The degradation on a type error is per proposal, not per parameter: one
  annotation the checker rejects costs the helper all of them.
- Verification first requires a clean original project, then checks complete
  prospective project graphs, overlaying all changed files together. A newly
  imported helper therefore exists in its host while its consumers are checked.
  Safe project checking rules are honored; project
  plugins, configured executables and report destinations are not executed.
- Pyright verification uses a private copy of Python sources, stubs, typing
  markers and checker configuration, made once per run, kept in step with the
  project as it is refactored, and watched by one long-lived language server.
  A file is recopied when its content differs, not merely when its size or
  timestamp does, so an edit by something other than Towel cannot leave a
  verdict standing against a project the copy no longer matches. A checker
  configuration whose bytes are not UTF-8 is refused rather than copied
  without rewriting the absolute paths in it. Cyclic or external source symlinks and
  configured source or stub search roots outside the project cannot be
  represented safely and cause verification to decline the proposal.
  Project include/exclude settings still determine the checker's coverage.
- Mypy runs in an owned worker process, each build in a forked child of it that exits once it has answered, and never freezes the caller's garbage
  collector. Library users should call the oracle's `close()` when finished;
  `CombinedOracle.close()` closes both checkers. The CLI closes its oracle on
  both success and failure.
- Pyright reads files, so while it is consulted a probe copy of the module
  exists beside it in the package, created exclusively with owner-only
  permissions under a unique `_towel_probe_` name and removed afterwards,
  or at interpreter exit if a crash skipped the cleanup. A kill signal can
  leave it behind; it imports nothing the module does not.

## Conservative rejections

Towel prefers to leave code unchanged rather than transform it under
uncertainty. Every declined pair is traced under one of the reasons of
`RejectReason` (`src/towel/unification/models.py`), listed here in the order
the pair decision raises them, grouped by stage:

- Frame use. `frame_sensitive_block`: the block contains a suspension,
  a namespace read, a frame or stack read, a warning, a loop transfer
  out of the block, or a comprehension assignment expression (the list
  under *Frame and control flow* above). `frame_read_in_function`: the
  enclosing function reads its own frame (`locals()`, `dir()`, `eval`,
  `sys._getframe()`, ...) somewhere outside the block.
- Bindings crossing the block boundary. `nested_binding_escapes`: a block
  nested inside a loop or branch binds a name the rest of the function
  reads. `closure_crosses_block_boundary`: a nested function or lambda
  outside the block reads a name the block rebinds, or one inside the
  block reads a name the caller rebinds after it. `moves_scope_declaration`:
  a `global`/`nonlocal` declaration in the block names something the
  caller still uses.
- Type information the move would destroy. `narrowing_lost_at_call_site`: a
  test in the block narrows a name, and an expression the two sites differ in
  reads that name, so extraction would leave the reading outside the region
  the test governs. Decided from the proposal alone, so it applies whether or
  not type checking is on. The tests read as narrowing are `isinstance`,
  `issubclass`, `hasattr` and `callable`; a comparison of a name with `None`;
  `type(x) is C`; and a `match` whose patterns are not all bare captures. A
  test inside a nested function speaks about that scope's own names and is not
  counted. Truthiness, a `TypeGuard` function and equality with a literal do
  narrow and are not read here: a rule over bare names in a test refuses
  several sound extractions for each unsound one it catches, and what is not
  declined here is declined by the checker.
- Reassignment and deletion. `unsafe_reassignment_block1`/`_block2`: the
  block reassigns a name it did not bind (`result = result + 10` with
  `result` bound before it). `unbinds_external_name`: the block deletes,
  explicitly or through `except ... as`, a name bound before it or
  declared `global`/`nonlocal`.
- Shape. `value_producing_mismatch`: one block returns a value and the
  other does not. `incomplete_return_coverage_block1`/`_block2`: a
  value-producing block does not leave by `return`, `raise`, `break` or
  `continue` on every path. `trivial_return_blocks`: both blocks are a
  one-line `return name` of a name bound before them.
  `not_structurally_similar`: the blocks' per-statement node counts or
  type histograms differ by more than the similarity threshold.
- Unification. `unification_failed`: the blocks do not anti-unify, which
  includes a differing sub-expression that is a slice, a starred item, or
  a whole f-string (container syntax rather than values), a lambda with
  positional-only, keyword-only, or variadic parameters, an expression
  containing an assignment expression, and a substitution that would need
  more than the configured maximum parameters (`--max-parameters`).
  `return_variables_not_aligned`: a variable one block must return has no
  binding in the other. `mixed_return_and_variables`: a block both returns
  early and binds variables read afterwards, which one call statement
  cannot render.
- Free variables and lifetimes. `conditionally_bound_return`: a returned
  variable is not definitely bound at the block's exit and did not enter
  as a parameter. `incomplete_lifetime_block1`/`_block2`: the block reads a
  name that is bound only after it. `module_data_lookup`: the helper would
  receive module data (a module-level assignment) as an argument,
  snapshotting it. `rebound_external_binding`: the helper would receive a
  name another function rebinds through `global` or `nonlocal`, or a name
  the module's reflection makes unreliable. The names both sites resolve
  at module scope are read bare by a same-module helper and are exempt
  from both, so these two decline cross-file pairs and pairs where only
  one site resolves the name at module scope.
- Rendering. `impure_eager_parameter`: an argument that would be passed
  eagerly is not a literal, a resolvable name, or a tuple of those.
  `trivial_forwarding_helper`: the helper body would be a single
  forwarding statement (a lone `raise`, a `return` of one call, or a bare
  call), a forwarding statement whose result is bound and returned
  (`x = f(...)` then `return x`, or the tuple form), or a body that only
  binds parameters and literals to names and returns them; such a helper
  shares no logic, only a name, and is skipped by default
  (`skip_trivial_helpers=False` keeps it).
- Orphans. `orphaned_variables`: a name the block binds is read afterwards
  on a path that does not rebind it first, and the helper does not return
  it. A read after only a *conditional* rebinding is treated as orphaned
  even where the helper would return it (the annotated-assignment fixture
  r86 is rejected for this reason); returning such names was found unsafe
  in three fixtures, and a path-aware return analysis would recover the
  case. A match capture, `with` target, or exception name that would have
  to cross the block boundary in a way the return analysis does not
  represent is declined here or under the alignment reason above.
- Call sites. `forwarded_callee`: a differing expression in call position
  would be passed as `lambda *args, **kwargs: callee(*args, **kwargs)`,
  which reads worse than the duplication it removes. `undefined_names_in_call`:
  the generated call names something the site cannot resolve (a leaked
  placeholder, a name bound only inside the block).
  `instantiation_mismatch`: the helper applied to the call's arguments does
  not reproduce the block up to renamed binders.
- Placement. `nonlocal_safety_skip`: either block's function declares
  `nonlocal`. `private_name_lexical_class`: a site's method uses a
  `__private` name and the helper would live in another class, which
  changes name mangling. `cross_module_global_declaration`: a cross-file
  helper's participating modules include one whose functions declare
  `global`. `unknown_layout`: the project's packaging layout cannot be
  modeled, so no import can be written. `import_cycle`: every candidate
  host closes a static import cycle. `import_time_effects`: a cross-file
  helper's host module, which the borrower does not already import, would
  run module code beyond definitions, imports and literal assignments at
  import (a module that prints, registers or connects at import time); a
  module-level helper may move to a participating module that hosts it
  without that, and the pair is declined only when none does.
- The proposal. `duplicate_proposal`: the helper, home and sites repeat an
  earlier pair's, found through another pair of the same family.
  `existing_helper_becomes_forwarder`: a site is the whole body of a helper
  an earlier pass inserted, which would keep only the new call.

Other behaviors that leave a duplicate in place are not rejections of a
formed pair:

- A duplicate that is the whole body of an existing function is redirected
  to that function rather than extracted, but only when the function is a
  plain module-level `def`: a decorated, async, variadic, shadowed, or
  rebound function, or one whose call across files would close an import
  cycle, falls back to ordinary extraction (which the trivial-helper filter
  then usually declines, since the helper would restate the function).
- A block that begins at an `elif` is never extracted, because its call
  would have to be rendered inside the preceding branch's `else`; the
  `elif`'s own body and further branches remain candidates. This gives up a
  valid extraction when the preceding branch always exits (tabulate).
- Past the candidate-pair budget (`--max-pairs`, 20,000,000 by default) the
  largest groups of similar blocks are left out of pairing, with a warning
  naming them. The default is above every project in the ecosystem corpus
  (networkx needs 9.25 million pairs, sphinx 8.45 million); the 2,000,000
  the release candidate first shipped cut both, and sphinx made 346
  refactorings instead of 413.
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
  took 17 s and 100 took 134 s under 1.618, before this pass and the reuse
  index below; at `5ff2458` on September 19, 2026,
  `scripts/bench_similar_blocks.py` on one core of an Apple M5 Max with one
  other single-core job running takes 4.0 s for 50 and 15.9 s for 100, a
  factor of 4.0 for twice the functions), memoizes its per-candidate
  pipeline on the template, the candidate, and the pair's helper, and
  applies its constant-time filters before the semantic guards. The
  remaining growth is cubic: every one of the N²/2 pairs legitimately
  proposes the same N-site extraction until the first application collapses
  them. Pair evaluation therefore keeps the first proposal of each identity
  (helper body, home and sites) and declines the rest, so the pairs are
  still evaluated but their copies are not held: sixty near-identical
  38-line functions under `--max-pairs 200000` peaked at 33.6 GB before and
  0.74 GB after (`1db56a1`, September 18, 2026, before the module-name
  rule). The clustering scan cache is likewise bounded by the sites it
  holds, not only by its entry count. `--max-pairs` (20,000,000 by default)
  bounds the candidate pairs one analysis evaluates by leaving out the
  largest groups of similar blocks with a warning naming them. What it does
  not bound is a file below the budget whose every pair is admissible: a
  thousand near-identical five-line functions that all read one module
  global, which the module-name rule admits where the module-data rule used
  to decline every pair in seconds, take 33 minutes on one core (commit
  `5ff2458`, September 19, 2026) and yield one helper with 669 call sites.
  Its peak memory was 8.8 GB; bounding the scan cache by sites brought it
  to 6.6 GB, and what holds the rest has not been established. Lower
  `--max-pairs` or raise `--min-lines` to trade that result for time and
  memory.
- Verification, not analysis, dominates an annotated project. Every candidate
  signature is checked against the complete prospective project, and a proposal
  can try several. Both checkers are kept warm for the run. Mypy builds in a
  forked child of an owned worker against a cache that lives as long as the
  run, so a check re-checks the changed modules and their import cycle rather
  than the project, and its cost does not grow as the run goes on: on Sphinx's
  432 files a check of all 243 analyzed modules is about 1.1 s and an inference
  probe about 0.5 s, the same at the two-thousandth request as at the first.
  Pyright is one language server over a private copy that follows the project:
  about 0.5 s per check once warm, against 5 s for a fresh `pyright
  --outputjson`, which remains the fallback. The first rejection settles a
  candidate, so a project that configures both pays for both only when the
  first accepts.
  These figures were taken on macOS 26.5.1 with Python 3.12.13, against
  Sphinx 9.1.1 at `e44a40e`: 243 modules with mypy and Pyright both strict,
  checked through that project's own mypy 1.19.1 and pyright 1.1.407.
 A capped run on Sphinx (`--max-refactorings 45`, 52
  refactorings across 8 files) takes about 7 minutes, of which mypy is about
  3, over 52 whole-project checks for 43 extractions: 30 verified on the first
  candidate signature, 13 on a second, and none dropped.
- A full typed fixed point over a large project is still long. Sphinx had
  applied 276 refactorings across 101 files after 57 minutes and had not
  finished. That was measured before the verification work of 1.772: the same
  run now reaches a fixed point in 46 minutes, applying 380 refactorings
  across 105 files, where the same project without types changes 108 files in
  11 minutes. Sphinx's own test suite, run serially, reports the same 2385
  passed, 34 skipped and six pre-existing failures before and after.
  The tail was the cost: a proposal the project rejected used to be heard
  again at every whole-project analysis, and families of near-identical
  methods pair many ways. A declined proposal is now remembered for the whole
  run and heard once more only at the rehearing that ends it. A bounded `--max-refactorings` run is the practical form there.
  Nothing about the result depends on any of this: the checkers are consulted
  identically.
- What a checker still rejects is, on Sphinx, one thing and one family. The
  thing is a helper lifted into a base class whose body reads a member only
  its subclasses have, so the precise signature is refused and the helper
  keeps `Any`; the family is a generic helper whose inferred type parameter
  wants a bound. Neither loses the refactoring.
- Because the variants are generated lazily, a signature that verifies costs
  nothing further. A precise ordinary signature that passes means no generic
  candidate is ever built or checked, which is the cheapest order as well as
  the documented one.
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

Measured in September 2026 with the CLI defaults on the hardware described
above (macOS, Python 3.13,
single core unless stated): the wall time of a whole `towel dry` run on the
ecosystem check's clone of each project, before and after the measures
above, with identical output in every case. These are the measured tables
of package timings; the README refers here rather than repeating figures.

| Target | Before | After, one core | After, forking |
|---|---|---|---|
| pyflakes `test_other.py`, one analysis | more than 540 s, capped | 30.6 s | 7.4 s |
| boltons, fixed point | 32 s | 17 s | 15 s |
| pygments, fixed point | 170 s | 107 s | 60 s |
| pyflakes, fixed point | 300 s | 224 s | 54 s |

A later pass (the per-function-facts commit `177691d`, measured September
18, 2026) added the per-function facts, the statement-sequence buckets, and
the exact incremental global passes, and turned on formatting and typing by
default. Measured the same way, one core, identical output between the on
and off settings of each:

| Target | 1.618 | `177691d`, `--no-types --no-format` | `177691d`, defaults |
|---|---|---|---|
| Towel's own source, the 1.618 snapshot the exactness baselines use (16,000 lines, 45 applied), fixed point | 47.8 s | 33.9 s | 41.8 s |
| h2 (hyper-h2), fixed point | 5.2 s | 7.0 s | 11.9 s |
| Sphinx, fixed point, in the ecosystem check | 2513 s | not measured | 2058 s |

The bare-engine speedup is what the caches and buckets buy; the defaults
then spend part of it type-checking each applied refactoring, a cost
proportional to the number of applied changes rather than to project size.
The engine at the per-function-facts commit (`177691d`) also applied more
refactorings than 1.618 did on the same input (h2: 20 against 14; Towel's
source: 45 against 41), so the times compare a larger amount of work. The
times depend on the input as much as on the engine: at `5ff2458`
(September 19, 2026, Apple M5 Max, `TOWEL_WORKERS=1`, Python 3.12, one
other single-core job running), that day's Towel source, 22,690 lines
after the later audits' removals, has 15 duplicates to apply and `towel
dry src/towel` runs in 8.4 s without the type checker and formatter and
11.9 s with them.

The remaining cost is the pairwise evaluation of structurally distinct
candidates, which no cache can share; large test modules with hundreds of
similar methods remain the worst case. Progress is reported per phase.
There is no time budget, but `--max-pairs` (20,000,000 by default) bounds
the candidate pairs one analysis evaluates by leaving out the largest
groups of similar blocks with a warning; interrupt with Ctrl-C, which
leaves files unchanged. The ecosystem check applies a 30-minute limit per
phase by
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
when mypy is installed), the current implementation adds an owned checker
process. The following figures measure the earlier in-process implementation,
not current whole-project verification: Towel's source (22,690 lines, 15 applied) peaks at 174 MB without it and
894 MB with it (`5ff2458`, September 19, 2026, Apple M5 Max,
`TOWEL_WORKERS=1`, Python 3.12). Forking multiplies that: each worker is a copy-on-write fork
whose caches then diverge, so peak memory scales with the worker count.
networkx (200,000 lines, tests excluded) peaked at about 1 GB in one process
with `TOWEL_WORKERS=1`, and near 7.4 GB across twenty processes when forking
on an 18-core machine. The engine estimates the parent's resident
size against physical memory at fork time and caps the workers at about a third
of RAM, but that is an estimate, not a guarantee; on a memory-constrained
machine, or when running several large refactorings at once, set
`TOWEL_WORKERS` low. At `TOWEL_WORKERS=1` the footprint stays at the
single-process figure above.
