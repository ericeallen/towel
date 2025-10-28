# Cross-File Refactoring - Usage Guide

## The Problem

If you analyze files **separately**, you get duplicate definitions:

```python
from towel.unification.refactor_engine import UnificationRefactorEngine

engine = UnificationRefactorEngine(max_parameters=5, min_lines=4)

# ❌ WRONG - Analyzing files separately
proposals1 = engine.analyze_file("example3_file1.py")
proposals2 = engine.analyze_file("example3_file2.py")

# Result: extracted_func defined in BOTH files (duplicate!)
```

## The Solution

Analyze files **together** using `analyze_files()`:

```python
from towel.unification.refactor_engine import UnificationRefactorEngine

engine = UnificationRefactorEngine(max_parameters=5, min_lines=4)

# ✅ CORRECT - Analyzing files together
proposals = engine.analyze_files([
    "example3_file1.py",
    "example3_file2.py"
])

# Apply refactoring
if proposals:
    modified_files = engine.apply_refactoring_multi_file(proposals[0])

    # Write modified files
    for file_path, content in modified_files.items():
        with open(file_path, 'w') as f:
            f.write(content)

# Result:
#   - example3_file1.py defines extracted_func
#   - example3_file2.py imports it
```

## Current State of Files

After running `apply_cross_file_refactor.py`, the files are:

### example3_file1.py
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
    return extracted_func(customer, price)
```

### example3_file2.py
```python
"""
Example 3, File 2: Repeated code across multiple files.
"""
from example3_file1 import extracted_func


def calculate_discount_for_premium_customer(price, customer):
    """Calculate discount for premium customer."""
    return extracted_func(customer, price)
```

## Key Points

1. **Single definition**: `extracted_func` is defined once in file1
2. **Import in file2**: `from example3_file1 import extracted_func`
3. **Parameters**: Free variables `customer` and `price` are passed as params
4. **Working code**: Both files import and run successfully

## Verification

You can verify the files are correct:

```bash
cd test_examples
python3 -c "import example3_file1; import example3_file2; print('✓ Both files work')"
```

Or check the import line:

```bash
grep "^from example3_file1" test_examples/example3_file2.py
# Output: from example3_file1 import extracted_func
```
