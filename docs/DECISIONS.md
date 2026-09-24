# Design decisions

Each entry records a decision about what Towel promises or refuses, when it
was made, what it rules out, and why. An entry is not rewritten when it is
superseded; a later entry says so. Where an entry's consequences are still
being implemented, its status line says which.

The owner's standing rule frames all of them: a transformation that changes
what a program does is unsound, however rarely it triggers, and "rarely" is
not a defence. Declining is acceptable only where no sound transformation
exists; a decline that exists only because Towel lost information of its own
is a defect to fix, not a limitation to document.

## 2026-09-22: What Towel preserves

Every extraction changes something a program could in principle observe: a
traceback gains a frame, line numbers move, the module gains a name. So the
contract cannot be "nothing observable changes"; it has to say what is kept.

**Preserved:** return values; output; exceptions, by type and message; side
effects and their order; what importing a module does; and the names and
signatures of everything that existed before the refactoring.

**Not preserved:** stack frames and line numbers, and the existence of the new
helper names themselves.

Three consequences follow, each ruled on explicitly:

- **Objects created by moved code.** A function, lambda, generator or class
  created by moved code carries the helper's name in its `__qualname__`
  (`f1.<locals>.<lambda>` becomes `__extracted_func_0.<locals>.<lambda>`),
  which reaches output through `repr`, logging and registries; a dataclass's
  `repr` prints it. A block is declined unless every such object is provably
  only called within it, or passed only to a small set of builtins that call
  it and keep nothing (`sorted`/`min`/`max` keys, and `map`/`filter` whose
  iterator is consumed in the block).
- **No existing function is redirected to another.** When a duplicate is the
  whole body of an existing function, Towel used to rewrite `f2` as
  `return f1(...)`. A module-level call finds `f1` at call time, so a test
  patching `mod.f1` then changed `f2` too, and test suites patch module
  functions constantly. Both functions now call a new shared helper, so each
  existing function still depends only on itself.
- **Towel never adds anything to a class.** `A.m1(SimpleNamespace(v=10), 1)`
  runs on the original, and a helper reached through `self` breaks it; the
  owner's ruling of 2026-09-21 on `A.a(None, 3)` already said such calls
  count. Every helper is therefore a module-level function. A method's
  receiver becomes an explicit parameter, and private names are written in
  their mangled form (`self.__x` inside class `A` is `self._A__x`, which a
  module function can spell directly). Moved code that needs the class itself
  (zero-argument `super()`, `__class__`) is declined. This retires the defects
  that came from putting helpers in classes: helpers becoming `Protocol`
  members, class decorators rebuilding or wrapping the namespace, metaclass
  and `__init_subclass__` scans seeing the helper, subclasses overriding it,
  and rebound base names. Its costs: helpers stop being methods, a generic
  class's receiver needs a type variable in typed mode, and classes in
  different modules need an import where inheritance used to supply the
  helper. *Superseded the same day, before it was implemented; see "Methods
  keep method helpers" below.*

*Status: being implemented on the `audit-1772` branch; not yet released.*

## 2026-09-22: Methods keep method helpers

This supersedes the third consequence above. Towel keeps extracting the code
a method shares into a method reached through `self`.

The case that prompted the reversal, `A.m1(SimpleNamespace(v=10), 1)`, is a
call no checker accepts as written: mypy and pyright both reject it, because
the receiver of a method is typed as an instance of its class unless the
method declares otherwise. That places it outside the contract, which holds
for programs that are well formed. The alternative would itself have written
code no checker can verify. Private attribute access would have to move out of
the class in mangled form (`self.__x` inside `A` becomes `a._A__x`), and both
mypy and pyright reject `a._A__x` although it runs. The owner does not want
Towel to write code that cannot be type-checked under any annotations.

Method extraction is sound for well-typed programs under three conditions:

- **The receiver is an instance of the class.** This is the type every checker
  gives `self`. A method that declares another self type, such as
  `def m(self: HasV, n)` with a `Protocol`, has said that other receivers are
  allowed, and it gets no helper reached through `self`.
- **The class leaves an added function alone.** A metaclass, or an
  `__init_subclass__` anywhere in the class's hierarchy, may wrap, register or
  drop the functions a class defines. Only ones known to leave plain functions
  alone are allowed: `type`, `abc.ABCMeta`, `enum.EnumType`, `typing.Generic`.
  The class decorators, `Protocol` classes, one-line class bodies, rebound
  bases and project-wide helper names handled earlier are part of the same
  condition.
- **The moved code does not need the class itself.** Zero-argument `super()`
  and `__class__` bind to the class whose body the code is in.

Where a class cannot host, the helper is a module-level function that takes
the receiver as an ordinary parameter, as Towel already writes when no common
class exists; code using private names is then declined. A helper from
methods that never read their receiver stays a module-level function, as
ruled on 2026-09-21 for `A.a(None, 3)`, since that costs nothing.

*Status: the receiver and metaclass conditions are being implemented on the
`audit-1772` branch; the rest is implemented there. Not yet released. Where a
helper may be hosted is narrowed the same day by the next entry.*

## 2026-09-22: A method helper lives in the class that holds both duplicates

This narrows the previous entry. A helper is placed in a class only when every
duplicate it replaces is a method of that one class, and it gets a
class-private name (`__extracted_func_0`, which Python stores as
`_A__extracted_func_0`). Every other shared block becomes a module-level
function that takes the receiver as a parameter. That includes blocks shared
by sibling classes, by a parent and a child, or by classes in different
modules. Towel never puts a helper into a class that did not already contain
the duplicated code.

The owner chose this over both alternatives: hosting helpers in a common
ancestor as before, and never adding anything to a class. The justification:

- **Subclasses.** A method added to a class is inherited by every subclass,
  including subclasses outside the project that Towel cannot see. A subclass
  defining the same name overrides it, and the class's own methods then call
  the subclass's version. A class-private name removes that hazard. `__h`
  defined in `A` is stored as `_A__h`, and `self.__h()` inside `A` looks up
  `_A__h`, so no subclass can override it or collide with it. With a subclass
  `B` defining its own `__h(self, n: str) -> str`, `A`'s methods still call
  `A`'s helper, and mypy `--strict` and pyright strict both accept the pair.
- **No choice of base.** Hoisting a helper into a common ancestor meant
  choosing one. The choice may not be unique under multiple inheritance. The
  base name may be bound differently where each class statement runs, which
  was a defect found in the first audit round. And the ancestor may be a
  public base class that other code subclasses. Over rich, click, packaging
  and pygments, 13 of 49 class-homed helpers had been hoisted, into exactly
  such classes: pygments' `Formatter` and `Lexer`, rich's `JupyterMixin`,
  click's `UsageError`, packaging's `BaseSpecifier`. Several went into a
  third module containing neither duplicate. Under this rule no class gains a
  method unless the code it replaces was already in that class.
- **Why not module functions everywhere.** Moving a method's code out of its
  class writes code the checkers reject, and the owner does not want Towel to
  write code that cannot be type-checked under any annotations. A private
  attribute must then be spelled mangled (`a._A__x`); that runs, but mypy and
  pyright both reject it. Strict pyright also rejects a module function
  reading a protected `receiver._cache`, as `reportPrivateUsage`. Inside the
  class, both are accepted. Zero-argument `super()` and `__class__` would be
  too, since a helper defined in the same class body binds them to the same
  class. Only a same-class method helper can extract such code at all.
  (Moved code using zero-argument `super()` is extracted only into such a
  helper, and only when the helper takes the method's own receiver: the
  guard marks it as needing the class body, and placement declines the pair
  rather than give it a module function. `__class__` without `super()` is
  passed as an argument wherever the helper goes, each site passing its own
  class; beside `super()` the helper reads its own cell.)
- **The receiver.** A method's receiver is typed as an instance of its class
  unless the method declares otherwise. `A.m1(SimpleNamespace(v=10), 1)` is
  rejected by both checkers, so it is outside the contract. A method that
  declares another self type gets no helper reached through `self`.

The conditions on the host class that remain concern only the class that
already holds the code:

- it is not a `Protocol`;
- each of its class decorators is known to keep the namespace;
- its metaclass, and any `__init_subclass__` in its hierarchy, is known to
  leave plain functions alone;
- its body is not written on its header line;
- its methods declare no self type other than the class.

The costs:

- Blocks shared across classes become module functions with an explicit
  receiver. That is sound but less idiomatic.
- Such a block is declined when it uses private names or zero-argument
  `super()`. `__class__` in it is passed as an argument, each site passing
  its own class.
- A pyright-strict project's own check will reject one that reads protected
  attributes.
- Across modules, such a block needs an import under the import rule below.
- `rename-helpers`, which today refuses mangled names, must learn to rename
  class-private helpers.

Each fact about Python and the checkers in this entry is asserted by
`tests/test_hosting_rationale.py`. A release of either checker that changes
one fails that file and returns the decision for review.

*Status: decided; implementation follows the fix branches now in progress.
Not yet released.*

## 2026-09-22: Towel does not change externally visible class design

The owner's position: changing a class's design is a judgement for an
intelligent actor, not for a mechanical transformation. That covers moving a
function into a base class, a mixin or a new class. Towel is meant to be
paired with a coding agent. Towel's part is the extraction it can prove
sound. The agent's part is to inspect each extracted function and decide
whether it belongs in a class, existing or new, and to make that change as an
ordinary edit checked by the project's tests and type checker.

The justification: where a function belongs depends on intent that is not in
the program's semantics. It depends on which classes the function serves,
whether a base class is public API that others subclass, whether a mixin is
warranted, and what the project's conventions are. A mechanical rule either
chooses arbitrarily or encodes design heuristics. When Towel chose, it put
helpers into pygments' `Formatter` and click's `UsageError`, public bases
that plugins and users subclass. An agent can weigh those things, and its
change is reviewed like any other.

The class-private helper of the previous entry is consistent with this, as
the owner confirmed: it does not affect externally visible class design. It
goes only into the class that already contains both copies of the code. It
is invisible outside that class, since Python mangles its name. It changes no
interface and no hierarchy. It is an implementation detail of that class, as
the duplicated code was. A block shared across classes stays a module
function that takes the receiver. Towel leaves the design question it raises
to the agent.

*Status: the `towel-rename` skill will carry the agent's step once the
hosting rule and class-private renaming are implemented.*

## 2026-09-22: Import names come from the program

A module's name is not a property of its file. It depends on how the project
is installed or run, and every wrong import Towel wrote came from inventing an
answer: from packaging metadata (`src.waitress.task`, `foo.src.foo.a`), or
from the directory layout (`src.alpha.a` where `src/__init__.py` exists).
Checked against built wheels, the packaging readers named 1,039 of the
corpus's files wrongly when analysis started at a project's root, and "two
derivations must agree" failed exactly where both inventions agreed.

Towel now takes names only from the program's own imports.

Before refactoring it checks that those imports are well formed:

- every absolute import of a project module resolves to exactly one file (a
  stale `build/lib/alpha/` beside `src/alpha/`, or an installed package of the
  same name, makes `alpha` ambiguous);
- no file is reached under two names;
- no relative import climbs out of its top-level package.

Towel then writes:

- between two modules of one package, the spelling the importing file
  already uses for its neighbours, or a relative import;
- across top-level packages, an import only when the importing package
  already imports the other one, spelled as it already spells it.

`tests/` may borrow a helper from `alpha`, which it already imports; `alpha`
may never borrow one from `tests`. Anything else is declined. Packaging
metadata is no longer part of the soundness argument.

The costs, accepted:

- sibling packages that share a duplicate but never import each other get no
  shared helper;
- a directory of scripts that import nothing local gets no cross-file
  helpers;
- a project whose imports are ambiguous must exclude the stray copy first.

*Status: being implemented on the `audit-1772` branch; not yet released.*

## 2026-09-23: How the import model decides, and when a problem refuses

This refines the previous entry, which is now implemented as
`src/towel/import_model.py`. Measured against runtime oracles, the literal
rules reproduced some of the defects they were written to prevent, so the
model follows the import system's own rules:

- A built-in or frozen module cannot be shadowed by a project file of its
  name, and a regular package or module anywhere on the path beats a
  namespace directory of the same name.
- A stray `src/__init__.py` does not make `src` a package unless some import
  uses it as one.
- Only an import that actually runs attests a name. Imports under
  `TYPE_CHECKING`, inside `try`/`except ImportError`, or in a file that
  changes `sys.path` do not count.
- A package's `__main__.py` is never offered as a host, since importing it
  runs the program. Nor is a module below a directory without `__init__.py`
  inside a regular package, which setuptools' `find_packages` leaves out of
  the wheel.

**Import problems refuse the run only where they touch the code being
refactored.** Read literally, "Well-formed input" refuses every run whose
program has an import problem. Over the 141-project corpus that refuses 27
projects, almost all over files outside the code being refactored:

- a stale `build/lib/anyio` beside anyio's source;
- deliberately odd test data in sphinx's `tests/roots` and black's
  `tests/data`;
- an example importing a module that no longer exists.

Soundness does not need that. The model declines exactly the names a
problem involves, and a problem in test data cannot change how the package
being refactored is named. So a problem that involves the package being
refactored refuses the run before anything is written: anyio's stale copy
of itself must be excluded or deleted first. A problem anywhere else is
listed with its `--exclude` remedy, and the run continues. The owner chose
this as the friendliest policy that keeps soundness.

**The directory rule stays strict.** A new import may enter a directory only
where its own side already imports from it. beautifulsoup4's wheel leaves out
`bs4/tests`: without the rule, 10 of 40 sampled import pairs broke the
installed wheel, and with it none did. The price, as the share of candidate
module pairs that would otherwise have been spelled, is 44% for networkx,
31% for tornado, 27% for beautifulsoup4, 7% for sphinx, and none for
waitress, attrs or pytest. Most of it is test code borrowing across test
directories inside a package. Relaxing the rule for test code would accept
the risk that only part of a test tree ships, and the owner kept it strict.

*Status: the model is implemented; wiring it into import naming, host
choice, the cycle and import-effect checks, renaming and the CLI is in
progress on the `audit-1772` branch. Not yet released.*

## 2026-09-23: Cross-module extraction stays on for the third audit

Most of the P1s the first two audit rounds found were in cross-module
extraction: import naming, cycles and import-time effects, hosts that need a
dependency or do not ship, stubs, shadowed builtins, relative imports inside
moved code, and scripts run by path. The latest is a builtin patched into
the borrower alone (`mock.patch("m.len", create=True)`), which a helper
hosted elsewhere does not see. Cross-module helpers are about 14% of the
helpers on click, rich, packaging and pygments.

The owner kept cross-module extraction on by default. The stop rule of
2026-09-22 stands: if the third from-scratch audit finds cross-module P1s,
1.772 ships with cross-module extraction off by default, behind a flag. The
builtin case is being fixed by passing the builtins that moved code reads to
a cross-module helper from each call site, so each is looked up where the
original code looked it up. *Superseded the same day; see
"Cross-module extraction is opt-in" below.*

## 2026-09-23: Cross-module extraction is opt-in

This supersedes the previous entry. Cross-module extraction is enabled only
by an explicit flag, `--cross-module` (a matching engine option for library
use). The owner's reason is the user's point of view. Someone who runs
Towel to deduplicate a package may be surprised when it starts adding
imports between their modules, and an explicit opt-in removes the surprise.
It also confines the machinery behind most of the first two rounds' P1s
(import naming, cycles and import-time effects, hosts that must ship, stubs,
shadowed and patched builtins, scripts run by path) to runs that asked for
it.

Without the flag, a helper always lives in the module whose code it
replaces, and Towel writes no import of a project module that runs. It still
adds a type-only import under `if TYPE_CHECKING:` when a helper's annotation
needs a type another module defines. The owner kept those, in a
same-day amendment to this entry, because they never run and so cannot
change behaviour, while dropping them would cost annotations their
precision. The import model spells them, built only when one is needed.
Where it cannot spell a name, the annotation leaves the name written out or
falls back, and Towel never guesses. Without the flag an import problem never
refuses a run and is not reported, since nothing that runs depends on it.

The release corpus runs with the flag on for every project, to catch bugs in
the mode that needs it most. A project may turn it off only through its
manifest entry, with the reason recorded there, and the corpus report lists
every such exception. The third from-scratch audit covers both modes. The
stop rule applies to what it probes in each; what to do about a P1 found
only with the flag on is for the owner to decide when it arises.

*Status: being implemented on the `audit-1772` branch; not yet released.*

## 2026-09-23: A helper never takes a builtin as a parameter

A builtin that moved code reads resolves in the namespace of the module the
code runs in. A cross-module helper therefore reads `len` in its host, while
the original read it in the borrower. The difference shows only where the
two modules' `len` can differ. It can differ if one module shadows or
rebinds the name, or if a test patches it into one module alone:
`mock.patch("pkg.exports.len", ..., create=True)` is the documented `mock`
idiom for a builtin. Passing each builtin into the helper would preserve
that, but a helper that takes `len` or `print` as a parameter would surprise
anyone reading it, and the owner ruled it out.

A cross-module pair is declined instead, wherever the program gives evidence
that a builtin its moved code reads can differ between the participating
modules. The evidence is either of two things:

- the name is shadowed or rebound in one of them, statically (a definition,
  import, assignment, `global` rebinding, or a star import that could bind
  it) or through the module's own namespace (`globals()`, `vars()`, or
  `setattr` on the module);
- the project's own code or tests patch that builtin into one of them, by
  `mock.patch` or `patch.object` (with or without `create=True`), or by
  pytest's `monkeypatch.setattr` or `setitem`.

Otherwise the helper reads the builtin in its host, as any function does,
and takes no builtin parameter. This applies only with `--cross-module`. A
helper in the module whose code it replaces reads the same namespace the
code always did.

What remains is an assumption of the opt-in mode, stated in the
limitations: code outside the project that patches a builtin into one of its
modules is not seen.

*Status: implemented on the `audit-1772` branch; not yet released. The same
day the owner made this the default rather than a prohibition; see "A name
is its binding, not its spelling" below.*

## 2026-09-23: A name is its binding, not its spelling

Extraction moves code between environments, so every free variable of a
pair is a question of what it is bound to at each site and whether the
helper would see the same binding. The owner adopted this rule as the
default:

- **The same binding everywhere, the helper included.** The helper reads
  the name directly. Examples are a global of the same module, read by a
  helper in that module, and a builtin that no participating module shadows,
  rebinds or has patched.
- **Bound at each site to the corresponding thing.** This covers each
  enclosing function's own local, parameter or closure variable, and a
  module name in two different modules. The helper takes the name as an
  ordinary parameter, and each call site passes its own. This is how
  extraction treats free variables: sound, and unsurprising, since a helper
  takes the variables it uses. A parameter is evaluated at the call, while
  the original read the name where it used it, so the existing checks for
  rebinding in between (thunks, `module_data_lookup`, the closure guards)
  choose between eager, lazy and declining.
- **Bound to different kinds of thing at the two sites**, such as local at
  one and global at the other. The blocks differ at that name, and it is
  treated as any difference is: parameterized where that is safe, declined
  otherwise. A user global at one site against a local at the other is
  declined today, as `module_data_lookup`.

Builtins differ only in the second and third cases. Passing one is correct
but surprising, so by default a builtin is read directly only where it is
the builtin at every site and nothing says it may differ; otherwise the pair
is declined. A builtin at one site against a local of the same spelling at
the other (`len` in `first`, a parameter `len` in `second`) is such a
difference, and is declined.

**`--parameterize-builtins` opts out of that default.** With it, in exactly
the cases the default declines, each call site passes its own binding of the
builtin as a parameter, so behaviour is preserved. It never makes a builtin
a parameter where reading it directly is already sound. The owner asked for
it so that a user who wants those extractions can have them explicitly.

*Status: being implemented on the `audit-1772` branch; not yet released.*

## 2026-09-23: A typed run compares against its baseline

This supersedes the clean-baseline requirement of "Well-formed input".
Typed mode had refused a project unless Towel's baseline check of it was
clean. A study of 20 corpus projects in their own environments found all 17
that type-check passing their own check as their CI runs it, while Towel's
baseline was clean for 6. None of the errors Towel's baseline reported was
also reported by the project's own check. Towel checked something different:

- tests, benchmarks and docs outside the CI's targets;
- a configured pyright that no CI step runs;
- without the CI's flags or its typing dependencies.

The owner chose a **differential baseline**:

- Typed mode proceeds on a project whose baseline has errors, and rejects a
  candidate only if the check reports an error the baseline did not have.
- Errors are compared by file and message, ignoring line numbers. A message
  that embeds a line number reappears as new after a line shift, so it
  rejects rather than masks.
- The final cold confirmation compares the same way.
- A checker that cannot run at all still refuses the run.
- The run reports the pre-existing errors, and where they may hide new ones:
  an unresolved import or a missing stub turns types into `Any` downstream.

This checks strictly more than `--no-types`, the advice it replaces.

Making Towel's check agree with the project's own is recorded as a
proposal, not adopted: `docs/proposals/project-own-check.md`. It would
discover the check from tox, pre-commit, nox, Makefiles and workflows, and
use its targets, flags and checkers, always including the package being
refactored. On the same 20 projects it would agree exactly with the CI for
13, and the differential baseline would cover the rest.

The same day the owner confirmed that the default mode may consult the
import model for read-only questions about the program's own imports:
whether a callee is `typing.cast` or a project's own `cast`, and where an
absolutely imported base class is defined. It never prints or refuses
there, and it writes no import that runs.

*Status: being implemented on the `audit-1772` branch; not yet released.*

## 2026-09-22: Checked with the project's own checker, as configured

A candidate is verified by the checker the project configures, exactly as the
project configures it. Configured mypy plugins are loaded. Towel's worker used
to strip them as if they were settings about where to write output, but a
plugin is a type rule: without pydantic's plugin, mypy sees `Model(x=1)` as
taking `**data: Any`, and Towel accepted code the project's own mypy rejects.
Loading a plugin executes code the project's configuration names, which is
what the project's own `mypy` run does.

A configured checker or plugin that cannot run refuses the typed run, with
the checker's own error and the remedies, rather than verifying with less
than the project's check. A project that configures no checker keeps the
documented untyped behaviour.

*Status: being implemented on the `audit-1772` branch; not yet released.*

## 2026-09-22: Well-formed input

Towel requires a well-formed program before it refactors:

- every file it changes decodes in its declared encoding and parses;
- the program's imports pass the check above;
- in typed mode, the project's own checker runs as configured and its
  baseline is clean.

A file that does not parse is skipped rather than refused: it cannot run, so
nothing Towel does elsewhere can change its behaviour, and repositories often
keep deliberately invalid test data (17 of the corpus's first 52 projects
held files a checker could not build).

A valid file never contains a character its encoding cannot hold. Where
Towel's own rendering produced one, by printing the constant `"\u20ac"` back
as `€` into a Latin-1 file, the escape is written back inside its string
literal, and the result is kept only when it parses to the identical tree.

## 2026-09-22: When 1.772 ships

1.772 is released only after a from-scratch audit round finds no P1 in what
it probed. A P1 is a behaviour change or broken import reported as success,
or a false clean that leads to accepted output. The first round found about
twenty and the second sixteen, most in cross-module extraction. If a third
round still finds cross-module P1s, 1.772 ships with cross-module extraction
off by default behind a flag, and cross-module soundness becomes the next
release's work. The 141-project corpus then runs against the final commit.
Its recorded phases total about two hours, roughly half an hour to an hour of
wall time at four workers.

## 2026-09-24: A helper module shared by unrelated directories is a proposal

The owner suggested a way to recover cross-module duplicates that the
directory rule and the cycle and import-change checks decline. Behind a flag
of its own, the helper would go in a new module at the most specific regular
package that holds every borrower. A study of 12 corpus projects found 19,914
such duplicates recoverable, 3,831 of them between library modules. They
include the largest cross-module duplicates in sphinx's C and C++ domains
and in networkx's graph classes.

It is recorded as a proposal, `docs/proposals/shared-helper-module.md`, and
is not part of 1.772. A new host alone does not make it sound. Four more
things are needed:

- every directory from the common package down to each borrower must be a
  regular package;
- the new module must be shown to ship;
- test helpers must not become a shipped library module, which 949 of the
  recoverable duplicates would do;
- the new module needs its own cycle and import-change argument.

That last point matters because Towel's current checks see false cycles
through the common package's `__init__.py`, and accept only 684 of the 3,831
library cases. Building it now would reopen the audit rounds that 1.772 is
closing.

*Status: proposal; not scheduled.*

## 2026-09-24: A type-only import is not an import edge

The amendment that lets Towel write imports under `if TYPE_CHECKING:`
rests on their never running. Towel's cycle guard nonetheless counted them
as import edges, and so did its reckoning of what an import may load. A
type-only import that Towel wrote to make an annotation precise then
refused later extractions between the same two modules: on mistune, typed
refactorings fell from 19 to 17. That contradicted the premise, and it
traded away the precision the imports exist for. Towel's analysis of
import-time effects already treated such a body as never running.

The owner pointed out the contradiction, and the guard now follows the
premise. A body guarded by `TYPE_CHECKING` is not an import edge anywhere:
not for cycles, not for what an import may load, and not for what it
requires. The guard is recognised by binding: `typing.TYPE_CHECKING` or
`typing_extensions.TYPE_CHECKING`, however imported, or the module's own
`TYPE_CHECKING = False`, bound once and never again. Its `else` branch still
counts, and so does a guard Towel cannot resolve.

A program that sets `typing.TYPE_CHECKING = True` before importing is
outside the model. It breaks the idiom's own use for breaking cycles in
every project that relies on it. The tool once cited for doing so,
sphinx-autodoc-typehints, no longer does: version 3.13.7 has no such
option. It runs a guarded block only after its module has imported, one
statement at a time, and mocks whatever fails to import.

*Status: being implemented on the `audit-1772` branch; not yet released.*
