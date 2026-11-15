# Towel documentation

Towel finds repeated Python code and extracts it into shared helpers. Start
with the [Quick start](QUICKSTART.md) for a first refactoring; use the guides
below when you need a particular task or a precise rule.

These pages describe Towel **1.949**.

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
| Integrate Towel into scripts or understand upgrade guarantees | [Compatibility contract](COMPATIBILITY.md) |

## Understand the result

| Question | Read |
| --- | --- |
| How does extraction work, and why does output sometimes contain lambdas? | [Understanding generated code](GENERATED_CODE.md) |
| Which type checker runs, where do annotations come from, and why is a typed proposal declined? | [Type checking and helper annotations](TYPING.md) |
| What is verified, conservatively rejected or outside the supported model? | [Known limitations](KNOWN_LIMITATIONS.md) |

## Contribute and get help

- [Contributing](https://github.com/ericeallen/towel/blob/main/CONTRIBUTING.md): setup, coding standards, tests and documentation maintenance.
- [Security policy](https://github.com/ericeallen/towel/blob/main/SECURITY.md): private reporting and supported-release policy.
- [Code of conduct](https://github.com/ericeallen/towel/blob/main/CODE_OF_CONDUCT.md): community expectations.
- [Changelog](https://github.com/ericeallen/towel/blob/main/CHANGELOG.md): user-visible changes by version.

## For contributors and maintainers

- [Design](design/README.md): architecture, design decisions and proposals.
- [Maintainers' records](maintainers/README.md): the release procedure, release log, readiness reports, reviews and audits.

For an overview of the project, return to the [project README](https://github.com/ericeallen/towel/blob/main/README.md).
