# Requirements and resources

[Documentation index](README.md)

Support requirements are separate from resource measurements. The measurements below describe specific workloads and machines, not minimum hardware.

The supported interpreter range is Python 3.11–3.13. Package metadata permits
installation on newer Python versions so users can evaluate them, but Towel
warns that those interpreters are outside the validated range. Installation
success is not a support guarantee. See [compatibility](COMPATIBILITY.md).
Dated measurements below retain their original source identities and do not
establish results for a later artifact.

On this page:

- [Platform](#platform)
- [CPU](#cpu)
- [Memory](#memory)
- [Disk](#disk)

## Requirements

### Platform

Python 3.11 to 3.13 on a POSIX system (macOS or Linux). Applying changes needs POSIX filesystem semantics. Parallel analysis uses the `fork` start method; under other start methods analysis runs on a single core. Windows is not a supported or validated release platform.

Pull requests and pushes to `main` or `release/**` run the full Linux suite on Python 3.11–3.13.
A macOS Python 3.13 integration job builds and installs the wheel, verifies that
the tests import that installation, and exercises CLI behavior, file publication,
recovery and checker/formatter process lifecycle. This narrower macOS lane
complements the full Linux matrix; it does not run the complete corpus.

### CPU

The October 10 Click/Rich comparison used a pinned Linux container on an
18-core Apple M5 Max host, limited to two CPUs and 6 GiB memory with one Towel worker.
That is a measured setup, not a minimum or isolated-host guarantee. All
runtime CPU shortfalls were retained. [Performance](PERFORMANCE.md#october-10-click-and-rich-assessment)
reports the slower complete CLI results and their shared-host limits.

One core is enough. The tool parallelizes a large analysis by forking one worker per core, which speeds up big projects but changes nothing about the result; `TOWEL_WORKERS=1` keeps it on one core and `TOWEL_WORKERS=N` caps the workers.

### Memory

The completed 1.947 corpus used one project worker and one Towel worker in a
six-CPU, 6 GiB container with no swap. All 141 projects completed without a
phase timeout or signal termination. This validates that specific sequential
campaign, not a universal memory bound or concurrent-workload guarantee.
The final container was not OOM-killed; final lifetime peak memory was unavailable.
See the [publication record](PRODUCTION_READINESS.md#1947-published-release-october-26-2026)
for the observed peak's limits and the review of corpus outcomes.

The historical memory figures below were measured on an Apple M5 Max
(18 cores, 128 GiB); [the known limitations](KNOWN_LIMITATIONS.md#measurement-environment) record the rest of the environment. Measured in September 2026: a single analysis process holds the parsed modules and its caches: tens of megabytes for one file, about 250 MB for a 140,000-line project.

Forking multiplies that by the worker count, because each worker starts as a copy-on-write fork whose caches then diverge; a 200,000-line project on an 18-core machine peaked near 7.4 GB across 20 processes.

The tool estimates the parent's size against physical memory at fork time and caps the workers at roughly a third of RAM, but the estimate is not a guarantee. On a memory-constrained machine, or when running several large refactorings at once, set `TOWEL_WORKERS` low; at `TOWEL_WORKERS=1` the footprint stays at the single-process figure.

### Disk

Both in-place and out-of-place refactoring need temporary space for staged changes and recovery journals, and both copy the whole project's Python sources, stubs and configuration into a private temporary stage, removed when the run ends, where the refactoring is done.

An out-of-place run also copies the target in full, and writes only the refactored target to the output directory; an in-place run writes back only the files it rewrote.

Optional type checking adds, for the life of the run, a second copy of the project's checker inputs for pyright and a mypy cache directory; both are removed when the oracle is closed. Recovery journals retain original source bytes until resolved. See
[recovery and interrupted writes](CLI_GUIDE.md#recovery-and-interrupted-writes)
for their disk use, blocking rules and recovery commands.
