# Production readiness — 1.1.0

**Disposition: ready for production use as a reviewed refactoring tool.**
Every accepted proposal is checked by a syntactic instantiation invariant,
arguments with possible effects are evaluated inside the helper at their
original position, and seven public projects pass their full test suites
before and after transformation. This supersedes the alpha disposition in
[OPEN_SOURCE_AUDIT.md](OPEN_SOURCE_AUDIT.md). Publication remains a separate
maintainer decision; see [RELEASING.md](RELEASING.md).

Work was done on `audit/open-source-2026-09-12` in the audit checkout on
September 12–13, 2026, starting from `97fe0a2` (the 1.1.0a1 candidate). The
original checkout was not touched. No push, tag, upload, or visibility change
occurred.

## What "production-ready" means here

A refactoring tool cannot prove behavioral equivalence for arbitrary Python;
reflection, frame inspection, and concurrent mutation are outside any static
model. The bar applied instead:

1. The transformation is a faithful generalization of every block it
   replaces, verified per proposal, not assumed from the algorithm.
2. Evaluation order, count, and conditionality of every expression are
   preserved by construction, or the proposal is rejected.
3. Every remaining limitation is stated in
   [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md), together with the rejections
   that enforce it.
4. Failures are loud: a generated file that does not compile or a call that
   does not bind aborts the batch and rolls back.
5. Real projects with real test suites pass before and after, under the
   command-line defaults, without editing their tests.

## Defects found and repaired in this pass

Executable counterexamples were written before reading the fix paths;
each is now a fixture executed by `tests/test_hostile_battery.py` or
`tests/test_hostile_crossfile_battery.py`. The full table is in
[ADVERSARIAL_REVIEW.md](ADVERSARIAL_REVIEW.md#second-review--production-readiness-september-13-2026).
In summary:

- Unification consistency: a name unified as equal at one position and
  parameterized at another was substituted everywhere. Repaired by the
  instantiation check, which subsumes the unifier's internal rules.
- Argument hoisting: attribute, subscript, operator, and call arguments were
  evaluated once at the call site. Repaired by the eager/thunk policy.
- Closure cells, deletion, `except ... as`, `global` declarations, and match
  captures each produced a helper that rebinds or reads the wrong variable.
  Repaired by new guards and scope-analysis support.
- Slices parameterized into unparseable thunks; opaque decorators produced
  method helpers with the wrong arity; clustering produced overlapping
  replacements; a second fixed-point pass reused a generated parameter name.
  Each was found by a consumer project and repaired with a visible check.
- Flit-packaged projects were refused; Flit layouts are now inferred.
- Live variables returned from a helper were ordered independently per
  call site, so a second caller could unpack them in the wrong order; a
  block with an early return plus live variables was rendered as an
  assignment; a cross-module import landed inside a docstring; and helper
  names collided across modules. All four came from boltons and are repaired
  with checks that fail visibly.

## Consumer evidence

All runs used the command-line defaults (`towel dry PKG PKG --non-interactive`,
minimum block of three lines) on a disposable copy, on macOS with Python
3.13.7, with the project's own full suite run identically on the original and
the transformed copy. Tests were neither selected nor edited. Upstream
revisions are the checked-out commits on September 13, 2026.

| Project (revision) | Transformation | Suite before | Suite after |
|---|---|---|---|
| tabulate `268615a` | 1 file, 11 insertions, 12 deletions, 6 s | 366 passed, 17 skipped | 366 passed, 17 skipped |
| toolz `568c2b8` | 18 extractions across 5 files, 350 s | 185 passed, 1 failed (`test_has_version`, needs installed metadata) | identical: 185 passed, same 1 failure |
| markdown-it-py `a5950ca` (Flit) | 2 files, 14 insertions, 23 deletions, 93 s | 1000 passed, 1 skipped | 1000 passed, 1 skipped |
| pyparsing `efd56db` | 5 files, 80 insertions, 87 deletions, 62 s | 2140 passed, 27 skipped, 2041 subtests | 2140 passed, 27 skipped, 2041 subtests |
| more-itertools `9ed3dbb` (Flit, directory mode) | 1 file, 41 insertions, 49 deletions, 30 s | unittest OK, 5 skipped | unittest OK, 5 skipped |
| boltons `961dcff` (Flit) | 13 files, 318 insertions, 343 deletions, 148 s | 519 passed | 519 passed |
| humanize `3201e70` (Hatch) | no proposal at the default minimum; with a two-line minimum through the API, one cross-file extraction in `number.py` and `time.py`, 5.8 s | 798 passed | 798 passed |

Under earlier engines in this pass, pyparsing raised `TypeError` in twelve
test modules, boltons crashed with `SyntaxError` and then `IndexError` and
later failed 56 tests, toolz failed on overlapping replacements, and the two
Flit projects were refused. Those are the consumer-found defects above.
Every row in the table was rerun on the final committed engine, and every
suite is identical before and after.

## Verification gates on the final tree

- Python 3.11, 3.12, and 3.13: 1,134 tests passed on each, plus 34 subtests.
  Coverage is 89% against the unconditional 85% gate.
- Black, Flake8, strict mypy, Bandit, and all pre-commit hooks pass. Hostile
  fixtures are excluded from formatting because their layout is what they
  test.
- 26 single-file golden outputs: 2 changed (attribute and arithmetic
  arguments became thunks), reviewed by hand and executed. 3 cross-file
  goldens were stale relative to their inputs and the committed insertion
  policy and were regenerated.
- Hostile batteries: 61 single-file fixtures, of which 26 are transformed and
  35 rejected, and 6 cross-file fixtures; all preserve program output.
- Performance with the defaults: the 5,000-line `more.py` reaches a fixed
  point in 23 s. See KNOWN_LIMITATIONS.md for package-level timings.

## Not done here

- Hosted Linux CI has not run; the workflow is unchanged and local runs are
  macOS. Run it against the published commit before citing it.
- No wheel or source distribution was rebuilt for 1.1.0; `just release 1.1.0`
  prepares them and the maintainer approves hashes.
- Security support policy, confidential reporting channel, and repository
  visibility remain maintainer decisions recorded in RELEASING.md.
- Thunked arguments make helpers less readable (`__param_0()`); the
  `rename-helpers` workflow can name them but a readability pass that inlines
  pure single-use thunks would be a worthwhile follow-up.
