# Changelog

All notable changes to Towel are recorded here. This file summarizes releases;
[docs/RELEASE_LOG.md](docs/RELEASE_LOG.md) keeps the detailed engineering log,
and [docs/PRODUCTION_READINESS.md](docs/PRODUCTION_READINESS.md) records the
ecosystem evidence behind each claim. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.772] - 2026-09-21

Changes since 1.732.

A typed run over a large project did not finish. Sphinx, 243 modules with mypy
and Pyright both strict, ran 3 h 21 min without reaching a fixed point where
the same run without types takes 11 minutes. Nearly all of that was the mypy
worker getting slower with every request it served, and most of the rest was
asking the checkers questions whose answers were already known. It now reaches
a fixed point in 46 minutes, applying 380 refactorings across 105 files, after
which Sphinx's own test suite reports exactly what it reported before: 2385
passed, 34 skipped, and the same six failures that checkout already had.

Every figure in this file was measured on an Apple M5 Max with 18 cores and
128 GiB. This entry's were taken on macOS 26.5.1 with Python 3.12.13, the
machine otherwise idle. The Sphinx figures are Sphinx 9.1.1 at `e44a40e`, 243 modules with
mypy and Pyright both strict, checked by that project's own venv: **mypy
1.19.1 and pyright 1.1.407**. The mypy behaviour described below belongs to
that version; Towel's own checks run against a newer mypy and do not show it.

### Fixed
- The import sorter could reorder a file's own imports, and so the order
  their modules run in. With ruff excluding `pkg/app.py`, Towel rewrote
  `from pkg import zeta` / `from pkg import alpha` as
  `from pkg import alpha, zeta`, and a plugin registry's order flipped.
  httpcore's byte-compared `_sync` twins were re-sorted the same way.

  Sorting now respects each tool's own file selection, judged for the
  project's file rather than the run's staged copy:
  - ruff: `exclude`, `extend-exclude`, `lint.exclude` and
    `per-file-ignores`;
  - isort: `skip`, `extend_skip`, `skip_glob`, `extend_skip_glob` and
    `skip_gitignore`, skipped directories included.

  A file is sorted only if its original imports are already in the
  sorter's order, and a result is kept only if the file's own imports keep
  their order. So only the imports Towel added can move, and a file left
  unsorted is reported once. `ruff format` and Black respect their
  exclusions too.
- `# ty: ignore[...]` and `# pyrefly: ignore` were read as plain comments.
  Towel moved the code they silenced into call-site lambdas and left the
  ignores behind. On griffe, its own `ty check` went from 2 to 23
  diagnostics. These are now directives, as are:
  - `# zuban: ignore` and `# nosemgrep`;
  - Fixit's `# lint-ignore` and `# lint-fixme`;
  - ruff's `file-ignore`, `disable` and `enable`;
  - pyrefly's `ignore-errors`.

  An ignore on a line of its own governs the next line of code, as those
  tools read it.
- With `--cross-module`, Towel took a project directory named like a
  library it requires for that library, when its own interpreter lacked
  the library. For example, `click/` sharing one module with click, while
  the project depends on `click>=8`. The import it wrote then failed once
  the package was installed. A requirement declared in any of these now
  puts the name in doubt: pyproject.toml, setup.cfg, a uv, Poetry, PDM or
  Pipenv lockfile, or requirements*.txt.
- With `--cross-module`, a library installed in an environment inside the
  project root, such as the project's own `.venv`, was taken for the
  project's own code. It now counts as installed outside the project.
- With `--cross-module`, Towel could host a helper in a module the build
  leaves out of the wheel, so the installed package failed to import. It
  now reads the declared exclusions of hatch, setuptools, MANIFEST.in,
  Poetry, PDM, uv, flit and scikit-build-core, and every `.gitignore`. It
  uses them only to put a host in doubt, never to name a module. An import
  inside a function no longer shows that a directory ships.
- With `--cross-module`, a top-level module inside a package, imported by
  its bare name from beside it, went unrefused. The borrower was given a
  relative import that fails where the original works. It now refuses.
  `from . import x` and `from pkg import x` of a missing submodule are now
  reported, and their file is left unchanged.
- Each import problem's remedy now fits its kind:
  - `--exclude` for a stray copy in the tree;
  - an environment where the name is this tree, or one without it, for an
    installed copy;
  - a rename, `--exclude` or a dropped requirement, for a required
    distribution.
- Typed mode now reads per-module `follow_imports` as mypy does. An error
  in a module its own section follows was dropped, and a helper the
  project's mypy rejects was let through.
- A typed run over part of a project (`towel dry tests tests`) found the
  project's own packages in a stale installed copy, and wrote annotations
  for the installed API. It now finds them in the tree.
- Probes now name modules as mypy does under `explicit_package_bases`,
  `mypy_path` and namespace packages. A probe build that fails is now a
  checker failure. It had been read as unreachable code, which ended PEP
  420 projects at a false fixed point with exit status 0.
- A changed file outside the configured `files` is named as its importer
  names it, so mypy no longer refuses the build with "found twice".
- A mypy configuration that mypy only warns about no longer refuses the
  typed run, and the warning is passed on. Examples are an unknown option,
  a global option in an override, or a dropped Python version.
- A typed run accounted for errors on the lines a change wrote by message
  alone. Merging two copies of a block then freed the second copy's errors
  to hide a new error with the same message. A helper typed `int | str`,
  whose `p + p` repeated two pre-existing messages, was accepted, and the
  project's mypy rejected it.

  Each error is now accounted for by an error of the original that stood
  where it stands:
  - in the helper, the same message at the same statement of one copy of
    the block, each error once;
  - at a call site, its own copy's.
- A file whose imports the checker cannot resolve is now left alone even
  when the configuration silences that report. Examples are
  `ignore_missing_imports` and pyright's `reportMissingImports = "none"`.
  Every checker is asked what each import binds. A subtype question about
  a type the checker sees as `Any` answers unknown, so it no longer folds a
  union.
- A file that cannot be decoded is left out of a typed run and left
  unchanged, as without types, instead of refusing the run.
- Files that pyright's configuration excludes or ignores are known before
  the run, and pyright is not asked about them. That removes a minute-long
  stall. Another checker that checks them settles them, rather than their
  being declined as unreachable. Where no checker covers a file, its helper
  takes only the annotations its sites declare.
- A class the checker names by its whole path, which the helper's module
  does not import, is now imported under `TYPE_CHECKING` and named
  directly, as documented. It had been written `Any`.
- A verdict that nothing rebinds a name a block reads was memoized under the
  structure of the block and its function. It then answered for identical
  code whose enclosing function rebinds that name with `nonlocal`, in the
  same module or in another. The name was passed to the helper as an
  argument, and so read before the rebinding.

  The same key let a top-level copy of a block lend its bindings and escape
  verdict to an identical copy inside a loop. That copy was then extracted
  without returning the variable the loop reads next.

  Block guards and per-block analyses are now memoized per block site: the
  module's source, and the positions of the function and the block. On
  rich, one core, analysis takes about 6% longer, and the output is
  unchanged. A test runs the analysis with every memo disabled and requires
  the same verdicts, so a cache key that leaves out a dependency fails the
  suite.
- A block that spells a class-private name only as a parameter, or in an
  import's module or member, was moved out of its class. There the name is
  not mangled:
  - `(lambda __p=0: 7)(__p=y)` raised `TypeError` in a method and returned 7
    in a module-level helper;
  - `import __tool` inside `class A` imports `_A__tool`, but `__tool` in a
    helper.

  Every position CPython mangles now counts. This is checked against the
  interpreter's own symbol tables for all 37 identifier positions in its
  grammar. A call's keywords are compared with the parameters as the class
  stores them, so the same call is declined even when the helper stays in
  the class, where its `TypeError` would name the helper.
- A typing name that a helper's annotation needed was imported publicly
  (`from typing import Callable`). That replaced a same-named binding in
  every module that star-imports the host. It also replaced one in the host
  itself, where a star import, a `try` or an assignment had bound the name.
  `case Callable():` then raised `TypeError`, and a package's own `Any`
  became `typing.Any`. On click, rich and packaging, 13 modules gained a
  public name.

  Typing names are now reached through a private `import typing as
  _typing`, or through the module's own sole `import typing [as t]`.
- The `from typing import TYPE_CHECKING` written for a new guard rebound a
  module's own `TYPE_CHECKING`, for example
  `from pkg.flags import DEBUG as TYPE_CHECKING`, and turned its flag from
  True to False. A new guard now reads `if _typing.TYPE_CHECKING:`, which
  mypy and pyright both recognise, unless the module's sole binding of that
  name is typing's own.
- A class imported for the checker is bound under a private alias
  (`from pkg.models import Item as _Item`, spelled `"_Item"`), since
  checkers carry that import into every star-importer.
- A new guard carries `# pragma: no cover` only where the project's
  coverage exclusions would not otherwise match it, so coverage is
  unchanged.
- A typing import was skipped when the same line appeared anywhere in the
  file, even inside a function, and the helper's annotation then raised
  `NameError` at import.
- On Python 3.11 and 3.12, Towel compared the syntax trees it builds with
  parsed ones by `ast.dump`. Before 3.13, `ast.dump` spells a field the
  constructor was not given differently from one set empty. On 3.12 the
  helper's `FunctionDef` has no `type_params`, so nearly every helper was
  declined as "could not be rendered". Other comparisons could also miss:
  - the subtype relation that needs no checker;
  - union normalisation;
  - the instantiation check;
  - structural keys and proposal identities.

  Each miss cost a refactoring or precision, never soundness. Every such
  comparison now uses one spelling on every supported Python, the one
  3.13's `ast.dump` writes. On 3.12 this recovers 5 typed refactorings in
  the corpus: packaging 2, mistune 2 and nox 1.
- The test suite passes on Python 3.11, 3.12 and 3.13. Fixtures in PEP 695
  syntax, and tests of `typing.override`, are skipped where the running
  Python lacks them.
- The fallback annotations of a typed run are built from the checker's
  answer. Under strict mypy, the rung that made every annotation `Any` had
  verified 4 times in 46, and 32 of its refusals were "Returning Any". Now
  `Any` goes only where the errors point:
  - a parameter read on a failing line;
  - `Callable[[...], Any]` for a thunk whose result is at fault;
  - the parameter an "Argument N" error names.

  A return is loosened least, to `<declared> | Any`, which is also how a
  helper returning `NotImplemented` is typed. Under mypy's
  `warn_return_any`, the every-`Any` rung is not tried. The unannotated
  helper is not tried under `disallow_untyped_defs`, or in a module whose
  every function is annotated. Such a proposal is declined saying so;
  before, idna's output failed its `mypy --strict` CI with 4 errors.
- An extraction that no helper signature can type is declined early, under
  a named reason in the run's summary:
  - it narrows what its caller reads after the call;
  - it narrows what a call-site lambda reads;
  - it declares its class's attributes;
  - it completes its caller's partial type.

  Two of these are decided from the proposal alone, without asking the
  checker.
- A declined proposal is replayed at a rehearing, rather than checked
  again, only while nothing it could depend on has changed: its rendered
  files, the files its errors lie in and what they import, and the
  baseline's record of known errors. On packaging, checks that exactly
  repeated an earlier one went from 16 to 0.
- A number of annotation fixes:
  - outside a class, a `Self` the sites revealed becomes a type variable
    bound to their classes;
  - no annotation is written as the string `"None"`, which mypy 2 rejects
    under `native_parser`;
  - a site revealed as `Any` no longer erases another site's type in a
    join;
  - a copied declaration gives way to the narrower type the block saw;
  - a method helper is placed where the attributes it assigns keep their
    declarations;
  - every rung writes only the type-only imports its own annotations use.
- A class name shortened inside a subscripted annotation (`dict[Item, int]`)
  was left unquoted while `Item` was imported only under `TYPE_CHECKING`.
  mypy accepted it, but the module raised `NameError` on import.
- Coverage exclusions set in a project's configuration now count the way a
  `# pragma: no cover` comment does. Examples are `if __name__ ==
  .__main__.:`, `raise NotImplementedError` and `@overload` in
  `exclude_lines` or `exclude_also`. Before, only the comment counted, so
  such an entry could move excluded code into a helper that coverage
  measures. Towel reads the configuration as coverage.py 7.16 does: from
  `COVERAGE_RCFILE`, `.coveragerc`, `.coveragerc.toml`, `setup.cfg` or
  `tox.ini` `[coverage:report]`, or `pyproject.toml`
  `[tool.coverage.report]`. Its three default patterns count as well. Where
  `exclude_lines` leaves the pragma out, a `# pragma: no cover` comment
  excludes nothing, so it no longer declines a pair.
- A type-only import now joins the module's existing `TYPE_CHECKING` guard,
  recognised by binding as the import graph recognises it. Towel had found
  the guard by its exact text, and indented the new import four spaces:
  - packaging's `if TYPE_CHECKING:  # pragma: no cover` got a second guard;
  - a guard body indented two spaces or with a tab raised `IndentationError`;
  - in a module that rebinds `TYPE_CHECKING = True`, the import was joined
    to a block that runs.

  The import now takes the indentation of the guard's own body.
- A `--cross-module` run refuses only for a problem that leaves a name of the
  code being refactored in doubt. Such problems are:
  - an ambiguous name, such as a stale `build/lib` copy of the package, which
    `--exclude build` still clears;
  - a file reachable under two names;
  - a top-level name found inside a package;
  - a relative import that climbs out of its package.

  An import of a module the tree lacks refuses nothing, whether the run is
  from the root, on a package or on a sub-package. Such imports had stopped
  every run on sphinx, astroid and prompt-toolkit, and on seven packages
  whose modules import the `_version.py` their build generates, which a
  fresh clone lacks. With the refusal set aside, the package's name was
  flagged, so every helper its modules could share was declined: 3,858 of
  the 3,860 cross-module pairs on sphinx.

  Over 140 corpus projects, runs on the package refused 14 before and 4
  after. Runs from the root, measured only after the first of these changes,
  refused 24 and now refuse 18. Every remaining refusal is for a name in
  doubt, such as the four module names sphinx's `tests/roots` each defines
  more than once.
- In a `--cross-module` run, a file making such an import is left exactly as
  it was. It hosts no helper, borrows none, and none of its own duplicates
  are extracted. The run names each such file, with a remedy that fits:
  test data or an example can be left out with `--exclude`, and a package's
  generated module appears once the project is installed. When the file is
  a package's `__init__.py`, that package's modules still share helpers
  among themselves, and the run says in one line that they host none for
  modules outside the package.
- A run on a sub-package now refuses, instead of going on with every helper
  declined, when a relative import elsewhere in the package climbs out of
  it.
- An import below a module file names the module it lacks: `util.gone`, not
  `util`, when `util.py` exists. A module file that registers submodules at
  run time, as `six.py` registers `six.moves`, is not reported as missing
  them, wherever it sits.
- A typed run no longer changes code that the type checker does not look
  at. That is code the checker takes to be unreachable on the platform and
  Python version it checks for, and there it reports nothing. On darwin,
  mypy skips a module guarded by `assert sys.platform == "win32"`, so
  Towel's check of changes there was vacuous. trio's CI, which checks linux,
  darwin and win32, found 14 errors in output Towel had accepted.

  Every configured checker is now asked, by a `reveal_type` probe, whether
  it looks at the lines a change writes. A change it does not look at is
  declined as not verifiable. The regions are named before the run. trio's
  CI now passes on all three platforms.
- An import under `if TYPE_CHECKING:` never runs, and the cycle guard no
  longer follows it. Modules that name each other's classes only for the
  checker can now share a helper. A type-only import that Towel writes no
  longer refuses later pairs between the same two modules; on mistune it
  had cost two typed refactorings.

  The guard is recognised by resolving the name: typing's or
  typing_extensions' `TYPE_CHECKING`, however imported or aliased, or the
  module's own `TYPE_CHECKING = False`. Its `else` branch still counts as
  an import that runs, as do `if not TYPE_CHECKING:` and a shadowed or
  unresolvable name. A module's requirements now include the imports in the
  guard's `else`, which had been dropped.
- A type variable over classes from two modules now imports the one its host
  lacks, under `TYPE_CHECKING`, spelled as the program's own imports spell
  it. packaging's validation and required-key errors, and mistune's
  renderer `__call__`, are now typed without `Any`.
- Comments inside a moved block were lost with it, because the helper was
  rendered from its syntax tree. That included tool directives, such as
  asyncstdlib's and mashumaro's `# type: ignore`, as well as
  `# pyright: ignore`, `# noqa` and `# pragma: no cover`, and every
  explanatory comment. A lost directive changes what a checker, linter or
  coverage tool reports.

  The helper now carries each comment beside the code it was written for,
  in order. It also keeps the grouping parentheses and trailing commas that
  held a list split across lines, so Black and ruff reproduce the source
  layout. Where a formatter would move a directive off its code's line,
  that helper is written unformatted. Comments above or below a moved block
  stay with its call, and `preview` shows the helper's comments.
- A typed run no longer refuses a project whose type check already reports
  errors. It reports them first, and rejects a change only for an error they
  do not account for:
  - In a file no change touched, an error must match one at the same line.
  - In a file a change touched, the two texts are aligned by a line diff. An
    error on a line the change left alone must match the original's error
    on that line, and only errors on lines the change wrote are compared by
    message.
  - A message that names a line fails closed.
  - Each written change's check becomes the reference for the next.
  - The cold confirmation compares the same way.

  In a study of 20 corpus projects in their own environments, the old rule
  let 5 through. 19 are now refactored with types, for 125 refactorings, 77
  of them in the 14 newly admitted projects.
- With mypy and pyright both configured, the combined check stopped at the
  first checker that reported anything. Against pre-existing mypy errors,
  pyright was then never asked about any candidate. Every checker is now
  asked until one reports a new error.
- A file where the original check leaves a name the checker cannot type is
  not changed. Such names come from an unresolved or untyped import, a
  missing stub, an untyped decorator or an `Any` base class. A change there
  would be checked against `Any`, and could not fail. The run names these
  files up front and counts their proposals as not verifiable.
- The cold confirmation checks the original cold before it treats as new an
  error that only it reports. On trio, pyright's language server and command
  line disagreed about files no change touched, so every typed run failed at
  the end.
- Only a checker that cannot run still refuses a typed run. The corpus
  harness reruns a project without types only in that case, and records
  every other project's pre-existing errors.
- Typed runs read every type spelling that mypy 1.x, mypy 2.x and pyright
  print. A regular expression took the last `) -> ` as the end of a
  parameter list, so `lambda: parse_extras` in packaging's `_parser` was
  annotated as the function the lambda returns. Now read correctly:
  - a function returning `None` (`def (builtins.int)`), a function
    returning a function, and a generic function;
  - a named tuple or typed dict, and its constructor;
  - `Union` and `Optional` beside `|`, and a literal whose text holds `<`.

  Only a literal type the checker inferred is widened, and a declared one is
  kept. `Union`, `Optional` and `Literal` are imported where they are
  written.
- The type-variable rung recognises the program's own classes. It had
  identified a class by where its import points or where it is defined,
  which never matches the checker's `packaging.version.Version`. So the
  row, and every type variable in it, was lost: rows formed in 13 of 81
  attempts across packaging, rich, mistune and nox.

  A class is now named by the absolute name the import model gives its
  module. Where the program's imports name no module, the rung uses mypy's
  `__init__` chain instead. Rows now form in 58 of 66 attempts. Along with
  that:
  - a type only a call site can name may stand inside a type variable;
  - `Any` nested in a type is kept;
  - the signature brings its typing imports;
  - a parameter the body never reads is `object`;
  - union members line up by structure;
  - a class passed directly or through a thunk (`lambda: Row`) is
    `type[Row]`, and a returned variable bound to a parameter takes that
    argument's type.
- Sites in different modules that agree on every type get their common
  signature, naming the host's classes. Before, the ordinary signature wrote
  only builtins and fell back to `Any`, and mistune's block quote and spoiler
  helpers were declined.

  Together with the ladder and baseline changes above, typed refactorings
  rise as follows, measured on Python 3.12.13 with `--cross-module` from
  `eaa3882` to `336c890`:

  | Project | Typed refactorings | Project checks per run |
  |---|---|---|
  | packaging | 10 to 20 | 68 to 36 |
  | rich | 18 to 22 | 45 to 30 |
  | mistune | 15 to 19 | 31 to 19 |
  | nox | 5 to 5 | 9 to 7 |
  | idna | 3 to 1 | 5 to 5 |

  idna's output now passes its CI's `mypy --strict idna`. Every output
  passes its project's own mypy and test suite exactly as the unchanged
  project does, and every module imports as it did.
- Pyright's language server checks a project as `pyright` itself does. Towel
  sent it a `python.analysis` section without `autoSearchPaths`, which the
  server then leaves off and the command line always sets, so a src
  layout's package was analysed as `src.<pkg>` and consumers outside `src`
  imported the installed copy, which for an editable install is the user's
  own tree. A candidate that removed `edit` from click's exports broke six
  lines of its typing tests and was reported clean, and trio reported 1989
  errors where its own pyright reports 1870. The server's settings are now
  stated in full and match the command line's in every configuration
  tested; on click, trio, jinja2, markupsafe, attrs and structlog the two
  report identical errors. A consumer that imports its package through an
  editable install is judged against the candidate wherever the package
  lives (`python/`, `lib/`, `packages/<name>/src`, or configured
  `extraPaths`), and pyright's command line is told the project it checks,
  so its answers no longer depend on where the temporary directory lives.
- `s = super; s()` shared by sibling classes was moved into a module
  function, where it raised `RuntimeError: super(): __class__ cell not
  found`; `super()` reached through another name now stays where it is.
- A frame handle taken earlier in the loop body or branch that holds a
  duplicated block (`frame = sys._getframe()`, read later as
  `frame.f_locals`) no longer lets the block move; the check had stopped
  walking at the block and never saw the handle.
- A directory run ends when a proposal renders exactly the bytes its files
  already hold. The directory driver had counted such a proposal as applied,
  so the next analysis found it again and, with no iteration bound, the loop
  never stopped; the single-file driver has always had this guard. The
  proposal is now declined and remembered until the project changes.
- Type probes at adjacent extraction sites retain their lexical scope even when
  they share a source line. Conflicting mypy specialization notes for one probe
  are treated as ambiguous evidence instead of silently retaining the last type.
- An extraction no longer gives a method a receiver it never needed. A method
  reached through its class is an ordinary call with nothing bound, so
  `Formatter.as_dollars(None, 1.5)` runs for as long as the body reads no
  attribute of `self`, and routing the block it shares through
  `self._extracted_func_0(...)` made that an `AttributeError` while every
  genuine instance kept its answer and no checker said a word. Such a helper,
  and one shared by static methods or by classmethods that never read `cls`,
  is now a module-level function. An intermediate candidate made it a
  `staticmethod` reached as `ClassName._extracted_func_0`, and the class's
  name does not always reach the class: a parameter of that name, `del`,
  `global` rebinding, a mangled `class __C`, a decorator returning something
  else, or a call while the class body still runs each broke it. `__class__`
  would reach it, but mypy does not accept it. Over packaging, click and rich
  nothing is declined; click goes from 11 helpers to 10, one module function
  serving a third class.
- An import Towel writes names a module the installed project has, because
  its name is taken from the program's own imports instead of computed from
  packaging metadata. Checked against built wheels, the old layout readers
  named 102 files of seven corpus projects wrongly (a `setup.cfg` or
  `setup.py` src layout, never read, became `src.foo.a`), 937 more when a
  project directory shares its package's name (`foo.src.foo.a`), and
  waitress' `src/waitress/task.py` as `src.waitress.task`. Without a project
  mypy configuration the checker named modules from the same root, so it
  accepted those imports, and the adopted output failed with
  `ModuleNotFoundError`. Within a package Towel writes the spelling the
  importing module already uses for its neighbours, or a relative import.
  Across top-level packages it writes an import only where the importing
  package already imports the other one, spelled as it spells it. It enters
  a directory only where its own side already imports from it, so a library
  never borrows from a test package its wheel leaves out. A relative import
  never climbs above the importer's top package. What an import runs, for the
  cycle guard and the import-time checks, is read the same way; the old
  graph had matched filenames case-insensitively (`pygments/Lexer.py`) and
  let eight pygments lexers import others at load time. An import into an
  excluded directory, such as pip's `_vendor`, is treated as unknown rather
  than empty. `rename-helpers` names modules the same way, so it works on src
  layouts. The packaging readers are gone; `--prefer-absolute-imports`,
  `--pep420` and their engine keywords are accepted as deprecated no-ops, and
  `ProjectLayout` is removed. The new reject reason `unproven_import`
  replaces `unknown_layout`.
- `towel dry TARGET OUT` refactors TARGET inside a private copy of its whole
  project and writes only TARGET to OUT, when the run succeeds. It refactored a
  copy of TARGET alone, so everything read during the run saw a directory with
  no packaging metadata and no rest-of-project: the layout readers were never
  reached (every import came out relative, which hid the defects above), and
  an import cycle or an import-time effect through a module outside TARGET was
  invisible, leaving the adopted package unimportable where the same run in
  place was correct. Out-of-place and in-place runs now make the same
  decisions; staging click, rich and pygments (460 files) takes 0.07 s. A
  project root holding more than 20,000 Python files is refused with a
  message. A failed or interrupted run leaves no OUT behind.
- The complete mypy check no longer calls a broken consumer clean. The
  consumer scan cached an answer computed with the baseline's packages left
  out and reused it for later sparse requests, so after the first check of
  `towel dry . .` a sibling analysed package went unchecked; it never noticed a
  file an applied refactoring had made into a consumer; it named a PEP 420
  module `lib` where its importers say `nsp.lib`; it read `from app import
  helpers` as an import of `app` alone; and it resolved an `__init__`'s
  relative imports against the package's parent. It now keeps the whole import
  graph, re-reads only files whose stamp changed, and applies exclusions per
  request; at its file limit it fails the check instead of returning a partial
  list. A mypy run that applied anything ends with one complete check from an
  empty cache and a fresh scan, as a pyright run already did (0.6 s on click).
  An error mypy reports without a line is counted, and a crashed worker's
  standard error is quoted.
- A helper is placed only where it stays a plain member. Never in a
  `Protocol`, where it became a protocol member and turned runtime-checkable
  `isinstance` checks false; never in a class written on one line
  (`class E(Exception): pass`), where it was written at column zero; and only
  in a class, or through a base, whose every decorator is known to keep the
  namespace (`dataclass`, `total_ordering`, `final`, `enum.unique`), since a
  decorator that rebuilds or wraps it dropped or wrapped the helper. A module
  helper is no longer placed after an assignment, decorator or class body that
  could call it at import.
- A borrower is never made to import a host that requires a module the
  borrower does not: `import tornado` is inert as a statement, but a helper
  hosted in gunicorn's tornado worker made the sync worker unimportable
  wherever tornado was absent. The standard library, declared dependencies,
  and imports inside `try` require nothing. Over rich, click, packaging and
  pygments the output is byte-identical.
- A call site whose generated thunk would read a local that may be unbound is
  declined: the thunk raised `NameError` where the original raised
  `UnboundLocalError`, and `except UnboundLocalError` stopped matching.
- Generated helper names avoid every name another source under the project
  root defines as a class member or attribute, so a subclass outside the
  target can no longer override a new helper.
- An escape in a string literal survives extraction into a file whose
  encoding cannot hold the character it denotes. Rendering spells a constant
  by value, so `"\u20ac"` in a Latin-1 file came back as `€`, which the file
  cannot hold, and the run aborted. The escape is now written back, inside
  the literal only and kept only when the syntax tree is unchanged by it; a
  character no escape can spell is declined instead. A file nested too deeply
  to analyze is skipped with a warning instead of ending directory mode with
  `RecursionError`.
- A run whose every candidate the type checker could not judge (timeout or
  crash) exits non-zero and says so, instead of printing "No refactorings
  found!"; a run that applied some reports how many went unjudged. The summary
  shows the run's actual termination reason.
- `rename-helpers` refuses a batch that would leave an import of the old name
  behind, as it did on an out-of-place output whose imports name the package
  rather than the output directory. Rename after adopting.
- A project whose own mypy configuration excludes files (`exclude`, or `files`
  not naming them) is no longer refused for errors in files its own mypy run
  never checks; an excluded file that checked code imports is still checked.
- A block passes the caller's names that only a definition inside it reads
  where it stands: defaults and keyword-only defaults, decorators and
  annotations, class bases, keywords and bodies. The helper raised
  `NameError` before its first call. A bare `x: int` no longer counts as
  binding `x`, which raised `UnboundLocalError`.
- A `match` pattern's class, dotted value and mapping keys are read from the
  caller, and nothing in a pattern becomes a parameter except a class
  pattern's class or the root of a dotted name. `case [1, x]` against
  `case [2, x]` had become a capture that matched every pair, and
  `case Color.RED` a class pattern that raised `TypeError`.
- A `type` statement's value and a type parameter's bound are closures
  evaluated when first read, and are declined wherever the caller or the
  block rebinds what they read, as lambdas are.
- A block is declined when a function, lambda, generator or class it creates
  could be observed other than by calling it. Such an object would carry the
  helper's `__qualname__`, and a unified lambda the template's parameter
  names. It may still be called in the block, be the `key=` of `sorted`,
  `min` or `max`, or be the function of a `map` or `filter` consumed there.
  Rich makes 27 helpers instead of 28; packaging, click and pygments are
  unchanged.
- Constants that compare equal but differ in type (`0`, `0.0` and `False`;
  `1` and `True`) are different constants, extracted as arguments where the
  instantiation check used to decline them. Pygments makes 74 helpers
  instead of 72, and its lexers and formatters produce identical output over
  12,679 observations.
- An integer literal too wide for decimal conversion no longer ends the run;
  a block holding one of more than 640 digits is declined.
- A checker the project configures that is not installed refuses the typed
  run before anything is written, naming the checker and its configuration.
  It had been replaced by the other checker, or by none after a one-line
  note, so a pyright-only project was refactored unverified with exit
  status 0.
- mypy checks with the project's configured plugins, loaded as its own mypy
  loads them; a plugin that cannot load refuses the run, quoting mypy's
  error. Without them Towel accepted a helper the project's own mypy
  rejected.
- A module that ships its own stub is checked through the stub, as mypy
  checks it, so an importer of a name the implementation gained and the stub
  lacks is refused rather than accepted.
- An in-place run refactors a private copy of the project and writes the
  result once, as one journaled change, after its final confirmation
  succeeds. A refused confirmation or an interruption leaves the project
  unchanged, and a file edited during the run refuses the write instead of
  losing the edit. Over click the output is byte-identical and 1.4% slower.
- A pyright configuration is read with pyright's own grammar, so a file
  pyright rejects refuses the run before anything is written, naming the
  file and position; the language server would otherwise have checked with
  default settings. pyright's failures quote its standard error, and its
  command-line probes are written only into Towel's private copy.
- A generated annotation that uses syntax newer than the project's oldest
  Python (`int | None` before 3.10, `list[int]` before 3.9) is written as a
  string in a module that does not postpone annotations. The oldest Python
  comes from `requires-python`, else mypy's `python_version` or pyright's
  `pythonVersion`. A project declaring `>=3.9` got a helper that raised
  `TypeError` at import under 3.9.
- `dry` reports what it declined, counted by reason, instead of "No
  refactorings found!" alone; a type-check refusal is no longer reported as
  "could not be rendered"; and the `.towel-helpers.json` sidecar lists only
  helpers present in the output.
- A cross-module helper no longer reads a builtin that one of its modules may
  hold differently. The helper had read builtins in its host, so `len`
  shadowed only in the borrower was read as the builtin, and a test patching
  `open` into the borrower (`mock.patch("pkg.mod.open", ..., create=True)`,
  the documented idiom) stopped reaching the moved code. The pair is now
  declined when a participating module binds the name, may bind it through a
  star import, rebinds `__builtins__` or writes its own namespace at run
  time, or when the project's code or tests patch the name into one of them
  (`mock.patch`, `patch.object`, `monkeypatch.setattr`, and targets they
  compute). No helper takes a builtin as a parameter. A patch applied from
  outside the project is not seen. A relative import inside an extracted
  block stays within its own package.
- No helper takes a builtin as a parameter within one module either. Where
  one function binds `len` (`second(rows, name, len=...)`) and its duplicate
  reads the builtin, the helper had taken `len`, and the first function
  passed it the builtin; a block reading the builtin had also joined a
  helper whose other sites passed their own `len`, and `__import__` or a
  tuple of builtins could be passed as a value. Each is declined
  (`builtin_argument`); where both functions bind the name, each still
  passes its own.
- One predicate now decides what importing a module runs, for choosing a
  host and for placing a helper. Decorators, defaults, annotations,
  metaclasses and `__init_subclass__` count as code; a `TYPE_CHECKING` block
  and a short list of callables verified at run time do not. A registration
  decorator in the host had run when the borrower was imported.
- A helper is hosted only in a module that ships with the module importing
  it, never in `tests/` or `examples/` for the package to import. A module
  with a stub (`.pyi`, `typings/`, `<pkg>-stubs`) never hosts a cross-module
  helper, and a module that can run as a script (`__main__.py`, a main guard
  in any form, a `#!` line) stays runnable by its path. Dependencies
  declared by Poetry or in setup.cfg `install_requires` count as declared.
- One existing function is never rewritten to call another. When duplicates
  are whole function bodies, every site calls one new helper, so patching
  `mod.f1` in a test no longer changes `f2`. `reuse_existing_functions` no
  longer has any effect.
- A class keeps a method helper only when its receivers are unannotated or
  annotated as the class, `Self` or a type variable bound to it, and its
  metaclass and every `__init_subclass__` it runs are known to leave an
  added function alone. Otherwise the helper is a module-level function
  taking the receiver.
- A helper goes into a class only when every duplicate it replaces is a
  method of that class; blocks shared by different classes become
  module-level functions taking the receiver. Towel had hoisted method
  helpers into ancestors that held none of the code, among them click's
  `UsageError`, rich's `JupyterMixin`, packaging's `BaseSpecifier` and
  pygments' `Formatter` and `Lexer`. These are public bases that other code
  subclasses, where any subclass could take the helper's place
  (`docs/DECISIONS.md` gives the count measured when the rule was chosen).
  Whether such a function belongs in a class is left to whoever reviews the
  output (see `docs/DECISIONS.md`). pygments makes 71 refactorings instead
  of 77 and rich 30 instead of 33; click and packaging are unchanged, and
  all four behave identically on smoke programs.
- A method helper is class-private (`__extracted_func_N`, stored as
  `_Cls__extracted_func_N`), so no subclass in or outside the project can
  override it; a class that answers unknown attribute names through
  `__getattr__`, as tinydb's `Query` does, keeps answering the helper's old
  name. A class whose hierarchy defines `__getattribute__` gets a
  module-level helper instead, since it intercepts every lookup.
- `rename-helpers` renames a class-private helper by its class
  (`path.py:Class.__extracted_func_N`) to another class-private name; a
  non-private new name, or the stored name spelled elsewhere, refuses the
  batch. `reuse_existing_functions` is still accepted, as a documented
  no-op.
- A project that configures no mypy is checked as its own `mypy` would check
  the same files, with mypy's defaults. Towel had forced
  `check_untyped_defs`, `ignore_missing_imports` and `explicit_package_bases`
  on it, and each changed the verdict: dacite, clean under `mypy .`, was
  refused for 53 errors inside unannotated tests, and a root-relative
  `src.foo.a` import was accepted. Probes that infer a helper's types still
  check unannotated bodies, in a cache of their own. Such a project is now
  refused a typed run wherever its own `mypy` fails; configure mypy or pass
  `--no-types`.
- A check resolves a module's imports where the project's own mypy does. The
  project root had been searched first, so a test's `import helpers` was
  checked against a root `helpers.py` instead of the one beside it.
- A literal that a tool reads without running the program stays where the
  tool reads it. python-statemachine's `_("There should be exactly one
  initial state: {!r}")` had become `_(__param_1)`, and pybabel extracted
  neither message; click had lost four of its 71 the same way. The message
  and context arguments of Babel's, Django's and Flask-Babel's markers, and
  of keywords a project configures, are never parameters; nor are the names,
  fields and types of the functional typing forms, `Literal[...]`, or the
  type argument of typing's `cast` and `assert_type`, where no annotation
  makes a parameter acceptable to mypy or pyright. A typing form is
  recognized by what its callee is bound to, however it is spelled
  (`from typing import TypeVar as TV`, `typing.cast`, `typing_extensions`,
  a project module that re-exports it), so a project's own `cast`, such as
  sqlglot's `exp.cast(column, to)` or SQLAlchemy's, stays an ordinary call.
  Only click changes over the four reference packages: 10 refactorings
  instead of 11, with every message still extracted.
- A helper that computes nothing is no longer extracted, whatever
  `skip_trivial_helpers` says: two unrelated `return` statements had become
  `def _extracted_func_24(__param_0, self): return __param_0`, which rich's
  `Tag.markup` and `MofNCompleteColumn.render` called across two modules;
  packaging and pygments had one each, and those are exactly what is gone.
- The fixed point ends for a stated reason rather than an incidental one. On
  sqlglot it once extracted 597 helpers, each forwarding to the previous one
  with its tuple permuted, until ordinary SQL raised `RecursionError`. A
  helper that only calls generated helpers and repacks their results is now
  declined in every configuration, forwarding is recognized whatever order
  results come back in, and a lambda handed on whole is passed through
  rather than wrapped again. With the escape guard switched off, sqlglot's
  DuckDB generator went from no fixed point in 900 s to one in 18 s.
- A base-class name is resolved as the binding in effect where the class
  statement runs, not by finding a class of that qualname anywhere in the
  file. Python binds globals as a module executes, so `Base = object`
  written between two subclasses gives them different bases. The checks on
  a host class's bases (its metaclass, `__init_subclass__` and
  `__getattribute__`) therefore read the base the class really has. A
  binding that cannot be established there counts as unknown, and the
  helper is a module-level function.
- A copied annotation is no longer evaluated a second time. An annotation is an
  expression, and the copy runs at the helper's own `def`:
  `Annotated[int, mark('a')]` called `mark` again at import, and an annotation
  the source had quoted was unquoted into one that called it for the first
  time. An annotation is written bare only when evaluating it runs none of the
  program's own code; anything holding a call, a lambda, a comprehension, a
  conditional or an f-string is written as a string, which a checker reads
  identically and the interpreter never evaluates.
- A complete project check covers the modules that import the change. mypy
  follows imports out of the files it is given and reaches no caller, so a
  subclass in another package, unchanged and never imported by the package it
  extends, was broken by a helper whose name it already used while the check
  reported clean. Those modules are found by one `ast` pass per project. The
  project root is not walked in its place: repositories hold files no checker
  can build, and one of them fails the build and refuses the project.
- A rejected candidate no longer answers for the project that follows it.
  Its text was checked, so mypy cached each of its modules against the real
  file's mtime and size, which mypy trusts without hashing. Those paths were
  given text again only when the next request named them, and the next request
  is sparse, so a provider left speculatively returning `str` went on answering
  `str` while its file returned `int`. Such a path is now given what its file
  holds, named or not. On Sphinx this costs 3.5% and changes no output.
- Two unrelated files that infer one module name are no longer collapsed into
  one, which dropped the second file's errors and reported the project clean.
  Only a stub and the implementation beside it collapse; anything else keeps
  mypy's duplicate-module refusal.
- A project whose checker cannot start at all -- a config naming a Python
  version mypy has dropped -- is told how to proceed, as a project with
  pre-existing errors already was.
- The mypy worker's cost per request no longer grows over a run. Successive
  `build.build` calls in one process keep every rechecked module's tree alive
  (mypy 1.19; about 250,000 objects per build of `sphinx.application`), and
  each build opens with a full collection over all of it, so requests slowed
  linearly and a run quadratically: 14 s per request and a 14 GB worker after
  80 minutes. Each build now runs in a forked child that exits when it has
  answered. The same 117 recorded requests take 86.5 s instead of 855 s, at a
  flat 0.75 s and 60 MB, with identical diagnostics.
- A complete project check no longer makes mypy recheck every analyzed module.
  mypy consults its cache only for a module it reads itself, and every module
  was supplied as text. Text a file already holds is now withheld. A path that
  has once been supplied with differing text stays supplied for the life of
  the cache, because mypy records the file's mtime and size beside the text's
  hash and would otherwise answer for the file with its verdict on the text.
- The Pyright language server's silence is taken as a verdict only after it
  has answered for the change. A server slow to begin is as silent as one that
  has finished, and a candidate that broke three consumers read as clean when
  the server was delayed past the 0.35 s quiet period. Each check now carries a
  marker the server must publish first.
- A candidate is checked against the project as it now stands, which is
  decided by the content of each checker input rather than by its size and
  timestamp, so an edit by something other than Towel cannot leave a verdict
  standing against a project the copy no longer matches. The language
  server's private copy restored every file a candidate did not supply to the
  bytes it first saw, so in-place, single-file and library runs lost every
  refactoring already applied: a valid follow-up was rejected and a breaking
  candidate accepted. The copy now follows the project and tells the server
  exactly which files were created, changed or deleted.
- A supplied-text record cut short by an interrupted append is repaired instead
  of failing every later request, and stopping the mypy worker stops the build
  it was waiting for.

- An extraction never separates a narrowing test from code that depends on it.
  A checker narrows a variable along a control-flow region, so moving the test
  into a helper while an expression that needs it stays at the call site left
  each half well typed and the pair not. Declined where the proposal is built,
  before any checker is asked, so the same extractions happen whether or not
  type checking is on.
- A method's receiver is no longer annotated from its call sites. It was the
  union of the classes that happened to call it, which a checker rejects,
  since an explicit receiver must be a supertype of its own class, and which
  understated the method's domain in any case: a helper on a base class is
  inherited by every subclass. One such annotation was enough to send a whole
  helper down to the all-`Any` rung, so signatures that had lost every type
  recover them.
- A class passed to a helper is typed `type[C]`. A checker shows a reference
  to a class as its constructor's signature, which reads as something merely
  callable and is rejected where a type is wanted, by `isinstance` among
  others.
- An inferred annotation that names a class the module cannot reach imports it
  under `TYPE_CHECKING`. An ordinary import would often close a cycle, the
  extraction having just made the other module import this one, and a name
  wanted only by an annotation need not exist at run time.
- An imported helper is named after the project it belongs to, not after the
  directory a run is staging its output in. A module named after a scratch
  path cannot be resolved by the checker and breaks as soon as the reviewed
  output is adopted into the place it was written for.

### Changed
- A coverage configuration that coverage.py could not read is reported once
  per project root, and coverage.py's defaults are used in its place.
- A pair whose tool directives cannot move soundly is declined:
  - directives that differ between its sites (`directives_differ`): the
    helper has one line where the sites had several, so an ignore only one
    site needed would either silence the other or be lost;
  - any tool directive that reaches code, other than a name or a literal,
    which would become a call argument (`directive_on_argument`). Checker,
    linter, coverage, bandit and formatter directives all count. The
    argument would sit outside the directive's reach. jinja2's `as_const`
    extraction had put two `attr-defined` errors at its call sites this way.
    No directive is copied onto a call;
  - a region or file directive that would reach past the moved code
    (`directive_outlives_block`);
  - a coverage pragma or pylint `disable` around every site's block that
    would not reach the helper (`directive_around_block`). A module helper
    for pygments' `__main__`-only functions took `_lua_builtins.py` from
    100% to 40% line coverage;
  - a block whose first statement coverage excludes, since the call that
    replaces it would be measured (`excluded_block_start`).

  Explanatory comments from every site are kept, the first site's first.
- Helpers are shared across modules only with `--cross-module`
  (`cross_module_helpers=True` for library use), because a user
  deduplicating a package may not expect Towel to add imports between its
  modules. By default duplicates pair only within a module, the pair budget
  is spent only on pairs a run can form, and Towel writes no import between
  the project's modules that runs; an annotation that needs another module's
  type still gets a type-only import under `if TYPE_CHECKING:`. With
  `--cross-module`, `dry` and `preview` name the import problems they meet,
  with the `--exclude` remedy, and refuse a run when one concerns the package
  being refactored, such as a stale `build/lib` copy of it; `preview` accepts
  `--exclude`. Over click, rich, packaging and pygments, the default drops
  exactly the cross-module helpers (0, 3, 5 and 13 of them).
- Code that calls zero-argument `super()` is extracted into a method helper
  of the class that holds both duplicates, where it resolves as it did,
  diamonds and subclass overrides included: the helper has the class's own
  `__class__` cell and the method's receiver. Where no such helper can be
  written the pair is declined, never given a module function
  (`needs_class_body`, `super_in_call`). Output over click, rich, packaging
  and pygments is unchanged.
- A long run says what it is doing. Verifying a proposal the project rejects
  advances no counter, so the progress bar could stand still for minutes,
  which is indistinguishable from a hung run; the display is now redrawn while
  a proposal is being weighed and names the proposal it is weighing.
- `towel dry` reports what a typed run involves before asking to proceed: how
  many files a candidate is verified against, how many third-party packages
  their import graph pulls in, and that the number of checks depends on how
  many proposals the project rejects. It states no duration, that being
  governed by the rejections and so unknown in advance.
- Pyright is consulted through one long-lived language server per project and
  a persistent private copy, instead of a fresh `pyright --outputjson` and a
  fresh copy per check. The command line remains the fallback and reaches the
  same verdicts.
- Every configured checker must accept a candidate, so the first rejection is
  the verdict and the remaining checkers are not asked. Verdicts are remembered
  per candidate and project state.
- An unannotated helper is checked only when every error of the all-`Any`
  rejection lies inside the helper's own definition. To a caller the two are
  the same function, so an error anywhere else survives the change.
- A proposal the project declines is remembered for the whole run instead of
  until the next analysis. When nothing more applies and something was
  declined that the project has changed under since, the run holds a
  rehearing: the memory is cleared and the whole project analysed once more,
  so every declined proposal is heard against the project as it now stands.
  Only a rehearing that applies nothing ends the run, which is a stronger
  guarantee than before, when the terminating analysis was restricted to the
  files that had changed. Of the 382 extractions an uncapped Sphinx run
  considers, 24 had every annotation variant refused and not one of those was
  ever accepted at a later analysis.
- A typed run that was checked through a language server is confirmed once
  more at the end by a pyright started from nothing, sharing none of the
  session's state. A session that fails part way through a run still earns
  that confirmation, being exactly when the verdicts already given are worth a
  second opinion, and a confirmation that cannot run refuses the run rather
  than reporting success. A language server answers when it has gone quiet, and
  while a marker it must publish first keeps silence from being read as a
  verdict before it has begun, one cold check over the finished project turns
  any residue of that kind from a silent wrong answer into a loud one. About
  ten seconds on Sphinx, and no flag: a guarantee that holds only when asked
  for is not one.

### Preserving type relationships

An extracted helper used to lose the relationships among its types. Where two
call sites passed `int` and `str`, the shared parameter became `int | str` and
so did the return, which says nothing about the two travelling together: the
helper could be read as taking an `int` and returning a `str`. Towel now
anti-unifies the types alongside the code, so the helper's signature carries
the correlation the call sites had.

### Added
- `--parameterize-builtins` (`parameterize_builtins=True`), off by default:
  where a builtin that duplicated code reads may differ between its sites (a
  function's own `len` against the builtin, or with `--cross-module` a module
  that may hold the name), each site passes its own binding to the helper as
  a parameter instead of the pair being declined. A builtin every site reads
  alike is still read directly. In typed code the parameter keeps the
  checker's signature where it can be written, else `type[C]` for a class
  (`type[str]`) or `Callable[..., R]` for a callable (`Callable[..., int]`
  for `len`).
- Extracted module-level helpers can preserve relationships among argument and
  return types through type anti-unification. Nested containers, multiple type
  parameters, and supported existing generic binders receive fresh helper type
  parameters. Concrete disagreements can use constrained `TypeVar` declarations
  when an unrestricted generic body does not type-check.
- Generic signatures, their imports, and their declarations are checked with
  the complete prospective project and committed as one transaction. Failed
  attempts leave no declarations behind. Generated syntax remains compatible
  with Python 3.11; existing PEP 695 input requires Python 3.12 or newer.
- Generic instance and class helper methods preserve argument/result
  relationships and retain the host's type parameters. A module-level helper
  shared by methods that never read their receiver infers fresh parameters
  from its explicit arguments. Fresh declarations precede the host class at
  module scope, preserving descriptor behavior and declaration rollback.

### Fixed (release harness)
- The corpus runs Towel the way a user runs it: as `towel` inside each
  project's own environment. That environment holds the project installed
  editable from the tree under test, Towel's `format` and `types` extras,
  and one candidate wheel, checked against `--towel-src` in every
  environment. mypy, pyright, Black, ruff and isort are taken at the
  versions the project pins: its lock file first, then the `rev` of the
  tool's `.pre-commit-config.yaml` hook, then an exact pin in a requirements
  file (httpx's `requirements.txt` has `mypy==1.17.1`). The environment also holds whatever the project declares its
  own type check needs: typing-named dependency groups and extras, the
  dependencies of tox environments and nox sessions that run a checker, its
  pre-commit mypy and pyright hooks' `additional_dependencies`, and
  requirements files named for typing. That install only adds; nothing
  already there may change. Towel had run in the harness's own interpreter,
  which held Towel's tools and none of the project's dependencies. mypy
  therefore reported a project's third-party imports as errors: packaging's
  1158 errors are 14 in its own environment and none once its typing
  dependencies are installed, and its typed run now completes. The import
  model found the tools' own copies of click and packaging, and called those
  projects' names ambiguous. Each result records the interpreter, the
  candidate, every tool's version and what chose it, and what was installed
  for typing.
- The project's editable install follows the tree each test run exercises:
  the clone for the baseline, the refactored copy for Towel and the run
  after it, and the original package for a retest of the original. A test
  that imports the installed copy therefore tests the same code as the rest
  of its run. The run after refactoring had reached the unrefactored clone
  that way, so a regression there could pass as flaky.
- Every corpus refactor passes `--cross-module`. A manifest entry turns it
  off only with a `cross_module_reason`, and the summary names each such
  project.
- The sdist no longer ships a built wheel: `recursive-include scripts *`
  picked up the corpus image's copy whenever one had been built there.
- Two runs that failed *different* tests can no longer both count as a PASS. A
  failure line is `FAILED <node id> - <message>`, and the id was taken by
  splitting at the first `" - "` -- which a parametrized id may contain, so
  `test_case[same - before]` and `test_case[same - after]` both truncated to
  `test_case[same`. The harness compares those sets to award PASS, so a failure
  Towel introduced could hide behind a pre-existing failure in the same
  parametrized test. Only a separator outside brackets ends the id now.
- A phase that times out takes its descendants with it. Killing the immediate
  process left a suite's own workers, a server it started or a build it spawned
  running, writing into the scratch tree that the project's later phases read.
  Each phase leads a process group ended with it, and the worker watchdog ends
  those groups too.
- The corpus runs the default type policy and reruns without types only the
  projects Towel declines to verify, naming them and their reason in the
  report; their verdicts are evidence about the untyped path alone. The refusal
  is first held to its wording -- the count, a diagnostic naming its file, and
  the way forward -- and one that fails that is a verdict of its own rather
  than a project quietly skipped.
- The ecosystem check gives each corpus project its own output directory.
  Towel writes its recovery journal to the common parent of the files a
  transaction changes and refuses to start beneath a pending journal that may
  cover its targets. A one-module project's single output file put that journal
  directly in the shared work directory, an ancestor of every other project, so
  concurrent projects refused each other at `--workers 4`. The runtime is
  unchanged; its refusal was correct.

### Notes
- Towel keeps a precise ordinary signature when one verifies against the whole
  project, and reaches for a generic signature only when that ordinary
  signature does not. A generic candidate is therefore an alternative to losing
  the annotation, not a replacement for an exact one.
- Declined by design, with no weakening of the annotations that do ship: a
  helper hosted inside a function, variadic type parameters, a source method
  whose `self` or `cls` carries an explicit annotation, and evidence that
  resolves to `Any` or an unknown name.
- A fresh parameter whose bound mentions another helper parameter (`U` bounded
  by `list[T]`) is declined; an ordinary module `TypeVar` bound cannot depend on
  another function's binder. Where a constrained generic function makes mypy
  report several specializations for one probe, the evidence is treated as
  ambiguous rather than resolved arbitrarily. Both are recorded in
  [the type-parameter design](docs/proposals/type-parameters.md).

## [1.732.post1] - 2026-09-19

### Fixed
- README documentation links use absolute URLs pinned to the release tag, so
  they resolve from PyPI as well as GitHub. The Python implementation is
  unchanged from 1.732.
- Regression checks reject repository-relative README links and stale release
  tags in documentation URLs. The release procedure verifies GitHub source,
  documentation, and CI before uploading packages to PyPI.

## [1.732] - 2026-09-19

Changes since 1.618.

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
  `[tool.ruff]`, `ruff.toml` or `.ruff.toml` is present and ruff is
  installed, otherwise Black with the project's own line length and string
  quoting. Only the
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
  for a project with `[tool.mypy]`, `mypy.ini`, `.mypy.ini` or a `[mypy]`
  section in `setup.cfg`, pyright for one with `[tool.pyright]` or
  `pyrightconfig.json`; for a project configuring both,
  mypy infers while both verify the prospective project for new errors.
  For inference, Pyright probes a temporary sibling copy of the module;
  mypy reveals expressions in an in-memory copy of the site's module, at
  the point where the call will stand, using an owned worker with incremental
  caches. Verification checks the modified files together with unchanged
  consumers under the project's configured scope. A revealed type is written only
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
- With a clean original type-checking baseline, helper extractions and existing
  function reuse check all modified files together in the prospective project,
  including unchanged consumers. New errors make generated helper annotations
  fall back to `Any`, then to none; every variant must pass. Reused functions
  keep their existing signatures, and incompatible calls decline the reuse.
  Checker failure or remaining new errors decline the proposal. Cross-file
  helper imports no longer lose valid annotations because their host was stale.
- Before oracle inference or verification, the original complete project is
  checked. If it already has type errors, Towel aborts and asks the user to
  fix them or explicitly rerun with `--no-types`. That option preserves
  existing source annotations and generates unannotated helpers. Checker
  crashes and timeouts remain distinct failures; checking is never silently disabled.
  A clean baseline keeps verification enabled for subsequent proposals.
- A [proposal for type-parameter inference](docs/proposals/type-parameters.md)
  records how generic helpers could preserve relationships among arguments
  and return values. This work is deferred beyond 1.732.
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
- The candidate pairs an analysis evaluates are bounded by a configurable
  candidate-pair budget (`--max-pairs`, default 20,000,000; past it the
  largest groups of similar blocks are left out with a warning), and the
  minimum block length (`--min-lines`) and helper parameter limit
  (`--max-parameters`) are exposed on the command line.

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
- In directory mode, a global re-pass after the first re-pairs functions in
  rewritten files and files with deferred verification refusals. Deferred
  proposals are retried only after the project changes, since verification can
  depend on unchanged consumers elsewhere in the project. Other unchanged
  pairs retain their verdict under the conditions explained in
  `docs/ARCHITECTURE.md`; `incremental_global_passes=False` restores full
  re-pairing.
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
  `BlockAnalysis` mixin of their own and the engine core is about 800 lines;
  the unifier is split the same way (`UnifierState` under constant consistency,
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
  functions took 17 s and 100 took 134 s at 1.618, and 6.8 s and 36 s at
  commit a0596f0 (September 18, 2026, one core of a machine shared with
  other work); measured with `scripts/bench_similar_blocks.py` at commit
  5ff2458 (September 19, 2026, one core), 50 take 4.0 s and 100 take
  15.9 s, with identical output. Orphan detection, the instantiation
  check's normalized block and the class-private-name scan are memoized
  on structure, so the re-parse after each applied proposal hits too. The
  analysis session grows to the number of files and the source bytes an
  analysis covers, so a project above 128 files or 8 MiB of source no
  longer re-parses everything on every pass (trio's second pass: 144
  parses and 7.2 s, now none and 5.0 s). Both insertion-position parses
  go through the engine's parse memo.
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
  `TowelError` (and is still a `ValueError`); `towel.project_layout` is a
  top-level module (the old path
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
- Pair evaluation keeps one proposal per distinct refactoring (the same
  helper over the same clustered sites, whichever pair found it first)
  instead of every pair's copy. A file of sixty near-identical functions
  under the pair budget peaked at 33.6 GB holding the copies and now peaks
  at 0.74 GB; the output is the same, since the overlap filter never chose
  a later copy.
- A proposal whose helper, home and call sites repeat an earlier pair's is
  declined (`duplicate_proposal`) before the reuse redirect, the forwarder
  filter and annotation run on it; the clustering scan's key no longer
  carries names the template block never reads, which made it differ per
  function position; and the function lookup every clustered site makes is
  memoized. The clustering scan cache is bounded by the sites it holds
  (200,000) as well as by its entry count, since a scan of a file of a
  thousand near-identical functions holds a thousand sites. Such a file,
  which the module-name rule now admits where the module-data rule used to
  decline every pair in seconds, is evaluated in full: a thousand
  near-identical five-line functions reading one module global take 33
  minutes on one core (commit `5ff2458`, September 19, 2026) and yield one
  helper with 669 sites, peaking at 6.6 GB (8.8 GB before the scan cache
  was bounded); its pair evaluation is what `--max-pairs` bounds. A
  per-candidate memo beneath the scan, which no run ever read, is gone.
- The verdict of the instantiation check is memoized on the helper, call
  and block, which it repeated many times over across pair and cluster
  evaluation: a file of a hundred similar functions took 31 s instead of
  43 s when the memo landed (commit 77d18a8); the current figure is in the
  clustering entry above.
- The `format` extra's Black floor is the version the goldens were
  generated with (`black>=26.3.1`, the same floor as the `dev` extra); it
  was two majors lower, so a user at the extra's floor could get output the
  goldens do not show. The sdist no longer ships the `.templates` scratch
  directory or two debugging scripts.
- The help says what the commands do: usage lines read `towel`, each
  boolean option's help names its default, the retired spellings are left
  out, and `preview` no longer promises per-phase summaries under
  `--progress detail`, which only the fixed-point driver emits.
- The structural memo is keyed on a block's structure, so equal blocks share
  one entry wherever they appear.

### Deprecated
- The `--max-iterations`, `--non-interactive` and `rename-helpers
  --dry-run` spellings still parse but are left out of the help; use
  `--max-refactorings`, `--no-interactive` and `--preview`.

### Removed
- `nominal_unifier.py`, which nothing imported, and the TOML backport for
  Python 3.10, below the supported floor, with its `tomli` dev dependency.
- `ImportGraphCache.clear`, which nothing called: the cache is built per run
  and dropped with the engine.

### Fixed
- The ecosystem release gate requires completed, nonempty test runs. Setup
  failures, incomplete runs, unknown verdicts, changed failing-test identities,
  and missing tests cannot pass merely because exit codes or totals match.
  Documented known failures and flaky-test reruns cannot hide a changed test
  count; matching pre-existing failures remain visible in the report. Pytest
  commands request complete tallies and failure identities; custom runner
  failure statuses must be declared, and retests retain their actual statuses.
  Retests remove broad selectors wherever they occur and require the exact
  selected test count; ambiguous command options decline the retest. Agreement
  on isolated tests requires confirmation with the complete original test
  command, so test-order regressions cannot disappear from the check. Failed
  Git observations cannot become `NO_CHANGE`, and added, deleted, or renamed
  paths count as changes. Source provenance names the checkout actually used;
  archives explicitly require a separately retained source manifest.
  The corpus manifest supplies Cheroot's declared test plugins and SimPy's
  benchmark fixture, and runs PLY's test scripts from the project root so each
  suite imports the source tree under test.
  The harness records its requested typing mode. Its explicit `--no-types`
  option supports behavioral validation of projects without a complete typing
  environment; it never retries a failed typed run by silently opting out.
- The worker-cleanup regression fails when process inspection fails, instead
  of treating an empty result from a failed command as proof that workers exited.
- Mypy runs in an owned persistent worker with incremental caches and periodic
  garbage collection. Its process-global state cannot freeze or unfreeze a
  library caller's heap, concurrent requests are serialized, and explicit
  cleanup covers both combined checkers and failed optional-dependency setup.
  The CLI preserves input-project configuration while checking the copied
  output under its original module identities, and closes the checker on
  success and failure. Project type rules are honored while plugins,
  configured executables and report destinations remain disabled.
  (1.772 replaced that periodic collection: each build now runs in a forked
  child of the worker which exits when it has answered.)
- Mypy diagnostic presentation is normalized for in-memory sources. A
  project's `pretty = true` setting no longer crashes when a subtype probe
  or prospective helper extends beyond the on-disk file; project type rules
  remain unchanged.
- Checker outcomes distinguish infrastructure failure from valid diagnostics.
  A pyright timeout, malformed output or failure is never evidence of a clean
  project or a successful subtype relation.
- Lexical binding analysis recognizes PEP 695 type parameters and shadowed
  builtins, preserving generic identity and conditional-name evaluation.
- Ruff subprocesses exclude the current project from Python's import path.
  Import sorting preserves ordered providers of shared bindings and statement
  boundaries. Permanent filesystem conflicts stop instead of retrying forever.
  Directory mode also terminates after all proposals in an unchanged project
  fail rendering or type verification, and retries deferred proposals after
  another successful change can alter their checking context.
- Helper renaming preserves builtin reads, keyword callers, exact selection
  filters and Python protocol behavior. Static aliases and re-exports are
  tracked; ambiguous callable/module aliases, visible reflection and dynamic
  keyword dictionaries are refused before writing. Source discovery prunes
  environments and excluded directories and reports unreadable consumers.
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
  definition, raised the same way. Both are now read where the block read
  them: a same-module helper reads the name bare (next entry), and a
  cross-file helper takes it as a thunk.
- A free name that both sites resolve at module scope, or nowhere, is no
  longer a parameter of a same-module helper: the helper reads it bare,
  which is the lookup the block made, at the moment the block made it. A
  helper defined below its callers, a class, an import, or module data a
  callback rebinds between two reads (previously declined as
  `module_data_lookup` or `rebound_external_binding`) all extract, with
  fewer parameters and no thunk; hyper-h2 loses seven such parameters and
  gains three extractions, and its own 1,662 tests pass on the output. A
  clustered occurrence whose same-spelled name is a local does not join;
  cross-file helpers still take the name as a parameter, and module data
  a callback may rebind still declines a cross-file pair. So does a
  same-file pair whose helper is hosted in a shared ancestor class defined
  in another module: the pair is decided again with every name a
  parameter, since the ancestor's module need not import them (the
  release-candidate ecosystem run found oauthlib's `BearerToken` read bare
  in `introspect.py`, which raised NameError in 55 tests). `__class__`, the
  cell zero-argument `super()` reads, names the defining class and stays a
  parameter of a method helper.
- A call to a function or method of the same module whose body reads a
  frame relative to its caller (`sys._getframe(n)`, `inspect.stack()`, a
  `stacklevel=`), directly or through other such functions, is a frame read
  at its call site, and the block stays where it is. The module-name rule
  had admitted blocks that typing_extensions' `_caller()` reads through,
  which a reflection guard had declined only by accident, and six
  `TypeAliasType` pickling tests failed in the release-candidate ecosystem
  run.
- A call site that would pass a callee as
  `lambda *args, **kwargs: callee(*args, **kwargs)` is declined by name
  (`forwarded_callee`). It used to be declined by accident, because the
  lambda's own parameters were counted as names the site could not resolve;
  that miscount also declined any thunk containing a lambda, such as
  `lambda: sorted(items, key=lambda x: x)`, which now extracts.
- A comprehension's target shadows only its own expressions: `[i for i in
  xs]` used to become `[__param_0() for i in xs]` while a free read of `i`
  after the comprehension stayed literal.
- The frame-reading builtins, `eval`/`exec`, `sys._getframe` and
  `inspect.currentframe` anywhere in the enclosing function decline the
  block, not only inside it, and their aliases, imported or assigned
  (`e = eval`, `warn = warnings.warn`, `gf = sys._getframe`, an alias of an
  alias), are resolved through the module's bindings.
- `warnings.warn` without `stacklevel`, and a `stacklevel` reached through
  an alias, decline the block.
- An object a class instantiation or a resource factory (`open`,
  `connect`, `socket`, `mkdtemp`, `Popen`, `urlopen`, ...) binds in the
  block is returned from the helper rather than dropped when the helper's
  frame ends, since a later read may observe its lifetime (a temporary
  file, a weak reference).
- A helper import goes after the module's last leading import, before its
  first definition (after the docstring when there are no imports), so a
  script that runs a statement before its imports keeps that statement
  first. A host for a cross-file helper is accepted only when the borrower
  already imports it or every module the new import would load is
  definition-only, so a helper import cannot run a module that prints at
  import time (`xf13_import_time_effects` is declined).
- A form feed or a Unicode line separator in a source no longer crashes the
  splice: lines are counted the way the tokenizer counts them.
- Async comprehensions are declined.
- A rendering failure in one proposal ends that proposal, not the run.
- A relative output path no longer ends the fixed point early.
- Type inference no longer switches off when the working directory is
  above the output.
- A pending transaction journal blocks a run only when its manifest names
  a file the run would change (a journal without a readable manifest
  blocks everything beneath it), and journals of concurrent runs never
  share a name.
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
  two are declined; the third follows the shared-free-variable rule above.
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
- A non-integer or non-positive `TOWEL_WORKERS` is reported, and a
  configured worker count never reaches a platform without `fork`.
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
- With mypy, a union whose members the checker could not all order (some
  verdicts unanswerable: A under B, B under C, C under A) let every member
  absorb every other, emptied the union, and the join raised `IndexError`
  (sphinx, after eighteen minutes of refactoring). A member now absorbs
  another only on a definite `YES`, a normalization that would keep nothing
  keeps the union as it was, and a join with no members writes no
  annotation.
- A function preceded by `@overload` stubs is a valid reuse target: the
  reuse check verifies the last module-level definition of the name, which
  is the runtime binding the calls resolve to, instead of refusing a name
  defined more than once (rfc3986's `normalize_query`).
- A type mypy reveals with the path of an out-of-place output directory
  that is not an identifier (`h2-dbg.stream.H2Stream`, which parsed as a
  subtraction and was written as `self: h2 - H2Stream`) is rewritten with a
  placeholder for that component, and a revealed type is accepted only when
  its tree is made of names, attributes, subscripts, tuples, constants and
  unions.
- `rename-helpers` with a rename file it cannot open or parse exits 1 with
  `Error: ...` on stderr like every other command failure; it used to print
  the failure to stdout without the prefix.
- Invariants the engine enforced with `assert`, which `python -O` removes
  (a helper without a host name, a node without an end position), are
  exceptions that survive `-O`; a rendered module that does not parse
  raises `RefactoringError` naming the parse error instead of being dropped
  as "no insertion point"; and when applying a change fails, the cleanup's
  own error no longer hides the failure that triggered it. The cross-file
  reuse redirect follows the same import-time rule as cross-file helpers: a
  site is not redirected to a function in another module when the import
  would make the site's module load one that runs code at import time.
- Ctrl-C prints one line instead of a traceback, and a reader that closes
  the pipe ends the run quietly; `preview` and `rename-helpers` warn about
  a pending journal and an in-place `dry` refuses before analysing; two
  source paths that differ only by case are refused before an out-of-place
  copy would merge them on a case-insensitive volume; a virtual environment
  inside the input is recognized by its `pyvenv.cfg` whatever it is named;
  the inline progress bar goes to stderr like tqdm's, so redirected output
  stays clean, and a run that asked for tqdm without it installed says so
  once.
- A single-file run prints the frame-sensitivity warning that directory
  runs already printed when a file reads `locals()`, `eval` or the frame.
- A `pyproject.toml` that does not parse is reported (and no layout
  information is taken from it) instead of being read as empty.

### Security
- The pyright oracle starts `python -I -m pyright`. It runs from the
  module's directory, and `-m` put that directory first on `sys.path`, so a
  project package named like a standard module was imported and executed
  in its place: sphinx's `locale` package ran when pyright's launcher
  imported `subprocess`, and pyright produced nothing for flask, pytest,
  structlog, sphinx, trio and werkzeug in the release-candidate ecosystem
  run.
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

[Unreleased]: https://github.com/ericeallen/towel/compare/v1.772...HEAD
[1.772]: https://github.com/ericeallen/towel/compare/v1.732.post1...v1.772
[1.732.post1]: https://github.com/ericeallen/towel/compare/v1.732...v1.732.post1
[1.732]: https://github.com/ericeallen/towel/compare/v1.618...v1.732
[1.618]: https://github.com/ericeallen/towel/compare/v1.414...v1.618
[1.414]: https://github.com/ericeallen/towel/releases/tag/v1.414
