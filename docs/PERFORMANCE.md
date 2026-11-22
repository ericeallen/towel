# Performance

A module takes seconds. A 30,000-line package takes tens of seconds without
type checking and a few minutes with it. A 150,000-line project with strict
mypy and Pyright can take an hour or more: checking each change is most of
the time.

On this page:

- [How long it takes](#how-long-it-takes)
- [Keeping a run short](#keeping-a-run-short)
- [Memory](#memory)
- [Why some projects take longer](#why-some-projects-take-longer)
- [Earlier measurements](#earlier-measurements)

## How long it takes

| Project | Size | Time |
| --- | --- | --- |
| One module | 2,000 lines | seconds |
| boltons, Click | 24,000 to 29,000 lines | tens of seconds |
| Towel's own source | 46,000 lines | 35 s without types and formatting; 2 min 20 s with mypy |
| pygments | 137,000 lines | about two minutes |
| networkx, Sphinx | 150,000 to 200,000 lines | several minutes to over an hour |

These were measured with releases before 2.0, on one core unless noted, on an
Apple M5 Max (18 cores, 128 GiB). Native timings use macOS; the release-corpus
timing below uses a Docker Linux VM. Other machines will differ; relaxed mode,
2.0's default, extracts more, and in a typed project each extraction is
checked.

## Keeping a run short

- **`--no-types`** skips type checking, which is several times faster on a typed
  project and saves little on an untyped one. Use it where the project is not
  type-checked anyway.
- **`--time-limit SECONDS`** gives up after that long, writes nothing and exits
  124. Writing, once started, always finishes.
- **`--max-refactorings N`** stops after `N` changes; review, commit and run again.
- **A smaller target**: one package, or `--exclude` for tests and generated code.
- **`towel preview --quick`** lists likely candidates in seconds, without the
  checks; it is a sketch, not the result.

A complete `preview` does all the work of `dry` and then discards it. If you
mean to keep the result, run `dry` into a copy or on a branch instead of
previewing first.

## Memory

One analysis process holds the parsed project: tens of megabytes for a module,
about 250 MB for a 140,000-line project. Towel forks workers for a large
project, at most eight and no more than the cores and memory allow, and each
worker adds its own copy: a 200,000-line project peaked near 7.4 GB across 20
processes on an 18-core machine before 2.0 capped the workers. On a small
machine, or when running several projects at once, use `--jobs 1` or
`TOWEL_WORKERS=1`, which keeps the single-process figure.

## Why some projects take longer

Towel compares blocks within each file pairwise, so a few large modules full
of near-identical methods cost more than many small ones. `--min-lines` raises
the smallest block considered, and `--max-pairs` caps the pairs one analysis
compares, leaving out the largest groups of similar blocks with a warning.
Within a single function, Towel searches the first 1,024 blocks that could
match another block of the function, in source order. This bounds the work on
long runs of similar statements. Use `--explain` to locate a limited search.

After each change, Towel analyses the changed files again, because one
extraction can expose another; a project with hundreds of extractions repeats
that hundreds of times, and in a typed project checks each one.

Relaxed mode, the default, finds more to extract than strict mode, and so
repeats that analysis more often. On a large, repetitive project the difference
is large: on networkx, strict mode applied 67 refactorings in about 13 CPU
minutes, while relaxed mode had applied 175 after 25 and was still going. To
keep such a run short, use `--max-refactorings N` and run again after review,
point Towel at one package, or use `--strict`.

## Earlier measurements

The Sphinx-only rerun at `29454ab` took 5,660 s (about 94 minutes) and changed
55 files. It ran alone in a Docker Linux VM with 18 cores and 8 GB of memory,
using Python 3.12, `TOWEL_WORKERS=1`, `--cross-module`, and default typing
(mypy and Pyright both strict). These are measured conditions, not minimum
resource requirements or time guarantees.

The dated investigations and release comparisons behind these figures, up to
1.949, are in [performance measurements](maintainers/PERFORMANCE_HISTORY.md).

[Documentation index](README.md)
