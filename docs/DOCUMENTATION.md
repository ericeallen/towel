# Towel - Complete Documentation

**Version 1.0.0** - Unification-Based Duplicate Code Detection and Refactoring

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Installation](#installation)
3. [Usage Guide](#usage-guide)
4. [Architecture](#architecture)
5. [Testing](#testing)
6. [Development](#development)
7. [Implementation Details](#implementation-details)
8. [Troubleshooting](#troubleshooting)

---

## Quick Start

### Installation

```bash
# Clone repository
git clone <repo-url>
cd dry-detector

# Install (no dependencies required!)
pip install -e .

# Or install with development tools
pip install -e ".[dev]"
```

### Basic Usage

```bash
# Preview refactoring opportunities (read-only)
python3 scripts/preview my_code.py

# Apply refactorings (writes to output location)
python3 scripts/dry input/ output/

# Or using justfile
just preview my_code/
just dry input/ output/
```

---

## Installation

### Requirements

- Python 3.7+
- **Zero external runtime dependencies** - uses only Python stdlib!

### For Users

```bash
# Standard installation
pip install -e .
```

### For Developers

```bash
# Install with development tools (coverage, black, flake8, mypy)
pip install -e ".[dev]"

# Or from requirements
pip install -r requirements-dev.txt
```

---

## Usage Guide

### CLI Tools

#### `scripts/preview` - Read-Only Preview

Preview refactoring opportunities without modifying files:

```bash
# Preview a single file
python3 scripts/preview my_code.py

# Preview a directory
python3 scripts/preview src/

# Using justfile
just preview my_code/
```

**Output:**
- Lists all detected duplicates
- Shows extracted function preview
- Displays parameter count
- Indicates same-file vs cross-file refactoring

#### `scripts/dry` - Apply Refactorings

Apply refactorings to code:

```bash
# Refactor to new location (safe - doesn't overwrite)
python3 scripts/dry src/ src_refactored/

# Refactor single file
python3 scripts/dry my_code.py my_code_clean.py

# Refactor in-place (OVERWRITES original)
python3 scripts/dry src/ src/

# Using justfile
just dry input/ output/
```

**Safety:**
- Copies files before modification
- Can specify different output directory
- In-place mode only when input == output

### Configuration Options

The refactoring engine accepts these parameters:

- `max_parameters` (default: 5) - Maximum parameters for extracted functions
- `min_lines` (default: 4) - Minimum lines required for duplicate detection
- `parameterize_constants` (default: True) - Treat differing constants as parameters

---

## Architecture

### Overview

Towel uses **unification** from type inference theory to detect and refactor duplicate code.

```
src/towel/
└── unification/           # Unification-based refactoring system (91% coverage)
    ├── refactor_engine.py # Main refactoring engine (89%)
    ├── unifier.py         # Robinson-style unification algorithm (88%)
    ├── extractor.py       # Hygienic function extraction (82%)
    ├── scope_analyzer.py  # Variable scope analysis (100%)
    ├── orphan_detector.py # Orphan variable detection (100%)
    └── builtins.py        # Python builtin filtering (100%)
```

### How It Works

1. **Parsing**: Parse Python files into ASTs using `ast` module
2. **Block Extraction**: Extract all contiguous code blocks from functions
3. **Unification**: Use Robinson-style unification to find blocks that can be unified:
   - Match AST structure recursively
   - Allow alpha-renaming of loop variables (`i` ≈ `j`)
   - Parameterize differing constants and expressions
   - Respect Python builtin names and scoping rules
4. **Orphan Detection**: Validate extraction won't create undefined variable references
5. **Function Extraction**: Generate hygienically-renamed extracted functions
6. **Replacement Generation**: Create function calls with correct parameter order
7. **Cross-File Support**: Handle duplicates across files with import generation

### Key Features

#### Alpha-Renaming
Treats loop variables as equivalent binding constructs:
```python
for i in range(10):  # Unifies with
for j in range(10):  # this block
```

#### Orphan Variable Detection
Prevents unsafe extractions:
```python
# Won't extract lines 1-3 alone because 'total' would be orphaned
x = 10
y = 20
total = x + y
return total  # Uses 'total'
```

#### Return Value Propagation
Detects returns anywhere in block (not just at end):
```python
if x > 100:
    return y * 2  # Nested return detected
# Correctly generates: return extracted_func()
```

#### F-String Handling
Never parameterizes f-string literal parts:
```python
f"Hello {name}"  # Only 'name' can be parameterized, not "Hello "
```

#### Builtin Filtering
Never parameterizes Python builtins (`len`, `print`, `range`, etc.)

---

## Testing

### Running Tests

```bash
# Run all 97 unit tests
just test

# Run with coverage (91%)
just coverage-unification

# Generate HTML coverage report
just coverage-html
open htmlcov/index.html

# Test specific aspects
just test-bindings    # Binding constructs
just test-returns     # Return value propagation
just test-fstrings    # F-string handling
just test-orphans     # Orphan variable detection
just test-engine      # End-to-end refactoring
```

### Test Organization

```
tests/
├── test_bindings.py              # Binding constructs (for loops, comprehensions)
├── test_return_values.py         # Return value propagation
├── test_fstrings.py              # F-string and constant handling
├── test_orphan_detection.py      # Orphan variable detection
├── test_scope_analyzer.py        # Scope analysis edge cases
├── test_unifier_edge_cases.py    # Unification edge cases
├── test_refactoring_engine.py    # End-to-end refactoring
├── test_comprehensive_coverage.py # Error paths and edge cases
└── test_final_coverage_push.py   # Final coverage tests
```

### Test Examples

```
test_examples/
├── example1_simple.py            # Simple validation logic
├── example4_complex.py           # Complex data processing loops
├── bindings_for_loops.py         # Loop variable edge cases
├── bindings_comprehensions.py    # List/dict/set comprehensions
├── return_values.py              # Return propagation scenarios
└── fstrings_constants.py         # F-string and constant tests
```

### Coverage Metrics

- **91% overall coverage**
- **97 comprehensive unit tests**
- **4 modules at 100% coverage**: `scope_analyzer`, `orphan_detector`, `builtins`, `__init__`

---

## Development

### Project Structure

```
dry-detector/
├── src/                      # Source code (src layout)
│   └── towel/
│       └── unification/
├── scripts/                  # Executable scripts
│   ├── dry                   # Main refactoring tool
│   └── preview               # Preview tool
├── tests/                    # Unit tests
│   └── test_*.py
├── docs/                     # Documentation
│   ├── DOCUMENTATION.md      # This file
│   └── *.md                  # Additional docs
├── test_examples/            # Test fixtures
├── pyproject.toml            # Modern packaging (PEP 517/518)
├── requirements.txt          # Runtime dependencies (zero!)
├── requirements-dev.txt      # Development dependencies
├── justfile                  # Task runner
├── README.md                 # Project README
└── .gitignore               # Git ignore rules
```

### Development Workflow

```bash
# Format code
just format

# Lint code
just lint

# Type check
just typecheck

# Run all quality checks
just check

# Clean generated files
just clean

# Reset test examples
just reset-examples
```

### Code Quality Tools

- **black**: Code formatting (line-length 100)
- **flake8**: Linting
- **mypy**: Type checking
- **coverage**: Test coverage analysis

### Adding New Tests

1. Create test file in `tests/test_*.py`
2. Import test infrastructure:
   ```python
   import unittest
   from towel.unification.refactor_engine import UnificationRefactorEngine
   ```
3. Create test class inheriting from `unittest.TestCase`
4. Run tests: `just test`

---

## Implementation Details

### Unification Algorithm

The unifier implements a Robinson-style unification algorithm:

1. **Structural Matching**: Recursively compare AST node types
2. **Field Comparison**: Compare all fields of matching node types
3. **Substitution Building**: Build substitution mapping for differences
4. **Alpha-Renaming**: Treat binding constructs as equivalent
5. **Constant Parameterization**: Parameterize differing constants
6. **Builtin Filtering**: Never parameterize Python builtins

### Scope Analysis

The scope analyzer tracks:

- **Bindings**: Variables assigned in each scope
- **Free Variables**: Variables used but not defined
- **Enclosing Scopes**: Nested scope hierarchies
- **Comprehension Scopes**: Local comprehension variables

### Orphan Detection

Orphan variables are detected by checking if:

1. Variable is bound in extracted block
2. Variable is used in remaining code
3. Variable is NOT rebound in remaining code

### Function Extraction

The extractor:

1. Generates unique parameter names (avoiding conflicts)
2. Orders parameters consistently
3. Handles return value propagation
4. Creates hygienic function definitions
5. Generates replacement function calls

---

## Troubleshooting

### Common Issues

#### Import Errors After Restructuring

**Problem**: `ModuleNotFoundError: No module named 'towel'`

**Solution**: Reinstall package:
```bash
pip install -e .
```

#### Tests Fail After Moving Files

**Problem**: Tests can't find modules

**Solution**: Update `PYTHONPATH` or reinstall:
```bash
export PYTHONPATH="${PYTHONPATH}:$(pwd)/src"
# OR
pip install -e .
```

#### Coverage Not Found

**Problem**: `coverage: command not found`

**Solution**: Install development dependencies:
```bash
pip install -e ".[dev]"
# OR
pip install coverage
```

### Getting Help

1. Check this documentation
2. Run `just help` for available commands
3. Check individual doc files in `docs/` for specific topics
4. Review test examples in `test_examples/`

---

## Additional Documentation

For more detailed information on specific topics, see:

- **[Usage Guide](USAGE_GUIDE.md)** - Detailed usage instructions
- **[Unification Implementation](UNIFICATION_IMPLEMENTATION.md)** - Algorithm details
- **[Cross-File Refactoring](CROSS_FILE_REFACTORING.md)** - Cross-file support
- **[Testing Best Practices](TESTING_BEST_PRACTICES.md)** - Testing guidelines
- **[Justfile Reference](JUSTFILE_REFERENCE.md)** - All available commands
- **[Troubleshooting Guide](TROUBLESHOOTING.md)** - Common issues and solutions

---

## Version History

### v0.2.0 (Current)
- Removed all legacy code
- Achieved 91% test coverage with 97 tests
- Reorganized to src layout
- Created comprehensive documentation
- Added pyproject.toml for modern packaging

### v0.1.0
- Initial unification-based implementation
- Orphan variable detection
- Alpha-renaming support
- F-string handling
- Cross-file refactoring

---

**Documentation Generated**: 2025-10-25
**Coverage**: 91% with 97 tests
**License**: See LICENSE file
