# Justfile Reference

Complete reference for all `just` commands available in this project.

## Installation

First, install [just](https://github.com/casey/just):

```bash
# macOS
brew install just

# Linux (with cargo)
cargo install just

# Other platforms
# See: https://github.com/casey/just#installation
```

## Quick Reference

```bash
just                    # Show list of commands
just --list            # Same as above
just help              # Show detailed help
```

## User Commands

### Analyze and Refactor

```bash
just analyze <dir>     # Analyze directory for duplicates (interactive)
just preview <dir>     # Preview what would be refactored (read-only)
just example           # Run simple example on test_examples/
```

**Examples:**
```bash
just analyze test_examples
just preview src/
just example
```

### Specific Refactorings

```bash
just refactor-examples     # Apply to test_examples (with confirmation)
just refactor-example3     # Apply cross-file refactoring to example3
just show-example1         # Show what would be extracted from example1
```

## Developer Commands

### Testing

```bash
just test                  # Run core tests
just test-cross-file       # Test cross-file refactoring
just test-examples         # Test on example files
just test-all              # Run all tests
just validate              # Validate implementation
```

### Code Quality

```bash
just format                # Format code with black
just lint                  # Lint with flake8
just typecheck             # Type check with mypy
just check                 # Run all code quality checks
```

### Debugging

```bash
just debug-unification     # Run unification debug script
just debug-extraction      # Run extraction debug script
just debug-blocks          # Run block extraction debug script
```

### Cleanup

```bash
just clean                 # Clean generated files and caches
just reset-examples        # Reset example files to original state
```

### CI/CD

```bash
just ci                    # Run CI checks (format, lint, typecheck, test)
just build                 # Build package
```

## Documentation Commands

```bash
just docs                  # Show usage guide
just docs-cross-file       # Show cross-file refactoring docs
just docs-directory        # Show directory usage docs
```

## Installation Commands

```bash
just install               # Install dependencies
```

## Command Details

### `just analyze <dir>`

Analyzes a directory for duplicate code and prompts to apply refactorings.

- Finds all Python files in the directory
- Analyzes for both same-file and cross-file duplicates
- Shows all refactoring opportunities
- Asks for confirmation before applying

**Example:**
```bash
just analyze src/
```

**Output:**
```
Analyzing directory: src/
Found 10 Python files in src/
Found 5 refactoring opportunities

1. Extract common code from func1 and func2
   Parameters: 2
   Cross-file: No

Apply refactorings? (y/N):
```

### `just preview <dir>`

Preview what would be refactored without making any changes.

**Example:**
```bash
just preview test_examples
```

**Output:**
```
Found 7 opportunities
1. Extract common code from process_json_data and process_xml_data (1 params)
2. Extract common code from process_user_data and process_admin_data (1 params)
...
```

### `just test`

Runs the core test suite:
- Unification tests
- Cross-file refactoring tests

**Example:**
```bash
just test
```

### `just test-all`

Runs comprehensive test suite:
- Core tests
- Example file tests
- Validation tests

**Example:**
```bash
just test-all
```

### `just check`

Runs all code quality checks:
- Formatting with black
- Linting with flake8
- Type checking with mypy

**Example:**
```bash
just check
```

### `just clean`

Cleans up generated files:
- `__pycache__` directories
- `.pyc` and `.pyo` files
- `.egg-info` directories
- `.pytest_cache` and `.mypy_cache`
- Temporary refactored files

**Example:**
```bash
just clean
```

### `just reset-examples`

Resets test example files to their original state with duplicates.

**Example:**
```bash
just reset-examples
```

This is useful for:
- Re-running tests after applying refactorings
- Demonstrating the tool
- Testing iteratively

## Command Patterns

### Interactive Workflow

```bash
# 1. Preview what would change
just preview src/

# 2. Analyze with confirmation
just analyze src/

# 3. Review changes and test
just test
```

### Development Workflow

```bash
# 1. Make changes
# 2. Format and check
just check

# 3. Run tests
just test-all

# 4. Clean up
just clean
```

### Testing Workflow

```bash
# 1. Reset examples
just reset-examples

# 2. Run tests
just test-all

# 3. Try specific examples
just show-example1
just refactor-example3
```

## Tips

### List All Commands
```bash
just --list
```

### Get Help
```bash
just help
```

### Dry Run (see what would execute)
```bash
just --dry-run analyze test_examples
```

### Verbose Mode
```bash
just --verbose test
```

## Common Workflows

### First-Time User
```bash
# 1. Install
just install

# 2. Run example
just example

# 3. Try on test directory
just preview test_examples

# 4. Read docs
just docs
```

### Regular User
```bash
# Analyze your project
just analyze src/

# Or preview first
just preview src/
```

### Developer
```bash
# Make changes
# ...

# Check code quality
just check

# Run tests
just test-all

# Clean up
just clean
```

### CI/CD
```bash
# Run all CI checks
just ci
```

This runs:
1. `just clean`
2. `just test-all`
3. `just check`

## Environment

Commands respect these environment variables:
- Standard Python environment variables
- Tool-specific configs (black, flake8, mypy)

## Requirements

Commands may require:
- Python 3.8+
- `black` (for `just format`)
- `flake8` (for `just lint`)
- `mypy` (for `just typecheck`)

Missing tools will be skipped with a message.

## Exit Codes

- `0` - Success
- `1` - Failure (test failed, lint errors, etc.)

## See Also

- [README.md](../README.md) - Main documentation
- [USAGE_GUIDE.md](USAGE_GUIDE.md) - Complete usage guide
- [CROSS_FILE_REFACTORING.md](CROSS_FILE_REFACTORING.md) - Cross-file details
