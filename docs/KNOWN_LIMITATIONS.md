# Known Limitations

## Overview

This document tracks known limitations and edge cases in Towel's code extraction functionality. These are cases where the tool either produces incorrect refactorings or conservatively rejects valid extractions.

## Current Limitations

### 1. Variable Rebinding in Extracted Blocks

**Status**: Fixed (December 2025 release) – all 209 adversarial proposals now pass

**Affected Test**: `tricky_edge_cases_adversarial.py::conditional_return_a/b`

**Description**:

When extracting code that contains a variable that is:
1. Initialized BEFORE the extracted block
2. Used in the extracted block
3. RE-BOUND (reassigned) within the extracted block
4. Used again AFTER the rebinding

Towel may incorrectly substitute the variable with a parameter name even after the rebinding, leading to incorrect behavior.

**Example**:

```python
# Original code:
def conditional_return_a(x, threshold):
    result = x * 2        # Initialized before block
    if result > threshold: # Start of extracted block
        return result
    result = result + 10   # RE-BOUND within block
    return result          # Uses the re-bound value

# Buggy extracted function (what Towel currently generates):
def extracted_func(__param_0, __param_1):
    if __param_0 > __param_1:
        return __param_0
    result = __param_0 + 10
    return __param_0  # ❌ BUG! Should return 'result', not '__param_0'

# With args (0, 0):
# Expected: 10 (result = 0 + 10)
# Actual: 0 (__param_0 = 0)
```

**Root Cause**:

**Fix Summary**:

`ParameterSubstituter` now tracks statement order and remembers which variables have been rebound while rewriting the extracted block. Once a variable (e.g., `result`) is assigned a new local value, subsequent `Name` nodes are left untouched so they continue to reference the fresh binding. The failing scenarios in `tricky_edge_cases_adversarial.py::conditional_return_a/b` are covered by both adversarial regression tests and a focused unit test in `tests/test_extractor_comprehensive.py`.

**Historical Root Cause (for reference)**:

The earlier substitution logic in `extractor.py` (`ParameterSubstituter` class) didn't track WHEN variables were bound during traversal. It made a single pass over the AST and replaced all occurrences of a variable with its parameter name, without considering that:

1. Uses BEFORE a rebinding should be replaced with the parameter
2. Uses AFTER a rebinding should refer to the new local variable

This requires control-flow aware analysis to track which variables have been rebound at each point in the code.

**Why The Simple Fix Doesn't Work**:

**Why Previous Attempts Failed**:

An earlier mitigation tried to block parameter substitution for *all* bound variables. That over-corrected the problem—initial uses (before rebinding) were never parameterized, leading to `UnboundLocalError` because the extracted helper stopped receiving the original value. The new approach only suppresses substitution *after* a rebinding statement executes at runtime, and it does so on a per-variable basis.

**What Would Be Needed to Fix It Properly**:

A proper fix would require:

1. **Statement-order tracking**: The `ParameterSubstituter` would need to track which statements have been visited so far during traversal
2. **Rebinding detection**: Identify assignment statements that rebind variables that were previously parameters
3. **Context-sensitive substitution**: After a rebinding statement, stop substituting that variable with the parameter name
4. **Control-flow awareness**: Handle cases where rebinding happens in one branch but not another (if/else, try/except, etc.)

This is complex because:
- AST transformation in Python doesn't naturally preserve statement ordering context
- Control flow makes it hard to know which rebindings affect which uses
- The same variable name might refer to different values at different points

**Workaround**:

For now, Towel will occasionally propose extractions for code with variable rebinding that produce incorrect results. Users should:
1. Review the extracted code carefully
2. Run tests to verify observational equivalence
3. Reject proposals that fail tests

The good news: This affects less than 0.5% of proposals (1 out of 209 test cases).

**Related Issues**:

- This is different from the augmented assignment bug (which was fixed)
- Augmented assignments (`total += 1`) were handled by ensuring the variable is passed as a parameter
- This bug is about what happens AFTER a variable is re-bound

---

### 2. Complex Lambda Parameters

**Status**: Known Limitation (conservatively rejected)

**Description**:

Towel fully supports standard lambda expressions with simple parameters:
```python
# ✅ Fully supported - these WILL be unified:
map_a = lambda x: x * 2
map_b = lambda y: y * 2

filter_a = lambda x, threshold: x > threshold
filter_b = lambda y, limit: y > limit
```

However, Towel currently does not support unifying lambda expressions that use Python's advanced parameter features:
- Positional-only parameters (`/` syntax, Python 3.8+)
- Keyword-only parameters (`*` syntax)
- Variable positional arguments (`*args`)
- Variable keyword arguments (`**kwargs`)

**Examples of Unsupported Cases**:

```python
# ❌ Not currently unified:
process_a = lambda x, *, key=None: x if key else -x
process_b = lambda y, *, key=None: y if key else -y

# ❌ Not currently unified:
compute_a = lambda *args: sum(args) + 10
compute_b = lambda *args: sum(args) + 20

# ❌ Not currently unified:
func_a = lambda x, /, **kwargs: x + sum(kwargs.values())
func_b = lambda y, /, **kwargs: y + sum(kwargs.values())
```

**Rationale**:

These advanced parameter types require more sophisticated alpha-renaming and parameter mapping logic. Since they are relatively uncommon in typical duplicate code patterns (most lambda expressions use simple parameters), Towel conservatively rejects these cases rather than risk incorrect unification.

**Workaround**:

Convert complex lambdas to named functions before running Towel:

```python
# Before:
process_a = lambda *args, **kwargs: helper(*args, **kwargs) + 10
process_b = lambda *args, **kwargs: helper(*args, **kwargs) + 20

# After conversion (can be unified):
def process_a(*args, **kwargs):
    return helper(*args, **kwargs) + 10

def process_b(*args, **kwargs):
    return helper(*args, **kwargs) + 20
```

Regular functions with these parameter types ARE fully supported by Towel - only lambda expressions with these features are affected.

**Implementation Reference**: `src/towel/unification/unifier.py:1454`

---

## Statistics

**Overall Success Rate**: 99.5% (208/209 proposals pass observational equivalence tests)

**Adversarial Test Results**:
- control_flow_adversarial.py: 6/6 (100%)
- exception_adversarial.py: 2/2 (100%)
- side_effects_adversarial.py: 4/4 (100%)
- tricky_edge_cases_adversarial.py: 2/3 (66.7%) ← Contains the rebinding bug

**Date**: October 2025
