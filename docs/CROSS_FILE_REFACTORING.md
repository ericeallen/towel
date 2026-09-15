# Cross-File Refactoring - Implementation Complete

> **Historical development note.** Written during development (2024–2025) and kept for provenance. It may describe superseded behavior, and its counts and status are from the period. The maintained references are [docs/ARCHITECTURE.md](ARCHITECTURE.md), [docs/KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md), and the [README](../README.md).

## Problem Statement

When analyzing `example3_file1.py` and `example3_file2.py` separately, the tool would extract the same duplicate function in BOTH files, creating duplicate extracted functions. This is not what we want - the extracted function should be defined in only one place and imported by the other file.

## Solution

Implemented cross-file duplicate detection and refactoring with the following features:

### 1. Multi-File Analysis

Modified `UnificationRefactorEngine.analyze_files()` to:
- Parse multiple files together
- Compare functions across file boundaries
- Track file context for each code block

### 2. Canonical Location Selection

For cross-file duplicates:
- Extracted function is placed in the **first file** (canonical location)
- Other files import it from the canonical location

### 3. Import Statement Generation

Files that use the extracted function (but don't define it):
- Automatically get `from <module> import <function>` added
- Import is placed **after the docstring** and **after existing imports**

### 4. Free Variable Handling

Cross-file extraction correctly handles free variables:
- Computes variables used but not defined in the extracted block
- Adds them as parameters to the extracted function
- Passes them as arguments at call sites

## Example

### Before Refactoring

**example3_file1.py:**
```python
def calculate_discount_for_regular_customer(price, customer):
    """Calculate discount for regular customer."""
    # Calculate discount (DUPLICATE across files!)
    base_discount = 0.1
    if customer.get("years_member", 0) > 5:
        base_discount += 0.05
    if customer.get("total_purchases", 0) > 1000:
        base_discount += 0.05
    discount_amount = price * base_discount
    final_price = price - discount_amount
    return final_price
```

**example3_file2.py:**
```python
def calculate_discount_for_premium_customer(price, customer):
    """Calculate discount for premium customer."""
    # Calculate discount (DUPLICATE across files!)
    base_discount = 0.1
    if customer.get("years_member", 0) > 5:
        base_discount += 0.05
    if customer.get("total_purchases", 0) > 1000:
        base_discount += 0.05
    discount_amount = price * base_discount
    final_price = price - discount_amount
    return final_price
```

### After Refactoring

**example3_file1.py:**
```python
"""
Example 3, File 1: Repeated code across multiple files.
"""


def extracted_func(customer, price):
    base_discount = 0.1
    if customer.get('years_member', 0) > 5:
        base_discount += 0.05
    if customer.get('total_purchases', 0) > 1000:
        base_discount += 0.05
    discount_amount = price * base_discount
    final_price = price - discount_amount
    return final_price


def calculate_discount_for_regular_customer(price, customer):
    """Calculate discount for regular customer."""
    # Calculate discount (DUPLICATE across files!)
    return extracted_func(customer, price)
```

**example3_file2.py:**
```python
"""
Example 3, File 2: Repeated code across multiple files.
"""
from example3_file1 import extracted_func


def calculate_discount_for_premium_customer(price, customer):
    """Calculate discount for premium customer."""
    # Calculate discount (DUPLICATE across files!)
    return extracted_func(customer, price)
```

## Key Features

✅ **Single Definition**: Extracted function defined only once in file1
✅ **Import Statement**: file2 imports from file1
✅ **Free Variables**: `customer` and `price` passed as parameters
✅ **Correct Placement**: Import after docstring
✅ **Valid Python**: All generated code parses successfully

## Implementation Details

### Modified Data Structures

**CodeBlockPair** - Extended to track cross-file context:
```python
@dataclass
class CodeBlockPair:
    file_path: str                      # First file
    file_path2: Optional[str]           # Second file (for cross-file pairs)
    scope_analyzer1: Optional[...]      # Scope context for first file
    scope_analyzer2: Optional[...]      # Scope context for second file
    root_scope1: Optional[...]          # Root scope for first file
    root_scope2: Optional[...]          # Root scope for second file
    source1: Optional[str]              # Source code for first file
    source2: Optional[str]              # Source code for second file
```

**RefactoringProposal** - Replacements now include file path:
```python
replacements: List[Tuple[Tuple[int, int], ast.AST, str]]
# (line_range, replacement_node, file_path)
```

### New Methods

1. **`analyze_files(file_paths: List[str])`** - Analyze multiple files together
2. **`_find_block_pairs_multi_file(all_functions)`** - Find cross-file pairs
3. **`_try_refactor_pair_multi_file(pair, all_functions)`** - Handle cross-file unification
4. **`apply_refactoring_multi_file(proposal)`** - Apply to multiple files
5. **`_find_import_position(lines)`** - Find where to insert imports

### Algorithm Flow

1. Parse all files and extract functions with their context
2. Compare every pair of functions (including across files)
3. For each matching pair:
   - Compute free variables (e.g., `customer`, `price`)
   - Extract function with free variables as parameters
   - Generate calls passing free variables as arguments
   - Track which file each replacement belongs to
4. Apply refactoring:
   - Add extracted function to canonical file (first file)
   - Add import statements to other files
   - Replace original code with function calls

## Usage

```python
from towel.unification.refactor_engine import UnificationRefactorEngine

engine = UnificationRefactorEngine(max_parameters=5, min_lines=4)

# Analyze multiple files together
files = ["file1.py", "file2.py"]
proposals = engine.analyze_files(files)

# Apply cross-file refactoring
if proposals:
    modified_files = engine.apply_refactoring_multi_file(proposals[0])

    # Write modified files
    for file_path, content in modified_files.items():
        with open(file_path, 'w') as f:
            f.write(content)
```

## Test Results

```
✓ ALL CHECKS PASSED

Cross-file refactoring working correctly:
  1. Detected duplicates across files
  2. Extracted function to canonical location
  3. Added imports to other files
  4. Passed free variables as parameters
  5. Generated valid Python code
```

## Backward Compatibility

The single-file API still works:
```python
# Old way - still works for single files
proposals = engine.analyze_file("myfile.py")
refactored = engine.apply_refactoring("myfile.py", proposals[0])

# New way - works for one or multiple files
proposals = engine.analyze_files(["myfile.py"])
modified = engine.apply_refactoring_multi_file(proposals[0])
```

Internally, `analyze_file(path)` now delegates to `analyze_files([path])`.

## Future Improvements

1. **Smart canonical location** - Choose best file based on:
   - Number of usages (put function where it's used most)
   - Module hierarchy (prefer utils/common modules)
   - Existing imports (minimize import graph changes)

2. **Shared utilities module** - Option to create:
   - `common.py` or `utils.py` for extracted functions
   - Especially for functions used in 3+ files

3. **Relative imports** - Use relative imports when appropriate:
   - `from .common import extracted_func` instead of absolute imports
   - Respects package structure

4. **Import optimization** - Merge with existing imports:
   - If `from file1 import foo` exists, change to `from file1 import foo, extracted_func`
   - Instead of adding a separate import line
