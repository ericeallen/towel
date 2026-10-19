# Audit and release preparation log

[Documentation index](README.md)

## 2026-10-18: scoped proof memo implemented; fresh validation requested

The maintainer extended 1.946 to implement source-proof reuse for owned immutable
analysis trees, preserve mutation protections, rerun the Python 3.11–3.13 suites
and compare generated output with the cache enabled and disabled. This supersedes
the earlier no-more-tests instruction and memo deferral. Materialization and caller
trees retain fresh proofs. The preceding profile is not a speedup measurement for
this implementation. The [completed local validation](PRODUCTION_READINESS.md#scoped-source-proof-memo-local-validation-october-18-2026)
records 10,655/10,838/10,825 passes on Python 3.11/3.12/3.13, 93–94% coverage
and byte-identical CLI outputs. Each full run also had a stale navigation failure
(corrected and rechecked) and a blocked `ps` observer in the unchanged watchdog
test. That test remains unverified here; no all-green full-suite result is claimed.

## 2026-10-18: publication requested without further tests

The maintainer requested publication of 1.946 using existing evidence. No tests
were run in this documentation/publication pass. The source through `8a25ded`
includes `--stats`; the source-correspondence memo remains deferred. Current
network access cannot reach PyPI/GitHub, so upload and remote verification remain
blocked. See the [publication checkpoint](RELEASING.md#1946-publication-checkpoint-october-18-2026).

## 2026-10-10: upcoming 1.946

Version 1.946, named for ln(7) ≈ 1.945910149, is in local preparation; no
publication is authorized or recorded. The latest full audit covered
`9d57b0d25f3ee743e0ce0035ed13df65b2344164`, still packaged as 1.792:
10,583 passing canonical tests, 45 passing subtests, 32 skips, eight warnings
and 93.478046% coverage, plus independent static/architecture/behavior and
six-consumer checks. All six original test pairs and 531 generated Python files
matched; 35 known baseline failure records remain, with nine hidden
pytest-truncated portions unverified. The source inventories and owned
worker closure were independently checked.

The earlier raw-regex repair source `a6dc6ed` has fresh package/static checks and
a complete Voluptuous canary. Eight actual shared-host R,F,F,R samples in two
completed cohorts measured Voluptuous 90.984 → 87.569 seconds (−3.75%) and
Click 47.165 → 58.195 seconds (+23.39%) against published `44d7795`, two per
source/workload. Exact original pairs, all 24 displayed Click failure payloads,
repeated per-arm outputs and physical closure passed. Resources are now
2 CPUs/6 GiB RAM/12 GiB combined RAM+swap; per-CLI CPU is unavailable. Rich
has zero actual samples here after strict preproduct failures. Its cohort and
fresh full release gates remain pending; restoration is unresolved. The
[current portable record](../tests/release_evidence/1.946-warning-guard/README.md)
preserves all raw samples and limits.

A later parity-only repair distinguishes complete paired backslashes from
unmatched odd escapes before formatted-string braces, preserving native
warnings/errors and immutable fact reuse under the existing guards. Focused
integration passed 479 tests with 221 version-conditioned skips on Python 3.13;
3,719 finite external controls per native Python 3.11–3.14 passed. The unchanged
native always/ignore/error oracle and repeated/change/disabled construction
fixtures are scoped correctness/cost evidence. They do not establish timing
improvement or full acceptance, and the earlier `a6dc6ed` cohorts remain
source-bound. A new freeze and fresh final gates are pending; publication remains
unauthorized. See the
[parity repair](PERFORMANCE.md#october-10-backslash-parity-repair).

The earlier eight complete production CLI observations compared `9d57b0d` with
released `44d7795`, in R,F,F,R order for each of Click and Rich. With two
samples per source and workload, audited-source means were 21.85% slower
for Click and 78.10% slower for Rich. Every runtime idle shortfall remains
in the shared-host record. Original pairs, visible payloads and outputs
matched; all workers and containers closed. The restoration target is
unmet. The [performance guide](PERFORMANCE.md#october-10-click-and-rich-assessment)
and [source-bound record](../tests/release_evidence/1.946-preparation/README.md)
retain the observations and limitations.

These results do not certify newly versioned 1.946 source or archives. Fresh
release gates, interpreter matrix/corpus and exact-commit CI remain pending.
The dated checkpoints below are historical; their earlier authorization and
pending status do not describe current release readiness.

## 2026-10-07: historical audit remediation

At this checkpoint, user authorization covered the parameter-focused audit
repairs and release documentation. Matrix and corpus runs were excluded until
separately authorized. Work was local; no push or publication occurred.

Hypotheses established by executable audit evidence:

- Final placement must use the completed helper's receiver/class-frame needs;
  ownership arity must be checked after all interface additions.
- Refusal retention requires the same closed checker context everywhere;
  unknown/custom/plugin state cannot justify skipping a new check.
- Physical source wrapping is not evidence that a high-arity tiny helper is
  worthwhile. Judge the completed interface and actual call-site burden.
- Free-variable queries repeat immutable blocks within one unification. Store
  immutable facts only for that lifetime, copy results, and clear on reset.
- Whole f-strings cannot enter substitutions through normal parameterization;
  the downstream removal scan is obsolete, while inner-expression support stays.

Regression tests precede implementation. Placement and profitability focused
checks pass, including class-frame/private-name/generic behavior. The shared
closed-context policy passes custom/plugin/configuration retry checks. New
per-unification block caching passes result isolation and lifetime tests;
whole f-string parameterization remains rejected before the removed scan.
Black, flake8, strict mypy, Bandit and the pinned dependency advisory check pass.
Final native validation and bounded performance measurement were pending
at this intermediate checkpoint; their completed result is recorded below.
Historical release evidence does not validate this edited source.

The interrupted native attempt exposed legacy cache/receiver expectations
and a comment fixture made unprofitable by the new quality policy. Those
tests now preserve coverage with fresh-check, AST-interface and before/after
behavior assertions. A watchdog test needs process observation; it passes
when that access is enabled. The subsequent unchanged-source full run recorded
9,679 passes and 18 failures: one-site manual materialization fixtures, obsolete
refusal-count expectations and one state-machine golden. They were subsequently
repaired while preserving their original feature checks. Both failed attempts
are retained as failed evidence. A final complete native rerun was required
and later completed as below.

All reproduced fixture failures are repaired without changing the runtime.
Final native validation at `04d2710` passes 9,697 tests, 20 skips and 26 subtests
with 93% combined line coverage; every non-ignored source byte stays unchanged.
Black, flake8, strict mypy and Bandit pass. Four serial complete typed/formatted
CLI observations yield byte-identical output; the new memo halves repeated
work in its bounded control but establishes no wall-clock benefit. Raw source,
driver, result and failure records are retained in the remediation evidence.
Final documentation/artifact refresh followed; matrix/corpus were still
unauthorized at that checkpoint. Later 1.792 publication is recorded in
[production readiness](PRODUCTION_READINESS.md#1792-published-release-october-8-2026).
