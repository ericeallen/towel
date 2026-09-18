# Changelog

All notable changes to Towel are recorded here. This file summarizes releases;
[docs/RELEASE_LOG.md](docs/RELEASE_LOG.md) keeps the detailed engineering log,
and [docs/PRODUCTION_READINESS.md](docs/PRODUCTION_READINESS.md) records the
ecosystem evidence behind each claim. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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
  Construct the engine with `reuse_existing_functions=False` to restore the
  old behavior.
- `towel dry` formats the code it inserts with Black when Black is installed
  (`pip install "code-towel[format]"`), using the project's own `[tool.black]`
  line length and string quoting. Only the generated helper and the rewritten
  call statements are formatted, never the surrounding file, and each snippet
  is checked to have the same syntax tree before and after. Pass `--no-format`
  to insert the unformatted rendering; without Black a note says so. Library
  callers pass any `snippet_formatter` callable to the engine.
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
- When mypy is installed (`pip install "code-towel[types]"`), the argument
  expressions and return values those copied annotations could not name are
  typed by mypy: `towel dry` reveals each one in an in-memory copy of the
  site's module, at the point where the call will stand, once per applied
  refactoring with an incremental cache. A type is written only when every
  site agrees, it contains no `Any`, and every name in it resolves where the
  helper is defined. `--no-types` leaves helpers unannotated; library callers
  pass a `type_inferrer` to the engine. Sites that disagree on a parameter's
  type, or on a revealed return type, join into a union (`int | None`,
  `int | str`); declared return types must agree, since a union is not a
  lower bound. Once a helper carries any annotation, whatever is still bare
  becomes `Any`, so the signature is complete (a partial one is an error
  under mypy's `disallow-incomplete-defs`); `from typing import Any` is
  added to the host when it lacks it. Code with no annotations stays bare.
  With mypy installed the subtype relation is mypy's own, asked through
  probe functions appended to an in-memory copy of the host module: unions
  are normalized by it (`int | bool` is `int`, `float | int` is `float`, a
  subclass under its base disappears), the declared return types' meet is
  found through it, and the helper's revealed return type is written only
  when mypy confirms it is a subtype of every site's declared return type,
  which is what keeps the sites type-checking. Without mypy a syntactic
  relation (identity, union membership, the numeric tower, `object`) stands
  in.
- Analysis facts are computed once per function instead of once per candidate
  block (definite assignment, locally bound names, nested scopes), and the
  same-file clustering pass applies its constant-time filters before the
  semantic guards. Together these remove about half of the AST traversal on
  a 16k-line project with an identical proposal list.
- The trivial-forwarding filter also recognizes `name = call(...)` followed
  by `return name`, and the tuple form `a, b = call(...)` then
  `return (a, b)`. Without it, once Black wrapped such a body over the
  three-line minimum, two generated helpers of that shape paired with each
  other and extracted a third, without end (h2).

### Fixed
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
- Definite-assignment analysis now knows that `except E as name` deletes
  `name` when the handler exits, and that a `del` nested in a branch unbinds
  its target on that path. The returned-variable check relies on this; without
  it a helper could return a handler's name and raise `UnboundLocalError`
  where the original code did not.

## [1.618] — 2026-09-17

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

## [1.414] — 2026-09-15

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
