# Requirements and resources

[Documentation index](README.md)

Support requirements are separate from resource measurements. The measurements below describe specific workloads and machines, not minimum hardware.

On this page:

- [Platform](#platform)
- [CPU](#cpu)
- [Memory](#memory)
- [Disk](#disk)

## Requirements

### Platform

Python 3.11 to 3.13 on a POSIX system (macOS or Linux). Applying changes needs POSIX filesystem semantics. Parallel analysis uses the `fork` start method; under other start methods analysis runs on a single core. Windows is not a supported or validated release platform.

### CPU

One core is enough. The tool parallelizes a large analysis by forking one worker per core, which speeds up big projects but changes nothing about the result; `TOWEL_WORKERS=1` keeps it on one core and `TOWEL_WORKERS=N` caps the workers.

### Memory

Every figure here was measured on an Apple M5 Max (18 cores, 128 GiB); [the known limitations](KNOWN_LIMITATIONS.md#measurement-environment) record the rest of the environment. Measured in September 2026: a single analysis process holds the parsed modules and its caches: tens of megabytes for one file, about 250 MB for a 140,000-line project.

Forking multiplies that by the worker count, because each worker starts as a copy-on-write fork whose caches then diverge; a 200,000-line project on an 18-core machine peaked near 7.4 GB across 20 processes.

The tool estimates the parent's size against physical memory at fork time and caps the workers at roughly a third of RAM, but the estimate is not a guarantee. On a memory-constrained machine, or when running several large refactorings at once, set `TOWEL_WORKERS` low; at `TOWEL_WORKERS=1` the footprint stays at the single-process figure.

### Disk

Both in-place and out-of-place refactoring need temporary space for staged changes and recovery journals, and both copy the whole project's Python sources, stubs and configuration into a private temporary stage, removed when the run ends, where the refactoring is done.

An out-of-place run also copies the target in full, and writes only the refactored target to the output directory; an in-place run writes back only the files it rewrote.

Optional type checking adds, for the life of the run, a second copy of the project's checker inputs for pyright and a mypy cache directory; both are removed when the oracle is closed. Recovery journals retain original source bytes until resolved. See
[recovery and interrupted writes](CLI_GUIDE.md#recovery-and-interrupted-writes)
for their disk use, blocking rules and recovery commands.
