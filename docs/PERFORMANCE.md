# Performance and timing

[Documentation index](README.md)

Use this guide to estimate a run and understand the limits of the published measurements.

On this page:

- [Measurement conditions](#measurement-conditions)
- [October 6 optimization experiments](#october-6-optimization-experiments)
- [October 5 repaired candidate comparison](#october-5-repaired-candidate-comparison)
- [October 2 complete CLI comparison](#october-2-complete-cli-comparison)
- [Planning and bounding a run](#planning-and-bounding-a-run)
- [Historical end-to-end timings](#historical-end-to-end-timings)
- [Why larger projects take longer](#why-larger-projects-take-longer)

## How long it takes

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
ownership memo reduced its isolated repeated-input CPU cost by about 93 percent,
but that phase reduction has not translated into a measurable complete-command
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
Matrix and corpus reruns remain pending explicit authorization.

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
October 5 result above supersedes it for current-release performance.

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
