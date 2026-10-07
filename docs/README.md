# Towel documentation

Towel finds repeated Python code and extracts it into shared helpers. Start
with the [Quick start](QUICKSTART.md) for a first refactoring; use the guides
below when you need a particular task or a precise rule.

This documentation describes the source version you are reading. Dated
benchmarks, validation reports and proposals retain their original source and
artifact identities; they do not establish results for a later release.

## Start and use Towel

| I want to… | Read |
| --- | --- |
| Install Towel and review my first extraction | [Quick start](QUICKSTART.md) |
| Choose CLI options, exclude files, refactor in place or recover an interrupted write | [Command-line guide](CLI_GUIDE.md) |
| Check supported platforms and resource use | [Requirements and resources](REQUIREMENTS.md) |
| Estimate a run or understand a timing claim | [Performance and timing](PERFORMANCE.md) |
| Share helpers between modules or resolve an import refusal | [Cross-module guide](CROSS_MODULE.md) |
| Give generated helpers and parameters meaningful names | [Naming guide](NAMING.md) |
| Call Towel from Python | [Python API guide](USAGE_GUIDE.md) |

## Understand the result

| Question | Read |
| --- | --- |
| How does extraction work, and why does output sometimes contain lambdas? | [Understanding generated code](GENERATED_CODE.md) |
| Which type checker runs, where do annotations come from, and why is a typed proposal declined? | [Type checking and helper annotations](TYPING.md) |
| What is verified, conservatively rejected or outside the supported model? | [Known limitations](KNOWN_LIMITATIONS.md) |
| How are the algorithms and implementation organized? | [Architecture](ARCHITECTURE.md) |
| Why did we choose these semantics and policies? | [Design decisions](DECISIONS.md) |

## Contribute and release

- [Agent work log](agent_log.md): current development hypotheses and pending checks; not release approval.

- [Contributing](../CONTRIBUTING.md): setup, coding standards, tests and documentation maintenance.
- [Release procedure](RELEASING.md): frozen source, exact artifacts, evidence gate and CI.
- [Security policy](../SECURITY.md): private reporting and supported-release policy.
- [Code of conduct](../CODE_OF_CONDUCT.md): community expectations.

## Changes and evidence

- [Changelog](../CHANGELOG.md): user-visible changes by version.
- [Production readiness](PRODUCTION_READINESS.md): validation scope, results and qualifications.
- [Release log](RELEASE_LOG.md): dated engineering and validation checkpoints.
- [Adversarial review](ADVERSARIAL_REVIEW.md): discovered defects and their regression coverage.
- [Open-source audit](OPEN_SOURCE_AUDIT.md): the historical distribution and maintenance audit.
- [Design proposals and experiments](proposals/README.md): implementation status, deferred designs and dated measurements.

For an overview of the project, return to the [project README](../README.md).
