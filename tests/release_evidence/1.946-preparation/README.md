# Evidence for 1.946 preparation

[Documentation index](../../../docs/README.md) · [Performance guide](../../../docs/PERFORMANCE.md)

The [measurement record](measurements.json) retains all eight completed
development observations from October 10, 2026: two Click and two Rich samples
per version, each in release/fixed/fixed/release order. The sources are released
`44d779563578a32e24f8213fba96f43c6698ab43` and development
`9d57b0d25f3ee743e0ce0035ed13df65b2344164`. Both artifacts still carried
**1.792** metadata. These observations precede the version and documentation
update and do not certify the final **1.946** source, wheel or sdist.

The complete cold production CLI includes project checking, formatting and
final cold confirmation. Setup, waiting and original before/after tests are
outside that timing boundary. Fresh work volumes, environments and checker
state were used; no hardware page-cache flush is claimed. Runs used the same
pinned image, two container CPUs, 6 GiB memory and memory-plus-swap, no network,
one Towel worker, and pinned consumer revisions. No profiler was active during
the timing samples.

Each run passed its source, artifact, runtime, input, dependency, driver and
resource checks. Original before/after counts and visible failure payloads
matched, and generated outputs matched each version's reference. Click retained
24 baseline failures, Rich eight; eight hidden truncated Rich diagnostic portions
remain unverified. Click performed four released versus five development
applications; Rich performed 15 with each. Counts are observations of the
selected environments, not claims that upstream projects fail their own CI.

This is **shared-host observational evidence**. Each run passed a fresh
20-interval preflight at at least 80% aggregate CPU idle. Rich additionally used
a separately measured bounded settling period. During the runs all one-second
CPU intervals were retained, including idle values below 70%; no actual sample
was filtered or replaced. Aggregate CPU includes the owned VM and background
work. Native snapshots cannot establish complete attribution, per-core placement,
thermal state, disk or memory contention, or every brief competing process.
The eight samples were accepted as observations, not isolated-host certification.

The compact JSON contains the exact CLI wall and waited-child CPU samples,
observed runtime idle summaries, test counts, application/output counts, artifact
hashes and qualifications. It omits host process arguments and private paths.
Its `provenance` binds the original independently checked aggregate by SHA-256.
Full raw receipts, continuous CPU rows, query spans and closure records remain
in the shared handoff `20261009-performance-recovery-01a11b7c`, under
`observational-assessments-20261010-rich-r03`, `click-balanced-series-r07`,
`rich-balanced-series-r09` and `runs`.

All owned workers and all eight containers were independently confirmed closed;
no active containers remained. The source and frozen instruments stayed unchanged.
Earlier failed preflights and excluded diagnostic timings remain in the handoff
under their original identities; they are not substituted for these samples.

The completed correctness audit on `9d57b0d` is a separate historical result:
10,583 tests and 45 subtests passed, 32 skipped, 93.478% coverage, plus broad
behavior, static and six consumer checks. The [readiness report](../../../docs/PRODUCTION_READINESS.md)
records its limitations and the versioned release gates still required.
