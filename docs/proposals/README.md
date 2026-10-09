# Design proposals and experiments

[Documentation index](../README.md)

The [October 8 review](REVIEW_2026-10-08.md) examines all 15 original documents
and their individual ideas against the released 1.792 code and newest frozen
release evidence. It separates retained work, completed designs, retired policies
and historical measurements. Priorities are recommendations, not a schedule.

Older proposal bodies preserve their dates, measurements and contracts; current
decisions and code take precedence. The 1.792 release from `44d7795` is complete.
Development checkpoints do not become current validation results, and their
artifacts numbered 1.772 must not be published. Current behavior is in the guides
linked from the documentation index.

The current Unreleased work implements exact ownership-fact reuse and
occurrence-specific constant substitution. Ownership materialization keeps its
independent uncached proof. Constant differences no longer replace equal literals
at unrelated paths, and common caller literals use exact carrier selectors.
These are development changes, not a new publication. Complete default CLI
latency remains unestablished: shared-host contention prevented acceptance of the
balanced comparison; observed repetitive-input CPU reductions are separately
qualified. The current development change also orders contiguous eligible thunk
slots by their certified evaluation sequence, preserving all-caller immutable
results and the remaining parameter positions. Its full audit is pending.
Bounded nested-block liveness is the remaining implementation step after that
audit completes.

## Retained work and research

| Document | Current direction |
| --- | --- |
| [Declines needing wider analysis](decline-capabilities.md) | Narrow capability recovery; list expressions, match/except-star suites and unrelated scope guards are already recovered. Refresh current refusals before choosing work. |
| [Dethunking](dethunking.md) | Small exact parameter-order and definite-assignment improvements; its census is historical and attribute-purity assumptions are not recommended. |
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
