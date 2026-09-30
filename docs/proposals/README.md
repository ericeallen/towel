# Design proposals and experiments

[Documentation index](../README.md)

These records explain designs, decisions and measurements. Their presence here
does not mean a feature is implemented or scheduled. Read each document's
status and date; historical validation is bound to the source and artifacts
it names. Current user-facing behavior is in the guides linked from the index.

## Implemented designs and development history

| Document | How to read it |
| --- | --- |
| [Work leading to 1.792](next-release.md) | Approved scope, implementation sequence and dated progress; final release validation remains a separate step. |
| [Preview that shows what dry would do](preview-what-dry-would-do.md) | Implemented complete-preview design; proposed presentation extensions remain unimplemented. |
| [Type parameters](type-parameters.md) | Design shipped in 1.772, including inference order and declined cases. |
| [Declines needing wider analysis](decline-capabilities.md) | Partially implemented in 1.792; distinguishes completed work from remaining capability gaps. |
| [Performance roadmap](performance-roadmap.md) | Historical profiles and the subsequent work recorded under “Done since.” |
| [Dethunking](dethunking.md) | Historical analysis of lambda arguments, resolved cases and remaining ideas. |

## Deferred, unscheduled or withdrawn designs

| Document | Status |
| --- | --- |
| [Project's own type-check invocation](project-own-check.md) | Unscheduled proposal extending the existing differential baseline. |
| [Shared helper module](shared-helper-module.md) | Unscheduled design for sharing across otherwise unrelated directories. |
| [Trusted decorators](trusted-decorators.md) | Proposal for a later release; not an available configuration option. |
| [Template Method](template-method.md) | Idea constrained by the decision that Towel does not change externally visible class design. |
| [Reuse an input function](reuse-existing-function.md) | Earlier implementation withdrawn because rebinding could change other callers. This differs from sharing helpers generated during one run. |

## Dated validation and performance evidence

| Report | Scope |
| --- | --- |
| [Earlier frozen development validation](next-validation.md) | Original candidate and incomplete corpus history, retained under their original identities. |
| [Completed corrected-source validation](next-validation-final.md) | Completed development validation of `3af8c4b`; its version-1.772 artifacts must not be uploaded. |
| [Published 1.772 comparison](published-1772-comparison.md) | Actual published wheel versus a development candidate; separate full-command and first-reveal timings. |

For the release process and required evidence, use the
[release procedure](../RELEASING.md). For shipped changes, use the
[changelog](../../CHANGELOG.md).
