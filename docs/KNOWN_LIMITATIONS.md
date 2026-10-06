# Known limitations

[Documentation index](README.md)

This document states what Towel verifies about a transformation, what it
rejects, and where its implementation has limits. Towel must preserve the
behavior of programs that do not use reflection or self-instrumentation,
including through code they call. A behavior change in such a program is a
soundness defect: fix the transformation or refuse it. Documenting an
implementation gap does not waive this requirement.

Reflection and self-instrumentation
are excluded under the [October 2 decision](DECISIONS.md#2026-10-02-reflection-and-self-instrumentation-are-outside-the-preservation-contract);
they are not detected to refuse or warn on a program. Read this together with
[the production readiness report](PRODUCTION_READINESS.md) and
[the adversarial review](ADVERSARIAL_REVIEW.md).

**Contents**

- [What every accepted proposal verifies](#what-is-verified-for-every-accepted-proposal)
- [Observable differences](#observable-differences-that-remain) · [Reflection and stack depth](#reflection-over-a-namespace-and-stack-depth)
- [Cross-file imports](#a-cross-file-helper-adds-an-import-of-its-host-module)
- [Method insertion](#method-insertion) · [Helper types](#type-annotations-on-helpers)
- [Body instrumentation](#decorators-that-compile-or-instrument-a-body)
- [Conservative rejections](#conservative-rejections)
- [Unreadable programs](#a-program-towel-cannot-read-whole)
- [Performance](#performance) · [Resources and platform](#resources-and-platform) · [Measurement environment](#measurement-environment)

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
  block it replaces up to the renaming of names the block itself binds, and
  a name may be renamed only where the running program cannot see its
  spelling. The comparison is by binding: each binder, in whatever scope of
  the block (a lambda's, a comprehension's, a nested function's or class
  body's), matches only its own occurrences, and a name free in the helper
  matches the block's only where the site reads the same thing, the module's
  name or builtin, never a local or closure variable of the site's function.
  An argument substituted where a scope of the helper binds a name it reads,
  or a thunk's argument under a scope of the thunk's that does, is a capture
  and declines the call site. On the spelling: `UnboundLocalError` and
  `NameError` name a variable read, augmented or deleted while unbound, so a
  renamed binder that some read may find unbound (after a `del`, an empty
  loop, an unmatched case, the end of its `except ... as` clause, or in a
  closure), or that a `global` or `nonlocal` declaration names, declines the
  call site. The comparison is not flow-sensitive: a name the block both
  reads before binding it and binds is one binder in both fragments, and
  the incomplete-lifetime and reassignment guards, not this check, decline
  such a block. A
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
  then indistinguishable from the in-place one (`thunk_inlining.py`). What
  the helper evaluates before it must neither run code nor raise: reading a
  parameter or a name the helper has already bound, building a tuple, list,
  set or dict of constants and such names, creating a lambda whose defaults
  are such, a constant, a negative number. A global or builtin read, a set or
  dict inserting anything but a constant, `*` and `**`, and unpacking a
  target list are effects, so a thunk after them stays a thunk.
- **Binding discipline.** The block may not rebind, delete, or `except ... as`
  a name bound before it, by whatever construct binds it (an assignment, a
  `for` or `with` target, a `match` capture, a nested `def` or `class`, an
  import, a walrus); may not read a local before it binds it (`scale =
  scale(n)`) unless the call site has the name bound on every path; may not
  carry a `global`/`nonlocal` declaration the caller still uses; may not
  rebind a name a closure outside the block reads; may not define a
  closure over a name the caller rebinds after the block; and may not hold
  its function's only binding of a name the function's other code reads,
  unless the call assigns the name back. Python makes a name local to a
  function if any of its code binds it, a bare annotation or a `del`
  included, so without that binding the function's other reads of the name
  (before the block, in a nested function, lambda, comprehension or class
  body, a nested `nonlocal`) would find a module name, a builtin or an
  enclosing function's variable where they raised `UnboundLocalError` or
  saw the block's value. A name the block binds that its function declares
  `global` is declared `global` in the helper too, whatever construct binds
  it, and two sites whose functions declare a name the blocks spell
  differently share no helper, nor does a clustered occurrence whose
  function declares differently from the pair's. Names bound in the
  block and read afterwards are returned, where `count += 1` and `del count`
  read `count` as a load does, including targets of annotated assignments
  and assignment expressions. Every nonconstant expression may produce a
  resource or finalizable value, including properties, subscriptions,
  operators and aliases; iterator and context-manager targets count too.
  Such values are retained across an ordinary block's helper return. A
  literal constant assignment is the narrow harmless case. When several
  such values are returned, their caller bindings preserve the original
  compiler local-slot order, so finalizers run in the same relative order.
  An occurrence with an incompatible order, or without compiler evidence
  sufficient to establish it, is declined. A
  returned name must be definitely bound where the block ends or have
  entered as a parameter. A partial extraction that splits potentially
  owned values between caller and helper is refused, including values that
  would bypass an ordinary returned tuple on exceptions. Whole-body
  extraction can transfer original argument ownership on CPython when the
  compiled cleanup order and immutable bindings are certified. It adds a
  final holder parameter and a caller-side box/delete/pop sequence. Parameter
  cells, rebinding, escaping thunks and unsupported runtimes do not receive
  this exemption. A custom formatter must preserve the certified AST. Runtime
  reads of native type parameters are refused when they require cells;
  annotation-only native type parameters can remain on the original function. These conservative
  checks can decline ordinary numeric-looking code: its runtime values are
  not proved to lack finalizers merely by their spelling or annotations.
- **Control flow and class context.** Blocks containing `yield`, `await`,
  async loops, context managers or comprehensions, `break`/`continue` targeting
  an outer loop, or comprehension assignment expressions are rejected where
  the helper cannot preserve their control flow or scope. Zero-argument
  `super()`, including aliases, needs the original class cell and receiver;
  it moves only when the method-helper checks establish that context (see
  *Method insertion*). These checks do not protect frame or source inspection.
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
  loops, context managers, and non-exhaustive `match` statements never
  establish new definite bindings after the statement. All context managers
  may suppress an exception before a body assignment; an earlier manager may
  also suppress a later manager's entry failure. A name a statement may delete
  (`del`, or the end of
  an `except ... as` clause) is unbound for whatever may follow it: the rest
  of its list, the next iteration of a loop that holds it, and the handlers,
  `else` and `finally` of a `try` that holds it.
- **Module names stay module names.** A same-module helper reads module
  names where the original block did, so ordinary reassignment by a callback
  or another thread remains visible at each read. A same-file helper stays
  in that module, even when its callers inherit from a class elsewhere.
  Across modules, callable module bindings are passed as lookup thunks, such
  as `lambda: callback`, and the helper performs each original lookup at its
  use. The existing proof may inline a thunk only for one use at the first
  effect. Module-data cases that cannot preserve lookup timing still decline
  (`module_data_lookup`, `rebound_external_binding`). A clustered occurrence
  does not join an existing eager parameter when its own binding requires a
  module lookup.

  By default a cross-module helper may not read a bare builtin from its
  host on another module's behalf (`builtin_may_differ_by_module`). The
  exception is `__debug__`, which Python fixes for the interpreter. An
  absence of assignments in the project does not establish that different
  modules keep equal bindings: ordinary code outside the scanned project
  can assign `mod.len`, and a partially initialized star import can expose a
  different binding. The September audit's builtin-cycle counterexample
  therefore receives no cross-module extraction under the default policy.
  Same-module builtin reads remain bare and preserve their original lookup.

  `--parameterize-builtins` (`parameterize_builtins=True`) explicitly permits
  each caller's binding to reach a shared helper. Module-resolved names use
  lookup thunks such as `lambda: len` and `lambda: print`, preserving repeated
  reads and intervening ordinary assignments. Only the existing first-effect,
  single-use proof permits eager evaluation. Local bindings retain their
  ordinary parameter behavior. The option does not permit blocks that differ
  in which builtin they use; the builtin-argument and other binding checks
  still apply (`tests/test_no_builtin_arguments.py`).

  Typed lookup thunks retain their callable result type. For example,
  `lambda: len` is `Callable[[], Callable[..., int]]`, `lambda: print` is
  `Callable[[], Callable[..., None]]`, and `lambda: str` is
  `Callable[[], type[str]]`. The checker determines these types; the fallback
  only loosens an otherwise unspellable builtin signature to what its body
  needs. Signatures with incompatible overload results or unsupported generic
  parameters can still require `Any`, subject to the normal project check
  (`tests/test_parameterize_builtins.py`). Reflective namespace manipulation
  remains outside the preservation contract and is not detected to refuse
  extraction.
- **Relative imports stay in their package.** A relative import in a block
  resolves in the package of the module that runs it, so a helper holding
  `from .sub import VAL` imports its host's `sub` for every caller. A block
  with a relative import is shared across modules only when every
  participating module resolves each of its relative imports in the same
  package (fixtures `xf23`-`xf25`); the part of the block after the import
  may still be shared, taking the imported name as a parameter. A same-file
  pair's helper lives beside its sites, never in a base class elsewhere
  (`xf26`).
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
  future imports and non-import statements are barriers. An import runs its
  module where it stands, so the order of a file's own imports is the order
  of their import-time effects, and the sorter never changes it: it runs only
  on a file its own configuration selects (ruff's `exclude`,
  `extend-exclude`, `lint.exclude` and `per-file-ignores`; isort's `skip`,
  `extend_skip`, `skip_glob`, `extend_skip_glob` and `skip_gitignore`,
  judged for the project's file rather than the run's staged copy) and only
  when it already leaves that file's text before the change as it is, so
  that sorting can move only the imports Towel added; and its result is
  used only when the file's own imports still bind their names in the order
  they did. A file that fails either test keeps Towel's imports where it put
  them, and the run says so once for the file. The formatters leave alone
  the code Towel writes where their own configuration excludes the path
  Towel was given: `ruff format` by `--force-exclude`, Black by `exclude`,
  `extend-exclude` or `force-exclude`. That choice is made for the whole
  run, since a snippet is formatted before the file it goes into is known;
  it changes only layout.
- **Configured Ruff lint.** When Ruff is installed, each modified file with
  a project Ruff configuration is checked against its preceding source,
  including nested configurations, file exclusions and per-file ignores.
  Towel declines a proposal that introduces a diagnostic, or whose check
  fails. An existing diagnostic must match an unchanged line in the same
  named lexical scope, with the same code, message and column. A warning
  moved into a helper is conservatively new and can cost an extraction.
  The check does not apply fixes or add suppressions, and remains active
  with `--no-format`. It uses configured rules, rather than interpreting
  additional lint options in a project's CI commands.
- **Annotations.** Every generated helper and its call sites are checked
  together in the prospective project, including unchanged consumers, and the
  check is compared with the project's own as it stood: an error the project
  already had is left as it is, and a change passes only when its check
  reports nothing the project's did not (see *Type annotations on helpers*
  below). A change that introduces a type error has its annotations replaced
  by `Any`, and then removed. Every variant must pass; checker failure or a
  new error declines the change. A checker that cannot run at all refuses the
  typed run, and `--no-types` is the explicit way on. So does a candidate's
  check that fails where the same check of the project as it stands, with no
  change applied, fails too: no candidate could be judged, and the run stops
  at the first rather than declining each as not judged. It never silently
  disables verification. A helper is annotated only in code that
  already uses annotations, from what the sites declare and what the checker
  reveals; see *Type annotations on helpers* below.

These checks are syntactic and local. They establish that the helper is a
faithful generalization of each block under Python's lexical scoping. They do
not establish behavioral equivalence for programs that observe their own
frames, names, or source.

## Observable differences that remain

Reflection can observe that extraction changes a program's source, AST,
bytecode, frames or namespace. Towel does not preserve those observations and
does not scan for them, issue reflection warnings or decline code to protect
them. This exclusion applies to direct operations and callees, whether reached
by their original names, aliases, decorators, ordinary calls or hooks.

Users can explicitly protect a function with `# towel: no-extract` immediately
after its signature's final colon. Its definition and nested bodies stay
intact; unmarked code remains eligible. This does not freeze its absolute line
position, its module or class namespace, or unmarked callers and callees.
Protect the frames that matter: a callee reading its caller's frame may require
marking that caller. When refactoring a directory, use `--exclude filename.py`
when that file’s entire source must remain unchanged. See
[Protect a function](CLI_GUIDE.md#protect-a-function).

Examples include:

- `locals()`, `globals()`, `vars()`, `dir()`, `eval` and `exec` whose behavior
  depends on the original frame or namespace;
- frame and traceback inspection, logging of function names, and warning
  attribution or deduplication tied to source positions;
- reading a function's source, AST or bytecode, reading a sibling's `.py` file,
  or testing exact line numbers and traceback text;
- inline-snapshot's call-site source reading and other source-sensitive
  libraries, such as icecream, devtools, varname and sorcery;
- body compilation, AST rewriting and reflective class transformations,
  including typeguard, numba and pytest assertion rewriting.

The old inline-snapshot exception was motivated by a measured rich-click
failure: 68 of 151 tests failed when calls with different source literals were
merged into a helper. That historical result remains evidence of source
sensitivity; it no longer implies a dedicated refusal. Earlier ecosystem
checks recorded source/frame-sensitive cases involving lark, glom, rich,
pyparsing and trio, and the September 19 fifth-audit battery at `5ff2458`
retained four such differences. These are observations of those tested source
states, not current detection guarantees.

Boltons' `namedtuple` and `namedlist` factories use `sys._getframe(1)` to
derive the generated class's `__module__` from their caller. Moving that read
into a helper changes the observed frame and therefore the module name;
pickling the generated class can then fail to find it. This is an excluded
frame-inspection dependency, not a general exclusion of pickling. The corpus
manifest pins the two observed pickle-test failures at the recorded Boltons
revision.

The following cases distinguish excluded reflective behavior from ordinary
semantics that transformations must preserve:

- **Reflection and dynamic rebinding.** Rebinding module globals or closure
  cells through `globals()[...]`, namespace dictionaries, `setattr`, `exec`
  or reflective patch APIs is outside the guarantee. Ordinary assignments,
  including those made by callbacks or another thread, do not become reflection
  merely because they execute dynamically. Their bindings and effects must be
  preserved. Under
  `--cross-module`, loading a helper's host earlier can also change what that
  host imports from a module rebound elsewhere. The borrower's own earlier
  statements still precede its inserted import (*Import-time behavior*).
- **Metaclasses and descriptors.** Method extraction assumes the usual
  descriptor protocol. Receiver compatibility remains a placement constraint.
  A metaclass, class decorator, `__init_subclass__` or lookup hook can inspect,
  register, wrap or redirect the new private helper; those reflective changes
  are outside the guarantee. Body instrumentation receives no special
  protection. `__slots__` interactions with added methods are not modeled
  beyond compilation.
- **Import-time behavior.** Helpers are inserted before the first definition
  in a module, after imports, except that a helper whose annotations name
  classes or functions of the module goes after the last of them, so the
  names can be written bare, when no statement before that point could run
  code at import time. One judgment decides that here and for a cross-file
  host below (`ImportTimeCode`): a statement runs code when anything it
  evaluates as the module loads can run code other than Python's own, which
  a call, a decorator, a default, an evaluated annotation, an attribute
  access or an operator on a name, and a base class whose metaclass or
  `__init_subclass__` is not Python's all can. A base of the project is
  followed through its bases, into the module that defines it. The
  exceptions are a short list of callables, resolved through the module's
  own imports, that build a value and touch nothing else: `property`,
  `staticmethod` and `classmethod`, `abc.abstractmethod`, the `functools`
  caches, `contextlib` context managers, `typing.final` and `override`, the
  namespace-preserving class decorators, `TypeVar` and its kin, `NewType`,
  `dataclasses.field`, builtin constructors over constants, and
  `re.compile` of a constant pattern that compiles here without a warning;
  `typing.overload` records a registry and `logging.getLogger` a logger a
  later `dictConfig` would disable, so both count as code. Nothing under a
  literal `False` or the immutable integer comparison `0 > 1` runs,
  and a condition comparing `sys.version_info`, `sys.platform` or `os.name`
  with constants runs nothing. When a name the
  annotations need is defined only after such code, the helper goes before
  it anyway where annotations are postponed (`from __future__ import
  annotations`), and is declined elsewhere. Cross-file helpers add a module
  import. Every import Towel writes, a helper's and a typed run's `import
  typing as _typing` alike, goes after each statement ahead of the module's
  first definition that imports or can run code at import, so a script's
  `print("loading")` above its imports and a `time.sleep = patch` below them
  still run before what the new import loads. It goes after the docstring
  when there is one, and never above a `#!` line, an encoding declaration
  (line 1 or 2), a file-wide `# type: ignore` or the module's leading
  comment block. A write that Python would read in another encoding than
  the file's own is refused, and its proposal dropped. Static local import cycles are rejected
  (including cycles through a package's `__init__`, which `from . import
  name` runs), dynamic ones are not detected. An import under a
  provably false constant guard closes no cycle. Imported `TYPE_CHECKING`
  flags and locally assigned Boolean names are mutable, so both branches
  remain possible runtime edges. Setting `typing.TYPE_CHECKING = True` is
  ordinary assignment and is covered by the preservation requirement.
- **Concurrency of application.** A run refactors a private copy of the
  project and writes back only when it has succeeded, as one batch; a file
  edited during the run refuses the batch, nothing written. Files are replaced
  atomically one at a time; a batch is not atomic across files to a concurrent
  reader. Application requires exclusive write access; a concurrent editor
  writing in the check/replace interval is not prevented. Interrupted batches
  leave a recovery journal.
- **Hard-linked files in place.** A file is replaced by renaming a new one
  over it, which would leave its other hard links holding the old text. An
  in-place directory run names each hard-linked file before it starts and
  declines every proposal that would write one ("not writable in place: its
  file is hard-linked"); the rest of the run is written. A single hard-linked
  file refactored in place is refused before the run, with the remedy of
  writing to a new file. Out of place, such files are refactored as any other.

### Reflection over a namespace, and stack depth

A program that enumerates a namespace can see an added helper. A metaclass,
class decorator, descriptor, `__init_subclass__`, package initializer or lookup
hook can then register, wrap, instrument or redirect that helper. Private names
prevent accidental overrides but do not conceal helpers from reflection.
Towel neither detects these behaviors to refuse extraction nor guarantees
that their results remain unchanged.

The same exclusion covers source/AST/bytecode inspection, direct frame reads,
source-sensitive callees and import hooks that transform module bodies. There
is no exception for recognized libraries or for explicit `@` syntax: `@deco`,
`f = deco(f)`, stacked calls and class hooks share the same boundary. The
[October 2 decision](DECISIONS.md#2026-10-02-reflection-and-self-instrumentation-are-outside-the-preservation-contract)
supersedes September 27's selected-instrumentation protection.

Each helper call adds a stack frame. Recursive functions are refactored like
any other, so a deeply recursive function uses more stack after extraction
and may reach Python's recursion limit sooner.

### A cross-file helper adds an import of its host module

Sharing a helper across modules is opt-in (`--cross-module`,
`cross_module_helpers`). By default only duplicates within one module are
paired, pairs across modules are neither formed nor counted against the pair
budget, and no import between the project's modules that runs is ever
written. The one import of a project module the default writes is the
type-only one a helper's annotation needs, under `if 0 > 1:`, which
never runs; it is spelled by the rules below, and where none applies the
annotation keeps the checker's full name.

With `--cross-module`, a helper shared across modules lives in one of them
and the others import it, spelled as the program's own imports show that
import works (docs/DECISIONS.md, "Import names come from the program"):
between two modules of one package the relative import, or the absolute one
where the importing module already spells its own package absolutely; across
top-level packages an absolute import only where the importing package
already imports the other; and into a directory only where the importing
side already imports from it, with an import that runs whenever its module
is imported, so `bs4` never borrows from `bs4/tests`, which its wheel leaves
out. An import inside a function shows nothing: shop's `cli.main` imported
`shop.devtools` only for a developer's command, setuptools' `find` excluded
it from the wheel, and `shop/stats.py` was made to import it unconditionally
(the third audit's D5). A module the build configuration declares left out
of the wheel or the sdist, from which the wheel is usually built, is never a
host for a module it keeps: hatch's `exclude = ["src/shop/_devtools.py"]`
kept one module out of a package that ships, and the installed `shop.stats`
could not import the helper hosted there, and so did Poetry's `packages =
[{ include = "shop/[!_]*.py", from = "src" }]` and PDM's `includes` (round
4). The declarations read, each in the distribution's own pyproject.toml or
setup.cfg, only to put a host in doubt and never to name a module, are
hatch's `exclude`, `include`, `only-include`, `packages` and
`only-packages`, and its wheel's default of the package named after the
project; setuptools' `packages.find` `where`, `include` and `exclude`, an
explicit `packages` list, `package-dir` and `py-modules`; MANIFEST.in's
`exclude`, `recursive-exclude`, `global-exclude` and `prune`; Poetry's
`packages` (`include`, `from`, `format`), `include` and `exclude`, and its
default package; PDM's `includes`, `excludes`, `source-includes` and
`package-dir`, and its default of `package-dir`'s packages; uv's
`module-name`, `module-root`, `source-include`, `source-exclude` and
`wheel-exclude`; flit's module and its sdist `exclude`; scikit-build-core's
`wheel.packages` and its excludes; and every `.gitignore` above a module.
Each is read to leave out at least what the backend would, as the wheels
each backend built of `tests/test_shipped_files_recorded.py`'s trees show,
and what puts a file back (hatch's `force-include`, MANIFEST.in's `graft`)
is not read. What a setup.py, a build hook, setuptools' discovery where
nothing selects, or a backend not listed leaves out is not known, and a
module it leaves out can still host a helper. A candidate host
some borrower cannot import that way is never taken, and a pair none
survives is declined (`unproven_import`).
Names are read from every Python file under the project root, but not from a
directory `--exclude` names, which is how a stray copy is set aside, nor from
the directories every scan skips or behind a symbolic link; what an import
that enters one runs is then unknown, and a host whose import would enter
one is not taken. The costs, accepted by the owner: sibling packages that
never import each other share nothing, and nor do subpackages that never
import from each other (hostile fixture `xf23`); a directory of scripts that
import nothing local gets no cross-file helpers; and a name in doubt gets
none. A name is in doubt when the tree holds two copies of it, such as a
stale `build/lib/alpha` beside `src/alpha`, which `--exclude` sets aside;
when this interpreter can import it from outside the project, the project's
own `.venv` included, which `--exclude` cannot reach, so Towel must run where
that name is this tree (the project installed editable) or where nothing
provides it; and when the project requires a distribution of that name, so
that installed it imports the distribution and not the directory, which is
resolved by renaming or excluding the directory, or by dropping the
requirement. The third audit's P1-2 was such a namesake: `app` required
`click>=8` and the tree held a `click/` sharing `utils.py` with the library;
run from `uvx`, which lacks click, Towel hosted a helper in `click/utils.py`,
and the installed `app` could not import it. A requirement is read from
PEP 621's dependencies and extras, PEP 735's groups, Poetry's dependency
tables, the development dependencies of `[tool.uv]` and `[tool.pdm]`, every
hatch environment's `dependencies` and `extra-dependencies` (in
pyproject.toml or hatch.toml), setup.cfg's `install_requires` and
`extras_require`, a Pipfile, the uv, Poetry, PDM and Pipenv lockfiles, and
the requirements files at the root (`requirements*.txt`,
`*-requirements.txt`, `*_requirements.txt`, the same with pip-tools' `.in`,
and every `.txt` or `.in` in `requirements/`, with what they include), and
matched to a name by its own normalized name. So a distribution whose import name
differs from its own (`PyYAML` provides `yaml`), a dependency's dependency
where no lockfile records it, and whatever a setup.py, `tox.ini`, a
noxfile, hatch's environment `overrides` or a CI recipe installs are known
only when this interpreter can import them: Towel
runs with the interpreter it was started with, which stands for the
project's, so run it in the project's own environment. A directory the
program imports a module of that it lacks, from outside it, is in doubt
too: `third_party/click/` holding `utils.py` is not the `click` whose
`click.core` the program also imports (the round-4 audit's P1-3), and
`--exclude` or a rename resolves it. The directory's own import of a module
it lacks, a build's `_version.py`, is no such sign, and nor is any when the
project's metadata names the project itself so: sphinx's test data imports
a `sphinx.missing_module4` its tests mock, and the distribution named
`Sphinx` is sphinx itself. So a namesake the program imports only modules
of that it holds, where the library is unseen as above, is still taken for
the library. A top-level name
found only as a module inside a package the program imports as one, as
`pkg/c.py`'s `import helpers_top` finds only `pkg/helpers_top.py`, is in
doubt too, and the file making the import runs as a script, so it is given
no new import (the third audit's P1-6). So is a file a link gives a second
name the program uses: a directory link `beta -> src/alpha` beside
`alpha`, a file link, a link inside a package that an import goes through,
or a hard link, each resolved by replacing the link with a copy (the
round-4 audit's P1-4). Only an import that attests locates a name,
makes it ambiguous, or finds it inside a package: one inside
`try`/`except ImportError`, under a runtime condition, or in a file that
changes `sys.path`, as graphene's setup.py imports `pyutils.version` after
appending the package to `sys.path`, does none of these, though one that
runs still counts where it can load a file under a second name. An imported
`TYPE_CHECKING` flag is mutable, so its imports remain possible runtime
dependencies; only a proved immutable false guard excludes them. Before it
writes anything, a
`--cross-module` run of `dry` or `preview` names every such problem with the
remedy for its kind, and refuses the run when one leaves in doubt a
top-level name located at or around the target, or lies under the target and
leaves its own file's name in doubt. An import of a module the tree lacks,
however it is spelled (`from . import gone` and `from pkg import gone` as
much as `from .gone import x`, where nothing binds `gone`), leaves no name in
doubt and refuses nothing, and does not make a stray `src/__init__.py` a
package the program uses, from the
root, on a package or on a subpackage; the file making it is left exactly as
it was, with no helper hosted, borrowed or extracted within it; one under
an immutable false guard never runs and leaves its file free; an uncertain
conditional import does not establish an unconditional dependency. The cost is
that file's own duplicates, and, when it is a package's `__init__.py`, every
helper a module outside that package would borrow from a module inside it:
chardet's `detect` and `detect_all` share a block in its `__init__.py`,
which a fresh clone lacking `_version.py` leaves as it is. Only a
`--cross-module` run consults the model; a run without it refactors such a
file as any other, and writes no import between modules that runs.

The host is chosen so that no import cycle closes and no original module load
is advanced past definitions. A new submodule load is also refused: Python
binds the submodule on its parent package, replacing any ordinary value
already there, including one set by code outside the analyzed tree. A fresh
top-level host must contain only literal bindings and inert function
definitions, apart from proven standard future directives, and satisfy the
remaining import checks. Every import on the way is resolved as the program's
import model resolves it, an ambiguous name to every place it could be. Towel
does not know whether importing that module has
requirements of its own: gunicorn's `workers/gtornado.py` raises at import
time unless tornado is installed, and a helper hosted there made
`workers/sync.py` import it, so environments without tornado could no longer
import the sync worker. Towel now refuses a host whose import would run
code (the judgment of *Import-time behavior* above) in a module the
borrower's own imports do not already run (`import_time_effects`): a
registration decorator there would register wherever the borrower is
imported (fixtures `xf27`, `xf28`). A module whose classes derive from a
base with a metaclass of the project's own counts as running code even when
that metaclass only builds the class, since nothing shows it registers
nothing; in earlier validation this cost Pygments 12 of its 23 cross-module
helpers. Towel also refuses a host
whose import would require a module the borrower does not already
import: an unconditional import, including one inside a module-level `if`,
of anything outside the project, the standard library and the project's
declared dependencies: PEP 621's `[project].dependencies`, Poetry's
`[tool.poetry.dependencies]` less the optional ones an extra installs, and
setup.cfg's `install_requires`, each less any a marker limits
(`pywin32; sys_platform == "win32"`, or Poetry's `markers`, `platform` or
`python`); a setup.py is not run, so dependencies it alone declares are not
known. The standard library counts only where its documentation places it
(`known_platforms`, read from the Availability notes of CPython 3.11 to
3.13): `msvcrt`, `winreg`, `os.startfile`, `ctypes.windll` and
`signal.CTRL_C_EVENT` are Windows's alone, `fcntl`, `termios`, `os.fork` and
`signal.SIGALRM` are missing on Windows, `tkinter` wherever Python lacks
Tk, and a module a supported version removed, `distutils` or `cgi`, is
missing there; a name below a module counts as the module (`curses.ascii`).
Notes that leave out only WebAssembly and mobile platforms are not read,
since the documentation says those mean a module "does not work or is not
available" there, nor is availability stated only in prose, nor are
undocumented private modules (`_winapi`). An import inside a `try` whose
handlers catch `ImportError` is taken as an optional dependency and
requires nothing; one in its handler, `else` or `finally`, or in a `try`
that lets the error through, is required. A module the program imports only
under a condition hosts a helper only for a borrower whose own import
already loads it (`conditionally_imported_host`): every import of it sits
under an `if` (a `sys.platform`, `os.name` or `platform.system()` test, or
any other), in a `try`, a loop or a `with`, or in a function body, or in a
module that is itself so imported, as shop's `__init__` imports
`_winconsole` only under `sys.platform == "win32"`. One import at the top
of any other module, a test's included, shows the module importable where
that module is. The cost is that two modules gated alike share no helper
unless one already imports the other. A dependency declared under a
distribution name that differs from its import name (`PyYAML` for `yaml`) is
not recognized, which refuses a host rather than accepting one; an import
made by `importlib` or `__import__` is not seen at all.

A distribution is released on its own, so a borrower that belongs to one
borrows only from a host in the same distribution (`other_distribution`): in
a monorepo, `beta` depending on `alpha` was made to import a helper from
`alpha/a.py`, and the new beta installed against the released alpha raised
`ImportError`. A module belongs to the nearest directory above it with a
`setup.py`, a `setup.cfg` declaring `[metadata]` or `[options]`, or a
`pyproject.toml` with a `[project]`, `[build-system]` or `[tool.poetry]`
table; one in none, a test or a script beside the packages, keeps the rules
below. On a monorepo-shaped fixture with helpers within each of two
distributions, across them, and in a root test, only the one across them is
declined.

A distribution ships the packages its metadata names, not the repository, so
a host is refused as well when its import would load a module of a
top-level package the borrower does not already rely on
(`new_top_level_package`): its own, one its import already loads, or one its
package already imports at run time, the rule new imports are spelled by. A
helper hosted in `tests/test_b.py` that `zeta/a.py` imported made the
installed `zeta.a` raise `ModuleNotFoundError`. Another participating module
is then tried as host, so a test module that imports the package under test
takes the helper from it, and a pair between the package and a module that
never imports it is declined. What a
borrower "already loads" counts its enclosing packages and explicit module
imports before its first definition, including those imports' parent packages.
`import pkg.host` and `from pkg.host import value` establish that host;
`from pkg import host` may read an ordinary attribute and does not. An imported
module can still be
initializing, so its later imports establish no further completed loads.
Imports in a function body, a branch or a `try` make nothing certainly present.
A fresh shared helper is defined before ordinary imports and effects, with
quoted annotations, so a partially initialized host can supply it. An existing
generated helper must already have an inert prefix that proves its definition
is available. Changing a program's imports to overcome these refusals can
change its behavior and requires a separate review.

A module written to run as a program may be run by its path: `__main__.py`,
a module whose first line is a `#!` interpreter line, and one with a main
guard anywhere in its own scope, `__name__ == "__main__"` either way round
or opening an `and`. A module that runs code at import with no such sign
of being a script is taken to be imported only. Run by path, the module's own
directory is on `sys.path` in place of the directory its package is imported
from: `python pkg/tool_b.py` finds `pkg` only where something else put it on
the path.
Such a borrower gains an import only when a run by path already needed
what it needs (`run_by_path_import`): when the imports it runs before its
first definition already import that top-level package absolutely, or its
own directory holds the host's package; or when one of those leading
imports is relative, so a run by path already fails there. The import it
gains must then be absolute, and a relative one is declined when it is
written (`r08`; `tests/test_run_by_path.py`).

A type checker reads a module's stub in its place, so a host is refused as
well when it has one (`host_has_stub`): a `.pyi` beside it, its stub under
the project's `typings` directory, or a `<package>-stubs` directory for its
top-level package that holds its stub, or that is not partial and so hides
every module it omits. The stub is looked for under the name the program's
imports give the host and under the one its package chain gives it, since
mypy resolves even a relative import to an absolute name. `from alpha.a import __extracted_func_0` against
`alpha/a.pyi` was an unknown symbol to pyright and a missing attribute to
mypy (audit `k30`). Stub directories named only in a checker's
configuration (`mypy_path`, pyright's `stubPath`) are not read.

## Method insertion

A helper becomes a method only of the class whose methods hold both
duplicates, one module-level class statement, and nowhere else: a block
shared by sibling classes, by a parent and its child, or by classes in
different modules becomes a module-level function that takes the receiver as
an ordinary argument (docs/DECISIONS.md, "A method helper lives in the class
that holds both duplicates"). No class gains a member unless the code it
replaces was already in that class. Whether such a function belongs in a
class, an existing base, a mixin or a new one, is a design question Towel
leaves to whoever reviews the change ("Towel does not change externally
visible class design"). The rule's costs:

- A block shared across classes is a module function with an explicit
  receiver, which is sound but less idiomatic than a method.
- Such a block is declined when the code moving out of the class uses a
  class-private name (`self.__x`), which a module function would have to
  spell mangled, `receiver._A__x`, a spelling mypy and pyright both reject
  (`private_name_lexical_class`). `__class__` is passed to it as an
  argument, each site passing its own class. Zero-argument `super()` in such
  a block is declined (`needs_class_body`): a module function has no class
  cell, and its `super()` raises `RuntimeError`.
- A pyright-strict project rejects a module function that reads a protected
  attribute (`receiver._cache`, `reportPrivateUsage`), so its own check
  declines such an extraction.
- Across modules the function lives in one of them and the other imports
  it, under the import rules above; a base class both modules already
  imported no longer serves as the host. On the pricing corpus this
  declines 6 of pygments' 77 refactorings and 3 of rich's 33, for the
  import-time code of the module that would host the function.
- In typed mode the receiver is annotated from the call sites, as a union of
  the classes or as `Any`, and no generic signature is tried whose receiver
  is a different class at each site: two subclasses sharing `values[0]` of a
  `list[int]` in one and a `list[str]` in the other, once a generic method of
  their base, are declined under mypy's strict checks.

A method helper is class-private: `__extracted_func_0`, which its class `A`
stores as `_A__extracted_func_0` and its methods call as
`self.__extracted_func_0()`. An ordinary differently named subclass's own
`__extracted_func_0` is stored under its own name, preventing an accidental
override through that spelling. Mangling goes by name, not by class, so
a subclass named like its base (`class A(base.A)`) that defines
`__extracted_func_0` stores it as `_A__extracted_func_0` too: Python sources
under the project root are read for such a member, for `_A__extracted_func_0`
spelled out, and for explicit attribute stores or deletions of the stored
name before a number is chosen. Sources outside that root are not seen.
A class named only with underscores (`class __`) mangles nothing and gets a
module-level helper. A module-level helper avoids identifiers at its insertion
site and names that other project sources explicitly store or delete as
attributes (`lib._extracted_func_0 = ...`); an unrelated class member does not
claim a module helper's name. Names supplied reflectively through `setattr`,
namespace subscripts or `type(..., namespace)` are not reserved and have no
preservation guarantee. The root is the nearest directory with packaging
metadata above the input (or the directory above its packages), read with the
consumer scan's exclusions.

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

The class holding both duplicates takes the helper only when every
decorator on the source methods is known to preserve the receiver, the
methods have a first parameter named `self` (or the method is a
`classmethod`), and both read an attribute of it. That parameter's
annotation, if it has one, must name only the class: the class itself,
`Self`, or a type variable bound to the class, or `type[...]` of one of those
for a class method. `def m(self: HasV)` declares that any object with the
protocol's attributes may be passed, as `Box.m(other)`, and
`self.__extracted_func_0()` would raise `AttributeError` on it, so such a
method gets the module-level helper that takes the receiver as an argument.
The class's declared contract must permit adding a private helper: not a
`Protocol` (a method there is one more member every structural implementer
lacks, so a runtime-checkable `isinstance` turns false), and not written with
its body on the header's line (`class Base: pass` takes no further statement).
An unknown class decorator does not itself veto adding a helper. Decorator
classification still informs ordinary Protocol resolution and import-time
effects. A base that could be `Protocol` on any path through its module, or is spelled
`Protocol`, counts as one. Direct-base aliases are followed through project
imports, assignments and conditional expressions. A concrete class that
implements a protocol is still eligible; an unresolved computed base, such
as a factory call, takes a module helper instead. This does not evaluate
arbitrary calls or inspect aliases inside external libraries.
Namespace scans and lookup interception that
observe the added helper are reflection (see *Reflection over a namespace,
and stack depth*). Towel does not inspect ancestry to approve that machinery.
The class-private name prevents an ordinary subclass method from accidentally
overriding the helper; it does not bypass a hook that observes every lookup.
A `__getattr__` that serves unknown names still runs only when normal lookup
fails, so an existing `_extracted_func_0` supplied from data remains separate
from the new mangled name (fixture `r158`).
Local classes, nested classes, duplicated class names, decorators not known
to preserve the receiver (`mock.patch`, `pytest.mark.*`), functions nested inside methods, and class-body functions with no parameter or
a first parameter other than `self` get a module-level helper that takes the
receiver explicitly. Additional call sites gathered from the same file join a
method helper only when they are methods of the same class with the same
receiver kind; other occurrences keep their code.

Zero-argument `super()` is `super(__class__, self)`: the cell the compiler
gives every function of a class body that loads `super` or `__class__`, and
the current value of the frame's first argument. A method helper of the class
holding both duplicates has the same cell and, reached as
`self.__extracted_func_0()`, the same receiver first, so code using `super()`
moves there and nowhere else. Every condition above applies, and none falls
back to a module function: a pair whose helper cannot be a method of that
class is declined (`needs_class_body`), including one in methods that never
read an attribute of their receiver, which may still run with `None` in its
place (`super(One, None)` is an unbound super whose own attributes answer,
though from Python 3.12 a call site that has already run raises instead). The
method must also keep its first parameter, never rebinding or deleting it,
not even through a nested `nonlocal`, and its own scope must leave
`__class__` alone (`frame_sensitive_block`): otherwise its `super()` reads
something a helper's would not. A `super()` in a lambda, nested function or
comprehension of the moved code moves with it and reads that scope's own
first argument, as before; a comprehension's is the receiver where Python
inlines it (3.12 on) and its iterator where it does not, in the method and
the helper alike. `__class__` beside `super()` is read bare in the helper,
its own cell, which a parameter of that name would hide. A call site that
would evaluate `super()` itself, as a thunk or by passing `super` for the
helper to call, is declined (`super_in_call`). `super(One, self)`, which names
its class and object, reads no cell and moves anywhere.

## Type annotations on helpers

Some extractions no helper signature can type, and a typed run declines
them early, under the reason its summary counts:

- *narrows what its caller reads after the call*: the block tests a name or
  attribute that the code after it relies on (packaging's `if
  self._key_cache is None`), and the narrowing ends with the helper;
- *narrows what a call-site lambda reads*: a test that once governed an
  expression now passed as a lambda (rich's `task.total is not None` beside
  `lambda: int(task.total)`);
- *declares its class's attributes*: assignments through a method's
  receiver were the class's attribute declarations (nox's
  `self.location_name = location`), and a function outside the class
  declares none;
- *completes its caller's partial type*: mypy learns an empty collection's
  element type from the next statement that fills it (mistune's `attrs =
  {}`), and passed to a call first it is an error at the assignment;
- *would be the one unannotated function of its module*: every annotated
  signature was refused, in a module whose every function is annotated,
  where a checker that skips unannotated bodies would accept what the
  project's stricter CI rejects (idna).

Annotations are written only from evidence, and their limits follow from
where the evidence comes from:

- Every name bound for an annotation is private and spelled nowhere in its
  module (`import typing as _typing`, `from pkg.models import Item as
  _Item`), so nothing the program already binds
  changes meaning and no module that star-imports the helper's module takes
  a new public name. The one assumption is the one the helpers' own names
  rest on: a star import brings a private name only from a provider whose
  `__all__` lists it. A binding the module already has is used instead only
  when it is the name's single binding anywhere in the module; the module's
  existing annotation imports can be reused without changing their guards.
  New type-only imports use `if 0 > 1:`, an immutable false comparison that
  both mypy and pyright read for types. Existing `TYPE_CHECKING` guards are
  never extended with new imports. A new guard carries `# pragma: no cover`
  where the
  project's coverage.py exclusions match only with it, as coverage.py's
  defaults do. A project whose exclusions match neither form, such as one
  whose `exclude_lines` lists `if TYPE_CHECKING:` and not the pragma, counts
  the guarded import as a missed line.

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
  The checkers' own notation is read as the annotation it means, mypy's
  before and after 2.0 and pyright's: a callable (`def (x: int)` returns
  `None`), one returning another, a generic callable, a named tuple or typed
  dict as its class, and a literal widened to its type only where mypy marks
  it inferred (`Literal['a']?`). A callable no parameter list can state is
  written `Callable[..., R]`. A class passed as a value, or returned by a
  thunk the site passes (`lambda: Row`), is `type[Row]`, not its constructor.
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
  conflicting free-variable domains, variadic type parameters, names that
  resolve to nothing, and `Any`/`Unknown` as the whole type decline generic
  inference; `Any` inside a type (`Mapping[str, Any]`) is the program's own.
  A type the checker reveals is matched by the absolute name the program's
  imports give its module, or, for a module none names, the name mypy is
  given for it, so the checker's `pkg.version.Version` is the host's
  `Version`, and a name imported only under `TYPE_CHECKING` counts. A type
  only a call site can name may stand inside a type variable, but a signature
  that would have to write it where the helper is defined is not offered, and
  a variable is not constrained to it. A parameter the helper's body never
  reads is `object`. At most two generic contracts
  are tried: unrestricted concrete disagreements, then constraints with two to
  four concrete alternatives; sites in different modules that agree on every
  type are offered their common signature instead. Every fresh helper type
  parameter must occur in an input. Instance and class helpers retain type parameters bound by their host
  class, which can also appear only in the result. A module helper taken from
  static methods freshens source class parameters and must infer them from
  explicit arguments. Generic method
  inference currently requires implicit `self`/`cls` typing; explicit receiver
  contracts are not generalized.
  A module function shared by methods of different classes gets no generic
  signature whose receiver differs by site (see *Method insertion*).
  Function-hosted generic helpers remain unsupported. See
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
  imported under an immutable false guard and named by a private alias; only where no module of
  the project owns it, or its short name is already taken, is the type left
  unwritten and the parameter completed with `Any`. Any subscripted
  annotation that would not evaluate at definition time (`memoryview[int]`
  on an interpreter where `memoryview` is not generic) is written as a
  string. So is any annotation using syntax younger than the oldest Python
  the helper's module has to run on, in a module that does not postpone its
  annotations: a union written with `|` before 3.10, a subscripted builtin or
  `collections.abc` class before 3.9. That Python is the lower bound of the
  project's `requires-python` (or setup.cfg's `python_requires`, or Poetry's
  `python` dependency), else the older of mypy's `python_version` and pyright's
  `pythonVersion`; where the project declares none, the oldest Python the
  syntax could need, unless the module already evaluates that syntax in its
  own top-level signatures, which it could not import without. Generic inference can also retain a foreign site's imported type when
  the helper's host binds the same canonical import; matching spellings alone
  are insufficient. Its annotations and TypeVar domains are quoted.
- The typing guarantee is exactly as strong as the checker the project
  configures. Towel verifies every prospective change with the configured
  checker or checkers and declines whatever they reject; it makes no claim
  about a checker the project does not configure, even one that happens to be
  installed. A project that configures mypy alone can therefore accept output
  that Pyright would reject, and the reverse. Configure both to be checked by
  both. A configured checker that is not installed where Towel runs refuses the
  typed run before anything is written; it is never replaced by the other
  checker or by none, so `--no-types` is the only way to proceed without it.

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
- A rejected precise signature does not automatically erase all annotations.
  The bounded annotation ladder can generalize complete argument/result rows
  and try targeted `Any` substitutions. Each candidate still passes the project
  check; the ladder preserves unchanged annotations where that narrower repair
  succeeds. A final broad fallback is a separate validated candidate, not
  permission to ignore new checker errors.
- Verification checks complete prospective project graphs, overlaying all
  changed files together, and compares each with the project as it stood. A newly
  imported helper therefore exists in its host while its consumers are checked.
  Project checking rules are honored, and so are configured mypy plugins,
  which are loaded and run as in the project's own mypy run (a plugin module
  must be importable from the interpreter Towel runs; a `.py` path is resolved
  from the configuration file). A plugin that cannot be loaded refuses the
  typed run before anything is written. Configured executables and report
  destinations are never used. Each mypy build imports the plugins afresh in a
  forked child, so a plugin that is slow to import (django-stubs sets Django
  up) costs that much on every check.
- Errors the original check reports are compared with, not refused. A change
  is rejected for an error its check reports that the project's check did not:
  in a file no change has touched, the same message at the same line; in one a
  change has touched, the same message on the same line wherever the change
  moved it, found by a line diff of the two texts in which the copies the
  change replaced and the helper it wrote pair with nothing. An error in the
  helper must be the same message at the same statement of one copy of the
  block the helper was made from, statements counted in order through the
  copy and through the helper's body, and each of that copy's errors accounts
  for one; an error at a call site must be the same message on the lines that
  call replaced; any other error on a line the change wrote must be the same
  message on the lines the same stretch of the diff replaced. Everything else
  is new. Merging two copies into one helper frees the other copy's errors,
  and they account for nothing: before this, with both copies holding `n + s`
  and `s + n`, a helper typed `int | str` whose `p + p` raised the same two
  messages on a line both copies had clean was accepted. One that disappears
  from a line the change left alone accounts for nothing either. A helper
  whose statements do not follow its block's (one Towel added before them)
  is accounted for by no copy, an error on an import the sorter moved into
  another stretch of the file is new, and where either text of a changed file
  is unknown every error in it is new; each costs a change. A message
  that names a line (mypy's `Name "x" already defined on line 12`) reappears
  as new when that line moves, which declines the change rather than hide an
  error: a module with such an error below the place a helper would go keeps
  its duplicates. Once a driver writes a change, that change's own check is the
  reference for the next, so a later change cannot bring back an error an
  earlier one removed; direct `apply_refactoring` calls, whose writes the
  engine does not see, compare with the original's errors throughout.
- The check Towel runs is not always the project's own. It covers the tests,
  benchmarks and docs the project's configuration reaches, which a CI job may
  never check, every checker the project configures, including one no CI step
  runs, and it runs without flags the CI passes on its command line. In a
  study of 20 corpus projects, all 17 that type-check pass their own check as
  their CI runs it, and Towel's check was clean for 6; none of the errors it
  reported was one the project's check reports. Those errors are compared with
  like any other, so a change is checked there too, against what that check
  already says. The difference cuts the other way as well: Towel's check can
  accept what the project's rejects. Refactored with types in their own
  environments, 16 of those 17 projects still pass their own check; two did
  not until code the checker does not look at was left alone, and one still
  does not. idna configures no mypy, so Towel checked it with mypy's defaults,
  which accept an unannotated helper, and its CI runs `mypy --strict idna`,
  which rejects it (four errors); the same output comes from a project whose
  baseline was clean, as idna's is without its two fuzz tests. trio's CI runs
  mypy for linux, darwin and win32, and Towel's check ran for the platform it
  runs on: a module that begins `assert sys.platform == "win32" or not
  TYPE_CHECKING` is unreachable to mypy anywhere else, so nothing in it was
  checked, and there Towel accepted a helper annotated with `Any`, which
  trio's configuration forbids, and one that moved two classes' attribute
  assignments out of their `__init__`, which hides the attributes from the
  checker (13 errors for win32, one for darwin). Code the checker does not
  look at is no longer changed (below), which closes trio's case: in its
  Python 3.11 environment the run names 137 such regions, declines the 5
  proposals that touch them, and applies 17, and all three platforms of its
  CI's mypy pass. Checking as the project's CI does, flags included, is what
  would close idna's.
- Code the checker does not look at is not changed. A checker takes code to
  be unreachable when the platform and Python version it checks for make a
  `sys.platform`, `sys.version_info` or `TYPE_CHECKING` test false, when an
  `assert` it knows fails precedes it, when nothing can reach it (after a
  `return` on every path), or when the declared types rule it out on every
  platform (packaging's `return NotImplemented` after an `isinstance` test
  its argument's annotation always passes), and reports nothing there, so
  its acceptance of a change there says nothing. Which code that is, is each checker's own rule,
  so Towel asks it: a `reveal_type((0))` placed before a statement is answered
  exactly where the checker looks. Before accepting a change, every
  configured checker is asked about each statement on the lines the change
  writes (the call sites, the helper, its imports), and one it does not answer
  at declines the change as `not verifiable: the type checker does not look at
  the code it changes`. Before the run, the checker that infers is asked about
  the start of every block of the analyzed files, and the blocks it does not
  look at are named and not changed at all; mypy and pyright each take their
  own platform and version, so pyright set to check for Windows can skip what
  mypy looks at, and only the question put to every checker settles a change.
  A body that shares its header's line (`if x: return`, including one-line
  `case`, `except` and `except*` bodies) is probed on a line of its own in the
  text the checker is given. Semicolon-separated statements have separate probe
  sites, so an early exit does not make the following statement appear looked at.
  A module in which no probe can be placed is taken to be looked at nowhere.
  A probe build that fails is not silence: before the run it refuses the run, and
  for a change it leaves the
  change not judged. The body of a function without
  annotations, which mypy does not check unless configured to, counts as
  looked at, since mypy answers there (with `Any`): the project's own mypy
  leaves it unchecked on every platform too. A file a checker's configuration
  has it report nothing on -- pyright's `exclude` and `ignore`, and what its
  `include` leaves out, read from the configuration and its `extends` chain
  before the run -- is outside that checker's check, not code it takes to be
  unreachable: it is not probed with that checker, and the other checkers
  settle it (param's pyright ignores `version.py`, which its mypy checks; all
  four proposals there had been declined as unreachable). A language server
  asked about such a file never answered, and the run waited a minute and
  then gave the server up; the marker a settle waits for now goes where the
  server reports, spelled as the server knows its workspace (a target outside
  `include = ["src"]` had its marker written in `src` under the resolved
  `/private/var` spelling of a copy made in `/var`, and waited out the minute
  all the same), and, for an `include` that names no directory outright, in
  the first directory whose files it matches.
  mypy reports on what the project's own mypy run checks --
  its `files`, `packages` or `modules`, else what Towel is pointed at, less
  what `exclude` matches -- and on what those follow their imports to, but
  not on a module whose options set `ignore_errors`, which suppresses its
  `reveal_type` notes with its errors, nor on an implementation behind its own
  stub. A file no configured checker reports on is changed as the
  body of an unannotated function is, since the project's own check says
  nothing there on any platform, and its helper takes only the annotations
  its sites declare, completed with `Any`: nothing would check an inferred
  one, and the most precise rung, tried first, was accepted unchecked. The
  project check still judges what the change does to the files the checkers
  report on.
- Where the original check leaves a name it cannot type -- an import it cannot
  resolve or finds no types for (mypy's `import-not-found` and
  `import-untyped`, pyright's `reportMissingImports` and
  `reportMissingTypeStubs`), a decorator without types, a base class of type
  `Any` -- no change to that file is attempted. A configuration can silence
  the errors that say so (`ignore_missing_imports`, pyright's
  `reportMissingImports = "none"`), so every checker is also asked, before the
  run, what each import of the analyzed files binds: a probe imports the same
  module and names under names of its own, where the import stands, and
  reveals them. mypy answers `Any` for a module it cannot resolve or finds no
  types for, and pyright `Unknown` for a name or attribute such an import
  binds (pyright gives the module itself a module's type). For a queried `Self`
  import or module attribute, the same checker is also asked in a fresh class
  at the import site. Only its exact contextual self type establishes the
  import's identity; custom classes, instances and type variables named `Self`
  do not provide that evidence. The ordinary checker comparison still rejects
  new type errors in value and annotation uses. A fresh parent-module
  alias can hide a submodule that Pyright sees through the program's original
  binding. When the parent and submodule are imported in one module-level
  import block, every read of that binding follows the block, and there are no
  competing module bindings or writes to the queried path, its ancestors or
  descendants, Towel also asks about the original binding just after the block.
  Unrelated attribute writes and local names do not interfere. An alias
  replaced within that block is not asked about attributes read from its
  replacement afterward; uncertain binding or read order remains conservative.
  Only the same checker's exact named module overrides the alias's `Unknown`;
  unsupported contexts keep the ordinary check. This question shares the existing checker
  exchange. With the report
  silenced, uvicorn's `websockets` module missing where Towel ran, a change
  had left a `type: ignore` unused in the project's own check. A name a typed
  module declares as `Any` is the same wherever the check runs, and is not
  named. pyright with `typeCheckingMode = "off"` answers `Any` rather than
  `Unknown`, and reports missing imports as warnings, which the comparison
  does not count, so a file importing a module missing where Towel runs is
  not named there; that mode checks almost nothing. A subtype question about
  a type that spells `Any`, or that the checker finds assignable to a class
  of the probe's own (a class with an `Any` base), or one the checker gave no
  answer about at all, answers unknown, so it never folds one member of a
  union into another. Whatever such a name reaches is
  `Any`, which accepts every use, and the subtype questions that normalize a
  helper's annotations answer yes about it, so a misuse would pass Towel's
  check while the project's own, which may see the real type, rejects it. The
  run names these files before it starts, and counts the proposals declined
  for them as `not verifiable`; installing the missing module or its stubs
  where Towel runs has them refactored with types. A module that imports such
  a name from one of these files (`from pkg.compat import wcwidth`) sees it as
  `Any` too and is not declined. In the study's 20 projects, no module imports
  any of the 22 names that such imports and decorators bind at module scope.
- Literal unconditional `mypy --strict` checks in ordinary GitHub workflow
  jobs also determine the typed run. Supported commands are direct `mypy`,
  `python -m mypy` (including a literal versioned interpreter), and
  `uv run --frozen mypy`, with existing project path targets. Their explicit
  targets override configured `files`, `packages` and `modules`; per-module
  options retain mypy's precedence over CLI flags. The same policy applies
  to inference, baseline, candidate and final confirmation checks. Unsupported
  strict commands, conditional or computed contexts and conflicting policies
  refuse typed verification with a diagnostic. If one Towel input spans strict
  targets and files outside them, narrow the input. Other CI flags, platform
  matrices, checker versions and dependencies remain outside this reader's
  contract; it does not establish full CI parity.
- A project with neither mypy configuration nor a supported literal strict
  workflow check is checked with mypy's defaults, as its
  own `mypy` would check the same files: the bodies of functions without
  annotations are not checked, an import mypy finds no types for (not
  installed, or installed without stubs or `py.typed`) is an error, and a
  module is named by its packages, not from explicit package bases. Two
  files in directories that are not packages and share a name (a `conftest.py`
  in each of two test directories) therefore refuse the typed run with mypy's
  own message, as they refuse `mypy` itself; configuring mypy
  (`explicit_package_bases`, `ignore_missing_imports`) is how such a project
  says otherwise. Only the probes that infer a helper's types check untyped
  function bodies, in a cache of their own.
- A module that ships its own stub (`a.pyi` beside `a.py`) is checked through
  the stub, as mypy checks it: its importers see the stub, and the
  implementation itself is not checked by mypy unless the configuration's
  `files` names it. A change inside such an implementation is therefore not
  verified by mypy, exactly as the project's own mypy run never checks it;
  Pyright, when configured, still checks the implementation as a file. With
  mypy alone the implementation is a file no configured checker reports on,
  and its helper takes only the annotations its sites declare, completed with
  `Any`, as a file outside `files`, or one whose options set `ignore_errors`,
  does: mypy's probe of such a file still answers, because the probe makes
  it a source, and an inferred annotation there was accepted that no check
  of the project looks at. The stub cannot come to disagree with the code:
  every name and signature it declares is kept, and the helper, which it does
  not declare, is private to the module (a stubbed module never hosts a
  helper another module imports).
- Which files' errors count, and what each module is called, is mypy's own
  rule under the project's configuration and supported literal CI targets.
  Where neither names
  `files`, `packages` or `modules`, the project's run is taken to be mypy
  over what Towel is pointed at, less what `--exclude` names, so a consumer
  outside that target (the tests of `towel dry src/pkg`) is not checked; a
  run that includes it checks it. A file the configuration does not
  name counts where a module it names imports it and the imported module's
  own `follow_imports` (its `[[tool.mypy.overrides]]` section, else the
  global setting) reports what it finds there; a changed file the
  configuration does not follow at all (`skip`, `error`) is left out of the
  check, so its importers see what the project's run sees. Modules are named
  as mypy's walk names them (`explicit_package_bases`, `mypy_path`,
  namespace packages), and a changed file the configuration does not name is
  named as the import reaching it names it. A check of a target that leaves
  the project's own package out (`towel dry tests tests`) finds that package
  in the tree where an installed copy, typed or not, would answer for it, as
  the project's run over the tree finds it; where the configuration names
  `files`, that run is those files, and what they find installed is what the
  check finds. A lone module named like installed code (`examples/json5.py`)
  is not taken for it. A configuration mypy only warns about (an option it
  does not know, a global option in a per-module section, a Python version
  it has dropped) is checked as mypy checks it, and the warning is passed on.
- A project with more than one mypy configuration (a `pyproject.toml` with
  `[tool.mypy]` in a sub-project as well as at the root) is checked once per
  configuration, each in a cache of its own, as `mypy` run from that
  configuration's directory checks it: the root's covers the sub-project's
  files too, under the root's options, unless its own `files` or `exclude`
  says otherwise, and the sub-project's covers them under its own. A change
  must pass both, and a check reads a file of the other configuration's with
  the change's text wherever it imports one. Where that run could not build
  at all, two sub-projects each with a `tests/conftest.py` outside any
  package, say, the typed run over the root is refused as that `mypy` run is;
  run Towel on each sub-project, or have the root's configuration `exclude`
  them. Directories that no mypy configuration covers, grouped by their
  packaging files instead, are projects of their own, and no check spans
  two of them.
- Pyright verification uses a private copy of Python sources, stubs, typing
  markers and checker configuration, made once per run, kept in step with the
  project as it is refactored, and watched by one long-lived language server.
  A file is recopied when its content differs, not merely when its size or
  timestamp does, so an edit by something other than Towel cannot leave a
  verdict standing against a project the copy no longer matches. A
  `pyrightconfig.json`, or a `pyproject.toml` with a `[tool.pyright]` table,
  whose bytes are not UTF-8 is refused rather than copied without rewriting
  the absolute paths in it; any other `.json`, `.toml`, `.ini` or `.cfg` that
  is not UTF-8 is data to pyright and is copied as it is. A `pyrightconfig.json` (or a
  file it extends) that pyright itself cannot parse refuses the typed run
  before anything is written, naming the file and the position: pyright's
  grammar is JSON with `//` and `/* */` comments and one trailing comma per
  object or array, and a byte-order mark, a form feed or a no-break space is
  an error to it. pyright's language server would otherwise go on checking
  with default settings. A pyright command line that fails is reported with
  the end of what it printed to standard error. A symbolic link is a link in
  the copy. One into the project leads to the copy's counterpart, so an import
  through it sees a candidate's text; it used to be copied under its own
  name, and an import through it read the file as it was. One out of the
  project, or into an environment or another directory the copy leaves out,
  leads where it always did, so pyright enumerates and imports through it as
  it does in the project. A candidate that changes a file the copy reaches
  only through a link out of the project is not judged, since the check would
  read that file as it was. A link back to a directory that holds it, and a
  configured source or stub search root outside the project, cannot be
  represented safely and refuse the typed run. An environment the
  configuration names with `venvPath` and `venv` is where pyright resolves
  imports in the copy too, through a mirror of it whose `.pth` files lead into
  the copy where an editable install leads into the project; the copy left the
  environment out, and pyright fell back, silently, on Towel's interpreter. One
  pyright could not use (missing, unreadable, holding no site-packages) refuses
  the typed run, since pyright would fall back so. When Towel's interpreter is
  that environment's own, pyright adds the interpreter's standard library
  directories to its search paths, and through the mirror it does not; the
  standard library is resolved from typeshed first either way. A search path
  setting (`extraPaths`, `stubPath`, `typingsPath`, `typeshedPath`, an
  execution environment's `extraPaths`) that leads into what the copy leaves
  out, such as an environment's site directory, names the original in the
  copy's configuration, rather than nothing.
  Project include/exclude settings still determine the checker's coverage.
  A change is checked from the root of its nearest pyright configuration and
  from every configured root enclosing that one within the repository, and
  each root's copy shows every change beneath it: pyright run at the outer
  root checks a member with a configuration of its own as well, and a
  consumer there was judged against the member's original text. Where nothing
  configures pyright, the roots are checker and packaging roots, which enclose
  one another only inside a repository (a `.git` or `.hg` directory); outside
  one, a change to a member is checked from the member and from the roots of
  the run's other files, so a consumer in an enclosing directory that the
  run's target leaves out is not checked.
- Mypy runs in an owned worker process, each build in a forked child of it that exits once it has answered, and never freezes the caller's garbage
  collector. Library users should call the oracle's `close()` when finished;
  `CombinedOracle.close()` closes both checkers. The CLI closes its oracle on
  both success and failure.
- Pyright reads files, so a module whose types are asked about is written,
  with its probes, into Towel's private copy of the project, in place of the
  module's own copy there: the copy a language server watches, or one kept
  for the command line when no server runs. Nothing is written beside the
  project's files. The copies live in the system temporary directory and go
  when the oracle is closed; a kill signal can leave one there, never in the
  project.

## Decorators that compile or instrument a body

Reflection and body/class instrumentation are outside the preservation
guarantee. For example, typeguard can recompile a function's source with
checks, and numba can compile its body. Moving part of that body into a plain
helper can change what is checked or compiled. The same applies to project
code rewriting an AST, source readers such as inline-snapshot, and import
hooks that rewrite modules. Towel does not resolve an instrumenter's reach,
maintain a body-transformer allowlist or refuse extraction to preserve it.

This boundary applies equally to explicit decorators, calls such as
`fast = njit(kernel)`, stacked calls, metaclasses and construction hooks. It
does not exclude ordinary nonreflective calls, effects or receiver behavior
from the normal correctness checks, and does not mean that all decorated code
is refused.

Pytest's assertion rewriting is also unsupported: an `assert` may move to a
module that pytest rewrites differently, changing its diagnostic message.
Towel does not read pytest's rewrite configuration to prevent this. Earlier
reviews recorded differences involving `testpaths`, early imports and package
initializers under the former partial model; those historical findings are
superseded as preservation requirements, not reclassified as successful tests.

Coverage configuration is not a placement constraint. Under `--cross-module`,
code can move between files measured differently by coverage.py's `source`,
`include` and `omit` settings. One historical round-4 probe moved a block from
an omitted file into a measured module and increased that module's missed
lines from 4 to 5. This remains a possible tooling difference.

External type/build tools are a separate boundary: source constructs such as
TypeVar, Literal and cast, and Babel catalog-extraction calls, retain their
position requirements. Those checks protect declared typing and build
contracts rather than self-inspection by the refactored program.

The [September 27 decision](DECISIONS.md#2026-09-27-supported-body-instrumentation-is-protected-regardless-of-syntax)
records the earlier supported-instrumentation implementation and validation.
The [October 2 decision](DECISIONS.md#2026-10-02-reflection-and-self-instrumentation-are-outside-the-preservation-contract)
removed that exception.

## Conservative rejections

Within its preservation contract, Towel leaves code unchanged when the
required checks cannot establish a valid transformation. Every declined pair is traced under one of the reasons of
`RejectReason` (`src/towel/unification/models.py`), listed here in the order
the pair decision raises them, grouped by stage; a `dry` run that applied
nothing prints how many pairs its last analysis declined for each (a pair
that only repeated another's proposal is not counted), and every run counts
the proposals it built and did not apply, by reason:

- Control flow and class context. `frame_sensitive_block` retains its
  historical diagnostic name for suspension, loop transfers out of the block,
  comprehension assignment expressions and unsupported `super()` context.
  Zero-argument or aliased `super()` requires the original class cell and
  receiver. `frame_read_in_function` remains for an aliased `super()` outside
  the block when moving the block would remove the function's class cell.
  These historical diagnostic names do not imply a reflection scan.
  Reflection, warning attribution, body instrumentation and source readers
  are not refusal categories.
- Bindings crossing the block boundary. `nested_binding_escapes`: a block
  nested inside a loop or branch binds a name the rest of the function
  reads. `closure_crosses_block_boundary`: a nested function or lambda
  outside the block reads a name the block rebinds, or one inside the
  block reads a name the caller rebinds after it. `moves_scope_declaration`:
  a `global`/`nonlocal` declaration in the block names something the
  caller still uses.
- Objects created by moved code. `created_object_escapes`: a callable or
  iterator created in the block may outlive it. A moved closure captures the
  helper's bindings, so later caller rebinding can change the original result
  while leaving the helper's captured value unchanged. Unification can also
  rename lambda parameters and change an escaping callable's keyword
  interface. Both affect ordinary calls, independently of reflection.
  A created callable may be called within the block with accepted arguments,
  serve as `key=` for `sorted`, `min` or `max`, or be the function of `map` or
  `filter` consumed there. Other uses decline conservatively. Classes,
  coroutine functions and decorated definitions can expose closures or defer
  execution and are not analyzed more precisely here. Metadata such as
  `__qualname__`, repr and logging observations is outside the guarantee.
  `thunk_of_possibly_unbound_local`: a lambda the helper call would carry
  reads a local of the calling function that may be unbound there. The
  original raises `UnboundLocalError` at that read; a thunk can only raise
  `NameError`, so an `except UnboundLocalError` would stop matching. The
  same reason declines a thunk that reads a closure variable an inlined
  list, set or dict comprehension of the calling function rebinds: from
  Python 3.12 (PEP 709) that comprehension shares the function's scope, and
  the thunk would raise `NameError`. It declines on 3.11 too, where the
  thunk would work.
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
  `result` bound before it), whatever construct rebinds it: a `for` or
  `with` target, a `match` capture, a nested `def` or `class`, an import, a
  walrus. `i = -1; for i in xs: ...` is declined: with `xs` empty the
  helper's `i` would be unbound where the caller's was `-1`.
  `unbinds_external_name`: the block deletes, explicitly or through
  `except ... as`, a name bound before it or declared `global`/`nonlocal`.
- Shape. `value_producing_mismatch`: one block produces a value (a
  `return`, or variables its caller reads afterwards) and the other does
  not. `return_versus_variables`: the first block's value is its `return`
  and the second's the variables its caller reads after it, and one call
  cannot be both `return helper()` and `x = helper()`.
  `incomplete_return_coverage_block1`/`_block2`: a block that returns
  does not leave by `return`, `raise`, `break` or `continue` on every path
  (block enumeration already leaves such blocks out). `not_structurally_similar`: the blocks'
  per-statement node counts or type histograms differ by more than the
  similarity threshold.
- Unification. `unification_failed`: the blocks do not anti-unify, which
  includes a differing sub-expression that is a slice, a starred item, or
  a whole f-string (container syntax rather than values), a lambda with
  positional-only, keyword-only, or variadic parameters, an expression
  containing an assignment expression, and a substitution that would need
  more than the configured maximum parameters (`--max-parameters`). It
  also includes a difference a tool reads where it stands: a translation
  marker's message, or the name, fields or type a checker reads of a typing
  form. A form is what the block's module binds to typing's object,
  however it is imported (`TV("T")` after `from typing import TypeVar as
  TV`, `t.cast(Alpha, v)`, one re-exported by a module of the project); a
  project's own `cast` is an ordinary call, and a binding Towel cannot
  follow is taken to be the form its name spells. A form a module of the
  project re-exports under a name of its own (`L` for `Literal`) is not
  recognized, nor is one a module outside the project re-exports
  (`hypothesis.internal.compat.TypedDict`), since such an import is taken
  at its word, as it must be for SQLAlchemy's `cast`; where a checker is
  configured it declines what either lets through.
  `return_variables_not_aligned`: a variable one block must return has no
  binding in the other. `mixed_return_and_variables`: a block both returns
  early and binds variables read afterwards, which one call statement
  cannot render.
- Free variables and lifetimes. `owned_binding_exception_boundary`: a moved
  owned binding crosses an outside handler or manager before ownership can
  return. `owned_binding_frame_boundary`: caller and helper would retain
  potentially finalizable values in different frames without a certified
  whole-body argument transfer. `conditionally_bound_return`: a returned
  variable is not definitely bound at the block's exit and did not enter
  as a parameter. `incomplete_lifetime_block1`/`_block2`: the block reads a
  name that is bound only after it, or reads a local before its own binding
  of it (`scale = scale(n)`, which raises `UnboundLocalError`) where the call
  site may not have the name bound: with the block gone the name may not be
  local to the caller, and the argument would find a module name or raise
  `NameError`. `scope_declarations_differ`: one site's function declares
  `global` or `nonlocal` a name the blocks spell and the other's does not,
  so no one set of declarations in the helper serves both.
  `module_data_lookup`: the helper would receive module data
  (a module-level assignment) as an argument, snapshotting it. `rebound_external_binding`: the helper would receive a
  name another function rebinds through `global` or `nonlocal`. The names both sites resolve
  at module scope are read bare by a same-module helper and are exempt
  from both, so these two decline cross-file pairs and pairs where only
  one site resolves the name at module scope.
- Rendering. `impure_eager_parameter`: an argument that would be passed
  eagerly is not a literal, a resolvable name, or a tuple of those.
  `trivial_return_blocks`: the helper would compute nothing. Its one
  statement returns nothing, or returns or evaluates only names, literals,
  tuples of them and the thunks its sites pass, so each site would hand its
  own expression to a helper that hands it back: two unrelated `return`
  statements, such as rich's `Tag.markup` and `MofNCompleteColumn.render`,
  unify that way, and so do two `return name` blocks. Declined whatever
  `skip_trivial_helpers` says. `trivial_forwarding_helper`: the helper
  body would be a single forwarding statement (a lone `raise`, a `return`
  of one call, or a bare call), a forwarding statement whose result is
  bound and returned (`x = f(...)` then `return x`, or the tuple form), or
  a body that only binds parameters and literals to names and returns
  them; such a helper shares no logic, only a name, and is skipped by
  default (`skip_trivial_helpers=False` keeps it). One whose only
  computation is calling helpers Towel generated is declined under this
  reason whatever the setting.
- Orphans. `orphaned_variables`: a name the block binds is read afterwards
  on a path that does not rebind it first, and the helper does not return
  it; an augmented assignment and a `del` read the name as a load does. A
  read after only a *conditional* rebinding is treated as orphaned even where
  the helper would return it (the annotated-assignment fixture
  r86 is rejected for this reason); returning such names was found unsafe
  in three fixtures, and a path-aware return analysis would recover the
  case. A match capture, `with` target, or exception name that would have
  to cross the block boundary in a way the return analysis does not
  represent is declined here or under the alignment reason above.
- Call sites. `super_in_call`: the call would evaluate zero-argument
  `super()` itself, in a thunk or by passing `super` for the helper to call,
  where no frame reads the method's receiver and class cell.
  `forwarded_callee`: a differing expression in call position
  would be passed as `lambda *args, **kwargs: callee(*args, **kwargs)`,
  which reads worse than the duplication it removes. `moves_only_binding`:
  the block holds its function's only binding of a name the function reads
  elsewhere, before or after the block or from a nested scope, and the call
  would not bind it again, so the name would stop being local to the
  function. `builtin_argument`: the
  call would hand the helper a builtin, directly, through a lambda, or in a
  literal container, or one site's function binds a builtin's name that the
  other reads as the builtin (*Module names stay module names*).
  `undefined_names_in_call`:
  the generated call names something the site cannot resolve (a leaked
  placeholder, a name bound only inside the block).
  `instantiation_mismatch`: the helper applied to the call's arguments does
  not reproduce the block up to renamed binders.
  `unsupported_extraction`: the extractor cannot write the helper or one
  of its calls (a generated parameter name the block already uses, an
  annotated assignment whose target would become an argument, a parameter
  with no argument at a site); the trace's detail says which. A further
  occurrence that cannot join a pair's helper for this reason is traced as
  `DECLINE-SITE[unsupported_extraction]`, and the pair keeps its helper.
- Tool directives in the moved code. The comments of a block move into the
  helper with its code, and a directive (`# type: ignore`, `# pyright:
  ignore`, `# ty: ignore`, `# pyrefly: ignore`, `# zuban: ignore`,
  `# pyre-ignore` and `# pyre-fixme`, `# noqa`, `# ruff: ...`, `# pragma: no
  cover`, `# nosec`, `# nosemgrep`, `# pylint: ...`, Fixit's
  `# lint-ignore`, `# fmt: ...`, `# isort: ...`, a type comment) changes
  what a tool reports for its line, of which the helper has one where the
  sites had several.
  `directives_differ`: the blocks do not carry the same directives, written
  alike up to spacing, at the same places, as when only one copy of a line
  needed its `# type: ignore` (mashumaro's `type_name`): the helper's line
  would be silenced for every site or for none. `directive_on_argument`: a
  directive reaches code of a block's own, anything but a name or a
  literal, that would become an argument of the call and so be written at
  the call site, where the directive does not reach: a `# type: ignore` or
  `# noqa` on its line, a `# nosec`, `# fmt: skip` or line-level
  `# pylint: disable`, a `# pragma: no cover` on the statement or the
  clause it excludes, the statement after a `# noinspection`, the next
  line of code after an ignore on a line of its own (ty and ruff read it
  as the next logical line, or inside brackets the next physical one;
  pyre, pyrefly, Semgrep and Fixit as the next line, and pyrefly reads
  every checker's `<tool>: ignore` that way), or a `# fmt: off` or
  `# ruff: disable` region. The directive is not copied onto the call line
  either, which would silence or exclude a line its tool never saw it on.
  Measured on September 24, 2026, extending the rule from a checker's
  ignore to every directive cost no refactoring: the `--no-types` fixed
  points of click, rich, packaging, pygments, asyncstdlib, mashumaro,
  python-statemachine, tinydb, fastjsonschema, pint, autopep8, docutils,
  jinja2, markdown, pyparsing, sqlparse and tornado were byte-identical, and
  a first analysis of those and of attrs, boltons, coverage.py and its
  tests, more-itertools, pytest and its tests, requests, urllib3 and
  werkzeug declined no pair for it that the checker's rule did not (one,
  in jinja2). A directive for the whole file reaches its module wherever
  the code is written and is not counted here. The two reasons below cost
  five refactorings in those fixed points: four in pygments' builtins
  scripts, whose `__main__` block no test runs (a module helper took
  `_lua_builtins.py` from 100% to 40% line coverage), and one in
  markdown's legacy inline patterns, both of whose sites are excluded. The
  first analysis of the other nine declined two pairs for them: in
  coverage.py's tests a block opening with its own `# pragma: nested`, and
  in more-itertools two version-specific implementations each excluded on
  its `def` line, where one of them runs on any given Python and the
  helper might well have been covered; Towel cannot tell, and declines.
  `directive_outlives_block`:
  a region directive on a line of its own is not closed within the block,
  so its region reaches code that stays behind; or a match of coverage.py's
  exclusion regexes spans the block's edge, so the code outside it would
  stop being excluded; or a file-wide directive (`flake8: noqa`, `ruff:
  noqa`, `ruff: file-ignore`, `mypy:`, `pyright: strict`, `pyrefly:
  ignore-errors`, `pyre-strict`) would move into another module. The
  region directives are those of every tool the comment table knows that
  has one: Black's and ruff format's `fmt: off`/`on` (and `yapf:
  disable`/`enable`, which both read the same way), yapf's, autopep8's
  `autopep8: off`/`on`, isort's `isort: off`/`on`, pylint's and pytype's
  `disable`/`enable`, and ruff 0.16's range suppression `ruff:
  disable[...]`/`enable[...]`; flake8, mypy, pyright, ty, pyrefly, pyre,
  Bandit, Semgrep, Fixit, pycln and codespell have none. A closer counts
  as its tool reads it, from its source or its measured behaviour (Black
  26.5.1, ruff 0.16.9, yapf 0.43.0, autopep8 2.3.2, isort 9.0.1). An
  opener is what any of them reads as one: yapf reads `fmt: off` anywhere
  in a comment line, prose included, and autopep8 anywhere in the text, so
  a comment explaining `# fmt: off` opens their regions. A closer counts
  only where every reader of that family accepts it: Black's at the
  opener's level and spelled `# fmt: on`, `# fmt:on` or `# yapf: enable`
  (`# FMT: ON` closes nothing); ruff's with the same codes in the same
  order; pylint's and pytype's `enable` naming every message the `disable`
  did; isort's the whole line `# isort: on`. pytype's reading was not
  verified, so its region is taken to reach as far as either a
  line-by-line or a block-scoped reading would carry it. A `fmt: off`
  written inside a string, which autopep8 also reads, is not seen.
  `directive_around_block`: an ignore on a line of its own above a site's
  block governs the block's first statement, and would stay above the call
  that takes its place, silencing the call and not the helper (pylint's
  `disable-next` and isort's `# isort: list` and the like included); or
  a directive outside a site's block reaches it and would not reach the
  helper: `# pragma: no cover` or `# pylint: disable` at the end of the
  header of a statement enclosing the block (its function's `def` line, a
  class, an `if`, loop, `else:` or `except` line), a region directive
  opened before the block and not closed before it (pylint's in an earlier
  clause of the same statement too), or a match of coverage.py's regexes
  that begins before the block and excludes its lines. A helper is still
  reached where the region covers it: a module helper when the region
  opens before the module's first definition and is not closed before its
  last statement; a method helper when it opens before the method holding
  the block (before the class's first statement, for a formatter's) and is
  not closed before the class's end; a nested helper when it opens before
  its function's first statement; and one inside the class or function
  whose header carries the directive. A formatter's region declines at any
  site, since that site's layout would be formatted in the helper. For a
  coverage exclusion or a linter's directive, while one site is measured
  or linted, the helper is that site's code and its tool reports nothing
  new, so every site must be reached for the pair to decline. pygments' builtins
  scripts define functions under `if __name__ == '__main__':  # pragma: no
  cover`; a module helper for two of them took `_lua_builtins.py` from 100%
  to 40% line coverage, and a loop body excluded by its header's pragma
  failed a `--fail-under=100` gate at 70%. `excluded_block_start`:
  the block's first statement is a simple statement coverage excludes (the
  `log(...)` before a `raise`, both marked `# pragma: no cover`); the call
  that replaces the block runs exactly when that statement did, so it
  would be measured and never run. A block opening with an excluded clause
  (`if error:  # pragma: no cover`) still moves: its header runs whenever
  it is reached, and so does the call. `directive_on_shared_line`: the
  block starts after, or ends before, a statement on the same line that
  stays at the call site (`a = 1; b = 2  # noqa: E702` with the block at
  `b`), and that line carries a directive or coverage excludes it. The
  directive governs the whole line: moved into the helper it would leave
  `a = 1` unsilenced, and left on the call's line it would also reach the
  call while the helper took a copy. A plain comment there moves as any
  other. `layout_not_kept`: a formatter directive keeps lines of a block
  as written (a formatter's region inside it, a `# fmt: skip`, ruff's
  trailing `# fmt: off` or yapf's trailing `# yapf: disable`, or a
  formatter's region around the blocks that still covers the helper, which
  keeps the whole block), and the helper cannot hold them byte for byte,
  apart from a uniform re-indentation. Such lines are written into the
  helper from the first site's own text, in place of their rendering, and a
  formatting of the helper that changes them is not used. The pair is
  declined when the sites keep different statements or write them
  differently, when a parameter would stand in them, or when they cannot be
  re-indented unchanged: a string running across their lines, a tab in
  their indentation, a line indented less than their first, a statement
  sharing their first or last line, a `# fmt: skip` on a clause's header,
  or, in a method helper, a line indented other than by whole levels of
  four spaces, which the class's indentation would change. A further site whose directives
  differ from the pair's is left out of the cluster rather than declining it.
  What coverage.py excludes is read from the project's own configuration,
  as coverage.py reads it (`src/towel/coverage_config.py`, following
  coverage.py 7.16.0): the first of `COVERAGE_RCFILE` or `.coveragerc`,
  `.coveragerc.toml`, `setup.cfg`, `tox.ini` and `pyproject.toml` that it
  would use, at the project's root, with its defaults (the `# pragma: no
  cover` comment, a `...` stub line, `if TYPE_CHECKING:`) unless
  `exclude_lines` replaces them and whatever `exclude_also` adds. A line
  any of those regexes matches (`if __name__ == .__main__.:`, `raise
  NotImplementedError`, `@overload`, `@abstractmethod`) counts exactly as a
  line with the pragma does, for its statement and the clause or decorated
  definition it opens; where `exclude_lines` leaves the pragma out, the
  comment excludes nothing, and is carried as a plain comment. A relative
  `COVERAGE_RCFILE` is taken from the project's root, where coverage.py is
  taken to run, and the root is the nearest directory with a
  `pyproject.toml`, `setup.cfg` or `setup.py`, so a `.coveragerc` elsewhere
  is not seen. A configuration coverage.py could not read (it does not
  parse, holds a regex that does not compile or a value of the wrong type,
  or `COVERAGE_RCFILE` names no file) is reported once and replaced by the
  defaults. Exclusions a coverage plugin makes are not seen. coverage.py
  matches its regexes against the raw text, strings included, and so does
  Towel: pytest excludes `assert False` and `@pytest.mark.xfail` lines, its
  tests write such lines into the files they create with
  `pytester.makepyfile("""...""")`, and coverage.py excludes each whole
  statement that holds one; so a block starting with one is declined,
  though that statement runs. Measured on September 24, 2026 on the
  `--no-types` fixed points of pygments, markdown, coverage.py and its
  tests, pytest and its tests, attrs, more-itertools, mashumaro,
  python-statemachine, jinja2, sqlparse, werkzeug, packaging, h2, click and
  rich, run in their own projects, reading the configuration cost 29 of
  pytest's 697 test refactorings and 3 of coverage.py's 295, and changed
  nothing else. In a first analysis of pytest's tests, 915 of the pairs it
  declined were for such text in a fixture's string and 46 for a real
  `@pytest.mark.xfail` decorator on the tests holding the blocks; all 37 in
  coverage.py's tests were for text in strings.
- Placement. `needs_class_body`: the blocks use zero-argument `super()` and
  the helper cannot be a method of the class holding both, reached through
  the same receiver (*Method insertion*); nothing else binds `super()` alike.
  `nonlocal_safety_skip`: a block reads, writes or declares a name its
  function makes `nonlocal`. An unrelated declaration does not block the
  extraction. `private_name_lexical_class`: the moving block uses a
  `__private` name and the helper would live in another class, which
  changes name mangling. `cross_module_global_declaration`: a cross-file
  helper's participating modules include one whose functions declare
  `global`. `unproven_import`: no participating module can be imported by
  every other one in a way the program's own imports show to work (*A
  cross-file helper adds an import of its host module*). `import_cycle`: every candidate
  host closes a static import cycle. `import_time_effects`: a cross-file
  helper's host module, which the borrower's import does not already
  load, would run code at import (*Import-time behavior*: a module that
  prints, registers or connects at import time); `new_import_requirement`: it would require a package outside the project,
  the standard library every supported platform and Python has, and the
  declared dependencies no marker limits; `conditionally_imported_host`: the
  program imports the host, or a package it is in, only under a condition;
  `other_distribution`: the borrower belongs to a distribution the host is
  not in; `new_top_level_package`: it would load a
  top-level package of the project the borrower's import does not;
  `run_by_path_import`: the borrower runs as a program, and run by its path
  it could not resolve the import; `host_has_stub`: a type checker would
  read the host's stub, which lacks the helper, in its place. In each
  case a module-level helper may move to a participating module that hosts
  it without the change, and the pair is declined only when none does.
  `relative_import_across_packages`: the block runs a relative import that
  some participating module resolves in another package.
  `bare_name_differs_by_module`: after the pair was decided again with them
  as parameters, the helper still reads bare a name that a site in another
  module could resolve differently. `builtin_may_differ_by_module`: the
  helper would read bare a builtin in a different module's namespace (*Module names stay module names*).
- The proposal. `duplicate_proposal`: the helper, home and sites repeat an
  earlier pair's, found through another pair of the same family.
  `existing_helper_becomes_forwarder`: a site is the whole body of a helper
  this run inserted, which would keep only a new forwarding call. A proved
  whole-body identity call may instead reuse that helper. Equivalent module
  helpers with distinct generated names can share one definition through an
  alias when their plain signatures and bodies match, their annotations are
  literal strings or `None`, and the alias's earlier import resolves to the
  exact proved helper. Evaluated annotations such as `n: int` retain separate
  definitions under this conservative rule.
  Same-module consolidation and general consumer redirection are not attempted.
  A helper-shaped function already in the input retains its independent
  binding: its body may share a fresh helper, but its name is never reused or
  aliased merely because of its spelling.

Other behaviors that leave a duplicate in place are not rejections of a
formed pair:

- A literal that a tool reads without running the program is never a
  parameter, so blocks that differ only in one are not duplicates. That
  covers the message and context arguments of translation markers (Babel's,
  Django's and Flask-Babel's keywords, and keywords a project configures for
  extraction), which message extraction reads from the source, and the
  names, fields and types of the functional typing forms (`TypeVar`,
  `NewType`, `NamedTuple`, `TypedDict`, `Enum`, ...), `Literal[...]`, and a
  type spelled as a string in `cast` or `assert_type`, which no annotation
  lets a checker accept as a parameter.
- In a `match` pattern only a class pattern's class, or the root of a dotted
  value, may become a parameter; a differing literal or capture in a pattern
  makes the blocks different code.
- An integer literal wider than 640 decimal digits declines its block,
  because generated code would spell it in decimal.
- A block whose only computation is a call of a helper this run generated,
  unpacking and repacking what it returns, is never extracted: it shares no
  code the user wrote. With it excluded, every extraction moves some of the
  user's code into a helper, which is what makes the fixed point end.
- A duplicate that is the whole body of an existing function is extracted
  like any other, and the function becomes a call of the new helper; it is
  never rewritten to call another existing function that restates it. Such
  a call looks the other function up in its module every time, so
  `mock.patch("mod.f1")`, or any other rebinding of `mod.f1`, changed `f2`
  as well (audit `r06`). The `reuse_existing_functions` setting is
  deprecated and changes nothing. Compatible plain module helpers generated
  earlier in this staged run can be reused with their original signature and
  safe host. Equivalent generated helpers from independent batches can become
  aliases when plain positional signatures, bodies, inert annotations and
  import/provenance checks agree. User-input functions retain independent
  bindings. General consumer redirection, same-name collisions and same-module
  consolidation remain deferred.
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
- No packaging configuration is read to name a module: setuptools, Hatch,
  Flit, Poetry and pdm projects are named alike, from the imports their
  modules and tests already make. A module no import reaches has no absolute
  name, and is reached only by the relative imports of its own package.

Set `DEBUG_PROPOSAL_REJECTIONS=1` to log the reason for each rejected pair
(the `towel.rejections` logger, at DEBUG, on stderr), one line per pair
naming each block by file, function and lines:
`REJECT[reason]: path::function@(start, end) <-> path::function@(start, end)`.
Each pair is traced once and in pair order, whatever `TOWEL_WORKERS` is, so
counting the lines by reason prices a decline the same with any worker count.

## A program Towel cannot read whole

The checks that decline unsafe changes read the whole program, from the project
root down (`src/towel/program_files.py`). A file there that does not parse on the
Python Towel runs on refuses every run before anything is written, because it may
run on a newer Python and do there what those checks never saw (round-4 audit
P1-2 and P1-3). Towel cannot tell a file in newer syntax from one that is invalid
on every Python, so both refuse. A file that does not decode in its declared
encoding runs on no Python, and is left alone. `--exclude` still means "leave
this unchanged": what it names is read as evidence like the rest, and only a file
it names that does not parse is taken for no part of the program. The refusal
suggests an `--exclude` for each file that clears it, the directory's name when
two or more of the files share it, else the file's own; only a file given
explicitly as the target has none, since it is always analyzed. Of the 140 corpus
projects, 8 hold a file that does not parse on 3.12 and 3.13, all cleared that
way (black's `tests/data`, parso's `normalizer_issue_files`, unidecode's
root-level Python 2 `benchmark.py`). On 3.11 sphinx and cattrs join them, and
django holds five more such files, all written for 3.12, which sphinx and django
require. What remains:

- A name matches at any depth, so `--exclude tests.py` leaves every file of that
  name unchanged, not only the one that does not parse.
- The newest Python the refusal names is read from `requires-python` (or
  setup.cfg's `python_requires`, or Poetry's `python`) and the `Programming
  Language :: Python :: 3.N` classifiers; a `setup.py` is not read.
- Past the scans' limit of 20,000 Python files the program is not read whole,
  and the scans say so themselves.

## Performance

Analysis is quadratic in candidate blocks per file. The measures below keep
it tractable, all exact: they change no proposal.

- Blocks that can never be accepted are not enumerated: one that returns on
  some path but not every path, or a lone expression statement. On pyflakes'
  2,167-line `test_other.py` that removes 32,857 of 44,826 rejected pairs.
- Pure structural unification results are stored as positions and rehydrated
  onto matching blocks. Site-sensitive safety checks also key or validate the
  relevant module, source and scope facts; equal AST structure alone cannot
  transfer a permission to another binding environment. Other immutable facts
  use AST-lifetime memos. See [the cache boundaries](ARCHITECTURE.md)
  for the different lifetimes and invalidation rules.
- The clustering pass scans a file for the sites that can share a helper
  once per distinct helper template, not once per pair (every pair of N
  near-identical blocks renders the same template; 50 identical functions
  took 17 s and 100 took 134 s under 1.618, before this pass and the reuse
  index below; at `8cb8b8c` on September 24, 2026, the module and command
  of `scripts/bench_similar_blocks.py` on one core of an Apple M5 Max, with
  other work loading the machine to a load average of 15 to 19, take 6.4 to
  7.9 s for 50 and 27 to 29 s for 100, in CPU time as in wall time, a
  factor of about 4 for twice the functions; at `5ff2458`, with one other
  single-core job running and before 1.772's per-call-site safety checks,
  they took 4.0 s and 15.9 s). Constant-time filters precede semantic
  guards. The former per-candidate pipeline memo was removed because template
  bindings and helper state can differ; semantic guards use the current site. The
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
  --outputjson`, which remains the fallback. The first checker to report a new
  error settles a candidate, so a project that configures both pays for both
  only when the first reports none.
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
  run reached a fixed point in 46 minutes on September 21, before the audit
  rounds, applying 380 refactorings across 105 files, where the same project
  without types changed 108 files in 11 minutes. Sphinx's own test suite, run
  serially, reported the same 2385 passed, 34 skipped and six pre-existing
  failures before and after.
  The Sphinx-only rerun at `29454ab` took 5,660 s (about 94 minutes) and changed
  55 files. It ran alone in a Docker Linux VM with 18 cores and 8 GB of memory,
  using Python 3.12, `TOWEL_WORKERS=1`, `--cross-module`, and default typing
  (mypy and Pyright both strict). These are measured conditions, not minimum
  resource requirements or time guarantees. Its suite was again unchanged;
  the audit rounds' checks decline more, and typed verification is nearly all
  of the time.
  The tail was the cost: a proposal the project rejected used to be heard
  again at every whole-project analysis, and families of near-identical
  methods pair many ways. A declined proposal is now remembered for the whole
  run and heard once more only at the rehearing that ends it. A bounded `--max-refactorings` run is the practical form there.
  Nothing about the result depends on any of this: the checkers are consulted
  identically.
- What a checker still rejected on Sphinx, when last measured (`2057bf6`,
  September 21, 2026), was one thing and one family. The family is a
  generic helper whose inferred type parameter wants a bound, which does
  not lose the refactoring. The thing was a helper lifted into a base class
  whose body read a member only its subclasses have; it no longer arises,
  since from `da05485` a helper goes only into the class that holds both
  duplicates, and a block that sibling classes share becomes a module
  function whose receiver is annotated from its call sites.
- Because the variants are generated lazily, a signature that verifies costs
  nothing further. A precise ordinary signature that passes means no generic
  candidate is ever built or checked, which is the cheapest order as well as
  the documented one.
- The forwarder check finds a function whose body starts where a site does
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
of package timings; the [performance guide](PERFORMANCE.md) refers here rather than repeating figures.

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
11.9 s with them. At `8cb8b8c` (September 24, 2026, the same machine and
settings, other work loading it to a load average of 11 to 13) the source
is 46,165 lines (`wc -l` over the 80 modules of `src/towel`), and the same
command applies 22 refactorings in 35 s without the type checker and
formatter, and 21 in 138 s with them, mypy being the one checker the
project configures.

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

Python 3.11 to 3.13 on a POSIX system (macOS or Linux). Applying changes needs
POSIX filesystem semantics. Parallel analysis uses the `fork` start method;
under other start methods analysis runs on a single core. Windows is not a
supported or validated release platform.

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
