# Adversarial Testing Results

## Overview

We conducted adversarial testing by creating 5 test files specifically designed to break Towel's observational equivalence guarantees:

1. `side_effects_adversarial.py` - Global state, mutable arguments
2. `control_flow_adversarial.py` - Break/continue/return in loops
3. `exception_adversarial.py` - Try/except/finally blocks
4. `closure_adversarial.py` - Closures and variable capture
5. `tricky_edge_cases_adversarial.py` - Multiple returns, ternary operators

## Results Summary

- **Total adversarial proposals**: 15
- **Passed**: 10 (66.7%)
- **Failed**: 5 (33.3%)

## Bugs Discovered

### Bug #1: Augmented Assignment with Free Variables

**Status**: Identified root cause, partial fix attempted

**Problem**: When extracting code containing augmented assignments (`total += num`) where the variable is a free variable (not bound in the extracted block), Towel fails to parameterize the variable correctly.

**Example**:
```python
# Original:
def sum_until_zero_a(numbers):
    total = 0
    for num in numbers:
        if num == 0:
            break
        total += num
    return total
```

**Buggy extraction** (what Towel currently generates):
```python
def extracted_func(__param_14, __param_15):
    for num in __param_14:
        if num == 0:
            break
        total += num  # ❌ 'total' is undefined!
    return __param_15
```

**Root Cause**: The variable `total` is initialized BEFORE the extracted block (`total = 0`) but used INSIDE the extracted block (`total += num`). This makes `total` a FREE VARIABLE in the extracted code - it's referenced but not defined.

**Why AugAssign fix didn't work**: We added a `visit_AugAssign` method to `ParameterSubstituter`, but the real issue is that `total` never makes it into the substitution in the first place. The free variable isn't being detected and added as a parameter during the unification/extraction phase.

**Correct behavior**: Towel should EITHER:
1. Detect `total` as a free variable and add it as a parameter, OR
2. Reject this extraction as invalid (orphaned variable)

### Bug #2: Multiple Return Statements

**Status**: Identified, not yet fixed

**Problem**: When extracting code with multiple return statements, especially conditional returns, Towel generates incorrect code.

**Example**:
```python
def conditional_return_a(x, threshold):
    result = x * 2
    if result > threshold:
        return result
    result = result + 10
    return result
```

**Symptom**: Original returns 10, refactored returns 0

**Root Cause**: Unknown - needs investigation. Likely related to how control flow with early returns is handled during extraction.

### Bug #3: Try/Finally with Variables

**Status**: Similar to Bug #1

**Problem**: Variables modified in try/finally blocks face the same free variable issue as augmented assignments.

**Example**:
```python
def process_with_cleanup_a(items):
    results = []
    count = 0
    try:
        for item in items:
            count += 1
            results.append(item * 2)
    finally:
        results.append(count)
    return results
```

**Symptom**: `UnboundLocalError` for `count`

## Passing Cases

What Towel handled correctly:

✅ **Side effects** (4/4 proposals):
- Global state mutations
- Mutable default arguments
- List/dict modifications
- Multiple mutable arguments

✅ **Break/continue in simple loops** (4/6 proposals)

✅ **Tricky edge cases** (2/3 proposals):
- String formatting
- Ternary operators
- Type checks

✅ **Closures** (0 proposals generated):
- Correctly conservative - didn't attempt to extract closures

## Recommendations

### Immediate Actions

1. **Add free variable detection**: Before extracting a block, detect all free variables (variables used but not defined in the block) and either:
   - Add them as parameters to the extracted function
   - Reject the extraction if they can't be safely parameterized

2. **Add extraction validation**: Reject extractions that contain:
   - Variables modified inside loops with early exits (break/continue) where the variable is initialized outside
   - Multiple return statements in the middle of a block
   - Try/finally blocks that modify variables from outer scopes

### Longer Term

3. **Enhance orphan detection**: The current orphan detector (`orphan_detector.py`) checks for variables bound in the extracted block and used after. We need to also check for:
   - Variables used in the extracted block but bound BEFORE it
   - Variables that are both read and written (like augmented assignments)

4. **Control flow analysis**: Build better understanding of control flow to handle:
   - Early returns
   - Break/continue statements
   - Exception propagation

## Test Files Location

The adversarial test files are in `test_examples/`:
- `side_effects_adversarial.py`
- `control_flow_adversarial.py`
- `exception_adversarial.py`
- `closure_adversarial.py`
- `tricky_edge_cases_adversarial.py`

These should be kept as regression tests to ensure bugs don't resurface.

## Conclusion

The adversarial testing successfully identified critical correctness bugs that the original test suite missed. The core issue is incomplete handling of **free variables** in extracted code blocks - variables that are used but not defined within the block being extracted.

The fact that Towel caught 66.7% of adversarial cases is encouraging, but the 33.3% failure rate on specifically-designed attack cases shows there's room for improvement in the extraction validation logic.

---

**Date**: October 2025
**Testing Strategy**: Adversarial testing with intentionally tricky cases
**Impact**: High - these bugs cause runtime errors and break observational equivalence
