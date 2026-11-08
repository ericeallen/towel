# Historical release evidence

[Documentation index](README.md)

These records were previously on the project front page. Their dates, sources,
measurements and qualifications are unchanged; they do not validate later source.

### Published 1.947

Towel **1.947** was published October 26, 2026 on
[PyPI](https://pypi.org/project/code-towel/1.947/) and
[GitHub](https://github.com/ericeallen/towel/releases/tag/v1.947) from commit
`8a853126f5499eb11dfa04fbe304dc332dc5ef73`. The
[1.947 overview](https://github.com/ericeallen/towel/blob/v1.948/docs/RELEASE_1_947.md)
and [changelog](https://github.com/ericeallen/towel/blob/v1.948/CHANGELOG.md#1947)
describe binding-safe renaming, lower candidate-enumeration costs, preservation
of each caller's captured lookup mapping, each file's formatting policy and
explicit rename consumer scope. The builtin-mapping guard also avoids repeated scans of
large unchanged modules within an analysis. Narrow or unmarked rename targets
may now require a project-root rerun or an explicit `--complete-scope` declaration.

The release completed its local gates and
[exact-commit GitHub CI](https://github.com/ericeallen/towel/actions/runs/40312460216).
All 141 corpus projects completed: 89 `PASS`, 45 `NO_CHANGE`, five reviewed
import-ambiguity refusals and two known warning-location limitations. There
were no phase timeouts or signal terminations. Differential fuzzing completed
9,400 runs with zero failures and 30 inconclusive cases. The published wheel
and source archive matched the validated hashes; a fresh PyPI installation and
all 22 version-pinned README documentation links were verified.
The [release notes](https://github.com/ericeallen/towel/releases/tag/v1.947)
record the final outcomes, qualifications and controlled comparison with 1.946.

### Historical evidence from 1.947 preparation

The following checkpoints describe earlier candidates; the completed release
above supersedes their pending status without changing their recorded results.

At the **October 19, 2026 preparation checkpoint**, 1.947 had not been published.
The preceding repair source `2027689`, still numbered 1.946, passed its Linux
Python 3.13 `just ci` run: 10,901 tests and 45 subtests, 226 skips and 94%
coverage. Those are prior-source repair checks. The final 1.947 source and
artifacts still required their own complete release gates at that checkpoint.
A subsequent candidate, `59aafb1`, passed its interpreter matrix and other
scoped release checks, but its corpus exposed repeated large-module scanning.
That corpus was stopped intentionally; its results do not validate the later
runtime repair. The [performance guide](https://github.com/ericeallen/towel/blob/v1.948/docs/PERFORMANCE.md#1947-large-module-builtin-guard-repair)
records the finding without claiming a measured speedup for the fix.
Final validation and publication outcomes belong to the external release record
and [PyPI](https://pypi.org/project/code-towel/). See the
[readiness report](https://github.com/ericeallen/towel/blob/v1.948/docs/PRODUCTION_READINESS.md)
and [release checkpoint](https://github.com/ericeallen/towel/blob/v1.948/docs/RELEASING.md#1947-preparation-checkpoint-october-19-2026).

### Historical evidence from 1.946 preparation

The following measurements retain their original sources and environments;
they do not validate 1.947.

An earlier development audit at `9d57b0d` passed 10,583 tests and 45 subtests,
with 32 skips and 93.478% coverage. Its six consumer checks preserved their
original test outcomes and expected generated outputs. These results precede
the 1.946 metadata and documentation update; they do not certify the final
versioned artifacts. See the
[readiness report](https://github.com/ericeallen/towel/blob/v1.948/docs/PRODUCTION_READINESS.md)
for source-bound evidence and its limits. The October 18
[local validation](https://github.com/ericeallen/towel/blob/v1.948/docs/PRODUCTION_READINESS.md#scoped-source-proof-memo-local-validation-october-18-2026)
records the full Python 3.11–3.13 runs, 93–94% coverage, byte-identical CLI
outputs and the remaining process-observer limitation.

The October 18 profile of `0b3494c` measured Rich 7.6% slower than 1.792
(97.4/97.6 versus 90.5/90.7 seconds, shared macOS host, Python 3.12).
Click's noisier difference includes a fifth extraction where 1.792 made four.
That profile predates scoped source-correspondence memoization, implemented
in subsequent 1.946 source. It reuses a proof only for an owned immutable tree within one
analysis; caller trees and materialization retain fresh checks. No new timing
benefit is claimed. See
[where the remaining time goes](https://github.com/ericeallen/towel/blob/v1.948/docs/RELEASE_1_946.md#where-the-remaining-time-goes-profiled-on-0b3494c).

The earlier October 10 container cohorts use
versioned 1.946 source `a6dc6ed`: Voluptuous averaged 87.57 versus 90.98 seconds
(3.75% lower), while Click averaged 58.19 versus 47.16 seconds (23.39% slower).
These are two shared-host samples per source per workload; Rich has no actual
sample in that container campaign. Its full release gates and exact-commit CI
were incomplete; the October 18 native profile above is a separate measurement.
The [portable record](https://github.com/ericeallen/towel/blob/v1.948/tests/release_evidence/1.946-warning-guard/README.md)
binds all eight actual samples, identities, outputs and limits. These results
predate the subsequent backslash-parity repair. The later prepared 1.946 runtime
is not certified by those earlier source-bound cohorts.

The earlier October 10 shared-host comparison of `9d57b0d` with released 1.792
measured the complete cold CLI:
Click averaged 56.69 versus 46.53 seconds (21.85% slower), and Rich averaged
177.70 versus 99.77 seconds (78.10% slower), with two samples per version
per workload. The
[performance guide](https://github.com/ericeallen/towel/blob/v1.948/docs/PERFORMANCE.md)
records all samples, CPU time, workload differences and measurement limits.
README documentation links target the `v1.948` source snapshot. Dated
preparation records retain the status of their original checkpoints. The historical
[1.947 GitHub release](https://github.com/ericeallen/towel/releases/tag/v1.947)
records that version's completed publication and validation.
