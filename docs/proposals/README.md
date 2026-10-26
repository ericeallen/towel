# Design proposals and experiments

[Documentation index](../README.md)

The [October 8 review](REVIEW_2026-10-08.md) examines all 15 original documents
and their individual ideas against the released 1.792 code and the frozen
release evidence available at that review. It separates retained work, completed designs, retired policies
and historical measurements. Priorities are recommendations, not a schedule.

Older proposal bodies preserve their dates, measurements and contracts; current
decisions and code take precedence. **1.947 was published October 26, 2026**,
following 1.946. The [1.947 overview](../RELEASE_1_947.md) describes the audit
repairs; the [publication record](../PRODUCTION_READINESS.md#1947-published-release-october-26-2026)
separates completed release validation from the historical source-bound checks
below. The [release comparison](../PERFORMANCE.md#1947-published-release-comparison)
records the measured full-CLI costs against 1.946 without claiming a general speedup.
Documentation changes do not establish new release-gate results.
Development checkpoints do not become current validation results, and their
artifacts numbered 1.772 must not be published. Current behavior is in the guides
linked from the documentation index.

## Disposition checkpoint, October 18, 2026

The maintainer extended 1.946 to implement source-correspondence proof reuse
within one owned analysis, preserving caller-tree and materialization checks,
and requested fresh Python 3.11–3.13 suites and cache-on/off output comparisons.
The October 18 Rich profile (+7.6% at `0b3494c`) predates this implementation. See the
[decision](../DECISIONS.md#2026-10-18-one-analysis-may-reuse-its-source-correspondence-proof)
and [publication checkpoint](../RELEASING.md#1946-publication-checkpoint-october-18-2026).
Remote publication was unverified at that checkpoint. The October 10 records
below remain historical.

## Preparation checkpoint, October 10, 2026

All four selected proposals are implemented. Each stage received its own frozen
audit; subsequent repairs and performance work received a final fresh audit on
`9d57b0d25f3ee743e0ce0035ed13df65b2344164`: 10,583 tests and 45 subtests passed,
32 tests skipped, and line coverage was 93.478%. These results belong to that
source, not to later documentation or versioned artifacts.

| Implemented change | Preserved boundary |
| --- | --- |
| Exact ownership-fact reuse | Materialization still performs an independent uncached proof. |
| Occurrence-specific constant substitution | AST field/index paths preserve equal literals elsewhere, exact types and common caller literal pools. |
| Certified leading-thunk parameter ordering | Only contiguous eligible slots in the same positional segment move; every caller must prove immutable results. Other parameters, the slash and existing evaluation guards stay fixed. |
| IF-only fresh local outputs | Original-source certificates and aligned-output reproof cover contiguous IF suites. Prior bindings, captures, unsupported ancestry, exceptions and lifetime obligations remain restrictive. |

The latest completed versioned cohorts compare repaired `a6dc6ed`/1.946 with
published `44d7795`/1.792: Voluptuous was 3.75% lower and Click 23.39% slower,
with two shared-host observations per source/workload in R,F,F,R order.
Rich has zero actual samples in this new campaign and remains pending, as do
fresh full release gates. The [current portable record](../../tests/release_evidence/1.946-warning-guard/README.md)
retains source/wheel/cap/tool identities, original pairs, outputs and closure;
restoration remains unresolved.

The earlier default-CLI comparison observed `9d57b0d` **21.85% slower
on Click and 78.10% slower on Rich** than published 1.792. There were two samples
per version per workload, in R,F,F,R order, with complete source and original
before/after checks. These are disclosed shared-host observations, not isolated
host certification or a general performance claim. The restoration target is
not met. See the [historical performance evidence](performance-roadmap.md#october-10-2026-preparation-and-completed-comparison)
for timing boundaries and capability differences. Earlier rejected and profiled
runs remain separate historical evidence.

## Deferred groups

These remain proposals or research, rather than commitments for 1.946.

| Group | Remaining scope |
| --- | --- |
| Broader control flow | Loops, async/suspension, nested definitions and wider binding/control transfer beyond the bounded IF proof. |
| Dethunk precision | Bare-name binding precision and mixed pure/impure callers; no arbitrary attribute-purity assumption. |
| Checker and environment integration | General CI invocation, flags, targets, checker selection and environment reproduction. |
| Shared helper modules | New package hosts, shipping, initialization and import/binding obligations. |
| Richer type contracts | Receiver/local generics, richer domains and optional PEP 695 output; core generic inference is already shipped. |
| Further exact performance work | N-way discovery and Rust require fresh profiles and complete-discovery proofs. |
| Template Method assistance | Research and reviewed agent edits; automatic base/MRO changes remain ruled out. |
| Preview presentation | Unified JSON/stat extensions; complete preview is already shipped. |

Decorator trust overrides and redirection to user-defined functions are
withdrawn, not deferred implementations.

## Retained work and research

| Document | Current direction |
| --- | --- |
| [Declines needing wider analysis](decline-capabilities.md) | Narrow capability recovery; list expressions, match/except-star suites and unrelated scope guards are already recovered. Refresh current refusals before choosing work. |
| [Dethunking](dethunking.md) | Certified parameter ordering is implemented; bare-name inference remains research. Its census is historical and attribute-purity assumptions are not recommended. |
| [Performance roadmap](performance-roadmap.md) | Most fixes are complete. Profile full default runs, preserve exhaustive discovery and site-specific safety proofs. Skeleton-only search and Rust remain deferred. |
| [Project's own type-check invocation](project-own-check.md) | Literal GitHub mypy strict policy exists. General invocation/environment integration remains deferred, preferably starting with explicit structured policy. |
| [Shared helper module](shared-helper-module.md) | Retain opt-in design research with installed-package, shipping, import-effect and caller-binding gates. Old counts are upper bounds. |
| [Template Method](template-method.md) | Retain visitor-readability research and paired-agent work; automatic base/MRO changes remain outside Towel's mechanical role. |
| [Type parameters](type-parameters.md) | Core inference is shipped; receiver contracts, local generics, richer domains and optional syntax remain bounded research. |

## Implemented designs and development history

| Document | How to read it |
| --- | --- |
| [Work leading to 1.792](next-release.md) | Completed implementation history; old pending-release statements and intermediate comparisons are superseded by the final release record. |
| [Preview that shows what dry would do](preview-what-dry-would-do.md) | Implemented complete-preview design; proposed presentation extensions remain unimplemented. |

## Retired and withdrawn designs

| Document | Status |
| --- | --- |
| [Trusted decorators](trusted-decorators.md) | Retired October 2 with the body-instrumentation guarantee; not an available configuration option. |
| [Reuse an input function](reuse-existing-function.md) | Earlier implementation withdrawn because rebinding could change other callers. This differs from sharing helpers generated during one run. |

## Historical validation and comparison archive

These reports are evidence for their exact sources and artifacts, not feature
requests or a queue of validation work to repeat.

| Report | Scope |
| --- | --- |
| [Earlier frozen development validation](next-validation.md) | Original candidate and incomplete corpus history, retained under their original identities. |
| [Completed corrected-source validation](next-validation-final.md) | Completed development validation of `3af8c4b`; its version-1.772 artifacts must not be uploaded. |
| [Published 1.772 comparison](published-1772-comparison.md) | Actual published wheel versus a development candidate; separate full-command and first-reveal timings. |

For the release process and required evidence, use the
[release procedure](../RELEASING.md). For shipped changes, use the
[changelog](../../CHANGELOG.md).
