# Performance and timing

[Documentation index](README.md)

Use this guide to estimate a run and understand the limits of the published measurements.

The 1.947 builtin-mapping guard exposed a repeated large-module scan in candidate
`59aafb1`. The [repair and its limits](#1947-large-module-builtin-guard-repair)
below distinguish the completed corpus observation from partial diagnostic
profiles. No complete-command speedup is claimed for the subsequent repair.

The October 18 whole-command profile at `0b3494c` measured Rich +7.6% versus
1.792 on a shared native macOS/Python 3.12 host. Click's noisier difference
includes one more extraction. The maintainer subsequently requested scoped source-correspondence memoization
in 1.946. It is now implemented; the earlier profile does not measure its benefit.
See [the profile and attribution](RELEASE_1_946.md#where-the-remaining-time-goes-profiled-on-0b3494c).

The earlier October 10 container cohorts compare versioned 1.946 source `a6dc6ed` with
published 1.792 source `44d7795`: Voluptuous was 3.75% lower and Click 23.39%
slower in two shared-host samples per source per workload. Rich has zero actual
samples in that container campaign; the later native profile is separate. These observations do not certify
the final release or establish general restoration. The earlier `9d57b0d`
Click/Rich comparison retains its own 1.792 metadata, samples and limits.

On this page:

- [1.947 large-module builtin guard repair](#1947-large-module-builtin-guard-repair)
- [Partner eligibility and interval-analysis limits](#partner-eligibility-and-interval-analysis-limits)
- [October 10 versioned warning-guard cohorts](#october-10-versioned-warning-guard-cohorts)
- [October 10 Voluptuous regression investigation](#october-10-voluptuous-regression-investigation)
- [October 10 backslash-parity repair](#october-10-backslash-parity-repair)
- [October 10 Click and Rich assessment](#october-10-click-and-rich-assessment)
- [Measurement conditions](#measurement-conditions)
- [October 7 block-fact memo](#october-7-block-fact-memo)
- [October 6 optimization experiments](#october-6-optimization-experiments)
- [October 5 repaired candidate comparison](#october-5-repaired-candidate-comparison)
- [October 2 complete CLI comparison](#october-2-complete-cli-comparison)
- [Planning and bounding a run](#planning-and-bounding-a-run)
- [Historical end-to-end timings](#historical-end-to-end-timings)
- [Why larger projects take longer](#why-larger-projects-take-longer)

## How long it takes

### 1.947 large-module builtin guard repair

Frozen candidate `59aafb189f1d3feb2f97c433dabbf77815bb5a36` completed the
More-itertools corpus refactor in 474.037 seconds. Its original and generated
consumer suites both passed all 914 tests. The new definition-time builtin
mapping guard queried module bindings per candidate pair. Each query checked
warning eligibility before consulting the binding cache. The unchanged
174,732-character `more.py` exceeded the global lexical cache's
131,072-character admission cap, so these checks repeatedly tokenized it.

A separate transparent diagnostic observed 2,988 warning-eligibility calls over
five distinct complete source strings: 2,983 repeated calls, or 99.8327%.
The 174,732-character input alone accounted for 2,983 calls. Every call still
performed its original computation. A partial 44.947-second cProfile observation
attributed 39.921 seconds, about 89%, to lexical token scanning; it was interrupted
and is not a completed CLI measurement. The independently verified published
1.946 wheel, SHA-256
`090c158f8dbd6795dbbe7e18d0bcffbbb884141f16d04100663c8d4ccc45c0cd`,
completed a separate diagnostic run with the same consumer Python bytes and
third-party distribution versions. All three resulting Python files matched
the candidate's completed corpus output byte for byte. These observations
identify the repeated-input cause; differing profiling and concurrent-work
conditions do not establish an unprofiled speed ratio.

The repair retains one immutable builtin-mapping verdict per live scope
analyzer, keyed by the exact source and parser configuration. Only sources
proven warning-free by the native parsing policy are admitted. Each analyzer
retains at most one source with an 8 MiB storage budget, including a conservative
reserve for a separately cached UTF-8 representation; weak ownership bounds
its lifetime. This is a per-analyzer bound, not a process-wide memory bound.
The memo retains no AST or block facts. Source or parser-limit changes invalidate
the entry; warning-capable sources, over-budget inputs and disabled memoization
retain the ordinary computation. The global lexical cache's 131,072-character
admission cap and 16 MiB budget are unchanged.

The `59aafb1` corpus was intentionally stopped after eight completed project
results because the changed runtime needs fresh validation. Exit status 143
records that stop, not an out-of-memory failure. Its completed checks and
partial diagnostics remain source-bound evidence under
`/private/tmp/towel-release-1947-final-validation/`; they are not release
acceptance for the later repair. Final source/artifact checks and complete
CLI comparisons belong to the external release record. No production timing
or performance-restoration claim follows from these profiles.

### Partner eligibility and interval-analysis limits

Before enumerating blocks, the engine identifies structural function partners.
With the default within-module policy, a function alone in its module does no
candidate-block enumeration, even during a directory run. Cross-module mode
can make functions in different modules eligible, so a singleton file does
not imply a fast exit there. Existing generated helpers remain potential
partners. Incremental changed-file filtering still follows the global pair
budget calculation, preserving which buckets and proposals it admits.

Eligible functions share per-statement facts and per-suite prefix summaries
for numeric signatures and exact negative guards. Caller-sensitive return
coverage and narrowing checks retain their full proofs. Exact statement
slices and statement-type tuples are still materialized: storage can grow
with the total lengths of enumerated intervals. The pair budget limits evaluated
pairs, not this earlier storage or overall memory. A budget warning counts
blocks from functions that can pair; unrelated singleton blocks no longer
inflate that count. These are bounded analysis improvements, not a
whole-project timing or memory guarantee. The dated measurements below retain
their original source and artifact identities.

### October 10 versioned warning-guard cohorts

The [portable record](../tests/release_evidence/1.946-warning-guard/README.md)
and [raw samples](../tests/release_evidence/1.946-warning-guard/measurements.json)
bind eight complete cold default CLI observations to versioned 1.946 source
`a6dc6ed4c75fa9c1b65aa9029f2a6361ec7dce77` and published 1.792 source
`44d779563578a32e24f8213fba96f43c6698ab43`.

| Workload | Published mean (range), seconds | 1.946 candidate mean (range), seconds | Change | Samples per source |
| --- | ---: | ---: | ---: | ---: |
| Voluptuous | 90.984 (90.572–91.396) | 87.569 (87.522–87.616) | −3.75% | 2 |
| Click | 47.165 (46.816–47.514) | 58.195 (57.338–59.052) | +23.39% | 2 |

Each cohort ran R,F,F,R. Paired fixed/released ratios were 0.957611/0.967360
for Voluptuous and 1.206768/1.261359 for Click. The CLI timer includes typing,
formatting, cross-module extraction, fixed-point materialization and final
checking; it excludes setup, settling and original before/after tests. No
profiler or census ran during these samples. Per-CLI waited-child CPU is
unavailable, and retained whole-container CPU/idle context must not substitute
for it. Resources were two CPUs, 6 GiB RAM, **12 GiB combined RAM+swap**, one
worker, CPython 3.12.14 and disabled networking in the same pinned image. These
caps differ from the earlier 6 GiB combined comparison below; do not pool means.

Voluptuous retained 182 passes in each before/after pair, applying 39 released
versus 40 candidate refactorings across four files. Click retained its exact
2,035 passes, 24 existing pager failures, 24 skips, one xfail and 31,000
deselections. All 24 displayed failure headers and E-lines matched before/after
in all four Click samples. No hidden object state or upstream CI claim follows.
Click applied four released versus five candidate refactorings across three
files. Repeated output matched within each source arm: 10 Python files plus
`py.typed` and `tests/tests.md` for Voluptuous, 17 Python files plus `py.typed`
for Click. Capability differences remain explicit.

All eight source, installed artifact, dependency, input, original-pair, output,
context and physical closure checks passed. Every one-second telemetry row
was retained, including idle below 70%; whole-container minima were 42–48%
for Voluptuous and 43–47% for Click. The fresh 20-second preflight required
at least 80% idle. This is shared-host evidence, not isolated-host certification,
per-core or full contention attribution. There was no replacement of an actual
sample. Rich has **zero actual samples** here after strict preproduct settling/
preflight failures; its complete cohort is pending. The restoration target
remains unresolved, and fresh full release validation remains required.

### October 10 Voluptuous regression investigation

The first versioned 1.946 candidate, `a53e351`, was stopped during the required
141-project corpus after 31 completed projects. Voluptuous's complete refactor
had not returned after approximately 62 minutes of wall and CPU time. The
released 1.792 run completed in 118.65 seconds on matching pinned source,
CPython 3.12.14, checker and formatter versions, harness and container settings.
The stopped run is incomplete negative evidence, not a measured completed-command
ratio or a corpus pass. Its raw inputs, logs, partial outcomes and closure
records remain under the external handoff
`20261010T113508Z-codex-1946-release-validation`.

A separate bounded production-path replay completed 1,000 original module-fact
queries over six distinct native source/parser-configuration inputs, with 994
repeated queries. The unchanged Voluptuous test module accounted for 182 queries
and 364 native parses: all missed the existing fact cache because an overbroad
warning guard rejected ordinary raw-regex brace escapes. Native parsing of that
exact text under always, ignore and error warning filters emitted no warnings
and produced identical syntax trees. The other five inputs produced 813 cache
hits. The replay stopped deliberately at its bound; it is neither a full
refactor nor a timing benchmark.

The repair narrows the guard conservatively: escapes before every possible
formatted-string start proceed to the existing lexical checks. Potentially
warning-capable formatted strings continue to parse freshly. Cache capacity,
source ownership, parser limits and diagnostic behavior remain unchanged.
The complete Voluptuous and Click cohorts above now provide source-bound
observations of repaired `a6dc6ed`. They do not turn the replay into a timing
sample. At that checkpoint, fresh Rich measurements and full release validation remained pending;
no general restoration claim follows from the repair or these limited cohorts.

### October 10 backslash-parity repair

A separately bounded Rich replay established repeated exact source/configuration
queries that missed immutable facts because the warning guard matched the last
backslash of a complete even run. Exact native source observations were
warning-free under always, ignore and error filters. The parity-only refinement
matches complete odd runs, including the existing carriage-return case; paired
literal backslashes no longer cause this guard refusal. Existing prefix/token/
nesting checks remain authoritative, including conservative refusal of an
ordinary raw brace regex after an earlier possible formatted prefix. No cache,
warning filter, source budget or parser-limit policy was changed.

The integrated seven-file battery passed **479 tests**, with **221
version-conditioned skips**, on native Python 3.13. The generic native AST/
warning/error oracle is unchanged. Permanent fixtures cover complete even/odd
runs, later odd escapes, carriage returns, mixed/raw prefixes, conditional
templates and nested strings. Shared fact-construction fixtures cover ordinary
raw regexes and Markup/ANSI-like paired escapes; identical queries reuse one
construction, while changed sources and disabled memoization recompute with
identical semantic results. Separately, **3,719 finite external controls per
native Python 3.11–3.14** passed; these do not replace the integrated matrix.

No complete-command timing result for this later repair is claimed. The
completed Voluptuous/Click measurements above remain bound to pre-parity
`a6dc6ed` and their exact wheels; no sample is relabelled. At that checkpoint,
a new frozen source, fresh final release gates and source-bound performance
assessments remained pending. Raw scoped evidence is retained in
`20261010T161505Z-codex-1946-warning-guard-validation`, under
`parity-controls-r01` and `parity-integration-r01`. Construction counts and
finite native parity are not full cost attribution or release approval.

### October 10 Click and Rich assessment

At this October 10 checkpoint, the upcoming version was **1.946**, named for
ln(7) ≈ 1.945910149; no performance-restoration claim was established. The completed eight-sample assessment compared
`9d57b0d25f3ee743e0ce0035ed13df65b2344164` with released source
`44d779563578a32e24f8213fba96f43c6698ab43`. Each arm used a fresh offline wheel
built from its named source; both wheels declared **1.792**. The retained
[measurement record](../tests/release_evidence/1.946-preparation/measurements.json)
and [evidence guide](../tests/release_evidence/1.946-preparation/README.md) bind
all samples to their source, artifacts, inputs and external raw observations.

| Workload | Released source mean (range), seconds | Audited source mean (range), seconds | Difference | Samples per source |
| --- | ---: | ---: | ---: | ---: |
| Click | 46.527 (45.506–47.548) | 56.693 (55.535–57.850) | 21.85% slower | 2 |
| Rich | 99.773 (99.660–99.885) | 177.699 (177.256–178.142) | 78.10% slower | 2 |

Order was release, audited, audited, release for Click, then the same order for
Rich. The predeclared paired wall-time ratios were 1.220400 and 1.216656 for
Click, and 1.787487 and 1.774610 for Rich. Mean waited-child CPU time rose from
52.301 to 62.492 seconds on Click and from 99.862 to 177.751 seconds on Rich.
This accounting includes waited descendants; it does not attribute CPU to a
particular checker or analysis phase. The restoration target remains unmet.

The actual installed default CLI ran with typing, formatting, cross-module
extraction and final checking enabled. Each run had a fresh work volume,
environment and checker process state, one Towel worker, and the same pinned
Linux image under CPython 3.12.14 with two CPUs, 6 GiB memory and networking
disabled. Click used its 177 tracked input files at `6aabf099bf`; Rich used
553 at `9d8f9a372c`. Setup, host settling and the original before/after test
commands are excluded from CLI time. Dependency caches were reused; hardware
page caches were not flushed. No profiling or call census ran during timing.

These are **shared-host observations**, with two samples per source and
workload, not isolated-host certification or a general throughput claim. Every
preflight required 20 fresh one-second intervals at or above 80% aggregate idle.
Continuous one-second CPU observations and ten-second native/container context
covered setup through the final tail. Runtime idle below 70% was retained and
disclosed, rather than classified automatically as unrelated contention. Native
snapshots cannot attribute all host CPU or detect every brief contender.
Across the Click samples, runtime idle minima ranged from 44% to 56%, with
9.26%–42.11% of intervals below 70%; Rich minima were 50%–57%, with
9.47%–9.95% below 70%. No interval was filtered out of the report.

The original declared before/after test commands matched exactly in all eight
samples: Click retained 2,035 passes, 24 baseline failures, 24 skips, one xfail
and 31,000 deselections; Rich retained 948 passes, eight baseline failures and
25 skips. All visible failure records matched; the eight pytest-truncated Rich
portions remain unverified. Generated Python output matched its own source's
reference and repeated output within each arm: 17 files for Click and 100 for
Rich. Released Click applied four refactorings versus five under the audited
source, both across three files; both Rich arms applied 15 across seven files.
The comparison preserves those capabilities and does not normalize time by
refactoring count.

A first Rich preflight reported 76% idle and launched no production sample; it
remains retained. Completion used separately declared Rich labels after bounded
host settling before each unchanged preflight. Waiting ran no Towel analysis or
checker and was excluded from CLI time. Previous rejected, confounded and
instrumented runs remain historical evidence. No actual sample was replaced;
there was no fallback, confirmation substitution or oracle waiver. All owned
workers closed, all eight containers exited with code zero and no OOM, and the
source and frozen measurement files stayed unchanged.

### Historical 1.792 development comparisons

The October 5 repaired 1.792 runtime was **11.9% slower** than published 1.772 on the
October 5 complete typed, formatted Packaging comparison: 43.43 seconds versus
38.80 seconds. This is one controlled fixture, not a whole-corpus estimate.
The candidate performs more extractions, but that does not establish improved
throughput or justify dividing elapsed time by the refactoring count.

A controlled comparison during 1.792 development used the actual published
1.772 wheel: Sphinx's first Pyright reveal fell from 931.07 to 8.63 seconds,
with identical requests and answers. Packaging's full command remained
effectively flat (39.98 versus 39.77 seconds) while the candidate applied one
more extraction. These are separate workloads and timing boundaries; no
whole-corpus speedup is established. The
[comparison report](proposals/published-1772-comparison.md)
records the precise source/artifact identities, alternating runs and ordinary
background activity. The older timings below retain their historical scope.

### October 7 block-fact memo

The parameter-audit remediation uses source `04d2710`, runtime tree
`792623860332f187d72fa263a9e2aeb6976d930c`, after the complete native suite finished.
Both arms use the same corrected source; the control restores only the previous
fresh free-variable query. Each of four real CLI commands uses a fresh process,
input/output copy and checker workers, typing and formatting enabled,
`TOWEL_WORKERS=1`, and ABBA order on the same 22-module Packaging 26.3 fixture.
Input Python/configuration bytes remain unchanged. All Python outputs are
byte-identical: 18 refactorings across eight changed files.

| Block-query memo | Mean complete-command seconds | Samples |
| --- | ---: | ---: |
| Disabled control | 56.72 | 2 |
| Enabled | 56.70 | 2 |

No wall-clock improvement is established. Ordinary background activity varied;
retained per-arm CPU snapshots do not describe a dedicated idle machine.
A separate instrumented six-function public fixed-point control makes 160
queries in both arms; fresh computations fall from 160 to 80 with identical
output and observed results/effects. That work reduction is scoped to a single
unification's immutable inputs, rather than a cross-run cache or CLI speed claim.
The [retained evidence](../tests/release_evidence/1.792-parameter-audit-fixes/README.md)
binds drivers, raw observations, source, configuration and outputs. This is no
new comparison with published 1.772 and does not clear matrix/corpus gates.

### October 6 optimization experiments

The audit repairs changed the runtime after the October 5 freeze. Those older
release gates and timings do not validate the new source. Complete installed CLI
controls for the first two optimizations used correctness checkpoint
`e0c185675e094eb14db305eaadd1a918934eaf55`; the third used
`8626d50e359a6aabfe19e34514bcb8b29ef768e2`. Each comparison used the same Python/dependencies and fresh copies of the same Packaging slice.
Each comparison used two samples per arm in ABBA order, with default typing,
formatting, automatic workers and final checking. All outputs were byte-identical:
19 refactorings across 8 files.

| Optimization | Control mean seconds | Enabled mean seconds | Measured wall benefit |
| --- | ---: | ---: | --- |
| Immutable statement ownership memo | 49.80 | 49.91 | None established |
| Witnessed Any-result retry pruning | 47.49 | 48.59 | None established |
| Exact completed-refusal retention | 47.30 | 47.37 | None established |

These small samples include changing ordinary host activity. They do not establish
a regression, a general speedup, or a new comparison with published 1.772. The
earlier phase-only ownership experiment (`3fdce9d` versus `77f6793`)
replayed 4,705 calls over 941 distinct fixture-derived inputs, with identical
results. Mean CPU cost fell from 0.279 to 0.020 seconds, about 93 percent.
That bounded repeated-call diagnostic predates the later frame-transfer
repairs and measures a phase, rather than a complete command. Its reduction has not translated into a measurable complete-command
benefit. The second optimization preserves precise signatures and skips only
parameter erasure that cannot repair a witnessed fixed-Any return diagnostic.

The third implementation retains completed type refusals only for an unchanged
whole-project revision and exact candidate/checker/rendering context. Its focused
controls pass. Instrumented complete CLI runs each performed 185 mypy exchanges
and 30 materializations: all five earlier completed refusals predated the last
project change, so revision 19 correctly invalidated them before rehearing. This
fixture supplies no applicable repeated refusal for the new optimization to skip.
The [retained evidence](../tests/release_evidence/1.792-performance-optimization/README.md)
binds all three comparisons to source, wheel, dependencies and output hashes.
These measurements predate removal of the inner rendered-variant refusal memo.
That removal makes each submitted variant issue a fresh project-check request;
no new timing for it is claimed here. The outer completed-proposal rehearing
cache remains separately qualified. Matrix and corpus reruns were pending
explicit authorization at that October 6 checkpoint; the completed 1.792
publication has its own [release record](PRODUCTION_READINESS.md#1792-published-release-october-8-2026).

### October 5 repaired candidate comparison

The completed comparison ran from 23:06 to 23:14 UTC on October 5, after the
matrix, native tests, fuzz, self-refactoring and fresh corpus had finished.
It used source commit `00b4f4f27eeb45a17236fc7af4e74a3881339b23`, whose runtime
tree is `2b1181b59a50599f339b9dc552b383836e441c3d`, unchanged since audit-fix
commit `c5311b4`. Each run started a fresh process and input copy, executing
`towel dry <input>/packaging <output> --no-interactive --progress detail`
with typing, formatting and automatic worker selection enabled.

| Runtime | Mean seconds | Timing variation (CV) | Refactorings | Changed files |
| --- | ---: | ---: | ---: | ---: |
| Published 1.772 | 38.802 | 1.47% | 18 | 8 |
| Earlier 1.792 control (`000d751`) | 35.375 | 0.85% | 19 | 9 |
| Repaired 1.792 (`00b4f4f`) | 43.425 | 0.87% | 20 | 9 |

Four observations per arm ran in two balanced blocks, each ordered
published, earlier control, repaired, repaired, earlier control, published.
The repaired candidate was 11.51% and 12.32% slower than published 1.772 in
the respective blocks, and 22.76% slower than the earlier control overall.
All 12 commands exited zero without untyped fallback. Input hashes stayed
unchanged and repeated Python outputs were byte-identical within each arm;
outputs differ between versions. These observations resolve a roughly 10%
difference, not sub-percent precision. They do not isolate the cause of the
slowdown or establish identical-work performance.

The host was an Apple M5 Max with 18 cores and 128 GiB RAM, native macOS and
Python 3.13.7. All arms used identical installed dependency versions apart
from Towel, including mypy 2.3.1, Pyright 1.1.414 and Black 26.5.1. The
controlled Packaging 26.3 fixture contains 22 Python modules and a strict
mypy configuration targeting Python 3.10. The actual published wheel's
SHA-256 is `4af0f6067d830e396aecf82320d69cb82343a848f24b7d1f3e2a152018605683`.
The repaired wheel's SHA-256 is
`1a3e3b81dec76cd1330e256f42756b833a0530198e3a0bf7ade0237420560683`;
its installed runtime files were checked against the frozen source.

Background Python and Spotlight activity remained. The preceding host survey
showed 80–93% CPU idle and no change in swap counters; per-process load was
recorded every five seconds during timing. This capacity experiment made
the earlier arbitrary CPU thresholds observations rather than automatic
rejection, retaining identity, dependency, input, output and typed-CLI checks.
It does not claim a quiet-host preflight pass, continuous whole-system
telemetry or an interference-free machine.

The [retained observations](../tests/release_evidence/1.792-audit-fixes/README.md)
include all 12 timings and CLI logs, runtime/input/output hashes, original
completion records and the capacity experiment's declared changes. Complete
background-load records remain in the external handoff. The following
October 2 results describe an earlier runtime, not this repaired candidate.

### October 2 complete CLI comparison

After removing reflection-specific analysis and sharing repeated typing
evidence, runtime commit `000d751` completes the controlled Packaging 26.3
workload about 13% faster than the published 1.772 wheel with typing,
formatting and automatic worker selection enabled:

This is a historical development result. The repaired candidate's completed
October 5 result above superseded it at that preparation checkpoint; neither
measures the later released 1.792 source or the 1.946 artifacts prepared afterward.

| Complete command settings | Published 1.772 | Candidate `000d751` |
| --- | ---: | ---: |
| Typing and formatting enabled, automatic workers | 41.59 s | 36.24 s |
| Typing enabled, formatting disabled, automatic workers | 39.25 s | 34.75 s |
| Typing enabled, formatting disabled, one worker | 40.23 s | 36.01 s |

Each entry is the mean of two fresh-process runs in balanced order, using
identical installed dependencies and immutable inputs. The fixture contains
22 Python modules and strict mypy configuration targeting Python 3.10; Towel
runs under Python 3.13.7. The candidate applies 19 refactorings across nine
files; 1.772 applies 18 across eight. Candidate output matches the preceding
candidate byte for byte, both with and without formatting. These measurements
establish this workload's improvement, not a whole-corpus speedup. Spotlight
background activity was present; validation did not overlap timing runs.

The preceding candidate spent about 26 seconds waiting for 162 mypy requests.
Inference-local reuse reduces that to 148 requests while preserving all 78
logical subtype batches and their 375 questions and answers. Immutable
statement summaries also avoid repeated narrowing walks across overlapping
source windows. Every fresh subtype build still validates its evidence, and
unknown answers are retried. All project checks and final cold validation
remain. This differs from the cold-analysis fixture's much larger speedup;
that fixture does not measure a complete command.

The [retained measurements](../tests/release_evidence/post-1772-typed-cli/README.md)
include every timing sample, runtime and input hashes, output identities,
dependency versions and conditions. The earlier comparisons above are
historical results from their named source states.

### Measurement conditions

Every time in this section was measured on an Apple M5 Max (18 cores,
128 GiB), and each figure names its commit and conditions. Native timings use
macOS; the release-corpus timing below uses a Docker Linux VM. The
[measurement environment](KNOWN_LIMITATIONS.md#measurement-environment)
records the rest. Expect other hardware to differ.

### Planning and bounding a run

There is no time budget; progress is reported per phase on stderr, so stdout can be piped or redirected. Ctrl-C and SIGTERM both end the run cleanly (an interrupted apply is rolled back from its journal), and a reader that closes the pipe early is not an error.

Pairing is quadratic in the number of candidate blocks per file, so a few large modules with many near-identical methods are the worst case, not total line count. With N near-identical blocks in one file every pair proposes the same N-site extraction; each distinct proposal is kept once, but evaluating the pairs still grows as N cubed until the first application collapses them into one helper. `--max-pairs` bounds the candidate pairs one analysis evaluates (the largest groups of similar blocks are left out, with a warning) and `--min-lines` raises the smallest block considered.

### Historical end-to-end timings

Rough expectations with the defaults, one core: a 2,000-line module takes
seconds; Towel's own source (about 46,000 lines, by `wc -l` over the 80
modules of `src/towel`) runs to a fixed point in about 2 minutes 20
seconds, or 35 s without the type checker and formatter (commit `8cb8b8c`,
September 24, 2026, with other work loading the machine); a package the size of boltons (24,000 lines) or Click (29,000
lines) takes tens of seconds, and pygments (137,000 lines) about two
minutes, by the dated measurements in the
[performance section of Known limitations](KNOWN_LIMITATIONS.md#performance),
which is the one table of package timings and says what each figure
measured; the largest projects in the ecosystem check, networkx and Sphinx
(150,000 to 200,000 lines), take from several minutes to over an hour.

The Sphinx-only rerun at `29454ab` took 5,660 s (about 94 minutes) and changed
55 files. It ran alone in a Docker Linux VM with 18 cores and 8 GB of memory,
using Python 3.12, `TOWEL_WORKERS=1`, `--cross-module`, and default typing
(mypy and Pyright both strict). These are measured conditions, not minimum
resource requirements or time guarantees. The typed checks are nearly all of
that time; the [release report](PRODUCTION_READINESS.md)
distinguishes this rerun from the preceding full corpus.

### Why larger projects take longer

The two largest projects in the ecosystem check, networkx and Sphinx, are the slowest because their directory fixed point re-pairs the project after each batch of applied changes; later global passes re-pair only the files rewritten since the previous one, which changes no proposal (the argument is in [the architecture document](ARCHITECTURE.md#incremental-global-passes-and-why-they-are-exact)), and the ecosystem check still gives both extended budgets.

Forking cuts the wall time of a large project several-fold on a multi-core machine. With the type checker and formatter installed, the defaults add to an annotated project's time in proportion to the number of applied refactorings, each of which is type-checked: Towel's own source (commit `8cb8b8c`, September 24, 2026, one core) applies 22 refactorings in 35 s with `--no-types --no-format` and 21 in 138 s with the defaults, its one configured checker, mypy, verifying the complete prospective project through an owned mypy worker that forks a child per build (a project that configures pyright is also checked by a long-lived pyright language server over a private copy of the project).

At `5ff2458` (September 19, 2026), on half as much source and with mypy held in-process, the same runs took 8.4 s and 11.9 s, and in-process mypy raised peak memory from about 174 MB to about 894 MB.
