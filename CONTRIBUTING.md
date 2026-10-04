# Contributing to Towel

Thank you for your interest in contributing to Towel! We welcome contributions from the community.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [How to Contribute](#how-to-contribute)
  - [Reporting Bugs](#reporting-bugs)
  - [Suggesting Enhancements](#suggesting-enhancements)
  - [Pull Requests](#pull-requests)
- [Development Setup](#development-setup)
- [Coding Standards](#coding-standards)
- [Testing](#testing)
- [Local release verification](#local-release-verification)
- [Documentation](#documentation)
- [Pull Request Process](#pull-request-process)

## Code of Conduct

This project follows its [Code of Conduct](CODE_OF_CONDUCT.md). Please be respectful and considerate in your interactions with other contributors.

## Getting Started

1. Fork the repository on GitHub
2. Clone your fork locally
3. Set up the development environment (see [Development Setup](#development-setup))
4. Create a new branch for your changes
5. Make your changes
6. Test your changes
7. Submit a pull request

## How to Contribute

### Reporting Bugs

For security-sensitive reports, use the private channel described in [SECURITY.md](SECURITY.md); do not open a public issue. Before creating ordinary bug reports, please check existing issues to avoid duplicates. When you create a bug report, include as many details as possible:

- **Use a clear and descriptive title**
- **Describe the exact steps to reproduce the problem**
- **Provide specific examples** (code snippets, input files, etc.)
- **Describe the behavior you observed and what you expected**
- **Include error messages and stack traces**
- **Specify your Python version and operating system**

### Suggesting Enhancements

Enhancement suggestions are tracked as GitHub issues. When creating an enhancement suggestion:

- **Use a clear and descriptive title**
- **Provide a detailed description of the proposed feature**
- **Explain why this enhancement would be useful**
- **Include examples of how the feature would be used**

### Pull Requests

1. **Fork the repository** and create your branch from `main`
2. **Make your changes** following our coding standards
3. **Add tests** for any new functionality
4. **Ensure all tests pass** (run `uv run --frozen pytest -q`)
5. **Update documentation** as needed
6. **Write clear commit messages**
7. **Submit a pull request** with a clear description of your changes

## Development Setup

1. Clone your fork and add this repository as `upstream`, which the
   pull-request steps below rebase on:
   ```bash
   git clone https://github.com/<your-account>/towel.git
   cd towel
   git remote add upstream https://github.com/ericeallen/towel.git
   ```

2. Create the pinned development environment with uv (Python 3.13 for the
   formatting and typing gates):
   ```bash
   uv sync --frozen --extra dev
   ```

   Or, without uv, create and activate a virtual environment and install the
   `dev` extra:
   ```bash
   python3.13 -m venv .venv
   source .venv/bin/activate
   pip install -e ".[dev]"
   ```

   The `dev` extra includes Black, ruff, isort, mypy, and pyright, which the
   formatting and typing tests exercise (the mypy and pyright tests skip when
   those are absent), hypothesis for the property-based tests, and pip-audit
   for the dependency scan CI runs.

3. Install pre-commit hooks (enforces code quality):
   ```bash
   uv run --frozen pre-commit install
   ```

   The hooks check Black formatting, run flake8 linting and strict mypy over the
   whole tree (`src/towel` and `tests`), Bandit on `src/towel` (CI also scans `scripts`), and other checks before each commit. They call `python` from
   the environment, so activate it or prefix commits with
   `PATH="$PWD/.venv/bin:$PATH"`; a file in progress must already type-check
   for a commit to go through. Never bypass the hooks.

4. Verify the setup:
   ```bash
   just test        # or: uv run --frozen pytest -q
   just check       # formatting, lint, typing, Bandit
   just check-diagrams   # parses the documentation's mermaid diagrams (needs Node)
   ```

## Coding Standards

- Follow [PEP 8](https://www.python.org/dev/peps/pep-0008/) style guidelines
- Use meaningful variable and function names
- Write docstrings for public functions and classes
- Keep functions focused and reasonably sized
- Add type hints where appropriate
- Comment complex logic
- Run Black explicitly to format code (line length 100); hooks check formatting
- All code must pass flake8 linting
- Strict mypy is enforced on `src/towel` and on `tests/` (`[tool.mypy]` in
  `pyproject.toml`). Test bodies are checked with every strict flag; the only
  relaxation is that test functions and unittest methods need no signatures
  (`tests.*` override). `warn_unused_ignores` is on, so a `# type: ignore`
  must be necessary and must name its error code

## Testing

All code contributions should include tests:

- Write unit tests for new functionality; a change to the unifier, definite
  assignment, or the instantiation check should also keep the property-based
  tests in `tests/test_properties.py` passing, and a new engine defect found on
  real code becomes a hostile fixture
- Every defect an audit reports becomes a test, so that the next audit need
  not find it again: at least its reproducer as a hostile fixture, named by
  your branch's prefix (`tests/hostile_cases/r<prefix>_<family>_<case>.py`,
  `tests/hostile_crossfile/xf<prefix>_<family>_<case>/`) rather than by the
  next free number. Until its fix lands the fixture sits in its battery's
  `KNOWN_DEFECTS`, with the reason from `tests/audit_defects.py`, as a strict
  expected failure; the fix turns it into an XPASS, which fails the run, and
  the entry is then removed
- `tests/test_differential_grammar.py` refactors a fixed set of generated
  projects and compares each program before and after; a failure prints the
  seed and the source. `just fuzz` runs many more seeds, and writes each
  failure as a fixture ready to commit
- A library module never prints: warnings go to the `towel` logger and traces
  to its child loggers (see `src/towel/diagnostics.py`); a new engine setting
  read from the environment goes into `Settings`, not into `os.environ` reads
- A change to what the engine extracts from `test_examples/` shows up in
  `tests/test_regression.py`: regenerate the goldens only after verifying the
  new output, and if an example starts or stops being refactored, move it in
  or out of `EXPECTED_UNCHANGED_EXAMPLES` there and explain why in the commit
- Ensure existing tests continue to pass
- Run the test suite before submitting:
  ```bash
  uv run --frozen pytest -q
  ```
- Aim for high test coverage of new code
- Include both positive and negative test cases
- **Preserve the intent of policy regressions.** Tests linked to an approved
  entry in `docs/DECISIONS.md` encode a design requirement. When one fails,
  investigate the implementation against that requirement; changed engine
  output is not evidence that the expected behavior has changed. Revise an
  expectation only with evidence that the test contradicts the recorded rule,
  or a separately agreed revision of that rule. Record the rationale and
  update the decision and test together. Keep each test's explanation of the
  invariant and the failure it prevents. For example, caller-narrowing tests
  must protect both avoiding wasted typed validation and retaining smaller
  valid extractions; making every proposal disappear satisfies neither goal.

### Running Tests

```bash
# Run all tests
uv run --frozen pytest -q

# Run specific test file
uv run --frozen pytest -q tests/test_file.py

# Run with coverage (pair evaluation forks workers, so each process writes
# its own data file and `combine` must precede the report)
uv run --frozen coverage run -m pytest -q
uv run --frozen coverage combine
uv run --frozen coverage report --fail-under=85
```

### CI commands and behavioral evidence

Use Python 3.13 for formatting and typing gates; the lockfile also resolves
test dependencies for supported older interpreters. Python 3.11 is the minimum.
`just ci` runs the local quality, test, dependency-audit and build gates:

```bash
uv sync --frozen --extra dev
uv run --frozen black --check src/towel tests scripts
uv run --frozen flake8 src/towel scripts tests
uv run --frozen mypy
uv run --frozen coverage run -m pytest -q
uv run --frozen coverage combine
uv run --frozen coverage report --fail-under=85
uv run --frozen bandit -r src/towel scripts -ll
just audit-dependencies
uv run --frozen python -m build
```

Behavioral tests compare sampled return values and types, exceptions, output,
and argument mutations. Cross-file tests isolate imports for each execution.
Empty selections, unsupported class construction and cross-file returned
closures do not count as success. Single-file comparison samples one layer of
returned callables; it does not validate deeper layers. This is regression
evidence, not a proof of equivalence for arbitrary programs.

Set `TOWEL_CHECK_AST_IMMUTABLE=1` to check on every cache reuse that analysis
has left the module's AST unchanged. Large analyses fork workers when a timed
probe projects enough work; `TOWEL_WORKERS=1` forces one worker.

### Ecosystem checks

`just ecosystem --run-untrusted-code --no-types` runs the standing ecosystem
check (`scripts/ecosystem_check.py`) on all 141 manifest entries. It tests each
project before and after refactoring a copy. Revisions are pinned except for
the deliberate check of Towel's current `main`, whose resolved commit is recorded.

The manifest's test requirements must cover the pinned project's selected
suite, including CI-selected optional libraries and pytest plugins. For example,
Tabulate's test workflow installs its NumPy, pandas and wide-character support;
MarkdownIt's test workflow selects its `testing` and `linkify` extras. An
all-skipped optional test family or an unknown pytest configuration option does
not establish that this context is complete. Keep upstream constraints and
record actual installed versions; an unpinned dependency is not an upstream pin.
The harness does not automatically install every testing, development or docs
group. Preserve all dependencies in an explicitly selected broad development
extra or default group, even when they include documentation and orchestration
tools. Name the chosen context: installing outer uv defaults alongside an inner
tox test group is a broader corpus environment, not the original single CI
testenv. Independent contexts with incompatible constraints remain separate.
One selected context does not claim coverage of the whole CI matrix.

Peewee uses its documented local/default SQLite profile and `runtests.py`.
Optional features can skip when their modules are absent; CPython may still
build its default extensions from shipped C sources. Its broader CI service,
client, extension and conditional Git dependency setup is a separate context.
Record the actual installed extension and driver state when reviewing its run.

New weak tool requirements must preserve a compatible selected lock, hook or
requirements-file version. The manifest records those original choices; after
installation, the environment report calls them project-installed tools.
A tool incompatible with Towel's extras is replaced as those extras require,
and the report records the override. That final version is an explicit
variation from the upstream formatter/checker environment.

Weekly CI explicitly uses `--no-types`: the manifest supplies runtime test
dependencies, not every project's complete typing environment. Omit the flag
to retain Towel's default typing policy; the harness never disables checking
automatically. Reports record the requested typing mode and every outcome.

The harness accepts `PASS`, `NO_CHANGE` and specifically documented
`BROKEN_KNOWN` outcomes. Setup failures, typing failures, timeouts and incomplete
test runs fail its gate. A no-change result does not validate a transformation.
A release review must separately account for any refusal or fallback under
[the evidence gate](docs/RELEASING.md#repository-owned-evidence-gate).

These checks execute third-party code with your privileges, so use a disposable
machine or container. The opt-in is `--run-untrusted-code` or
`TOWEL_ECOSYSTEM_RUN_UNTRUSTED=1`.

Towel runs inside each project's environment, alongside an installation of
that project from the tested tree, Towel's `format` and `types` extras, and the
configured checker dependencies. One wheel is built from `--towel-src` or
supplied with `--towel-wheel`; a wheel whose code differs from the source is
refused. Point `--towel-src` at a committed snapshot's `src` directory. Reports
record the actual commit and dirty state; source archives need an independently
retained source manifest. See [Production readiness](docs/PRODUCTION_READINESS.md)
for results and qualifications.

## Local release verification

Choose and commit a release version before freezing its validation source.
`just build` and `just bump-version VERSION` are development commands; they do
not establish release fitness. The supported local release path is
`just release VERSION /path/to/release-evidence.json`. It requires a clean
checkout and completed, reviewed evidence bound to the exact prebuilt wheel
and source archive. It does not rebuild, publish, tag, or enforce remote policy.

Use `just verify-release-evidence VERSION /path/to/release-evidence.json` for
validation-only artifacts. Preserve their nonpublication status. Additional
documentation/tests/evidence need exact source hashes, an explicit review and
supplemental checks; their results are not part of the frozen suite counts.
Runtime, configuration and corpus-harness changes invalidate old evidence.
See [the evidence schema and release procedure](docs/RELEASING.md#repository-owned-evidence-gate).

## Documentation

- Keep the README focused on the project overview and first-use path. Put
  detailed tasks and explanations in the appropriate guide linked from
  [the documentation index](docs/README.md), and add user-visible changes
  under `[Unreleased]` in CHANGELOG.md
- Give each new guide an index entry and a backlink. Longer references should
  have a compact contents list using their existing headings. Prefer relative
  links inside `docs/`; the root README needs release-pinned absolute links
  because it is also the PyPI description
- When moving a measured claim, move its documentation regression to the new
  canonical page. Preserve the evidence, numerical expectations and negative
  mutation tests; a new layout does not change what a historical run measured
- Add docstrings to new functions and classes
- Update relevant documentation in the `docs/` directory: a new guard or
  rejection belongs in KNOWN_LIMITATIONS.md, a new stage or rule in
  ARCHITECTURE.md, a new engine defect found on real code in
  ADVERSARIAL_REVIEW.md with its fixture
- Include examples for new features
- Keep documentation clear and concise

## Pull Request Process

1. **Update your branch** with the latest changes from `main`:
   ```bash
   git fetch upstream
   git rebase upstream/main
   ```

2. **Ensure all tests pass** and code follows standards

3. **Push your changes** to your fork:
   ```bash
   git push origin your-branch-name
   ```

4. **Create a pull request** on GitHub

5. **Address review feedback** if requested

6. **Wait for approval** from maintainers

## Questions?

If you have questions, feel free to:
- Open an issue for discussion
- Reach out to the maintainers
- Check existing documentation and issues

Thank you for contributing to Towel!

CI runs the full tests and the 85% coverage gate on each supported Python
minor. Python 3.13 also runs formatting, lint, typing, Bandit, the strict
dependency audit and distribution builds. The separate ecosystem workflow runs
weekly or on demand on a discarded runner; Dependabot proposes grouped updates
weekly. Publication still requires review of the exact source, artifacts, CI
and policy decisions.

Release maintainers should follow [docs/RELEASING.md](docs/RELEASING.md).
