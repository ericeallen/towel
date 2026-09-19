# Production readiness

**1.732.post1 is a documentation-only update to the 1.732 beta release.**
It fixes README links on PyPI and adds documentation-link regression checks.
The Python implementation and dependency pins are unchanged.

The runtime is frozen at `1d246075`; validated
source `2e4ebe6` adds test and ecosystem-harness corrections after that runtime.
The original 1.732 release commit adds documentation updates only; runtime, tests, harness,
dependencies, and build configuration remain unchanged from `2e4ebe6`.
The current evidence below is separate from the historical 1.732 and 1.414
runs retained later in this report. Passing the sampled tests does not
establish equivalence for arbitrary Python programs; review generated changes
and the [known limitations](KNOWN_LIMITATIONS.md).

## Current 1.732 validation (September 19, 2026)

The completed source matrix at `2e4ebe6` recorded:

| Python | Passed | Skipped | Subtests passed | Coverage |
|---|---:|---:|---:|---:|
| 3.11 | 2,534 | 11 | 34 | 93% |
| 3.12 | 2,545 | 0 | 34 | 93% |
| 3.13 | 2,545 | 0 | 34 | 93% |

The 3.11 skips exercise PEP 695 syntax that requires Python 3.12 or later.
All three runs completed without warnings. Black, Flake8, strict mypy, Bandit,
and the lockfile check also passed. Commands, tool versions and full logs are
retained in the accompanying September 19 audit evidence archive.

The audit fixes cover generic binding scopes and builtin shadowing, ordered
imports, helper renaming, checker isolation and cleanup, coherent project
verification, and termination after permanent application refusals. The
[1.732 changelog](../CHANGELOG.md#1732---2026-09-19) records the changes and
their regressions; type-parameter inference remains deferred. Eight final
regression cases cover missing corpus dependencies, PLY's import origin, and
full-suite confirmation after isolated reruns. The original faulty manifest
and an isolated-only harness mutation fail the corresponding negative controls.

The 141-project consumer evidence requests `--no-types` explicitly. Its
manifest installs runtime test dependencies, not each project's complete
configured typing environment. It measures consumer behavior in that mode;
it does not establish that these projects pass Towel's default typed path.

The complete **r6** run recorded 117 `PASS`, 19 `NO_CHANGE`, three
`BROKEN_KNOWN`, and two `BASELINE_ERROR` outcomes, and correctly exited with
failure. Cheroot lacked declared test plugins; PLY's test command changed
directories and invalidated its relative import path. A third environment
correction supplies SimPy's declared benchmark fixture, allowing ten test
bodies that previously errored to run. Separate complete before/refactor/after
runs of these three projects used the same Towel runtime and upstream pins.
The combined coverage is **119 `PASS`, 19 `NO_CHANGE`, and three
`BROKEN_KNOWN`**, covering all 141 entries. This is the initial full run plus
three corrected-environment runs, not a replacement of the original records.

PLY passes all 73 tests on both sides. Cheroot retains the same 30 failures
among 216 tests; SimPy retains the same two benchmark assertion failures among
151 tests. These `PASS` comparisons mean matching outcomes, not clean upstream
suites. Bottle's initial differing outcome was independently checked with three
paired complete-suite reruns, all 363 tests passing on both sides each time.
The three documented known limitations concern glom, Lark, and pyparsing's
frame, traceback, or generated-source observations. A `NO_CHANGE` result does
not validate a transformation.

The frozen r6 producer predates the final harness corrections. Independent
adjudication checks complete outcomes, exact failure identities, conserved test
counts, full-suite confirmation after isolated agreement, and actual changed
source files. The audit archive retains original and supplemental reports,
source hashes, commands, exit statuses, and complete logs separately.

The typed path has separate integration coverage. With an available checker,
Towel checks the complete original project before inference or output copying.
Existing errors abort with diagnostics and a request to fix them or explicitly
use `--no-types`; checker crashes and timeouts remain distinct failures.
A clean baseline stays active while all prospective changes are checked with
unchanged consumers, including copied outputs and calls to existing functions.
Reused signatures are preserved. The harness never silently retries a typed
refusal with checking disabled.

A separate positive typed-consumer check used Tomli at
`5a77b12a7a9f052ce5a20c335d2825658f6aea52`, Python 3.13.7, and its declared
mypy 1.19.1 in an isolated environment. Towel accepted the complete original
baseline; independent configured mypy checks passed before and after two
actual extractions. Both helpers have concrete annotations without `Any`,
and all 36 existing parser signatures are unchanged. Tomli's full unittest
command passed before and after: 18 methods with one Python 3.15-only skip,
including 228 valid and 505 invalid TOML parsing cases. This was a bounded
two-extraction run, not a full fixed point; optional formatters were absent.
The accompanying audit evidence retains the environment, source comparison,
fixture inventory, and complete logs. This supplements the typed integration
tests without extending the r6 corpus's typing claims.

Exact wheel and source-distribution hashes and their installation-check
results are retained in the accompanying audit and release evidence, separately
from these source-gate results.

## Historical 1.732 candidate run (September 19, 2026)

The reported totals below belong to `347d62b`, before the later audit fixes.
They are preserved as historical evidence, not as results for the current
candidate. The older harness could accept incomplete or unrecognized test
runs; its recorded verdicts have not been retroactively reclassified under
the stricter current gate.

The release gate for 1.732 ran on commit `347d62b` from a detached
worktree snapshot, macOS, Python 3.13.7 for the harness, the refactoring
and every project's environment, rebuilt in a fresh work directory. (The
previous directory's environments mixed Python 3.12 and 3.13, some without
a `pyvenv.cfg`, and macOS's daily cleanup of `/tmp` had deleted `HEAD`,
`config` and `index` from 39 of its clones.) Three projects ran at a time
with Towel's default forked workers, and the defaults were on: formatting
through Black, isort and ruff, annotations inferred through mypy and
verified by every checker the project configures, mypy or pyright or both.
An external sampler recorded the memory of every refactor's process tree
every two seconds.

**Totals: PASS 118, NO_CHANGE 19, BROKEN_KNOWN 4, BROKEN 0.** Against the
`938d351` run: dpath now changes and passes; pluggy and trio, previously
known-broken, pass because nothing their known tests watch differs; rich's
documented traceback test now differs and is known-broken as the manifest
records; and pyparsing is newly known-broken. The module-name rule admits
six of its blocks that end in `raise ParseException(...)`, and
`ParseException.explain(depth=1)` counts traceback frames, so the helper's
frame shows (the traceback-shape limitation, as for glom; the manifest
names only that test). The four known-broken projects are glom, lark,
pyparsing and rich.

Two earlier candidate runs that day found five defects, each fixed before
this run, with a regression test:

- oauthlib: a same-file pair's helper hosted in a shared ancestor class
  defined in another module read `BearerToken` bare, and 55 tests raised
  `NameError` (`52a754f`, fixture xf15).
- typing_extensions: blocks that call its own `_caller()`, which reads
  `sys._getframe(depth + 1)`, were extracted, and six `TypeAliasType`
  pickling tests failed; calls to a module's own caller-frame readers now
  count as frame reads (`09ba025`, fixture r146).
- pyright ran as `python -m pyright` from the module's directory, which
  put the project's own packages ahead of the standard library, so sphinx's
  `locale` package was executed and pyright produced nothing for flask,
  pytest, structlog, sphinx, trio and werkzeug; it now runs with
  `python -P` (`0bb2075`).
- mypy's finished builds piled up as uncollected reference cycles, and
  sphinx's refactor reached 40 GB in one process; the oracle now collects
  them every ten builds, and the same refactor, run alone, peaks at 1.76 GB
  and takes 1,140 s (`e42ed4f` and its follow-up).
- The candidate-pair budget's default of 2,000,000 cut networkx (9.25
  million pairs) and sphinx (8.45 million) on every pass, and their changed
  files fell from 105 to 73 and from 101 to 87; the default is now
  20,000,000 (`b68c9c8`), and sphinx changes 107 files.

**Memory.** The machine's used memory peaked at 34.1 GB of 128 GB, and free
memory never fell below 66.8 GB, with three projects in flight. The median
refactor's largest process peaked at 153 MB and no single refactor process
exceeded 1.65 GB (sphinx). Summed over forked workers, the largest trees
were sphinx 22.6 GB, croniter 15.6 GB and networkx 10.8 GB, but forked
workers share their pages copy-on-write, so those sums overstate what the
machine held.

**Time.** The run took 77 minutes, 61 of them sphinx, whose refactor now
checks every change with pyright as well as mypy (its configuration asks
for both) and is no longer cut by the budget. Per-project refactor times in
this run are not a clean comparison with `938d351`: other refactors ran on
the same machine during it while the defects above were being measured.
Measured alone on the final commit, sphinx's refactor takes 1,140 s
(2,058 s at `938d351`) and applies 440 refactorings across 107 files, and
Towel's own source takes 13.3 s against 12.8 s at `5ff2458` (Python 3.13,
one core, the defaults).

The subsequent timing-only changes, through the reviewed `83f4d8a` checkpoint,
left the exactness baselines byte-identical, and click and jinja2 produced
identical output with and without the last of them. The current validation
above records the later audit fixes separately from these historical timing checks.

## Historical 141-project corpus (September 17–18, 2026)

On September 17, 2026 the corpus grew from 91 to 141 projects: 47 more
libraries (mistune, blinker, pycparser, invoke, python-fire, bottle,
waitress, gunicorn, wsproto, hpack, hyperframe, h2, pyupgrade,
python-dotenv, backoff, schedule, traitlets, soupsieve, beautifulsoup4,
mkdocs, chardet, pyasn1, ecdsa, rsa, ply, inflect, unidecode, texttable,
docopt-ng, yapf, mccabe, flake8, jsonpointer, rfc3986, uritemplate,
tomli-w, wheel, installer, pyproject-hooks, simpy, termcolor,
typing_extensions, cheroot, sly, environs, apispec, webargs) and Towel
itself at `v1.414`, `v1.618`, and current `main`, refactored by the
candidate engine and then run through Towel's own test suite. The run was
made with the current defaults, which since 1.618 include the reuse
redirect, Black or ruff formatting of inserted code, and type annotations
on helpers inferred and verified through mypy (the harness environments
have Black and mypy installed, so both were exercised on every project).

The first full run of the 141 found six projects broken and the
follow-up runs found four more defects, every one now fixed with a
regression test:

- pycodestyle's own dog-food test failed on an 86-character generated call:
  Black's default line length was used although the project declares 79
  in `setup.cfg`. The formatter now follows the line length the project
  declares in any tool section (`49af7c5`).
- tornado: `memoryview[int]`, copied from tornado's own signatures, was
  written bare and raised `TypeError` at import on an interpreter where
  `memoryview` is not generic. A subscripted annotation is now written
  bare only when it evaluates at definition time (`33ea6d2`).
- Towel on itself (`v1.618` and `main`): a union of two forward references
  was written `'A' | 'B'`, a `TypeError` at definition. The union is now
  quoted as one string (`9b4062f`).
- sphinx: with the output directory beside the original clone
  (`sphinx-cleaned` next to `sphinx`), the cycle guard resolved the
  package's own absolute imports against the original, where the helper
  import that closed the cycle did not exist, and `sphinx.transforms`
  broke on import. A package's own absolute imports are now resolved
  inside the tree being refactored (`9b4062f`, fixture `252c3db`).
- sphinx again: inconsistent subtype verdicts from mypy (some
  unanswerable) let every member of a union absorb every other, emptying
  it, and `_joined` raised `IndexError`. A member now absorbs another only
  on a definite `True` (`c3ddf01`).
- trio's `test_deprecate` asserts the exact line a warning is issued from
  inside the test module, which a helper inserted above it shifts. This is
  one of the silent kinds in KNOWN_LIMITATIONS and is marked
  `BROKEN_KNOWN` (`13d7eb2`). trio then crashed on the import sorter, which
  had reordered imports under `if TYPE_CHECKING:`: the guard now allows
  nested reordering and keeps the file as assembled when a sorter does
  anything else (`4a58da2`).
- beautifulsoup4 and invoke: a helper hosted in a submodule that reaches
  back into its package with `from . import name` was imported by the
  package initializer, which the submodule then imported half-initialized.
  The cycle guard now treats that import as an edge to `__init__` and
  searches from the host's package initializers (`7fadbbc`).
- h2: with Black on, a forwarding helper wrapped over the three-line
  minimum paired with another of its shape and extracted a third, without
  end. The trivial-helper filter now recognizes assign-then-return and
  unpack-then-return forwarders (`98aa37b`); mypy's module names for a
  non-identifier directory (`h2 - H2Stream`) are kept out of annotations
  by the same commit.
- rfc3986: a reuse target preceded by `@overload` stubs was verified
  against the first stub; the last definition is the runtime binding
  (`bc55b75`).
- gunicorn: a helper hosted in `workers/gtornado.py`, which raises at
  import unless tornado is installed, made `workers/sync.py` import it.
  That is a limitation, not a defect (Towel does not know a module's
  import-time requirements): it is recorded in KNOWN_LIMITATIONS and the
  harness installs tornado for gunicorn.

The release-gate run is on commit `938d351c7ac2613237686248c3c1b420b220a688`, macOS, Python 3.13, from a
detached worktree snapshot of that commit (the harness imports Towel's
source live, so a commit made mid-run would have changed what later
projects were tested with), three projects at a time. Sphinx's
refactoring took 2058 s against 2513 s under 1.618 on the same
revision, with more files changed (1938 s in a re-run on an otherwise
idle machine; during this run the machine was also running a code audit);
networkx took 309 s against 352 s, with 105 files changed against 67.

| Project | Commit | Verdict | Changed files | Refactor s | Before | After |
|---|---|---|---|---|---|---|
| anyio | `9e2b5924e5` | PASS | 10 | 42 | 8 failed, 3926 passed, 337 skipped, 5 xfailed | 8 failed, 3926 passed, 337 skipped, 5 xfailed |
| apispec | `bfac55c9bf` | PASS | 3 | 2 | 618 passed, 6 skipped | 618 passed, 6 skipped |
| arrow | `2224255c4a` | PASS | 2 | 5 | 1902 passed | 1902 passed |
| astroid | `6011e6c31e` | PASS | 11 | 61 | 3 failed, 2135 passed, 90 skipped, 15 xfailed, 35 subtests passed | 3 failed, 2135 passed, 90 skipped, 15 xfailed, 35 subtests passed |
| attrs | `8f76777632` | PASS | 2 | 2 | 5 failed, 1402 passed, 4 skipped, 1 xfailed | 5 failed, 1402 passed, 4 skipped, 1 xfailed |
| backoff | `d82b23c42d` | PASS | 3 | 0 | 2 failed, 120 passed, 1 error | 2 failed, 120 passed, 1 error |
| beautifulsoup4 | `c772c5e6b3` | PASS | 21 | 78 | 900 passed, 7 skipped | 900 passed, 7 skipped |
| bidict | `61e98274c4` | NO_CHANGE | 0 | 0 | 1 failed, 7 passed, 2 errors |  |
| black | `acd6198877` | PASS | 7 | 12 | 2 failed, 479 passed, 3 skipped, 8 subtests passed | 2 failed, 479 passed, 3 skipped, 8 subtests passed |
| bleach | `f0355a7af0` | PASS | 9 | 169 | 436 passed, 1 skipped, 3 xfailed | 436 passed, 1 skipped, 3 xfailed |
| blinker | `c336405966` | NO_CHANGE | 0 | 0 | 25 passed |  |
| boltons | `961dcff3f4` | PASS | 13 | 9 | 519 passed | 519 passed |
| bottle | `90bd4aa743` | PASS | 1 | 4 | 363 passed | 363 passed |
| cachetools | `4500e3d042` | PASS | 3 | 1 | 333 passed | 333 passed |
| cattrs | `5bf7c97293` | PASS | 18 | 12 | 2 failed, 1042 passed, 15 xfailed | 2 failed, 1042 passed, 15 xfailed |
| cerberus | `65e977de08` | PASS | 8 | 20 | 248 passed, 1 skipped | 248 passed, 1 skipped |
| chardet | `e9603fad69` | PASS | 5 | 6 | 11936 passed, 6 deselected, 12 xfailed | 11936 passed, 6 deselected, 12 xfailed |
| cheroot | `edef8ff862` | PASS | 1 | 2 | ImportError: Error importing plugin "pytest_cov": No module named 'pytest_cov' | ImportError: Error importing plugin "pytest_cov": No module named 'pytest_cov' |
| click | `6aabf099bf` | PASS | 5 | 29 | 2058 passed, 25 skipped, 31000 deselected, 1 xfailed | 2058 passed, 25 skipped, 31000 deselected, 1 xfailed |
| colorama | `841634ed2a` | PASS | 5 | 3 | 38 passed, 14 skipped | 38 passed, 14 skipped |
| croniter | `3dd4d14e97` | PASS | 3 | 295 | 248 passed, 92 subtests passed | 248 passed, 92 subtests passed |
| decorator | `2322c7bfdb` | NO_CHANGE | 0 | 0 | 26 passed |  |
| deepdiff | `79e4379278` | PASS | 5 | 6 | 7 failed, 1256 passed, 44 skipped, 1 error | 7 failed, 1256 passed, 44 skipped, 1 error |
| django-forms | `8cbdd4a814` | PASS | 3 | 3 | OK (skipped=2) | OK (skipped=2) |
| django-utils | `8cbdd4a814` | PASS | 6 | 2 | OK (skipped=21) | OK (skipped=21) |
| docopt-ng | `587f41a614` | NO_CHANGE | 0 | 0 | 615 passed |  |
| dpath | `26b77325f5` | NO_CHANGE | 0 | 0 | 79 passed |  |
| ecdsa | `bff40c6cf2` | PASS | 20 | 77 | 2039 passed, 5 skipped | 2039 passed, 5 skipped |
| environs | `4e57a32ffe` | PASS | 1 | 1 | 135 passed | 135 passed |
| feedparser | `a22c5521cb` | PASS | 9 | 2 | 4297 passed, 8 skipped | 4297 passed, 8 skipped |
| filelock | `4efd93e048` | PASS | 3 | 3 | 1456 passed, 54 skipped | 1456 passed, 54 skipped |
| flake8 | `efe6750405` | PASS | 2 | 2 | 466 passed, 1 xfailed | 466 passed, 1 xfailed |
| flask | `d73fa1cdcb` | PASS | 2 | 5 | 494 passed | 494 passed |
| funcy | `5419a8f879` | PASS | 1 | 0 | 219 passed | 219 passed |
| glom | `fd70d3051a` | BROKEN_KNOWN | 8 | 4 | 1 failed, 201 passed | 11 failed, 191 passed |
| gunicorn | `afc7d2fd5d` | PASS | 32 | 33 | 2072 passed, 534 skipped | 2072 passed, 534 skipped |
| h11 | `62c5068c97` | PASS | 6 | 17 | 78 passed | 78 passed |
| h2 | `bc239af1d1` | PASS | 4 | 10 | 1662 passed | 1662 passed |
| hpack | `d05cff4cd4` | PASS | 1 | 1 | 502 passed | 502 passed |
| html5lib | `fd4f032bc0` | PASS | 20 | 140 | 972 passed, 28 skipped | 972 passed, 28 skipped |
| httpx | `b5addb64f0` | PASS | 7 | 14 | 10 failed, 1407 passed, 1 skipped | 10 failed, 1407 passed, 1 skipped |
| humanize | `3201e702ed` | NO_CHANGE | 0 | 0 | 724 passed, 74 skipped |  |
| hyperframe | `632e309bf6` | PASS | 1 | 1 | 114 passed, 1 skipped | 114 passed, 1 skipped |
| idna | `cd1739200f` | PASS | 1 | 1 | 6445 passed, 1 skipped, 56 subtests passed | 6445 passed, 1 skipped, 56 subtests passed |
| inflect | `262a247d2d` | PASS | 1 | 2 | 214 passed, 16 xfailed | 214 passed, 16 xfailed |
| inflection | `88eefaacf7` | NO_CHANGE | 0 | 0 | 467 passed |  |
| installer | `59f0a65af9` | NO_CHANGE | 0 | 0 | 150 passed |  |
| invoke | `6a71e680c5` | PASS | 20 | 36 | 114 failed, 859 passed, 11 skipped | 114 failed, 859 passed, 11 skipped |
| isort | `131f4adcd5` | PASS | 7 | 15 | 2 failed, 623 passed, 1 skipped | 2 failed, 623 passed, 1 skipped |
| itsdangerous | `672971d66a` | PASS | 2 | 3 | 297 passed | 297 passed |
| jinja2 | `5ef70112a1` | PASS | 3 | 59 | 911 passed | 911 passed |
| jmespath | `2812594e69` | PASS | 5 | 1 | 998 passed, 1 skipped | 998 passed, 1 skipped |
| jsonpatch | `d8e1a6e244` | PASS | 1 | 1 | 110 passed | 110 passed |
| jsonpointer | `5998f951dc` | NO_CHANGE | 0 | 0 | 23 passed |  |
| jsonschema | `15f8613be5` | PASS | 9 | 77 | 7826 passed, 703 skipped | 7826 passed, 703 skipped |
| lark | `9a4fb9c745` | BROKEN_KNOWN | 9 | 11 | SKIPPED [1] tests/test_parser.py:1033: start/end values work differently for the basic lexer | FAILED tests/__main__.py::TestStandalone::test_transformer - NameError: name ... |
| loguru | `48acf77ac2` | PASS | 4 | 2 | 1615 passed, 53 skipped | 1615 passed, 53 skipped |
| mako | `411b4ac6cf` | PASS | 9 | 9 | 517 passed, 53 skipped | 517 passed, 53 skipped |
| markdown | `819fff96b0` | PASS | 13 | 8 | FAILED (errors=364, skipped=110) | FAILED (errors=362, skipped=110) |
| markdown-it-py | `a5950caef3` | PASS | 14 | 5 | 1000 passed, 1 skipped | 1000 passed, 1 skipped |
| markupsafe | `b2e4d9c768` | NO_CHANGE | 0 | 0 | 39 passed, 41 skipped |  |
| marshmallow | `c54aa7292a` | PASS | 2 | 3 | 1190 passed | 1190 passed |
| mccabe | `292b5c71c3` | NO_CHANGE | 0 | 0 | 1 failed, 15 passed |  |
| mistune | `a1b50bc12e` | PASS | 19 | 8 | 1158 passed, 6 subtests passed | 1158 passed, 6 subtests passed |
| mkdocs | `2862536793` | PASS | 2 | 3 | FAILED (failures=2, skipped=4) | FAILED (failures=2, skipped=4) |
| more-itertools | `9ed3dbb0ae` | PASS | 2 | 6 | OK (skipped=5) | OK (skipped=5) |
| natsort | `e2328c20b6` | PASS | 1 | 1 | 355 passed | 355 passed |
| networkx | `8977f1cfba` | PASS | 105 | 309 | 9282 passed, 58 skipped, 741 xfailed | 9282 passed, 58 skipped, 741 xfailed |
| nox | `fe279740b7` | PASS | 2 | 6 | 906 passed, 47 skipped, 1 xpassed | 906 passed, 47 skipped, 1 xpassed |
| oauthlib | `40b0ab56da` | PASS | 19 | 7 | 703 passed, 2 skipped, 21 subtests passed | 703 passed, 2 skipped, 21 subtests passed |
| outcome | `03ed6218b0` | NO_CHANGE | 0 | 0 | 3 failed, 7 passed |  |
| packaging | `10590c194e` | PASS | 9 | 17 | 62434 passed, 1 skipped, 427 deselected | 62434 passed, 1 skipped, 427 deselected |
| parsimonious | `eb79639859` | PASS | 3 | 3 | 84 passed, 2 skipped | 84 passed, 2 skipped |
| parso | `7f5b142b54` | PASS | 5 | 4 | 1985 passed | 1985 passed |
| pathspec | `f0fb3f4aaa` | PASS | 3 | 3 | 215 passed, 372 skipped, 280 subtests passed | 215 passed, 372 skipped, 280 subtests passed |
| peewee | `2866df242d` | PASS | 1 | 68 | OK (skipped=176) | OK (skipped=176) |
| platformdirs | `c5ef1edbb4` | PASS | 2 | 1 | 1229 passed, 106 skipped | 1229 passed, 106 skipped |
| pluggy | `0744fd993b` | BROKEN_KNOWN | 3 | 1 | 169 passed | 1 failed, 168 passed |
| ply | `9d7c40099e` | PASS | 2 | 10 | ModuleNotFoundError: No module named 'ply' | ModuleNotFoundError: No module named 'ply' |
| prettytable | `2a6cd4fb41` | PASS | 1 | 22 | 338 passed | 338 passed |
| pyasn1 | `8003397013` | PASS | 7 | 7 | 1261 passed, 65 subtests passed | 1261 passed, 65 subtests passed |
| pycodestyle | `d6c38543a9` | PASS | 1 | 1 | 770 passed, 5 skipped | 770 passed, 5 skipped |
| pycparser | `f93324c195` | PASS | 3 | 54 | 132 passed, 6 skipped | 132 passed, 6 skipped |
| pyflakes | `52cb7296b4` | PASS | 9 | 29 | 748 passed, 25 skipped | 748 passed, 25 skipped |
| pygments | `38f426a6b1` | PASS | 60 | 26 | 5330 passed, 16 skipped | 5330 passed, 16 skipped |
| pyjwt | `b9f6a9d79c` | PASS | 2 | 4 | 456 passed, 4 skipped | 456 passed, 4 skipped |
| pyparsing | `efd56db4e5` | PASS | 4 | 12 | 2140 passed, 27 skipped, 2041 subtests passed | 2140 passed, 27 skipped, 2041 subtests passed |
| pyproject-hooks | `184c9f56a8` | PASS | 1 | 1 | 42 passed | 42 passed |
| pyrsistent | `0c0b7aec8c` | PASS | 5 | 1 | 536 passed, 102 skipped | 536 passed, 102 skipped |
| pytest | `99ab2acccf` | PASS | 23 | 93 | 4535 passed, 126 skipped, 12 xfailed, 1 xpassed, 1 error | 2 failed, 4533 passed, 126 skipped, 12 xfailed, 1 xpassed, 1 error |
| python-dateutil | `48bd1af97e` | PASS | 8 | 7 | 41 failed, 1991 passed, 47 skipped, 17 xfailed | 41 failed, 1991 passed, 47 skipped, 17 xfailed |
| python-dotenv | `a00cb2eed0` | PASS | 1 | 1 | 1 failed, 251 passed, 1 skipped | 1 failed, 251 passed, 1 skipped |
| python-fire | `716bbc23d7` | PASS | 15 | 49 | 273 passed | 273 passed |
| python-prompt-toolkit | `583b3412c7` | PASS | 28 | 65 | 156 passed | 156 passed |
| python-slugify | `fee5aa338d` | NO_CHANGE | 0 | 0 | 104 passed, 30 subtests passed |  |
| pyupgrade | `fbad673bb8` | PASS | 9 | 2 | 1100 passed, 1 skipped, 4 xfailed | 1100 passed, 1 skipped, 4 xfailed |
| rfc3986 | `2248a185cb` | PASS | 5 | 8 | -- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html | -- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html |
| rich | `9d8f9a372c` | PASS | 17 | 24 | 8 failed, 948 passed, 25 skipped | 8 failed, 948 passed, 25 skipped |
| rsa | `42b0e14ffb` | PASS | 4 | 2 | 1 failed, 99 passed | 1 failed, 99 passed |
| schedule | `82a43db1b9` | PASS | 1 | 3 | 40 passed, 41 skipped | 40 passed, 41 skipped |
| schema | `310a1239b6` | PASS | 1 | 1 | 124 passed | 124 passed |
| simpy | `f43816490c` | PASS | 2 | 1 | 141 passed, 10 errors | 141 passed, 10 errors |
| six | `c8e394065c` | NO_CHANGE | 0 | 0 | 184 passed, 16 skipped |  |
| sly | `09c2ba30df` | PASS | 1 | 3 | 15 passed | 15 passed |
| sniffio | `6996e05d9b` | PASS | 1 | 0 | 3 passed, 1 skipped | 3 passed, 1 skipped |
| sortedcontainers | `3ac358631f` | PASS | 3 | 18 | 366 passed | 366 passed |
| soupsieve | `4caaf89344` | PASS | 3 | 3 | 398 passed, 1 skipped | 398 passed, 1 skipped |
| sphinx | `e44a40eb2f` | PASS | 101 | 2058 | 7 failed, 2383 passed, 35 skipped | 7 failed, 2383 passed, 35 skipped |
| sqlparse | `60cdc64972` | PASS | 2 | 1 | 506 passed, 2 xfailed, 1 xpassed | 506 passed, 2 xfailed, 1 xpassed |
| starlette | `03f12b7fcf` | PASS | 6 | 5 | 1249 passed, 2 xfailed | 1249 passed, 2 xfailed |
| structlog | `73393f34b4` | PASS | 3 | 18 | 4 failed, 880 passed, 37 skipped | 4 failed, 880 passed, 37 skipped |
| tabulate | `268615a5c2` | PASS | 1 | 1 | 323 passed, 60 skipped | 323 passed, 60 skipped |
| tenacity | `3e58094d3b` | PASS | 2 | 2 | 183 passed, 1 skipped, 15 subtests passed | 183 passed, 1 skipped, 15 subtests passed |
| termcolor | `4ce05dda98` | NO_CHANGE | 0 | 0 | 43 failed, 46 passed |  |
| texttable | `b4c00a6862` | NO_CHANGE | 0 | 0 | 15 passed |  |
| tomli | `5a77b12a7a` | PASS | 1 | 1 | 17 passed, 1 skipped, 744 subtests passed | 17 passed, 1 skipped, 744 subtests passed |
| tomli-w | `1210bb6f57` | NO_CHANGE | 0 | 0 | 269 passed, 2 xfailed |  |
| tomlkit | `4b38becd75` | PASS | 6 | 25 | 1058 passed | 1058 passed |
| toolz | `568c2b8393` | PASS | 6 | 13 | 1 failed, 185 passed | 1 failed, 185 passed |
| tornado | `85b6917d05` | PASS | 40 | 297 | 473 failed, 746 passed, 189 skipped, 86 subtests passed | 473 failed, 746 passed, 189 skipped, 86 subtests passed |
| towel-main | `6c08ff2942` | PASS | 13 | 24 | 1181 passed, 34 subtests passed | 1181 passed, 34 subtests passed |
| towel-v1.414 | `4bb390b8a4` | PASS | 14 | 25 | 1257 passed, 34 subtests passed | 1257 passed, 34 subtests passed |
| towel-v1.618 | `21500b5bf6` | PASS | 13 | 24 | 1181 passed, 34 subtests passed | 1181 passed, 34 subtests passed |
| tqdm | `9cf5a12b1f` | PASS | 8 | 3 | 168 passed, 11 skipped | 168 passed, 11 skipped |
| traitlets | `c4f1247774` | PASS | 2 | 5 | 710 passed, 1 skipped | 710 passed, 1 skipped |
| trio | `59d94d7ae0` | BROKEN_KNOWN | 37 | 239 | 9 failed, 536 passed, 62 skipped, 1 xfailed | 10 failed, 535 passed, 62 skipped, 1 xfailed |
| typer | `a80f6e5ecd` | PASS | 7 | 18 | 409 failed, 956 passed, 35 skipped, 2 xfailed | 409 failed, 956 passed, 35 skipped, 2 xfailed |
| typing_extensions | `3ae9b7553a` | PASS | 1 | 1 | FAILED (failures=1, skipped=11) | FAILED (failures=1, skipped=11) |
| unidecode | `158ec2c7e7` | NO_CHANGE | 0 | 0 | 66 passed |  |
| uritemplate | `e7a947e738` | NO_CHANGE | 0 | 0 | 58 passed, 66 subtests passed |  |
| virtualenv | `e13bb213aa` | PASS | 6 | 4 | 385 passed, 69 skipped | 385 passed, 69 skipped |
| voluptuous | `44593ce7c3` | PASS | 4 | 40 | 182 passed | 182 passed |
| waitress | `016eea527d` | PASS | 4 | 2 | 819 passed, 10 skipped, 45 subtests passed | 819 passed, 10 skipped, 45 subtests passed |
| wcwidth | `17986f51dd` | PASS | 3 | 3 | 1 failed, 1363 passed, 10 skipped | 1 failed, 1363 passed, 10 skipped |
| webargs | `abe0d763ae` | PASS | 2 | 1 | 503 passed, 4 skipped | 503 passed, 4 skipped |
| werkzeug | `6a604e005d` | PASS | 15 | 52 | 1024 passed | 1024 passed |
| wheel | `b25c3c2ef5` | PASS | 2 | 2 | 74 passed | 74 passed |
| wrapt | `f1586a5e48` | PASS | 3 | 2 | 1 failed, 1222 passed, 8 skipped | 1 failed, 1222 passed, 8 skipped |
| wsproto | `5e0685d074` | PASS | 2 | 2 | 230 passed | 230 passed |
| xmltodict | `6e29fba282` | NO_CHANGE | 0 | 0 | 134 passed |  |
| yapf | `1200509529` | PASS | 7 | 4 | 611 passed | 611 passed |

Totals: BROKEN_KNOWN 4, NO_CHANGE 20, PASS 117; no project unsupported, broken, crashed, or timed out.
The `BROKEN_KNOWN` verdicts are pluggy and glom (frame-relative), lark
(source-observing), and trio (line-relative), each requiring every newly
failing test to match a failure the manifest names. Towel's own three
revisions pass their suites after being refactored by the candidate, with
annotated helpers inserted into an annotated, strictly type-checked
codebase. The suite at `938d351` was 1,181 tests; at `5ff2458` (September
19, 2026) it collects 2,182.

## The 1.414 report (September 12–15, 2026)
Publication remains a separate maintainer decision; see
[RELEASING.md](RELEASING.md). Unattended use is not claimed: the first three
batches of new projects each found new defect classes (see below), but the
fourth and fifth batches of ten previously unseen projects each passed clean,
meeting the stopping rule of two consecutive defect-free batches. The failure
rate on an unfamiliar project is low but not zero.

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

`scripts/ecosystem_check.py` (run as `just ecosystem --run-untrusted-code`,
and weekly in CI; it executes the projects' own code with the caller's
privileges, refuses without that opt-in, and belongs on a disposable
machine) clones each project in `scripts/ecosystem/manifest.toml` at the
commit pinned there, runs its suite,
refactors a copy with the CLI defaults, runs the suite again, and compares
exit status and the normalized test outcomes, ignoring the warning tally,
and reruns any test whose result differs to tell a flaky difference from a
regression. The table is the final run on commit `a00511e0e0e0`, macOS,
Python 3.13, September 15, 2026, three projects at a time with two forked
workers each; the report, per-project JSON, and all logs are archived with
the release evidence. Platform and resource requirements are in
[KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md#resources-and-platform).

| Project (revision) | Verdict | Files changed | Refactor s | Suite |
|---|---|---|---|---|
| anyio `f695900` | PASS | 12 | 34 | identical: 8 failed, 3926 passed, 337 skipped, 5 xfailed |
| arrow `2224255` | PASS | 2 | 2 | identical: 1902 passed |
| astroid `5d1a0a2` | PASS | 13 | 67 | identical: 3 failed, 2135 passed, 90 skipped, 15 xfailed, 35 subtests passed |
| attrs `8f76777` | PASS | 4 | 5 | identical: 4 failed, 1400 passed, 7 skipped, 1 xfailed |
| bidict `61e9827` | NO_CHANGE | 0 | 0 | no proposal at the default minimum |
| black `20622e1` | PASS | 2 | 14 | identical: 2 failed, 479 passed, 3 skipped, 8 subtests passed |
| bleach `f0355a7` | PASS | 10 | 197 | identical: 436 passed, 1 skipped, 3 xfailed |
| boltons `961dcff` | PASS | 13 | 18 | identical: 519 passed |
| cachetools `4500e3d` | PASS | 3 | 2 | identical: 333 passed |
| cattrs `5bf7c97` | PASS | 17 | 9 | identical: 2 failed, 1042 passed, 15 xfailed |
| cerberus `65e977d` | PASS | 8 | 36 | identical: 248 passed, 1 skipped |
| click `6aabf09` | PASS | 5 | 16 | identical: 2058 passed, 25 skipped, 31000 deselected, 1 xfailed |
| colorama `841634e` | PASS | 5 | 7 | identical: 38 passed, 14 skipped |
| croniter `3dd4d14` | PASS | 5 | 1101 | identical: 248 passed, 92 subtests passed |
| decorator `2322c7b` | NO_CHANGE | 0 | 0 | no proposal at the default minimum |
| deepdiff `79e4379` | PASS | 5 | 12 | identical: 7 failed, 1256 passed, 44 skipped, 1 error |
| django-forms `850e026` | PASS | 4 | 5 | identical: OK (skipped=2) |
| django-utils `850e026` | PASS | 6 | 5 | identical: OK (skipped=21) |
| dpath `26b7732` | NO_CHANGE | 0 | 0 | no proposal at the default minimum |
| feedparser `a22c552` | PASS | 9 | 4 | identical: 4297 passed, 8 skipped |
| filelock `4efd93e` | PASS | 2 | 3 | identical: 1456 passed, 54 skipped |
| flask `d73fa1c` | PASS | 3 | 2 | identical: 494 passed |
| funcy `5419a8f` | NO_CHANGE | 0 | 0 | no proposal at the default minimum |
| glom `fd70d30` | BROKEN_KNOWN | 5 | 7 | frame-relative: test_error.py asserts literal traceback frames, and the helper adds one |
| h11 `62c5068` | PASS | 6 | 26 | identical: 78 passed |
| html5lib `fd4f032` | PASS | 19 | 205 | identical: 972 passed, 28 skipped |
| httpx `b5addb6` | PASS | 12 | 8 | identical: 10 failed, 1407 passed, 1 skipped |
| humanize `3201e70` | NO_CHANGE | 0 | 0 | no proposal at the default minimum |
| idna `cd17392` | PASS | 1 | 1 | identical: 6445 passed, 1 skipped, 56 subtests passed |
| inflection `88eefaa` | NO_CHANGE | 0 | 0 | no proposal at the default minimum |
| isort `131f4ad` | PASS | 5 | 12 | identical: 2 failed, 623 passed, 1 skipped |
| itsdangerous `672971d` | PASS | 3 | 0 | identical: 297 passed |
| jinja2 `5ef7011` | PASS | 6 | 43 | identical: 911 passed |
| jmespath `2812594` | PASS | 5 | 1 | identical: 998 passed, 1 skipped |
| jsonpatch `d8e1a6e` | PASS | 1 | 1 | identical: 110 passed |
| jsonschema `865c27f` | PASS | 8 | 190 | identical: 7826 passed, 703 skipped |
| lark `9a4fb9c` | BROKEN_KNOWN | 8 | 20 | source-observing: lark.tools.standalone copies marked regions of named source files into one artifact, so a helper moved to another file is absent from it |
| loguru `48acf77` | PASS | 3 | 2 | identical: 1615 passed, 53 skipped |
| mako `411b4ac` | PASS | 10 | 21 | identical: 517 passed, 53 skipped |
| markdown `0d6afd1` | PASS | 10 | 9 | identical: FAILED (errors=364, skipped=110) |
| markdown-it-py `a5950ca` | PASS | 2 | 7 | identical: 1000 passed, 1 skipped |
| markupsafe `b2e4d9c` | NO_CHANGE | 0 | 0 | no proposal at the default minimum |
| marshmallow `c54aa72` | PASS | 2 | 1 | identical: 1190 passed |
| more-itertools `b2f3aff` | PASS | 1 | 4 | identical: OK (skipped=5) |
| natsort `e2328c2` | PASS | 1 | 0 | identical: 355 passed |
| networkx `4e74880` | PASS | 67 | 352 | identical: 9282 passed, 58 skipped, 741 xfailed |
| nox `fe27974` | PASS | 3 | 3 | identical: 906 passed, 47 skipped, 1 xpassed |
| oauthlib `40b0ab5` | PASS | 22 | 22 | identical: 703 passed, 2 skipped, 21 subtests passed |
| outcome `03ed621` | NO_CHANGE | 0 | 0 | no proposal at the default minimum |
| packaging `10590c1` | PASS | 10 | 14 | identical: 62434 passed, 1 skipped, 427 deselected |
| parsimonious `eb79639` | PASS | 3 | 3 | identical: 84 passed, 2 skipped |
| parso `7f5b142` | PASS | 3 | 5 | identical: 1985 passed |
| pathspec `f0fb3f4` | PASS | 6 | 2 | identical: 215 passed, 372 skipped, 280 subtests passed |
| peewee `2866df2` | PASS | 1 | 107 | identical: OK (skipped=176) |
| platformdirs `5921065` | PASS | 2 | 1 | identical: 1229 passed, 106 skipped |
| pluggy `0a49741` | BROKEN_KNOWN | 3 | 0 | frame-relative: _verify_all_args_are_provided warns with stacklevel through the extracted helper |
| prettytable `2a6cd4f` | PASS | 1 | 25 | identical: 338 passed |
| pycodestyle `d6c3854` | NO_CHANGE | 0 | 1 | no proposal at the default minimum |
| pyflakes `52cb729` | PASS | 10 | 111 | identical: 748 passed, 25 skipped |
| pygments `38f426a` | PASS | 45 | 89 | identical: 5330 passed, 16 skipped |
| pyjwt `b9f6a9d` | PASS | 2 | 3 | identical: 456 passed, 4 skipped |
| pyparsing `efd56db` | PASS | 5 | 12 | identical: 2140 passed, 27 skipped, 2041 subtests passed |
| pyrsistent `0c0b7ae` | PASS | 4 | 1 | identical: 536 passed, 102 skipped |
| pytest `53bc06b` | PASS | 16 | 32 | identical: 1 failed, 4515 passed, 126 skipped, 12 xfailed, 1 xpassed, 1 error |
| python-dateutil `48bd1af` | PASS | 6 | 12 | identical: 41 failed, 1991 passed, 47 skipped, 17 xfailed |
| python-prompt-toolkit `583b341` | PASS | 27 | 297 | identical: 156 passed |
| python-slugify `fee5aa3` | NO_CHANGE | 0 | 0 | no proposal at the default minimum |
| rich `9d8f9a3` | PASS | 15 | 62 | identical: 8 failed, 948 passed, 25 skipped |
| schema `310a123` | PASS | 1 | 1 | identical: 124 passed |
| six `c8e3940` | NO_CHANGE | 0 | 0 | no proposal at the default minimum |
| sniffio `6996e05` | PASS | 1 | 0 | identical: 3 passed, 1 skipped |
| sortedcontainers `3ac3586` | PASS | 2 | 21 | identical: 366 passed |
| sphinx `e44a40e` | PASS | 96 | 2513 | identical: 7 failed, 2383 passed, 35 skipped |
| sqlparse `60cdc64` | PASS | 2 | 2 | identical: 506 passed, 2 xfailed, 1 xpassed |
| starlette `03f12b7` | PASS | 5 | 5 | identical: 1249 passed, 2 xfailed |
| structlog `73393f3` | PASS | 6 | 3 | identical: 4 failed, 880 passed, 37 skipped |
| tabulate `268615a` | NO_CHANGE | 0 | 1 | no proposal at the default minimum |
| tenacity `3e58094` | PASS | 2 | 1 | identical: 183 passed, 1 skipped, 15 subtests passed |
| tomli `5a77b12` | PASS | 1 | 1 | identical: 17 passed, 1 skipped, 744 subtests passed |
| tomlkit `4b38bec` | PASS | 6 | 19 | identical: 1058 passed |
| toolz `568c2b8` | PASS | 5 | 40 | identical: 1 failed, 185 passed |
| tornado `7ed8156` | PASS | 40 | 380 | identical: 1173 passed, 157 skipped, 86 subtests passed |
| tqdm `9cf5a12` | PASS | 8 | 5 | identical: 168 passed, 11 skipped |
| trio `59d94d7` | PASS | 37 | 158 | identical: 9 failed, 536 passed, 62 skipped, 1 xfailed |
| typer `a80f6e5` | PASS | 5 | 27 | identical: 409 failed, 956 passed, 35 skipped, 2 xfailed |
| virtualenv `ca4025d` | PASS | 5 | 3 | identical: 356 passed, 31 skipped |
| voluptuous `44593ce` | PASS | 4 | 51 | identical: 182 passed |
| wcwidth `1e6b48d` | PASS | 3 | 4 | identical: 1 failed, 1364 passed, 10 skipped |
| werkzeug `6a604e0` | PASS | 15 | 15 | identical: 1024 passed |
| wrapt `f1586a5` | PASS | 5 | 4 | identical: 1 failed, 1222 passed, 8 skipped |
| xmltodict `6e29fba` | NO_CHANGE | 0 | 0 | no proposal at the default minimum |
Totals: PASS 75, NO_CHANGE 13, BROKEN_KNOWN 3, and no project unsupported,
broken, crashed, or timed out. A `BROKEN_KNOWN` verdict requires
every newly failing test to match a failure the manifest names, so a
documented limitation cannot hide an unrelated regression; the three are
frame-relative (pluggy's `stacklevel`, glom's literal traceback assertions)
and source-observing (lark's standalone generator copies marked regions of
named source files). Suites that already failed before transformation
(attrs, python-dateutil, toolz, wcwidth, wrapt) fail identically after it;
those failures are missing package metadata or environment-specific.

The corpus is 91 projects: 39 libraries from the first batch, 20 larger
or application-shaped projects added on September 13, 2026, and 32 more on
September 14 in three batches (oauthlib, parso, rich, typer, structlog,
cattrs, isort, PyJWT, xmltodict, arrow, six, tornado, trio, h11, jmespath,
dpath, loguru, DeepDiff, MarkupSafe, sniffio, outcome, Mako, html5lib,
bleach, feedparser, Cerberus, croniter, PrettyTable, parsimonious,
inflection, pyrsistent, jsonpatch). The September 13 batch was: Flask,
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

The September 14 additions came in two batches. The first of them found two
more engine defects: a base-class name resolved by matching any class of that
name across the project, so a helper for oauthlib's OAuth 2 endpoints was
inserted into the OAuth 1 base class; and a helper placed in a function looked
up by name landed in the wrong same-named method, so tornado's call sites
raised `NameError`. Both are fixed with fixtures and review rows. The second
batch of ten (trio through Mako) found no engine defect, the first clean
batch; the harness also gained a rerun of any test whose result differs
between the two trees, which reclassifies a flaky difference as a pass, and it
now ignores the pytest warning tally, which shifts with line numbers rather
than test outcomes. A third batch of ten (html5lib, bleach, feedparser,
Cerberus, croniter, PrettyTable, parsimonious, inflection, pyrsistent,
jsonpatch) was also clean, so two consecutive batches of previously unseen
projects found no defect and the stopping rule is met.

## Verification gates on the 1.414 tree

- Python 3.11, 3.12, and 3.13: 1,257 tests passed on each, plus 34 subtests.
  Coverage is 89% against the unconditional 85% gate.
- Black, Flake8, strict mypy, Bandit, and all pre-commit hooks pass. Hostile
  fixtures are excluded from formatting because their layout is what they
  test.
- 26 single-file golden outputs: 2 changed (attribute and arithmetic
  arguments became thunks), reviewed by hand and executed. 3 cross-file
  goldens were stale relative to their inputs and the committed insertion
  policy and were regenerated.
- Hostile batteries at 1.414: 84 single-file fixtures, of which 43 were
  transformed and 41 rejected, and 6 cross-file fixtures; all preserve
  program output. (At `5ff2458`, September 19, 2026: 129 single-file
  fixtures, 87 transformed and 42 rejected, and 12 cross-file fixtures, 11
  transformed.)
- Performance with the defaults: the 5,000-line `more.py` reaches a fixed
  point in 23 s. See KNOWN_LIMITATIONS.md for package-level timings.

## Historical 1.414 limitations and outstanding work

- Hosted Linux CI has not run; the workflow is unchanged and local runs are
  macOS. Run it against the published commit before citing it.
- No wheel or source distribution was rebuilt for 1.414; `just release 1.414`
  prepares them and the maintainer approves hashes.
- Security support policy, confidential reporting channel, and repository
  visibility remain maintainer decisions recorded in RELEASING.md.
- Thunked arguments read as `__param_0()`. An inlining pass passes a thunk
  eagerly when the helper evaluates it first, once, and unconditionally,
  which covers the common `x = E` opening; thunks used repeatedly or after
  an effect stay deferred. The `rename-helpers` workflow can name the rest.
- The ecosystem corpus was 91 projects at 1.414 and is 141 now; the
  largest, networkx and Sphinx, are refactored within hours-long budgets.
  No measurement yet says how much of each transformed block its suite
  exercises. The stopping rule for calling the pass rate stable was two
  consecutive batches of ten previously unseen projects with no new engine
  defect; the fourth and fifth batches (September 14–15) each met it, after
  the first three batches found defects. The September 17 batch of fifty
  found defects again, all in the features added since 1.618 (formatting,
  annotations, reuse) and in the cycle guard, rather than in the extraction
  core. That is stability on the sampled corpus, not a guarantee for an
  arbitrary project, which is why unattended use is still not claimed.
