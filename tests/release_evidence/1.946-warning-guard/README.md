# Versioned warning-guard measurements, October 10, 2026

[Measurements](measurements.json) retain every actual sample in the two completed
cohorts: Voluptuous R,F,F,R, then Click R,F,F,R. Published source
`44d779563578a32e24f8213fba96f43c6698ab43` used its published **1.792** wheel;
warning-guard source `a6dc6ed4c75fa9c1b65aa9029f2a6361ec7dce77` used the freshly
built **1.946** wheel. This is eight shared-host observations, two per source
per workload, not completed release certification. Rich has zero actual samples
in this campaign: failed bounded settling/preproduct preflights are retained
separately, and its cohort remains pending.

| Workload | Published mean (range), seconds | Versioned candidate mean (range), seconds | Change |
| --- | ---: | ---: | ---: |
| Voluptuous | 90.984 (90.572–91.396) | 87.569 (87.522–87.616) | −3.75% |
| Click | 47.165 (46.816–47.514) | 58.195 (57.338–59.052) | +23.39% |

CLI wall time covers the complete cold installed default production path:
typing, formatting, cross-module extraction, fixed-point materialization and
final validation. Setup, measured host settling and before/after tests are
outside the CLI timer. Each run used a fresh private input/environment/work
volume, the same pinned Linux image and CPython 3.12.14, one Towel worker,
two CPUs, 6 GiB RAM, 12 GiB combined RAM+swap and disabled networking.
Dependency caches were reused; hardware page caches were not flushed.
No profiler or census ran during these eight samples. Per-CLI waited-child CPU
is unavailable; native/VM CPU is context, not equivalent attribution.

Both Voluptuous arms retained 182 passing tests. The released arm applied 39
refactorings and the candidate 40, each across four files. Both Click arms
retained 2,035 passes, 24 existing pager failures, 24 skips, one xfail and 31,000
deselections. Its 24 displayed failure headers and E-lines matched exactly
before/after in all four samples; no claim is made about undisplayed object
state or upstream CI. Released Click applied four refactorings versus five
under 1.946, both across three files. Each arm's repeated output matched its
own source: 10 Python files plus `py.typed` and `tests/tests.md` for Voluptuous,
and 17 Python files plus `py.typed` for Click. Output equality across versions is not claimed.

Each fresh preflight required 20 one-second intervals at least 80% aggregate
idle. Continuous one-second CPU rows and native/container context covered the
whole container window, including setup, tests and postproof. Every idle value
below 70% remains in the record; it is not automatically a veto or proof of
unrelated contention. Idle minima were 42–48% for Voluptuous and 43–47% for
Click. Native snapshots cannot fully attribute CPU or certify per-core placement,
thermal state, disk/memory contention or every brief competitor. The record
contains per-sample idle means and shortfall counts. All original-pair,
source/artifact/input/runtime/dependency/context/output and physical closure
checks passed. All eight owned containers exited with PID 0, status 0 and no
OOM; no sample was replaced or filtered.

The JSON binds source/wheel identities, pins/tools/caps, all eight wall samples,
raw-report/receipt hashes, exact baseline failure IDs, per-sample context and
root independent cohort/payload readbacks. It omits private host paths and
process arguments. Full raw logs, input/output inventories, telemetry/query
spans and closure records remain in the shared handoff
`20261010T161505Z-codex-1946-warning-guard-validation`, under
`matched-series-r04` and `matched-series-r05`. Click slot 4 continued in r05
after a preproduction waiting attempt; no actual production sample was retried.

These cohorts do not establish a confidence interval, isolated-host or general
speed claim. The requested restoration target remains unresolved: Click is
slower here, and fresh Rich measurements and the final full release gates are
pending. A subsequent source change cannot inherit these results. The rejected
partial a53 corpus campaign and the
[earlier 9d57 eight-sample comparison](../1.946-preparation/README.md)
remain separate history; that older comparison used 6 GiB combined RAM+swap,
so its means must not be mixed with these cohorts. Publication and exact-commit
CI remain pending under the [release procedure](../../../docs/RELEASING.md).
