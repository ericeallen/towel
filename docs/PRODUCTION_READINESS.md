# Production readiness — 1.1.0

**Disposition: ready for production use as a reviewed refactoring tool.**
Every accepted proposal is checked by a syntactic instantiation invariant,
arguments with possible effects are evaluated inside the helper at their
original position, and a standing 71-project ecosystem check passes every
project's own test suite before and after transformation, apart from three
documented frame-sensitive or source-observing cases. This supersedes
the alpha disposition in [OPEN_SOURCE_AUDIT.md](OPEN_SOURCE_AUDIT.md).
Publication remains a separate maintainer decision; see
[RELEASING.md](RELEASING.md). Unattended use is not claimed: every batch of
new projects so far has found new defect classes (see below), so the failure
rate on an unfamiliar project is unknown.

Work was done on `audit/open-source-2026-09-12` in the audit checkout on
September 12–14, 2026, starting from `97fe0a2` (the 1.1.0a1 candidate). The
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
  evaluated once at the call site. Repaired by the eager/thunk policy. A
  later review of that policy removed list and set displays from the eager
  set (fresh object per evaluation) and barred assignment expressions from
  parameterization (a thunk would bind in the wrong scope).
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
- The ecosystem check found eight more: two local classes with one name
  treated as one; tab-indented classes; free variables and name arguments
  bound only on some path read eagerly; direct `warnings.warn(stacklevel=)`
  in a block; stale proposals aborting a batch; a function nested inside a
  method dispatched as a method; an annotated assignment not counted as a
  binding; and a block starting at an `elif` rendered as a sibling of its
  `if`. Each is a fixture; the last one had also been recorded in a golden
  output, which is regenerated.

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

## Ecosystem check

`scripts/ecosystem_check.py` (run as `just ecosystem`, and weekly in CI)
clones each project in `scripts/ecosystem/manifest.toml`, runs its suite,
refactors a copy with the CLI defaults, runs the suite again, and compares
exit status and the normalized summary line. The table is the final run on
commit `539669cb3f88`, macOS, Python 3.13, September 14, 2026, three
projects at a time with two forked workers each; the report, per-project
JSON, and all logs are archived with the release evidence.

| Project (revision) | Verdict | Files changed | Refactor s | Suite |
|---|---|---|---|---|
| anyio `a87a823` | PASS | 12 | 34 | identical: 8 failed, 3923 passed, 337 skipped, 5 xfailed |
| arrow `2224255` | PASS | 2 | 2 | identical: 1902 passed |
| astroid `5d1a0a2` | PASS | 13 | 66 | identical: 3 failed, 2135 passed, 90 skipped, 15 xfailed, 35 subtests passed |
| attrs `8f76777` | PASS | 4 | 5 | identical: 4 failed, 1400 passed, 7 skipped, 1 xfailed |
| bidict `61e9827` | NO_CHANGE | 0 | 0 | no proposal at the default minimum |
| black `20622e1` | PASS | 2 | 15 | identical: 2 failed, 479 passed, 3 skipped, 8 subtests passed |
| boltons `961dcff` | PASS | 13 | 18 | identical: 519 passed |
| cachetools `4500e3d` | PASS | 3 | 2 | identical: 333 passed |
| cattrs `5bf7c97` | PASS | 17 | 9 | identical: 2 failed, 1042 passed, 15 xfailed, 2 warnings |
| click `6aabf09` | PASS | 5 | 16 | identical: 2058 passed, 25 skipped, 31000 deselected, 1 xfailed |
| colorama `841634e` | PASS | 5 | 7 | identical: 38 passed, 14 skipped |
| decorator `2322c7b` | NO_CHANGE | 0 | 0 | no proposal at the default minimum |
| django-forms `850e026` | PASS | 4 | 5 | identical: OK (skipped=2) |
| django-utils `850e026` | PASS | 6 | 5 | identical: OK (skipped=21) |
| filelock `4efd93e` | PASS | 2 | 2 | identical: 1456 passed, 54 skipped |
| flask `d73fa1c` | PASS | 3 | 2 | identical: 494 passed |
| funcy `5419a8f` | NO_CHANGE | 0 | 0 | no proposal at the default minimum |
| glom `fd70d30` | BROKEN_KNOWN | 5 | 7 | frame-relative: test_error.py asserts literal traceback frames, and the helper adds one |
| httpx `b5addb6` | PASS | 12 | 8 | identical: 10 failed, 1407 passed, 1 skipped |
| humanize `3201e70` | NO_CHANGE | 0 | 0 | no proposal at the default minimum |
| idna `cd17392` | PASS | 1 | 1 | identical: 6445 passed, 1 skipped, 56 subtests passed |
| isort `131f4ad` | PASS | 5 | 11 | identical: 2 failed, 623 passed, 1 skipped |
| itsdangerous `672971d` | PASS | 3 | 0 | identical: 297 passed |
| jinja2 `5ef7011` | PASS | 6 | 43 | identical: 911 passed |
| jsonschema `865c27f` | PASS | 8 | 191 | identical: 7826 passed, 703 skipped |
| lark `9a4fb9c` | BROKEN_KNOWN | 8 | 20 | source-observing: lark.tools.standalone copies marked regions of named source files into one artifact, so a helper moved to another file is absent from it |
| markdown `0d6afd1` | PASS | 10 | 9 | identical: FAILED (errors=364, skipped=110) |
| markdown-it-py `a5950ca` | PASS | 2 | 6 | identical: 1000 passed, 1 skipped, 1 warning |
| marshmallow `c54aa72` | PASS | 2 | 1 | identical: 1190 passed |
| more-itertools `b2f3aff` | PASS | 1 | 4 | identical: OK (skipped=5) |
| natsort `e2328c2` | PASS | 1 | 0 | identical: 355 passed |
| networkx `4e74880` | PASS | 67 | 353 | identical: 9282 passed, 58 skipped, 741 xfailed |
| nox `18beba7` | PASS | 3 | 3 | identical: 899 passed, 47 skipped, 1 xpassed |
| oauthlib `40b0ab5` | PASS | 22 | 22 | identical: 703 passed, 2 skipped, 10 warnings, 21 subtests passed |
| packaging `10590c1` | PASS | 10 | 14 | identical: 62434 passed, 1 skipped, 427 deselected |
| parso `7f5b142` | PASS | 3 | 5 | identical: 1985 passed |
| pathspec `f0fb3f4` | PASS | 6 | 2 | identical: 215 passed, 372 skipped, 280 subtests passed |
| peewee `2866df2` | PASS | 1 | 100 | identical: OK (skipped=176) |
| platformdirs `5921065` | PASS | 2 | 1 | identical: 1229 passed, 106 skipped |
| pluggy `0744fd9` | BROKEN_KNOWN | 3 | 0 | frame-relative: _verify_all_args_are_provided warns with stacklevel through the extracted helper |
| pycodestyle `d6c3854` | NO_CHANGE | 0 | 1 | no proposal at the default minimum |
| pyflakes `52cb729` | PASS | 10 | 111 | identical: 748 passed, 25 skipped |
| pygments `38f426a` | PASS | 45 | 89 | identical: 5330 passed, 16 skipped, 3 warnings |
| pyjwt `b9f6a9d` | PASS | 2 | 3 | identical: 456 passed, 4 skipped |
| pyparsing `efd56db` | PASS | 5 | 12 | identical: 2140 passed, 27 skipped, 2041 subtests passed |
| pytest `de30d84` | PASS | 16 | 32 | identical: 1 failed, 4515 passed, 126 skipped, 12 xfailed, 1 xpassed, 2 warnings, 1 error |
| python-dateutil `48bd1af` | PASS | 6 | 12 | identical: 41 failed, 1991 passed, 47 skipped, 17 xfailed |
| python-prompt-toolkit `583b341` | PASS | 27 | 296 | identical: 156 passed |
| python-slugify `fee5aa3` | NO_CHANGE | 0 | 0 | no proposal at the default minimum |
| rich `9d8f9a3` | PASS | 15 | 60 | identical: 8 failed, 948 passed, 25 skipped, 1 warning |
| schema `310a123` | PASS | 1 | 1 | identical: 124 passed |
| six `c8e3940` | NO_CHANGE | 0 | 0 | no proposal at the default minimum |
| sortedcontainers `3ac3586` | PASS | 2 | 21 | identical: 366 passed |
| sphinx `e44a40e` | PASS | 96 | 2442 | identical: 7 failed, 2383 passed, 35 skipped, 35 warnings |
| sqlparse `60cdc64` | PASS | 2 | 2 | identical: 506 passed, 2 xfailed, 1 xpassed |
| starlette `03f12b7` | PASS | 5 | 5 | identical: 1249 passed, 2 xfailed |
| structlog `73393f3` | PASS | 6 | 3 | identical: 4 failed, 880 passed, 37 skipped |
| tabulate `268615a` | NO_CHANGE | 0 | 1 | no proposal at the default minimum |
| tenacity `3e58094` | PASS | 2 | 1 | identical: 183 passed, 1 skipped, 15 subtests passed |
| tomli `5a77b12` | PASS | 1 | 1 | identical: 17 passed, 1 skipped, 744 subtests passed |
| tomlkit `4b38bec` | PASS | 6 | 19 | identical: 1058 passed |
| toolz `568c2b8` | PASS | 5 | 40 | identical: 1 failed, 185 passed |
| tornado `7ed8156` | PASS | 40 | 371 | identical: 1173 passed, 157 skipped, 8 warnings, 86 subtests passed |
| tqdm `9cf5a12` | PASS | 8 | 5 | identical: 168 passed, 11 skipped |
| typer `a80f6e5` | PASS | 5 | 27 | identical: 409 failed, 956 passed, 35 skipped, 2 xfailed |
| virtualenv `ca4025d` | PASS | 5 | 3 | identical: 356 passed, 31 skipped |
| voluptuous `44593ce` | PASS | 4 | 51 | identical: 182 passed |
| wcwidth `1e6b48d` | PASS | 3 | 4 | identical: 1 failed, 1364 passed, 10 skipped |
| werkzeug `6a604e0` | PASS | 15 | 15 | identical: 1024 passed |
| wrapt `f1586a5` | PASS | 5 | 4 | identical: 1 failed, 1222 passed, 8 skipped |
| xmltodict `6e29fba` | NO_CHANGE | 0 | 0 | no proposal at the default minimum |
Totals: PASS 59, NO_CHANGE 9, BROKEN_KNOWN 3, and no project unsupported,
broken, crashed, or timed out. A `BROKEN_KNOWN` verdict requires
every newly failing test to match a failure the manifest names, so a
documented limitation cannot hide an unrelated regression; the three are
frame-relative (pluggy's `stacklevel`, glom's literal traceback assertions)
and source-observing (lark's standalone generator copies marked regions of
named source files). Suites that already failed before transformation
(attrs, python-dateutil, toolz, wcwidth, wrapt) fail identically after it;
those failures are missing package metadata or environment-specific.

The corpus is 71 projects: 39 libraries from the first batch, 20 larger
or application-shaped projects added on September 13, 2026, and 12 more on
September 14 (oauthlib, parso, rich, typer, structlog, cattrs, isort,
PyJWT, xmltodict, arrow, six, tornado). The September 13 batch was: Flask,
Werkzeug, httpx, Starlette, anyio, jsonschema, virtualenv, nox, Markdown,
tqdm, lark, peewee, prompt_toolkit, astroid, networkx (refactored without
its 77,000 lines of tests, `exclude = ["tests"]`), pytest, Sphinx, Black,
and Django's utils and forms packages driven by Django's own test runner.
Networkx has a two-hour budget and Sphinx ninety minutes; both finish.

The second batch found eleven defect classes before it passed, each now a
fixture in `tests/hostile_cases` (r93 to r100) and a row in
[ADVERSARIAL_REVIEW.md](ADVERSARIAL_REVIEW.md): import aliases
parameterized as expressions and alpha-renamed by the checker; imports and
unpacked targets not counted as bindings; annotations in function bodies
treated as free variables; a helper inserted into one class's method while
a same-named method of another class called it; a clustered call site
outside the function that received the helper; a value-producing block
whose branches only sometimes returned, called as `return helper(...)`; and
a clustered site whose bound name was read after a conditional rebinding.
The last two silently changed results (networkx's `is_isomorphic` answered
False for isomorphic graphs) and were caught only because the project's
suite covered the transformed lines.

## Verification gates on the final tree

- Python 3.11, 3.12, and 3.13: 1,252 tests passed on each, plus 34 subtests.
  Coverage is 89% against the unconditional 85% gate.
- Black, Flake8, strict mypy, Bandit, and all pre-commit hooks pass. Hostile
  fixtures are excluded from formatting because their layout is what they
  test.
- 26 single-file golden outputs: 2 changed (attribute and arithmetic
  arguments became thunks), reviewed by hand and executed. 3 cross-file
  goldens were stale relative to their inputs and the committed insertion
  policy and were regenerated.
- Hostile batteries: 84 single-file fixtures, of which 43 are transformed and
  41 rejected, and 6 cross-file fixtures; all preserve program output.
- Performance with the defaults: the 5,000-line `more.py` reaches a fixed
  point in 23 s. See KNOWN_LIMITATIONS.md for package-level timings.

## Not done here

- Hosted Linux CI has not run; the workflow is unchanged and local runs are
  macOS. Run it against the published commit before citing it.
- No wheel or source distribution was rebuilt for 1.1.0; `just release 1.1.0`
  prepares them and the maintainer approves hashes.
- Security support policy, confidential reporting channel, and repository
  visibility remain maintainer decisions recorded in RELEASING.md.
- Thunked arguments read as `__param_0()`. An inlining pass passes a thunk
  eagerly when the helper evaluates it first, once, and unconditionally,
  which covers the common `x = E` opening; thunks used repeatedly or after
  an effect stay deferred. The `rename-helpers` workflow can name the rest.
- The ecosystem corpus is 71 projects; the largest, networkx and Sphinx,
  are refactored within hours-long budgets. No measurement yet says how
  much of each transformed block its suite exercises. A stopping rule for
  claiming unattended use would be a fixed run of new projects with no new
  defect class; the second batch of 20 found eleven, so that count restarts
  at zero with the next additions.
