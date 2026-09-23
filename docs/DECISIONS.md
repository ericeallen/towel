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
  class, both are accepted. So are zero-argument `super()` and `__class__`,
  because a helper defined in the same class body binds them to the same
  class. Keeping same-class helpers as methods is what lets that code be
  extracted at all.
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
- Such a block is declined when it uses private names. Zero-argument `super()`
  or `__class__` in it is declined as before.
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

## 2026-09-22: Towel does not change class design

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

The class-private helper of the previous entry is consistent with this. It
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
