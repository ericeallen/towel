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
- [Documentation](#documentation)

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

For security-sensitive reports, first read [SECURITY.md](SECURITY.md); no confidential reporting channel is currently verified. Before creating ordinary bug reports, please check existing issues to avoid duplicates. When you create a bug report, include as many details as possible:

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
4. **Ensure all tests pass** (run `pytest`)
5. **Update documentation** as needed
6. **Write clear commit messages**
7. **Submit a pull request** with a clear description of your changes

## Development Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/ericeallen/towel.git
   cd towel
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

   The hooks run Black formatting, flake8 linting, strict mypy over the whole
   tree, Bandit, and other checks before each commit. They call `python` from
   the environment, so activate it or prefix commits with
   `PATH="$PWD/.venv/bin:$PATH"`; a file in progress must already type-check
   for a commit to go through. Never bypass the hooks.

4. Verify the setup:
   ```bash
   just test        # or: uv run --frozen pytest -q
   just check       # formatting, lint, typing, Bandit
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
- Strict mypy is enforced on `src/towel` and the typed test helpers (`[tool.mypy] files` in `pyproject.toml`)

## Testing

All code contributions should include tests:

- Write unit tests for new functionality; a change to the unifier, definite
  assignment, or the instantiation check should also keep the property-based
  tests in `tests/test_properties.py` passing, and a new engine defect found on
  real code becomes a hostile fixture
- A library module never prints: warnings go to the `towel` logger and traces
  to its child loggers (see `src/towel/diagnostics.py`); a new engine setting
  read from the environment goes into `Settings`, not into `os.environ` reads
- Ensure existing tests continue to pass
- Run the test suite before submitting:
  ```bash
  pytest
  ```
- Aim for high test coverage of new code
- Include both positive and negative test cases

### Running Tests

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_file.py

# Run with coverage
coverage run -m pytest
coverage report --fail-under=85
```

## Documentation

- Update the README.md if you change functionality, and add an entry under
  `[Unreleased]` in CHANGELOG.md
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

For reproducible tool versions, use `uv sync --frozen --extra dev` and the commands in README.md. CI runs the full tests and an unconditional 85% coverage gate for each supported Python version. `just release VERSION` prepares local distributions only; publication requires maintainer review of the current audit and policy decisions.

Release maintainers should follow [docs/RELEASING.md](docs/RELEASING.md).
