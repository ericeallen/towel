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

This project adheres to a code of conduct. By participating, you are expected to uphold this code. Please be respectful and considerate in your interactions with other contributors.

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

Before creating bug reports, please check existing issues to avoid duplicates. When you create a bug report, include as many details as possible:

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
   git clone https://github.com/yourusername/towel.git
   cd towel
   ```

2. Create and activate a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. Install development dependencies:
   ```bash
   pip install -e .
   pip install pytest
   ```

4. Verify the setup:
   ```bash
   pytest
   ```

## Coding Standards

- Follow [PEP 8](https://www.python.org/dev/peps/pep-0008/) style guidelines
- Use meaningful variable and function names
- Write docstrings for public functions and classes
- Keep functions focused and reasonably sized
- Add type hints where appropriate
- Comment complex logic

## Testing

All code contributions should include tests:

- Write unit tests for new functionality
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
pytest --cov=src/towel
```

## Documentation

- Update the README.md if you change functionality
- Add docstrings to new functions and classes
- Update relevant documentation in the `docs/` directory
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
