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
  block it replaces up to the renaming of names the block itself binds, and
  a name may be renamed only where the running program cannot see its
  spelling: `UnboundLocalError` and `NameError` name a variable read,
  augmented or deleted while unbound, so a renamed binder that some read may
  find unbound (after a `del`, an empty loop, an unmatched case, the end of
  its `except ... as` clause, or in a closure), or that a `global` or
  `nonlocal` declaration names, declines the call site. A
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
  rebind a name a closure outside the block reads; and may not define a
  closure over a name the caller rebinds after the block. Names bound in the
  block and read afterwards are returned, where `count += 1` and `del count`
  read `count` as a load does, including targets of annotated assignments
  and assignment expressions, as is a name bound to a class instantiation or
  to a known resource factory (`open`, `connect`,
  `socket`, `mkdtemp`, `Popen`, `urlopen`, ...), whose lifetime a later
  statement could observe; a factory outside that list is not detected. A
  returned name must be definitely bound where the block ends or have
  entered as a parameter.
- **Frame and control flow.** Blocks containing `yield`, `await`, `async`
  loops, context managers or comprehensions, `locals()`, `globals()`,
  `eval`, `exec`, zero-argument `vars()` or `dir()`, `super()` reached
  through another name (`s = super; s()`, `builtins.super()`),
  `break`/`continue` targeting an outer loop, comprehension assignment
  expressions, `warnings.warn` in any spelling with or without
  `stacklevel`, any call with a `stacklevel=` keyword, or direct frame or
  stack inspection are rejected; aliases of these bound by import or
  assignment are resolved. A block is also rejected when its enclosing
  function reads its own frame (`locals()`, `dir()`, `eval`,
  `sys._getframe()`, ...) anywhere outside the block. Zero-argument
  `super()` itself moves only into a method helper of the class that holds
  the block (see *Method insertion*).
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
  bind definitely, and a name a statement may delete (`del`, or the end of
  an `except ... as` clause) is unbound for whatever may follow it: the rest
  of its list, the next iteration of a loop that holds it, and the handlers,
  `else` and `finally` of a `try` that holds it.
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
  A same-file pair's helper, a method or a function, always lives in the
  pair's own module, so these names are always read there (oauthlib's
  `BearerToken`, fixture `xf15`, broke when a helper was hoisted into a base
  class defined in another module). By default a builtin is never a
  parameter, since a call such as `helper(rows, len)` would surprise every
  reader, so a helper that a site in another module calls, which only a
  `--cross-module` run writes, reads its builtins bare in its host's
  namespace. That is the lookup each site made only while no
  participating module holds the name, and the pair is declined
  (`builtin_may_differ_by_module`) wherever the program shows one may: a
  statement of the module's own scope binds the name, a function of it
  declares it `global`, a star import of it may bind it (a project module's
  literal `__all__` or else its public top-level names say what; a module
  outside the project may bind anything), it rebinds `__builtins__`, it
  writes its own namespace at run time (`globals()[...] = ...`, `vars()` or
  `locals()` so used at its top level, `globals().update(...)`, `globals()`
  handed to other code, `setattr(sys.modules[__name__], ...)`, `exec`,
  `eval`), or the project's own code or tests patch the name into it:
  `mock.patch("pkg.mod.len")` in any spelling, with or without
  `create=True`, `patch.object`, `patch.multiple`, `patch.dict` of its
  `__dict__`, pytest's `monkeypatch.setattr` in either form or
  `monkeypatch.setitem` of its `__dict__`, `setattr(mod, "len", ...)`, or
  `mod.len = ...` (`tests/test_builtins_across_modules.py`, fixtures
  `xf17`-`xf19` and `xf22`; `xf21` shares what the other two modules can).
  A module is matched by every dotted name its path gives it below the
  project root, which include the names the program's imports use, and by
  the file a relative import names. A target is read through literals,
  f-strings, `+` and names bound once to a string, and one whose module part
  is computed at run time (`"pkg." + name + ".len"`) counts for every
  module. The names checked are the ones CPython's symbol table says the
  rendered helper reads from its module, so reads inside its lambdas and
  comprehensions count. Not seen: code outside the project that patches a
  builtin into one of its modules (with `create=True`, or through `mock`,
  which creates a builtin's name without being asked); a target computed
  whole, as by a wrapper that passes its argument to `patch`; and a module
  object reached other than by an import, `importlib.import_module`,
  `getattr` with a spelled name or `sys.modules`, such as a fixture's
  return value. A cross-module helper then reads that builtin in its host's
  namespace, and the patch reaches only the code the host itself runs. A
  module `__getattr__` changes no bare lookup and is not consulted. In any
  pair, same-module or not, no generated call hands its helper a builtin:
  an argument, or what a lambda argument returns, that is a name its site
  reads from the builtins, bare or in a literal tuple, list, set or dict,
  declines the pair (`builtin_argument`), and a clustered block whose call
  would hand one over keeps its code. So a pair is declined where one
  site's function binds a builtin's name and the other reads the builtin,
  and where the blocks differ in a builtin that Towel's own list of
  builtins leaves out (`lambda: __import__`, `lambda: __debug__`) or in a
  container of builtins against a plain name (`(int, str)`). Where both
  functions bind the name, each passes its own local. A lambda that calls a
  builtin (`lambda: len(rows)`) passes what the builtin computed, and an
  attribute of one (`str.upper`) is not the builtin
  (`tests/test_no_builtin_arguments.py`). With `--parameterize-builtins`
  (`parameterize_builtins=True`), each of those declines that concerns a
  builtin the sites may disagree about, one site's function binding the
  name or a module that may hold it, passes the builtin as an ordinary
  parameter instead, each site giving its own: eagerly, or as a thunk where
  the site may not have bound it, as for any free variable. The rules for a
  name rebound between the call and the read still apply, so a module whose
  function declares the name `global` or writes its namespace at run time
  (`rebound_external_binding`), or that assigns it at its top level
  (`module_data_lookup`), is declined as before. A builtin every site reads
  alike stays bare, and blocks that differ in which builtin they use
  (`lambda: __import__`, `(int, str)`) are declined all the same. Such
  a parameter is annotated from what the checker reveals of the builtin,
  loosened to what the helper's body needs where the host could not spell
  the signature: a class every constructor of which makes it is
  `type[str]`, a callable whose overloads all return one type or whose
  parameters name typeshed's protocols is `Callable[..., int]`, a signature
  over builtins alone stays exact (`Callable[[object], str]`). A builtin
  whose overloads return different types (`open`, `sorted`, `min`) or a
  generic one (`abs`) has no such annotation and gets `Any`, which a strict
  checker may then refuse where the helper returns what it computes
  (`tests/test_parameterize_builtins.py`).
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
- **Annotations.** Every generated helper and its call sites are checked
  together in the prospective project, including unchanged consumers, and the
  check is compared with the project's own as it stood: an error the project
  already had is left as it is, and a change passes only when its check
  reports nothing the project's did not (see *Type annotations on helpers*
  below). A change that introduces a type error has its annotations replaced
  by `Any`, and then removed. Every variant must pass; checker failure or a
  new error declines the change. A checker that cannot run at all refuses the
  typed run, and `--no-types` is the explicit way on. It never silently
  disables verification. A helper is annotated only in code that
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
  suspension, `locals()`/`globals()`/`vars()`/`dir()` with no arguments, a
  `super()` reached through another name,
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
  usual descriptor protocol. Only a method whose decorators are all known to
  leave its body alone is refactored at all (*Decorators that compile or
  instrument a body*), in a class whose machinery passes the test below; one
  decorated with any of them but the recognized
  receiver-preserving decorators receives a module-level helper with the
  receiver passed explicitly. A class decorator is trusted to leave a
  helper in place only when it is one of `dataclasses.dataclass`,
  `functools.total_ordering`, `typing.final`, `typing_extensions.final` and
  `enum.unique`, reached through the module's own absolute imports; a class
  carrying any other decorator takes no helper. A metaclass, and every
  `__init_subclass__` on the class's method resolution order, sees the
  namespace a method helper joins and may wrap, register or drop it, so the
  class holding both duplicates takes one only when all of that is known to
  leave a plain function alone: its metaclass is `type`, `abc.ABCMeta` or the
  enum metaclass, it defines no `__init_subclass__` itself, and each base is
  a builtin class, `abc.ABC`, `typing.Generic[...]`, an enum, one of the
  library classes read to build a subclass with Python's own machinery
  (*Decorators that compile or instrument a body* below), or a class of the
  project that qualifies in turn, resolved through the module's imports.
  Any other class (pygments' lexers, whose metaclass is the project's own; a
  base reached through a star import or built by a call such as
  `with_metaclass(...)`; `NamedTuple`; a library base class nobody read)
  keeps its code: moving it out is refused as well. `__slots__`
  interactions with added methods are not modeled beyond compilation.
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
  `TYPE_CHECKING` resolved through the imports runs, however it branches,
  and a condition comparing `sys.version_info`, `sys.platform` or `os.name`
  with constants runs nothing. When a name the
  annotations need is defined only after such code, the helper goes before
  it anyway where annotations are postponed (`from __future__ import
  annotations`), and is declined elsewhere. Cross-file helpers add a module import; a helper
  import goes after the module's last leading import (after the docstring
  when there are none), so a script that runs a statement before its
  imports keeps it first. Static local import cycles are rejected
  (including cycles through a package's `__init__`, which `from . import
  name` runs), dynamic ones are not detected. An import under a
  `TYPE_CHECKING` guard, resolved as above, never runs and closes no cycle;
  one in the guard's `else`, under `if not TYPE_CHECKING:`, or under a name
  a scope binds for itself does. A program that sets `typing.TYPE_CHECKING`
  true before importing is outside this model.
- **Concurrency of application.** A run refactors a private copy of the
  project and writes back only when it has succeeded, as one batch; a file
  edited during the run refuses the batch, nothing written. Files are replaced
  atomically one at a time; a batch is not atomic across files to a concurrent
  reader. Application requires exclusive write access; a concurrent editor
  writing in the check/replace interval is not prevented. Interrupted batches
  leave a recovery journal.

### A cross-file helper adds an import of its host module

Sharing a helper across modules is opt-in (`--cross-module`,
`cross_module_helpers`). By default only duplicates within one module are
paired, pairs across modules are neither formed nor counted against the pair
budget, and no import between the project's modules that runs is ever
written. The one import of a project module the default writes is the
type-only one a helper's annotation needs, under `if TYPE_CHECKING:`, which
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
could not import the helper hosted there. The declarations read, only to put
a host in doubt and never to name a module, are hatch's `exclude`,
`include`, `only-include` and `packages`; setuptools' `packages.find`
`include` and `exclude` and an explicit `packages` list, in pyproject.toml
or setup.cfg; MANIFEST.in's `exclude`, `recursive-exclude`, `global-exclude`
and `prune`; Poetry's `exclude`, PDM's `excludes`, uv's `source-exclude` and
`wheel-exclude`, flit's sdist `exclude` and scikit-build-core's excludes; and
every `.gitignore` above a module. Each is read to leave out at least what
the backend would, and an include that could put a file back is not read.
What a setup.py, a build hook or a backend not listed leaves out is not
known, and a module it leaves out can still host a helper. A candidate host
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
round-4 audit's P1-4). Only an import that attests does
any of this: one inside `try`/`except ImportError`, under `TYPE_CHECKING`,
or in a file that changes `sys.path`, as graphene's setup.py imports
`pyutils.version` after appending the package to `sys.path`, neither
locates a name nor puts one in doubt. Before it writes anything, a
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
`if TYPE_CHECKING:` never runs and leaves its file free. The cost is
that file's own duplicates, and, when it is a package's `__init__.py`, every
helper a module outside that package would borrow from a module inside it:
chardet's `detect` and `detect_all` share a block in its `__init__.py`,
which a fresh clone lacking `_version.py` leaves as it is. Only a
`--cross-module` run consults the model; a run without it refactors such a
file as any other, and writes no import between modules that runs.

The host is chosen so that no import cycle closes, preferring a module the
borrowers already import; when none qualifies, one borrower gains a new
import edge. Every import on the way is resolved as the program's import
model resolves it, an ambiguous name to every place it could be. Towel does not know whether importing that module has
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
nothing; most of Pygments' lexer modules are such, which costs Pygments 12
of its 23 cross-module helpers. Towel also refuses a host
whose import would require a module the borrower does not already
import: an unconditional import, including one inside a module-level `if`,
of anything outside the project, the standard library and the project's
declared dependencies: PEP 621's `[project].dependencies`, Poetry's
`[tool.poetry.dependencies]` less the optional ones an extra installs, and
setup.cfg's `install_requires`; a setup.py is not run, so dependencies it
alone declares are not known. An import inside `try` is taken as an
optional dependency and requires nothing. A dependency declared under a
distribution name that differs from its import name (`PyYAML` for `yaml`) is
not recognized, which refuses a host rather than accepting one; an import
made by `importlib` or `__import__` is not seen at all.

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
borrower "already loads" counts only the imports its top level certainly
runs: an import in a function body, a branch or a `try` makes nothing
present, and a borrower whose function imports the host lazily no longer
counts as running the host's import-time code already.

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
- Such a block is declined when the function holding it uses a
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
`self.__extracted_func_0()`. No subclass, in the project or outside it, can
override it or collide with it, since a subclass's own `__extracted_func_0`
is stored under the subclass's name. Mangling goes by name, not by class, so
a subclass named like its base (`class A(base.A)`) that defines
`__extracted_func_0` stores it as `_A__extracted_func_0` too: Python sources
under the project root are read for such a member, for `_A__extracted_func_0`
spelled out, and for attribute stores, `setattr` and namespace writes of the
stored name, before a number is chosen, and one outside that root is not
seen. A class named only with underscores (`class __`) mangles nothing, and
gets the module-level helper. A module-level helper's name must likewise be
one no source under the root writes into a namespace (an attribute store
`lib._extracted_func_0 = ...`, `setattr`, a subscript store); a class member
of that name can no longer displace it and claims nothing. The root is the
nearest directory with packaging metadata above the input (or the directory
above its packages), read with the consumer scan's exclusions.

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
The class must also be able to hold the helper as an ordinary member: not a
`Protocol` (a method there is one more member every structural implementer
lacks, so a runtime-checkable `isinstance` turns false), not written with its
body on the header's line (`class Base: pass` takes no further statement), and
not decorated beyond the known namespace-preserving decorators. A base that
could be `Protocol` on any path through its module, or is spelled
`Protocol`, counts as one. Its metaclass and every `__init_subclass__` it
runs must be known to leave a plain function alone (see *Metaclasses and
descriptors* above), and no class on its method resolution order may bind
`__getattribute__`, which intercepts every attribute lookup, the helper's
included: a proxy that answers attributes from another object would look the
helper up there. A builtin base must look attributes up as `object` does,
which rules out `type` and `super`, so a metaclass's methods share a module
function. `__getattr__` is allowed, since it runs only when normal lookup
fails and nothing spells the helper's stored name: a class whose
`__getattr__` serves every unknown name from its data keeps serving
`_extracted_func_0` (fixture `r158`). A subclass that defines
`__getattribute__` is not examined, since the rule judges the class alone and
a subclass may lie outside the project: one that lets the class's own methods
through but answers every other name from another object sends
`self.__extracted_func_0` there too, and the call raises on its instances.
Local classes, nested classes, duplicated class names, decorators known to
leave the body alone but not to preserve the receiver (`mock.patch`,
`pytest.mark.*`), functions nested inside methods, and class-body functions with no parameter or
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

Annotations are written only from evidence, and their limits follow from
where the evidence comes from:

- Every name bound for an annotation is private and spelled nowhere in its
  module (`import typing as _typing`, `from pkg.models import Item as
  _Item`, `if _typing.TYPE_CHECKING:`), so nothing the program already binds
  changes meaning and no module that star-imports the helper's module takes
  a new public name. The one assumption is the one the helpers' own names
  rest on: a star import brings a private name only from a provider whose
  `__all__` lists it. A binding the module already has is used instead only
  when it is the name's single binding anywhere in the module; the module's
  own `TYPE_CHECKING`, a flag of its own under that name, is never mistaken
  for `typing`'s. A new guard carries `# pragma: no cover` where the
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
  imported under `TYPE_CHECKING` and named by a private alias; only where no module of
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
- The degradation on a type error is per proposal, not per parameter: one
  annotation the checker rejects costs the helper all of them.
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
  A body that shares its header's line (`if x: return`) is probed on a line of
  its own in the text the checker is given; a module in which no probe can be
  placed is taken to be looked at nowhere. A probe build that fails is not
  silence: before the run it refuses the run, and for a change it leaves the
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
  server reports. A file no configured checker reports on is changed as the
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
  binds (pyright gives the module itself a module's type). With the report
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
- A project that configures no mypy is checked with mypy's defaults, as its
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
  Pyright, when configured, still checks the implementation as a file.
- Which files' errors count, and what each module is called, is mypy's own
  rule under the project's configuration. A file the configuration does not
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
- Pyright verification uses a private copy of Python sources, stubs, typing
  markers and checker configuration, made once per run, kept in step with the
  project as it is refactored, and watched by one long-lived language server.
  A file is recopied when its content differs, not merely when its size or
  timestamp does, so an edit by something other than Towel cannot leave a
  verdict standing against a project the copy no longer matches. A checker
  configuration whose bytes are not UTF-8 is refused rather than copied
  without rewriting the absolute paths in it. A `pyrightconfig.json` (or a
  file it extends) that pyright itself cannot parse refuses the typed run
  before anything is written, naming the file and the position: pyright's
  grammar is JSON with `//` and `/* */` comments and one trailing comma per
  object or array, and a byte-order mark, a form feed or a no-break space is
  an error to it. pyright's language server would otherwise go on checking
  with default settings. A pyright command line that fails is reported with
  the end of what it printed to standard error. Cyclic or external source symlinks and
  configured source or stub search roots outside the project cannot be
  represented safely and cause verification to decline the proposal.
  Project include/exclude settings still determine the checker's coverage.
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

Some decorators do more than wrap the function they decorate. typeguard's
`@typechecked` recompiles it from its source with a check after every
annotated assignment, and numba's `@njit` compiles it in nopython mode. Code
moved out of such a function into a plain helper is no longer checked or
compiled: the check stops raising, or the kernel calling a Python helper
stops compiling. So Towel extracts a block, or places a call site, only where
every decorator that can reach the code is known to leave the body alone, and
places a helper inside a function or class only under the same condition.
The decorators that can reach a function's code are its own, those of every
function enclosing it, and those of every class enclosing it (typeguard
instruments every method of a decorated class). Anything else is declined
under `decorator_may_transform_body[...]`, which names the decorator
(fixtures `r7d_*`).

A decorator is known when it is one of these:

- An entry of `KNOWN_DECORATORS` in `src/towel/unification/decorator_reach.py`,
  each recording its canonical name and the library version whose source was
  read to verify it: the builtins `property` (and its `setter`, `getter`,
  `deleter`), `staticmethod` and `classmethod`; `functools.wraps`, `cache`,
  `lru_cache`, `cached_property`, `singledispatch`, `singledispatchmethod`,
  `partialmethod` and, on classes, `total_ordering`; `contextlib.contextmanager`
  and `asynccontextmanager`; `abc.abstractmethod`; `typing` and
  `typing_extensions` `overload`, `override`, `final`, `no_type_check`,
  `runtime_checkable`, `dataclass_transform` and `deprecated` (with
  `warnings.deprecated`); `dataclasses.dataclass` and `enum.unique` on classes;
  `unittest.mock.patch` and its `object`, `dict` and `multiple`,
  `unittest.skip`, `skipIf`, `skipUnless` and `expectedFailure`;
  `pytest.fixture` and every `pytest.mark.*`; and click's `command` and `group`
  (without a `cls` argument), `option`, `argument`, `confirmation_option`,
  `password_option`, `version_option`, `help_option`, `pass_context` and
  `pass_obj`.
- A function of the project that Towel can show is a plain wrapper: it returns
  the function unchanged, perhaps after storing it in a module-level `dict`,
  `list` or `set` display (a registry) or setting a non-dunder attribute on it,
  or it returns a wrapper that only calls the function with the wrapper's own
  arguments, with or without `functools.wraps`. A factory of such a decorator
  (`@retry(3)`) counts too. It must not read the function's `__code__`,
  `__globals__`, `__closure__` or any attribute but `__name__`, `__qualname__`,
  `__module__` and `__doc__`, nor hand it to any callable but `wraps`,
  `update_wrapper` and a registry's own container methods; a decorator doing
  anything the analysis cannot show harmless is declined, however harmless it
  is.

Names are resolved by binding, never by spelling. `from functools import wraps
as w` makes `@w(f)` `functools.wraps`; a `property` the module or class binds
itself is not the builtin; `@pytest.mark.parametrize(...)` and
`@click.option(...)` resolve through the callee of the call, and
`needs_db = pytest.mark.skipif(...)` makes `@needs_db` that call's decorator. A
module-level name counts only when every binding the module could give it is
known, so a compatibility import (`try: from typing import override` ...
`except ImportError: from typing_extensions import override`) counts and a name
rebound anywhere in the module to something unknown does not. A decorator
named through a local of an enclosing function, a method of an object
(`@app.route("/x")`, `@cli.command()`, `@f.register`), a class, or any other
expression is declined.

A decorator applied by hand counts as one written with `@`: every call in the
value of an assignment at module or class level, in any module of the
project, applies its callee to each definition an argument of it names, and
is judged as that decorator would be. `fast = numba.njit(kernel)` and
`fast = njit(cache=True)(kernel)` decline `kernel`, `f = typechecked(f)`
declines `f`, `method = wrap(method)` in a class body declines `method`, and
`C = typechecked(C)` declines every method of `C`, however the argument is
spelled (`kernels.slow`, an alias, a name imported from another module). A
call given the function among other arguments (`x = property(get, set)`,
`T = TypeVar("T", bound=Model)`) is covered only where the entry's reading
covers it. A name that cannot be followed to its definition (through a star
import, a name bound by a loop) is taken to be every definition of its name.
So `ORDER = sorted(items, key=rank)` at module level declines `rank`.

The class that holds the code is judged by its machinery as well: a
metaclass, or an `__init_subclass__` anywhere on its method resolution order,
may wrap or recompile its methods while the class is built. Every class
enclosing the code must pass the test a class taking a method helper passes
(*Metaclasses and descriptors* below): a metaclass that is `type`,
`abc.ABCMeta` or the enum metaclass, no `__init_subclass__` or
`__getattribute__` of its own, and bases that are builtins, `abc.ABC`,
`typing.Generic[...]`, enums, the library classes below, or classes of the
project that pass in turn, whose decorators are known to add no machinery. A
class not in a module's own body passes only when it has no bases and no
keywords, and binds neither name. Anything else declines under
`class_machinery_may_transform_methods[...]`, which names what fails the test:
`metaclass LexerMeta`, `__init_subclass__ of Base`, `decorator mock.patch of
Base`, or `base pydantic.BaseModel`, since the test reads no other class
outside the project.

The library classes are those of `KNOWN_BASES` in
`src/towel/unification/known_bases.py`, each read in CPython 3.11.15, 3.12.13
and 3.13.7 and checked of the running interpreter by the suite: `type` or
`ABCMeta` builds a subclass, nothing on the order defines
`__getattribute__`, and none defines `__init_subclass__` but
`unittest.TestCase`, whose own only sets two attributes of the subclass. They
are `unittest.TestCase`, `IsolatedAsyncioTestCase`, `TestResult` and
`TextTestResult`; `asyncio.BaseProtocol`, `Protocol`, `BufferedProtocol`,
`DatagramProtocol` and `SubprocessProtocol`; `ast.NodeVisitor` and
`NodeTransformer`; `logging.Filterer`, `Filter`, `Formatter`, `Handler` and
`StreamHandler`; `threading.Thread`; `html.parser.HTMLParser`;
`json.JSONEncoder` and `JSONDecoder`; `argparse.Action`, `HelpFormatter`,
`ArgumentParser` and `Namespace`; `http.server.BaseHTTPRequestHandler`;
`socketserver.ThreadingMixIn`; `string.Formatter`; `textwrap.TextWrapper`;
`contextlib.ContextDecorator`, `AbstractContextManager` and
`AbstractAsyncContextManager`; `collections.UserDict`; the `collections.abc`
classes (`Mapping`, `MutableMapping`, `Sequence`, `Set`, `Iterable` and the
rest of the common ones, subscripted or not); and `importlib.abc.MetaPathFinder`
and `Loader`. Classes implemented in C (`io.StringIO`, `datetime.datetime`,
`threading.local`, `ctypes.Structure`) and third-party classes are not read and
not listed. An ancestor of the project may carry, besides the decorators that
keep a class's namespace, `unittest.skip`, `skipIf`, `skipUnless`,
`expectedFailure`, `typing.no_type_check` and `typing.dataclass_transform`,
each called or not. A module that binds a builtin by a compatibility import
(`try: from builtins import object` ... `except ImportError: pass`) holds the
builtin on every path, and the base resolves to it.

A `TestCase` subclass can therefore take a class-private method helper,
`_Case__extracted_func_0`. unittest's loader collects only names starting with
its `testMethodPrefix`, `test`, and pytest collects a `TestCase`'s tests through
that same loader, so neither collects it (fixture
`r7d_method_helper_in_a_testcase`); a loader whose prefix starts with an
underscore would.

Under `--cross-module`, a block holding an `assert` is shared between two
modules only when pytest rewrites both alike (`assert_rewriting_differs`). A
rewritten assert that fails reports the values it compared; a plain one
reports only its message. Following pytest 9.1.1's own rules, a module is
rewritten when it is a `conftest.py`, matches `python_files` (`test_*.py`
and `*_test.py` by default), is a file `testpaths` names, or is named, or
lies in a package named, by a `-p` of `addopts`, a `pytest_plugins` or a
`register_assert_rewrite` at the top of the root's `conftest.py`; never under
`--assert=plain` and never with `PYTEST_DONT_REWRITE` in its docstring. The
configuration is the first pytest reads from the project's root upward
(`pytest.toml`, `pytest.ini`, `pyproject.toml`, `tox.ini`, `setup.cfg`).
Where the project cannot say, the pair is declined: a pytest configuration
below the root, `-o`, `-c` or `--rootdir` in `addopts`, a `pytest11` entry
point of the project itself (its packages are rewritten once it is
installed), a `pytest_plugins` or `register_assert_rewrite` anywhere else
(for the modules it names), or a value that is not a literal. What the
invocation adds (`PYTEST_ADDOPTS`, `PYTEST_PLUGINS`, a module path given on
the command line) is taken to be absent (fixtures `xf7d_*`).

What this does not see:

- **A function handed to a compiler or source reader only inside another
  call.** Decoration by hand is read from assignments at module or class
  level alone. An expression statement (`atexit.register(f)`,
  `app.add_url_rule("/", view_func=f)`), a call in a function body
  (`kernel = numba.njit(slow)` inside `setup()`, `Thread(target=f)`), a
  default value, and a function reached through a container
  (`njit(KERNELS["slow"])`) or through a name bound other than by a `def`, an
  import or a plain alias (`g = f if fast else h`) are not seen, and code may
  still move out of the function they hand over.
- **Import hooks.** A hook that rewrites a whole module (typeguard's
  `install_import_hook`) is neither a decorator nor class machinery. A helper
  in the same module is rewritten with it; one shared across modules with
  `--cross-module` is rewritten only if its host is, and only pytest's
  assertion rewriting is modeled.
- **Shadowed library names.** A module of the project named like a
  third-party library in the list is read as the project's own code, but one
  named like a standard-library module (`functools.py` at an import root) is
  taken to be the standard library, as everywhere else in Towel. A class
  whose metaclass's `__prepare__` fills the class namespace in advance could
  bind a decorator's name before its body runs; that is not modeled.

## Conservative rejections

Towel prefers to leave code unchanged rather than transform it under
uncertainty. Every declined pair is traced under one of the reasons of
`RejectReason` (`src/towel/unification/models.py`), listed here in the order
the pair decision raises them, grouped by stage; a `dry` run that applied
nothing prints how many pairs its last analysis declined for each (a pair
that only repeated another's proposal is not counted), and every run counts
the proposals it built and did not apply, by reason:

- Decorators and class machinery. `decorator_may_transform_body[...]`,
  counted with the decorator it names
  (`decorator_may_transform_body[typeguard.typechecked]`): a decorator,
  written with `@` or applied by a call a module or class body assigns, that
  can reach the code of a block, of a call site, or of the helper's host is
  not known to leave the body alone. `class_machinery_may_transform_methods[...]`,
  counted with what fails the test (`...[metaclass LexerMeta]`): a class
  enclosing the code does not pass the method-host test of its machinery.
  `assert_rewriting_differs`: under `--cross-module`, a block holding an
  `assert` would join modules pytest does not rewrite alike. See *Decorators
  that compile or instrument a body* above.
- Frame use. `frame_sensitive_block`: the block contains a suspension,
  a namespace read, a frame or stack read, a warning, a loop transfer
  out of the block, a comprehension assignment expression, or a `super()`
  reached through another name (the list under *Frame and control flow*
  above), or zero-argument `super()` in a method that rebinds its receiver or
  binds `__class__`. `frame_read_in_function`: the enclosing function reads
  its own frame (`locals()`, `dir()`, `eval`, `sys._getframe()`, ...)
  somewhere outside the block, or calls `super()` through another name there
  while the block holds a load of `super` or `__class__`, which may be what
  gives the function its class cell.
- Bindings crossing the block boundary. `nested_binding_escapes`: a block
  nested inside a loop or branch binds a name the rest of the function
  reads. `closure_crosses_block_boundary`: a nested function or lambda
  outside the block reads a name the block rebinds, or one inside the
  block reads a name the caller rebinds after it. `moves_scope_declaration`:
  a `global`/`nonlocal` declaration in the block names something the
  caller still uses.
- Objects created by moved code. `created_object_escapes`: a function,
  lambda, generator or class the block creates could be observed other than
  by calling it. Created inside a helper it would carry the helper's
  `__qualname__` (`f1.<locals>.<lambda>` would become
  `__extracted_func_0.<locals>.<lambda>`), which reaches output through
  `repr`, logging and registries, and a unified lambda would carry the
  template's parameter names. Such an object may still be called in the
  block with arguments it accepts, be the `key=` of `sorted`, `min` or `max`,
  or be the function of a `map` or `filter` consumed in the block; anything
  else, and any class defined in the block, is declined. Lambdas passed to
  methods, to `functools.reduce`, or joined by a non-literal separator are
  declined by this rule although many are harmless.
  `thunk_of_possibly_unbound_local`: a lambda the helper call would carry
  reads a local of the calling function that may be unbound there. The
  original raises `UnboundLocalError` at that read; a thunk can only raise
  `NameError`, so an `except UnboundLocalError` would stop matching.
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
- Free variables and lifetimes. `conditionally_bound_return`: a returned
  variable is not definitely bound at the block's exit and did not enter
  as a parameter. `incomplete_lifetime_block1`/`_block2`: the block reads a
  name that is bound only after it, or reads a local before its own binding
  of it (`scale = scale(n)`, which raises `UnboundLocalError`) where the call
  site may not have the name bound: with the block gone the name may not be
  local to the caller, and the argument would find a module name or raise
  `NameError`. `module_data_lookup`: the helper would receive module data
  (a module-level assignment) as an argument, snapshotting it. `rebound_external_binding`: the helper would receive a
  name another function rebinds through `global` or `nonlocal`, or a name
  the module's reflection makes unreliable. The names both sites resolve
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
  which reads worse than the duplication it removes. `builtin_argument`: the
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
  a region directive on a line of its own (`fmt: off`/`on`, `isort:
  off`/`on`, `yapf: disable`/`enable`, `pylint: disable`/`enable`, `ruff:
  disable`/`enable`) is not closed within the block, so its region reaches
  code that stays behind, or a file-wide directive (`flake8: noqa`, `ruff:
  noqa`, `ruff: file-ignore`, `mypy:`, `pyright: strict`, `pyrefly:
  ignore-errors`, `pyre-strict`) would move into another module.
  `directive_around_block`: an ignore on a line of its own above a site's
  block governs the block's first statement, and would stay above the call
  that takes its place, silencing the call and not the helper; or
  every site's block is reached by a directive outside it that would not
  reach the helper: `# pragma: no cover` or `# pylint: disable` at the end
  of the header of a statement enclosing the block (its function's `def`
  line, a class, an `if`, loop, `else:` or `except` line), or a `# pylint:
  disable` on a line of its own earlier in a body enclosing it and not
  enabled again before it. A helper written inside that class, as a method
  at the class's end, or inside that function, from a directive on its
  `def` line, is still reached; one on a line of its own at module level
  reaches every helper of its module and is not counted. While one site is
  measured or linted, the helper is that site's code and its tool reports
  nothing new, so a single site outside is enough. pygments' builtins
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
  other. A further site whose directives
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
  `nonlocal_safety_skip`: either block's function declares
  `nonlocal`. `private_name_lexical_class`: a site's method uses a
  `__private` name and the helper would live in another class, which
  changes name mangling. `cross_module_global_declaration`: a cross-file
  helper's participating modules include one whose functions declare
  `global`. `unproven_import`: no participating module can be imported by
  every other one in a way the program's own imports show to work (*A
  cross-file helper adds an import of its host module*). `import_cycle`: every candidate
  host closes a static import cycle. `import_time_effects`: a cross-file
  helper's host module, which the borrower's import does not already
  load, would run code at import (*Import-time behavior*: a module that
  prints, registers or connects at import time); `new_import_requirement`:
  it would require a package outside the project, the standard library and
  the declared dependencies; `new_top_level_package`: it would load a
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
  helper would read bare a builtin that a participating module may hold in
  its namespace (*Module names stay module names*).
- The proposal. `duplicate_proposal`: the helper, home and sites repeat an
  earlier pair's, found through another pair of the same family.
  `existing_helper_becomes_forwarder`: a site is the whole body of a helper
  an earlier pass inserted, which would keep only the new call, while
  another site is not a whole body. When every site is the whole body of its
  function, each of them, an earlier helper included, becomes a call of the
  new helper, since no function is ever redirected to another.

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
  deprecated and changes nothing. A site that duplicates an earlier pass's helper, where
  the other site is only part of its function, is left in place rather than
  reduced to a call of it or chained through it.
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
  index below; at `8cb8b8c` on September 24, 2026, the module and command
  of `scripts/bench_similar_blocks.py` on one core of an Apple M5 Max, with
  other work loading the machine to a load average of 15 to 19, take 6.4 to
  7.9 s for 50 and 27 to 29 s for 100, in CPU time as in wall time, a
  factor of about 4 for twice the functions; at `5ff2458`, with one other
  single-core job running and before 1.772's per-call-site safety checks,
  they took 4.0 s and 15.9 s), memoizes its per-candidate
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
