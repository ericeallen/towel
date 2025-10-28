# Summary of All Fixes

## Issues You Identified

1. **Builtins as parameters**: `print` and `range` were being passed as function parameters
2. **Parameter/binding collision**: `param_3` used as both parameter AND loop variable
3. **Missing alpha-renaming**: Loop variables `i` vs `j` not recognized as equivalent

## Before the Fixes

```python
def extracted_func(param_0, param_1, param_2, param_3, print, range):  # ← 6 params!
    x = param_0
    y = param_1
    if x == y:
        if y > param_2:
            for param_3 in range(0, x):  # ← param_3 is BOTH parameter and loop var!
                print(param_3)
            return y

def foo():
    extracted_func(1, 2, 5, i, print, range)  # ← Passing i, print, range!

def bar():
    extracted_func(3, 4, 10, j, print, range)
```

**Problems**:
- ✗ 6 parameters (should be 3)
- ✗ `print` and `range` as parameters (should be builtins)
- ✗ Loop variable `param_3` is also a parameter (collision!)
- ✗ Passing undefined `i` and `j` as arguments

## After the Fixes

```python
def extracted_func(param_0, param_1, param_2):  # ← 3 params!
    x = param_0
    y = param_1
    if x == y:
        if y > param_2:
            for i in range(0, x):  # ← i is a bound variable, not a param
                print(i)            # ← print and range are builtins
            return y

def foo():
    extracted_func(1, 2, 5)  # ← Only 3 arguments!

def bar():
    extracted_func(3, 4, 10)
```

**Results**:
- ✓ 3 parameters (correct)
- ✓ `print` and `range` used as builtins
- ✓ Loop variable `i` is NOT a parameter
- ✓ Alpha-renaming: `i` and `j` recognized as equivalent
- ✓ Code is valid and executable Python

## Implementation Details

### 1. Builtin Tracking

**File**: `towel/unification/builtins.py` (NEW)

Tracks all Python builtins that should never be parameterized:
- Functions: `print`, `range`, `len`, `int`, `str`, etc.
- Constants: `True`, `False`, `None`
- Exceptions: `ValueError`, `TypeError`, etc.

### 2. Free Variable Filtering

**File**: `towel/unification/scope_analyzer.py`

Updated `get_free_variables()` to:
- Recognize loop variables as bindings (not free variables)
- Recognize comprehension variables as bindings
- Filter out Python builtins from free variables

### 3. Alpha-Renaming

**File**: `towel/unification/unifier.py`

Implemented proper alpha-equivalence for bound variables:
- Track mappings: `(block_idx, var_name) -> canonical_name`
- When unifying `for i...` vs `for j...`:
  - Establish mapping: `(block_0, 'i') -> 'i'` and `(block_1, 'j') -> 'i'`
  - Use canonical name `i` in extracted function
- When comparing Names:
  - Apply alpha-renaming first
  - Then check if canonical names match

## Testing

```bash
# Test the fixes
python3 -c "
from towel.unification.refactor_engine import UnificationRefactorEngine
import ast

engine = UnificationRefactorEngine(max_parameters=5, min_lines=4)
proposals = engine.analyze_file('my_test/my_test.py')

if proposals:
    best = proposals[0]
    print(f'Parameters: {best.parameters_count}')

    params = [arg.arg for arg in best.extracted_function.args.args]
    print(f'Parameter names: {params}')

    # Check for bugs
    if 'print' not in params:
        print('✓ print is NOT a parameter')
    if 'range' not in params:
        print('✓ range is NOT a parameter')

    # Show extracted function
    print()
    print(ast.unparse(best.extracted_function))
"
```

**Output**:
```
Parameters: 3
Parameter names: ['param_0', 'param_1', 'param_2']
✓ print is NOT a parameter
✓ range is NOT a parameter

def extracted_func(param_0, param_1, param_2):
    x = param_0
    y = param_1
    if x == y:
        if y > param_2:
            for i in range(0, x):
                print(i)
            return y
```

## Verification Checklist

- [x] Builtins not parameterized
- [x] Loop variables not parameterized
- [x] No parameter/binding collisions
- [x] Alpha-equivalence for `i` vs `j`
- [x] Code is valid Python
- [x] Code executes correctly
- [x] Correct number of parameters (3 not 6)

## Documentation

- `ALPHA_RENAMING_FIX.md` - Detailed technical explanation
- `CONSTANT_PARAMETERIZATION.md` - Constant parameterization feature
- `FIXES_SUMMARY.md` - This file

## What's Next

The system now correctly handles:
- ✓ Constant parameterization (1 vs 3, 2 vs 4, etc.)
- ✓ Builtin recognition (print, range, len, etc.)
- ✓ Alpha-renaming for loop variables (i vs j)
- ✓ Proper scoping and binding analysis

This makes it a much more robust and practical duplicate code detector!
