# Towel

[![CI](https://github.com/ericeallen/towel/actions/workflows/ci.yml/badge.svg)](https://github.com/ericeallen/towel/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/ericeallen/towel/branch/main/graph/badge.svg)](https://codecov.io/gh/ericeallen/towel)
[![PyPI](https://img.shields.io/pypi/v/code-towel.svg)](https://pypi.org/project/code-towel/)

**A Python tool that DRYs your code.**

Towel automatically detects and refactors violations of the DRY (Don't Repeat Yourself) principle in Python codebases using unification algorithms from automated theorem proving.

## Testing Status

- **Unit Tests**: 125/125 passing (100%)
- **Observational Equivalence**: 175/175 refactoring proposals pass automated behavioral equivalence testing
- **Test Coverage**: 18 example files with comprehensive edge cases

All refactorings are verified to behave identically to the original code through automated observational equivalence testing.

## Quick Start

The tool uses only Python stdlib with zero external dependencies.

### Using Just (Recommended)

The easiest way to use the tool is with [just](https://github.com/casey/just):

```bash
# Install just: brew install just (macOS) or cargo install just

# Preview refactoring opportunities (read-only)
just preview test_examples/

# Apply refactorings to a new directory
just dry test_examples/ cleaned_examples/

# Apply refactorings in-place (overwrites original)
just dry my_code/ my_code/

# Run tests
just test

# Check test coverage
just coverage-unification

# See all commands
just --list
```

### CLI Commands

After installation, Towel provides three main commands:

```bash
# Preview duplicates (read-only)
towel preview <file_or_directory>

# Apply refactorings
towel dry <input> <output>

# Rename extracted functions with LLM assistance
towel rename-helpers <directory>
```

### Direct Script Usage

You can also run the scripts directly without installation:

```bash
# Preview duplicates (read-only)
python3 scripts/preview <file_or_directory>

# Refactor code (writes to output location)
python3 scripts/dry <input> <output>
```

## Features

- **Zero External Dependencies**: Uses only Python standard library
- **Unification-Based Analysis**: Advanced algorithm from automated theorem proving
- **Hygienic Code Generation**: Generates functions with hygienically renamed parameters to avoid name conflicts. The generated parameter names like `__param_0` are intentionally generic and can be renamed to more meaningful names using an LLM coding assistant for better readability.
- **Referential Transparency**: Preserves referential transparency and maintains program semantics through careful scope analysis
- **Safe Refactoring**:
  - Alpha-renaming for loop variables (treats `i` and `j` as equivalent)
  - Return value propagation (detects returns anywhere in block)
  - Orphan variable detection (prevents unsafe extractions)
  - Builtin filtering (never parameterizes `len`, `print`, etc.)
  - F-string handling (correct AST manipulation)
- **Smart Parameterization**:
  - Constant parameterization (different numbers/strings become parameters)
  - Structural comparison (only extracts truly similar code)
  - Max parameter limits (prevents over-parameterization)
- **Class-Aware Method Extraction**:
  - Promotes duplicate methods into the nearest shared base class when possible
  - Preserves decorators (`@classmethod`, `@staticmethod`) and implicit binders (`self`/`cls`)
  - Rewrites call sites across files to dispatch through the new helper correctly
  - Falls back gracefully when no safe shared ancestor exists
- **Cross-File Support**: Automatically handles duplicates spanning multiple files
- **Comprehensive Testing**:
  - 175/175 refactoring proposals pass observational equivalence testing
  - 125 unit tests passing (100%)
  - Automatic observational equivalence testing verifies refactored code behaves identically to original
  - Tests 175 refactoring proposals across 18 example files automatically
  - Intelligent test input generation based on AST analysis
  - Recursive testing of returned functions (closures)
  - See `tests/OBSERVATIONAL_EQUIVALENCE.md` for details

## Installation

### For Users

```bash
# Install from PyPI (zero external dependencies)
pip install code-towel

# Or install from source
git clone https://github.com/ericeallen/towel.git
cd towel
pip install -e .
```

### For Developers

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install with dev dependencies
pip install -e ".[dev]"

# Alternatively, install the specific tools you need
pip install black flake8 mypy coverage
```

### Optional: Enhanced Progress Bars

For nicer progress bars during analysis, you can optionally install `tqdm`:

```bash
pip install tqdm
```

Without `tqdm`, Towel still works perfectly with zero dependencies - it just uses simpler progress indicators.

## Usage

### Preview Mode (Read-Only)

Preview refactoring opportunities without modifying files:

```bash
# Preview a single file
python3 preview.py my_code.py

# Preview a directory
python3 preview.py src/
```

### Refactoring Mode

Apply refactorings to code:

```bash
# Refactor to a new location (safe - doesn't overwrite)
python3 dry.py src/ src_refactored/

# Refactor a single file
python3 dry.py my_code.py my_code_clean.py

# Refactor in-place (overwrites original)
python3 dry.py src/ src/
python3 dry.py src/ src/ --progress detail --max-iterations 0  # verbose unlimited
python3 dry.py src/ src/ --progress none --max-iterations 100  # quiet capped
```

### Examples

Preview duplicates in test examples:

```bash
just preview test_examples/
```

Refactor a project to a new location:

```bash
just dry my_project/ my_project_refactored/
```

Test the tool with coverage:

```bash
just coverage-unification
```

### Renaming Extracted Helpers with LLM Assistance

After running the DRY tool, extracted functions are named `__extracted_func_*` with generic parameter names like `__param_0`. Towel includes an interactive tool that uses LLM assistance to rename these into meaningful, human-readable names:

```bash
# Interactive mode: Generate LLM prompt and apply suggestions
towel rename-helpers src/

# List all extracted helpers
towel rename-helpers src/ --list

# Apply renamings from a JSON file
towel rename-helpers src/ --rename-file renames.json

# Dry run (preview only)
towel rename-helpers src/ --dry-run

# Limit to specific files or functions
towel rename-helpers src/ --file mymodule.py
towel rename-helpers src/ --function __extracted_func_7
```

#### How It Works

The `rename-helpers` command works in two modes:

**1. Interactive Mode (default):**
- Analyzes all `__extracted_func_*` functions in your code
- Generates a prompt showing each function's code
- You paste this prompt into Claude Code, ChatGPT, or any LLM
- The LLM suggests meaningful names based on what each function does
- You paste the LLM's JSON response back
- Towel automatically renames all references throughout your codebase

**2. File Mode (`--rename-file`):**
- Provide a JSON file mapping old names to new names
- Towel applies these renamings across your entire codebase

Example workflow:
```bash
# Run the refactoring tool
towel dry src/ src_cleaned/

# Use LLM to rename extracted functions
towel rename-helpers src_cleaned/
# (Follow the interactive prompts to get LLM suggestions)
```

The tool uses smart regex-based renaming to update all function definitions and calls throughout your project.

## How It Works

The tool uses **unification** from automated theorem proving to detect and parameterize duplicates:

1. **Parsing**: Parses Python files into ASTs using the `ast` module
2. **Block Extraction**: Extracts all contiguous code blocks from functions
3. **Unification**: Uses a nominal unification algorithm to find blocks that can be unified:
   - Matches AST structure recursively
   - Allows alpha-renaming of loop variables (`i` ≈ `j`)
   - Parameterizes differing constants and expressions
   - Respects Python builtin names and scoping rules
4. **Orphan Detection**: Validates that extraction won't create undefined variable references
5. **Function Extraction**: Generates hygienically-renamed extracted functions
6. **Replacement Generation**: Creates function calls with correct parameter order
7. **Cross-File Support**: Handles duplicates across multiple files with import generation

## Safety Guarantees

The tool ensures safe refactorings by:

- **Orphan Variable Detection**: Never extracts code that binds variables used later
- **Return Value Propagation**: Detects return statements anywhere in block
- **Alpha-Renaming**: Treats loop variables `i`, `j`, `k` as equivalent binding constructs
- **Builtin Filtering**: Never parameterizes Python builtins (`len`, `print`, `range`, etc.)
- **F-String Handling**: Correct AST manipulation for f-strings (never parameterizes literal parts)
- **Comprehension Scoping**: Respects that comprehension variables are local to the comprehension
- **Structural Similarity**: Only unifies blocks with >60% structural similarity

## Advanced Features

**Class-Aware Helper Promotion** – Duplicate instance, class, or static methods are automatically lifted into their nearest shared base class, even across different files:

- Builds an inheritance table while scanning the project
- Chooses the most specific shared ancestor for the extracted helper
- Preserves method semantics (decorators, implicit parameters, and call dispatch)
- Emits the helper in the ancestor class and rewrites original methods to dispatch through it, inserting imports only when needed

**Orphan Variable Detection** – Prevents unsafe extractions that would create undefined variables:
```python
# Rejects this unsafe extraction:
def compute():
  x = 10
  y = 20
  total = x + y
  return total  # Would leave 'total' undefined if lines 1-3 extracted alone
```

**Return Value Propagation** – Detects returns anywhere in code blocks:
```python
# Correctly generates: return extracted_func()
if x > 100:
  return y * 2  # Nested return automatically detected
```

**Alpha-Renaming for Loop Variables** - Treats `i`, `j`, `k` as equivalent binding variables:
```python
for i in range(10):  # Unifies with
for j in range(10):  # this block
```

### Progress & Iteration Feedback

The directory fixed-point refactoring loop supports progress modes via `--progress`:

| Mode    | Description |
|---------|-------------|
| `tqdm`  | Rich progress bar (applied count + queued proposals). |
| `auto`  | Attempts `tqdm`; falls back to single-line textual bar. |
| `none`  | Suppresses progress output (quiet / CI). |
| `detail`| Verbose: lists discovered proposals (first 25) and localized follow-ups. |

`refactor_directory_to_fixed_point` returns `(results_dict, termination_reason)` where `termination_reason` is `fixed_point` (no proposals remain) or `iteration_cap` (stopped due to `--max-iterations N`). Use `--max-iterations 0` (default) for unlimited iterations until a fixed point.

Example (detail mode):

```text
[towel] Analyzing 26 file(s)...
[towel] Discovered 128 proposal(s)
  1. Extract common code from state_machine_pattern_v1 and state_machine_pattern_v2
  2. Extract common code from deeply_nested_computation_v1 and deeply_nested_computation_v2
  ...
```

Localized follow-ups: after each applied proposal the engine re-analyzes only changed files and prepends new opportunities to the queue for faster chained extraction.

## Testing

### Observational Equivalence Testing

Towel includes **automatic observational equivalence testing** that verifies refactored code behaves identically to the original:

```bash
# Run observational equivalence tests
just test-observational

# Run comprehensive automatic tests on all 18 example files
python -m unittest tests.test_observational_equivalence.TestAutomaticObservationalEquivalence -v
```

**Features:**
- **175/175 proposals pass (100%)** across **18 example files**
- No manual test configuration needed - extracts function names from proposals
- Intelligent test input generation using AST analysis:
  - Detects tuple unpacking: `for a, b in pairs:` → generates `[('a', 1), ('b', 2)]`
  - Detects dictionary access: `data['key']` → generates `{'key': 'value'}`
  - Uses type annotations and parameter name heuristics
- Recursive testing: When functions return functions (closures), tests the returned functions for behavioral equivalence
- **100% success rate** - all refactorings preserve program behavior

See `tests/OBSERVATIONAL_EQUIVALENCE.md` for complete documentation.

### Unit Tests

Run the comprehensive unit test suite:

```bash
# Run all 125 unit tests (100% passing)
just test

# Run with coverage
just coverage-unification

# Generate HTML coverage report
just coverage-html

# Test specific aspects
just test-bindings    # Binding constructs (for loops, comprehensions)
just test-returns     # Return value propagation
just test-fstrings    # F-string handling
just test-orphans     # Orphan variable detection
just test-engine      # End-to-end refactoring

### Fast Smoke Suite

During inner-loop development you can run a curated fast subset (proposal generation, stability, and observational equivalence on representative examples) instead of the full suite:

```bash
just test-smoke
```

Use this for quick validation (< a few minutes). Run the full suite (`just test` or `just test-all`) before pushing or releasing.
```

## Project Structure

```
src/towel/          # Core package (towel for compatibility)
└── unification/           # Unification-based refactoring system
    ├── refactor_engine.py # Main refactoring engine
    ├── unifier.py         # Nominal unification algorithm
    ├── extractor.py       # Hygienic function extraction
    ├── scope_analyzer.py  # Variable scope analysis
    ├── orphan_detector.py # Orphan variable detection
    └── builtins.py        # Python builtin filtering

scripts/
├── dry                    # Main refactoring tool
├── preview                # Read-only preview tool
└── verify-examples        # Verification tool

tests/                     # 125 comprehensive unit tests
test_examples/             # 18 example files with duplicates
docs/                      # Documentation
```

## Examples

The `test_examples/` directory contains examples with DRY violations:

- `example1_simple.py`: Simple repeated validation logic
- `example4_complex.py`: Complex data processing loops
- `bindings_for_loops.py`: Loop variable edge cases
- `bindings_comprehensions.py`: List/dict/set comprehensions
- `return_values.py`: Return value propagation scenarios
- `fstrings_constants.py`: F-string and constant handling

## Justfile Commands

Run `just --list` to see all commands, or `just help` for detailed help.

### Common Commands

```bash
# For users
just dry <input> <output>   # Refactor code (writes to output)
just preview <target>       # Preview duplicates (read-only)
just help                   # Show detailed help

# For developers
just test                   # Run all 97 unit tests
just coverage-unification   # Check coverage (91%)
just coverage-html          # Generate HTML coverage report
just check                  # Run all code quality checks (format, lint, typecheck)
just clean                  # Clean generated files
just reset-examples         # Reset example files to original state

# Documentation
just docs                  # Show usage guide
just docs-cross-file       # Show cross-file refactoring docs
just docs-directory        # Show directory usage docs
```

### Examples

```bash
# Analyze test examples
just analyze test_examples

# Preview what would change in src/
just preview src/

# Run all tests
just test-all

# Apply refactoring to example3
just refactor-example3
```

## Python API - Unification-Based Approach

The tool uses a unification-based approach for principled refactoring:

```python
from towel.unification.refactor_engine import UnificationRefactorEngine

# Create engine
engine = UnificationRefactorEngine(
    max_parameters=5,  # Max parameters for extracted functions
    min_lines=4        # Minimum lines for code blocks
)

# Analyze entire directory (finds cross-file duplicates)
proposals = engine.analyze_directory("src/")

# Apply refactorings
for proposal in proposals:
    modified_files = engine.apply_refactoring_multi_file(proposal)
    for file_path, content in modified_files.items():
        with open(file_path, 'w') as f:
            f.write(content)
```

See [docs/USAGE_GUIDE.md](docs/USAGE_GUIDE.md) for complete API documentation.

## Documentation

- **[docs/USAGE_GUIDE.md](docs/USAGE_GUIDE.md)** - Complete usage guide
- **[docs/CROSS_FILE_REFACTORING.md](docs/CROSS_FILE_REFACTORING.md)** - Cross-file refactoring details
- **[docs/README_DIRECTORY_USAGE.md](docs/README_DIRECTORY_USAGE.md)** - Directory analysis guide
- **[docs/UNIFICATION_IMPLEMENTATION.md](docs/UNIFICATION_IMPLEMENTATION.md)** - Implementation details
- **[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)** - Common issues and solutions
- **[docs/JUSTFILE_REFERENCE.md](docs/JUSTFILE_REFERENCE.md)** - Complete justfile command reference

## License

Apache 2.0
