# Maintainers' records

[Documentation index](../README.md)

These pages record how Towel is released and how each release was validated and
reviewed. They are kept for maintainers and auditors; to use Towel, start from
the [documentation index](../README.md) instead.

## Releasing

- [Release procedure](RELEASING.md): frozen source, exact artifacts, evidence gate and CI.
- [Release log](RELEASE_LOG.md): dated engineering and validation checkpoints.

## Validation and history

- [Production readiness](PRODUCTION_READINESS.md): validation scope, results and qualifications.
- [1.947 overview](RELEASE_1_947.md): the seven audit repairs, scope changes and validation boundary.
- [1.946 overview](RELEASE_1_946.md): historical improvements since 1.792 and their limits.
- [Historical release evidence](RELEASE_HISTORY.md): earlier releases' records, under their original sources.

## Reviews and audits

- [Adversarial review](ADVERSARIAL_REVIEW.md): discovered defects and their regression coverage.
- [Open-source audit](OPEN_SOURCE_AUDIT.md): the historical distribution and maintenance audit.
- [Correctness review of `towel dry`, October 31, 2026](reviews/gemini-2026-10-31/REPORT.md): 28 runnable hazard cases and the defects they found.
- [Documentation against the shipped CLI, October 31, 2026](reviews/gemini-2026-10-31-docs-vs-cli.md): every documented command and option checked against 1.947.
