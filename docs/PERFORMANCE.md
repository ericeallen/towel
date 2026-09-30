# Performance and timing

[Documentation index](README.md)

Use this guide to estimate a run and understand the limits of the published measurements.

On this page:

- [Measurement conditions](#measurement-conditions)
- [Planning and bounding a run](#planning-and-bounding-a-run)
- [Historical end-to-end timings](#historical-end-to-end-timings)
- [Why larger projects take longer](#why-larger-projects-take-longer)

## How long it takes

A controlled comparison during 1.792 development used the actual published
1.772 wheel: Sphinx's first Pyright reveal fell from 931.07 to 8.63 seconds,
with identical requests and answers. Packaging's full command remained
effectively flat (39.98 versus 39.77 seconds) while the candidate applied one
more extraction. These are separate workloads and timing boundaries; no
whole-corpus speedup is established. The
[comparison report](proposals/published-1772-comparison.md)
records the precise source/artifact identities, alternating runs and ordinary
background activity. The older timings below retain their historical scope.

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
