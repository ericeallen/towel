# Towel documentation

Towel finds repeated Python code and extracts it into shared helpers. Start
with the [Quick start](QUICKSTART.md) for a first refactoring; use the guides
below when you need a particular task or a precise rule.

These pages describe Towel **2.0**.

## Start and use Towel

| I want to… | Read |
| --- | --- |
| Install Towel and review my first extraction | [Quick start](QUICKSTART.md) |
| Run Towel on a real project and review what it changed | [Using Towel on a project](USING_TOWEL.md) |
| Understand a helper, a lambda or an argument Towel wrote | [Reading Towel's output](GENERATED_CODE.md) |
| Choose between relaxed mode, the default, and `--strict` | [Relaxed and strict](RELAXED_AND_STRICT.md) |
| Find out why Towel left some code alone | [Why something was left alone](WHY_LEFT_ALONE.md) |
| Refactor a project checked by mypy or Pyright | [Type checking and helper annotations](TYPING.md) |
| Share helpers between modules or resolve an import refusal | [Cross-module guide](CROSS_MODULE.md) |
| Give generated helpers and parameters meaningful names | [Naming guide](NAMING.md) |
| Follow a recipe for CI, pre-commit or a large project | [Recipes](RECIPES.md) |
| See extractions Towel made in real projects | [Gallery](GALLERY.md) |
| Find a quick answer | [Frequently asked questions](FAQ.md) |

## Reference

| I need… | Read |
| --- | --- |
| Every command-line option, file exclusion, in-place runs and recovery | [Command-line guide](CLI_GUIDE.md) |
| Exit statuses, the JSON result and what may change between releases | [Compatibility contract](COMPATIBILITY.md) |
| To call Towel from Python | [Python API guide](USAGE_GUIDE.md) |
| Supported platforms and resource use | [Requirements and resources](REQUIREMENTS.md) |
| To estimate how long a run takes | [Performance and timing](PERFORMANCE.md) |
| What Towel cannot see, and what it leaves alone | [Known limitations](KNOWN_LIMITATIONS.md) |
| The meaning of a term | [Glossary](GLOSSARY.md) |

## About Towel

- [How Towel is validated](VALIDATION.md): the tests and checks each release passes.
- [How Towel compares](COMPARISON.md): clone detectors, an IDE's extract-method and AI assistants, and what each is good at.
- [Changelog](https://github.com/ericeallen/towel/blob/main/CHANGELOG.md): user-visible changes by version.
- [Getting help](GETTING_HELP.md): where to ask, what to put in a report, and how to report a security problem.

## For contributors and maintainers

- [Contributing](https://github.com/ericeallen/towel/blob/main/CONTRIBUTING.md): setup, coding standards, tests and documentation maintenance.
- [Security policy](https://github.com/ericeallen/towel/blob/main/SECURITY.md): private reporting and supported-release policy.
- [Code of conduct](https://github.com/ericeallen/towel/blob/main/CODE_OF_CONDUCT.md): community expectations.
- [Design](design/README.md): architecture, design decisions and proposals.
- [Maintainers' records](maintainers/README.md): the release procedure, release log, readiness reports, reviews and audits.

For an overview of the project, return to the [project README](https://github.com/ericeallen/towel/blob/main/README.md).
